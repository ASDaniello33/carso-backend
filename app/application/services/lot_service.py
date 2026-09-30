"""Service métier — lots d'un appel à proposition (Lot C1, instruction/02 §B).

Fonctions : créer/éditer les lots, consulter. Contraintes structurelles
portées par la base et revérifiées au service (instruction/09 §4) :

- le lot vit toujours rattaché à un appel à proposition existant (FK CASCADE) ;
- ``numero`` est unique par appel (``uq_lots_appel_a_proposition_id_numero``) — le
  doublon est refusé en service (409) avant d'atteindre la contrainte.

Mutations tracées par AuditEvent (règle 10), sans Approbation. Ni commit ni
rollback (instruction/04 §5).
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from app.application.dto import LotInput, LotUpdateInput
from app.application.trace import TraceContext
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.domain.enums import ActionAudit, ActorType
from app.domain.organization import Lot
from app.infrastructure.repositories import (
    AppelAPropositionRepository,
    LotRepository,
)

_ENTITY = "lot"

_CHAMPS = (
    "numero",
    "titre",
    "zone",
    "objectifs",
    "resultats_attendus",
    "mission_description",
    "partenariat",
    "date_fin",
    "donnees_source",
)


class LotService:
    """Use cases : ajouter, modifier, obtenir, lister_pour_appel_a_proposition."""

    def __init__(self, session: Session, actor_id: str | None = None) -> None:
        self._session = session
        self.lots = LotRepository(session)
        self.appels = AppelAPropositionRepository(session)
        self._trace = TraceContext(session, actor_id=actor_id)

    def ajouter(self, entree: LotInput) -> Lot:
        """Crée un lot rattaché à un appel existant.

        Raises:
            NotFoundError: appel à proposition introuvable.
            ConflictError: ``numero`` déjà utilisé pour cet appel.
        """
        if self.appels.get(entree.appel_a_proposition_id) is None:
            raise NotFoundError(f"Appel à proposition {entree.appel_a_proposition_id} introuvable")
        if self.lots.get_by_numero(entree.appel_a_proposition_id, entree.numero) is not None:
            raise ConflictError(
                f"Le numéro de lot {entree.numero!r} existe déjà pour cet appel",
                details={
                    "appel_a_proposition_id": str(entree.appel_a_proposition_id),
                    "numero": entree.numero,
                },
            )
        lot = Lot(
            appel_a_proposition_id=entree.appel_a_proposition_id,
            numero=entree.numero,
            titre=entree.titre,
            zone=entree.zone,
            objectifs=entree.objectifs,
            resultats_attendus=entree.resultats_attendus,
            mission_description=entree.mission_description,
            partenariat=entree.partenariat,
            date_fin=entree.date_fin,
            donnees_source=entree.donnees_source,
        )
        self.lots.add(lot)
        self._session.flush()
        self._trace.record_event(
            actor_type=ActorType.HUMAIN.value,
            action=ActionAudit.LOT_ENREGISTRE.value,
            entity_type=_ENTITY,
            entity_id=lot.id,
            after={"numero": lot.numero, "titre": lot.titre},
        )
        return lot

    def modifier(self, lot_id: UUID, entree: LotUpdateInput) -> Lot:
        """Modification partielle. ``appel_a_proposition_id`` n'est jamais modifiable.

        Raises:
            ValidationError: aucune modification fournie.
            ConflictError: nouveau ``numero`` déjà pris par un autre lot.
        """
        lot = self._get(lot_id)
        modifie = False
        for nom in _CHAMPS:
            valeur = getattr(entree, nom)
            if valeur is None or getattr(lot, nom) == valeur:
                continue
            if nom == "numero":
                existant = self.lots.get_by_numero(lot.appel_a_proposition_id, valeur)
                if existant is not None and existant.id != lot.id:
                    raise ConflictError(
                        f"Le numéro de lot {valeur!r} existe déjà pour cet appel",
                        details={
                            "appel_a_proposition_id": str(lot.appel_a_proposition_id),
                            "numero": valeur,
                        },
                    )
            setattr(lot, nom, valeur)
            modifie = True
        if not modifie:
            raise ValidationError(
                "Aucune modification fournie pour le lot",
                details={"lot_id": str(lot_id)},
            )
        self._trace.record_event(
            actor_type=ActorType.HUMAIN.value,
            action=ActionAudit.LOT_MODIFIE.value,
            entity_type=_ENTITY,
            entity_id=lot.id,
            after={"numero": lot.numero, "titre": lot.titre},
        )
        return lot

    def obtenir(self, lot_id: UUID) -> Lot:
        """Consultation (lecture pure)."""
        return self._get(lot_id)

    def lister_pour_appel_a_proposition(self, appel_a_proposition_id: UUID) -> list[Lot]:
        """Lots d'un appel (l'appel doit exister)."""
        if self.appels.get(appel_a_proposition_id) is None:
            raise NotFoundError(f"Appel à proposition {appel_a_proposition_id} introuvable")
        return self.lots.list_for_appel_a_proposition(appel_a_proposition_id)

    def _get(self, lot_id: UUID) -> Lot:
        lot = self.lots.get(lot_id)
        if lot is None:
            raise NotFoundError(f"Lot {lot_id} introuvable")
        return lot


__all__ = ["LotService"]
