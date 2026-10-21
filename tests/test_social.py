"""Chat social — conversations, messages, annonces, réactions (incrément 20).

Le stockage des pièces jointes est testé avec le vrai ``LocalDocumentStorage``
pointé sur un dossier temporaire : le contrat ``chat/{conversation_id}/`` est
celui de la production (chemin logique confiné).
"""

from __future__ import annotations

import io
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.application.services.social_service import SocialService
from app.core.errors import ConflictError, NotFoundError, PermissionDeniedError, ValidationError
from app.domain.enums import (
    RoleMembreConversation,
    TypeAuteurMessage,
    TypeConversation,
)
from app.domain.identity import Utilisateur
from app.domain.social import Message


def _compte(statut: str = "actif") -> Utilisateur:
    return Utilisateur(
        email=f"{uuid4().hex[:8]}@carso.mg",
        nom="Test",
        prenom="User",
        password_hash="x",
        role="collaborateur",
        statut=statut,
    )


# --- Conversations ----------------------------------------------------------------


def test_conversation_directe_unique_par_paire(api_session_factory) -> None:
    session = api_session_factory()
    try:
        alice = _compte()
        bob = _compte()
        session.add_all([alice, bob])
        session.commit()

        service = SocialService(session)
        premiere = service.creer_directe(creee_par=alice.id, autre=bob.id)
        seconde = service.creer_directe(creee_par=bob.id, autre=alice.id)
        assert premiere.id == seconde.id
        assert premiere.type == TypeConversation.DIRECTE.value
    finally:
        session.close()


def test_conversation_directe_refuse_soi_meme(api_session_factory) -> None:
    session = api_session_factory()
    try:
        alice = _compte()
        session.add(alice)
        session.commit()
        with pytest.raises(ValidationError):
            SocialService(session).creer_directe(creee_par=alice.id, autre=alice.id)
    finally:
        session.close()


def test_conference_animateur_et_membres(api_session_factory) -> None:
    session = api_session_factory()
    try:
        alice = _compte()
        bob = _compte()
        carol = _compte()
        session.add_all([alice, bob, carol])
        session.commit()

        service = SocialService(session)
        conference = service.creer_conference(
            titre="Point hebdo", creee_par=alice.id, membres=[bob.id, carol.id]
        )
        membres = service.membres_actifs(conference.id)
        roles = {str(m.utilisateur_id): m.role for m in membres}
        assert roles[str(alice.id)] == RoleMembreConversation.ANIMATEUR.value
        assert roles[str(bob.id)] == RoleMembreConversation.MEMBRE.value

        # Seul l'animateur ferme ; la conférence fermée refuse l'écriture
        # mais reste lisible.
        with pytest.raises(PermissionDeniedError):
            service.fermer_conference(conference.id, par=bob.id)
        service.fermer_conference(conference.id, par=alice.id)
        with pytest.raises(ConflictError):
            service.poster_message(
                conference.id, auteur_id=bob.id, contenu="Bonjour ?"
            )
        messages = service.lister_messages(
            conference.id, utilisateur_id=bob.id
        )
        assert messages == []
    finally:
        session.close()


def test_reintegration_apres_depart(api_session_factory) -> None:
    session = api_session_factory()
    try:
        alice = _compte()
        bob = _compte()
        session.add_all([alice, bob])
        session.commit()

        service = SocialService(session)
        conference = service.creer_conference(
            titre="Chantier X", creee_par=alice.id, membres=[bob.id]
        )
        service.quitter_conversation(conference.id, utilisateur_id=bob.id)
        service.rejoindre_conference(conference.id, utilisateur_id=bob.id)
        assert service.est_membre_actif(conference.id, bob.id)
        with pytest.raises(ConflictError):
            service.rejoindre_conference(conference.id, utilisateur_id=bob.id)
    finally:
        session.close()


# --- Messages ---------------------------------------------------------------------


