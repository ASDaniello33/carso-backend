"""Mention d'agent dans le chat social (@agent_generaliste)—incrément 21.

Décision validée—**seul l'agent généraliste lecture seule** peut être
invoqué dans une conversation sociale—aucune écriture métier n'est possible
par ce canal, conformément au contrat de l'agent (§1.8 AGENTS.md).

Flux (incrément 26—mention **asynchrone**)—un membre écrit un message
contenant ``@agent_generaliste`` (ou ``@Agent Généraliste``) → le message
humain est posté et **la réponse HTTP part immédiatement** → la mention est
traitée en tâche de fond (``BackgroundTasks``)—le graphe du généraliste est
invoqué en **streaming token par token** (``graph.stream``), le texte s'accumule,
et le message d'agent est posté **une seule fois à la fin**—le fil social
affiche des messages complets, l'attente perçue est réduite (premier token)
et le front affiche l'indicateur « l'agent écrit… » pendant l'invocation.
La mention est traitée hors transaction HTTP (best-effort émis après la
réponse)—un échec de modèle n'annule jamais le message de l'utilisateur, et
une erreur est postée comme message système lisible.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import AsyncIterator, Callable
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.application.services.social_service import SocialService

logger = logging.getLogger(__name__)

#: Seul agent invocable (décision validée — lecture seule).
AGENT_GENERALISTE = "agent_generaliste_readonly"
#: Alias acceptés dans un message (insensible à la casse/accents via \w+).
_MENTION = re.compile(
    r"@agent[_ ]?g[ée]n[ée]raliste\b|@agent[_ ]?generaliste[_ ]?readonly\b",
    re.IGNORECASE,
)

#: Échec technique de l'invocation (jamais d'exception vers le chat).
#: Le texte ne nomme **aucune cause** : l'ancien message affirmait « le modèle
#: est indisponible ou mal configuré », ce que la tâche ignorait — il a été lu
#: comme « l'agent ne répond plus » (constat 04/10) alors que la panne venait
#: d'un outil. La cause réelle part dans les journaux, pas dans une supposition.
_MESSAGE_ECHEC = (
    "Je n'ai pas pu terminer cette demande (incident technique côté agent). "
    "Réessayez : votre message a bien été envoyé."
)

#: Invocation interrompue : le run a dépassé la borne (voir _DELAI_MAX_INVOCATION_S).
_MESSAGE_DELAI_DEPASSE = (
    "La demande a dépassé le temps de traitement autorisé et a été interrompue. "
    "Reformulez-la plus courte, ou réessayez : votre message a bien été envoyé."
)

#: Aucun message IA : le graphe a tourné sans rien dire (jamais un blanc).
_MESSAGE_SANS_REPONSE = "Je n'ai pas de réponse à proposer pour cette demande."

#: Borne d'une invocation, en secondes.
#: Mesuré le 04/10 : une question simple (« Combien de missions en cours ? ») a
#: demandé **2 min 07** — deux appels modèle et des outils distants. La borne est
#: volontairement au-dessus : elle n'arrête pas un run légitime, elle empêche un
#: run suspendu de garder indéfiniment sa session **et** l'indicateur du fil.
_DELAI_MAX_INVOCATION_S = 180.0

#: Consigne ajoutée à la question quand l'agent est invoqué **depuis le chat
#: social** : les outils d'affichage y sont invisibles (pas de panneau AG-UI
#: pour les rendre) et ``poser_questionnaire`` serait interrompu sans jamais
#: être repris (aucun répondeur HITL dans le fil). L'agent répond donc en
#: texte — les tool calls **backend** (recherches, lecture, agrégats, web)
#: restent pleinement disponibles.
_CONSIGNE_CHAT_SOCIAL = (
    "(Contexte : tu réponds dans un fil de discussion, pas dans le panneau "
    "agent. Réponds uniquement en texte clair et concis ; n'appelle pas les "
    "outils d'affichage ni poser_questionnaire : ils ne sont pas disponibles "
    "ici. Tes outils de recherche et de lecture restent utilisables.)"
)


def detecter_mention_generaliste(contenu: str) -> bool:
    """Vrai si le message invoque l'agent généraliste."""
    return bool(_MENTION.search(contenu or ""))


