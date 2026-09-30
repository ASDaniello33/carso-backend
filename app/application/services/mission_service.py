"""Service métier — missions (Lot L0, instruction/06 §6).

Règles appliquées :

- **règle 5 [C]** : une mission est créée à partir d'offres **approuvées** —
  jamais depuis une offre en brouillon ou en revue ;
- **relation N-N confirmée CARSO** : un appel à proposition se répond par une
  offre **technique** et une offre **financière** du *même lot* ; la mission
  référence ces offres. Une prestation directe sans offre reste possible ;
- **lieu distinct** : la mission référence un ``Lieu`` (``lieu_id``), jamais un
  texte libre — le lieu n'appartient pas au lot ;
- **invariant confirmé** : l'organisation est obligatoire et existante (FK
  RESTRICT) — issue de l'offre pour une mission d'offres, vérifiée pour une
  prestation directe ;
- **aucune règle inventée** : pas de contrainte de dates (Q6/Q9 non confirmés),
  pas de validation automatique de statut ;
- **traçabilité** : chaque création et chaque changement de statut écrit un
  ``AuditEvent`` dans la transaction courante ;
- le service ne fait ni commit ni rollback : la transaction appartient à
  l'appelant (instruction/04 §5).
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from app.application.dto import (
    MissionDepuisAppelInput,
    MissionDepuisOffresInput,
    MissionDirecteInput,
)
from app.application.services import notifications_evenements as notifications
from app.application.trace import TraceContext
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.domain.enums import ActionAudit, ActorType, StatutMission, StatutOffre
from app.domain.execution import Mission
from app.domain.organization import Offre
from app.domain.state_machines import validate_transition
from app.infrastructure.repositories import (
    AppelAPropositionRepository,
    LieuRepository,
    LotRepository,
    MissionRepository,
    OffreRepository,
    OrganisationRepository,
)

_ENTITY = "mission"


class MissionService:
    """Use cases : creer_depuis_appel, creer_depuis_offres, creer_directe,
    changer_statut, modifier, obtenir, lister_par_statut. Aucun commit."""

    def __init__(self, session: Session, actor_id: str | None = None) -> None:
        self._session = session
        self.missions = MissionRepository(session)
        self.offres = OffreRepository(session)
        self.lots = LotRepository(session)
        self.organisations = OrganisationRepository(session)
        self.lieux = LieuRepository(session)
        self.appels = AppelAPropositionRepository(session)
        self._trace = TraceContext(session, actor_id=actor_id)

    def creer_depuis_appel(self, entree: MissionDepuisAppelInput) -> Mission:
        """Crée une mission depuis **un appel et son lot** (chemin UI unique).

        L'utilisateur part de l'appel : l'appel porte l'organisation et ses lots
        portent les offres, donc tout se déduit — l'organisation est celle de
        l'appel (jamais fournie par l'appelant) et la mission référence les
        offres **approuvées du lot** (règle 5 [C] : jamais une offre en
        brouillon ou en revue).

        Le lot doit appartenir à l'appel fourni : une mission d'un autre appel
        serait une mission dont l'organisation ne correspond plus. Les offres du
        lot qui ne sont pas encore approuvées ne bloquent que si **aucune** n'est
        approuvée : dans ce cas il n'y a rien à référencer (une mission ne
        référence jamais une offre non approuvée) — l'utilisateur termine d'abord
        le cycle des offres.

        Raises:
            NotFoundError: appel, lot ou lieu inconnu.
            ValidationError: lot hors de l'appel, aucune offre approuvée.
            ConflictError: une de ces offres est déjà référencée par une mission.
        """
        appel = self.appels.get(entree.appel_a_proposition_id)
        if appel is None:
            raise NotFoundError(
                f"Appel à proposition {entree.appel_a_proposition_id} introuvable"
            )
        lot = self.lots.get(entree.lot_id)
        if lot is None:
            raise NotFoundError(f"Lot {entree.lot_id} introuvable")
        if lot.appel_a_proposition_id != appel.id:
            raise ValidationError(
                "Ce lot n'appartient pas à l'appel fourni",
                details={
                    "lot_id": str(lot.id),
                    "appel_attendu": str(lot.appel_a_proposition_id),
                },
            )

        offres_du_lot = self.offres.list_for_lot(lot.id)
        offres = [o for o in offres_du_lot if o.statut == StatutOffre.APPROUVE.value]
        if not offres:
            raise ValidationError(
                "Aucune offre approuvée sur ce lot : la mission référence des "
                "offres approuvées (règle 5). Terminez d'abord le cycle des offres.",
                details={
                    "offres_du_lot": [
                        {"reference": o.reference, "statut": o.statut}
                        for o in offres_du_lot
                    ],
                },
            )

        deja_rattachees = self.missions.list_for_offres([o.id for o in offres])
        if deja_rattachees:
            raise ConflictError(
                "Une mission référence déjà une de ces offres",
                details={
                    "missions": [
                        {"reference": m.reference, "id": str(m.id)}
                        for m in deja_rattachees
                    ]
                },
            )

        lieu_id = self._valider_lieu(entree.lieu_id)

        mission = Mission(
            organisation_id=appel.organisation_id,
            lieu_id=lieu_id,
            reference=entree.reference.strip(),
            titre=entree.titre.strip(),
            description=entree.description,
            date_debut=entree.date_debut,
            date_fin=entree.date_fin,
            statut=StatutMission.PLANIFIEE.value,
        )
        mission = self._creer(
            mission,
            extra={
                "appel_a_proposition_id": str(appel.id),
                "lot_id": str(lot.id),
                "offres": [o.reference for o in offres],
                "lieu_id": str(lieu_id) if lieu_id else None,
            },
        )
        # Relation N-N posée après l'ajout à la session (cf. creer_depuis_offres).
        mission.offres = offres
        self._session.flush()
        return mission

    def creer_depuis_offres(self, entree: MissionDepuisOffresInput) -> Mission:
        """Crée une mission référençant **plusieurs offres approuvées** (règle 5).

        Les offres doivent être approuvées, appartenir au **même lot** et
        n'être rattachées à aucune autre mission. L'organisation de la mission
        est celle des offres : elle n'est jamais fournie par l'appelant.
        """
        if not entree.offre_ids:
            raise ValidationError(
                "Au moins une offre est requise pour créer une mission depuis des offres"
            )

        offres = self._charger_offres(entree.offre_ids)
        lots = {offre.lot_id for offre in offres}
        if len(lots) > 1:
            raise ValidationError(
                "Toutes les offres d'une mission doivent appartenir au même lot",
                details={"lots": sorted(str(lot) for lot in lots)},
            )
        lot_id = next(iter(lots))

        non_approuvees = [o for o in offres if o.statut != StatutOffre.APPROUVE.value]
        if non_approuvees:
            raise ConflictError(
                "Une mission ne peut être créée qu'à partir d'offres approuvées",
                details={
                    "offres_non_approuvees": [
                        {"reference": o.reference, "statut": o.statut}
                        for o in non_approuvees
                    ]
                },
            )

        deja_rattachees = self.missions.list_for_offres(entree.offre_ids)
        if deja_rattachees:
            raise ConflictError(
                "Une mission référence déjà une de ces offres",
                details={
                    "missions": [
                        {"reference": m.reference, "id": str(m.id)}
                        for m in deja_rattachees
                    ]
                },
            )

        lieu_id = self._valider_lieu(entree.lieu_id)
        organisation_id = offres[0].organisation_id

        mission = Mission(
            organisation_id=organisation_id,
            lieu_id=lieu_id,
            reference=entree.reference.strip(),
            titre=entree.titre.strip(),
            description=entree.description,
            date_debut=entree.date_debut,
            date_fin=entree.date_fin,
            statut=StatutMission.PLANIFIEE.value,
        )
        mission = self._creer(
            mission,
            extra={
                "lot_id": str(lot_id),
                "offres": [o.reference for o in offres],
                "lieu_id": str(lieu_id) if lieu_id else None,
            },
        )
        # La relation N-N est posée APRÈS l'ajout de la mission à la session :
        # sinon SQLAlchemy avertit d'une écriture de collection sur un objet
        # encore détaché. Un flush suffit (aucun commit ici).
        mission.offres = offres
        self._session.flush()
        return mission

    def creer_directe(self, entree: MissionDirecteInput) -> Mission:
        """Crée une prestation directe sans offre (aucune offre rattachée).

        L'organisation est obligatoire et existante (FK RESTRICT).
        """
        if self.organisations.get(entree.organisation_id) is None:
            raise NotFoundError(f"Organisation {entree.organisation_id} introuvable")
        lieu_id = self._valider_lieu(entree.lieu_id)

        mission = Mission(
            organisation_id=entree.organisation_id,
            lieu_id=lieu_id,
            reference=entree.reference.strip(),
            titre=entree.titre.strip(),
            description=entree.description,
            date_debut=entree.date_debut,
            date_fin=entree.date_fin,
            statut=StatutMission.PLANIFIEE.value,
        )
        return self._creer(mission)

    def changer_statut(self, mission_id: UUID, cible: str) -> Mission:
        """Transition planifiee → en_preparation → en_cours → cloturee.

        Args:
            mission_id: identifiant de la mission.
            cible: statut visé (vocabulaire ``StatutMission``).

        Raises:
            ValidationError: statut cible inconnu.
            ConflictError: transition interdite par la machine à états.
        """
        vocabulaire = {s.value for s in StatutMission}
        if cible not in vocabulaire:
            raise ValidationError(
                f"Statut de mission inconnu : {cible!r}",
                details={"statuts_valides": sorted(vocabulaire)},
            )

        mission = self._get(mission_id)
        validate_transition(_ENTITY, mission.statut, cible)
        mission.statut = cible
        self._trace.record_event(
            actor_type=ActorType.HUMAIN.value,
            action=ActionAudit.MISSION_STATUT_CHANGE.value,
            entity_type=_ENTITY,
            entity_id=mission.id,
            after={"statut": cible},
        )
        return mission

    def changer_lieu(self, mission_id: UUID, lieu_id: UUID | None) -> Mission:
        """Rattache (ou retire avec ``None``) le lieu d'exécution de la mission.

        Le lieu est une donnée distincte : il appartient à la mission, pas au lot.
        Un identifiant inconnu est refusé ; aucun lieu n'est créé implicitement.
        """
        mission = self._get(mission_id)
        valide = self._valider_lieu(lieu_id)
        avant = str(mission.lieu_id) if mission.lieu_id else None
        if valide == mission.lieu_id:
            return mission
        mission.lieu_id = valide
        self._trace.record_event(
            actor_type=ActorType.HUMAIN.value,
            action=ActionAudit.MISSION_MODIFIEE.value,
            entity_type=_ENTITY,
            entity_id=mission.id,
            after={"lieu_id": str(valide) if valide else None},
            event_metadata={"lieu_id_avant": avant},
        )
        return mission

    def modifier(
        self,
        mission_id: UUID,
        *,
        titre: str | None = None,
        lieu_id: UUID | None = None,
        description: str | None = None,
        acteur: str = ActorType.AGENT.value,
    ) -> Mission:
        """Modification partielle : titre, lieu, description.

        ``acteur`` distingue les deux chemins qui partagent ce service : le tool
        d'agent sous HITL (``AGENT``, défaut — comportement historique) et le
        formulaire d'interface (``HUMAIN``). Un audit attribué au mauvais acteur
        rendrait la trace inutilisable.
        """
        mission = self._get(mission_id)
        modifie = False
        if titre is not None and titre.strip() and titre != mission.titre:
            mission.titre = titre.strip()
            modifie = True
        if lieu_id is not None and lieu_id != mission.lieu_id:
            mission.lieu_id = self._valider_lieu(lieu_id)
            modifie = True
        if description is not None and description != mission.description:
            mission.description = description
            modifie = True
        if not modifie:
            raise ValidationError(
                "Aucune modification fournie pour la mission",
                details={"mission_id": str(mission_id)},
            )
        self._trace.record_event(
            actor_type=acteur,
            action=ActionAudit.MISSION_MODIFIEE.value,
            entity_type=_ENTITY,
            entity_id=mission.id,
            after={
                "titre": mission.titre,
                "lieu_id": str(mission.lieu_id) if mission.lieu_id else None,
            },
        )
        return mission

    def obtenir(self, mission_id: UUID) -> Mission:
        """Charge une mission (lecture pure, sans mutation)."""
        return self._get(mission_id)

    def lister_par_statut(self, statut: str) -> list[Mission]:
        """Missions d'un statut donné (usage : UI de suivi, agents en lecture)."""
        return self.missions.list_by_statut(statut)

    def lister_toutes(self) -> list[Mission]:
        """Toutes les missions (usage UI de suivi, tables modestes)."""
        return self.missions.list_all()

    # --- internes -----------------------------------------------------------

    def _charger_offres(self, offre_ids: tuple[UUID, ...]) -> list[Offre]:
        """Charge les offres demandées ; refuse un identifiant inconnu."""
        offres: list[Offre] = []
        for offre_id in dict.fromkeys(offre_ids):  # dédoublonne, garde l'ordre
            offre = self.offres.get(offre_id)
            if offre is None:
                raise NotFoundError(f"Offre {offre_id} introuvable")
            offres.append(offre)
        return offres

    def _valider_lieu(self, lieu_id: UUID | None) -> UUID | None:
        """Vérifie qu'un lieu fourni existe (aucun lieu implicitement créé)."""
        if lieu_id is None:
            return None
        if self.lieux.get(lieu_id) is None:
            raise NotFoundError(f"Lieu {lieu_id} introuvable")
        return lieu_id

    def _creer(self, mission: Mission, *, extra: dict[str, object] | None = None) -> Mission:
        """Vérifie l'unicité de référence, persiste, trace. Un flush suffit :
        le commit appartient à l'appelant."""
        if self.missions.get_by_reference(mission.reference) is not None:
            msg = f"Référence de mission déjà utilisée : {mission.reference!r}"
            raise ConflictError(msg, details={"reference": mission.reference})

        self.missions.add(mission)
        # Flush (pas commit) : l'id doit exister avant l'écriture de l'AuditEvent.
        self._session.flush()
        after: dict[str, object] = {
            "reference": mission.reference,
            "statut": mission.statut,
        }
        if extra:
            after.update(extra)
        self._trace.record_event(
            actor_type=ActorType.HUMAIN.value,
            action=ActionAudit.MISSION_CREEE.value,
            entity_type=_ENTITY,
            entity_id=mission.id,
            after=after,
        )
        notifications.notifier_mission_creee(self._session, mission)
        return mission

    def _get(self, mission_id: UUID) -> Mission:
        mission = self.missions.get(mission_id)
        if mission is None:
            raise NotFoundError(f"Mission {mission_id} introuvable")
        return mission


__all__ = ["MissionService"]