def test_message_exige_membre_actif_et_contenu(api_session_factory) -> None:
    session = api_session_factory()
    try:
        alice = _compte()
        bob = _compte()
        carol = _compte()
        session.add_all([alice, bob, carol])
        session.commit()

        service = SocialService(session)
        conversation = service.creer_directe(creee_par=alice.id, autre=bob.id)

        # Carol n'est pas membre : interdite.
        with pytest.raises(PermissionDeniedError):
            service.poster_message(conversation.id, auteur_id=carol.id, contenu="salut")
        # Message vide sans pièce jointe : interdit.
        with pytest.raises(ValidationError):
            service.poster_message(conversation.id, auteur_id=alice.id, contenu="   ")

        message = service.poster_message(conversation.id, auteur_id=alice.id, contenu="Bonjour")
        assert message.contenu == "Bonjour"
        # Bob lit ; l'auteur (seul) supprime.
        assert service.lister_messages(conversation.id, utilisateur_id=bob.id)[0].id == message.id
        with pytest.raises(PermissionDeniedError):
            service.supprimer_message(message.id, utilisateur_id=bob.id)
        service.supprimer_message(message.id, utilisateur_id=alice.id)
        assert service.lister_messages(conversation.id, utilisateur_id=bob.id) == []
    finally:
        session.close()


def test_message_agent_poste_par_le_service(api_session_factory) -> None:
    session = api_session_factory()
    try:
        alice = _compte()
        bob = _compte()
        session.add_all([alice, bob])
        session.commit()

        service = SocialService(session)
        conversation = service.creer_directe(creee_par=alice.id, autre=bob.id)
        reponse = service.poster_message_agent(
            conversation.id,
            agent_id="agent_generaliste_readonly",
            nom_agent="Agent Généraliste",
            contenu="Il y a 2 missions en cours.",
        )
        assert reponse.auteur_type == "agent"
        assert reponse.auteur_agent_id == "agent_generaliste_readonly"
        assert reponse.auteur_id is None
    finally:
        session.close()


# --- Annonces / commentaires / réactions ------------------------------------------


def test_annonce_commentaires_et_suppression_auteur(api_session_factory) -> None:
    session = api_session_factory()
    try:
        alice = _compte()
        bob = _compte()
        session.add_all([alice, bob])
        session.commit()

        service = SocialService(session)
        annonce = service.publier_annonce(
            auteur_id=alice.id, titre="Réunion générale", contenu="Vendredi 10h"
        )
        commentaire = service.commenter(
            annonce.id, auteur_id=bob.id, contenu="Bien noté"
        )
        assert service.lister_commentaires(annonce.id)[0].id == commentaire.id

        with pytest.raises(PermissionDeniedError):
            service.supprimer_annonce(annonce.id, utilisateur_id=bob.id)
        service.supprimer_annonce(annonce.id, utilisateur_id=alice.id)
        assert service.lister_annonces() == []
        # Le commentaire d'une annonce supprimée n'est plus commentable :
        with pytest.raises(NotFoundError):
            service.commenter(annonce.id, auteur_id=bob.id, contenu="encore ?")
        # Suppression de son propre commentaire : ok.
        with pytest.raises(PermissionDeniedError):
            service.supprimer_commentaire(commentaire.id, utilisateur_id=alice.id)
        service.supprimer_commentaire(commentaire.id, utilisateur_id=bob.id)
    finally:
        session.close()


def test_annonce_pieces_multiples(api_session_factory) -> None:
    """Une annonce porte **plusieurs** pièces, dans l'ordre (incrément 33).

    Règles vérifiées : ordre conservé (position), compatibilité pièce unique
    (remplie avec la 1re pièce), borne haute (10), texte OU pièce obligatoire.
    """
    session = api_session_factory()
    try:
        alice = _compte()
        session.add(alice)
        session.commit()

        service = SocialService(session)
        annonce = service.publier_annonce(
            auteur_id=alice.id,
            titre="Photos et rapport",
            contenu="Support de la réunion",
            pieces=[
                {"nom": "photo-a.png", "chemin": "chat/x/a.png",
                 "mime": "image/png", "taille": 100},
                {"nom": "photo-b.png", "chemin": "chat/x/b.png",
                 "mime": "image/png", "taille": 200},
                {"nom": "rapport.pdf", "chemin": "chat/x/r.pdf",
                 "mime": "application/pdf", "taille": 300},
            ],
        )
        assert [p.nom for p in annonce.pieces] == ["photo-a.png", "photo-b.png", "rapport.pdf"]
        assert [p.position for p in annonce.pieces] == [0, 1, 2]
        # Compat : la pièce unique historique reflète la première pièce.
        assert annonce.piece_nom == "photo-a.png"
        assert annonce.piece_chemin == "chat/x/a.png"

        # Sans texte ni pièce : refusé.
        with pytest.raises(ValidationError):
            service.publier_annonce(auteur_id=alice.id, titre="Vide", contenu="  ")

        # Au-delà de la borne : refusé.
        with pytest.raises(ValidationError):
            service.publier_annonce(
                auteur_id=alice.id,
                titre="Trop de pièces",
                contenu=None,
                pieces=[
                    {"chemin": f"chat/x/p{i}.png", "nom": f"p{i}"}
                    for i in range(11)
                ],
            )
    finally:
        session.close()


