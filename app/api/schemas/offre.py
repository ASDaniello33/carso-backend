"""Schémas API — offres génériques (technique, financière, autre) et budgets.

Une offre répond à ``Appel + Lot`` : l'appel est déduit du lot, mais peut être
fourni explicitement (le service vérifie la cohérence).
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

#: Types d'offre confirmés CARSO (miroir de ``domain.enums.TypeOffre``).
TypeOffreLiteral = Literal["offre_technique", "offre_financiere", "autre"]


class OffreCreate(BaseModel):
    """Création d'une offre en brouillon depuis un lot (règle 2)."""

    lot_id: UUID
    type: TypeOffreLiteral
    reference: str = Field(min_length=1, max_length=100)
    titre: str = Field(min_length=1, max_length=255)
    appel_a_proposition_id: UUID | None = None
    modele_document_id: UUID | None = None


class OffreUpdate(BaseModel):
    """Modification d'identité d'une offre (le lot et l'appel ne bougent pas)."""

    titre: str | None = Field(default=None, min_length=1, max_length=255)
    type: TypeOffreLiteral | None = None
    date_debut_prevue: date | None = None
    date_fin_prevue: date | None = None


class OffreRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    organisation_id: UUID
    appel_a_proposition_id: UUID
    lot_id: UUID
    type: str
    reference: str
    titre: str
    statut: str
    version: int
    date_debut_prevue: date | None = None
    date_fin_prevue: date | None = None
    modele_document_id: UUID | None = None
    approved_at: datetime | None = None


class OffreResume(BaseModel):
    """Vue allégée d'une offre, imbriquée dans une mission."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    type: str
    reference: str
    titre: str
    statut: str


class LigneBudgetCreate(BaseModel):
    """Saisie d'une ligne : PAS de ``cout_total`` — recalculé côté serveur (règle 7)."""

    categorie: str = Field(min_length=1, max_length=100)
    quantite: Decimal = Field(ge=0)
    cout_unitaire: Decimal = Field(ge=0)
    description: str | None = Field(default=None, max_length=2000)
    unite: str | None = Field(default=None, max_length=50)


class LigneBudgetRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    budget_id: UUID
    categorie: str
    quantite: Decimal
    cout_unitaire: Decimal
    cout_total: Decimal
    ordre: int | None = None


class BudgetRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    offre_id: UUID | None = None
    mission_id: UUID | None = None
    devise: str
    statut: str
    version: int


class BudgetTotalRead(BaseModel):
    """Total déterministe recalculé par le service (jamais stocké aveuglément)."""

    budget_id: UUID
    devise: str
    total: Decimal
    nb_lignes: int
