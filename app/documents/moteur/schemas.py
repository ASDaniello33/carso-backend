"""Schémas du moteur documentaire (INC-128) — contrats, pas règles métier.

Trois familles, volontairement séparées :

- **analyse** : ce que le document contient réellement, tel que l'extracteur
  déterministe l'a lu (``Bloc``, ``AnalyseModele``) ;
- **proposition / configuration** : ce que l'IA propose (``ChampPropose``) et ce
  qu'un humain a validé (``ConfigurationModele``). Une proposition n'est jamais
  appliquée directement : elle doit passer par la configuration ;
- **génération** : les données fournies (``DonneesGeneration``) et le rapport de
  validation (``RapportValidation``).

Le vocabulaire de classification est fermé (STATIC / DYNAMIC / REPEATABLE /
CONDITIONAL) : une valeur hors de ce vocabulaire est refusée par Pydantic, pas
normalisée en silence.
"""

from __future__ import annotations

import re
import unicodedata
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

__all__ = [
    "TAG_PREFIX",
    "AnalyseModele",
    "Bloc",
    "CelluleBloc",
    "ChampModele",
    "ChampPropose",
    "Classification",
    "ConfigurationModele",
    "DonneesGeneration",
    "LigneBloc",
    "PropositionModele",
    "RapportValidation",
    "RunBloc",
    "SegmentValeur",
    "StyleBloc",
    "ZoneModele",
    "cle_valide",
]

#: Préfixe des Content Controls posés par l'instrumentation.
TAG_PREFIX = "carso:"

#: Type de contenu attendu d'un champ (le rendu en dépend).
TypeChamp = Literal["texte", "paragraphe", "lignes", "booleen"]

_CLE_INTERDITE = re.compile(r"[^a-z0-9_]+")


def cle_valide(valeur: str) -> str:
    """Normalise une clé de champ en identifiant stable (``Titre du projet`` → ``titre_du_projet``).

    Raises:
        ValueError: clé vide après normalisation.
    """
    sans_accents = unicodedata.normalize("NFKD", valeur or "")
    ascii_ = sans_accents.encode("ascii", "ignore").decode("ascii").casefold()
    cle = _CLE_INTERDITE.sub("_", ascii_).strip("_")
    if not cle:
        raise ValueError("Clé de champ vide : fournir un identifiant exploitable")
    if cle[0].isdigit():
        cle = f"champ_{cle}"
    return cle[:60]


class Classification(StrEnum):
    """Sort de l'élément dans une génération future."""

    STATIC = "STATIC"
    DYNAMIC = "DYNAMIC"
    REPEATABLE = "REPEATABLE"
    CONDITIONAL = "CONDITIONAL"


class StyleBloc(BaseModel):
    """Mise en forme lue sur le paragraphe (style nommé + mise en forme directe)."""

    nom: str | None = None
    alignement: str | None = None
    gras: bool | None = None
    italique: bool | None = None
    taille_pt: float | None = None
    couleur: str | None = None
    police: str | None = None
    numerotation: str | None = None
    retrait_mm: float | None = None
    interligne: float | None = None
    bordures: str | None = None


class RunBloc(BaseModel):
    """Segment de texte homogène d'un paragraphe."""

    texte: str = ""
    gras: bool | None = None
    italique: bool | None = None
    souligne: bool | None = None
    taille_pt: float | None = None
    couleur: str | None = None
    police: str | None = None


class CelluleBloc(BaseModel):
    """Cellule de tableau, avec ses fusions réelles."""

    ligne: int
    colonne: int
    texte: str = ""
    fusion_horizontale: int = 1
    fusion_verticale: Literal["debut", "suite"] | None = None
    largeur_mm: float | None = None


class LigneBloc(BaseModel):
    """Ligne de tableau (``entete`` = répétée en haut de page par Word)."""

    index: int
    entete: bool = False
    cellules: list[CelluleBloc] = Field(default_factory=list)


class Bloc(BaseModel):
    """Élément identifié du document (paragraphe ou tableau)."""

    block_id: str
    partie: str
    type: Literal["paragraphe", "tableau"]
    position: dict[str, int] = Field(default_factory=dict)
    texte: str = ""
    style: StyleBloc = Field(default_factory=StyleBloc)
    niveau_titre: int | None = None
    runs: list[RunBloc] = Field(default_factory=list)
    images: int = 0
    saut_page: bool = False
    lignes: list[LigneBloc] = Field(default_factory=list)
    colonnes: int = 0
    largeurs_mm: list[float] = Field(default_factory=list)


class ZoneModele(BaseModel):
    """Section de page du document (marges, format, en-tête, pied)."""

    partie: str
    largeur_mm: float | None = None
    hauteur_mm: float | None = None
    orientation: str | None = None
    marges_mm: dict[str, float | None] = Field(default_factory=dict)
    entete: str = ""
    pied: str = ""


