"""Service métier—sessions de formation (Lot L0 ; ADR 0012).

Règles appliquées :

- une session vit **toujours rattachée à une mission existante** (FK CASCADE) ;
- **1 mission → 1 session** : la planification de la mission la crée en
  ``planifie``. La création manuelle reste possible (session
  ``en_planification``), refusée si la mission a déjà la sienne ;
- ``en_cours`` (jour J) et ``cloture`` (fin dépassée) sont appliqués **par le
  balayage de cycle de vie**, jamais saisis : ce service ne propose que la
  confirmation et l'annulation ;
- annuler une session **annule aussi sa mission** (et réciproquement,
  ``MissionService.annuler``) : l'exécution et sa préparation ne peuvent pas
  diverger ;
- traçabilité—planification, confirmation et changement de statut écrivent un
  ``AuditEvent`` dans la transaction courante ;
- le service ne fait ni commit ni rollback (instruction/04 §5).
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from app.application.dto import SessionPlanificationInput, SessionUpdateInput
from app.application.perimetre import Perimetre, perimetre_effectif
from app.application.services import notifications_evenements as notifications
from app.application.trace import TraceContext
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.domain.enums import (
    ActionAudit,
    ActorType,
    StatutMission,
    StatutSessionFormation,
)
from app.domain.execution import SessionFormation
from app.domain.state_machines import validate_transition
from app.infrastructure.repositories import MissionRepository, SessionRepository

_ENTITY = "session"
_MISSION = "mission"


class SessionService:
    """Use cases : planifier (manuel), confirmer, annuler, changer_statut,
    modifier, obtenir, lister_pour_mission."""

    def __init__(
        self,
        session: Session,
        actor_id: str | None = None,
        *,
        perimetre: Perimetre | None = None,
    ) -> None:
        self._session = session
        self._perimetre = perimetre if perimetre is not None else perimetre_effectif()
        self.sessions = SessionRepository(session)
        self.missions = MissionRepository(session)
        self._trace = TraceContext(session, actor_id=actor_id)

    def planifier(self, entree: SessionPlanificationInput) -> SessionFormation:
        """Crée **manuellement** une session en ``en_planification``.

        Chemin exceptionnel : le chemin nominal est la planification de la
        mission (``MissionService.planifier``), qui crée la session en
        ``planifie`` d'emblée. Une mission qui a déjà sa session est refusée
        (409) — la contrainte d'unicité doit se lire en erreur métier, pas en
        500 de contrainte brute.
        """
        self._perimetre.exiger_complet("planifier une session")
        mission = self.missions.get(entree.mission_id)
        if mission is None or not self._perimetre.mission_autorisee(mission.id):
            raise NotFoundError(f"Mission {entree.mission_id} introuvable")
        existante = self.sessions.get_for_mission(entree.mission_id)
        if existante is not None:
            raise ConflictError(
                "Cette mission a déjà sa session : une mission n'en porte "
                "qu'une. La replanifier passe par la mission.",
                details={
                    "mission_id": str(entree.mission_id),
                    "session_existante": str(existante.id),
                },
            )

        session_formation = SessionFormation(
            mission_id=entree.mission_id,
            theme=entree.theme,
            date_debut=entree.date_debut,
            date_fin=entree.date_fin,
            lieu=entree.lieu,
            statut=StatutSessionFormation.EN_PLANIFICATION.value,
        )
        self.sessions.add(session_formation)
        # Flush (pas commit) : l'id doit exister avant l'écriture de l'AuditEvent.
        self._session.flush()
        self._trace.record_event(
            actor_type=ActorType.HUMAIN.value,
            action=ActionAudit.SESSION_PLANIFIEE.value,
            entity_type=_ENTITY,
            entity_id=session_formation.id,
            after={
                "mission_id": str(entree.mission_id),
                "statut": StatutSessionFormation.EN_PLANIFICATION.value,
            },
        )
        notifications.notifier_session_changement(
            self._session,
            session_formation,
            mission.titre,
            StatutSessionFormation.EN_PLANIFICATION.value,
        )
        return session_formation

    def confirmer(self, session_id: UUID) -> SessionFormation:
        """``en_planification`` → ``planifie``, décision humaine.

        Si la mission est encore ``en_planification``, elle passe ``planifie``
        dans la même transaction : l'exécution ne peut pas être prête sans que
        la mission le soit. Les deux transitions passent par
        ``validate_transition``.
        """
        self._perimetre.exiger_complet("confirmer une session")
        session_formation = self._get(session_id)
        validate_transition(
            _ENTITY,
            session_formation.statut,
            StatutSessionFormation.PLANIFIE.value,
        )
        session_formation.statut = StatutSessionFormation.PLANIFIE.value
        self._trace.record_event(
            actor_type=ActorType.HUMAIN.value,
            action=ActionAudit.SESSION_STATUT_CHANGE.value,
            entity_type=_ENTITY,
            entity_id=session_formation.id,
            after={"statut": StatutSessionFormation.PLANIFIE.value},
        )
        mission = self.missions.get(session_formation.mission_id)
        if (
            mission is not None
            and mission.statut == StatutMission.EN_PLANIFICATION.value
        ):
            validate_transition(
                _MISSION, mission.statut, StatutMission.PLANIFIE.value
            )
            mission.statut = StatutMission.PLANIFIE.value
            self._trace.record_event(
                actor_type=ActorType.HUMAIN.value,
                action=ActionAudit.MISSION_PLANIFIEE.value,
                entity_type=_MISSION,
                entity_id=mission.id,
                after={
                    "statut": StatutMission.PLANIFIE.value,
                    "session_confirmee": str(session_formation.id),
                },
            )
        notifications.notifier_session_changement(
            self._session,
            session_formation,
            mission.titre if mission is not None else "",
            StatutSessionFormation.PLANIFIE.value,
        )
        return session_formation

    def annuler(self, session_id: UUID, *, motif: str | None = None) -> SessionFormation:
        """Annule la session **et sa mission** (décision humaine interne).

        Une mission annulée ne bloque plus la disponibilité de son équipe.
        """
        self._perimetre.exiger_complet("annuler une session")
        session_formation = self._get(session_id)
        validate_transition(
            _ENTITY, session_formation.statut, StatutSessionFormation.ANNULE.value
        )
        session_formation.statut = StatutSessionFormation.ANNULE.value
        self._trace.record_event(
            actor_type=ActorType.HUMAIN.value,
            action=ActionAudit.SESSION_STATUT_CHANGE.value,
            entity_type=_ENTITY,
            entity_id=session_formation.id,
            after={"statut": StatutSessionFormation.ANNULE.value},
            event_metadata={"annulation": True, "motif": motif},
        )
        mission = self.missions.get(session_formation.mission_id)
        if (
            mission is not None
            and mission.statut != StatutMission.ANNULE.value
            and mission.statut != StatutMission.CLOTURE.value
        ):
            validate_transition(_MISSION, mission.statut, StatutMission.ANNULE.value)
            mission.statut = StatutMission.ANNULE.value
            self._trace.record_event(
                actor_type=ActorType.HUMAIN.value,
                action=ActionAudit.MISSION_STATUT_CHANGE.value,
                entity_type=_MISSION,
                entity_id=mission.id,
                after={"statut": StatutMission.ANNULE.value},
                event_metadata={
                    "annulation": True,
                    "motif": motif or "Annulation de la session",
                },
            )
        return session_formation

    def changer_statut(self, session_id: UUID, cible: str) -> SessionFormation:
        """Transitions **manuelles** : confirmation et annulation seulement.

        ``en_cours`` et ``cloture`` sont automatiques (balayage de cycle de
        vie, ADR 0012) : les saisir à la main mentirait sur le calendrier.

        Raises:
            ValidationError: statut cible inconnu ou saisie interdite.
            ConflictError: transition interdite par la machine à états.
        """
        self._perimetre.exiger_complet("changer le statut d'une session")
        vocabulaire = {s.value for s in StatutSessionFormation}
        if cible not in vocabulaire:
            raise ValidationError(
                f"Statut de session inconnu : {cible!r}",
                details={"statuts_valides": sorted(vocabulaire)},
            )
        if cible in {
            StatutSessionFormation.EN_COURS.value,
            StatutSessionFormation.CLOTURE.value,
        }:
            raise ValidationError(
                "Ce statut est appliqué automatiquement au jour J par le "
                "balayage de cycle de vie, jamais saisi à la main.",
                details={"statut": cible},
            )
        if cible == StatutSessionFormation.ANNULE.value:
            return self.annuler(session_id)
        if cible == StatutSessionFormation.PLANIFIE.value:
            return self.confirmer(session_id)
        raise ValidationError(
            "Transition manuelle inconnue pour une session.",
            details={"statut": cible},
        )

    def modifier(self, session_id: UUID, entree: SessionUpdateInput) -> SessionFormation:
        """Modification partielle : thème, dates, lieu.

        La mission n'est jamais modifiable ici : une session vit dans le contexte
        d'une mission (la rattacher ailleurs serait une autre session). Un champ
        absent (``None``) n'est pas appliqué — le formulaire d'interface envoie
        seulement ce qui a changé.

        Raises:
            ValidationError: aucune modification fournie.
        """
        self._perimetre.exiger_complet("modifier une session")
        session_formation = self._get(session_id)
        modifie = False
        for nom in ("theme", "date_debut", "date_fin", "lieu"):
            valeur = getattr(entree, nom)
            if valeur is None or getattr(session_formation, nom) == valeur:
                continue
            setattr(session_formation, nom, valeur)
            modifie = True
        if not modifie:
            raise ValidationError(
                "Aucune modification fournie pour la session",
                details={"session_id": str(session_id)},
            )
        self._trace.record_event(
            actor_type=ActorType.HUMAIN.value,
            action=ActionAudit.SESSION_MODIFIEE.value,
            entity_type=_ENTITY,
            entity_id=session_formation.id,
            after={
                "theme": session_formation.theme,
                "date_debut": (
                    session_formation.date_debut.isoformat()
                    if session_formation.date_debut
                    else None
                ),
                "date_fin": (
                    session_formation.date_fin.isoformat()
                    if session_formation.date_fin
                    else None
                ),
                "lieu": session_formation.lieu,
            },
        )
        return session_formation

    def obtenir(self, session_id: UUID) -> SessionFormation:
        """Charge une session (lecture pure, sans mutation)."""
        return self._get(session_id)

    def lister_pour_mission(self, mission_id: UUID) -> list[SessionFormation]:
        """Sessions d'une mission du périmètre (404 sinon, comme la mission).

        Une mission porte **une** session (ADR 0012) : la liste reste la
        signature historique, elle contient au plus un élément.
        """
        if self.missions.get(mission_id) is None or not self._perimetre.mission_autorisee(
            mission_id
        ):
            raise NotFoundError(f"Mission {mission_id} introuvable")
        return self.sessions.list_for_mission(mission_id)

    def lister(
        self, *, mission_id: UUID | None = None, statut: str | None = None
    ) -> list[SessionFormation]:
        """Sessions toutes missions confondues, filtres facultatifs (page UI).

        Contrairement à :meth:`lister_pour_mission`, aucune existence de mission
        n'est exigée : c'est une lecture de tableau de bord, pas une lecture
        dans le contexte d'une mission. Le périmètre restreint la liste aux
        missions autorisées (un formateur ne voit que ses sessions).
        """
        sessions = self.sessions.list_filtered(mission_id=mission_id, statut=statut)
        return [s for s in sessions if self._perimetre.mission_autorisee(s.mission_id)]

    # --- internes -----------------------------------------------------------

    def _get(self, session_id: UUID) -> SessionFormation:
        session_formation = self.sessions.get(session_id)
        if session_formation is None or not self._perimetre.mission_autorisee(
            session_formation.mission_id
        ):
            raise NotFoundError(f"Session {session_id} introuvable")
        return session_formation


__all__ = ["SessionService"]
