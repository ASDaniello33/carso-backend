"""Exposition AG-UI des agents CARSO (CopilotKit / ``ag-ui-langgraph``).

Chaque agent est exposé **individuellement** sur ``/api/v1/agui/{agent_id}`` :
il n'existe aucun endpoint « orchestrateur » (AGENTS.md §1.6).

Deux responsabilités :

1. **Catalogue** (``GET /api/v1/agui/``) — toujours disponible, même sans
   ``ag-ui-langgraph`` : l'interface sait quels agents existent et où les joindre.
2. **Montage des graphes** — un endpoint par agent, alimenté par le
   ``AgentRuntimeManager`` (``app.agents.manager``).

Changement à chaud : quand l'administrateur modifie le modèle ou redémarre le
runtime, le manager reconstruit ses graphes et **notifie** ce module, qui
démonte puis remonte les endpoints sur les nouveaux graphes — sans redémarrer le
backend. Le manager ne connaît pas FastAPI : le couplage reste dans ce module.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, FastAPI
from pydantic import BaseModel

from app.agents.catalogue import AGENT_DEFINITIONS
from app.agents.harness import recursion_limit
from app.agents.manager import get_runtime_manager
from app.api.agui_ids import AgentFluxIdsUniques
from app.api.deps import get_current_user
from app.api.schemas.agent_runtime import HitlReponseItem, HitlScenarioItem
from app.core.config import Settings, get_settings

_logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/agui",
    tags=["ag-ui"],
    dependencies=[Depends(get_current_user)],
)

#: Agents exposés (compatibilité : source unique dans ``app.agents.catalogue``).
AGENTS_EXPOSES = AGENT_DEFINITIONS


class AgentCatalogueItem(BaseModel):
    agent_id: str
    display_name: str
    description: str
    path: str
    configured: bool
    hitl_scenarios: list[HitlScenarioItem] = []


#: Export paresseux : le module n'existe que si l'extra AG-UI est installé,
#: et les versions varient. ``None`` => pas d'événement RUN_ERROR (l'exception
#: remontera telle quelle, comportement d'avant le pont).
try:  # pragma: no cover - dépend de l'installation de l'extra
    from ag_ui.core.events import RunErrorEvent
except ImportError:  # pragma: no cover - dépend de l'installation de l'extra
    RunErrorEvent = None  # type: ignore[assignment,misc]


def _message_erreur(exc: BaseException) -> str:
    """Message du modèle d'échec : lisible, sans secret (AGENTS.md §9).

    Un incident réseau vers le provider (DNS, TLS, connectivité) est traduit
    en cause générique — la traceback brute, elle, reste dans les logs serveur.
    """
    brut = str(exc).strip()
    if any(
        indice in brut.lower()
        for indice in (
            "connection error",
            "getaddrinfo",
            "connecterror",
            "apiconnectionerror",
            "ssl",
            "timed out",
            "timeout",
        )
    ):
        return (
            "Le modèle d'IA est injoignable (réseau ou fournisseur indisponible). "
            "Vérifiez la configuration dans Paramètres, puis réessayez."
        )
    if brut:
        return brut
    return type(exc).__name__


class StreamingRunError:
    """Décorateur : la fin brutale d'un run devient un événement RUN_ERROR.

    Sans ce pont, une rupture du provider pendant le streaming (connexion
    coupée, DNS, modèle qui abandonne) traverse la pile ASGI : uvicorn
    journalise une ``Exception in ASGI application`` et le navigateur reçoit
    un flux SSE tronqué sans statut — l'utilisateur ne voit rien de l'échec.

    Avec le pont, un événement AG-UI ``RUN_ERROR`` est émis avant de clore le
    flux : CopilotKit v2 le propage (code ``agent_run_error_event``) et
    ``signalerErreurChat`` affiche le retour système en français.

    Le flux est d'abord réexpédié tel quel : le pont n'altère **que** la fin.
    """

    def __init__(self, interne: Any) -> None:
        self._interne = interne

    def __getattr__(self, nom: str) -> Any:
        return getattr(self._interne, nom)

    def clone(self) -> StreamingRunError:
        """Un clone par requête : état isolé, même chaîne de décorateurs."""
        cloner = getattr(self._interne, "clone", None)
        interne = cloner() if callable(cloner) else self._interne
        return StreamingRunError(interne)

    async def run(self, input_data: Any):  # type: ignore[no-untyped-def]
        """Réexpédie le flux ; une exception devient un RUN_ERROR terminal."""
        try:
            async for event in self._interne.run(input_data):
                yield event
        except BaseException as exc:
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            _logger.warning("Run AG-UI interrompu : %s: %s", type(exc).__name__, exc)
            if RunErrorEvent is not None:
                yield RunErrorEvent(message=_message_erreur(exc))


def chemin_agui(agent_id: str, api_prefix: str) -> str:
    """Chemin HTTP d'exposition d'un agent."""
    return f"{api_prefix}/agui/{agent_id}"


def _base_agui(api_prefix: str) -> str:
    """Préfixe commun des endpoints AG-UI (hors catalogue)."""
    return f"{api_prefix}/agui/"


@router.get("/", response_model=list[AgentCatalogueItem])
def lister_agents_agui() -> list[AgentCatalogueItem]:
    """Catalogue des agents exposés — reflète la configuration effective."""
    prefix = get_settings().api_v1_prefix
    pret = get_runtime_manager().is_configured()
    return [
        AgentCatalogueItem(
            agent_id=definition.agent_id,
            display_name=definition.display_name,
            description=definition.description,
            path=chemin_agui(definition.agent_id, prefix),
            configured=pret,
            hitl_scenarios=[
                HitlScenarioItem(
                    id=scenario.id,
                    titre=scenario.titre,
                    contexte=scenario.contexte,
                    action=scenario.action,
                    reponses=[
                        HitlReponseItem(
                            valeur=reponse.valeur,
                            libelle=reponse.libelle,
                            description=reponse.description,
                        )
                        for reponse in scenario.reponses
                    ],
                )
                for scenario in definition.hitl_scenarios
            ],
        )
        for definition in AGENT_DEFINITIONS
    ]


def _importer_agui() -> Any | None:
    """Import paresseux : le backend démarre sans l'extra ``ag-ui-langgraph``."""
    try:
        import ag_ui_langgraph
    except ImportError:
        return None
    return ag_ui_langgraph


