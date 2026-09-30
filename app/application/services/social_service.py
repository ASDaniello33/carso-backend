"""Service métier — social (chat, conférences, annonces) — incrément 20.

Règles appliquées (décisions validées) :

- une conversation **directe** est unique par paire (peu importe l'ordre) ;
- tout compte actif peut créer une conférence et inviter des comptes actifs ;
  l'animateur est le créateur ;
- une conférence fermée reste **lisible** mais n'accepte plus d'écriture ;
- un message/annonce/commentaire est supprimable **par son auteur seulement**
  (soft-delete, décision validée) ;
- une réaction est unique par (utilisateur, emoji, cible) : recliquer retire ;
- les pièces jointes vivent sous ``chat/{conversation_id}/`` (messages) et
  ``chat/annonce-{annonce_id}/`` (annonces) — scope ``chat`` du stockage
  documentaire, chemins logiques confinés (anti path-traversal).

Le service ne fait ni commit ni rollback (instruction/04 §5).
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select, true
from sqlalchemy.orm import Session

from app.core.errors import ConflictError, NotFoundError, PermissionDeniedError, ValidationError
from app.domain.enums import (
    RoleMembreConversation,
    TypeAuteurMessage,
    TypeCibleReaction,
    TypeConversation,
)
from app.domain.identity import Utilisateur
from app.domain.social import (
    Annonce,
    AnnonceLecture,
    Commentaire,
    Conversation,
    MembreConversation,
    Message,
    PieceAnnonce,
    Reaction,
)

#: Longueurs maximales (le chat est un canal court ; l'annonce est un billet).
MAX_MESSAGE = 4000
MAX_ANNONCE = 10000
MAX_COMMENTAIRE = 2000
MAX_EMOJI = 16
#: Pièces jointes par annonce (incrément 33) : borne haute volontairement
#: modeste — l'upload reste séquentiel côté client.
MAX_PIECES_ANNONCE = 10


class SocialService:
    """Use cases du chat social et des annonces."""

    def __init__(self, session: Session) -> None:
        self._session = session

    # ------------------------------------------------------------------
    # Conversations
    # ------------------------------------------------------------------

    def creer_directe(self, *, creee_par: UUID, autre: UUID) -> Conversation:
        """Ouvre (ou rouvre) la conversation directe entre deux comptes actifs."""
        if creee_par == autre:
            raise ValidationError("Une conversation directe se crée avec un autre utilisateur")
        self._verifier_compte_actif(autre)
        # Conversation directe unique par paire : les deux membres actifs,
        # exactement deux membres actifs au total (peu importe l'ordre).
        paire = (
            select(MembreConversation.conversation_id)
            .join(Conversation, Conversation.id == MembreConversation.conversation_id)
            .where(
                Conversation.type == TypeConversation.DIRECTE.value,
                MembreConversation.parti_at.is_(None),
                MembreConversation.utilisateur_id.in_([creee_par, autre]),
            )
            .group_by(MembreConversation.conversation_id)
            .having(func.count() == 2)
        ).scalar_subquery()
        existante = self._session.scalar(select(Conversation).where(Conversation.id.in_(paire)))
        if existante is not None:
            return existante

        conversation = Conversation(
            type=TypeConversation.DIRECTE.value,
            creee_par_id=creee_par,
        )
        self._session.add(conversation)
        self._session.flush()
        for membre in (creee_par, autre):
            self._session.add(
                MembreConversation(
                    conversation_id=conversation.id,
                    utilisateur_id=membre,
                    role=RoleMembreConversation.MEMBRE.value,
                )
            )
        self._session.flush()
        return conversation

    def creer_conference(
        self, *, titre: str, creee_par: UUID, membres: list[UUID]
    ) -> Conversation:
        """Crée une conférence (groupe temporaire nommé) avec ses membres."""
        titre_net = (titre or "").strip()
        if not 3 <= len(titre_net) <= 200:
            raise ValidationError("Le titre d'une conférence compte 3 à 200 caractères")
        self._verifier_compte_actif(creee_par)
        uniques = {m for m in membres if m != creee_par}
        for membre in uniques:
            self._verifier_compte_actif(membre)

        conversation = Conversation(
            type=TypeConversation.CONFERENCE.value,
            titre=titre_net,
            creee_par_id=creee_par,
        )
        self._session.add(conversation)
        self._session.flush()
        self._session.add(
            MembreConversation(
                conversation_id=conversation.id,
                utilisateur_id=creee_par,
                role=RoleMembreConversation.ANIMATEUR.value,
            )
        )
        for membre in uniques:
            self._session.add(
                MembreConversation(
                    conversation_id=conversation.id,
                    utilisateur_id=membre,
                    role=RoleMembreConversation.MEMBRE.value,
                )
            )
        self._session.flush()
        return conversation

    def fermer_conference(self, conversation_id: UUID, *, par: UUID) -> Conversation:
        """Clôture logique d'une conférence : lisible, plus d'écriture."""
        conversation = self._obtenir_conversation(conversation_id)
        if conversation.type != TypeConversation.CONFERENCE.value:
            raise ValidationError("Seule une conférence peut être fermée")
        self._verifier_animateur(conversation, par)
        if conversation.fermee_at is None:
            conversation.fermee_at = datetime.now(UTC)
            self._session.flush()
        return conversation

    def rejoindre_conference(
        self, conversation_id: UUID, *, utilisateur_id: UUID
    ) -> MembreConversation:
        """Invite (ou réintègre) un compte actif dans une conférence ouverte."""
        conversation = self._obtenir_conversation(conversation_id)
        if conversation.type != TypeConversation.CONFERENCE.value:
            raise ValidationError("On n'invite pas dans une conversation directe")
        if conversation.fermee_at is not None:
            raise ConflictError("Cette conférence est fermée")
        self._verifier_compte_actif(utilisateur_id)
        membre = self._session.scalar(
            select(MembreConversation).where(
                MembreConversation.conversation_id == conversation_id,
                MembreConversation.utilisateur_id == utilisateur_id,
            )
        )
        if membre is not None:
            if membre.parti_at is None:
                raise ConflictError("Cet utilisateur est déjà membre")
            membre.parti_at = None  # réintégration
            self._session.flush()
            return membre
        membre = MembreConversation(
            conversation_id=conversation_id,
            utilisateur_id=utilisateur_id,
            role=RoleMembreConversation.MEMBRE.value,
        )
        self._session.add(membre)
        self._session.flush()
        return membre

    def quitter_conversation(self, conversation_id: UUID, *, utilisateur_id: UUID) -> None:
        """Quitte une conférence (l'historique reste consultable côté autre)."""
        membre = self._session.scalar(
            select(MembreConversation).where(
                MembreConversation.conversation_id == conversation_id,
                MembreConversation.utilisateur_id == utilisateur_id,
                MembreConversation.parti_at.is_(None),
            )
        )
        if membre is None:
            raise NotFoundError("Vous n'êtes pas membre de cette conversation")
        conversation = self._obtenir_conversation(conversation_id)
        if conversation.type == TypeConversation.DIRECTE.value:
            raise ValidationError("Une conversation directe ne se quitte pas ; elle reste en liste")
        membre.parti_at = datetime.now(UTC)
        self._session.flush()

    def retirer_membre(
        self, conversation_id: UUID, *, utilisateur_cible: UUID, par: UUID
    ) -> None:
        """Retire un membre d'une conférence (animateur seul) — incrément 24.

        Soft-delete (``parti_at``) : l'historique reste consultable de son côté,
        il ne peut plus écrire ni voir les nouveaux messages. L'animateur ne
        peut pas se retirer lui-même par ce geste : il ferme ou archive.
        """
        conversation = self._obtenir_conversation(conversation_id)
        if conversation.type != TypeConversation.CONFERENCE.value:
            raise ValidationError("On ne retire un membre que d'une conférence")
        self._verifier_animateur(conversation, par)
        if utilisateur_cible == par:
            raise ValidationError(
                "L'animateur ne se retire pas lui-même : fermez ou supprimez la conférence"
            )
        membre = self._session.scalar(
            select(MembreConversation).where(
                MembreConversation.conversation_id == conversation_id,
                MembreConversation.utilisateur_id == utilisateur_cible,
                MembreConversation.parti_at.is_(None),
            )
        )
        if membre is None:
            raise NotFoundError("Ce compte n'est pas membre actif de la conférence")
        membre.parti_at = datetime.now(UTC)
        self._session.flush()

    def supprimer_conversation(self, conversation_id: UUID, *, utilisateur_id: UUID) -> None:
        """Suppression de la discussion, avec des règles par type.

        - **directe** : sort **de la liste de l'appelant** seulement (soft-delete
          par membre, ``archive_at``) — l'autre membre conserve l'historique ;
          un nouveau message la fait réapparaître.
        - **conférence** : l'animateur seul archive la discussion entière
          (``Conversation.archive_at``) : elle disparaît des listes et refuse
          l'écriture, les messages restent en archive logique.
        """
        conversation = self._obtenir_conversation(conversation_id)
        if conversation.type == TypeConversation.DIRECTE.value:
            membre = self._session.scalar(
                select(MembreConversation).where(
                    MembreConversation.conversation_id == conversation_id,
                    MembreConversation.utilisateur_id == utilisateur_id,
                    MembreConversation.parti_at.is_(None),
                )
            )
            if membre is None:
                raise PermissionDeniedError("Réservé aux membres de la conversation")
            membre.archive_at = datetime.now(UTC)
            self._session.flush()
            return
        self._verifier_animateur(conversation, utilisateur_id)
        if conversation.archive_at is None:
            conversation.archive_at = datetime.now(UTC)
            self._session.flush()

    def renommer_conversation(
        self, conversation_id: UUID, *, titre: str, utilisateur_id: UUID
    ) -> Conversation:
        """Renomme une conférence (animateur seul) — les directes n'ont pas de titre."""
        conversation = self._obtenir_conversation(conversation_id)
        if conversation.type != TypeConversation.CONFERENCE.value:
            raise ValidationError("Seule une conférence porte un titre modifiable")
        self._verifier_animateur(conversation, utilisateur_id)
        titre_net = (titre or "").strip()
        if not 3 <= len(titre_net) <= 200:
            raise ValidationError("Le titre d'une conférence compte 3 à 200 caractères")
        conversation.titre = titre_net
        self._session.flush()
        return conversation

    def lister_conversations(self, utilisateur_id: UUID) -> list[Conversation]:
        """Conversations visibles : hors archive (par membre, directes ; entière,
        conférences), membres actifs ; tri updated_at desc."""
        return list(
            self._session.scalars(
                select(Conversation)
                .join(MembreConversation, MembreConversation.conversation_id == Conversation.id)
                .where(
                    MembreConversation.utilisateur_id == utilisateur_id,
                    MembreConversation.parti_at.is_(None),
                    MembreConversation.archive_at.is_(None),
                    Conversation.archive_at.is_(None),
                )
                .order_by(Conversation.updated_at.desc())
            )
        )

    def membres_actifs(self, conversation_id: UUID) -> list[MembreConversation]:
        return list(
            self._session.scalars(
                select(MembreConversation).where(
                    MembreConversation.conversation_id == conversation_id,
                    MembreConversation.parti_at.is_(None),
                )
            )
        )

    def membres_de_plusieurs(
        self, conversation_ids: list[UUID]
    ) -> dict[UUID, list[MembreConversation]]:
        """Membres actifs de plusieurs conversations (une seule requête).

        Évite le N+1 : la liste des conversations affichée à l'écran doit nommer
        chaque discussion directe, donc lire les membres de toutes les
        conversations d'un coup.
        """
        if not conversation_ids:
            return {}
        lignes = list(
            self._session.scalars(
                select(MembreConversation)
                .where(
                    MembreConversation.conversation_id.in_(conversation_ids),
                    MembreConversation.parti_at.is_(None),
                )
                .order_by(MembreConversation.conversation_id, MembreConversation.created_at)
            )
        )
        resultat: dict[UUID, list[MembreConversation]] = {}
        for ligne in lignes:
            resultat.setdefault(ligne.conversation_id, []).append(ligne)
        return resultat

    def non_lus_par_conversation(
        self, utilisateur_id: UUID, conversation_ids: list[UUID]
    ) -> dict[UUID, int]:
        """Nombre de messages non lus de l'utilisateur, par conversation (une requête).

        Non lu = message (non supprimé, pas de moi) posté après la dernière
        lecture du membre ; jamais lu → tous comptent. Les directes archivées
        par l'utilisateur ne sont pas comptées (hors de sa liste).
        """
        if not conversation_ids:
            return {}
        membres = self._session.scalars(
            select(MembreConversation).where(
                MembreConversation.conversation_id.in_(conversation_ids),
                MembreConversation.utilisateur_id == utilisateur_id,
                MembreConversation.parti_at.is_(None),
            )
        ).all()
        resultat: dict[UUID, int] = {}
        for membre in membres:
            if membre.archive_at is not None:
                continue
            total = self._session.scalar(
                select(func.count())
                .select_from(Message)
                .where(
                    Message.conversation_id == membre.conversation_id,
                    Message.supprime_at.is_(None),
                    Message.auteur_id != utilisateur_id,
                    (
                        Message.created_at > membre.derniere_lecture_at
                        if membre.derniere_lecture_at is not None
                        else true()
                    ),
                )
            )
            if total:
                resultat[membre.conversation_id] = total
        return resultat

    def marquer_lu(self, conversation_id: UUID, *, utilisateur_id: UUID) -> None:
        """Pose la dernière lecture à « maintenant » (membre actif requis)."""
        if not self.est_membre_actif(conversation_id, utilisateur_id):
            raise PermissionDeniedError("Réservé aux membres de la conversation")
        membre = self._session.scalar(
            select(MembreConversation).where(
                MembreConversation.conversation_id == conversation_id,
                MembreConversation.utilisateur_id == utilisateur_id,
                MembreConversation.parti_at.is_(None),
            )
        )
        assert membre is not None  # garanti par est_membre_actif
        membre.derniere_lecture_at = datetime.now(UTC)
        self._session.flush()

    def marquer_non_lu(self, conversation_id: UUID, *, utilisateur_id: UUID) -> None:
        """Ramène la lecture au début de la conversation (badge « tout non lu »).

        La lecture est posée à ``created_at`` du membre lui-même : aucun
        message ne peut être antérieur à son arrivée, donc **tous** les
        messages des autres redeviennent non lus — sans valeur magique.
        """
        membre = self._session.scalar(
            select(MembreConversation).where(
                MembreConversation.conversation_id == conversation_id,
                MembreConversation.utilisateur_id == utilisateur_id,
                MembreConversation.parti_at.is_(None),
            )
        )
        if membre is None:
            raise NotFoundError("Vous n'êtes pas membre de cette conversation")
        membre.derniere_lecture_at = membre.created_at
        self._session.flush()

    def est_membre(self, conversation_id: UUID, utilisateur_id: UUID) -> bool:
        """Membre actif, ou ancien membre d'une conférence (lecture d'historique)."""
        return (
            self._session.scalar(
                select(MembreConversation.id).where(
                    MembreConversation.conversation_id == conversation_id,
                    MembreConversation.utilisateur_id == utilisateur_id,
                )
            )
            is not None
        )

    # ------------------------------------------------------------------
    # Messages
    # ------------------------------------------------------------------

    def poster_message(
        self,
        conversation_id: UUID,
        *,
        auteur_id: UUID,
        contenu: str,
        piece: dict[str, object] | None = None,
        reponse_a_id: UUID | None = None,
    ) -> Message:
        """Poste un message (texte et/ou pièce jointe déjà stockée)."""
        conversation = self._obtenir_conversation(conversation_id)
        if conversation.fermee_at is not None:
            raise ConflictError("Cette conférence est fermée à l'écriture")
        if not self.est_membre_actif(conversation_id, auteur_id):
            raise PermissionDeniedError("Seul un membre actif peut écrire")
        texte = (contenu or "").strip()
        if not texte and piece is None:
            raise ValidationError("Un message contient du texte ou une pièce jointe")
        if len(texte) > MAX_MESSAGE:
            raise ValidationError(f"Un message compte au plus {MAX_MESSAGE} caractères")
        maintenant = datetime.now(UTC)
        message = Message(
            conversation_id=conversation_id,
            auteur_id=auteur_id,
            auteur_type=TypeAuteurMessage.HUMAIN.value,
            contenu=texte,
            created_at=maintenant,
            reponse_a_id=reponse_a_id,
            **_champs_piece(piece),
        )
        self._session.add(message)
        self._session.flush()
        # Un message fait réapparaître une directe supprimée (perception de
        # l'autre membre conservée) et maintient le lien « dernière lecture »
        # de l'auteur (il vient d'écrire : rien de non lu pour lui). La
        # conversation est datée : la liste triée par updated_at reflète
        # l'activité réelle (dernier message en tête).
        conversation.updated_at = maintenant
        for membre in self.membres_actifs(conversation_id):
            if membre.utilisateur_id == auteur_id:
                membre.derniere_lecture_at = maintenant
            elif membre.archive_at is not None:
                membre.archive_at = None
        return message

    def poster_message_agent(
        self,
        conversation_id: UUID,
        *,
        agent_id: str,
        nom_agent: str,
        contenu: str,
    ) -> Message:
        """Réponse d'un agent invoqué par @mention (postée par le service, incrément 21)."""
        conversation = self._obtenir_conversation(conversation_id)
        if conversation.fermee_at is not None:
            raise ConflictError("Cette conférence est fermée à l'écriture")
        message = Message(
            conversation_id=conversation_id,
            auteur_id=None,
            auteur_type=TypeAuteurMessage.AGENT.value,
            auteur_agent_id=agent_id,
            contenu=contenu,
            created_at=datetime.now(UTC),
        )
        self._session.add(message)
        self._session.flush()
        conversation.updated_at = datetime.now(UTC)
        return message

    def lister_messages(
        self, conversation_id: UUID, *, utilisateur_id: UUID, plafond: int = 100
    ) -> list[Message]:
        """Historique du plus ancien au plus récent (membres actifs ou anciens)."""
        if not self.est_membre(conversation_id, utilisateur_id):
            raise PermissionDeniedError("Cette conversation ne vous est pas destinée")
        return list(
            self._session.scalars(
                select(Message)
                .where(
                    Message.conversation_id == conversation_id,
                    Message.supprime_at.is_(None),
                )
                .order_by(Message.created_at.asc())
                .limit(plafond)
            )
        )

    def supprimer_message(self, message_id: UUID, *, utilisateur_id: UUID) -> None:
        """Suppression par l'auteur seulement (soft-delete, décision validée)."""
        message = self._session.get(Message, message_id)
        if message is None or message.supprime_at is not None:
            raise NotFoundError("Message introuvable")
        if message.auteur_id != utilisateur_id:
            raise PermissionDeniedError("Seul l'auteur peut supprimer son message")
        message.supprime_at = datetime.now(UTC)
        self._session.flush()

    def obtenir_message(self, message_id: UUID) -> Message | None:
        return self._session.get(Message, message_id)

    # ------------------------------------------------------------------
    # Annonces / commentaires
    # ------------------------------------------------------------------

    def publier_annonce(
        self,
        *,
        auteur_id: UUID,
        titre: str,
        contenu: str | None,
        piece: dict[str, object] | None = None,
        pieces: list[dict[str, object]] | None = None,
    ) -> Annonce:
        """Publication à toute l'équipe (texte + plusieurs images/documents).

        ``pieces`` (incrément 33) : liste de ``{nom, chemin, mime, taille}`` —
        chaque chemin a été pré-stocké par ``POST /social/annonces/pieces``.
        ``piece`` (singulier) reste accepté pour compatibilité (ancien client),
        fusionné en tête de liste. Une annonce reste soumise à : texte OU
        au moins une pièce.
        """
        titre_net = (titre or "").strip()
        if not 3 <= len(titre_net) <= 200:
            raise ValidationError("Le titre d'une annonce compte 3 à 200 caractères")
        texte = (contenu or "").strip() or None
        if texte and len(texte) > MAX_ANNONCE:
            raise ValidationError(f"Une annonce compte au plus {MAX_ANNONCE} caractères")
        toutes = list(pieces or [])
        if piece is not None:
            toutes = [piece, *toutes]
        toutes = [p for p in toutes if p.get("chemin")]
        if len(toutes) > MAX_PIECES_ANNONCE:
            raise ValidationError(
                f"Une annonce accepte au plus {MAX_PIECES_ANNONCE} pièces jointes"
            )
        if not texte and not toutes:
            raise ValidationError("Une annonce contient un texte ou une pièce jointe")
        annonce = Annonce(
            auteur_id=auteur_id,
            titre=titre_net,
            contenu=texte,
        )
        for position, p in enumerate(toutes):
            annonce.pieces.append(
                PieceAnnonce(
                    nom=str(p.get("nom") or "pièce"),
                    chemin=str(p["chemin"]),
                    mime=p.get("mime"),  # type: ignore[arg-type]
                    taille=p.get("taille"),  # type: ignore[arg-type]
                    position=position,
                )
            )
        # Ancien champ (pièce unique) rempli avec la première pièce : les
        # anciens clients (et le téléchargement historique) restent valides.
        if toutes:
            premiere = toutes[0]
            annonce.piece_nom = str(premiere.get("nom") or "pièce")
            annonce.piece_chemin = str(premiere["chemin"])
            annonce.piece_mime = premiere.get("mime")  # type: ignore[assignment]
            annonce.piece_taille = premiere.get("taille")  # type: ignore[assignment]
        self._session.add(annonce)
        self._session.flush()
        return annonce

    def lister_annonces(self, *, plafond: int = 30) -> list[Annonce]:
        return list(
            self._session.scalars(
                select(Annonce)
                .where(Annonce.supprime_at.is_(None))
                .order_by(Annonce.created_at.desc())
                .limit(plafond)
            )
        )

    def supprimer_annonce(self, annonce_id: UUID, *, utilisateur_id: UUID) -> None:
        annonce = self._session.get(Annonce, annonce_id)
        if annonce is None or annonce.supprime_at is not None:
            raise NotFoundError("Annonce introuvable")
        if annonce.auteur_id != utilisateur_id:
            raise PermissionDeniedError("Seul l'auteur peut supprimer son annonce")
        annonce.supprime_at = datetime.now(UTC)
        self._session.flush()

    def commenter(
        self, annonce_id: UUID, *, auteur_id: UUID, contenu: str
    ) -> Commentaire:
        annonce = self._session.get(Annonce, annonce_id)
        if annonce is None or annonce.supprime_at is not None:
            raise NotFoundError("Annonce introuvable")
        texte = (contenu or "").strip()
        if not texte:
            raise ValidationError("Un commentaire ne peut pas être vide")
        if len(texte) > MAX_COMMENTAIRE:
            raise ValidationError(f"Un commentaire compte au plus {MAX_COMMENTAIRE} caractères")
        commentaire = Commentaire(annonce_id=annonce_id, auteur_id=auteur_id, contenu=texte)
        self._session.add(commentaire)
        self._session.flush()
        return commentaire

    def lister_commentaires(self, annonce_id: UUID) -> list[Commentaire]:
        return list(
            self._session.scalars(
                select(Commentaire)
                .where(
                    Commentaire.annonce_id == annonce_id,
                    Commentaire.supprime_at.is_(None),
                )
                .order_by(Commentaire.created_at.asc())
            )
        )

    def supprimer_commentaire(self, commentaire_id: UUID, *, utilisateur_id: UUID) -> None:
        commentaire = self._session.get(Commentaire, commentaire_id)
        if commentaire is None or commentaire.supprime_at is not None:
            raise NotFoundError("Commentaire introuvable")
        if commentaire.auteur_id != utilisateur_id:
            raise PermissionDeniedError("Seul l'auteur peut supprimer son commentaire")
        commentaire.supprime_at = datetime.now(UTC)
        self._session.flush()

    # ------------------------------------------------------------------
    # Réactions
    # ------------------------------------------------------------------

    def basculer_reaction(
        self,
        *,
        utilisateur_id: UUID,
        emoji: str,
        cible_type: str,
        cible_id: UUID,
    ) -> dict[str, object]:
        """Pose, remplace ou retire LA réaction de l'utilisateur sur la cible.

        Incrément 24 : **une seule réaction par utilisateur et par cible**
        (bug signalé : « Cœur » puis « Like » coexistaient). Même emoji →
        retrait ; autre emoji → remplacement. Répond l'emoji effectif après
        bascule (``emoji`` null = réaction retirée).
        """
        if cible_type not in {t.value for t in TypeCibleReaction}:
            raise ValidationError(f"Type de cible inconnu : {cible_type!r}")
        emoji_net = (emoji or "").strip()
        if not 1 <= len(emoji_net) <= MAX_EMOJI:
            raise ValidationError("Emoji invalide")
        self._verifier_cible_existe(cible_type, cible_id)
        existante = self._session.scalar(
            select(Reaction).where(
                Reaction.utilisateur_id == utilisateur_id,
                Reaction.cible_type == cible_type,
                Reaction.cible_id == cible_id,
            )
        )
        if existante is not None:
            if existante.emoji == emoji_net:
                self._session.delete(existante)
                self._session.flush()
                return {"reacted": False, "emoji": None}
            precedent = existante.emoji
            existante.emoji = emoji_net
            self._session.flush()
            return {"reacted": True, "emoji": emoji_net, "emoji_precedent": precedent}
        self._session.add(
            Reaction(
                utilisateur_id=utilisateur_id,
                emoji=emoji_net,
                cible_type=cible_type,
                cible_id=cible_id,
            )
        )
        self._session.flush()
        return {"reacted": True, "emoji": emoji_net}

    def reactions_de(self, cible_type: str, cible_id: UUID) -> list[Reaction]:
        return list(
            self._session.scalars(
                select(Reaction).where(
                    Reaction.cible_type == cible_type,
                    Reaction.cible_id == cible_id,
                )
            )
        )

    # ------------------------------------------------------------------
    # Réponses à un message (reply) — incrément 24
    # ------------------------------------------------------------------

    def poster_reponse(
        self,
        conversation_id: UUID,
        *,
        auteur_id: UUID,
        contenu: str,
        reponse_a_id: UUID,
        piece: dict[str, object] | None = None,
    ) -> Message:
        """Poste une réponse à un message précis de la même conversation."""
        cible = self.obtenir_message(reponse_a_id)
        if cible is None or cible.conversation_id != conversation_id:
            raise ValidationError("Le message cité n'appartient pas à cette conversation")
        return self.poster_message(
            conversation_id,
            auteur_id=auteur_id,
            contenu=contenu,
            piece=piece,
            reponse_a_id=reponse_a_id,
        )

    # ------------------------------------------------------------------
    # Lectures d'annonces (badge non-lus) — incrément 24
    # ------------------------------------------------------------------

    def marquer_annonce_lue(self, annonce_id: UUID, *, utilisateur_id: UUID) -> None:
        """Enregistre la lecture d'une annonce (idempotent)."""
        annonce = self._session.get(Annonce, annonce_id)
        if annonce is None or annonce.supprime_at is not None:
            raise NotFoundError("Annonce introuvable")
        existante = self._session.scalar(
            select(AnnonceLecture).where(
                AnnonceLecture.annonce_id == annonce_id,
                AnnonceLecture.utilisateur_id == utilisateur_id,
            )
        )
        if existante is None:
            self._session.add(
                AnnonceLecture(
                    annonce_id=annonce_id,
                    utilisateur_id=utilisateur_id,
                    lu_at=datetime.now(UTC),
                )
            )
            self._session.flush()

    def resume_non_lus(self, utilisateur_id: UUID) -> dict[str, int]:
        """Badge de la barre latérale : messages + annonces non lus (incrément 24).

        Une seule route pour le badge « Social » : le compteur de messages est
        la somme des non-lus des conversations de l'utilisateur, celui des
        annonces le nombre d'annonces visibles jamais lues.
        """
        conversations = self.lister_conversations(utilisateur_id)
        ids = [c.id for c in conversations]
        messages = sum(self.non_lus_par_conversation(utilisateur_id, ids).values())
        return {
            "messages": int(messages),
            "annonces": self.annonces_non_lues(utilisateur_id),
        }

    def annonces_non_lues(self, utilisateur_id: UUID) -> int:
        """Nombre d'annonces visibles non lues par l'utilisateur (une requête)."""
        lectures = (
            select(AnnonceLecture.annonce_id)
            .where(AnnonceLecture.utilisateur_id == utilisateur_id)
            .scalar_subquery()
        )
        return int(
            self._session.scalar(
                select(func.count())
                .select_from(Annonce)
                .where(
                    Annonce.supprime_at.is_(None),
                    Annonce.id.not_in(lectures),
                )
            )
            or 0
        )

    def marquer_toutes_annonces_lues(self, *, utilisateur_id: UUID) -> int:
        """Marque toutes les annonces visibles comme lues ; renvoie le compteur."""
        maintenant = datetime.now(UTC)
        non_lues = [a.id for a in self.lister_annonces(plafond=500)]
        lues = set(
            self._session.scalars(
                select(AnnonceLecture.annonce_id).where(
                    AnnonceLecture.utilisateur_id == utilisateur_id,
                    AnnonceLecture.annonce_id.in_(non_lues),
                )
            )
        )
        for annonce_id in non_lues:
            if annonce_id in lues:
                continue
            self._session.add(
                AnnonceLecture(
                    annonce_id=annonce_id,
                    utilisateur_id=utilisateur_id,
                    lu_at=maintenant,
                )
            )
        self._session.flush()
        return len(non_lues) - len(lues)

    # ------------------------------------------------------------------
    # Internes
    # ------------------------------------------------------------------

    def _obtenir_conversation(self, conversation_id: UUID) -> Conversation:
        conversation = self._session.get(Conversation, conversation_id)
        if conversation is None:
            raise NotFoundError(f"Conversation {conversation_id} introuvable")
        return conversation

    def est_membre_actif(self, conversation_id: UUID, utilisateur_id: UUID) -> bool:
        return (
            self._session.scalar(
                select(MembreConversation.id).where(
                    MembreConversation.conversation_id == conversation_id,
                    MembreConversation.utilisateur_id == utilisateur_id,
                    MembreConversation.parti_at.is_(None),
                )
            )
            is not None
        )

    def _verifier_compte_actif(self, utilisateur_id: UUID) -> None:
        utilisateur = self._session.get(Utilisateur, utilisateur_id)
        if utilisateur is None:
            raise NotFoundError(f"Utilisateur {utilisateur_id} introuvable")
        if utilisateur.statut != "actif":
            raise ValidationError("Seul un compte actif participe au chat social")

    def _verifier_animateur(self, conversation: Conversation, utilisateur_id: UUID) -> None:
        membre = self._session.scalar(
            select(MembreConversation).where(
                MembreConversation.conversation_id == conversation.id,
                MembreConversation.utilisateur_id == utilisateur_id,
                MembreConversation.parti_at.is_(None),
            )
        )
        if membre is None or membre.role != RoleMembreConversation.ANIMATEUR.value:
            raise PermissionDeniedError("Seul l'animateur peut fermer la conférence")

    def _verifier_cible_existe(self, cible_type: str, cible_id: UUID) -> None:
        modele = {
            TypeCibleReaction.MESSAGE.value: Message,
            TypeCibleReaction.ANNONCE.value: Annonce,
            TypeCibleReaction.COMMENTAIRE.value: Commentaire,
        }.get(cible_type)
        if modele is None or self._session.get(modele, cible_id) is None:
            raise NotFoundError("Cible de réaction introuvable")


def _champs_piece(piece: dict[str, object] | None) -> dict[str, object]:
    """Champs de pièce jointe normalisés (chemin logique déjà validé par la route)."""
    if not piece:
        return {}
    return {
        "piece_nom": piece.get("nom"),
        "piece_chemin": piece.get("chemin"),
        "piece_mime": piece.get("mime"),
        "piece_taille": piece.get("taille"),
    }


__all__ = ["SocialService"]