def _poser_perimetre_auteur(session: Session, auteur_id: Any) -> tuple[Any, Any, Any]:
    """Pose le périmètre, le demandeur **et le contexte** de l'auteur d'une mention.

    L'agent généraliste lit les mêmes services que l'API : il doit donc hériter
    du même filtrage. Un auteur introuvable reçoit un périmètre restreint vide
    (refus par défaut — on ne présume jamais d'un droit).

    Le contexte d'exécution (identité, rôle, profil, surface d'outils) est posé
    dans le même mouvement que le périmètre : un agent invoqué par mention
    sociale n'a pas de requête HTTP à lui, mais il a un auteur — et il doit
    savoir qui il est, comme dans le chat AG-UI.

    Returns:
        Les jetons de restauration (périmètre, demandeur, contexte) à rendre en
        ``finally``.
    """
    from app.agents.contexte import UserContext, poser_contexte_utilisateur
    from app.application.perimetre import PERIMETRE_VIDE, poser_perimetre, pour_utilisateur
    from app.application.trace import poser_demande_par
    from app.infrastructure.repositories import UtilisateurRepository

    utilisateur = None
    if auteur_id is not None:
        try:
            utilisateur = UtilisateurRepository(session).get(UUID(str(auteur_id)))
        except (ValueError, TypeError):
            utilisateur = None
    perimetre = pour_utilisateur(session, utilisateur) if utilisateur else PERIMETRE_VIDE
    if utilisateur is not None:
        from app.agents.contexte import depuis as contexte_depuis

        contexte = contexte_depuis(session, utilisateur, perimetre=perimetre)
    else:
        # Auteur inconnu : contexte restreint sans le moindre droit (on ne
        # présume jamais — même règle que le périmètre vide).
        contexte = UserContext(est_interne=False)
    return (
        poser_perimetre(perimetre),
        poser_demande_par(auteur_id if utilisateur else None),
        poser_contexte_utilisateur(contexte),
    )


