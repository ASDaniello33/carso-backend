"""Base de connaissance documentaire (RAG) — métadonnées, chunks, configuration.

Trois tables, un même principe : **PostgreSQL reste la source de vérité**.

- ``documents_connaissance`` : la bibliothèque (livre, étude, guide, rapport,
  documentation technique). Le fichier physique vit sous un répertoire géré ;
  ``chemin_stockage`` est un chemin **logique** relatif à cette racine, jamais
  un chemin libre (même règle que ``Document``, ADR 0006).
- ``chunks_connaissance`` : le découpage indexé, avec sa **provenance** (page,
  section). Le texte des passages est lisible et vérifiable : une citation
  d'agent doit pouvoir être retrouvée dans le document source.
- ``configurations_embedding`` : le modèle d'embedding appliqué, versionné et
  auditable. La ``signature`` (``provider:modele:dimensions``) est recopiée sur
  chaque chunk : chercher avec une signature différente est **refusé** plutôt
  que de comparer des vecteurs incompatibles (résultats silencieusement faux).

Aucune de ces tables ne porte de secret en clair : la clé du provider
d'embedding est un jeton Fernet (``app.core.chiffrement``), comme la clé du
provider de chat (``ConfigurationRuntimeAgent``).
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
    UniqueConstraint,
    false,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.domain.base import Base, TimestampMixin, UuidPkMixin
from app.domain.enums import StatutIndexationConnaissance

if TYPE_CHECKING:
    from app.domain.identity import Utilisateur


class DocumentConnaissance(Base, UuidPkMixin, TimestampMixin):
    """Un ouvrage de la bibliothèque CARSO et l'état de son indexation.

    Le fichier d'origine est **conservé** : la base indexe une copie découpée,
    le document reste téléchargeable et vérifiable. Aucun écrasement — la
    suppression est explicite et tracée.
    """

    __tablename__ = "documents_connaissance"
    __table_args__ = (
        Index("ix_documents_connaissance_statut", "statut"),
        Index("ix_documents_connaissance_categorie", "categorie"),
        Index("ix_documents_connaissance_langue", "langue"),
        Index("ix_documents_connaissance_titre", "titre"),
        # Un même contenu n'entre pas deux fois : le doublon est signalé
        # (409) avec la fiche existante, il reste visible dans la bibliothèque.
        Index(
            "uq_documents_connaissance_checksum",
            "checksum_sha256",
            unique=True,
        ),
    )

    titre: Mapped[str] = mapped_column(String(500), nullable=False)
    auteur: Mapped[str | None] = mapped_column(String(255))
    editeur: Mapped[str | None] = mapped_column(String(255))
    annee: Mapped[int | None] = mapped_column(Integer)
    #: Vocabulaire ouvert (``livre``, ``etude``, ``guide``, ``rapport``…) : les
    #: catégories définitives de CARSO ne sont pas arrêtées, une valeur libre
    #: ne doit donc pas être refusée. L'interface propose les connues.
    categorie: Mapped[str | None] = mapped_column(String(100))
    langue: Mapped[str] = mapped_column(
        String(10), nullable=False, default="fr", server_default="fr"
    )
    description: Mapped[str | None] = mapped_column(Text)
    mots_cles: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)

    nom_fichier: Mapped[str] = mapped_column(String(255), nullable=False)
    #: ``{id}/{nom}`` sous la racine de la base de connaissance.
    chemin_stockage: Mapped[str] = mapped_column(String(1024), nullable=False)
    mime_type: Mapped[str | None] = mapped_column(String(255))
    taille_octets: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    checksum_sha256: Mapped[str] = mapped_column(String(64), nullable=False)

    nombre_pages: Mapped[int | None] = mapped_column(Integer)
    nombre_caracteres: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    nombre_chunks: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    statut: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default=StatutIndexationConnaissance.EN_ATTENTE.value,
        server_default=StatutIndexationConnaissance.EN_ATTENTE.value,
    )
    #: Dernier échec lisible par un humain (PDF chiffré, librairie absente…).
    #: Jamais un chemin physique ni une clé (AGENTS.md §9).
    erreur: Mapped[str | None] = mapped_column(Text)
    #: Modèle réellement utilisé au dernier indexage réussi.
    signature_embedding: Mapped[str | None] = mapped_column(String(255))
    date_indexation: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    ajoute_par_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("utilisateurs.id", ondelete="SET NULL")
    )
    ajoute_par: Mapped[Utilisateur | None] = relationship("Utilisateur")

    chunks: Mapped[list[ChunkConnaissance]] = relationship(
        "ChunkConnaissance",
        back_populates="document",
        cascade="all, delete-orphan",
        order_by="ChunkConnaissance.position",
    )

    def resume(self) -> dict[str, Any]:
        """Vue sûre pour l'API et l'audit (aucun objet ORM, aucun chemin disque)."""
        return {
            "titre": self.titre,
            "auteur": self.auteur,
            "categorie": self.categorie,
            "langue": self.langue,
            "nom_fichier": self.nom_fichier,
            "statut": self.statut,
            "nombre_chunks": self.nombre_chunks,
            "erreur": self.erreur,
        }


class ChunkConnaissance(Base, UuidPkMixin):
    """Passage indexé d'un document, avec sa provenance.

    ``position`` est l'ordre de lecture ; ``page`` (PDF) et ``section`` (titre
    DOCX, feuille XLSX) permettent à l'agent de citer une source vérifiable.
    """

    __tablename__ = "chunks_connaissance"
    __table_args__ = (
        UniqueConstraint(
            "document_id",
            "position",
            name="uq_chunks_connaissance_document_position",
        ),
        Index("ix_chunks_connaissance_document_id", "document_id"),
    )

    document_id: Mapped[UUID] = mapped_column(
        ForeignKey("documents_connaissance.id", ondelete="CASCADE"), nullable=False
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    page: Mapped[int | None] = mapped_column(Integer)
    section: Mapped[str | None] = mapped_column(String(255))
    contenu: Mapped[str] = mapped_column(Text, nullable=False)
    nombre_caracteres: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: Signature du modèle d'embedding du vecteur associé. Sert au contrôle
    #: anti-mélange (``ConnaissanceService.rechercher``).
    signature_embedding: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    document: Mapped[DocumentConnaissance] = relationship(
        "DocumentConnaissance", back_populates="chunks"
    )


class ConfigurationEmbedding(Base, UuidPkMixin, TimestampMixin):
    """Modèle d'embedding appliqué à la base de connaissance (versionné).

    Une seule ligne est ``actif=True``. La configuration administrée est
    prioritaire sur ``EMBEDDING_*`` du ``.env`` ; la désactiver revient au
    déploiement. Chaque enregistrement crée une **nouvelle** ligne — l'audit
    reste lisible, comme pour la configuration de chat.
    """

    __tablename__ = "configurations_embedding"
    __table_args__ = (
        Index("ix_configurations_embedding_actif", "actif"),
        Index("ix_configurations_embedding_created_at", "created_at"),
    )

    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    model: Mapped[str] = mapped_column(String(150), nullable=False)
    base_url: Mapped[str | None] = mapped_column(String(500))
    dimensions: Mapped[int] = mapped_column(Integer, nullable=False)
    #: ``provider:modele:dimensions`` — la clé du contrôle anti-mélange.
    signature: Mapped[str] = mapped_column(String(255), nullable=False)
    api_key_chiffree: Mapped[str | None] = mapped_column(String(1000))
    actif: Mapped[bool] = mapped_column(
        nullable=False, default=False, server_default=false()
    )
    modifie_par: Mapped[str | None] = mapped_column(String(64))

    def resume(self) -> dict[str, Any]:
        """Vue sans secret (l'interface affiche seulement *qu'une* clé existe)."""
        return {
            "provider": self.provider,
            "model": self.model,
            "base_url": self.base_url,
            "dimensions": int(self.dimensions),
            "signature": self.signature,
            "cle_administree": bool(self.api_key_chiffree),
            "actif": bool(self.actif),
        }


__all__ = [
    "ChunkConnaissance",
    "ConfigurationEmbedding",
    "DocumentConnaissance",
]
