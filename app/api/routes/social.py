"""Routes — social (chat, conférences, annonces) — incrément 20.

Contrôleurs minces (AGENTS.md §2.4) : les règles vivent dans ``SocialService``.
Les pièces jointes sont stockées par ``LocalDocumentStorage`` sous le scope
``chat`` — ``chat/{conversation_id}/`` pour les messages,
``chat/annonce-{annonce_id}/`` pour les annonces — avec les mêmes garanties
que le reste du système (confinement du chemin, plafond de taille, type
vérifié). Le téléchargement passe par ``/social/pieces/{message}`` et
``/social/annonces/{annonce}/piece`` : lecture réservée aux destinataires.
"""

from __future__ import annotations

import io
import uuid
from typing import Annotated
from uuid import UUID

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.api.deps import AdminUser, CurrentUser, DbSession, get_current_user
from app.api.schemas.social import (
    AnnonceCreate,
    AnnonceLectureCreate,
    CommentaireCreate,
    ConversationCreate,
    ConversationRename,
    DocumentConversationCreate,
    MessageCreate,
    OrphelinChatRead,
    OrphelinsChatPurgeCreate,
    OrphelinsChatPurgeRead,
    ReactionCreate,
    lire_annonce,
    lire_commentaire,
    lire_conversation,
    lire_message,
)
from app.application.services.social_service import SocialService
from app.application.trace import Decision
from app.core.config import get_settings
from app.documents.paths import build_logical_path
from app.documents.storage import LocalDocumentStorage
from app.domain.enums import RoleUtilisateur, TypeConversation
from app.domain.identity import Utilisateur
from app.domain.social import Annonce

router = APIRouter(
    prefix="/social",
    tags=["social"],
    dependencies=[Depends(get_current_user)],
)


def _stockage() -> LocalDocumentStorage:
    reglages = get_settings()
    return LocalDocumentStorage(reglages.storage_root, max_bytes=reglages.max_upload_bytes)


def _televerser_piece(
    fichier: UploadFile,
    chemin_logique: str,
) -> dict[str, object]:
    stocke = _stockage().save(chemin_logique, io.BytesIO(fichier.file.read()))
    return {
        "nom": fichier.filename,
        "chemin": stocke.logical_path,
        "mime": stocke.mime_type,
        "taille": stocke.taille_octets,
    }


# --- Conversations ----------------------------------------------------------------


@router.get("/conversations", response_model=list)
def lister_conversations(session: DbSession, utilisateur: CurrentUser):
    """Conversations de l'utilisateur, membres embarqués (une seule requête).

    Les membres sont inclus pour que la liste puisse nommer chaque discussion
    directe sans N appels : les ids servent de clés à l'annuaire côté client.
    """
    service = SocialService(session)
    conversations = service.lister_conversations(utilisateur.id)
    ids = [c.id for c in conversations]
    membres_par_conversation = service.membres_de_plusieurs(ids)
    non_lus = service.non_lus_par_conversation(utilisateur.id, ids)
    resultat = []
    for conversation in conversations:
        donnees = lire_conversation(conversation).model_dump(mode="json")
        donnees["membres"] = [
            {"utilisateur_id": str(m.utilisateur_id), "role": m.role}
            for m in membres_par_conversation.get(conversation.id, [])
        ]
        donnees["non_lus"] = non_lus.get(conversation.id, 0)
        resultat.append(donnees)
    return resultat


@router.post("/conversations", status_code=status.HTTP_201_CREATED)
def creer_conversation(
    payload: ConversationCreate, session: DbSession, utilisateur: CurrentUser
):
    """Crée une conversation directe (1 membre) ou une conférence (titre + membres)."""
    service = SocialService(session)
    if payload.type == TypeConversation.DIRECTE.value:
        if len(payload.membres) != 1:
            raise HTTPException(
                status_code=422,
                detail="Une conversation directe vise exactement un autre utilisateur",
            )
        conversation = service.creer_directe(
            creee_par=utilisateur.id, autre=payload.membres[0]
        )
    elif payload.type == TypeConversation.CONFERENCE.value:
        conversation = service.creer_conference(
            titre=payload.titre or "",
            creee_par=utilisateur.id,
            membres=payload.membres,
        )
    else:
        raise HTTPException(status_code=422, detail="Type de conversation inconnu")
    # Membres embarqués dès la création : l'UI nomme la discussion immédiatement
    # (sinon « Compte inconnu » jusqu'au rechargement de la liste).
    donnees = lire_conversation(conversation).model_dump(mode="json")
    donnees["membres"] = [
        {"utilisateur_id": str(m.utilisateur_id), "role": m.role}
        for m in service.membres_actifs(conversation.id)
    ]
    return donnees