def _routes_suivies(app: FastAPI) -> list[Any]:
    """Routes AG-UI ajoutées par ce module, suivies par **identité**.

    FastAPI récent (0.14x) encapsule les routeurs inclus dans des objets internes
    (``_IncludedRouter``) qui n'exposent pas ``.path`` : filtrer par chemin ne
    suffit donc plus pour démonter. On mémorise les routes réellement ajoutées
    entre deux instants — indépendant de la version de FastAPI.
    """
    suivies = getattr(app.state, "agui_routes", None)
    if suivies is None:
        suivies = []
        app.state.agui_routes = suivies
    return suivies


def _monter_un_agent(
    app: FastAPI, definition: Any, graph: Any, path: str, agui: Any
) -> None:
    """Monte un endpoint AG-UI en mémorisant les routes ajoutées."""
    suivies = _routes_suivies(app)
    avant = {id(route) for route in app.router.routes}
    _ajouter_endpoint(app, definition, graph, path, agui)
    for route in app.router.routes:
        if id(route) not in avant:
            suivies.append(route)


def _config_agui() -> dict[str, Any]:
    """Config LangGraph passée à chaque run AG-UI.

    Sans ``recursion_limit`` ici, ``ag-ui-langgraph`` laisse LangGraph poser
    son défaut figé (25) — ``AGENT_MAX_ITERATIONS`` n'avait aucun effet sur
    le chat. Le manager transmet déjà la valeur au harnais ; on la relit
    ici pour que le stream AG-UI utilise la même borne.
    """
    settings = get_runtime_manager().effective_settings()
    return {"recursion_limit": recursion_limit(None, settings)}


