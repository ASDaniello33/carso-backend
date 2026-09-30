"""Schémas API — social (incrément 20)."""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.domain.social import Annonce, Commentaire, Conversation, Message


class ConversationCreate(BaseModel):
    """Création d'une conversation directe ou d'une conférence."""

    model_config = ConfigDict(extra="forbid")

    type: str = Field(description="directe | conference")
    titre: str | None = Field(default=None, description="Titre (conférence seulement).")
    membres: list[UUID] = Field(
        default_factory=list,
        description="Autres membres (directe : exactement 1 ; conférence : 0+).",
    )


class MessageCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contenu: str = Field(default="", description="Texte du message (ou vide si pièce jointe).")
    piece_chemin: str | None = Field(
        default=None, description="Chemin logique retourné par l'upload (chat scope)."
    )
    piece_nom: str | None = None
    piece_mime: str | None = None
    piece_taille: int | None = Field(default=None, ge=0)
    reponse_a_id: UUID | None = Field(
        default=None, description="Message cité (reply) — même conversation."
    )


class DocumentConversationCreate(BaseModel):
    """Partage d'un document **existant** de la plateforme dans une conversation."""

    model_config = ConfigDict(extra="forbid")

    document_id: UUID = Field(description="Document du référentiel à importer dans le fil.")
    contenu: str = Field(
        default="",
        max_length=2000,
        description="Commentaire facultatif accompagnant le partage.",
    )


class MessageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    conversation_id: UUID
    auteur_id: UUID | None
    auteur_type: str
    auteur_agent_id: str | None
    contenu: str
    piece_nom: str | None
    piece_chemin: str | None
    piece_mime: str | None
    piece_taille: int | None
    reponse_a_id: UUID | None = None
    created_at: object


class ConversationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    type: str
    titre: str | None
    fermee_at: object | None
    archive_at: object | None = None
    creee_par_id: UUID
    created_at: object
    updated_at: object | None = None


class PieceAnnonceEntree(BaseModel):
    """Une pièce pré-stockée (incrément 33) : référence renvoyée par l'upload."""

    chemin: str = Field(min_length=1, description="Chemin logique (scope chat).")
    nom: str | None = None
    mime: str | None = None
    taille: int | None = Field(default=None, ge=0)


class AnnonceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    titre: str = Field(min_length=3, max_length=200)
    contenu: str | None = None
    # Pièce unique historique (compat) — remplacée par ``pieces`` (incr. 33).
    piece_chemin: str | None = None
    piece_nom: str | None = None
    piece_mime: str | None = None
    piece_taille: int | None = Field(default=None, ge=0)
    # Pièces multiples (incrément 33) : chaque chemin vient de l'upload.
    pieces: list[PieceAnnonceEntree] = Field(default_factory=list, max_length=10)


class PieceAnnonceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    nom: str
    mime: str | None
    taille: int | None
    position: int


class AnnonceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    auteur_id: UUID
    titre: str
    contenu: str | None
    piece_nom: str | None
    piece_chemin: str | None
    piece_mime: str | None
    piece_taille: int | None
    created_at: object
    # Pièces multiples (incrément 33) : l'ordre (position) est conservé.
    pieces: list[PieceAnnonceRead] = []


class CommentaireCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contenu: str = Field(min_length=1, max_length=2000)


class CommentaireRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    annonce_id: UUID
    auteur_id: UUID
    contenu: str
    created_at: object


class ConversationRename(BaseModel):
    """Renommage d'une conférence (animateur seul)."""

    model_config = ConfigDict(extra="forbid")

    titre: str = Field(min_length=3, max_length=200)


class AnnonceLectureCreate(BaseModel):
    """Lecture d'une annonce (badge non-lus) — incrément 24."""

    model_config = ConfigDict(extra="forbid")

    annonce_id: UUID


class ReactionCreate(BaseModel):
    """Pose/retire une réaction (tout en JSON : cible + emoji)."""

    model_config = ConfigDict(extra="forbid")

    cible_type: str = Field(description="message | annonce | commentaire")
    cible_id: UUID
    emoji: str = Field(min_length=1, max_length=16)


class ReactionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    utilisateur_id: UUID
    emoji: str
    cible_type: str
    cible_id: UUID


def lire_message(message: Message) -> MessageRead:
    return MessageRead.model_validate(message)


def lire_conversation(conversation: Conversation) -> ConversationRead:
    return ConversationRead.model_validate(conversation)


def lire_annonce(annonce: Annonce) -> AnnonceRead:
    return AnnonceRead.model_validate(annonce)


def lire_commentaire(commentaire: Commentaire) -> CommentaireRead:
    return CommentaireRead.model_validate(commentaire)


__all__ = [
    "AnnonceCreate",
    "AnnonceRead",
    "CommentaireCreate",
    "CommentaireRead",
    "ConversationCreate",
    "ConversationRead",
    "ConversationRename",
    "MessageCreate",
    "MessageRead",
    "OrphelinsChatPurgeCreate",
    "OrphelinsChatPurgeRead",
    "OrphelinChatRead",
    "ReactionCreate",
    "ReactionRead",
    "lire_annonce",
    "lire_commentaire",
    "lire_conversation",
    "lire_message",
]


# --- Purge des orphelins du scope chat (incrément 34, administrateurs) --------


class OrphelinChatRead(BaseModel):
    """Fichier candidat à la purge (lecture seule, jamais de chemin physique)."""

    model_config = ConfigDict(from_attributes=True)

    chemin: str = Field(description="Chemin logique sous le scope chat.")
    nom: str
    taille_octets: int = Field(ge=0)


class OrphelinsChatPurgeCreate(BaseModel):
    """Purge d'orphelins choisie — le motif est **obligatoire**.

    Comme la purge d'une fiche documentaire : le geste efface la seule trace
    physique de ce qui existait, un motif vide rendrait l'audit muet.
    """

    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1, max_length=2000)
    chemins: list[str] = Field(min_length=1, description="Chemins logiques choisis.")


class OrphelinsChatPurgeRead(BaseModel):
    """Rapport d'une purge : purgés réellement, ignorés (revalidés référencés)."""

    purges: int = Field(ge=0)
    ignores: list[str] = Field(default_factory=list)