async def repondre_mention_en_tache_de_fond(
    *,
    conversation_id: Any,
    message_contenu: str,
    thread_id: str | None = None,
    auteur_id: Any | None = None,
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

    ``auteur_id`` est l'auteur humain de la mention : son **périmètre** est posé
    pour l'invocation de l'agent (il ne lit que ses missions, comme dans l'API)
    et il est inscrit dans l'audit des tools qu'il déclenche ("qui fait quoi").
    Sans ``auteur_id`` — appel direct de :func:`repondre_mention` par un test ou
    un script — le contexte ambiant est laissé tel quel : on n'invente pas de
    demandeur.
    """
    from app.agents.contexte import retablir_contexte_utilisateur
    from app.application.perimetre import retablir_perimetre
    from app.application.trace import retablir_demande_par
    from app.infrastructure.database import SessionLocal

    if not detecter_mention_generaliste(message_contenu):
        return
    session = (session_factory or SessionLocal)()
    jeton_perimetre = jeton_demandeur = jeton_contexte = None
    try:
        if auteur_id is not None:
            jeton_perimetre, jeton_demandeur, jeton_contexte = _poser_perimetre_auteur(
                session, auteur_id
            )
        await repondre_mention(
            session,
            auteur_id=auteur_id,
            conversation_id=conversation_id,
            message_contenu=message_contenu,
            thread_id=thread_id,
        )
        # Pas de dépendance FastAPI ici : la transaction de la tâche est
        # validée explicitement (message d'agent ou message d'erreur).
        session.commit()
    finally:
        if jeton_contexte is not None:
            retablir_contexte_utilisateur(jeton_contexte)
        if jeton_demandeur is not None:
            retablir_demande_par(jeton_demandeur)
        if jeton_perimetre is not None:
            retablir_perimetre(jeton_perimetre)
        session.close()


async def repondre_mention(
    session: Session,
    *,
    conversation_id: Any,
    message_contenu: str,
    thread_id: str | None = None,
    auteur_id: Any | None = None,
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
        reponse_texte = await _module._invoquer_generaliste(
            question, thread_id=thread_id, auteur_id=auteur_id
        )
    except Exception as exc:  # noqa: BLE001 - le chat ne doit pas échouer
        # Défense en profondeur : ``_invoquer_generaliste`` n'échoue jamais,
        # mais il est remplaçable (tests, monkeypatch) — et un échec ne part
        # jamais sans trace.
        logger.warning(
            "mention sociale : invocation en échec (%s)", type(exc).__name__, exc_info=True
        )
        reponse_texte = _MESSAGE_ECHEC
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


async def _iterer_reponse(
    question: str, *, thread_id: str | None, auteur_id: Any | None = None
) -> AsyncIterator[str]:
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
    # Contexte MCP **de l'auteur de la mention** : le generaliste utilise
    # les connecteurs globaux et ceux de ce compte. Un autre compte garde
    # son propre contexte, sans effet sur celui-ci.
    graphe = manager.graph(
        AGENT_GENERALISTE,
        str(auteur_id) if auteur_id is not None else None,
    )
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


def _texte_courant(morceaux: list[str]) -> str:
    """Dernier texte d'agent non vide accumulé par le streaming."""
    return "".join(morceaux).strip()


def _avec_note(texte: str, note: str) -> str:
    """Texte déjà écrit suivi de la note d'incident — la note seule s'il n'y a rien."""
    return f"{texte}\n\n{note}" if texte else note


async def _invoquer_generaliste(
    question: str, *, thread_id: str | None, auteur_id: Any | None = None
) -> str:
    """Réponse complète du généraliste — streaming asynchrone consommé.

    Quatre règles, chacune corrige un défaut constaté :

    1. ``_iterer_reponse`` (``astream``) : les outils distants du graphe sont
       async-only ; l'invocation synchrone levait ``NotImplementedError``.
    2. **Une panne ne fait pas perdre ce qui est déjà écrit** : le dernier texte
       d'agent est posté, suivi d'une note. Avant, un run qui avait répondu puis
       échoué sur un outil rendait le message d'échec **à la place**.
    3. **La cause est journalisée** (``logger.warning`` avec la pile) : l'échec
       était avalé sans trace, donc indiagnosticable (constat 04/10 : deux
       « Bonjour » ont rendu un échec technique, cause introuvable).
    4. **La durée est bornée** : un run suspendu garderait sa session et
       l'indicateur « l'agent écrit… » du fil ouverts.
    """
    import app.application.services.social_mention_service as _module

    morceaux: list[str] = []

    async def _consommer() -> None:
        async for delta in _module._iterer_reponse(
            question, thread_id=thread_id, auteur_id=auteur_id
        ):
            morceaux.append(delta)

    try:
        await asyncio.wait_for(_consommer(), timeout=_DELAI_MAX_INVOCATION_S)
    except TimeoutError:
        logger.warning(
            "mention sociale : invocation interrompue après %ss",
            _DELAI_MAX_INVOCATION_S,
        )
        return _avec_note(_texte_courant(morceaux), _MESSAGE_DELAI_DEPASSE)
    except Exception as exc:  # noqa: BLE001 - le chat ne doit pas échouer
        logger.warning(
            "mention sociale : invocation en échec (%s)", type(exc).__name__, exc_info=True
        )
        return _avec_note(_texte_courant(morceaux), _MESSAGE_ECHEC)
    return _texte_courant(morceaux) or _MESSAGE_SANS_REPONSE


__all__ = [
    "AGENT_GENERALISTE",
    "detecter_mention_generaliste",
    "repondre_mention",
    "repondre_mention_en_tache_de_fond",
]