def _ajouter_endpoint(
    app: FastAPI, definition: Any, graph: Any, path: str, agui: Any
) -> None:
    """Appelle l'API d'``ag-ui-langgraph`` en tolérant ses variantes."""
    add_endpoint = agui.add_langgraph_fastapi_endpoint
    wrapper = getattr(agui, "LangGraphAGUIAgent", None) or getattr(
        agui, "LangGraphAgent", None
    )
    if wrapper is not None:
        try:
            agent = wrapper(
                name=definition.agent_id,
                description=definition.description,
                graph=graph,
                config=_config_agui(),
                # Interruptions HITL : émettre l'outcome structuré AG-UI
                # (RUN_FINISHED outcome.type="interrupt") pour que le frontend
                # CopilotKit v2 rende le questionnaire et reprenne via
                # RunAgentInput.resume[]. Le legacy on_interrupt est désactivé
                # : un seul canal, moins d'ambiguïté côté client.
                emit_interrupt_outcome=True,
                enable_legacy_on_interrupt_event=False,
            )
            # Les providers réutilisent call_0 / call_1 d'un tour à l'autre.
            # On réécrit uniquement les ids d'appels d'outils (hors reprise
            # HITL ancien canal) : les ids de messages appartiennent au
            # reminting interne d'ag-ui-langgraph, toute réécriture en plus
            # désappariait le flux et tronquait la fin de stream (voir
            # app/api/agui_ids.py).
            agent = AgentFluxIdsUniques(agent)
            # La fin brutale d'un run (provider coupé…) devient un événement
            # RUN_ERROR : le client affiche l'échec au lieu d'un flux tronqué.
            agent = StreamingRunError(agent)
            try:
                add_endpoint(app=app, agent=agent, path=path)
                return
            except TypeError:
                add_endpoint(app, agent, path)
                return
        except TypeError:
            pass
    add_endpoint(app, graph, path)


def demonter_endpoints_agui(app: FastAPI, api_prefix: str | None = None) -> int:
    """Retire les endpoints AG-UI montés (les graphes remplacés ne servent plus).

    Deux passes, pour être robuste quelles que soient les versions :

    1. les routes **suivies** (identité) — voie normale ;
    2. toute route résiduelle exposant encore le préfixe AG-UI (hors catalogue).

    Returns:
        Le nombre de routes retirées.
    """
    prefix = api_prefix or get_settings().api_v1_prefix
    base = _base_agui(prefix)

    suivies = _routes_suivies(app)
    cibles = {id(route) for route in suivies}
    for route in app.router.routes:
        chemin = getattr(route, "path", None)
        if isinstance(chemin, str) and chemin.startswith(base) and chemin != base:
            cibles.add(id(route))

    if not cibles:
        return 0

    app.router.routes = [
        route for route in app.router.routes if id(route) not in cibles
    ]
    suivies.clear()
    # Le schéma OpenAPI est mis en cache : il doit refléter le nouveau montage.
    app.openapi_schema = None
    return len(cibles)


def _remonter(app: FastAPI, *, settings: Settings | None = None, agui: Any | None = None) -> None:
    """(Re)monte les endpoints AG-UI sur les graphes **courants** du manager."""
    paquet = agui if agui is not None else _importer_agui()
    if paquet is None:
        return
    cfg = settings or get_settings()
    manager = get_runtime_manager()

    demonter_endpoints_agui(app, cfg.api_v1_prefix)

    # L'absence de modèle ne bloque pas le montage : le graphe est construit
    # à la première invocation. Seul l'import d'ag-ui-langgraph est requis.
    if not manager.is_configured():
        _logger.info(
            "Modèle agent non configuré : endpoints AG-UI montés quand même "
            "(le modèle sera résolu à la première conversation)"
        )

    montes = 0
    for definition in AGENT_DEFINITIONS:
        path = chemin_agui(definition.agent_id, cfg.api_v1_prefix)
        try:
            graphe = manager.graph(definition.agent_id)
            _monter_un_agent(app, definition, graphe, path, paquet)
        except Exception as exc:
            _logger.warning("AG-UI non monté pour %s : %s", definition.agent_id, exc)
            continue
        montes += 1

    _logger.info("AG-UI : %s endpoint(s) monté(s)", montes)


def monter_endpoints_agui(app: FastAPI, settings: Settings) -> None:
    """Monte les endpoints AG-UI et les rattache au runtime rechargeable.

    L'écouteur est installé une seule fois par application : à chaque
    reconstruction du runtime (changement de modèle à chaud, redémarrage du
    runtime), les endpoints sont remontés sur les graphes à jour.
    """
    if _importer_agui() is None:
        _logger.info("ag-ui-langgraph absent : endpoints AG-UI non montés")
        return

    manager = get_runtime_manager()
    if not getattr(app.state, "agui_ecouteur_installe", False):
        manager.add_listener(lambda: _remonter(app))
        app.state.agui_ecouteur_installe = True

    _remonter(app, settings=settings)


__all__ = [
    "AGENTS_EXPOSES",
    "StreamingRunError",
    "chemin_agui",
    "demonter_endpoints_agui",
    "monter_endpoints_agui",
    "router",
]