def _reactions_agregees(
    session: DbSession,
    cible_type: str,
    cibles_ids: list[UUID],
    utilisateur_id: UUID,
) -> dict[str, dict[str, dict[str, object]]]:
    """Réactions consolidées de plusieurs cibles en une requête.

    Format ``{cible_id: {emoji: {total, moi}}}`` — l'UI affiche les compteurs
    dès le chargement (et surligne ceux de l'utilisateur courant) sans N appels.
    """
    from app.domain.social import Reaction

    if not cibles_ids:
        return {}
    lignes = list(
        session.scalars(
            select(Reaction).where(
                Reaction.cible_type == cible_type,
                Reaction.cible_id.in_(cibles_ids),
            )
        )
    )
    agrege: dict[str, dict[str, dict[str, object]]] = {}
    for ligne in lignes:
        par_emoji = agrege.setdefault(str(ligne.cible_id), {})
        compteur = par_emoji.setdefault(ligne.emoji, {"total": 0, "moi": False})
        compteur["total"] = int(compteur["total"]) + 1  # type: ignore[assignment]
        if ligne.utilisateur_id == utilisateur_id:
            compteur["moi"] = True
    return agrege


@router.get("/conversations/{conversation_id}/messages", response_model=list)
def lister_messages(conversation_id: UUID, session: DbSession, utilisateur: CurrentUser):
    messages = SocialService(session).lister_messages(
        conversation_id, utilisateur_id=utilisateur.id
    )
    reactions = _reactions_agregees(
        session, "message", [m.id for m in messages], utilisateur.id
    )
    resultat = []
    for message in messages:
        donnees = lire_message(message).model_dump(mode="json")
        donnees["reactions"] = reactions.get(str(message.id), {})
        resultat.append(donnees)
    return resultat


@router.post("/conversations/{conversation_id}/messages", status_code=status.HTTP_201_CREATED)
async def poster_message(
    background_tasks: BackgroundTasks,
    conversation_id: UUID,
    payload: MessageCreate,
    session: DbSession,
    utilisateur: CurrentUser,
):
    # Imports tardifs : ces services dépendent du package complet
    # ``app.application.services`` — les importer au niveau module créait un
    # cycle avec ``app.agents.providers`` (chargé par le package __init__).
    from app.application.services.social_mention_service import (
        detecter_mention_generaliste,
        repondre_mention_en_tache_de_fond,
    )

    message = SocialService(session).poster_message(
        conversation_id,
        auteur_id=utilisateur.id,
        contenu=payload.contenu,
        piece={
            "nom": payload.piece_nom,
            "chemin": payload.piece_chemin,
            "mime": payload.piece_mime,
            "taille": payload.piece_taille,
        }
        if payload.piece_chemin
        else None,
        reponse_a_id=payload.reponse_a_id,
    )
    reponse = lire_message(message).model_dump(mode="json")
    # Mention d'agent (@agent_generaliste) : traitée **après** la réponse HTTP
    # (incrément 26 — le message de l'utilisateur part instantanément, sans
    # attendre le modèle). La tâche de fond rouvre sa propre session ; le
    # message d'agent arrive ensuite dans le fil (polling front). Si une
    # mention est détectée, ``mention_en_cours`` le signale à l'UI (indicateur
    # « l'agent écrit… »).
    reponse["mention_en_cours"] = detecter_mention_generaliste(payload.contenu or "")
    if reponse["mention_en_cours"]:
        # Thread LangGraph **partagé par conversation** (incrément 31) : toutes
        # les mentions d'une même conversation alimentent le même fil de
        # mémoire de l'agent, qui répond donc en tenant compte des échanges
        # précédents. Identifiant déterministe (uuid5, jamais de collision ni
        # de table de mapping) — l'ancien ``thread_id=str(message.id)`` créait
        # un fil neuf par message, l'agent repartait sans mémoire.
        background_tasks.add_task(
            repondre_mention_en_tache_de_fond,
            conversation_id=conversation_id,
            message_contenu=payload.contenu,
            thread_id=str(
                uuid.uuid5(uuid.NAMESPACE_URL, f"carso-social-{conversation_id}")
            ),
            # Fabrique adossée à l'engine de la requête : en production c'est
            # l'engine applicatif, en test celui de la base mémoire partagée —
            # la tâche de fond voit ainsi les données de la requête.
            session_factory=sessionmaker(bind=session.get_bind(), expire_on_commit=False),
        )
    return reponse


