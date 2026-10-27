"""Service métier—missions (Lot L0, instruction/06 §6 ; ADR 0012).

Règles appliquées :

- **une mission naît d'un lot d'appel** (règle 26/10) : elle peut exister
  **avant** que toutes les offres du lot soient approuvées — les offres
  approuvées sont rattachées à la création ou plus tard
  (:meth:`rattacher_offres`), et appartiennent toujours au même lot ;
- **1 lot → 1 mission** : ``lot_id`` est unique quand il est renseigné
  (``uq_missions_lot_id``) ; une **prestation directe** (sans appel ni lot)
  reste possible et est identifiée comme telle ;
- **relation N-N confirmée CARSO**—un appel à proposition se répond par une
  offre **technique** et une offre **financière** du *même lot* ; la mission
  référence ces offres. Une mission ne référence jamais une offre non
  approuvée ;
- **lieu distinct**—la mission référence un ``Lieu`` (``lieu_id``), jamais un
  texte libre—le lieu n'appartient pas au lot ;
- **invariant confirmé**—l'organisation est obligatoire et existante (FK
  RESTRICT)—issue de l'appel pour une mission de lot, vérifiée pour une
  prestation directe ;
- **planification**—``planifier`` exige une date de début et une date de fin,
  **crée la session** en ``planifie`` (dates et lieu recopiés) et passe la
  mission à ``planifie``, dans la même transaction. Une mission sans équipe
  approuvée reste planifiable : un avertissement est retourné, jamais un
  blocage. ``en_cours`` et ``cloture`` sont automatiques (balayage de cycle de
  vie), jamais saisis ;
- **annulation**—annuler une mission annule sa session dans la même
  transaction, et réciproquement (:meth:`annuler` / ``SessionService``) ;
- **traçabilité**—chaque création, rattachement et changement de statut écrit
  un ``AuditEvent`` dans la transaction courante ;
- le service ne fait ni commit ni rollback—la transaction appartient à
  l'appelant (instruction/04 §5).
"""

from __future__ import annotations

from datetime import date
from uuid import UUID

from sqlalchemy.orm import Session

from app.application.dto import (
    MissionDepuisAppelInput,
    MissionDepuisOffresInput,
    MissionDirecteInput,
    MissionOffresRattachementInput,
)
from app.application.perimetre import Perimetre, perimetre_effectif
from app.application.services import notifications_evenements as notifications
from app.application.trace import TraceContext
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.domain.enums import (
    ActionAudit,
    ActorType,
    StatutAffectation,
    StatutMission,
    StatutOffre,
    StatutSessionFormation,
)
from app.domain.execution import Mission, SessionFormation
from app.domain.organization import Offre
from app.domain.state_machines import validate_transition
from app.infrastructure.repositories import (
    AffectationEquipeRepository,
    AppelRepository,
    LieuRepository,
    LotRepository,
    MissionRepository,
    OffreRepository,
    OrganisationRepository,
    SessionRepository,
)

_ENTITY = "mission"
_SESSION = "session"


