"""Schémas API — lots d'un appel à proposition (Lot C1, instruction/02 §B)."""

from datetime import date
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class LotCreate(BaseModel):
    """Création d'un lot rattaché à un appel à proposition."""

    numero: str = Field(min_length=1, max_length=50)
    titre: str = Field(min_length=1, max_length=255)
    zone: str | None = Field(default=None, max_length=255)
    objectifs: str | None = None
    resultats_attendus: str | None = None
    mission_description: str | None = None
    partenariat: str | None = None
    date_fin: date | None = None
    donnees_source: dict[str, Any] | None = None


class LotUpdate(BaseModel):
    """Modification partielle — seuls les champs fournis sont appliqués."""

    numero: str | None = Field(default=None, min_length=1, max_length=50)
    titre: str | None = Field(default=None, min_length=1, max_length=255)
    zone: str | None = Field(default=None, max_length=255)
    objectifs: str | None = None
    resultats_attendus: str | None = None
    mission_description: str | None = None
    partenariat: str | None = None
    date_fin: date | None = None
    donnees_source: dict[str, Any] | None = None


class LotRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    appel_a_proposition_id: UUID
    numero: str
    titre: str
    zone: str | None = None
    objectifs: str | None = None
    resultats_attendus: str | None = None
    mission_description: str | None = None
    partenariat: str | None = None
    date_fin: date | None = None
    donnees_source: dict[str, Any] | None = None