@router.post(
    "/conversations/{conversation_id}/pieces",
    status_code=status.HTTP_201_CREATED,
)
def televerser_piece_message(
    conversation_id: UUID,
    session: DbSession,
    utilisateur: CurrentUser,
    file: Annotated[UploadFile, File(description="Photo ou document")],
    nom: Annotated[str | None, Form()] = None,
):
    """Stocke une pièce jointe sous ``chat/{conversation_id}/`` (d'abord membre)."""
    service = SocialService(session)
    if not service.est_membre_actif(conversation_id, utilisateur.id):
        raise HTTPException(status_code=403, detail="Seul un membre actif peut joindre un fichier")
    chemin = build_logical_path("chat", conversation_id, nom or file.filename or "piece")
    piece = _televerser_piece(file, chemin)
    return piece


@router.post(
    "/conversations/{conversation_id}/documents",
    status_code=status.HTTP_201_CREATED,
)
def importer_document_plateforme(
    conversation_id: UUID,
    payload: DocumentConversationCreate,
    session: DbSession,
    utilisateur: CurrentUser,
):
    """Partage un **document de la plateforme** dans une conversation (30/09).

    Contrairement à ``/pieces`` (téléversement d'un fichier neuf), cette route
    importe un document **déjà enregistré** dans le référentiel documentaire :
    son fichier est **copié** dans le scope ``chat`` (le document d'origine
    reste inchangé — jamais déplacé), puis un message est posté avec cette
    pièce : tous les membres de la conversation peuvent le voir et le
    télécharger par ``/social/pieces/{message_id}``, avec le même contrôle
    d'accès que n'importe quelle pièce de chat (réservé aux membres).
    """
    from app.application.services.document_service import DocumentService
    from app.documents.paths import resolve_within_root

    service = SocialService(session)
    if not service.est_membre_actif(conversation_id, utilisateur.id):
        raise HTTPException(
            status_code=403, detail="Seul un membre actif peut partager un document"
        )

    document = DocumentService(session).obtenir(payload.document_id)
    chemin_source = resolve_within_root(get_settings().storage_root, document.storage_path)
    mime = document.mime_type or "application/octet-stream"

    # Message d'abord (la pièce référencée doit exister au moment du post) :
    # on copie le fichier, puis on poste le message porteur de la pièce.
    conversation_dir = conversation_id
    chemin_cible = build_logical_path("chat", conversation_dir, document.nom)
    stocke = _stockage().save(chemin_cible, io.BytesIO(chemin_source.read_bytes()))
    message = service.poster_message(
        conversation_id,
        auteur_id=utilisateur.id,
        contenu=payload.contenu or "",
        piece={
            "nom": document.nom,
            "chemin": stocke.logical_path,
            "mime": mime,
            "taille": document.taille_octets or stocke.taille_octets,
        },
    )
    return lire_message(message).model_dump(mode="json")


@router.delete("/messages/{message_id}", status_code=status.HTTP_204_NO_CONTENT)
def supprimer_message(message_id: UUID, session: DbSession, utilisateur: CurrentUser) -> None:
    SocialService(session).supprimer_message(message_id, utilisateur_id=utilisateur.id)


@router.get("/pieces/{message_id}", response_class=FileResponse)
def telecharger_piece_message(message_id: UUID, session: DbSession, utilisateur: CurrentUser):
    """Télécharge la pièce jointe d'un message (réservé aux membres)."""
    service = SocialService(session)
    message = service.obtenir_message(message_id)
    if message is None or message.piece_chemin is None:
        raise HTTPException(status_code=404, detail="Pièce jointe introuvable")
    if not service.est_membre(message.conversation_id, utilisateur.id):
        raise HTTPException(status_code=403, detail="Réservé aux membres de la conversation")
    from app.documents.paths import resolve_within_root

    chemin = resolve_within_root(get_settings().storage_root, message.piece_chemin)
    return FileResponse(path=chemin, filename=message.piece_nom or "piece")