class AnalyseModele(BaseModel):
    """Résultat complet du ``DocumentAnalyzer``.

    ``empreinte`` identifie la **version** du fichier analysé : elle sert à
    refuser une génération à partir d'une analyse devenue périmée.
    """

    format: str = "docx"
    empreinte: str
    nombre_blocs: int = 0
    blocs: list[Bloc] = Field(default_factory=list)
    zones: list[ZoneModele] = Field(default_factory=list)
    images: dict[str, int] = Field(default_factory=dict)
    placeholders: list[str] = Field(default_factory=list)
    titres: list[dict[str, Any]] = Field(default_factory=list)
    nombre_parties: int = 0
    avertissements: list[str] = Field(default_factory=list)


class ChampPropose(BaseModel):
    """Ce que l'IA propose pour **un** bloc : un rôle, pas encore une décision.

    ``block_id`` doit appartenir à l'analyse ; le service écarte les identifiants
    inventés (le prompt ne suffit pas à garantir cette règle).
    """

    block_id: str = Field(min_length=1, max_length=40)
    classification: Classification
    field_key: str
    label: str = ""
    type: TypeChamp = "texte"
    obligatoire: bool = False
    instruction: str | None = None
    colonnes: list[str] = Field(default_factory=list)
    raison: str | None = None
    confiance: float = Field(default=0.0, ge=0.0, le=1.0)

    @field_validator("field_key")
    @classmethod
    def _normaliser_cle(cls, valeur: str) -> str:
        return cle_valide(valeur)

    @field_validator("colonnes")
    @classmethod
    def _normaliser_colonnes(cls, valeurs: list[str]) -> list[str]:
        return [cle_valide(valeur) for valeur in valeurs]


class PropositionModele(BaseModel):
    """Sortie validée du service de compréhension."""

    nom: str = ""
    type_document: str | None = None
    champs: list[ChampPropose] = Field(default_factory=list)
    avertissements: list[str] = Field(default_factory=list)


class ChampModele(ChampPropose):
    """Champ tel que **validé** par un humain (ou repris d'une proposition)."""

    max_length: int | None = Field(default=None, ge=1, le=20000)
    source: str | None = None


class ConfigurationModele(BaseModel):
    """Configuration exploitable d'une version de modèle.

    ``statut`` vaut ``brouillon`` tant qu'un humain n'a pas validé. Seule une
    configuration ``validee`` instrumente le document et alimente une génération.
    """

    nom: str = ""
    type_document: str | None = None
    statut: Literal["brouillon", "validee"] = "brouillon"
    version: int = 1
    champs: list[ChampModele] = Field(default_factory=list)
    notes: str | None = None

    def champ(self, cle: str) -> ChampModele | None:
        """Champ par clé (``None`` si absent)."""
        return next((champ for champ in self.champs if champ.field_key == cle), None)

    def tags(self) -> list[str]:
        """Tags d'ancrage attendus dans le document instrumenté."""
        return [f"{TAG_PREFIX}{champ.field_key}" for champ in self.champs]

    @field_validator("champs")
    @classmethod
    def _cles_uniques(cls, valeurs: list[ChampModele]) -> list[ChampModele]:
        vues: set[str] = set()
        for champ in valeurs:
            if champ.field_key in vues:
                raise ValueError(f"Clé de champ dupliquée : {champ.field_key}")
            vues.add(champ.field_key)
        return valeurs


class SegmentValeur(BaseModel):
    """Morceau de texte avec sa mise en forme voulue (gras, italique…)."""

    texte: str = ""
    gras: bool | None = None
    italique: bool | None = None
    souligne: bool | None = None
    couleur: str | None = None
    taille_pt: float | None = None


class DonneesGeneration(BaseModel):
    """Données à injecter, par clé de champ de la configuration."""

    valeurs: dict[str, Any] = Field(default_factory=dict)


class RapportValidation(BaseModel):
    """Résultat du ``DocumentValidator`` — ce qui a été **vérifié**, pas supposé."""

    ok: bool = True
    erreurs: list[str] = Field(default_factory=list)
    avertissements: list[str] = Field(default_factory=list)
    parties_modifiees: list[str] = Field(default_factory=list)
    champs_manquants: list[str] = Field(default_factory=list)
    champs_inconnus: list[str] = Field(default_factory=list)
    tags_restants: list[str] = Field(default_factory=list)
    blocs_conserves: list[str] = Field(default_factory=list)
    #: Contrôle visuel : ``non_verifie`` tant qu'aucune conversion n'a eu lieu.
    visuel: str = "non_verifie"
    details: dict[str, Any] = Field(default_factory=dict)
