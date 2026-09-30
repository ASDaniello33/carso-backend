"""Service métier — registre des modèles de documents (instruction/06 §7).

```text
Organisation cliente
  ↓
ModeleDocument (versionné)
  ↓
Document de type 'template'  (fichier physique, jamais écrasé)
```

Une organisation cliente peut imposer son propre modèle de document. Le registre
associe un couple (organisation, type de document) à un fichier modèle, et il est
**versionné** : enregistrer un nouveau modèle crée une version supplémentaire,
l'ancienne reste consultable et le fichier associé n'est jamais modifié.

Aucun modèle « par défaut » n'est appliqué : si aucun modèle n'existe pour un
couple (organisation, type), la résolution échoue explicitement. Inventer un
modèle de repli produirait des documents au format non validé par CARSO.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from app.application.trace import TraceContext
from app.core.errors import NotFoundError, ValidationError
from app.domain.enums import ActionAudit, ActorType, TypeDocument
from app.domain.execution import ModeleDocument
from app.infrastructure.repositories import (
    DocumentRepository,
    ModeleDocumentRepository,
    OrganisationRepository,
)

_ENTITY = "modele_document"
_MAX_NOM = 255


class ModeleDocumentService:
    """Use cases : enregistrer un modèle, résoudre le modèle actif, lister."""

    def __init__(self, session: Session, actor_id: str | None = None) -> None:
        self.modeles = ModeleDocumentRepository(session)
        self._documents = DocumentRepository(session)
        self._organisations = OrganisationRepository(session)
        self._session = session
        self._trace = TraceContext(session, actor_id=actor_id)

    def enregistrer_modele(
        self,
        organisation_id: UUID,
        *,
        nom: str,
        type_document: str,
        document_template_id: UUID,
        registered_by: str | None = None,
    ) -> ModeleDocument:
        """Enregistre une nouvelle version de modèle pour (organisation, type).

        Le fichier modèle doit être un ``Document`` de type ``template`` : le
        registre référence des documents du référentiel documentaire, il ne
        duplique aucun fichier.

        Raises:
            NotFoundError: organisation ou document modèle inexistant.
            ValidationError: type de document inconnu ou document non « template ».
        """
        if self._organisations.get(organisation_id) is None:
            raise NotFoundError(f"Organisation {organisation_id} introuvable")

        gabarit = self._documents.get(document_template_id)
        if gabarit is None:
            raise NotFoundError(f"Document {document_template_id} introuvable")
        if gabarit.type_document != TypeDocument.TEMPLATE.value:
            msg = (
                "Le document fourni doit être de type 'template' pour servir de "
                "modèle à une organisation cliente"
            )
            raise ValidationError(
                msg, details={"type_document": gabarit.type_document}
            )

        type_valide = self._valider_type(type_document)
        libelle = self._valider_libelle(nom)
        version = self.modeles.current_version(organisation_id, type_valide) + 1

        modele = ModeleDocument(
            organisation_id=organisation_id,
            nom=libelle,
            type_document=type_valide,
            document_template_id=document_template_id,
            version=version,
        )
        self.modeles.add(modele)
        self._session.flush()

        self._trace.record_event(
            actor_type=ActorType.HUMAIN.value,
            action=ActionAudit.MODELE_DOCUMENT_ENREGISTRE.value,
            entity_type=_ENTITY,
            entity_id=modele.id,
            after={"version": version, "type_document": type_valide},
            event_metadata={"registered_by": registered_by} if registered_by else None,
        )
        return modele

    def resoudre_modele_actif(self, organisation_id: UUID, type_document: str) -> ModeleDocument:
        """Modèle de plus haute version pour ce couple (organisation, type).

        Raises:
            NotFoundError: aucun modèle enregistré — aucun repli silencieux.
        """
        modele = self.modeles.find_active(organisation_id, type_document)
        if modele is None:
            msg = (
                "Aucun modèle de document enregistré pour cette organisation et ce "
                "type : aucun modèle par défaut n'est appliqué"
            )
            raise NotFoundError(
                msg,
                details={
                    "organisation_id": str(organisation_id),
                    "type_document": type_document,
                },
            )
        return modele

    def lister(self, organisation_id: UUID) -> list[ModeleDocument]:
        """Modèles d'une organisation (toutes versions)."""
        return self.modeles.list_for_organisation(organisation_id)

    def obtenir(self, modele_id: UUID) -> ModeleDocument:
        """Charge un modèle (lecture pure)."""
        modele = self.modeles.get(modele_id)
        if modele is None:
            raise NotFoundError(f"Modèle de document {modele_id} introuvable")
        return modele

    # --- internes -----------------------------------------------------------

    def _valider_type(self, type_document: str) -> str:
        valides = {type_.value for type_ in TypeDocument}
        if type_document not in valides:
            msg = f"Type de document inconnu : {type_document!r}"
            raise ValidationError(msg, details={"types_valides": sorted(valides)})
        return type_document

    def _valider_libelle(self, nom: str) -> str:
        libelle = (nom or "").strip()
        if not libelle:
            raise ValidationError("Le libellé du modèle est obligatoire")
        if len(libelle) > _MAX_NOM:
            raise ValidationError(f"Libellé de modèle trop long (max {_MAX_NOM} caractères)")
        return libelle


__all__ = ["ModeleDocumentService"]
