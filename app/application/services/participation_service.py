"""Service métier — participations (Lot C1, instruction/02 §G).

Session ↔ Beneficiaire, unique par couple (contrainte
``uq_participations_session_beneficiaire``). Fonctions : inscrire, compléter
évaluation/observations, retirer, lister.

**Le pointage n'est plus ici** (règle confirmée CARSO, 24/09) : la présence est
un pointage daté qui vit dans ``PresenceService`` et dans la table
``presences`` — une ligne par ``(participation, date)``. Ne jamais réintroduire
une présence sur l'inscription : ce serait une deuxième vérité pour le même fait.

Mutations tracées par AuditEvent (règle 10), sans Approbation. Ni commit ni
rollback (instruction/04 §5).
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from app.application.dto import (
    ParticipationInscriptionInput,
    ParticipationUpdateInput,
)
from app.application.trace import TraceContext
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.domain.enums import ActionAudit, ActorType
from app.domain.execution import Participation
from app.infrastructure.repositories import (
    BeneficiaireRepository,
    ParticipationRepository,
    PresenceRepository,
    SessionRepository,
)

_ENTITY = "participation"


class ParticipationService:
    """Use cases : inscrire, modifier, retirer, lister_pour_session."""

    def __init__(self, session: Session, actor_id: str | None = None) -> None:
        self._session = session
        self.participations = ParticipationRepository(session)
        # Lu pour compter les pointages au retrait — jamais écrit ici : la règle
        # de pointage appartient à ``PresenceService``.
        self.presences = PresenceRepository(session)
        self.sessions = SessionRepository(session)
        self.beneficiaires = BeneficiaireRepository(session)
        self._trace = TraceContext(session, actor_id=actor_id)

    def inscrire(self, session_id: UUID, entree: ParticipationInscriptionInput) -> Participation:
        """Inscrit un bénéficiaire à une session.

        Raises:
            NotFoundError: session ou bénéficiaire introuvable.
            ConflictError: bénéficiaire déjà inscrit à cette session.
        """
        if self.sessions.get(session_id) is None:
            raise NotFoundError(f"Session {session_id} introuvable")
        if self.beneficiaires.get(entree.beneficiaire_id) is None:
            raise NotFoundError(f"Bénéficiaire {entree.beneficiaire_id} introuvable")
        if self.participations.get_for_couple(session_id, entree.beneficiaire_id) is not None:
            raise ConflictError(
                "Ce bénéficiaire est déjà inscrit à cette session",
                details={
                    "session_id": str(session_id),
                    "beneficiaire_id": str(entree.beneficiaire_id),
                },
            )
        participation = Participation(
            session_id=session_id,
            beneficiaire_id=entree.beneficiaire_id,
        )
        self.participations.add(participation)
        self._session.flush()
        self._trace.record_event(
            actor_type=ActorType.HUMAIN.value,
            action=ActionAudit.PARTICIPATION_INSCRITE.value,
            entity_type=_ENTITY,
            entity_id=participation.id,
            after={
                "session_id": str(session_id),
                "beneficiaire_id": str(entree.beneficiaire_id),
            },
        )
        return participation

    def modifier(self, participation_id: UUID, entree: ParticipationUpdateInput) -> Participation:
        """Complète évaluation/observations.

        La présence n'est pas modifiable ici : elle se pointe dans
        ``PresenceService``, pour une date précise.

        Raises:
            ValidationError: aucune modification fournie.
        """
        participation = self._get(participation_id)
        modifie = False
        for nom in ("evaluation", "observations"):
            valeur = getattr(entree, nom)
            if valeur is None or getattr(participation, nom) == valeur:
                continue
            setattr(participation, nom, valeur)
            modifie = True
        if not modifie:
            raise ValidationError(
                "Aucune modification fournie pour la participation",
                details={"participation_id": str(participation_id)},
            )
        self._trace.record_event(
            actor_type=ActorType.HUMAIN.value,
            action=ActionAudit.PARTICIPATION_MODIFIEE.value,
            entity_type=_ENTITY,
            entity_id=participation.id,
        )
        return participation

    def retirer(self, participation_id: UUID) -> None:
        """Retire une personne d'une session (désinscription).

        La participation est un **lien**, pas une donnée métier : la personne et
        la session survivent. Des pointages déjà enregistrés n'empêchent pas le
        retrait, mais ils **partent avec l'inscription** (``ON DELETE CASCADE``)
        : un pointage sans inscription n'a aucun sens. Leur nombre est conservé
        dans la trace, qui porte l'historique.
        """
        participation = self._get(participation_id)
        pointages = len(self.presences.list_for_participation(participation.id))
        self._trace.record_event(
            actor_type=ActorType.HUMAIN.value,
            action=ActionAudit.PARTICIPATION_RETIREE.value,
            entity_type=_ENTITY,
            entity_id=participation.id,
            after={
                "session_id": str(participation.session_id),
                "beneficiaire_id": str(participation.beneficiaire_id),
                "pointages_retires": pointages,
            },
        )
        self.participations.delete(participation)

    def obtenir(self, participation_id: UUID) -> Participation:
        """Consultation (lecture pure)."""
        return self._get(participation_id)

    def lister_pour_session(self, session_id: UUID) -> list[Participation]:
        """Participations d'une session (la session doit exister)."""
        if self.sessions.get(session_id) is None:
            raise NotFoundError(f"Session {session_id} introuvable")
        return self.participations.list_for_session(session_id)

    def _get(self, participation_id: UUID) -> Participation:
        participation = self.participations.get(participation_id)
        if participation is None:
            raise NotFoundError(f"Participation {participation_id} introuvable")
        return participation


__all__ = ["ParticipationService"]
