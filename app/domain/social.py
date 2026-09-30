"""Modèles — social (chat, conférences, annonces) — incrément 20.

Vocabulaire validé avec CARSO :

- **Conversation** : directe (2 membres, unique par paire) ou conférence
  (groupe nommé, temporaire — clôturable par son animateur) ;
- **Message** : texte court d'un membre, pièce jointe optionnelle stockée sous
  ``chat/{conversation_id}/`` (scope ``chat``) ;
- **Annonce** : publication à toute l'équipe (texte + image/document
  optionnels), avec commentaires et réactions ;
- **Réaction** : emoji polymorphe sur message / annonce / commentaire.

Une conversation fermée reste lisible (archive logique) : on ne supprime pas
l'historique des échanges. Aucun agent n'écrit ici directement : la réponse
d'un agent invoqué (@mention) est postée par le service, comme un message
d'auteur ``agent`` (incrément 21).
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.domain.base import Base, TimestampMixin, UuidPkMixin


class Conversation(Base, UuidPkMixin, TimestampMixin):
    """Fil de discussion : directe (2) ou conférence (groupe temporaire)."""

    __tablename__ = "conversations"
    __table_args__ = (
        Index("ix_conversations_type", "type"),
        Index("ix_conversations_ferme", "fermee_at"),
    )

    type: Mapped[str] = mapped_column(String(20), nullable=False)
    #: Titre des conférences (null pour une conversation directe).
    titre: Mapped[str | None] = mapped_column(String(255))
    #: Clôture logique d'une conférence : lisible, plus d'écriture.
    fermee_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: Suppression de la discussion (conférence, par l'animateur) : soft-delete.
    #: Les messages restent (archive logique) ; la conversation sort des listes.
    archive_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    creee_par_id: Mapped[UUID] = mapped_column(
        ForeignKey("utilisateurs.id", ondelete="RESTRICT"), nullable=False
    )

    membres: Mapped[list[MembreConversation]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan"
    )
    messages: Mapped[list[Message]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan"
    )


class MembreConversation(Base, UuidPkMixin, TimestampMixin):
    """Participation d'un compte à une conversation (rôle + fenêtre de présence)."""

    __tablename__ = "membres_conversation"
    __table_args__ = (
        UniqueConstraint(
            "conversation_id", "utilisateur_id", name="uq_membres_conversation_paire"
        ),
        Index("ix_membres_conversation_utilisateur", "utilisateur_id"),
    )

    conversation_id: Mapped[UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    utilisateur_id: Mapped[UUID] = mapped_column(
        ForeignKey("utilisateurs.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    #: L'utilisateur a quitté la conférence (l'historique reste consultable).
    parti_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: Dernière lecture de l'utilisateur (badge « messages non lus ») : null =
    #: tout ce qui précède est non lu ; un message de l'autre après cette date compte.
    derniere_lecture_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: Discussion directe supprimée **par cet utilisateur** (sort de sa liste ;
    #: l'autre membre la conserve). Un nouveau message la fait réapparaître.
    archive_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    conversation: Mapped[Conversation] = relationship(back_populates="membres")


class Message(Base, UuidPkMixin, TimestampMixin):
    """Un message du chat : texte, auteur (humain ou agent), pièce jointe optionnelle."""

    __tablename__ = "messages"
    __table_args__ = (
        Index("ix_messages_conversation_date", "conversation_id", "created_at"),
        Index("ix_messages_auteur", "auteur_id"),
        # Suppression auteur (décision validée) : soft-delete.
        Index("ix_messages_supprime", "supprime_at"),
    )

    conversation_id: Mapped[UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    #: Auteur humain ; null pour un message d'agent (voir ``auteur_type``).
    auteur_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("utilisateurs.id", ondelete="SET NULL")
    )
    #: humain | agent (TypeAuteurMessage) — la réponse @agent est postée par le service.
    auteur_type: Mapped[str] = mapped_column(String(10), nullable=False)
    #: Nom affiché pour un agent (ex. « Agent Généraliste ») ; null pour un humain.
    auteur_agent_id: Mapped[str | None] = mapped_column(String(60))
    contenu: Mapped[str] = mapped_column(Text, nullable=False)

    # Pièce jointe (photo ou document) stockée sous chat/{conversation_id}/.
    piece_nom: Mapped[str | None] = mapped_column(String(255))
    piece_chemin: Mapped[str | None] = mapped_column(String(1024))
    piece_mime: Mapped[str | None] = mapped_column(String(255))
    piece_taille: Mapped[int | None] = mapped_column(Integer)

    supprime_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: Réponse à un message spécifique (incrément 24) : null = message racine.
    reponse_a_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("messages.id", ondelete="SET NULL")
    )

    conversation: Mapped[Conversation] = relationship(back_populates="messages")


class PieceAnnonce(Base, UuidPkMixin, TimestampMixin):
    """Une pièce jointe d'annonce (incrément 33 : **plusieurs** par annonce)."""

    __tablename__ = "annonces_pieces"
    __table_args__ = (
        Index("ix_annonces_pieces_annonce", "annonce_id", "position"),
    )

    annonce_id: Mapped[UUID] = mapped_column(
        ForeignKey("annonces.id", ondelete="CASCADE"), nullable=False
    )
    nom: Mapped[str] = mapped_column(String(255), nullable=False)
    #: Chemin logique sous le scope ``chat`` (même stockage que le chat).
    chemin: Mapped[str] = mapped_column(String(1024), nullable=False)
    mime: Mapped[str | None] = mapped_column(String(255))
    taille: Mapped[int | None] = mapped_column(Integer)
    #: Ordre d'affichage (0 = première) — l'ordre du formulaire est conservé.
    position: Mapped[int] = mapped_column(SmallInteger, default=0, nullable=False)

    annonce: Mapped[Annonce] = relationship(back_populates="pieces")


class Annonce(Base, UuidPkMixin, TimestampMixin):
    """Publication à toute l'équipe : texte + plusieurs images/documents."""

    __tablename__ = "annonces"
    __table_args__ = (Index("ix_annonces_date", "created_at"),)

    auteur_id: Mapped[UUID] = mapped_column(
        ForeignKey("utilisateurs.id", ondelete="RESTRICT"), nullable=False
    )
    titre: Mapped[str] = mapped_column(String(255), nullable=False)
    contenu: Mapped[str | None] = mapped_column(Text)

    # Pièce **unique historique** (incrément 20) : conservée pour la migration
    # — toute nouvelle pièce passe par ``pieces`` (incrément 33).
    piece_nom: Mapped[str | None] = mapped_column(String(255))
    piece_chemin: Mapped[str | None] = mapped_column(String(1024))
    piece_mime: Mapped[str | None] = mapped_column(String(255))
    piece_taille: Mapped[int | None] = mapped_column(Integer)

    supprime_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    pieces: Mapped[list[PieceAnnonce]] = relationship(
        back_populates="annonce",
        cascade="all, delete-orphan",
        order_by="PieceAnnonce.position",
        lazy="selectin",
    )


class Commentaire(Base, UuidPkMixin, TimestampMixin):
    """Commentaire d'une annonce (suppression par l'auteur, soft-delete)."""

    __tablename__ = "commentaires"
    __table_args__ = (
        Index("ix_commentaires_annonce_date", "annonce_id", "created_at"),
    )

    annonce_id: Mapped[UUID] = mapped_column(
        ForeignKey("annonces.id", ondelete="CASCADE"), nullable=False
    )
    auteur_id: Mapped[UUID] = mapped_column(
        ForeignKey("utilisateurs.id", ondelete="RESTRICT"), nullable=False
    )
    contenu: Mapped[str] = mapped_column(Text, nullable=False)
    supprime_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Reaction(Base, UuidPkMixin, TimestampMixin):
    """Emoji polymorphe : **une seule** réaction par (utilisateur, cible) —
    changer d'emoji remplace la précédente, recliquer retire (incrément 24 :
    l'unicité par emoji autorisait plusieurs réactions du même utilisateur,
    bug logique signalé)."""

    __tablename__ = "reactions"
    __table_args__ = (
        UniqueConstraint(
            "utilisateur_id",
            "cible_type",
            "cible_id",
            name="uq_reactions_utilisateur_cible",
        ),
        Index("ix_reactions_cible", "cible_type", "cible_id"),
    )

    utilisateur_id: Mapped[UUID] = mapped_column(
        ForeignKey("utilisateurs.id", ondelete="CASCADE"), nullable=False
    )
    #: Emoji littéral (« 👍 ») — pas d'enum : l'interfaçage clavier reste libre.
    emoji: Mapped[str] = mapped_column(String(16), nullable=False)
    cible_type: Mapped[str] = mapped_column(String(20), nullable=False)
    cible_id: Mapped[UUID] = mapped_column(nullable=False)


class AnnonceLecture(Base, UuidPkMixin, TimestampMixin):
    """Lecture d'une annonce par un utilisateur (badge « annonces non lues »).

    Une ligne par (annonce, utilisateur) : la présence de la ligne suffit —
    l'absence compte comme non lue (incrément 24).
    """

    __tablename__ = "annonces_lectures"
    __table_args__ = (
        UniqueConstraint("annonce_id", "utilisateur_id", name="uq_annonces_lectures_paire"),
        Index("ix_annonces_lectures_utilisateur", "utilisateur_id"),
    )

    annonce_id: Mapped[UUID] = mapped_column(
        ForeignKey("annonces.id", ondelete="CASCADE"), nullable=False
    )
    utilisateur_id: Mapped[UUID] = mapped_column(
        ForeignKey("utilisateurs.id", ondelete="CASCADE"), nullable=False
    )
    lu_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


__all__ = [
    "Annonce",
    "AnnonceLecture",
    "Commentaire",
    "Conversation",
    "MembreConversation",
    "Message",
    "PieceAnnonce",
    "Reaction",
]
