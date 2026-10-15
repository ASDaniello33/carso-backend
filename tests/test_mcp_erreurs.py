"""Tests — panne d'un connecteur MCP : le détail va au LLM, le workflow continue.

Constat du 02/10 : ``langchain-mcp-adapters`` ne convertit en Tool Message que
les erreurs d'exécution **déclarées par le serveur** (``CallToolResult(isError=
True)``) ; les pannes de transport, de session et de conversion remontent
toujours en exception (docstring de ``convert_mcp_tool_to_langchain_tool``).
Sans traitement, LangGraph interrompt le run entier : l'utilisateur perd sa
conversation à cause d'un connecteur optionnel (constaté avec un serveur MCP
répondant 405 sur le flux GET, et avec un connecteur ``stdio`` arrêté).

Ces tests verrouillent l'enveloppe ``proteger_outil_mcp`` :

- toute exception devient un payload JSON lisible (``{ok: false, error: …}``),
  même forme que ``formater_erreur_outil``, sans secret ;
- l'invocation **synchrone** fonctionne aussi : les outils MCP sont async-only,
  un graphe invoqué hors coroutine levait ``NotImplementedError: StructuredTool
  does not support sync invocation`` ;
- le contrat de l'outil (nom, description, schéma d'entrée) est conservé ;
- le chargement par agent (``outils_mcp_pour``) enveloppe bien les outils reçus ;
- **au niveau du graphe** (modèle scripté, aucun réseau) : le run va au bout en
  asynchrone **et** en synchrone, l'échec arrive au modèle comme résultat d'outil.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import pytest
from langchain_core.tools import StructuredTool

from app.agents.base import AgentDefinition
from app.agents.harness import AgentRuntimeConfig, create_carso_deep_agent
from app.agents.mcp import (
    outils_mcp_pour,
    paquet_disponible,
    payload_erreur_mcp,
    proteger_outil_mcp,
    vider_cache,
)
from app.domain.mcp import ServeurMcp
from app.tools.permissions import PermissionPolicy
from tests.agent_fixtures import ScriptedChatModel, final_message, tool_call

_SCHEMA = {
    "type": "object",
    "properties": {"requete": {"type": "string"}},
    "required": ["requete"],
}


def _outil_mcp_simule(
    *,
    nom: str = "recherche_distante",
    resultat: Any = "ok",
    erreur: Exception | None = None,
) -> StructuredTool:
    """Outil MCP simulé : **async-only**, schéma dict, comme ceux des adaptateurs."""

    async def _coroutine(**arguments: Any) -> Any:
        _ = arguments
        if erreur is not None:
            raise erreur
        return resultat

    return StructuredTool.from_function(
        coroutine=_coroutine,
        name=nom,
        description="Outil distant de test",
        args_schema=_SCHEMA,
        response_format=("content_and_artifact" if isinstance(resultat, tuple) else "content"),
    )


async def _appeler_async(outil: Any, arguments: dict[str, Any]) -> str:
    resultat = await outil.ainvoke(arguments)
    assert isinstance(resultat, str)
    return resultat


# --- 1. Payload d'erreur --------------------------------------------------------


def test_payload_erreur_mcp_detaille_l_echec_et_le_conseil() -> None:
    """Le modèle reçoit le type, le message, la cause et la conduite à tenir."""
    try:
        try:
            raise OSError("connexion refusée (127.0.0.1:9111)")
        except OSError as cause:
            raise RuntimeError("session MCP perdue") from cause
    except RuntimeError as exc:
        charge = json.loads(payload_erreur_mcp("terminal_cmd", "terminal", exc))

    assert charge["ok"] is False
    erreur = charge["error"]
    assert erreur["code"] == "mcp_tool_error"
    assert "terminal_cmd" in erreur["message"]
    assert "RuntimeError" in erreur["message"]
    assert "session MCP perdue" in erreur["message"]

    details = erreur["details"]
    assert details["outil"] == "terminal_cmd"
    assert details["serveur"] == "terminal"
    assert details["type_erreur"] == "RuntimeError"
    assert details["causes"] and "connexion refusée" in details["causes"][0]
    assert "conseil" in details


def test_payload_erreur_mcp_ne_divulgue_aucun_secret() -> None:
    """Le payload est public côté modèle : jamais d'en-tête ni de jeton."""
    charge = json.loads(
        payload_erreur_mcp("outil", "serveur", RuntimeError("jeton refusé"))
    )
    texte = json.dumps(charge, ensure_ascii=False).lower()
    assert "authorization" not in texte
    assert "bearer" not in texte


# --- 2. Une exception devient un résultat d'outil -------------------------------


