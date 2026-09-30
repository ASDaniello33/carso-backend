"""Schémas API — appels à proposition : enregistrement, extraction, décision."""

from datetime import date
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class AppelAPropositionCreate(BaseModel):
    """Enregistrement humain d'un appel reçu (instruction/02 §B).

    ``type`` distingue l'appel à proposition, l'appel à manifestation d'intérêt
    et tout autre type d'appel reçu ; ``None`` retombe sur l'appel à
    proposition (aucune déduction).
    """

    organisation_id: UUID
    type: Literal["appel_a_proposition", "appel_a_manifestation_interet", "autre"] | None = None
    reference: str = Field(min_length=1, max_length=100)
    titre: str = Field(min_length=1, max_length=255)
    description: str | None = None
    date_reception: date | None = None
    date_limite: date | None = None


class AppelAPropositionUpdate(BaseModel):
    """Modification d'un appel reçu (jamais une donnée extraite par IA).

    Référence et organisation émettrice ne sont modifiables que tant que l'appel
    n'est pas validé : les lots officiels en héritent (contrôle dans le service).
    """

    organisation_id: UUID | None = None
    type: Literal["appel_a_proposition", "appel_a_manifestation_interet", "autre"] | None = None
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


class AppelAPropositionRead(BaseModel):
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
