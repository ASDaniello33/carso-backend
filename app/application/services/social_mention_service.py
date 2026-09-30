"""Mention d'agent dans le chat social (@agent_generaliste) — incrément 21.

Décision validée : **seul l'agent généraliste lecture seule** peut être
invoqué dans une conversation sociale — aucune écriture métier n'est possible
par ce canal, conformément au contrat de l'agent (§1.8 AGENTS.md).

Flux (incrément 26 — mention **asynchrone**) : un membre écrit un message
contenant ``@agent_generaliste`` (ou ``@Agent Généraliste``) → le message
humain est posté et **la réponse HTTP part immédiatement** → la mention est
traitée en tâche de fond (``BackgroundTasks``) : le graphe du généraliste est
invoqué en **streaming token par token** (``graph.stream``), le texte s'accumule,
et le message d'agent est posté **une seule fois à la fin** — le fil social
affiche des messages complets, l'attente perçue est réduite (premier token)
et le front affiche l'indicateur « l'agent écrit… » pendant l'invocation.
La mention est traitée hors transaction HTTP (best-effort émis après la
réponse) : un échec de modèle n'annule jamais le message de l'utilisateur, et
une erreur est postée comme message système lisible.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator, Callable
from typing import Any

from sqlalchemy.orm import Session

from app.application.services.social_service import SocialService

#: Seul agent invocable (décision validée — lecture seule).
AGENT_GENERALISTE = "agent_generaliste_readonly"
#: Alias acceptés dans un message (insensible à la casse/accents via \w+).
_MENTION = re.compile(
    r"@agent[_ ]?g[ée]n[ée]raliste\b|@agent[_ ]?generaliste[_ ]?readonly\b",
    re.IGNORECASE,
)

#: Message lisible si le modèle est indisponible (jamais d'exception vers le chat).
_MESSAGE_INDISPONIBLE = (
    "Je n'ai pas pu traiter votre demande (le modèle est indisponible ou "
    "mal configuré). Réessayez plus tard — votre message a bien été envoyé."
)

#: Consigne ajoutée à la question quand l'agent est invoqué **depuis le chat
#: social** : les outils d'affichage y sont invisibles (pas de panneau AG-UI
#: pour les rendre) et ``poser_questionnaire`` serait interrompu sans jamais
#: être repris (aucun répondeur HITL dans le fil). L'agent répond donc en
#: texte — les tool calls **backend** (recherches, lecture, agrégats, web)
#: restent pleinement disponibles.
_CONSIGNE_CHAT_SOCIAL = (
    "(Contexte : tu réponds dans un fil de discussion, pas dans le panneau "
    "agent. Réponds uniquement en texte clair et concis ; n'appelle pas les "
    "outils d'affichage ni poser_questionnaire — ils ne sont pas disponibles "
    "ici. Tes outils de recherche et de lecture restent utilisables.)"
)


def detecter_mention_generaliste(contenu: str) -> bool:
    """Vrai si le message invoque l'agent généraliste."""
    return bool(_MENTION.search(contenu or ""))


async def repondre_mention_en_tache_de_fond(
    *,
    conversation_id: Any,
    message_contenu: str,
    thread_id: str | None = None,
    session_factory: Callable[[], Session] | None = None,
) -> None:
    """Traite une mention d'agent **hors requête** (BackgroundTasks asynchrone).

    Rouvre une session SQLAlchemy dédiée (la session de la requête est fermée
    au retour HTTP), invoque l'agent en streaming et poste le message d'agent.
    La tâche est **asynchrone** (FastAPI exécute une coroutine de fond dans la
    boucle) car le graphe embarque des outils distants **async-only**
    (connecteurs MCP) : ``graph.stream`` synchrone levait
    ``NotImplementedError: StructuredTool does not support sync invocation``
    dès que le modèle appelait l'un d'eux — d'où le faux « modèle
    indisponible ». ``astream`` les exécute normalement.
    Toute exception est absorbée en message lisible — le chat social ne doit
    jamais échouer à cause du modèle. ``session_factory`` permet d'injecter la
    fabrique de test (la fabrique de production est ``SessionLocal``).
    """
    from app.infrastructure.database import SessionLocal

    if not detecter_mention_generaliste(message_contenu):
        return
    session = (session_factory or SessionLocal)()
    try:
        await repondre_mention(
            session,
            conversation_id=conversation_id,
            message_contenu=message_contenu,
            thread_id=thread_id,
        )
        # Pas de dépendance FastAPI ici : la transaction de la tâche est
        # validée explicitement (message d'agent ou message d'erreur).
        session.commit()
    finally:
        session.close()