# --- Conférences ------------------------------------------------------------------


@router.post("/conversations/{conversation_id}/membres", status_code=status.HTTP_201_CREATED)
def inviter_membre(
    conversation_id: UUID,
    utilisateur_cible: Annotated[UUID, Form()],
    session: DbSession,
    utilisateur: CurrentUser,
):
    membre = SocialService(session).rejoindre_conference(
        conversation_id, utilisateur_id=utilisateur_cible
    )
    return {"id": str(membre.id), "utilisateur_id": str(membre.utilisateur_id)}


@router.post("/conversations/{conversation_id}/depart", status_code=status.HTTP_204_NO_CONTENT)
def quitter_conversation(
    conversation_id: UUID, session: DbSession, utilisateur: CurrentUser
) -> None:
    SocialService(session).quitter_conversation(conversation_id, utilisateur_id=utilisateur.id)


@router.post(
    "/conversations/{conversation_id}/membres/{utilisateur_cible}/retrait",
    status_code=status.HTTP_204_NO_CONTENT,
)
def retirer_membre(
    conversation_id: UUID,
    utilisateur_cible: UUID,
    session: DbSession,
    utilisateur: CurrentUser,
) -> None:
    """Retire un membre de la conférence (animateur seul) — incrément 24."""
    SocialService(session).retirer_membre(
        conversation_id, utilisateur_cible=utilisateur_cible, par=utilisateur.id
    )


@router.post("/conversations/{conversation_id}/fermeture")
def fermer_conference(conversation_id: UUID, session: DbSession, utilisateur: CurrentUser):
    conversation = SocialService(session).fermer_conference(conversation_id, par=utilisateur.id)
    return lire_conversation(conversation)


@router.patch("/conversations/{conversation_id}")
def renommer_conversation(
    conversation_id: UUID,
    payload: ConversationRename,
    session: DbSession,
    utilisateur: CurrentUser,
):
    """Renomme une conférence (animateur seul)."""
    conversation = SocialService(session).renommer_conversation(
        conversation_id, titre=payload.titre, utilisateur_id=utilisateur.id
    )
    return lire_conversation(conversation)


