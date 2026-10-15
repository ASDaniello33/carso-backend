"""Catalogue AG-UI — disponible même sans extra ag-ui-langgraph."""

from fastapi.testclient import TestClient

from app.api.agui import AGENTS_EXPOSES
from app.main import app


def test_catalogue_agui_agents_exposes() -> None:
    client = TestClient(app)
    resp = client.get("/api/v1/agui/")
    assert resp.status_code == 200
    ids = {item["agent_id"] for item in resp.json()}
    attendus = {d.agent_id for d in AGENTS_EXPOSES}
    assert ids == attendus
    assert "agent_analyseur_appel_proposition" not in ids
    assert "agent_generateur_offre" in ids
    for item in resp.json():
        assert item["path"].startswith("/api/v1/agui/")
        assert item["display_name"]


def test_aucun_sous_agent_interne_apres_suppression_analyseur() -> None:
    """L'analyseur a été absorbé par le générateur : plus d'agent interne.

    La structure à deux listes reste le contrat d'un futur agent interne, mais
    aujourd'hui le runtime et le catalogue exposé coïncident.
    """
    from app.agents.catalogue import (
        AGENT_DEFINITIONS,
        AGENT_RUNTIME,
        agent_ids,
        runtime_agent_ids,
    )

    assert "analyseur" not in "".join(runtime_agent_ids())
    assert runtime_agent_ids() == agent_ids()
    assert AGENT_RUNTIME == AGENT_DEFINITIONS
    assert {d.agent_id for d in AGENTS_EXPOSES} == set(agent_ids())


def test_pas_de_coordinateur_dans_le_catalogue() -> None:
    ids = {d.agent_id for d in AGENTS_EXPOSES}
    assert "orchestrateur" not in "".join(ids).lower()
    assert "master" not in "".join(ids).lower()


def test_agents_ui_conversation_libre_via_system_prompt() -> None:
    """Chaque agent UI a un system prompt ; aucun message utilisateur figé."""
    from app.agents.harness import build_system_prompt
    from app.agents.prompt import (
        ASSISTANT_FORMATEUR_SYSTEM_PROMPT,
        GENERALISTE_SYSTEM_PROMPT,
        GENERATEUR_OFFRE_SYSTEM_PROMPT,
        PERSONNEL_SYSTEM_PROMPT,
        RH_SYSTEM_PROMPT,
        STATISTIQUE_SYSTEM_PROMPT,
    )

    attendus = {
        "agent_generateur_offre": GENERATEUR_OFFRE_SYSTEM_PROMPT,
        "agent_rh": RH_SYSTEM_PROMPT,
        "agent_assistant_formateur": ASSISTANT_FORMATEUR_SYSTEM_PROMPT,
        "agent_generaliste_readonly": GENERALISTE_SYSTEM_PROMPT,
        "agent_statistique": STATISTIQUE_SYSTEM_PROMPT,
        "agent_personnel": PERSONNEL_SYSTEM_PROMPT,
    }
    for definition in AGENTS_EXPOSES:
        assert definition.agent_id in attendus
        assert definition.prompt == attendus[definition.agent_id]
        systeme = build_system_prompt(definition)
        assert definition.prompt in systeme
        assert "Cadre CARSO" in systeme
        assert "Pièces jointes du chat" in systeme
        assert "Ne le redemande jamais" in systeme
        # Le harnais n'injecte aucun message utilisateur : AG-UI transmet
        # le message libre de l'humain.
        assert "Consigne complémentaire" not in systeme
        assert "Analyse l'appel" not in systeme


def test_preflight_agui_autorise_en_tetes_copilotkit() -> None:
    """CopilotKit envoie des x-copilotkit-* : le préflight ne doit pas les refuser.

    Une allowlist figée (Authorization + Content-Type) faisait échouer OPTIONS
    et le navigateur annulait le POST → TypeError: network error.
    """
    client = TestClient(app)
    resp = client.options(
        "/api/v1/agui/agent_rh",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": (
                "authorization,content-type,x-copilotkit-runtime-client,last-event-id"
            ),
        },
    )
    assert resp.status_code in {200, 204}
    autorises = resp.headers.get("access-control-allow-headers", "").lower()
    assert autorises == "*" or "x-copilotkit-runtime-client" in autorises
    assert resp.headers.get("access-control-allow-origin") == "http://localhost:3000"


def test_config_agui_passe_recursion_limit() -> None:
    """Sans ce config, LangGraph pose recursion_limit=25 et coupe le stream."""
    from app.api.agui import _ajouter_endpoint, _config_agui

    config = _config_agui()
    assert config["recursion_limit"] >= 1000

    recus: list[dict] = []

    class _Wrapper:
        def __init__(self, **kwargs) -> None:
            recus.append(kwargs)

    class _Paquet:
        LangGraphAgent = _Wrapper

        @staticmethod
        def add_langgraph_fastapi_endpoint(**_kwargs) -> None:
            return None

    class _Def:
        agent_id = "agent_test"
        description = "d"

    _ajouter_endpoint(app, _Def(), graph=object(), path="/x", agui=_Paquet())
    assert recus
    assert recus[0]["config"]["recursion_limit"] == config["recursion_limit"]
    assert recus[0]["emit_interrupt_outcome"] is True


