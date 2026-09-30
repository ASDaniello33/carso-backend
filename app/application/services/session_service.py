"""Service métier — sessions de formation (Lot L0, instruction/06 §6).

Règles appliquées :

- une session vit **toujours rattachée à une mission existante** (FK CASCADE) ;
- les transitions suivent la machine à états ``session``
  (planifiee → confirmee → realisee, annulable) — aucune règle inventée ;
- traçabilité : planification et changement de statut écrivent un
  ``AuditEvent`` dans la transaction courante ;
- le service ne fait ni commit ni rollback (instruction/04 §5).
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from app.application.dto import SessionPlanificationInput, SessionUpdateInput
from app.application.services import notifications_evenements as notifications
from app.application.trace import TraceContext
from app.core.errors import NotFoundError, ValidationError
from app.domain.enums import ActionAudit, ActorType, StatutSessionFormation
from app.domain.execution import SessionFormation
from app.domain.state_machines import validate_transition
from app.infrastructure.repositories import MissionRepository, SessionRepository

_ENTITY = "session"


class SessionService:
    """Use cases : planifier, changer_statut, obtenir, lister_pour_mission."""

    def __init__(self, session: Session, actor_id: str | None = None) -> None:
        self._session = session
        self.sessions = SessionRepository(session)
        self.missions = MissionRepository(session)
        self._trace = TraceContext(session, actor_id=actor_id)

    def planifier(self, entree: SessionPlanificationInput) -> SessionFormation:
        """Planifie une session rattachée à une mission existante (statut ``planifiee``)."""
        if self.missions.get(entree.mission_id) is None:
            raise NotFoundError(f"Mission {entree.mission_id} introuvable")

        session_formation = SessionFormation(
            mission_id=entree.mission_id,
            theme=entree.theme,
            date_debut=entree.date_debut,
            date_fin=entree.date_fin,
            lieu=entree.lieu,
            statut=StatutSessionFormation.PLANIFIEE.value,
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
                "statut": StatutSessionFormation.PLANIFIEE.value,
            },
        )
        notifications.notifier_session_changement(
            self._session,
            session_formation,
            session_formation.mission.titre,
            "planifiee",
        )
        return session_formation

    def changer_statut(self, session_id: UUID, cible: str) -> SessionFormation:
        """Transition planifiee → confirmee → realisee (annulable), machine à états.

        Raises:
            ValidationError: statut cible inconnu.
            ConflictError: transition interdite.
        """
        vocabulaire = {s.value for s in StatutSessionFormation}
        if cible not in vocabulaire:
            raise ValidationError(
                f"Statut de session inconnu : {cible!r}",
                details={"statuts_valides": sorted(vocabulaire)},
            )

        session_formation = self._get(session_id)
        validate_transition(_ENTITY, session_formation.statut, cible)
        session_formation.statut = cible
        self._trace.record_event(
            actor_type=ActorType.HUMAIN.value,
            action=ActionAudit.SESSION_STATUT_CHANGE.value,
            entity_type=_ENTITY,
            entity_id=session_formation.id,
            after={"statut": cible},
        )
        notifications.notifier_session_changement(
            self._session,
            session_formation,
            session_formation.mission.titre,
            cible,
        )
        return session_formation

    def modifier(self, session_id: UUID, entree: SessionUpdateInput) -> SessionFormation:
        """Modification partielle : thème, dates, lieu.

        La mission n'est jamais modifiable ici : une session vit dans le contexte
        d'une mission (la rattacher ailleurs serait une autre session). Un champ
        absent (``None``) n'est pas appliqué — le formulaire d'interface envoie
        seulement ce qui a changé.

        Raises:
            ValidationError: aucune modification fournie.
        """
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
        """Sessions d'une mission (la mission doit exister)."""
        if self.missions.get(mission_id) is None:
            raise NotFoundError(f"Mission {mission_id} introuvable")
        return self.sessions.list_for_mission(mission_id)

    def lister(
        self, *, mission_id: UUID | None = None, statut: str | None = None
    ) -> list[SessionFormation]:
        """Sessions toutes missions confondues, filtres facultatifs (page UI).

        Contrairement à :meth:`lister_pour_mission`, aucune existence de mission
        n'est exigée : c'est une lecture de tableau de bord, pas une lecture
        dans le contexte d'une mission.
        """
        return self.sessions.list_filtered(mission_id=mission_id, statut=statut)

    # --- internes -----------------------------------------------------------

    def _get(self, session_id: UUID) -> SessionFormation:
        session_formation = self.sessions.get(session_id)
        if session_formation is None:
            raise NotFoundError(f"Session {session_id} introuvable")
        return session_formation


__all__ = ["SessionService"]
