"""Schémas API — documents, extraction et modèles de documents.

Les schémas traduisent les contrats applicatifs (``app/application/dto.py``).
Le fichier lui-même ne transite jamais par un schéma : il arrive en ``multipart``
et repart en ``FileResponse``.
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class DocumentRead(BaseModel):
    """Métadonnées d'un document (le ``storage_path`` exposé est **logique**)."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    nom: str
    type_document: str
    mime_type: str | None = None
    taille_octets: int | None = None
    storage_path: str
    storage_disk: str | None = None
    checksum_sha256: str | None = None
    version: int
    statut: str
    organisation_id: UUID | None = None
    appel_a_proposition_id: UUID | None = None
    offre_id: UUID | None = None
    mission_id: UUID | None = None
    equipe_id: UUID | None = None
    session_id: UUID | None = None
    created_by: str | None = None
    created_at: datetime
    updated_at: datetime


class DocumentMetadonneesUpdate(BaseModel):
    """Métadonnées d'un document — jamais son contenu.

    Le contenu se remplace par une nouvelle version (``POST .../versions``).
    ``nom`` et ``type_document`` ne sont acceptés que sur un document à une seule
    version : ils forment la clé de regroupement des versions.
    """

    nom: str | None = Field(default=None, min_length=1, max_length=255)
    type_document: str | None = Field(default=None, min_length=1, max_length=50)
    doc_metadata: dict[str, Any] | None = None


class DocumentDecisionCreate(BaseModel):
    """Décision humaine. ``decided_by`` vient du JWT."""

    reason: str | None = Field(default=None, max_length=2000)


class DocumentPurgeCreate(BaseModel):
    """Purge définitive d'une fiche de la corbeille (administrateurs).

    ``reason`` est **obligatoire** ici, contrairement aux autres décisions : la
    purge efface la dernière trace du document, un motif vide rendrait l'audit
    muet sur le geste le plus destructeur du système.
    """

    reason: str = Field(min_length=1, max_length=2000)


class DocumentCorbeilleRead(BaseModel):
    """Fiche supprimée telle que la corbeille l'expose (ADR 0006).

    Le fichier n'est plus sur le disque (sauf s'il y est revenu) : ``fichier_present``
    dit s'il faut le redéposer pour restaurer, et ``restaurable`` / ``blocage``
    disent si la restauration est possible — le calcul vient du service, jamais de
    l'interface.
    """

    document_id: UUID
    nom: str
    type_document: str
    version: int
    statut_avant: str
    supprime_le: datetime | None = None
    supprime_par: str | None = None
    motif: str | None = None
    expire_le: datetime | None = None
    expiree: bool
    jours_restants: int | None = None
    retention_jours: int
    fichier_present: bool
    restaurable: bool
    blocage: str | None = None
    #: Ancres conservées : la corbeille se filtre par fiche métier.
    organisation_id: UUID | None = None
    appel_a_proposition_id: UUID | None = None
    offre_id: UUID | None = None
    mission_id: UUID | None = None
    equipe_id: UUID | None = None
    session_id: UUID | None = None


class RapportPurgeRead(BaseModel):
    """Ce qu'une purge définitive a effectivement effacé."""

    document_id: UUID
    fichiers_supprimes: int = 0


class ExtractionRead(BaseModel):
    """Texte extrait d'un document (lecture seule, bornée par la configuration)."""

    document_id: UUID
    nom: str
    extension: str
    mime_type: str | None = None
    adaptateur: str
    texte: str
    nb_caracteres: int
    nb_pages: int | None = None
    nb_feuilles: int | None = None
    feuilles: list[str] = Field(default_factory=list)
    tronque: bool


class ModeleDocumentCreate(BaseModel):
    """Enregistrement d'un modèle de document pour une organisation cliente."""

    nom: str = Field(min_length=1, max_length=255)
    type_document: str = Field(min_length=1, max_length=50)
    document_template_id: UUID


class ModeleDocumentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    organisation_id: UUID | None = None
    nom: str
    type_document: str
    document_template_id: UUID
    version: int
    statut: str | None = None
    created_at: datetime
    updated_at: datetime
