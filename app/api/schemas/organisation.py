"""Schémas API — organisations clientes (Lot C1, instruction/02 §A)."""

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class OrganisationCreate(BaseModel):
    """Création d'une organisation cliente."""

    nom: str = Field(min_length=1, max_length=255)
    type: str | None = Field(default=None, max_length=100)
    adresse: str | None = None
    email: str | None = Field(default=None, max_length=255)
    telephone: str | None = Field(default=None, max_length=50)
    statut: str | None = Field(default=None, max_length=50)


class OrganisationUpdate(BaseModel):
    """Modification partielle — seuls les champs fournis sont appliqués."""

    nom: str | None = Field(default=None, min_length=1, max_length=255)
    type: str | None = Field(default=None, max_length=100)
    adresse: str | None = None
    email: str | None = Field(default=None, max_length=255)
    telephone: str | None = Field(default=None, max_length=50)
    statut: str | None = Field(default=None, max_length=50)


class OrganisationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    nom: str
    type: str | None = None
    adresse: str | None = None
    email: str | None = None
    telephone: str | None = None
    statut: str | None = None