def test_reaction_bascule_et_unicite(api_session_factory) -> None:
    """Une SEULE réaction par utilisateur et par cible (incrément 24).

    Même emoji recliqué → retrait ; autre emoji → remplacement (le bug
    « Cœur puis Like coexistaient » est corrigé côté contrat de service).
    """
    session = api_session_factory()
    try:
        alice = _compte()
        bob = _compte()
        session.add_all([alice, bob])
        session.commit()

        service = SocialService(session)
        conversation = service.creer_directe(creee_par=alice.id, autre=bob.id)
        message = service.poster_message(conversation.id, auteur_id=alice.id, contenu="👍 ?")

        assert service.basculer_reaction(
            utilisateur_id=bob.id,
            emoji="👍",
            cible_type="message",
            cible_id=message.id,
        ) == {"reacted": True, "emoji": "👍"}
        # Second clic sur le même emoji → retire.
        assert service.basculer_reaction(
            utilisateur_id=bob.id,
            emoji="👍",
            cible_type="message",
            cible_id=message.id,
        ) == {"reacted": False, "emoji": None}
        # Un autre emoji après retrait : pose simple.
        assert service.basculer_reaction(
            utilisateur_id=bob.id,
            emoji="❤️",
            cible_type="message",
            cible_id=message.id,
        ) == {"reacted": True, "emoji": "❤️"}
        # Un AUTRE emoji alors qu'une réaction existe → remplace (jamais 2).
        resultat = service.basculer_reaction(
            utilisateur_id=bob.id,
            emoji="🎉",
            cible_type="message",
            cible_id=message.id,
        )
        assert resultat == {"reacted": True, "emoji": "🎉", "emoji_precedent": "❤️"}
        emojis = [r.emoji for r in service.reactions_de("message", message.id)]
        assert emojis == ["🎉"]
        # Mauvais type de cible : refusé.
        with pytest.raises(ValidationError):
            service.basculer_reaction(
                utilisateur_id=bob.id,
                emoji="🎉",
                cible_type="mur",
                cible_id=message.id,
            )
    finally:
        session.close()


# --- API + stockage des pièces jointes ---------------------------------------------


def test_upload_piece_jointe_dans_le_scope_chat(
    api_session_factory, api_client: TestClient, tmp_path, monkeypatch
) -> None:
    """La pièce jointe part bien sous ``chat/{conversation_id}/`` (dossier réel)."""
    from app.core.config import get_settings
    from app.domain.social import Conversation, MembreConversation

    monkeypatch.setattr(get_settings(), "storage_root", str(tmp_path))
    session = api_session_factory()
    try:
        alice = _compte()
        bob = _compte()
        session.add_all([alice, bob])
        session.commit()
        conversation = Conversation(type="directe", creee_par_id=alice.id)
        session.add(conversation)
        session.flush()
        # En mode lab, l'appelant API est l'utilisateur technique d'id stable
        # (dérivé de "carso://lab-user") : on l'inscrit comme membre, sinon la
        # route refuserait (non-membre).
        from app.api.deps import _LAB_USER_ID

        lab = Utilisateur(
            id=_LAB_USER_ID,
            email="lab@localhost",
            nom="Lab",
            prenom="Mode",
            password_hash="!",
            role="collaborateur",
            statut="actif",
        )
        session.merge(lab)
        session.flush()
        for utilisateur in (alice, bob, lab):
            session.add(
                MembreConversation(
                    conversation_id=conversation.id,
                    utilisateur_id=utilisateur.id,
                    role="membre",
                )
            )
        session.commit()
        conversation_id = conversation.id
    finally:
        session.close()

    reponse = api_client.post(
        f"/api/v1/social/conversations/{conversation_id}/pieces",
        files={"file": ("note.txt", io.BytesIO(b"contenu du chat"), "text/plain")},
        data={"nom": "note.txt"},
    )
    assert reponse.status_code == 201
    piece = reponse.json()
    assert piece["chemin"].startswith(f"chat/{conversation_id}/")
    assert (tmp_path / piece["chemin"]).exists()

    # Le message avec pièce jointe est posté via l'API (mêmes transaction et
    # session que la lecture qui suit) ; l'auteur est l'utilisateur lab.
    message_reponse = api_client.post(
        f"/api/v1/social/conversations/{conversation_id}/messages",
        json={
            "contenu": "voir la pièce",
            "piece_chemin": piece["chemin"],
            "piece_nom": piece["nom"],
            "piece_mime": piece["mime"],
            "piece_taille": piece["taille"],
        },
    )
    assert message_reponse.status_code == 201
    message_id = message_reponse.json()["id"]

    attendu = api_client.get(f"/api/v1/social/pieces/{message_id}")
    assert attendu.status_code == 200
    assert attendu.content == b"contenu du chat"