def test_outil_mcp_en_panne_rend_un_payload_et_non_une_exception() -> None:
    """Avant l'enveloppe, l'exception traversait le graphe et tuait le run."""
    outil = proteger_outil_mcp(
        _outil_mcp_simule(erreur=RuntimeError("connecteur arrêté")),
        serveur="parallel",
    )

    charge = json.loads(asyncio.run(_appeler_async(outil, {"requete": "carso"})))

    assert charge["ok"] is False
    details = charge["error"]["details"]
    assert details["serveur"] == "parallel"
    assert details["outil"] == "recherche_distante"
    assert "connecteur arrêté" in charge["error"]["message"]


def test_outil_mcp_en_panne_ne_casse_pas_l_invocation_synchrone() -> None:
    """Les outils MCP sont async-only : le pont synchrone doit exister."""
    outil = proteger_outil_mcp(
        _outil_mcp_simule(erreur=TimeoutError("délai dépassé")),
        serveur="terminal",
    )

    # Avant l'enveloppe : NotImplementedError: StructuredTool does not support
    # sync invocation (constaté sur le fil social, run perdu).
    charge = json.loads(outil.invoke({"requete": "ls"}))

    assert charge["ok"] is False
    assert charge["error"]["details"]["type_erreur"] == "TimeoutError"


def test_outil_mcp_succes_transmet_le_contenu_et_garde_le_contrat() -> None:
    """Le succès passe intact : nom, description et schéma sont ceux de l'outil."""
    outil = proteger_outil_mcp(_outil_mcp_simule(resultat="3 missions publiées"), serveur="p")

    assert outil.name == "recherche_distante"
    assert outil.description == "Outil distant de test"
    assert outil.args_schema == _SCHEMA
    assert asyncio.run(_appeler_async(outil, {"requete": "missions"})) == "3 missions publiées"
    assert outil.invoke({"requete": "missions"}) == "3 missions publiées"

    enveloppe = proteger_outil_mcp(
        _outil_mcp_simule(resultat=("texte lisible", {"brut": 1})), serveur="p"
    )
    # Contenu d'un outil ``content_and_artifact`` : le couple est déplié.
    assert asyncio.run(_appeler_async(enveloppe, {"requete": "x"})) == "texte lisible"


# --- 3. Outils de test : client MCP simulé, cache neutre ---------------------------


class _FauxClientMcp:
    """Client MCP simulé : un seul outil, qui échoue au transport."""

    def __init__(self, connexions: dict[str, Any], **options: Any) -> None:
        self.connexions = connexions
        self.options = options

    async def get_tools(self) -> list[Any]:
        return [
            _outil_mcp_simule(
                nom="faux_recherche",
                erreur=ConnectionError("405 Method Not Allowed"),
            )
        ]


def _espion(
    monkeypatch: pytest.MonkeyPatch, cible: str, methode: str, messages: list[str]
) -> None:
    """Journalise les appels ``logger.<methode>`` de ``cible`` dans ``messages``.

    Espion et non ``caplog`` : ``configure_logging`` remplace les handlers de
    la racine (cf. ``test_config``), le capteur ``caplog`` perd alors les
    enregistrements dans une suite complète alors qu'ils passent isolés.
    """
    logger = logging.getLogger(cible)
    original = getattr(logger, methode)

    def _record(*args: Any, **kwargs: Any) -> None:
        # Le journal n'est pas hijacké : l'appel d'origine part aussi, seul le
        # texte est recopié pour l'assertion.
        original(*args, **kwargs)
        messages.append(" ".join(str(a) for a in args))

    monkeypatch.setattr(logger, methode, _record)


@pytest.fixture(autouse=True)
def _cache_mcp_vide() -> None:
    """Le cache du module est global : chaque test part d'un état neutre."""
    vider_cache()
    yield
    vider_cache()


# --- 4. Niveau graphe : le workflow ne s'arrête pas ---------------------------------


def _config_fil(thread_id: str) -> dict[str, Any]:
    """Config LangGraph : le checkpointer du harnais exige un fil."""
    return {"configurable": {"thread_id": thread_id}}


def _contrat_minimal() -> AgentDefinition:
    """Contrat minimal : l'outil MCP arrive par ``extra_tools``, hors allow-list."""
    return AgentDefinition(
        agent_id="agent_mcp_test",
        display_name="Agent MCP de test",
        description="Agent de test sans tools métier",
        prompt="Tu es un agent de test.",
        tools=(),
        approval_required=(),
        collaboration=(),
        policy=PermissionPolicy(allowed_capabilities=frozenset()),
        skills=(),
    )


def _graphe_avec_outil_mcp(outil: Any, modele: ScriptedChatModel) -> Any:
    """Graphe réel du harnais : outil distant en ``extra_tools`` (comme le runtime)."""
    return create_carso_deep_agent(
        definition=_contrat_minimal(),
        tools=(),
        runtime=AgentRuntimeConfig(model=modele, extra_tools=(outil,)),
    )


def _outil_mcp_en_panne() -> Any:
    return proteger_outil_mcp(
        _outil_mcp_simule(
            nom="terminal_cmd", erreur=ConnectionError("serveur MCP arrêté")
        ),
        serveur="terminal",
    )


