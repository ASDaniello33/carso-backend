"""Service métier — bénéficiaires (Lot C1, instruction/02 §G).

Fonctions CRUD simples ; l'import Excel complet (détection colonnes, doublons,
preview, approbation, import transactionnel — instruction/06 §8) est prévu en
Lot T1 avec les outils documentaires. Données sensibles minimisées [? Q9] :
seuls les champs du modèle confirmé sont saisis.

Mutations tracées par AuditEvent (règle 10), sans Approbation. Ni commit ni
rollback (instruction/04 §5).
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from app.application.dto import BeneficiaireInput, BeneficiaireUpdateInput
from app.application.trace import TraceContext
from app.core.errors import NotFoundError, ValidationError
from app.domain.enums import ActionAudit, ActorType, StatutBeneficiaire
from app.domain.execution import Beneficiaire
from app.infrastructure.repositories import BeneficiaireRepository

_ENTITY = "beneficiaire"

_CHAMPS = ("nom", "prenom", "contact", "organisation_origine", "identifiant_externe")


class BeneficiaireService:
    """Use cases : creer, modifier, obtenir, lister."""

    def __init__(self, session: Session, actor_id: str | None = None) -> None:
        self._session = session
        self.beneficiaires = BeneficiaireRepository(session)
        self._trace = TraceContext(session, actor_id=actor_id)

    def creer(self, entree: BeneficiaireInput) -> Beneficiaire:
        """Enregistre un bénéficiaire."""
        beneficiaire = Beneficiaire(
            nom=entree.nom,
            prenom=entree.prenom,
            contact=entree.contact,
            organisation_origine=entree.organisation_origine,
            identifiant_externe=entree.identifiant_externe,
        )
        self.beneficiaires.add(beneficiaire)
        self._session.flush()
        self._trace.record_event(
            actor_type=ActorType.HUMAIN.value,
            action=ActionAudit.BENEFICIAIRE_ENREGISTRE.value,
            entity_type=_ENTITY,
            entity_id=beneficiaire.id,
            after={"nom": beneficiaire.nom, "prenom": beneficiaire.prenom},
        )
        return beneficiaire

    def modifier(self, beneficiaire_id: UUID, entree: BeneficiaireUpdateInput) -> Beneficiaire:
        """Modification partielle : seuls les champs fournis sont appliqués.

        Raises:
            ValidationError: aucune modification fournie.
        """
        beneficiaire = self._get(beneficiaire_id)
        modifie = False
        for nom in _CHAMPS:
            valeur = getattr(entree, nom)
            if valeur is not None and getattr(beneficiaire, nom) != valeur:
                setattr(beneficiaire, nom, valeur)
                modifie = True
        if not modifie:
            raise ValidationError(
                "Aucune modification fournie pour le bénéficiaire",
                details={"beneficiaire_id": str(beneficiaire_id)},
            )
        self._trace.record_event(
            actor_type=ActorType.HUMAIN.value,
            action=ActionAudit.BENEFICIAIRE_MODIFIE.value,
            entity_type=_ENTITY,
            entity_id=beneficiaire.id,
            after={"nom": beneficiaire.nom, "prenom": beneficiaire.prenom},
        )
        return beneficiaire

    def obtenir(self, beneficiaire_id: UUID) -> Beneficiaire:
        """Consultation (lecture pure)."""
        return self._get(beneficiaire_id)

    def archiver(self, beneficiaire_id: UUID) -> Beneficiaire:
        """Archive un bénéficiaire (20/09) : réversible, historique conservé.

        Les participations passées restent lisibles et pointées ; la personne
        cesse simplement d'être proposée aux nouvelles inscriptions.
        """
        return self._changer_statut(
            beneficiaire_id,
            StatutBeneficiaire.ARCHIVE.value,
            ActionAudit.BENEFICIAIRE_ARCHIVE.value,
        )

    def reactiver(self, beneficiaire_id: UUID) -> Beneficiaire:
        """Remet un bénéficiaire archivé dans les listes actives."""
        return self._changer_statut(
            beneficiaire_id,
            StatutBeneficiaire.ACTIF.value,
            ActionAudit.BENEFICIAIRE_REACTIVE.value,
        )

    def _changer_statut(self, beneficiaire_id: UUID, cible: str, action: str) -> Beneficiaire:
        beneficiaire = self._get(beneficiaire_id)
        avant = beneficiaire.statut
        if avant == cible:
            raise ValidationError(
                f"Ce bénéficiaire est déjà « {cible} »",
                details={"beneficiaire_id": str(beneficiaire_id), "statut": cible},
            )
        beneficiaire.statut = cible
        self._trace.record_event(
            actor_type=ActorType.HUMAIN.value,
            action=action,
            entity_type=_ENTITY,
            entity_id=beneficiaire.id,
            after={"statut": cible},
            event_metadata={"avant": avant},
        )
        return beneficiaire

    def lister(self, statut: str | None = None) -> list[Beneficiaire]:
        """Tous les bénéficiaires, ou seulement ceux d'un statut donné.

        Sans filtre, la liste reste complète (page Bénéficiaires : toutes
        sessions confondues) ; l'archivage n'efface rien.
        """
        if statut is None:
            return self.beneficiaires.list_all()
        return self.beneficiaires.list_by_statut(statut)

    def _get(self, beneficiaire_id: UUID) -> Beneficiaire:
        beneficiaire = self.beneficiaires.get(beneficiaire_id)
        if beneficiaire is None:
            raise NotFoundError(f"Bénéficiaire {beneficiaire_id} introuvable")
        return beneficiaire


__all__ = ["BeneficiaireService"]