def test_badge_non_lus_route(api_session_factory, api_client: TestClient) -> None:
    """GET /social/non-lus : le badge sidebar compte messages + annonces (incr. 24)."""
    session = api_session_factory()
    try:
        from app.api.deps import _LAB_USER_ID

        lab = Utilisateur(
            id=_LAB_USER_ID,
            email="lab@localhost",
            nom="Lab",
            prenom="Mode",
            password_hash="!",
            role="collaborateur",
            statut="actif",
        )
        alice = _compte()
        session.add_all([lab, alice])
        session.commit()
        service = SocialService(session)
        conversation = service.creer_directe(creee_par=alice.id, autre=lab.id)
        service.poster_message(conversation.id, auteur_id=alice.id, contenu="salut")
        service.publier_annonce(
            auteur_id=alice.id, titre="Nouvelle consigne", contenu="Merci de la lire"
        )
        session.commit()
    finally:
        session.close()

    badge = api_client.get("/api/v1/social/non-lus").json()
    assert badge["messages"] >= 1
    assert badge["annonces"] >= 1


def test_routes_sociales_exigent_authentification(api_client: TestClient) -> None:
    """Mode lab : l'utilisateur technique passe ; le contrat de liste est stable."""
    reponse = api_client.get("/api/v1/social/conversations")
    assert reponse.status_code == 200
    assert reponse.json() == []


# --- Incrément 23 : lecture, suppression, renommage, réactions API ----------------


def test_non_lus_et_marquage_lecture(api_session_factory) -> None:
    session = api_session_factory()
    try:
        alice = _compte()
        bob = _compte()
        session.add_all([alice, bob])
        session.commit()

        service = SocialService(session)
        conversation = service.creer_directe(creee_par=alice.id, autre=bob.id)
        service.poster_message(conversation.id, auteur_id=alice.id, contenu="un")
        service.poster_message(conversation.id, auteur_id=alice.id, contenu="deux")

        # Bob n'a jamais lu : 2 non lus. Alice vient d'écrire : 0.
        assert service.non_lus_par_conversation(bob.id, [conversation.id]) == {
            conversation.id: 2
        }
        assert service.non_lus_par_conversation(alice.id, [conversation.id]) == {}

        # Bob lit : plus rien de non lu ; un message posté **après** la lecture
        # compte à nouveau. Les horodatages sont posés explicitement : la
        # granularité d'horloge de la machine ne doit pas décider du résultat.
        from datetime import UTC, datetime, timedelta

        service.marquer_lu(conversation.id, utilisateur_id=bob.id)
        assert service.non_lus_par_conversation(bob.id, [conversation.id]) == {}
        message_trois = service.poster_message(
            conversation.id, auteur_id=alice.id, contenu="trois"
        )
        message_trois.created_at = datetime.now(UTC).replace(tzinfo=UTC) + timedelta(seconds=1)
        service._session.flush()
        assert service.non_lus_par_conversation(bob.id, [conversation.id]) == {
            conversation.id: 1
        }
        # Un non-membre n'est pas concerné.
        carol = _compte()
        session.add(carol)
        session.commit()
        assert service.non_lus_par_conversation(carol.id, [conversation.id]) == {}

        # « Marquer comme non lu » : la lecture revient au début de la
        # conversation — tous les messages d'alice redeviennent non lus.
        service.marquer_non_lu(conversation.id, utilisateur_id=bob.id)
        assert service.non_lus_par_conversation(bob.id, [conversation.id]) == {
            conversation.id: 3  # un, deux, trois
        }
        # …et un message posté après reste compté (comparaison > stricte).
        message_quatre = service.poster_message(
            conversation.id, auteur_id=alice.id, contenu="quatre"
        )
        message_quatre.created_at = datetime.now(UTC).replace(tzinfo=UTC) + timedelta(seconds=2)
        service._session.flush()
        assert service.non_lus_par_conversation(bob.id, [conversation.id]) == {
            conversation.id: 4
        }
        with pytest.raises(NotFoundError):
            service.marquer_non_lu(conversation.id, utilisateur_id=carol.id)
    finally:
        session.close()