def test_catalogue_agui_expose_scenarios_hitl() -> None:
    """Le catalogue AG-UI transporte les scénarios HITL (contrat CopilotKit)."""
    client = TestClient(app)
    resp = client.get("/api/v1/agui/")
    assert resp.status_code == 200

    par_id = {item["agent_id"]: item for item in resp.json()}
    assert par_id["agent_rh"]["hitl_scenarios"], "Agent RH sans scénario HITL"
    scenario = par_id["agent_rh"]["hitl_scenarios"][0]
    assert scenario["id"] == "proposition_affectation"
    assert any(r["valeur"] == "annuler" for r in scenario["reponses"])
    for item in par_id.values():
        assert isinstance(item["hitl_scenarios"], list)


# --- Pont d'erreurs : la fin brutale d'un run devient un RUN_ERROR -----------


import asyncio  # noqa: E402

from app.api.agui import StreamingRunError, _message_erreur  # noqa: E402


def _evenement(nom: str) -> dict:
    return {"type": nom}


def _types(events: list) -> list[str]:
    """Types d'événements : dicts (flux interne) ou modèles AG-UI (RUN_ERROR)."""
    return [e["type"] if isinstance(e, dict) else e.type for e in events]


def _message_de(event) -> str:
    return event["message"] if isinstance(event, dict) else event.message


class _InterneFlux:
    """Double minimal : délègue à un générateur, expose ``clone`` comme l'agent."""

    def __init__(self, fabrique) -> None:
        self._fabrique = fabrique

    def clone(self) -> "_InterneFlux":
        return self

    def run(self, entree):
        return self._fabrique(entree)


def test_le_flux_normal_traverse_le_pont_sans_modification() -> None:
    """Aucune exception : le pont est transparent (mêmes événements, même ordre)."""

    async def _flux(entree):
        yield _evenement("RUN_STARTED")
        yield {"type": "TEXT_MESSAGE_CONTENT", "delta": str(entree)}
        yield _evenement("RUN_FINISHED")

    pont = StreamingRunError(_InterneFlux(_flux))

    async def _collecter() -> list:
        return [e async for e in pont.run({"thread_id": "t1"})]

    sortis = asyncio.run(_collecter())
    assert _types(sortis) == [
        "RUN_STARTED",
        "TEXT_MESSAGE_CONTENT",
        "RUN_FINISHED",
    ]


def test_exception_en_cours_de_run_devient_run_error() -> None:
    """Le provider coupe en plein stream : le client reçoit RUN_ERROR, pas un flux tronqué."""

    async def _flux(_entree):
        yield _evenement("RUN_STARTED")
        yield _evenement("TEXT_MESSAGE_START")
        raise ConnectionError("Connection error. [Errno 11001] getaddrinfo failed")

    pont = StreamingRunError(_InterneFlux(_flux))

    async def _collecter() -> list:
        return [e async for e in pont.run({})]

    sortis = asyncio.run(_collecter())
    assert _types(sortis) == [
        "RUN_STARTED",
        "TEXT_MESSAGE_START",
        "RUN_ERROR",
    ]
    erreur = sortis[-1]
    assert "injoignable" in _message_de(erreur)
    assert "getaddrinfo" not in _message_de(erreur)  # détail technique filtré


def test_erreur_metier_passe_telle_quelle() -> None:
    """Un message métier explicite atteint l'utilisateur sans réécriture."""

    async def _flux(_entree):
        yield _evenement("RUN_STARTED")
        raise RuntimeError("Tool 'generer_document' non déclaré dans le contrat")

    pont = StreamingRunError(_InterneFlux(_flux))

    async def _collecter() -> list:
        return [e async for e in pont.run({})]

    sortis = asyncio.run(_collecter())
    assert sortis[-1].type == "RUN_ERROR"
    assert "generer_document" in sortis[-1].message


def test_message_erreur_vide_retombe_sur_le_nom_d_exception() -> None:
    assert _message_erreur(RuntimeError()) == "RuntimeError"
    assert _message_erreur(RuntimeError("panne réseau: Connection error"))[:5] == "Le mo"


def test_clone_du_pont_isole_par_requete() -> None:
    """``clone()`` traverse la chaîne de décorateurs (un état par requête SSE)."""

    class _Cible:
        clones = 0

        def clone(self):
            _Cible.clones += 1
            return self

    cible = _Cible()
    pont = StreamingRunError(cible)
    clone_pont = pont.clone()
    assert isinstance(clone_pont, StreamingRunError)
    assert _Cible.clones == 1
