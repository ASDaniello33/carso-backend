"""Schémas API — bénéficiaires (Lot C1, instruction/02 §G)."""

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class BeneficiaireCreate(BaseModel):
    """Création d'un bénéficiaire (données minimisées [? Q9])."""

    nom: str = Field(min_length=1, max_length=100)
    prenom: str = Field(min_length=1, max_length=100)
    contact: str | None = Field(default=None, max_length=255)
    organisation_origine: str | None = Field(default=None, max_length=255)
    identifiant_externe: str | None = Field(default=None, max_length=100)


class BeneficiaireUpdate(BaseModel):
    """Modification partielle — seuls les champs fournis sont appliqués."""

    nom: str | None = Field(default=None, min_length=1, max_length=100)
    prenom: str | None = Field(default=None, min_length=1, max_length=100)
    contact: str | None = Field(default=None, max_length=255)
    organisation_origine: str | None = Field(default=None, max_length=255)
    identifiant_externe: str | None = Field(default=None, max_length=100)


class BeneficiaireRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    nom: str
    prenom: str
    contact: str | None = None
    organisation_origine: str | None = None
    identifiant_externe: str | None = None
    statut: str = "actif"
