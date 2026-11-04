"""Schémas API — appels : enregistrement, extraction, décision."""

from datetime import date
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class AppelCreate(BaseModel):
    """Enregistrement humain d'un appel reçu (instruction/02 §B).

    ``type`` est une donnée explicite, jamais déduite. Le vocabulaire est
    **ouvert** (décision du 04/11) : les types canoniques et tout type saisi
    par l'équipe (voir ``GET /appels/types`` pour la liste de ceux déjà
    enregistrés). ``None`` retombe sur l'appel à proposition.
    """

    organisation_id: UUID
    type: str | None = Field(default=None, max_length=50)
    reference: str = Field(min_length=1, max_length=100)
    titre: str = Field(min_length=1, max_length=255)
    description: str | None = None
    date_reception: date | None = None
    date_limite: date | None = None


class AppelUpdate(BaseModel):
    """Modification d'un appel reçu (jamais une donnée extraite par IA).

    Référence et organisation émettrice ne sont modifiables que tant que l'appel
    n'est pas validé : les lots officiels en héritent (contrôle dans le service).
    """

    organisation_id: UUID | None = None
    type: str | None = Field(default=None, max_length=50)
    reference: str | None = Field(default=None, min_length=1, max_length=100)
    titre: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    date_reception: date | None = None
    date_limite: date | None = None


class ExtractionProposeeCreate(BaseModel):
    """Proposition d'extraction d'un agent (zone *proposal*, jamais officielle)."""

    donnees_extraites: dict[str, Any]
    proposed_by_agent: str = Field(min_length=1, max_length=100)


class DecisionCreate(BaseModel):
    """Décision humaine. ``decided_by`` vient du JWT (AUTH), plus du body."""

    decision: Literal["approuve", "rejete"]
    reason: str | None = Field(default=None, max_length=2000)


class AppelRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    organisation_id: UUID
    type: str
    reference: str
    titre: str
    description: str | None = None
    statut: str
    date_reception: date | None = None
    date_limite: date | None = None
    donnees_extraites: dict[str, Any] | None = None
