"""Schémas API des budgets — la saisie ne porte jamais de montant calculé.

``cout_total`` et le total d'un budget sont **recalculés par le service**
(règle 7) : aucun schéma d'entrée ne les accepte, un client ne peut pas imposer
un montant officiel.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class BudgetCreate(BaseModel):
    """Création du budget d'une offre **ou** d'une mission (un seul porteur)."""

    model_config = ConfigDict(extra="forbid")

    offre_id: UUID | None = None
    mission_id: UUID | None = None
    devise: str | None = Field(default=None, max_length=10)


class LigneBudgetUpdate(BaseModel):
    """Correction d'une ligne : mêmes règles que la création, sans ``cout_total``."""

    categorie: str = Field(min_length=1, max_length=100)
    quantite: Decimal = Field(ge=0)
    cout_unitaire: Decimal = Field(ge=0)
    description: str | None = Field(default=None, max_length=2000)
    unite: str | None = Field(default=None, max_length=50)


class LigneBudgetDetailRead(BaseModel):
    """Ligne telle qu'affichée : le coût total vient du serveur."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    budget_id: UUID
    categorie: str
    description: str | None = None
    unite: str | None = None
    quantite: Decimal
    cout_unitaire: Decimal
    cout_total: Decimal
    ordre: int | None = None


class BudgetResumeRead(BaseModel):
    """Budget listé avec son total recalculé (jamais un total stocké)."""

    budget_id: UUID
    offre_id: UUID | None = None
    mission_id: UUID | None = None
    devise: str
    statut: str
    version: int
    total: Decimal
    nb_lignes: int


class BudgetDetailRead(BaseModel):
    """Budget complet : entête, lignes ordonnées et total déterministe."""

    budget_id: UUID
    offre_id: UUID | None = None
    mission_id: UUID | None = None
    devise: str
    statut: str
    version: int
    lignes: list[LigneBudgetDetailRead]
    total: Decimal


class BudgetRepriseCreate(BaseModel):
    """Reprise d'un budget d'offre approuvé sur une mission."""

    model_config = ConfigDict(extra="forbid")

    mission_id: UUID


__all__ = [
    "BudgetCreate",
    "BudgetDetailRead",
    "BudgetRepriseCreate",
    "BudgetResumeRead",
    "LigneBudgetDetailRead",
    "LigneBudgetUpdate",
]