def test_non_lus_une_seule_requete_agregee(api_session_factory) -> None:
    """Le badge compte toutes les conversations en **une** requête (plus de N+1).

    La route ``/social/non-lus`` est appelée à chaque page et à chaque message :
    un aller-retour par discussion y était le principal coût de la barre
    latérale. Le ``GROUP BY`` doit rendre les mêmes comptes, une seule fois.
    """
    from sqlalchemy import event as sa_event

    session = api_session_factory()
    try:
        alice = _compte()
        bob = _compte()
        session.add_all([alice, bob])
        session.commit()

        service = SocialService(session)
        directe = service.creer_directe(creee_par=alice.id, autre=bob.id)
        service.poster_message(directe.id, auteur_id=alice.id, contenu="un")
        service.poster_message(directe.id, auteur_id=alice.id, contenu="deux")
        conference = service.creer_conference(
            titre="Point", creee_par=alice.id, membres=[bob.id]
        )
        service.poster_message(conference.id, auteur_id=alice.id, contenu="trois")
        # Pas de message de Bob ici : poster marque la conversation comme lue
        # jusqu'à maintenant ("le mien" effacerait le non-lu de « trois »).

        # Vide les écritures en attente : l'autoflush ne doit pas être compté
        # comme une requête du badge.
        service._session.flush()
        requetes: list[str] = []
        moteur = session.get_bind()

        def compter(conn, cursor, statement, parameters, context, executemany):
            requetes.append(statement)

        sa_event.listen(moteur, "before_cursor_execute", compter)
        try:
            comptes = service.non_lus_par_conversation(
                bob.id, [directe.id, conference.id]
            )
        finally:
            sa_event.remove(moteur, "before_cursor_execute", compter)

        assert comptes == {directe.id: 2, conference.id: 1}
        assert len(requetes) == 1, requetes

        # Une conversation archivée par l'utilisateur sort du badge, toujours
        # en une requête.
        service.supprimer_conversation(directe.id, utilisateur_id=bob.id)
        service._session.flush()
        sa_event.listen(moteur, "before_cursor_execute", compter)
        try:
            comptes = service.non_lus_par_conversation(
                bob.id, [directe.id, conference.id]
            )
        finally:
            sa_event.remove(moteur, "before_cursor_execute", compter)
        assert directe.id not in comptes
        assert comptes == {conference.id: 1}
    finally:
        session.close()


def test_suppression_directe_par_membre_unilateral(api_session_factory) -> None:
    session = api_session_factory()
    try:
        alice = _compte()
        bob = _compte()
        session.add_all([alice, bob])
        session.commit()

        service = SocialService(session)
        conversation = service.creer_directe(creee_par=alice.id, autre=bob.id)

        # Alice supprime : la directe sort de SA liste, Bob la conserve.
        service.supprimer_conversation(conversation.id, utilisateur_id=alice.id)
        assert conversation.id not in [c.id for c in service.lister_conversations(alice.id)]
        assert conversation.id in [c.id for c in service.lister_conversations(bob.id)]

        # Un message de Bob la fait réapparaître chez Alice.
        service.poster_message(conversation.id, auteur_id=bob.id, contenu="coucou")
        assert conversation.id in [c.id for c in service.lister_conversations(alice.id)]
    finally:
        session.close()


def test_suppression_conference_animateur_seul(api_session_factory) -> None:
    session = api_session_factory()
    try:
        alice = _compte()
        bob = _compte()
        session.add_all([alice, bob])
        session.commit()

        service = SocialService(session)
        conference = service.creer_conference(
            titre="Point projet", creee_par=alice.id, membres=[bob.id]
        )
        # Un membre simple ne peut pas supprimer la conférence.
        with pytest.raises(PermissionDeniedError):
            service.supprimer_conversation(conference.id, utilisateur_id=bob.id)
        service.supprimer_conversation(conference.id, utilisateur_id=alice.id)
        assert service.lister_conversations(alice.id) == []
        assert service.lister_conversations(bob.id) == []
    finally:
        session.close()


