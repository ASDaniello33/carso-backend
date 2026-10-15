"""Présence et mention d'agent (chat social) — incrément 21.

Le statut de présence est calculé (jamais stocké) ; la mention
``@agent_generaliste`` poste une réponse d'agent sans jamais faire échouer le
message humain (le généraliste est lecture seule).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock
from uuid import uuid4

from fastapi.testclient import TestClient

from app.application.services.presence_chat_service import (
    PresenceChatService,
    statut_de,
)
from app.application.services.social_mention_service import (
    _DELAI_MAX_INVOCATION_S,
    _MESSAGE_DELAI_DEPASSE,
    _MESSAGE_ECHEC,
    detecter_mention_generaliste,
    repondre_mention,
)
from app.application.services.social_service import SocialService
from app.domain.identity import Utilisateur


def _compte() -> Utilisateur:
    return Utilisateur(
        email=f"{uuid4().hex[:8]}@carso.mg",
        nom="Test",
        prenom="User",
        password_hash="x",
        role="collaborateur",
        statut="actif",
    )


# --- Présence ---------------------------------------------------------------------


def test_statut_calcule_depuis_derniere_activite() -> None:
    maintenant = datetime.now(UTC)
    assert statut_de(None) == "hors_ligne"
    assert statut_de(maintenant - timedelta(seconds=30), maintenant=maintenant) == "en_ligne"
    assert statut_de(maintenant - timedelta(minutes=3), maintenant=maintenant) == "absent"
    assert statut_de(maintenant - timedelta(hours=2), maintenant=maintenant) == "hors_ligne"


def test_heartbeat_et_libelle_relatif(api_session_factory) -> None:
    session = api_session_factory()
    try:
        utilisateur = _compte()
        session.add(utilisateur)
        session.commit()

        service = PresenceChatService(session)
        service.battement(utilisateur.id)
        resume = service.statut(utilisateur.id)
        assert resume["statut"] == "en_ligne"
        assert resume["libelle"] == "En ligne"

        # Activité vieillie artificiellement : "En ligne il y a N min".
        utilisateur.derniere_activite_at = datetime.now(UTC) - timedelta(minutes=5)
        session.flush()
        resume = service.statut(utilisateur.id)
        assert resume["statut"] == "absent"
        assert resume["libelle"] == "En ligne il y a 5 min"

        statuts = service.statuts_pour([utilisateur.id])
        assert statuts[str(utilisateur.id)]["statut"] == "absent"
    finally:
        session.close()


def test_route_presence_battement_en_mode_lab(api_client: TestClient) -> None:
    reponse = api_client.post("/api/v1/social/presence/battement")
    assert reponse.status_code == 204


# --- Mention d'agent ---------------------------------------------------------------


def test_detection_mention_generaliste() -> None:
    assert detecter_mention_generaliste("@agent_generaliste Donne les missions en cours")
    assert detecter_mention_generaliste("@Agent Généraliste, que sais-tu ?")
    assert detecter_mention_generaliste("coucou @AGENT_GENERALISTE")
    assert not detecter_mention_generaliste("Bonjour @bob, une question ?")
    assert not detecter_mention_generaliste("@agent_rh propose une affectation")


def test_mention_poste_une_reponse_dagent_meme_si_le_modele_echoue(
    api_session_factory, monkeypatch
) -> None:
    """Le message humain existe toujours ; l'échec reste lisible, sans cause inventée.

    Le message d'échec ne nommait pas non plus sa cause : il affirmait « le
    modèle est indisponible ou mal configuré » alors que la panne pouvait venir
    d'un outil — lu comme « l'agent ne répond plus » (constat 04/10). Il est
    désormais neutre, et figé ici mot pour mot.
    """
    session = api_session_factory()
    try:
        alice = _compte()
        bob = _compte()
        session.add_all([alice, bob])
        session.commit()
        social = SocialService(session)
        conversation = social.creer_directe(creee_par=alice.id, autre=bob.id)
        conversation_id = conversation.id
        session.commit()

        async def _crash(question: str, *, thread_id: str | None) -> str:
            raise RuntimeError("modèle hors service")

        monkeypatch.setattr(
            "app.application.services.social_mention_service._invoquer_generaliste",
            _crash,
        )
        resultat = asyncio.run(
            repondre_mention(
                session,
                conversation_id=conversation_id,
                message_contenu="@agent_generaliste Que peux-tu me dire ?",
            )
        )
        assert resultat is not None
        messages = social.lister_messages(conversation_id, utilisateur_id=alice.id)
        assert len(messages) == 1
        assert messages[0].auteur_type == "agent"
        assert messages[0].auteur_agent_id == "agent_generaliste_readonly"
        assert messages[0].contenu == _MESSAGE_ECHEC
    finally:
        session.close()


def test_mention_sans_mention_ne_repond_pas(api_session_factory) -> None:
    session = api_session_factory()
    try:
        alice = _compte()
        bob = _compte()
        session.add_all([alice, bob])
        session.commit()
        social = SocialService(session)
        conversation = social.creer_directe(creee_par=alice.id, autre=bob.id)
        assert (
            asyncio.run(
                repondre_mention(
                    session,
                    conversation_id=conversation.id,
                    message_contenu="Bonjour à tous",
                )
            )
            is None
        )
    finally:
        session.close()


def test_mention_invoque_le_graphe_et_poste_la_reponse(
    api_session_factory, monkeypatch
) -> None:
    """Chemin nominal : le graphe répond, la réponse est postée comme message agent."""
    session = api_session_factory()
    try:
        alice = _compte()
        bob = _compte()
        session.add_all([alice, bob])
        session.commit()
        social = SocialService(session)
        conversation = social.creer_directe(creee_par=alice.id, autre=bob.id)
        conversation_id = conversation.id
        session.commit()

        async def _nominal(
            question: str, *, thread_id: str | None, auteur_id: object = None
        ) -> str:
            assert "outils d'affichage" in question  # consigne fil social
            return "Il y a 2 missions en cours."

        monkeypatch.setattr(
            "app.application.services.social_mention_service._invoquer_generaliste",
            _nominal,
        )
        resultat = asyncio.run(
            repondre_mention(
                session,
                conversation_id=conversation_id,
                message_contenu="@agent_generaliste Donne-moi les missions en cours",
            )
        )
        assert resultat is not None
        messages = social.lister_messages(conversation_id, utilisateur_id=alice.id)
        assert messages[0].auteur_type == "agent"
        assert "2 missions" in messages[0].contenu
    finally:
        session.close()


def test_iterer_reponse_ne_poste_pas_un_resultat_doutil(
    api_session_factory, monkeypatch
) -> None:
    """Le JSON d'un ToolMessage n'est jamais la réponse ; la synthèse finale gagne.

    Incrément 30 : l'ancienne logique (croissance monotone sur n'importe quel
    message) postait le JSON brut du tool et **jetait** la synthèse finale
    (plus courte). Ici : les ToolMessages sont ignorés, le dernier message IA
    non vide fait foi — même quand il est plus court que l'outil.
    """
    from langchain_core.messages import AIMessage, ToolMessage

    import app.application.services.social_mention_service as _module

    class _Graphe:
        async def astream(self, entree: dict, config: dict, stream_mode: str) -> object:
            yield {"model": {"messages": [AIMessage(content="")]}}
            yield {
                "tools": {
                    "messages": [
                        ToolMessage(
                            tool_call_id="t1",
                            content='{"ok": true, "organisations": ["FAO"]}',
                        )
                    ]
                }
            }
            yield {
                "model": {
                    "messages": [
                        AIMessage(content="Voici la synthèse : 3 organisations.")
                    ]
                }
            }

    class _Manager:
        def graph(self, agent_id: str, utilisateur_id: object = None) -> object:
            assert agent_id == "agent_generaliste_readonly"
            return _Graphe()

    # Le graphe est résolu via l'import local ``from app.agents.manager import
    # get_runtime_manager`` : patcher la SOURCE intercepte bien l'appel.
    monkeypatch.setattr(
        "app.agents.manager.get_runtime_manager",
        lambda: _Manager(),
    )

    async def _recuperer() -> str:
        morceaux = [
            delta
            async for delta in _module._iterer_reponse(
                "Liste les organisations", thread_id="test"
            )
        ]
        return "".join(morceaux)

    assert asyncio.run(_recuperer()) == "Voici la synthèse : 3 organisations."


def _espionner_avertissements(monkeypatch) -> MagicMock:
    """Espionne ``logger.warning`` **au niveau de l'appel**.


    Le test ne dépend pas de la plomberie de capture : l'application remplace
    les handlers de la racine (``configure_logging``), ce qui rend ``caplog``
    dépendant de l'ordre de la suite — deux tests passaient isolés et
    échouaient en suite complète (constat 04/10). L'invariant vérifié ici est
    celui du service : **il rapporte la cause**, avec le type d'erreur.
    """
    import app.application.services.social_mention_service as module_service

    espion: MagicMock = MagicMock()
    monkeypatch.setattr(module_service.logger, "warning", espion)
    return espion


def test_mention_garde_la_reponse_deja_ecrite_et_trace_l_echec(
    api_session_factory, monkeypatch
) -> None:
    """Un run qui échoue **après** avoir répondu ne jette plus sa réponse.

    Le service rendait le message d'échec à la place du texte déjà produit :
    l'utilisateur perdait une réponse que l'agent avait écrite (constat 04/10),
    et l'échec partait sans aucune trace — donc indiagnosticable.
    """
    import app.application.services.social_mention_service as _module

    session = api_session_factory()
    try:
        alice = _compte()
        bob = _compte()
        session.add_all([alice, bob])
        session.commit()
        social = SocialService(session)
        conversation = social.creer_directe(creee_par=alice.id, autre=bob.id)
        conversation_id = conversation.id
        session.commit()

        async def _repond_puis_casse(
            question: str, *, thread_id: str | None = None, auteur_id: object = None
        ):
            yield "Il y a actuellement 3 missions en cours."
            raise RuntimeError("outil distant injoignable")

        monkeypatch.setattr(_module, "_iterer_reponse", _repond_puis_casse)
        espion = _espionner_avertissements(monkeypatch)
        resultat = asyncio.run(
            repondre_mention(
                session,
                conversation_id=conversation_id,
                message_contenu="@agent_generaliste Combien de missions ?",
            )
        )

        assert resultat is not None
        contenu = str(resultat["contenu"])
        assert contenu.startswith("Il y a actuellement 3 missions en cours.")
        assert _MESSAGE_ECHEC in contenu
        # La cause est rapportée avec le type d'erreur, jamais supposée.
        assert any(
            appel.args[0] == "mention sociale : invocation en échec (%s)"
            and "RuntimeError" in appel.args
            for appel in espion.call_args_list
        ), espion.call_args_list
    finally:
        session.close()


def test_mention_borne_la_duree_de_l_invocation(monkeypatch) -> None:
    """Un run suspendu est interrompu : le fil ne reste pas sans réponse.

    Mesuré le 04/10 : une question simple a demandé 2 min 07. La borne existait
    nulle part — un run bloqué gardait sa session et l'indicateur « l'agent
    écrit… » du fil ouverts sans fin.
    """
    import app.application.services.social_mention_service as _module

    async def _trop_long(
        question: str, *, thread_id: str | None = None, auteur_id: object = None
    ):
        await asyncio.sleep(5)
        yield "jamais atteint"

    monkeypatch.setattr(_module, "_iterer_reponse", _trop_long)
    monkeypatch.setattr(_module, "_DELAI_MAX_INVOCATION_S", 0.05)
    espion = _espionner_avertissements(monkeypatch)

    texte = asyncio.run(
        _module._invoquer_generaliste("Combien de missions ?", thread_id=None)
    )

    assert texte == _MESSAGE_DELAI_DEPASSE
    assert any(
        "invocation interrompue" in appel.args[0] for appel in espion.call_args_list
    ), espion.call_args_list
    # La borne de production reste au-dessus du run légitime le plus long mesuré
    # (127 s) : elle protège d'un blocage, elle ne coupe pas un travail normal.
    assert _DELAI_MAX_INVOCATION_S > 127
    assert _DELAI_MAX_INVOCATION_S == 180.0