@router.delete("/conversations/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
def supprimer_conversation(
    conversation_id: UUID, session: DbSession, utilisateur: CurrentUser
) -> None:
    """Supprime la discussion de la liste de l'appelant (directe) ou l'archive
    entière (conférence, animateur seul)."""
    SocialService(session).supprimer_conversation(conversation_id, utilisateur_id=utilisateur.id)


@router.get("/non-lus")
def badge_non_lus(session: DbSession, utilisateur: CurrentUser) -> dict[str, int]:
    """Badge « Social » de la barre latérale (incrément 24).

    ``messages`` : somme des messages non lus de toutes les conversations ;
    ``annonces`` : annonces visibles jamais lues. Un appel unique évite au
    layout de charger les listes complètes juste pour compter.
    """
    return SocialService(session).resume_non_lus(utilisateur.id)


@router.post("/conversations/{conversation_id}/lecture", status_code=status.HTTP_204_NO_CONTENT)
def marquer_lu(conversation_id: UUID, session: DbSession, utilisateur: CurrentUser) -> None:
    """Marque la conversation comme lue jusqu'à maintenant (badge non-lus)."""
    SocialService(session).marquer_lu(conversation_id, utilisateur_id=utilisateur.id)


@router.post(
    "/conversations/{conversation_id}/non-lecture", status_code=status.HTTP_204_NO_CONTENT
)
def marquer_non_lu(conversation_id: UUID, session: DbSession, utilisateur: CurrentUser) -> None:
    """Marque toute la conversation comme non lue (badge plein)."""
    SocialService(session).marquer_non_lu(conversation_id, utilisateur_id=utilisateur.id)


@router.get("/conversations/{conversation_id}/membres")
def lister_membres(conversation_id: UUID, session: DbSession, utilisateur: CurrentUser):
    service = SocialService(session)
    if not service.est_membre(conversation_id, utilisateur.id):
        raise HTTPException(status_code=403, detail="Réservé aux membres")
    membres = service.membres_actifs(conversation_id)
    return [
        {
            "utilisateur_id": str(m.utilisateur_id),
            "role": m.role,
        }
        for m in membres
    ]


# --- Annonces ---------------------------------------------------------------------


@router.get("/annonces", response_model=list)
def lister_annonces(session: DbSession, utilisateur: CurrentUser):
    annonces = SocialService(session).lister_annonces()
    reactions = _reactions_agregees(
        session, "annonce", [a.id for a in annonces], utilisateur.id
    )
    resultat = []
    for annonce in annonces:
        donnees = lire_annonce(annonce).model_dump(mode="json")
        donnees["reactions"] = reactions.get(str(annonce.id), {})
        resultat.append(donnees)
    return resultat


@router.post("/annonces", status_code=status.HTTP_201_CREATED)
def publier_annonce(payload: AnnonceCreate, session: DbSession, utilisateur: CurrentUser):
    annonce = SocialService(session).publier_annonce(
        auteur_id=utilisateur.id,
        titre=payload.titre,
        contenu=payload.contenu,
        piece={
            "nom": payload.piece_nom,
            "chemin": payload.piece_chemin,
            "mime": payload.piece_mime,
            "taille": payload.piece_taille,
        }
        if payload.piece_chemin
        else None,
        # Incrément 33 : pièces multiples (chaque chemin pré-stocké par
        # ``POST /social/annonces/pieces``) — l'ordre du formulaire est
        # conservé (position).
        pieces=[p.model_dump() for p in payload.pieces],
    )
    return lire_annonce(annonce)


@router.post("/annonces/lecture", status_code=status.HTTP_204_NO_CONTENT)
def marquer_annonce_lue(
    payload: AnnonceLectureCreate, session: DbSession, utilisateur: CurrentUser
) -> None:
    """Enregistre la lecture d'une annonce (badge « annonces non lues »)."""
    SocialService(session).marquer_annonce_lue(
        payload.annonce_id, utilisateur_id=utilisateur.id
    )


@router.post("/annonces/lecture-tout", status_code=status.HTTP_204_NO_CONTENT)
def marquer_toutes_annonces_lues(session: DbSession, utilisateur: CurrentUser) -> None:
    SocialService(session).marquer_toutes_annonces_lues(utilisateur_id=utilisateur.id)


@router.post("/annonces/pieces", status_code=status.HTTP_201_CREATED)
def televerser_piece_annonce(
    session: DbSession,
    utilisateur: CurrentUser,
    file: Annotated[UploadFile, File(description="Image ou document de l'annonce")],
    nom: Annotated[str | None, Form()] = None,
):
    """Pré-stocke la pièce d'une annonce sous ``chat/annonce-{uuid}/``.

    L'identifiant est généré ici (l'annonce n'existe pas encore) et repassé
    dans ``AnnonceCreate.piece_chemin`` par le client.
    """
    from uuid import uuid4

    chemin = build_logical_path("chat", uuid4(), f"annonce-{nom or file.filename or 'piece'}")
    return _televerser_piece(file, chemin)


@router.delete("/annonces/{annonce_id}", status_code=status.HTTP_204_NO_CONTENT)
def supprimer_annonce(annonce_id: UUID, session: DbSession, utilisateur: CurrentUser) -> None:
    SocialService(session).supprimer_annonce(annonce_id, utilisateur_id=utilisateur.id)


@router.get("/annonces/{annonce_id}/pieces/{piece_id}", response_class=FileResponse)
def telecharger_piece_annonce_multiple(
    annonce_id: UUID, piece_id: UUID, session: DbSession, utilisateur: CurrentUser
):
    """Télécharge une pièce **parmi plusieurs** (incrément 33).

    Même contrat de sécurité que l'ancien endpoint : annonce visible, chemin
    résolu sous le scope ``chat`` (anti path-traversal).
    """
    from app.domain.social import PieceAnnonce

    annonce = session.get(Annonce, annonce_id)
    if annonce is None or annonce.supprime_at is not None:
        raise HTTPException(status_code=404, detail="Annonce introuvable")
    piece = session.get(PieceAnnonce, piece_id)
    if piece is None or piece.annonce_id != annonce_id:
        raise HTTPException(status_code=404, detail="Pièce jointe introuvable")
    from app.documents.paths import resolve_within_root

    chemin = resolve_within_root(get_settings().storage_root, piece.chemin)
    return FileResponse(path=chemin, filename=piece.nom or "piece")


@router.get("/annonces/{annonce_id}/piece", response_class=FileResponse)
def telecharger_piece_annonce(annonce_id: UUID, session: DbSession, utilisateur: CurrentUser):
    annonce = session.get(Annonce, annonce_id)
    if annonce is None or annonce.supprime_at is not None or annonce.piece_chemin is None:
        raise HTTPException(status_code=404, detail="Pièce jointe introuvable")
    from app.documents.paths import resolve_within_root

    chemin = resolve_within_root(get_settings().storage_root, annonce.piece_chemin)
    return FileResponse(path=chemin, filename=annonce.piece_nom or "piece")


# --- Commentaires -----------------------------------------------------------------


@router.get("/annonces/{annonce_id}/commentaires", response_model=list)
def lister_commentaires(annonce_id: UUID, session: DbSession, utilisateur: CurrentUser):
    return [
        lire_commentaire(c) for c in SocialService(session).lister_commentaires(annonce_id)
    ]


@router.post(
    "/annonces/{annonce_id}/commentaires",
    status_code=status.HTTP_201_CREATED,
)
def commenter(
    annonce_id: UUID,
    payload: CommentaireCreate,
    session: DbSession,
    utilisateur: CurrentUser,
):
    commentaire = SocialService(session).commenter(
        annonce_id, auteur_id=utilisateur.id, contenu=payload.contenu
    )
    return lire_commentaire(commentaire)


@router.delete("/commentaires/{commentaire_id}", status_code=status.HTTP_204_NO_CONTENT)
def supprimer_commentaire(
    commentaire_id: UUID, session: DbSession, utilisateur: CurrentUser
) -> None:
    SocialService(session).supprimer_commentaire(commentaire_id, utilisateur_id=utilisateur.id)


# --- Réactions --------------------------------------------------------------------


@router.post("/reactions")
def basculer_reaction(
    payload: ReactionCreate,
    session: DbSession,
    utilisateur: CurrentUser,
) -> dict[str, object]:
    """Pose ou retire une réaction (JSON : cible_type + cible_id + emoji).

    Répond l'état consolidé lisible par l'UI : ``reacted`` (le clic a posé ou
    retiré), ``total`` (nombre de réactions de cet emoji sur la cible) et
    ``moi`` (l'utilisateur courant a réagi — coloration de la bulle).
    """
    service = SocialService(session)
    resultat = service.basculer_reaction(
        utilisateur_id=utilisateur.id,
        emoji=payload.emoji,
        cible_type=payload.cible_type,
        cible_id=payload.cible_id,
    )
    reactions = service.reactions_de(payload.cible_type, payload.cible_id)
    emoji_choisi = payload.emoji.strip()
    emoji_effectif = str(resultat.get("emoji") or "") or emoji_choisi
    return {
        "reacted": resultat["reacted"],
        "total": sum(1 for r in reactions if r.emoji == emoji_effectif),
        "moi": bool(resultat["reacted"]),
        "emoji": resultat.get("emoji"),
    }


@router.get("/reactions", response_model=list)
def lister_reactions(
    cible_type: str,
    cible_id: UUID,
    session: DbSession,
    utilisateur: CurrentUser,
):
    reactions = SocialService(session).reactions_de(cible_type, cible_id)
    from app.api.schemas.social import ReactionRead

    return [ReactionRead.model_validate(r).model_dump(mode="json") for r in reactions]


# --- Présence (incrément 21) -------------------------------------------------------


@router.post("/presence/battement", status_code=status.HTTP_204_NO_CONTENT)
def battement_presence(session: DbSession, utilisateur: CurrentUser) -> None:
    """Enregistre le dernier signal de vie de l'utilisateur (polling du frontend)."""
    from app.application.services.presence_chat_service import PresenceChatService

    PresenceChatService(session).battement(utilisateur.id)


@router.get("/presence/{utilisateur_id}")
def lire_presence(utilisateur_id: UUID, session: DbSession, utilisateur: CurrentUser):
    """Statut calculé d'un utilisateur : en_ligne | absent | hors_ligne."""
    from app.application.services.presence_chat_service import PresenceChatService

    return PresenceChatService(session).statut(utilisateur_id)


@router.post("/presence")
def lire_presences(
    ids: list[UUID], session: DbSession, utilisateur: CurrentUser
) -> dict[str, dict]:
    """Statuts de plusieurs utilisateurs (en-tête de conversation, liste social)."""
    from app.application.services.presence_chat_service import PresenceChatService

    return PresenceChatService(session).statuts_pour(ids)


# --- Annuaire (comptes actifs, données minimales) ---------------------------------


@router.get("/annuaire")
def annuaire(session: DbSession, utilisateur: CurrentUser) -> list[dict]:
    """Comptes **actifs** pour nommer les auteurs et choisir un destinataire.

    Données minimales d'un annuaire interne (id, nom, prénom, rôle + présence
    d'une photo — incrément 26) : ni email ni téléphone — le social n'a pas
    besoin de plus pour afficher un auteur ou ouvrir une discussion directe.
    """
    comptes = list(
        session.scalars(
            select(Utilisateur).where(
                Utilisateur.statut == "actif",
                Utilisateur.role != RoleUtilisateur.EN_ATTENTE.value,
            )
        )
    )
    return [
        {
            "id": str(compte.id),
            "nom": compte.nom,
            "prenom": compte.prenom,
            "role": compte.role,
            "photo_profil": bool(compte.photo_profil_chemin),
        }
        for compte in comptes
    ]


@router.get("/annuaire/{utilisateur_id}/photo-profil", response_class=FileResponse)
def photo_profil_annuaire(
    utilisateur_id: UUID, session: DbSession, utilisateur: CurrentUser
):
    """Photo de profil d'un compte de l'annuaire social (incrément 26).

    Réservée aux comptes actifs (le social ne nomme que des comptes actifs) :
    le Bearer du demandeur est requis, le fichier est servi depuis le stockage
    confiné (``resolve_within_root``) — jamais d'URL publique.
    """
    from fastapi import HTTPException

    from app.documents.paths import resolve_within_root

    cible = session.get(Utilisateur, utilisateur_id)
    if cible is None or cible.statut != "actif":
        raise HTTPException(status_code=404, detail="Compte introuvable")
    if not cible.photo_profil_chemin:
        raise HTTPException(status_code=404, detail="Aucune photo de profil")
    chemin = resolve_within_root(get_settings().storage_root, cible.photo_profil_chemin)
    return FileResponse(path=chemin)


# --- Purge des orphelins du scope chat (incrément 34, administrateurs) ---------


@router.get("/admin/chat/orphelins", response_model=list[OrphelinChatRead])
def lister_orphelins_chat(session: DbSession, _admin: AdminUser) -> list[OrphelinChatRead]:
    """Fichiers orphelins du scope ``chat`` — lecture pure, administrateurs.

    Un orphelin est un fichier du stockage social qu'aucune ligne métier ne
    référence : pièce retirée avant publication, annonce supprimée. La liste
    est un **candidat** : rien n'est purgé avant la validation humaine.
    """
    from app.application.services import OrphelinsChatService

    return list(OrphelinsChatService(session).lister_orphelins())


@router.post("/admin/chat/orphelins/purge", response_model=OrphelinsChatPurgeRead)
def purger_orphelins_chat(
    payload: OrphelinsChatPurgeCreate,
    session: DbSession,
    utilisateur: CurrentUser,
    _admin: AdminUser,
) -> OrphelinsChatPurgeRead:
    """Purge **validée** des orphelins choisis — administrateurs, motif obligatoire.

    Le service revalide chaque chemin au moment de l'exécution (référencé
    entre-temps → ignoré, jamais détruit) et trace le geste dans l'audit.
    """
    from app.application.services import OrphelinsChatService

    rapport = OrphelinsChatService(session, actor_id=str(utilisateur.id)).purger_orphelins(
        Decision(decided_by=str(utilisateur.id), reason=payload.reason),
        payload.chemins,
    )
    return OrphelinsChatPurgeRead(purges=rapport.purges, ignores=rapport.ignores)