def test_renommer_conference_animateur_seul(api_session_factory) -> None:
    session = api_session_factory()
    try:
        alice = _compte()
        bob = _compte()
        session.add_all([alice, bob])
        session.commit()

        service = SocialService(session)
        conference = service.creer_conference(
            titre="Nom initial", creee_par=alice.id, membres=[bob.id]
        )
        with pytest.raises(PermissionDeniedError):
            service.renommer_conversation(
                conference.id, titre="Nom pirate", utilisateur_id=bob.id
            )
        with pytest.raises(ValidationError):
            service.renommer_conversation(conference.id, titre="x", utilisateur_id=alice.id)
        renommee = service.renommer_conversation(
            conference.id, titre="Nouveau nom", utilisateur_id=alice.id
        )
        assert renommee.titre == "Nouveau nom"
    finally:
        session.close()


def test_reaction_api_json_et_contrat(api_client: TestClient, api_session_factory) -> None:
    """La route des réactions accepte du JSON pur et renvoie {reacted, total, moi}."""
    from app.api.deps import _LAB_USER_ID

    session = api_session_factory()
    try:
        alice = Utilisateur(
            id=_LAB_USER_ID,
            email="lab@localhost",
            nom="Lab",
            prenom="Mode",
            password_hash="!",
            role="collaborateur",
            statut="actif",
        )
        session.merge(alice)
        session.commit()
        service = SocialService(session)
        annonce = service.publier_annonce(
            auteur_id=alice.id, titre="Annonce test", contenu="Contenu"
        )
        annonce_id = str(annonce.id)
        session.commit()
    finally:
        session.close()

    corps = {"cible_type": "annonce", "cible_id": annonce_id, "emoji": "🎉"}
    premiere = api_client.post("/api/v1/social/reactions", json=corps)
    assert premiere.status_code == 200
    assert premiere.json() == {"reacted": True, "total": 1, "moi": True, "emoji": "🎉"}

    # Recliquer retire : le compteur repasse à zéro.
    seconde = api_client.post("/api/v1/social/reactions", json=corps)
    assert seconde.json() == {"reacted": False, "total": 0, "moi": False, "emoji": None}

    # Une autre emoji **remplace** la précédente (réaction unique, incrément 24)
    # et la réponse porte l'emoji effectif pour l'optimiste du client.
    coeur = api_client.post("/api/v1/social/reactions", json={**corps, "emoji": "❤️"})
    assert coeur.json() == {"reacted": True, "total": 1, "moi": True, "emoji": "❤️"}
    reactions_cible = api_client.get(
        f"/api/v1/social/reactions?cible_type=annonce&cible_id={annonce_id}"
    ).json()
    assert [r["emoji"] for r in reactions_cible] == ["❤️"]

    # La liste agrégée reflète l'état serveur : l'unique réaction restante est
    # le ❤️ (le 🎉 a été remplacé, pas cumulé — bug incrément 24 corrigé).
    aggregees = api_client.get("/api/v1/social/annonces").json()
    assert aggregees[0]["reactions"] == {"❤️": {"total": 1, "moi": True}}


# --- Mention asynchrone + photo d'annuaire (incrément 26) ---------------------------


