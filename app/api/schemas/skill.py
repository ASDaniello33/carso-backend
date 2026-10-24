"""Schémas — skills de l'organisation.

Les bornes exposées ici sont **volontairement larges** : la règle métier (nom
normalisé, longueurs exactes, nombre maximal) vit dans ``SkillsService`` et rend
un message actionnable. Ici, on refuse seulement un corps absurde, pour ne pas
laisser passer une requête de plusieurs mégaoctets avant de la rejeter.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class SkillRead(BaseModel):
    """Skill de l'organisation, tel que l'interface le liste."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    nom: str
    description: str
    instructions: str
    modifie_par: str | None = None
    created_at: datetime
    updated_at: datetime


class SkillCreate(BaseModel):
    """Création d'un skill : nom, quand l'appliquer, et le texte du guide."""

    nom: str = Field(min_length=1, max_length=80)
    description: str = Field(min_length=1, max_length=400)
    instructions: str = Field(min_length=1, max_length=4_000)


class SkillUpdate(BaseModel):
    """Correction d'un skill : chaque champ absent est conservé."""

    nom: str | None = Field(default=None, min_length=1, max_length=80)
    description: str | None = Field(default=None, min_length=1, max_length=400)
    instructions: str | None = Field(default=None, min_length=1, max_length=4_000)


__all__ = ["SkillCreate", "SkillRead", "SkillUpdate"]
