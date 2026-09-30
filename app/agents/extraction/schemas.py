"""Sortie structurée de l'analyse d'un appel à proposition (instruction/05 §11).

Le schéma couvre le minimum confirmé (Phase 2 : Organisation, AppelAProposition, Lot).
Tout champ client-spécifique non confirmé va dans ``extra`` — l'agent n'invente
jamais une colonne métier (AGENTS.md §2.2).
"""

from datetime import date
from typing import Any

from pydantic import BaseModel, Field


class OrganisationExtrait(BaseModel):
    """Organisation cliente telle qu'identifiée dans le document."""

    nom: str = Field(min_length=1, max_length=255)
    type: str | None = Field(default=None, max_length=100)


class LotExtrait(BaseModel):
    """Lot tel qu'identifié dans le document (champs de départ instruction/04)."""

    numero: str = Field(min_length=1, max_length=50)
    titre: str = Field(min_length=1, max_length=255)
    zone: str | None = Field(default=None, max_length=255)
    objectifs: str | None = None
    resultats_attendus: str | None = None
    mission_description: str | None = None
    partenariat: str | None = None
    date_fin: date | None = None


class ExtractionAppelAProposition(BaseModel):
    """Proposition d'extraction complète — PROPOSITION, jamais donnée officielle.

    Sérialisée (``model_dump(mode='json')``) dans ``appels_a_proposition.donnees_extraites``
    avec la zone ``_meta`` ajoutée par le service.
    """

    organisation: OrganisationExtrait
    resume: str | None = None
    lots: list[LotExtrait] = Field(default_factory=list)
    extra: dict[str, Any] = Field(default_factory=dict)
