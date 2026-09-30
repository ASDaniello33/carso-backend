"""Service métier — supports de formation (Formateur ↔ Mission ↔ Document).

Règles appliquées :

- le **document** est l'entité documentaire unique (``Document``) : un support
  ne crée jamais un second système documentaire ;
- le **formateur** est une personne du vivier affectée à la mission avec un rôle
  qui vaut Formateur (``est_formateur``) — on refuse une personne non affectée
  ou non formatrice plutôt que de l'accepter silencieusement ;
- la triple relation est unique (``mission`` + ``equipe`` + ``document``).
"""

from uuid import UUID

from sqlalchemy.orm import Session

from app.application.dto import SupportFormationInput
from app.application.trace import TraceContext
from app.core.errors import ConflictError, NotFoundError
from app.domain.enums import ActionAudit, ActorType, est_formateur
from app.domain.execution import SupportFormation
from app.infrastructure.repositories import (
    AffectationEquipeRepository,
    DocumentRepository,
    EquipeRepository,
    MissionRepository,
    SupportFormationRepository,
)

_ENTITY = "support_formation"


class SupportFormationService:
    """Use cases : ajouter, retirer, lister_pour_mission, lister_pour_formateur."""

    def __init__(self, session: Session, actor_id: str | None = None) -> None:
        self._session = session
        self.supports = SupportFormationRepository(session)
        self.missions = MissionRepository(session)
        self.equipes = EquipeRepository(session)
        self.documents = DocumentRepository(session)
        self.affectations = AffectationEquipeRepository(session)
        self._trace = TraceContext(session, actor_id=actor_id)

    def ajouter(self, entree: SupportFormationInput) -> SupportFormation:
        """Rattache un document à un formateur pour une mission donnée."""
        mission = self.missions.get(entree.mission_id)
        if mission is None:
            raise NotFoundError(f"Mission {entree.mission_id} introuvable")
        if self.equipes.get(entree.equipe_id) is None:
            raise NotFoundError(f"Équipe {entree.equipe_id} introuvable")
        if self.documents.get(entree.document_id) is None:
            raise NotFoundError(f"Document {entree.document_id} introuvable")

        self._exiger_formateur(mission.id, entree.equipe_id)

        if (
            self.supports.get_for(entree.mission_id, entree.equipe_id, entree.document_id)
            is not None
        ):
            raise ConflictError(
                "Ce document est déjà un support de formation pour ce formateur "
                "dans cette mission",
                details={
                    "mission_id": str(entree.mission_id),
                    "equipe_id": str(entree.equipe_id),
                    "document_id": str(entree.document_id),
                },
            )

        support = SupportFormation(
            mission_id=entree.mission_id,
            equipe_id=entree.equipe_id,
            document_id=entree.document_id,
            libelle=entree.libelle,
        )
        self.supports.add(support)
        self._session.flush()
        self._trace.record_event(
            actor_type=ActorType.HUMAIN.value,
            action=ActionAudit.SUPPORT_FORMATION_AJOUTE.value,
            entity_type=_ENTITY,
            entity_id=support.id,
            after={
                "mission_id": str(support.mission_id),
                "equipe_id": str(support.equipe_id),
                "document_id": str(support.document_id),
            },
        )
        return support

    def retirer(self, support_id: UUID) -> None:
        """Retire un support (aucun fichier n'est détruit — seul le lien part)."""
        support = self.supports.get(support_id)
        if support is None:
            raise NotFoundError(f"Support de formation {support_id} introuvable")
        self._trace.record_event(
            actor_type=ActorType.HUMAIN.value,
            action=ActionAudit.SUPPORT_FORMATION_RETIRE.value,
            entity_type=_ENTITY,
            entity_id=support.id,
            after={"document_id": str(support.document_id)},
        )
        self.supports.delete(support)

    def lister_pour_mission(self, mission_id: UUID) -> list[SupportFormation]:
        return self.supports.list_for_mission(mission_id)

    def lister_pour_formateur(
        self, mission_id: UUID, equipe_id: UUID
    ) -> list[SupportFormation]:
        return self.supports.list_for_equipe(mission_id, equipe_id)

    # --- internes ---------------------------------------------------------

    def _exiger_formateur(self, mission_id: UUID, equipe_id: UUID) -> None:
        """Refuse une personne qui n'est pas formateur dans cette mission.

        Règle confirmée : un Chef de Mission est également un Formateur
        (``est_formateur`` couvre les deux). Aucune affectation sur la mission
        ⇒ aucun support possible.
        """
        affectations = self.affectations.list_for_mission(mission_id)
        correspondantes = [a for a in affectations if a.equipe_id == equipe_id]
        if not correspondantes:
            raise ConflictError(
                "Cette personne n'est pas affectée à la mission",
                details={"mission_id": str(mission_id), "equipe_id": str(equipe_id)},
            )
        if not any(est_formateur(a.role_dans_mission) for a in correspondantes):
            raise ConflictError(
                "Le support de formation doit être porté par un formateur "
                "(un chef de mission est également formateur)",
                details={
                    "equipe_id": str(equipe_id),
                    "roles": sorted({a.role_dans_mission for a in correspondantes}),
                },
            )


__all__ = ["SupportFormationService"]
