"""Service métier — organisations clientes (Lot C1, instruction/02 §A).

Fonctions : créer, consulter, rechercher, modifier selon droits. L'archivage
définitif (instruction/02 §A « archiver selon règle ») n'est pas codé : la
règle d'archivage des organisations n'a pas été confirmée par CARSO [P].

Chaque mutation écrit un ``AuditEvent`` (règle 10) ; pas d'Approbation :
créer/modifier une organisation n'est pas la validation d'une proposition
d'agent. Le service ne fait ni commit ni rollback (instruction/04 §5).
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from app.application.dto import OrganisationInput, OrganisationUpdateInput
from app.application.trace import TraceContext
from app.core.errors import NotFoundError, ValidationError
from app.domain.enums import ActionAudit, ActorType
from app.domain.organization import Organisation
from app.infrastructure.repositories import OrganisationRepository

_ENTITY = "organisation"


def _appliquer(organisation: Organisation, champs: dict[str, str | None]) -> bool:
    """Applique les champs non ``None`` ; renvoie True si au moins un champ a changé."""
    modifie = False
    for nom, valeur in champs.items():
        if valeur is not None and getattr(organisation, nom) != valeur:
            setattr(organisation, nom, valeur)
            modifie = True
    return modifie


class OrganisationService:
    """Use cases : creer, modifier, obtenir, lister."""

    def __init__(self, session: Session, actor_id: str | None = None) -> None:
        self._session = session
        self.organisations = OrganisationRepository(session)
        self._trace = TraceContext(session, actor_id=actor_id)

    def creer(self, entree: OrganisationInput) -> Organisation:
        """Enregistre une organisation cliente."""
        organisation = Organisation(
            nom=entree.nom,
            type=entree.type,
            adresse=entree.adresse,
            email=entree.email,
            telephone=entree.telephone,
            statut=entree.statut,
        )
        self.organisations.add(organisation)
        self._session.flush()
        self._trace.record_event(
            actor_type=ActorType.HUMAIN.value,
            action=ActionAudit.ORGANISATION_ENREGISTREE.value,
            entity_type=_ENTITY,
            entity_id=organisation.id,
            after={"nom": organisation.nom},
        )
        return organisation

    def modifier(self, organisation_id: UUID, entree: OrganisationUpdateInput) -> Organisation:
        """Modification partielle : seuls les champs fournis sont appliqués.

        Raises:
            ValidationError: aucune modification fournie.
        """
        organisation = self._get(organisation_id)
        champs = {
            "nom": entree.nom,
            "type": entree.type,
            "adresse": entree.adresse,
            "email": entree.email,
            "telephone": entree.telephone,
            "statut": entree.statut,
        }
        if not _appliquer(organisation, champs):
            raise ValidationError(
                "Aucune modification fournie pour l'organisation",
                details={"organisation_id": str(organisation_id)},
            )
        self._trace.record_event(
            actor_type=ActorType.HUMAIN.value,
            action=ActionAudit.ORGANISATION_MODIFIEE.value,
            entity_type=_ENTITY,
            entity_id=organisation.id,
            after={"nom": organisation.nom},
        )
        return organisation

    def obtenir(self, organisation_id: UUID) -> Organisation:
        """Consultation (lecture pure)."""
        return self._get(organisation_id)

    def lister(self) -> list[Organisation]:
        """Toutes les organisations (usage contrôlé : référentiel modeste)."""
        return self.organisations.list_all()

    def _get(self, organisation_id: UUID) -> Organisation:
        organisation = self.organisations.get(organisation_id)
        if organisation is None:
            raise NotFoundError(f"Organisation {organisation_id} introuvable")
        return organisation


__all__ = ["OrganisationService"]
