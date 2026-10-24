"""Schémas API — lieux d'exécution d'une mission (donnée distincte)."""

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class LieuCreate(BaseModel):
    """Création d'un lieu : le nom est obligatoire, la localisation est libre.

    Les coordonnées vont **ensemble** : une seule fournie est refusée par le
    service (une position partielle dessinerait un point au milieu de l'océan).
    """

    nom: str = Field(min_length=1, max_length=255)
    adresse: str | None = Field(default=None, max_length=2000)
    ville: str | None = Field(default=None, max_length=255)
    pays: str | None = Field(default=None, max_length=100)
    zone: str | None = Field(default=None, max_length=255)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)


class LieuUpdate(BaseModel):
    """Modification partielle — seuls les champs fournis sont appliqués."""

    nom: str | None = Field(default=None, min_length=1, max_length=255)
    adresse: str | None = Field(default=None, max_length=2000)
    ville: str | None = Field(default=None, max_length=255)
    pays: str | None = Field(default=None, max_length=100)
    zone: str | None = Field(default=None, max_length=255)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)


class LieuRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    nom: str
    adresse: str | None = None
    ville: str | None = None
    pays: str | None = None
    zone: str | None = None
    latitude: float | None = None
    longitude: float | None = None


class PositionLieuUpdate(BaseModel):
    """Ajustement de la position d'un marqueur (les deux coordonnées, ensemble)."""

    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
