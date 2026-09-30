"""Schémas API — supports de formation (Formateur ↔ Mission ↔ Document).

Le support ne duplique jamais le document : il porte la relation. Le formateur
est identifié par ``equipe_id`` (une personne affectée à la mission avec un rôle
qui vaut Formateur).
"""

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class SupportFormationCreate(BaseModel):
    """Rattache un document existant à un formateur pour une mission."""

    equipe_id: UUID
    document_id: UUID
    libelle: str | None = Field(default=None, max_length=255)


class SupportFormationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    mission_id: UUID
    equipe_id: UUID
    document_id: UUID
    libelle: str | None = None
    statut: str | None = None
