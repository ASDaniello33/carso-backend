"""Document, Approbation, AuditEvent, AgentTask — entités [C].

Document = référentiel central (instruction/04 §2, instruction/06) : un chemin
disque seul ne constitue jamais une donnée documentaire (AGENTS.md §7).
AuditEvent/AgentTask ne contiennent aucun secret (instruction/08).
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    false,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.domain.base import Base, TimestampMixin, UuidPkMixin
from app.domain.enums import StatutAgentTask, StatutDocument

if TYPE_CHECKING:
    from app.domain.execution import Equipe, Mission, SessionFormation
    from app.domain.organization import AppelAProposition, Offre, Organisation


class Document(Base, UuidPkMixin, TimestampMixin):
    """Référentiel documentaire central [C].

    Anti path-traversal : ``storage_path`` est un chemin **logique contrôlé**,
    validé par la couche storage (instruction/06) — jamais un chemin libre.
    Un document approuvé n'est jamais écrasé : une nouvelle version = une
    nouvelle ligne (version + 1) [C].
    """

    __tablename__ = "documents"
    __table_args__ = (
        Index("ix_documents_type_document", "type_document"),
        Index("ix_documents_statut", "statut"),
        Index("ix_documents_organisation_id", "organisation_id"),
        Index("ix_documents_appel_a_proposition_id", "appel_a_proposition_id"),
        Index("ix_documents_offre_id", "offre_id"),
        Index("ix_documents_mission_id", "mission_id"),
        Index("ix_documents_equipe_id", "equipe_id"),
        Index("ix_documents_session_id", "session_id"),
        Index("ix_documents_checksum", "checksum_sha256"),
    )

    nom: Mapped[str] = mapped_column(String(255), nullable=False)
    type_document: Mapped[str] = mapped_column(String(50), nullable=False)
    mime_type: Mapped[str | None] = mapped_column(String(255))
    taille_octets: Mapped[int | None] = mapped_column(BigInteger)
    # Chemin logique sous storage_root (ex: offres/{id}/offre.pdf).
    storage_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    storage_disk: Mapped[str | None] = mapped_column(String(50))
    checksum_sha256: Mapped[str | None] = mapped_column(String(64))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    statut: Mapped[str] = mapped_column(
        String(50), nullable=False, default=StatutDocument.DRAFT.value,
        server_default=StatutDocument.DRAFT.value,
    )

    organisation_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("organisations.id", ondelete="SET NULL")
    )
    appel_a_proposition_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("appels_a_proposition.id", ondelete="CASCADE")
    )
    offre_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("offres.id", ondelete="CASCADE")
    )
    mission_id: Mapped[UUID | None] = mapped_column(ForeignKey("missions.id", ondelete="CASCADE"))
    equipe_id: Mapped[UUID | None] = mapped_column(ForeignKey("equipes.id", ondelete="CASCADE"))
    # Ancre session (décision utilisateur 20/09) : fiche de présence, checklist
    # et rapport appartiennent à la session, pas seulement à la mission. Le
    # dossier de stockage devient ``sessions/{id}/`` (cf. ADR 0003) : un
    # document doit retrouver son chemin, une ancre = un scope.
    session_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("sessions.id", ondelete="CASCADE")
    )

    doc_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_by: Mapped[str | None] = mapped_column(String(255))

    # Côtés "many" des relations métier (les listes correspondantes sont
    # définies sur Organisation/AppelAProposition/Mission/Equipe).
    organisation: Mapped[Organisation | None] = relationship("Organisation")
    appel_a_proposition: Mapped[AppelAProposition | None] = relationship(
        "AppelAProposition", back_populates="documents"
    )
    # Deux FK relient Document et Offre : condition de jointure explicite.
    offre: Mapped[Offre | None] = relationship("Offre", foreign_keys=[offre_id])
    mission: Mapped[Mission | None] = relationship("Mission", back_populates="documents")
    equipe: Mapped[Equipe | None] = relationship("Equipe", back_populates="documents")
    session: Mapped[SessionFormation | None] = relationship(
        "SessionFormation", back_populates="documents"
    )


class Approbation(Base, UuidPkMixin, TimestampMixin):
    """Trace d'une décision humaine sur une proposition [C].

    ``entity_type``/``entity_id`` : référence souple volontaire (toutes cibles
    possibles) ; pour les objets critiques (offre, affectation), l'association
    explicite existe aussi via les colonnes dédiées du flux (ex: approved_at).
    """

    __tablename__ = "approbations"
    __table_args__ = (
        Index("ix_approbations_entity", "entity_type", "entity_id"),
        Index("ix_approbations_decision", "decision"),
    )

    entity_type: Mapped[str] = mapped_column(String(100), nullable=False)
    entity_id: Mapped[UUID] = mapped_column(nullable=False)
    proposal_type: Mapped[str] = mapped_column(String(100), nullable=False)
    proposed_by_agent: Mapped[str | None] = mapped_column(String(100))
    decision: Mapped[str] = mapped_column(String(50), nullable=False)
    decided_by: Mapped[str | None] = mapped_column(String(255))
    decision_reason: Mapped[str | None] = mapped_column(Text)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuditEvent(Base, UuidPkMixin):
    """Trace des opérations sensibles [C]. Insert-only : jamais de mise à jour."""

    __tablename__ = "audit_events"
    __table_args__ = (
        Index("ix_audit_events_entity", "entity_type", "entity_id"),
        Index("ix_audit_events_action", "action"),
        Index("ix_audit_events_created_at", "created_at"),
    )

    actor_type: Mapped[str] = mapped_column(String(50), nullable=False)
    actor_id: Mapped[str | None] = mapped_column(String(255))
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(100), nullable=False)
    entity_id: Mapped[str | None] = mapped_column(String(255))
    before: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    after: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    event_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AgentTask(Base, UuidPkMixin, TimestampMixin):
    """Trace technique des tâches inter-agents [C]. Pas de secrets dans payload/result."""

    __tablename__ = "agent_tasks"
    __table_args__ = (
        Index("ix_agent_tasks_correlation_id", "correlation_id"),
        Index("ix_agent_tasks_status", "status"),
        Index("ix_agent_tasks_from_to", "from_agent", "to_agent"),
    )

    correlation_id: Mapped[str] = mapped_column(String(100), nullable=False)
    from_agent: Mapped[str] = mapped_column(String(100), nullable=False)
    to_agent: Mapped[str | None] = mapped_column(String(100))
    task_type: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(
        String(50), nullable=False, default=StatutAgentTask.PENDING.value,
        server_default=StatutAgentTask.PENDING.value,
    )
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    requires_approval: Mapped[bool] = mapped_column(
        nullable=False, default=False, server_default=false()
    )
