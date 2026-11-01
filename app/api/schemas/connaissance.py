"""Schémas de la base de connaissance (bibliothèque, recherche, embedding).

Les schémas Read projettent l'ORM ; les schémas d'entrée bornent ce qu'un
appelant peut envoyer (limite de résultats, longueur des champs). Aucun
schéma ne porte de chemin physique.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ConnaissanceRead(BaseModel):
    """Fiche d'un ouvrage de la bibliothèque (sans chemin disque)."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    titre: str
    auteur: str | None = None
    editeur: str | None = None
    annee: int | None = None
    categorie: str | None = None
    langue: str
    description: str | None = None
    mots_cles: list[str] = Field(default_factory=list)
    nom_fichier: str
    mime_type: str | None = None
    taille_octets: int
    nombre_pages: int | None = None
    nombre_caracteres: int
    nombre_chunks: int
    statut: str
    erreur: str | None = None
    signature_embedding: str | None = None
    date_indexation: datetime | None = None
    ajoute_par_id: UUID | None = None
    created_at: datetime
    updated_at: datetime


class ConnaissanceMiseAJour(BaseModel):
    """Correction de la fiche : un champ absent garde sa valeur."""

    titre: str | None = Field(default=None, max_length=500)
    auteur: str | None = Field(default=None, max_length=255)
    editeur: str | None = Field(default=None, max_length=255)
    annee: int | None = Field(default=None, ge=1000, le=2200)
    categorie: str | None = Field(default=None, max_length=100)
    langue: str | None = Field(default=None, max_length=10)
    description: str | None = None
    mots_cles: list[str] | None = None


class ConnaissancePassageRead(BaseModel):
    """Passage indexé, avec sa provenance (page, section)."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    position: int
    page: int | None = None
    section: str | None = None
    contenu: str
    nombre_caracteres: int


class ConnaissanceRechercheCreate(BaseModel):
    """Entrée d'une recherche (humaine ou outil) : question naturelle."""

    question: str = Field(min_length=2, max_length=2000)
    limite: int = Field(default=5, ge=1, le=20)
    document_id: UUID | None = None
    categorie: str | None = Field(default=None, max_length=100)


class ConnaissancePassageTrouve(BaseModel):
    """Passage trouvé, prêt à être cité par un agent ou affiché à l'écran."""

    chunk_id: str
    document_id: str
    titre: str
    auteur: str | None = None
    categorie: str | None = None
    langue: str
    page: int | None = None
    section: str | None = None
    position: int
    similarite: float
    extrait: str


class ConnaissanceRechercheRead(BaseModel):
    """Résultat de recherche : passages sourcés, jamais un résumé inventé."""

    question: str
    nombre: int
    passages: list[ConnaissancePassageTrouve]
    signature: str
    message: str | None = None


class EmbeddingConfigurationRead(BaseModel):
    """Modèle d'embedding effectif (aucune clé, seulement sa disponibilité)."""

    provider: str
    model: str
    base_url: str | None = None
    dimensions: int
    signature: str
    source: str
    cle_disponible: bool


class EmbeddingConfigurationAdministreeRead(BaseModel):
    """Ligne administrée active (``actif`` vrai) ou dernière configurée."""

    provider: str
    model: str
    base_url: str | None = None
    dimensions: int
    signature: str
    cle_administree: bool
    actif: bool


class IndexConnaissanceRead(BaseModel):
    """État de l'index vectoriel local."""

    disponible: bool
    signature: str | None = None
    passages: int | None = None
    coherent: bool | None = None
    message: str | None = None


class ConnaissanceEtatRead(BaseModel):
    """Tableau de bord de la bibliothèque (volumétrie, index, modèle)."""

    ouvrages: int
    passages: int
    octets: int
    par_statut: dict[str, int]
    langues: list[str]
    categories: list[str]
    formats_supportes: list[str]
    configure: bool
    message_configuration: str | None = None
    configuration: EmbeddingConfigurationRead | None = None
    configuration_administree: EmbeddingConfigurationAdministreeRead | None = None
    index: IndexConnaissanceRead


class EmbeddingTestCreate(BaseModel):
    """Demande de mesure réelle d'un modèle (aucune écriture)."""

    provider: str = Field(min_length=1, max_length=50)
    model: str = Field(min_length=1, max_length=150)
    base_url: str | None = Field(default=None, max_length=500)
    api_key: str | None = Field(default=None, max_length=500)


class EmbeddingTestRead(BaseModel):
    """Résultat d'un appel réel : la dimension observée fait foi."""

    provider: str
    model: str
    dimensions: int
    signature: str
    latence_ms: int


class EmbeddingConfigurationCreate(BaseModel):
    """Enregistrement du modèle appliqué (validation réelle avant écriture)."""

    provider: str = Field(min_length=1, max_length=50)
    model: str = Field(min_length=1, max_length=150)
    base_url: str | None = Field(default=None, max_length=500)
    api_key: str | None = Field(default=None, max_length=500)
    conserver_cle: bool = True


class EmbeddingConfigurationEnregistreeRead(BaseModel):
    """Configuration appliquée et conséquence sur l'index."""

    configuration: EmbeddingConfigurationRead
    reindexation_requise: bool
    message: str


class ReindexationRead(BaseModel):
    """Nombre d'ouvrages replanifiés."""

    ouvrages: int
    message: str


class IndexationRapportRead(BaseModel):
    """Rapport d'une indexation (succès ou échec lisible)."""

    document_id: str
    statut: str
    nombre_chunks: int = 0
    nombre_caracteres: int = 0
    signature: str | None = None
    message: str | None = None
    tronque: bool = False


class ConnaissanceListeRead(BaseModel):
    """Liste et compteur (l'interface affiche le total sans le recalculer)."""

    elements: list[ConnaissanceRead]
    total: int


class ConnaissanceMessageRead(BaseModel):
    """Message simple (retrait, réveil du worker)."""

    message: str
    details: dict[str, Any] | None = None


__all__ = [
    "ConnaissanceEtatRead",
    "ConnaissanceListeRead",
    "ConnaissanceMessageRead",
    "ConnaissanceMiseAJour",
    "ConnaissancePassageRead",
    "ConnaissancePassageTrouve",
    "ConnaissanceRead",
    "ConnaissanceRechercheCreate",
    "ConnaissanceRechercheRead",
    "EmbeddingConfigurationAdministreeRead",
    "EmbeddingConfigurationCreate",
    "EmbeddingConfigurationEnregistreeRead",
    "EmbeddingConfigurationRead",
    "EmbeddingTestCreate",
    "EmbeddingTestRead",
    "IndexConnaissanceRead",
    "IndexationRapportRead",
    "ReindexationRead",
]
