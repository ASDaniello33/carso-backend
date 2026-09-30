"""Modèles de la chaîne commerciale : Organisation → AppelAProposition → Lot → Offre.

Entities [C] from Phase 2 §1 / instruction/04 §2. Pending items ([P]) are
deliberately absent: no legal-id column, no NOT NULL beyond what the workflow
requires.

Règle métier confirmée avec CARSO : ``Offre`` est **générique** (elle n'est pas
forcément une offre de formation). Une offre répond toujours à ``Appel + Lot``
et porte un type explicite (technique, financière, autre). Un même lot peut
porter une offre technique **et** une offre financière : l'unicité porte donc
sur ``(lot_id, type)``.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import (
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.domain.base import Base, TimestampMixin, UuidPkMixin
from app.domain.enums import (
    StatutAppelAProposition,
    StatutBudget,
    StatutOffre,
    TypeAppel,
)

if TYPE_CHECKING:
    from app.domain.document import Document
    from app.domain.execution import Mission, ModeleDocument


class Organisation(Base, UuidPkMixin, TimestampMixin):
    """Entreprise/organisation cliente à l'origine d'un appel à proposition."""

    __tablename__ = "organisations"

    nom: Mapped[str] = mapped_column(String(255), nullable=False)
    type: Mapped[str | None] = mapped_column(String(100))
    adresse: Mapped[str | None] = mapped_column(Text)
    email: Mapped[str | None] = mapped_column(String(255))
    telephone: Mapped[str | None] = mapped_column(String(50))
    statut: Mapped[str | None] = mapped_column(String(50))

    appels_a_proposition: Mapped[list[AppelAProposition]] = relationship(
        back_populates="organisation"
    )
    modeles_documents: Mapped[list[ModeleDocument]] = relationship(back_populates="organisation")


class AppelAProposition(Base, UuidPkMixin, TimestampMixin):
    """Appel à proposition reçu par CARSO.

    ``donnees_extraites`` vit dans la zone *proposal* (Phase 2 §6) : la
    proposition IA n'est jamais la donnée officielle avant Approbation.
    """

    __tablename__ = "appels_a_proposition"
    __table_args__ = (
        UniqueConstraint("reference", name="uq_appels_a_proposition_reference"),
        Index("ix_appels_a_proposition_statut", "statut"),
        Index("ix_appels_a_proposition_organisation_id", "organisation_id"),
    )

    organisation_id: Mapped[UUID] = mapped_column(
        ForeignKey("organisations.id", ondelete="RESTRICT"), nullable=False
    )
    #: Nature de l'appel — ``appel_a_proposition``, ``appel_a_manifestation_interet``
    #: ou ``autre`` (types validés par ``domain.enums.TypeAppel``).
    #: Jamais déduite : c'est une donnée explicite (règle confirmée CARSO).
    type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default=TypeAppel.APPEL_A_PROPOSITION.value,
        server_default=TypeAppel.APPEL_A_PROPOSITION.value,
    )
    reference: Mapped[str] = mapped_column(String(100), nullable=False)
    titre: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    date_reception: Mapped[date | None] = mapped_column(Date)
    date_limite: Mapped[date | None] = mapped_column(Date)
    statut: Mapped[str] = mapped_column(
        String(50), nullable=False, default=StatutAppelAProposition.RECU.value,
        server_default=StatutAppelAProposition.RECU.value,
    )
    # Zone "proposal" — sortie d'extraction IA, jamais écrasée par une autre sortie.
    donnees_extraites: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    organisation: Mapped[Organisation] = relationship(back_populates="appels_a_proposition")
    lots: Mapped[list[Lot]] = relationship(
        back_populates="appel_a_proposition", cascade="all, delete-orphan"
    )
    documents: Mapped[list[Document]] = relationship(back_populates="appel_a_proposition")


class Lot(Base, UuidPkMixin, TimestampMixin):
    """Subdivision définie par l'appel — **pas forcément un lieu** (règle CARSO).

    Un lot peut représenter un lieu, un domaine, une zone, une activité ou une
    autre subdivision : sa nature n'est pas figée. Le **lieu d'exécution** d'une
    mission est une donnée distincte (``Mission.lieu_id``) et n'est jamais porté
    par le lot.
    """

    __tablename__ = "lots"
    __table_args__ = (
        UniqueConstraint(
            "appel_a_proposition_id",
            "numero",
            name="uq_lots_appel_a_proposition_id_numero",
        ),
        Index("ix_lots_appel_a_proposition_id", "appel_a_proposition_id"),
    )

    appel_a_proposition_id: Mapped[UUID] = mapped_column(
        ForeignKey("appels_a_proposition.id", ondelete="CASCADE"), nullable=False
    )
    numero: Mapped[str] = mapped_column(String(50), nullable=False)
    titre: Mapped[str] = mapped_column(String(255), nullable=False)
    zone: Mapped[str | None] = mapped_column(String(255))
    objectifs: Mapped[str | None] = mapped_column(Text)
    resultats_attendus: Mapped[str | None] = mapped_column(Text)
    mission_description: Mapped[str | None] = mapped_column(Text)
    partenariat: Mapped[str | None] = mapped_column(Text)
    date_fin: Mapped[date | None] = mapped_column(Date)
    donnees_source: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    appel_a_proposition: Mapped[AppelAProposition] = relationship(back_populates="lots")
    offres: Mapped[list[Offre]] = relationship(back_populates="lot")


class Offre(Base, UuidPkMixin, TimestampMixin):
    """Offre CARSO répondant à **un appel et un lot** (règle confirmée).

    L'offre est générique : elle n'est pas forcément une offre de formation.
    ``type`` est explicite — ``OFFRE_TECHNIQUE`` (méthodologie, activités,
    planning, équipe, moyens, livrables), ``OFFRE_FINANCIERE`` (budget, coûts,
    lignes budgétaires) ou ``AUTRE`` (appel qui n'est pas un appel à proposition,
    notamment une manifestation d'intérêt).

    Un appel à proposition se répond par une offre technique **et** une offre
    financière : l'unicité porte donc sur ``(lot_id, type)``. Une mission peut
    référencer plusieurs offres du même lot (relation N-N ``mission_offres``).
    """

    __tablename__ = "offres"
    __table_args__ = (
        UniqueConstraint("reference", name="uq_offres_reference"),
        UniqueConstraint("lot_id", "type", name="uq_offres_lot_id_type"),
        Index("ix_offres_statut", "statut"),
        Index("ix_offres_type", "type"),
        Index("ix_offres_lot_id", "lot_id"),
        Index("ix_offres_organisation_id", "organisation_id"),
        Index("ix_offres_appel_a_proposition_id", "appel_a_proposition_id"),
    )

    organisation_id: Mapped[UUID] = mapped_column(
        ForeignKey("organisations.id", ondelete="RESTRICT"), nullable=False
    )
    #: Lien explicite vers l'appel (règle : une offre répond à ``Appel + Lot``).
    #: Le service vérifie la cohérence : l'appel doit être celui du lot.
    appel_a_proposition_id: Mapped[UUID] = mapped_column(
        ForeignKey("appels_a_proposition.id", ondelete="RESTRICT"), nullable=False
    )
    lot_id: Mapped[UUID] = mapped_column(
        ForeignKey("lots.id", ondelete="RESTRICT"), nullable=False
    )
    type: Mapped[str] = mapped_column(String(50), nullable=False)
    reference: Mapped[str] = mapped_column(String(100), nullable=False)
    titre: Mapped[str] = mapped_column(String(255), nullable=False)
    date_debut_prevue: Mapped[date | None] = mapped_column(Date)
    date_fin_prevue: Mapped[date | None] = mapped_column(Date)
    statut: Mapped[str] = mapped_column(
        String(50), nullable=False, default=StatutOffre.BROUILLON.value,
        server_default=StatutOffre.BROUILLON.value,
    )
    modele_document_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("documents.id", ondelete="SET NULL")
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    organisation: Mapped[Organisation] = relationship()
    appel_a_proposition: Mapped[AppelAProposition] = relationship()
    lot: Mapped[Lot] = relationship(back_populates="offres")
    # Deux FK relient déjà Offre et Document : condition de jointure explicite.
    modele_document: Mapped[Document | None] = relationship(
        "Document", foreign_keys=[modele_document_id]
    )
    budgets: Mapped[list[Budget]] = relationship(back_populates="offre")
    #: Missions alimentées par cette offre (N-N : technique + financière).
    missions: Mapped[list[Mission]] = relationship(
        "Mission", secondary="mission_offres", back_populates="offres"
    )


class Budget(Base, UuidPkMixin, TimestampMixin):
    """Budget rattaché à une offre et/ou une mission (rattachement exact : [P])."""

    __tablename__ = "budgets"
    __table_args__ = (
        Index("ix_budgets_offre_id", "offre_id"),
        Index("ix_budgets_mission_id", "mission_id"),
    )

    offre_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("offres.id", ondelete="CASCADE")
    )
    mission_id: Mapped[UUID | None] = mapped_column(ForeignKey("missions.id", ondelete="CASCADE"))
    devise: Mapped[str] = mapped_column(
        String(10), nullable=False, default="MAD", server_default="MAD"
    )
    statut: Mapped[str] = mapped_column(
        String(50), nullable=False, default=StatutBudget.BROUILLON.value,
        server_default=StatutBudget.BROUILLON.value,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")

    offre: Mapped[Offre | None] = relationship(back_populates="budgets")
    mission: Mapped[Mission | None] = relationship(back_populates="budgets")
    lignes: Mapped[list[LigneBudget]] = relationship(
        back_populates="budget", cascade="all, delete-orphan"
    )


class LigneBudget(Base, UuidPkMixin, TimestampMixin):
    """Ligne de budget. ``cout_total`` est calculé côté serveur (règle déterministe)."""

    __tablename__ = "lignes_budget"
    __table_args__ = (Index("ix_lignes_budget_budget_id", "budget_id"),)

    budget_id: Mapped[UUID] = mapped_column(
        ForeignKey("budgets.id", ondelete="CASCADE"), nullable=False
    )
    categorie: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    unite: Mapped[str | None] = mapped_column(String(50))
    quantite: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False)
    cout_unitaire: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False)
    cout_total: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False)
    ordre: Mapped[int | None] = mapped_column(Integer)

    budget: Mapped[Budget] = relationship(back_populates="lignes")