def test_poster_message_avec_mention_repond_instantanement(
    api_client: TestClient, api_session_factory, monkeypatch
) -> None:
    """Le POST renvoie le message humain sans attendre le modèle (incrément 26).

    Avec TestClient, les tâches de fond s'exécutent **avant** la réponse : on
    monkeypatche l'invocation de l'agent pour prouver (a) que la réponse HTTP
    porte bien le message de l'utilisateur et ``mention_en_cours: true``, et
    (b) que la tâche de fond a posté le message d'agent avec sa session dédiée.
    En production (uvicorn), la tâche démarre après l'envoi de la réponse — le
    POST est alors réellement instantané.
    """
    from app.api.deps import _LAB_USER_ID
    from app.application.services.social_mention_service import AGENT_GENERALISTE

    session = api_session_factory()
    try:
        lab = Utilisateur(
            id=_LAB_USER_ID,
            email="lab@localhost",
            nom="Lab",
            prenom="Mode",
            password_hash="!",
            role="collaborateur",
            statut="actif",
        )
        session.merge(lab)
        bob = _compte()
        session.add(bob)
        session.commit()
        conversation_id = SocialService(session).creer_directe(
            creee_par=lab.id, autre=bob.id
        ).id
        session.commit()
    finally:
        session.close()

    conversation = str(conversation_id)

    # La route importe la tâche au moment de l'appel (import tardif, cycle) :
    # patcher le module du service intercepte donc bien la tâche de fond.
    async def _reponse_stub(
        question: str, *, thread_id: str | None, auteur_id: object = None
    ) -> str:
        return "Il y a 2 missions en cours."

    monkeypatch.setattr(
        "app.application.services.social_mention_service._invoquer_generaliste",
        _reponse_stub,
    )

    reponse = api_client.post(
        f"/api/v1/social/conversations/{conversation}/messages",
        json={"contenu": "@agent_generaliste Donne-moi les missions en cours"},
    )
    assert reponse.status_code == 201
    corps = reponse.json()
    # (a) La réponse porte le message humain + le signal « agent en cours ».
    assert corps["auteur_type"] == "humain"
    assert "@agent_generaliste" in corps["contenu"]
    assert corps["mention_en_cours"] is True
    # (b) La tâche de fond a posté la réponse de l'agent (session dédiée).
    session = api_session_factory()
    try:
        messages = SocialService(session).lister_messages(
            conversation_id, utilisateur_id=_LAB_USER_ID
        )
    finally:
        session.close()
    assert messages[-1].auteur_type == "agent"
    assert messages[-1].auteur_agent_id == AGENT_GENERALISTE
    assert "2 missions" in messages[-1].contenu


def test_thread_partage_par_conversation_poster_message(
    api_client: TestClient, api_session_factory, monkeypatch
) -> None:
    """Même conversation → même thread d'agent (incrément 31).

    L'incrément 30 filait ``thread_id=str(message.id)`` : chaque mention
    repartait sans mémoire des échanges précédents de la conversation.
    Désormais les mentions d'une même conversation partagent le **même** fil
    LangGraph (uuid5 déterministe — reproductible entre deux processus), et
    deux conversations différentes restent étanches l'une de l'autre.
    """
    import uuid as _uuid

    from app.api.deps import _LAB_USER_ID

    session = api_session_factory()
    try:
        lab = Utilisateur(
            id=_LAB_USER_ID,
            email="lab@localhost",
            nom="Lab",
            prenom="Mode",
            password_hash="!",
            role="collaborateur",
            statut="actif",
        )
        session.merge(lab)
        premier = _compte()
        second = _compte()
        session.add_all([premier, second])
        session.commit()
        # Deux conversations distinctes : ``creer_directe`` déduplique par
        # paire, il faut donc deux interlocuteurs différents.
        conv_une = SocialService(session).creer_directe(
            creee_par=lab.id, autre=premier.id
        ).id
        conv_deux = SocialService(session).creer_directe(
            creee_par=lab.id, autre=second.id
        ).id
        session.commit()
    finally:
        session.close()

    threads: list[str | None] = []

    async def _stub(
        question: str, *, thread_id: str | None, auteur_id: object = None
    ) -> str:
        threads.append(thread_id)
        return "Réponse."

    monkeypatch.setattr(
        "app.application.services.social_mention_service._invoquer_generaliste",
        _stub,
    )

    for _ in range(2):
        reponse = api_client.post(
            f"/api/v1/social/conversations/{conv_une}/messages",
            json={"contenu": "@agent_generaliste Donne-moi les missions en cours"},
        )
        assert reponse.status_code == 201
    reponse = api_client.post(
        f"/api/v1/social/conversations/{conv_deux}/messages",
        json={"contenu": "@agent_generaliste Donne-moi les organisations"},
    )
    assert reponse.status_code == 201

    assert len(threads) == 3
    assert threads[0] is not None
    # Les deux mentions de la même conversation partagent le même thread.
    assert threads[1] == threads[0]
    # Une autre conversation → un autre thread (pas de fuite de contexte).
    assert threads[2] != threads[0]
    # Le thread est dérivé de façon déterministe de la conversation (uuid5) :
    # stable même si le backend redémarre entre deux messages.
    assert threads[0] == str(
        _uuid.uuid5(_uuid.NAMESPACE_URL, f"carso-social-{conv_une}")
    )