async def repondre_mention(
    session: Session,
    *,
    conversation_id: Any,
    message_contenu: str,
    thread_id: str | None = None,
) -> dict[str, object] | None:
    """Invoque l'agent généraliste et poste sa réponse dans la conversation.

    Returns:
        Le message d'agent créé, ou ``None`` si aucune mention n'a été détectée.
        Une erreur d'invocation est postée comme message lisible (le chat
        social ne doit jamais échouer à cause du modèle).
    """
    if not detecter_mention_generaliste(message_contenu):
        return None

    question = _MENTION.sub("", message_contenu).strip() or "Que peux-tu me dire ?"
    # Contexte fil social : pas d'outils d'affichage ni de HITL (voir
    # ``_CONSIGNE_CHAT_SOCIAL``) — les tool calls backend restent permis.
    question = f"{question}\n\n{_CONSIGNE_CHAT_SOCIAL}"
    # Référence module : l'appel passe par le module, donc testable par
    # monkeypatch (et l'échec du modèle est converti en message lisible).
    import app.application.services.social_mention_service as _module

    try:
        reponse_texte = await _module._invoquer_generaliste(question, thread_id=thread_id)
    except Exception:  # noqa: BLE001 - le chat ne doit pas échouer
        reponse_texte = _MESSAGE_INDISPONIBLE
    social = SocialService(session)
    message = social.poster_message_agent(
        conversation_id,
        agent_id=AGENT_GENERALISTE,
        nom_agent="Agent Généraliste",
        contenu=reponse_texte,
    )
    # Le commit appartient à l'appelant (dépendance FastAPI pour la requête,
    # commit explicite dans la tâche de fond) — cohérent avec les autres services.
    return {"message_id": str(message.id), "contenu": reponse_texte}


async def _iterer_reponse(question: str, *, thread_id: str | None) -> AsyncIterator[str]:
    """Streaming (async) du graphe du généraliste — **messages IA seuls**.

    Deux règles, chacune corrige un défaut constaté :

    1. ``astream`` (et non ``stream``) : le graphe embarque des outils
       distants **async-only** (connecteurs MCP) — l'invocation synchrone
       levait ``NotImplementedError: StructuredTool does not support sync
       invocation`` au premier tool call sur l'un d'eux.
    2. Seul le **dernier message IA non vide** fait foi. L'ancienne logique
       (croissance monotone sur n'importe quel ``messages[-1]``) prenait les
       ``ToolMessage`` pour la réponse : le JSON brut d'un outil était posté
       tel quel, et la synthèse finale (plus courte) était **rejetée**. Les
       résultats d'outils ne sont jamais des messages d'agent.

    Lève toute exception du modèle — l'appelant convertit en message lisible.
    """
    from langchain_core.messages import AIMessage

    from app.agents.manager import get_runtime_manager

    manager = get_runtime_manager()
    graphe = manager.graph(AGENT_GENERALISTE)
    config: dict[str, Any] = {}
    if thread_id:
        config["configurable"] = {"thread_id": thread_id}
    texte = ""
    async for mise_a_jour in graphe.astream(
        {"messages": [{"role": "user", "content": question}]},
        config=config,
        stream_mode="updates",
    ):
        messages = None
        if isinstance(mise_a_jour, dict):
            for valeur in mise_a_jour.values():
                if isinstance(valeur, dict) and valeur.get("messages"):
                    messages = valeur["messages"]
                    break
        if not messages:
            continue
        dernier = messages[-1]
        if not isinstance(dernier, AIMessage):
            continue  # ToolMessage / SystemMessage : jamais affichés.
        contenu = getattr(dernier, "content", None)
        if contenu is None:
            continue
        nouveau = str(contenu)
        if nouveau.strip():
            texte = nouveau  # Dernier message IA non vide = réponse finale.
    yield texte


async def _invoquer_generaliste(question: str, *, thread_id: str | None) -> str:
    """Réponse complète du généraliste — streaming asynchrone consommé.

    Le graphe est invoqué via ``_iterer_reponse`` (``astream``) : le graphe
    tourne dans la boucle de la tâche de fond et le **dernier message IA non
    vide** est posté d'un bloc dans la conversation (jamais un résultat
    d'outil brut). Un échec du modèle est converti en message lisible.
    """
    import app.application.services.social_mention_service as _module

    try:
        morceaux: list[str] = []
        async for delta in _module._iterer_reponse(question, thread_id=thread_id):
            morceaux.append(delta)
    except Exception:  # noqa: BLE001 - le chat ne doit pas échouer
        return _MESSAGE_INDISPONIBLE
    texte = "".join(morceaux).strip()
    return texte or "Je n'ai pas de réponse à proposer pour cette demande."


__all__ = [
    "AGENT_GENERALISTE",
    "detecter_mention_generaliste",
    "repondre_mention",
    "repondre_mention_en_tache_de_fond",
]
