"""Schémas API — lieux d'exécution d'une mission (donnée distincte)."""

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class LieuCreate(BaseModel):
    """Création d'un lieu : le nom est obligatoire, la localisation est libre."""

    nom: str = Field(min_length=1, max_length=255)
    adresse: str | None = Field(default=None, max_length=2000)
    ville: str | None = Field(default=None, max_length=255)
    pays: str | None = Field(default=None, max_length=100)
    zone: str | None = Field(default=None, max_length=255)


class LieuUpdate(BaseModel):
    """Modification partielle — seuls les champs fournis sont appliqués."""

    nom: str | None = Field(default=None, min_length=1, max_length=255)
    adresse: str | None = Field(default=None, max_length=2000)
    ville: str | None = Field(default=None, max_length=255)
    pays: str | None = Field(default=None, max_length=100)
    zone: str | None = Field(default=None, max_length=255)


class LieuRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    nom: str
    adresse: str | None = None
    ville: str | None = None
    pays: str | None = None
    zone: str | None = None