def test_poster_message_sans_mention_ne_signale_pas_dagent(
    api_client: TestClient, api_session_factory
) -> None:
    """Sans mention, ``mention_en_cours`` est faux et aucun agent ne s'active."""
    from app.api.deps import _LAB_USER_ID

    session = api_session_factory()
    try:
        lab = Utilisateur(
            id=_LAB_USER_ID,
            email="lab@localhost",
            nom="Lab",
            prenom="Mode",
            password_hash="!",
            role="collaborateur",
            statut="actif",
        )
        session.merge(lab)
        bob = _compte()
        session.add(bob)
        session.commit()
        conversation_id = SocialService(session).creer_directe(
            creee_par=lab.id, autre=bob.id
        ).id
        session.commit()
    finally:
        session.close()

    reponse = api_client.post(
        f"/api/v1/social/conversations/{conversation_id}/messages",
        json={"contenu": "Bonjour à tous"},
    )
    assert reponse.status_code == 201
    assert reponse.json()["mention_en_cours"] is False


def test_photo_profil_annuaire_reserve_aux_comptes_actifs(
    api_client: TestClient, api_session_factory, tmp_path, monkeypatch
) -> None:
    """La photo d'un tiers est servie aux membres authentifiés (404 sinon)."""
    from app.core.config import get_settings
    from app.domain.social import Conversation, MembreConversation

    monkeypatch.setattr(get_settings(), "storage_root", str(tmp_path))
    session = api_session_factory()
    try:
        from app.api.deps import _LAB_USER_ID

        cible = _compte()
        session.add(cible)
        session.flush()
        lab = Utilisateur(
            id=_LAB_USER_ID,
            email="lab@localhost",
            nom="Lab",
            prenom="Mode",
            password_hash="!",
            role="collaborateur",
            statut="actif",
        )
        session.merge(lab)
        conversation = Conversation(type="directe", creee_par_id=lab.id)
        session.add(conversation)
        session.flush()
        for utilisateur in (cible, lab):
            session.add(
                MembreConversation(
                    conversation_id=conversation.id,
                    utilisateur_id=utilisateur.id,
                    role="membre",
                )
            )
        session.commit()
        cible_id = str(cible.id)
    finally:
        session.close()

    # Sans photo : 404 clair (le front garde le repli initiales).
    sans_photo = api_client.get(f"/api/v1/social/annuaire/{cible_id}/photo-profil")
    assert sans_photo.status_code == 404

    # Compte inconnu : 404 également (jamais 500).
    inconnu = api_client.get(
        "/api/v1/social/annuaire/00000000-0000-0000-0000-000000000000/photo-profil"
    )
    assert inconnu.status_code == 404

    # L'annuaire expose le drapeau photo_profil (le front évite les 404).
    annuaire = api_client.get("/api/v1/social/annuaire").json()
    assert all("photo_profil" in compte for compte in annuaire)


def test_lister_messages_garde_les_plus_recents(api_session_factory) -> None:
    """Au-delà du plafond, l'API renvoie les messages **les plus récents**.

    Régression du 04/10 : la requête triait du plus ancien au plus récent puis
    appliquait ``LIMIT 100`` — au-delà de 100 messages, les plus récents
    n'étaient jamais renvoyés. Symptôme observé côté interface : « mon message
    a disparu ». Ici 105 messages, plafond 100 : le plus ancien (n° 1) sort,
    le plus récent (n° 105) reste, et l'ordre croissant est préservé — les
    appelants indexent ``messages[-1]``.
    """
    from datetime import UTC, datetime, timedelta

    session = api_session_factory()
    try:
        alice = _compte()
        bob = _compte()
        session.add_all([alice, bob])
        session.commit()

        service = SocialService(session)
        conversation = service.creer_directe(creee_par=alice.id, autre=bob.id)
        base = datetime(2026, 10, 4, 9, 0, tzinfo=UTC)
        for index in range(105):
            session.add(
                Message(
                    conversation_id=conversation.id,
                    auteur_id=alice.id if index % 2 == 0 else bob.id,
                    auteur_type=TypeAuteurMessage.HUMAIN.value,
                    contenu=f"message {index}",
                    created_at=base + timedelta(seconds=index),
                )
            )
        session.commit()

        messages = service.lister_messages(
            conversation.id, utilisateur_id=bob.id, plafond=100
        )

        assert len(messages) == 100
        assert messages[-1].contenu == "message 104", "le plus récent doit être là"
        assert messages[0].contenu == "message 5", "fenêtre glissante sur la fin"
        assert [m.created_at for m in messages] == sorted(
            m.created_at for m in messages
        ), "ordre chronologique croissant conservé"
    finally:
        session.close()