def _modele_scripte() -> ScriptedChatModel:
    return ScriptedChatModel(
        responses=[
            tool_call("terminal_cmd", {"requete": "ls"}),
            final_message("J'ai poursuivi sans l'outil distant."),
        ]
    )


def test_graphe_asynchrone_survit_a_la_panne_d_un_outil_mcp() -> None:
    """Sans l'enveloppe, la ConnectionError traversait le graphe et tuait le run."""
    modele = _modele_scripte()
    graphe = _graphe_avec_outil_mcp(_outil_mcp_en_panne(), modele)

    etat = asyncio.run(
        graphe.ainvoke(
            {"messages": [{"role": "user", "content": "prépare la mission"}]},
            config=_config_fil("mcp-async"),
        )
    )

    assert etat["messages"][-1].content == "J'ai poursuivi sans l'outil distant."
    recus = modele.tool_messages_recus()
    assert "mcp_tool_error" in recus
    assert "terminal_cmd" in recus


def test_graphe_synchrone_survit_a_la_panne_d_un_outil_mcp() -> None:
    """Les outils MCP sont async-only : le pont synchrone du harnais doit tenir.

    Avant l'enveloppe : ``NotImplementedError: StructuredTool does not support
    sync invocation`` dès que le modèle appelait le connecteur.
    """
    modele = _modele_scripte()
    graphe = _graphe_avec_outil_mcp(_outil_mcp_en_panne(), modele)

    etat = graphe.invoke(
        {"messages": [{"role": "user", "content": "prépare la mission"}]},
        config=_config_fil("mcp-sync"),
    )

    assert etat["messages"][-1].content == "J'ai poursuivi sans l'outil distant."
    assert "mcp_tool_error" in modele.tool_messages_recus()


# --- 5. Chargement réel par agent ------------------------------------------------


def test_outils_mcp_pour_enveloppe_les_outils_charges(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Le chargement par agent rend des outils protégés, jamais les outils bruts."""
    if not paquet_disponible():  # pragma: no cover - extra MCP absent
        pytest.skip("langchain-mcp-adapters non installé")

    monkeypatch.setattr(
        "langchain_mcp_adapters.client.MultiServerMCPClient", _FauxClientMcp
    )
    serveur = ServeurMcp(
        nom="parallel",
        transport="streamable_http",
        url="https://search.parallel.ai/mcp",
        arguments=[],
        agents=["agent_statistique"],
        en_tetes_chiffres=None,
        actif=True,
    )

    outils = outils_mcp_pour("agent_statistique", [serveur])

    assert len(outils) == 1
    outil = outils[0]
    assert outil.name == "faux_recherche"

    charge = json.loads(asyncio.run(_appeler_async(outil, {"requete": "appels"})))

    assert charge["ok"] is False
    assert charge["error"]["details"]["serveur"] == "parallel"
    assert "405" in charge["error"]["message"]


def test_outils_mcp_pour_journalise_le_chargement_reussi(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Un connecteur qui répond laisse une trace lisible.

    Constat du 04/10 : `search.parallel.ai` journalisait cinq connexions et
    cinq « 405 Method Not Allowed » sur le flux SSE **sans qu'aucune ligne ne
    dise si les outils avaient été chargés**. Le 405 est le chemin normal de ce
    serveur (il n'expose pas de flux GET) : `tools/list` aboutit et rend
    `web_search`/`web_fetch`. Sans trace de succès, la panne et le bon
    fonctionnement sont indiscernables dans le journal.

    Assertion faite par espion sur ``_logger.info`` et non via ``caplog`` :
    ``configure_logging`` remplace les handlers de la racine (cf.
    ``test_config``), le capteur `caplog` n'est pas fiable en suite complète.
    """
    if not paquet_disponible():  # pragma: no cover - extra MCP absent
        pytest.skip("langchain-mcp-adapters non installé")

    monkeypatch.setattr(
        "langchain_mcp_adapters.client.MultiServerMCPClient", _FauxClientMcp
    )
    messages: list[str] = []
    # `_logger = logging.getLogger(__name__)` : le logger visé est bien
    # « app.agents.mcp », pas « app.agents.mcp._logger ».
    _espion(monkeypatch, "app.agents.mcp", "info", messages)

    serveur = ServeurMcp(
        nom="parallel",
        transport="streamable_http",
        url="https://search.parallel.ai/mcp",
        arguments=[],
        agents=["agent_statistique"],
        en_tetes_chiffres=None,
        actif=True,
    )

    outils = outils_mcp_pour("agent_statistique", [serveur])

    assert len(outils) == 1  # le chargement a bien abouti
    joined = " ".join(messages)
    assert "parallel" in joined
    assert "agent_statistique" in joined
    assert "faux_recherche" in joined