class MissionService:
    """Use cases : creer_depuis_appel, creer_depuis_offres, creer_directe,
    rattacher_offres, planifier, annuler, changer_statut, modifier, obtenir,
    lister_par_statut. Aucun commit."""

    def __init__(
        self,
        session: Session,
        actor_id: str | None = None,
        *,
        perimetre: Perimetre | None = None,
    ) -> None:
        self._session = session
        self._perimetre = perimetre if perimetre is not None else perimetre_effectif()
        self.missions = MissionRepository(session)
        self.offres = OffreRepository(session)
        self.lots = LotRepository(session)
        self.organisations = OrganisationRepository(session)
        self.lieux = LieuRepository(session)
        self.appels = AppelRepository(session)
        self.sessions = SessionRepository(session)
        self.affectations = AffectationEquipeRepository(session)
        self._trace = TraceContext(session, actor_id=actor_id)

    def creer_depuis_appel(self, entree: MissionDepuisAppelInput) -> Mission:
        """Crée une mission depuis **un appel et son lot** (chemin UI unique).

        L'organisation est celle de l'appel (jamais fournie par l'appelant) ;
        le lot doit appartenir à l'appel et n'être **la mission d'aucun autre
        lot déjà pourvu** : 1 lot → 1 mission. Les offres **approuvées** du lot
        sont rattachées s'il en existe — la mission peut naître avant
        l'approbation, elle ne référence jamais une offre non approuvée.

        Raises:
            NotFoundError: appel, lot ou lieu inconnu.
            ValidationError: lot hors de l'appel.
            ConflictError: le lot porte déjà une mission.
        """
        self._perimetre.exiger_complet("créer une mission")
        appel = self.appels.get(entree.appel_id)
        if appel is None:
            raise NotFoundError(
                f"Appel {entree.appel_id} introuvable"
            )
        lot = self.lots.get(entree.lot_id)
        if lot is None:
            raise NotFoundError(f"Lot {entree.lot_id} introuvable")
        if lot.appel_id != appel.id:
            raise ValidationError(
                "Ce lot n'appartient pas à l'appel fourni",
                details={
                    "lot_id": str(lot.id),
                    "appel_attendu": str(lot.appel_id),
                },
            )
        self._refuser_lot_deja_pourvu(lot.id, appel.reference)

        offres = [
            o
            for o in self.offres.list_for_lot(lot.id)
            if o.statut == StatutOffre.APPROUVE.value
        ]
        lieu_id = self._valider_lieu(entree.lieu_id)

        mission = Mission(
            organisation_id=appel.organisation_id,
            lot_id=lot.id,
            lieu_id=lieu_id,
            reference=entree.reference.strip(),
            titre=entree.titre.strip(),
            description=entree.description,
            date_debut=entree.date_debut,
            date_fin=entree.date_fin,
            statut=StatutMission.EN_PLANIFICATION.value,
        )
        mission = self._creer(
            mission,
            extra={
                "appel_id": str(appel.id),
                "lot_id": str(lot.id),
                "offres": [o.reference for o in offres],
                "lieu_id": str(lieu_id) if lieu_id else None,
            },
        )
        # Relation N-N posée après l'ajout à la session (cf. creer_depuis_offres).
        if offres:
            mission.offres = offres
            self._session.flush()
        return mission

    def creer_depuis_offres(self, entree: MissionDepuisOffresInput) -> Mission:
        """Crée une mission référençant **plusieurs offres approuvées** d'un lot.

        Les offres doivent être approuvées, appartenir au **même lot** et
        n'être rattachées à aucune autre mission. L'organisation de la mission
        est celle des offres : elle n'est jamais fournie par l'appelant.
        """
        self._perimetre.exiger_complet("créer une mission")
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
        lot = self.lots.get(lot_id)
        if lot is not None:
            self._refuser_lot_deja_pourvu(lot.id, lot.appel_id)

        lieu_id = self._valider_lieu(entree.lieu_id)
        organisation_id = offres[0].organisation_id

        mission = Mission(
            organisation_id=organisation_id,
            lot_id=lot_id,
            lieu_id=lieu_id,
            reference=entree.reference.strip(),
            titre=entree.titre.strip(),
            description=entree.description,
            date_debut=entree.date_debut,
            date_fin=entree.date_fin,
            statut=StatutMission.EN_PLANIFICATION.value,
        )
        mission = self._creer(
            mission,
            extra={
                "lot_id": str(lot_id),
                "offres": [o.reference for o in offres],
                "lieu_id": str(lieu_id) if lieu_id else None,
            },
        )
        mission.offres = offres
        self._session.flush()
        return mission

    def creer_directe(self, entree: MissionDirecteInput) -> Mission:
        """Crée une prestation directe sans appel ni lot (``lot_id = None``).

        L'organisation est obligatoire et existante (FK RESTRICT).
        """
        self._perimetre.exiger_complet("créer une mission")
        if self.organisations.get(entree.organisation_id) is None:
            raise NotFoundError(f"Organisation {entree.organisation_id} introuvable")
        lieu_id = self._valider_lieu(entree.lieu_id)

        mission = Mission(
            organisation_id=entree.organisation_id,
            lot_id=None,
            lieu_id=lieu_id,
            reference=entree.reference.strip(),
            titre=entree.titre.strip(),
            description=entree.description,
            date_debut=entree.date_debut,
            date_fin=entree.date_fin,
            statut=StatutMission.EN_PLANIFICATION.value,
        )
        return self._creer(mission, extra={"prestation_directe": True})

    def rattacher_offres(
        self, mission_id: UUID, entree: MissionOffresRattachementInput
    ) -> Mission:
        """Rattache des offres **approuvées du même lot** à une mission.

        La mission a pu naître avant l'approbation de toutes les offres de son
        lot : c'est le seul moment où ce rattachement a un sens. Une offre
        d'un autre lot, non approuvée, ou déjà rattachée ailleurs est refusée.
        """
        self._perimetre.exiger_complet("rattacher des offres à une mission")
        mission = self._get(mission_id)
        if mission.lot_id is None:
            raise ValidationError(
                "Une prestation directe ne référence pas d'offre",
                details={"mission_id": str(mission_id)},
            )
        if not entree.offre_ids:
            raise ValidationError("Au moins une offre est requise")

        offres = self._charger_offres(entree.offre_ids)
        hors_lot = [o for o in offres if o.lot_id != mission.lot_id]
        if hors_lot:
            raise ValidationError(
                "Une mission ne référence que les offres de son propre lot",
                details={
                    "lot_attendu": str(mission.lot_id),
                    "offres_hors_lot": [
                        {"reference": o.reference, "lot_id": str(o.lot_id)}
                        for o in hors_lot
                    ],
                },
            )
        non_approuvees = [o for o in offres if o.statut != StatutOffre.APPROUVE.value]
        if non_approuvees:
            raise ConflictError(
                "Une mission ne peut référencer que des offres approuvées",
                details={
                    "offres_non_approuvees": [
                        {"reference": o.reference, "statut": o.statut}
                        for o in non_approuvees
                    ]
                },
            )
        deja_rattachees = self.missions.list_for_offres(entree.offre_ids)
        autres = [m for m in deja_rattachees if m.id != mission.id]
        if autres:
            raise ConflictError(
                "Une de ces offres est déjà référencée par une autre mission",
                details={
                    "missions": [
                        {"reference": m.reference, "id": str(m.id)} for m in autres
                    ]
                },
            )

        nouvelles = [o for o in offres if o not in mission.offres]
        if not nouvelles:
            return mission  # rien à faire : ni écriture, ni trace

        mission.offres.extend(nouvelles)
        self._session.flush()
        self._trace.record_event(
            actor_type=ActorType.HUMAIN.value,
            action=ActionAudit.MISSION_OFFRES_RATTACHEES.value,
            entity_type=_ENTITY,
            entity_id=mission.id,
            after={"offres": [o.reference for o in nouvelles]},
        )
        return mission

    def planifier(self, mission_id: UUID) -> Mission:
        """Passe la mission à ``planifie`` et **crée sa session**.

        Exige ``date_debut`` et ``date_fin`` : sans elles, la session ne
        pourrait ni démarrer ni se clôturer automatiquement. La session naît
        en ``planifie`` avec les dates de la mission et le nom de son lieu.
        Idempotent : une mission déjà ``planifie`` (ou au-delà) est retournée
        telle quelle, sans seconde session.

        Returns:
            La mission, dont ``.session`` est renseignée. L'appelant lit
            l'avertissement « aucune équipe approuvée » dans le champ
            ``avertissements`` de la réponse de la route.
        """
        self._perimetre.exiger_complet("planifier une mission")
        mission = self._get(mission_id)
        if mission.statut != StatutMission.EN_PLANIFICATION.value:
            # Idempotence : planifier deux fois ne crée jamais deux sessions.
            return mission
        if mission.date_debut is None or mission.date_fin is None:
            raise ValidationError(
                "La planification exige une date de début et une date de fin : "
                "sans elles, la session ne peut ni démarrer ni se clôturer "
                "automatiquement.",
                details={
                    "mission_id": str(mission_id),
                    "date_debut": str(mission.date_debut),
                    "date_fin": str(mission.date_fin),
                },
            )

        session_formation = SessionFormation(
            mission_id=mission.id,
            theme=mission.titre,
            date_debut=mission.date_debut,
            date_fin=mission.date_fin,
            lieu=mission.lieu.nom if mission.lieu is not None else None,
            statut=StatutSessionFormation.PLANIFIE.value,
        )
        self.sessions.add(session_formation)
        self._session.flush()  # id de la session avant sa trace
        self._trace.record_event(
            actor_type=ActorType.HUMAIN.value,
            action=ActionAudit.SESSION_CREEE.value,
            entity_type=_SESSION,
            entity_id=session_formation.id,
            after={
                "mission_id": str(mission.id),
                "statut": StatutSessionFormation.PLANIFIE.value,
                "creee_par": "planification_mission",
            },
        )
        validate_transition(_ENTITY, mission.statut, StatutMission.PLANIFIE.value)
        mission.statut = StatutMission.PLANIFIE.value
        self._trace.record_event(
            actor_type=ActorType.HUMAIN.value,
            action=ActionAudit.MISSION_PLANIFIEE.value,
            entity_type=_ENTITY,
            entity_id=mission.id,
            after={
                "statut": StatutMission.PLANIFIE.value,
                "session_id": str(session_formation.id),
            },
        )
        notifications.notifier_session_changement(
            self._session,
            session_formation,
            mission.titre,
            StatutSessionFormation.PLANIFIE.value,
        )
        return mission

    def avertissement_equipe(self, mission_id: UUID) -> str | None:
        """Avertissement de planification si aucune équipe n'est approuvée.

        Une mission sans équipe approuvée reste planifiable (décision CARSO) :
        l'avertissement est une information, jamais un blocage.
        """
        mission = self._get(mission_id)
        approuvees = [
            a
            for a in self.affectations.list_for_mission(mission.id)
            if a.statut == StatutAffectation.APPROUVEE.value
        ]
        if approuvees:
            return None
        return (
            "Aucune équipe n'est encore approuvée sur cette mission : "
            "la session est créée, mais aucun formateur n'y est affecté."
        )

    def annuler(self, mission_id: UUID, *, motif: str | None = None) -> Mission:
        """Annule la mission **et** sa session (décision humaine interne).

        La session suit toujours la mission : les deux états changent dans la
        même transaction, chacun avec sa trace.
        """
        self._perimetre.exiger_complet("annuler une mission")
        mission = self._get(mission_id)
        validate_transition(_ENTITY, mission.statut, StatutMission.ANNULE.value)
        mission.statut = StatutMission.ANNULE.value
        self._trace.record_event(
            actor_type=ActorType.HUMAIN.value,
            action=ActionAudit.MISSION_STATUT_CHANGE.value,
            entity_type=_ENTITY,
            entity_id=mission.id,
            after={"statut": StatutMission.ANNULE.value},
            event_metadata={"annulation": True, "motif": motif},
        )
        session_formation = self.sessions.get_for_mission(mission.id)
        if session_formation is not None and session_formation.statut not in {
            StatutSessionFormation.CLOTURE.value,
            StatutSessionFormation.ANNULE.value,
        }:
            validate_transition(
                _SESSION, session_formation.statut, StatutSessionFormation.ANNULE.value
            )
            session_formation.statut = StatutSessionFormation.ANNULE.value
            self._trace.record_event(
                actor_type=ActorType.HUMAIN.value,
                action=ActionAudit.SESSION_STATUT_CHANGE.value,
                entity_type=_SESSION,
                entity_id=session_formation.id,
                after={"statut": StatutSessionFormation.ANNULE.value},
                event_metadata={
                    "annulation": True,
                    "motif": motif or "Annulation de la mission",
                },
            )
        return mission

    def changer_statut(self, mission_id: UUID, cible: str) -> Mission:
        """Transitions **manuelles** autorisées : ``annule`` uniquement.

        ``en_cours`` et ``cloture`` sont commandés par le calendrier et
        appliqués par le balayage de cycle de vie (ADR 0012) : aucun humain ne
        les saisit. La planification passe par :meth:`planifier`.

        Raises:
            ValidationError: statut cible inconnu ou transition manuelle
                interdite (``planifie`` via cette route, ``en_cours``,
            ``cloture``).
            ConflictError: transition interdite par la machine à états.
        """
        self._perimetre.exiger_complet("changer le statut d'une mission")
        vocabulaire = {s.value for s in StatutMission}
        if cible not in vocabulaire:
            raise ValidationError(
                f"Statut de mission inconnu : {cible!r}",
                details={"statuts_valides": sorted(vocabulaire)},
            )
        if cible == StatutMission.PLANIFIE.value:
            raise ValidationError(
                "La planification crée la session : elle passe par "
                "POST /missions/{id}/planification.",
                details={"mission_id": str(mission_id)},
            )
        if cible in {StatutMission.EN_COURS.value, StatutMission.CLOTURE.value}:
            raise ValidationError(
                "Ce statut est appliqué automatiquement par le balayage de "
                "cycle de vie, jamais saisi à la main.",
                details={"statut": cible},
            )

        mission = self._get(mission_id)
        validate_transition(_ENTITY, mission.statut, cible)
        if cible == StatutMission.ANNULE.value:
            return self.annuler(mission_id)
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
        self._perimetre.exiger_complet("modifier le lieu d'une mission")
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
        date_debut: date | None = None,
        date_fin: date | None = None,
        acteur: str = ActorType.AGENT.value,
    ) -> Mission:
        """Modification partielle : titre, lieu, description, dates.

        Les dates ne se modifient que pendant ``en_planification`` : la
        planification les exige (ADR 0012) et une mission née sans dates doit
        pouvoir les recevoir — sans cela elle resterait bloquée à vie. Dès que
        la mission est planifiée, la session porte le calendrier : changer les
        dates ici créerait un désaccord silencieux, le refus est explicite.
        Quand une seule des deux bornes est fournie, l'autre est celle déjà
        portée par la mission ; début > fin est refusé.

        ``acteur`` distingue les deux chemins qui partagent ce service : le tool
        d'agent sous HITL (``AGENT``, défaut — comportement historique) et le
        formulaire d'interface (``HUMAIN``). Un audit attribué au mauvais acteur
        rendrait la trace inutilisable.
        """
        self._perimetre.exiger_complet("modifier une mission")
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
        dates_fournies = (date_debut, date_fin) != (None, None)
        if dates_fournies:
            if mission.statut != StatutMission.EN_PLANIFICATION.value:
                raise ValidationError(
                    "Les dates d'une mission ne se changent qu'avant sa "
                    "planification : la session déjà créée porte le calendrier.",
                    details={
                        "mission_id": str(mission_id),
                        "statut": mission.statut,
                    },
                )
            debut_effectif = date_debut if date_debut is not None else mission.date_debut
            fin_effective = date_fin if date_fin is not None else mission.date_fin
            if debut_effectif is not None and fin_effective is not None:
                if debut_effectif > fin_effective:
                    raise ValidationError(
                        "La date de début de la mission ne peut pas suivre sa date de fin.",
                        details={
                            "mission_id": str(mission_id),
                            "date_debut": str(debut_effectif),
                            "date_fin": str(fin_effective),
                        },
                    )
            if date_debut is not None and date_debut != mission.date_debut:
                mission.date_debut = date_debut
                modifie = True
            if date_fin is not None and date_fin != mission.date_fin:
                mission.date_fin = date_fin
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
                "date_debut": str(mission.date_debut) if mission.date_debut else None,
                "date_fin": str(mission.date_fin) if mission.date_fin else None,
            },
        )
        return mission

    def obtenir(self, mission_id: UUID) -> Mission:
        """Charge une mission **dans le périmètre** (lecture pure, sans mutation).

        Hors périmètre, la mission est traitée comme inexistante (404) : un refus
        ne doit jamais révéler qu'une mission d'une autre équipe existe.
        """
        return self._get(mission_id)

    def lister_par_statut(self, statut: str) -> list[Mission]:
        """Missions d'un statut donné, restreintes au périmètre (UI, agents)."""
        return [
            mission
            for mission in self.missions.list_by_statut(statut)
            if self._perimetre.mission_autorisee(mission.id)
        ]

    def lister_toutes(self) -> list[Mission]:
        """Toutes les missions du périmètre (usage UI de suivi, tables modestes)."""
        return [
            mission
            for mission in self.missions.list_all()
            if self._perimetre.mission_autorisee(mission.id)
        ]

    # --- internes -----------------------------------------------------------

    def _refuser_lot_deja_pourvu(self, lot_id: UUID, reference_appel: str) -> None:
        """1 lot → 1 mission : un second refus explicite plutôt qu'un 500."""
        existante = self.missions.get_by_lot(lot_id)
        if existante is not None:
            raise ConflictError(
                "Ce lot a déjà sa mission : un lot ne porte qu'une mission.",
                details={
                    "lot_id": str(lot_id),
                    "mission_existante": str(existante.id),
                    "reference_mission": existante.reference,
                    "appel": reference_appel,
                },
            )

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
        if mission is None or not self._perimetre.mission_autorisee(mission.id):
            raise NotFoundError(f"Mission {mission_id} introuvable")
        return mission


__all__ = ["MissionService"]
