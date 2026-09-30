"""Schémas API — personnes du vivier (Lot C1, instruction/02 §D)."""

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class EquipeCreate(BaseModel):
    """Création d'une personne du vivier (le CV passe par /documents)."""

    nom: str = Field(min_length=1, max_length=100)
    prenom: str = Field(min_length=1, max_length=100)
    email: str | None = Field(default=None, max_length=255)
    telephone: str | None = Field(default=None, max_length=50)
    profil: str | None = None
    statut: str | None = Field(default=None, max_length=50)
    role_compte: str | None = Field(default=None, max_length=50)


class EquipeUpdate(BaseModel):
    """Modification partielle — seuls les champs fournis sont appliqués."""

    nom: str | None = Field(default=None, min_length=1, max_length=100)
    prenom: str | None = Field(default=None, min_length=1, max_length=100)
    email: str | None = Field(default=None, max_length=255)
    telephone: str | None = Field(default=None, max_length=50)
    profil: str | None = None
    statut: str | None = Field(default=None, max_length=50)
    role_compte: str | None = Field(default=None, max_length=50)


class EquipeRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    nom: str
    prenom: str
    email: str | None = None
    telephone: str | None = None
    profil: str | None = None
    statut: str | None = None
    role_compte: str | None = None
