"""Exposition AG-UI des agents CARSO (CopilotKit / ``ag-ui-langgraph``).

Chaque agent est exposé **individuellement** sur ``/api/v1/agui/{agent_id}`` :
il n'existe aucun endpoint « orchestrateur » (AGENTS.md §1.6).

Deux responsabilités :

1. **Catalogue** (``GET /api/v1/agui/``)—toujours disponible, même sans
   ``ag-ui-langgraph``—l'interface sait quels agents existent et où les joindre.
2. **Montage des graphes**—un endpoint par agent, alimenté par le
   ``AgentRuntimeManager`` (``app.agents.manager``).

Changement à chaud—quand l'administrateur modifie le modèle ou redémarre le
runtime, le manager reconstruit ses graphes et **notifie** ce module, qui
démonte puis remonte les endpoints sur les nouveaux graphes—sans redémarrer le
backend. Le manager ne connaît pas FastAPI—le couplage reste dans ce module.
"""

from __future__ import annotations

import json
import logging
import re
from contextvars import ContextVar
from typing import Any

from fastapi import APIRouter, Depends, FastAPI
from pydantic import BaseModel

from app.agents.catalogue import AGENT_DEFINITIONS
from app.agents.graphe_par_utilisateur import GrapheParUtilisateur
from app.agents.harness import recursion_limit
from app.agents.manager import get_runtime_manager
from app.api.agui_historique import HistoriqueArgumentsSains
from app.api.agui_ids import AgentFluxIdsUniques
from app.api.agui_interrupt_orphelin import PurgeInterruptOrphelin
from app.api.agui_outils_complets import ResultatsOutilsComplets
from app.api.agui_resume_interne import ResumeInterneMasque
from app.api.deps import CurrentUser, get_current_user
from app.api.permissions import agents_autorises, garde_agui
from app.api.schemas.agent_runtime import HitlReponseItem, HitlScenarioItem
from app.core.config import Settings, get_settings

_logger = logging.getLogger(__name__)


class _GrapheSansTopologie:
    """Stand-in de construction : ``ag_ui_langgraph`` ne lit que ``nodes``.

    ``LangGraphAGUIAgent.__init__`` parcourt ``graph.nodes`` pour repérer des
    sous-graphes compilés. Avec le proxy ``GrapheParUtilisateur``, cette lecture
    déclenchait la résolution du graphe **au montage**—donc une connexion
    PostgreSQL dès l'import de l'application, avant que le serveur ne réponde.
    Une base lente ou injoignable bloquait alors le démarrage entier (les
    préflights OPTIONS eux-mêmes recevaient une erreur d'initialisation).

    La topologie n'est utile qu'à l'attribution des événements de sous-agents,
    désactivée ici (``subagent_visibility`` par défaut). On construit donc le
    wrapper sans toucher à la base, puis on lui rend le proxy : chaque accès
    réel (schémas, état, flux) est résolu au run, dans le contexte du compte.
    """

    nodes: dict[str, Any] = {}


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


#: Dernier outil appelé dans le run (nom + issue), capturé sur les messages du
#: graphe : un échec « après un outil » se nomme, au lieu de finir en
#: « injoignable ». Contexte lié à la tâche : deux comptes en parallèle ne se
#: voient pas.
_TOOL_DERNIER: ContextVar[tuple[str, str] | None] = ContextVar(
    "agui_tool_dernier", default=None
)

#: Formes de message d'un tool en échec (LangChain / JSON d'erreur CARSO).
_OUTIL_ECHEC = re.compile(
    r"tool[\s_-]?call|tool[\s_-]?error|'ok':\s*false|\"ok\":\s*false|"
    r"erreur d'outil|tool invalide",
    re.IGNORECASE,
)


def _outil_du_message(message: Any) -> tuple[str | None, str]:
    """(nom d'outil, statut) d'un message de graphe, quand il en porte un."""
    nom = getattr(message, "name", None)
    if not nom:
        return None, str(getattr(message, "type", "") or "")
    contenu = getattr(message, "content", "")
    if not isinstance(contenu, str):
        return str(nom), str(getattr(message, "type", "") or "")
    return str(nom), "__echec__" if _OUTIL_ECHEC.search(contenu) else "ok"


def _dernier_outil(entree: Any) -> tuple[str, str] | None:
    """Dernier message d'outil du run — remis à zéro à chaque requête."""
    messages = entree.get("messages") if isinstance(entree, dict) else None
    if not isinstance(messages, list):
        return None
    dernier: tuple[str, str] | None = None
    for message in messages:
        nom, statut = _outil_du_message(message)
        if nom:
            dernier = (nom, statut)
    return dernier


#: Classes d'exception d'une panne de **transport** (DNS, TLS, socket).
#: Comparées par nom pour ne pas dépendre des SDK installés (openai, httpx,
#: google-genai, urllib3) : chacun a sa hiérarchie.
_CLASSES_RESEAU = frozenset(
    {
        "APIConnectionError",
        "OpenAIConnectionError",
        "APITimeoutError",
        "ConnectError",
        "ConnectTimeout",
        "ConnectionError",
        "ConnectionRefusedError",
        "ConnectionResetError",
        "MaxRetryError",
        "NewConnectionError",
        "ReadTimeout",
        "SSLError",
        "SSLCertVerificationError",
        "TimeoutException",
        "TimeoutError",
        "URLError",
    }
)

#: Indices textuels d'un transport cassé — **jamais** un refus du fournisseur.
#: Volontairement étroits : « timeout » seul est aussi le texte d'un 504 ou
#: d'un délai propre au fournisseur, qui porte alors un message utile.
_INDICES_RESEAU = (
    "connection error",
    "getaddrinfo",
    "name or service not known",
    "nodename nor servname",
    "connection refused",
    "connection reset",
    "certificate verify failed",
    "server certificate",
    "apiconnectionerror",
    "connecterror",
    "max retries exceeded",
)

#: Masques de secret : un message de fournisseur cite parfois la clé refusée.
_SECRETS = re.compile(
    r"(sk-[A-Za-z0-9_\-*.]+|Bearer\s+[A-Za-z0-9._\-]+|"
    r"(?:api[_-]?key|access[_-]?token|token|secret)\s*[=:]\s*[^\s,;\"'}]+)",
    re.IGNORECASE,
)
_URL = re.compile(r"https?://\S+", re.IGNORECASE)

#: Longueur maximale du détail repris du fournisseur (un écran de chat, pas un
#: journal) : coupe propre, jamais un message tronqué au milieu d'un mot.
_BORNE_DETAIL = 320


def _assainir_detail(texte: str) -> str:
    """Détail fournisseur affichable : une ligne, sans secret ni URL."""
    propre = " ".join(texte.split())
    propre = _SECRETS.sub("***", propre)
    propre = _URL.sub("(adresse du fournisseur)", propre)
    if len(propre) > _BORNE_DETAIL:
        propre = propre[:_BORNE_DETAIL].rstrip() + "…"
    return propre


#: Clé ``message`` d'un corps d'erreur, quel que soit le style de guillemets :
#: un ``repr`` Python mélange souvent les deux (« 'message': "… ':free' …" »).
_MESSAGE_IMBRIQUE = re.compile(
    r"""['"]message['"]\s*:\s*(?:"((?:[^"\\]|\\.)*)"|'((?:[^'\\]|\\.)*)')""",
    re.IGNORECASE,
)


def _desetoffer(valeur: str, guillemet: str) -> str:
    """Défait les échappements d'une chaîne prise dans un corps d'erreur."""
    if guillemet == '"':
        try:
            return json.loads(f'"{valeur}"')
        except ValueError:
            return valeur
    return valeur.replace("\\'", "'").replace("\\\\", "\\")


def _message_imbrique(brut: str) -> str:
    """Message utile porté par une erreur de fournisseur.

    Les SDK joignent le corps JSON du refus à ``str(exc)``
    (``Error code: 429 - {'error': {'message': '…'}}``). On en extrait le
    **message** — le reste (type, code, en-têtes) n'aide pas l'utilisateur.
    À défaut, le texte entier est repris tel quel (Azure : « Your requests to
    gpt-5-mini … exceeded token rate limit. »).
    """
    trouve = _MESSAGE_IMBRIQUE.search(brut)
    if not trouve:
        return brut
    if trouve.group(1) is not None:
        return _desetoffer(trouve.group(1), '"')
    return _desetoffer(trouve.group(2) or "", "'")


#: Code HTTP écrit dans le texte du SDK quand ``status_code`` est absent
#: (``Error code: 502 - …``, cas fréquent des passerelles d'agrégation).
_CODE_TEXTE = re.compile(r"\berror code:\s*(\d{3})\b", re.IGNORECASE)

#: Refus de quota exprimé sans statut (Azure : « … exceeded token rate limit. »).
#: Volontairement étroit : « quota » seul est aussi un mot du domaine métier
#: (budget d'un lot) — une erreur d'outil ne doit pas devenir un quota LLM.
_INDICES_QUOTA = (
    "rate limit",
    "rate_limit",
    "ratelimit",
    "insufficient_quota",
    "insufficient quota",
    "free allowance",
    "allowance for",
    "exceeded token",
    "tokens per minute",
    "tokens per day",
)


def _statut_http(exc: BaseException) -> int | None:
    """Code HTTP d'un refus de fournisseur, quand le SDK en expose un.

    Trois sources, dans l'ordre de fiabilité : l'attribut du SDK, la réponse
    HTTP jointe, puis le **texte** (``Error code: 429``) — les passerelles
    d'agrégation perdent souvent l'attribut et ne laissent que le texte.
    """
    for attribut in ("status_code", "http_status", "code"):
        valeur = getattr(exc, attribut, None)
        if isinstance(valeur, int) and 100 <= valeur <= 599:
            return valeur
    reponse = getattr(exc, "response", None)
    valeur = getattr(reponse, "status_code", None)
    if isinstance(valeur, int) and 100 <= valeur <= 599:
        return valeur
    trouve = _CODE_TEXTE.search(str(exc))
    return int(trouve.group(1)) if trouve else None


def _est_refus_de_quota(brut: str) -> bool:
    """Vrai pour un refus de quota même sans statut HTTP explicite."""
    minuscule = brut.lower()
    return any(indice in minuscule for indice in _INDICES_QUOTA)


def _est_panne_reseau(exc: BaseException, brut: str) -> bool:
    """Vrai pour un transport cassé — et seulement pour lui."""
    if type(exc).__name__ in _CLASSES_RESEAU:
        return True
    # Un refus HTTP porte un statut : c'est le fournisseur qui a répondu, donc
    # pas le réseau, quel que soit le vocabulaire de son message.
    if _statut_http(exc) is not None:
        return False
    minuscule = brut.lower()
    return any(indice in minuscule for indice in _INDICES_RESEAU)


def _message_selon_statut(statut: int, detail: str) -> str:
    """Refus du fournisseur : le statut, le détail du fournisseur, la suite.

    Le détail est repris **tel que le fournisseur l'a écrit** (assaini) : un
    « 429 : free allowance used up, comes back on 16 Nov » dit exactement quoi
    faire, là où « vérifiez la configuration » envoie l'utilisateur au hasard.
    """
    suite = detail if detail else ""
    if statut == 401 or statut == 403:
        return (
            f"Le fournisseur du modèle a refusé la clé d'API ({statut}). {suite} "
            "Vérifiez la clé et le modèle dans Paramètres, puis réessayez."
        ).strip()
    if statut == 429:
        return (
            f"Le fournisseur du modèle a refusé la requête : limite "
            f"d'utilisation ou quota atteint (429). {suite} "
            "Attendez, ou choisissez un autre modèle dans Paramètres."
        ).strip()
    if statut in (400, 404, 405, 413, 415, 422):
        return (
            f"Le fournisseur du modèle a refusé la requête ({statut}). {suite} "
            "Vérifiez le modèle choisi dans Paramètres."
        ).strip()
    if 500 <= statut <= 599:
        return (
            f"Le fournisseur du modèle est en erreur ({statut}). {suite} "
            "Réessayez dans quelques instants."
        ).strip()
    return f"Le fournisseur du modèle a refusé la requête ({statut}). {suite}".strip()


def _code_erreur(exc: BaseException) -> str:
    """Code machine du ``RUN_ERROR``, pour que le client ne re-devine rien.

    Le message porte la cause en français ; le code la rend **stable** :
    l'interface choisit son titre dessus et affiche le message tel quel. Sans
    lui, le client reclassait les messages par mots-clés (« model »,
    « timeout »…) et remplaçait un refus 429 explicite par « vérifiez la
    configuration ».

    Valeurs : ``silence_fournisseur``, ``quota_fournisseur``,
    ``cle_refusee``, ``requete_refusee``, ``fournisseur_en_erreur``,
    ``modele_injoignable``, ``erreur_run``.
    """
    brut = str(exc).strip().lower()
    if type(exc).__name__ == "StreamChunkTimeoutError" or (
        "no streaming chunk received" in brut
    ):
        return "silence_fournisseur"
    statut = _statut_http(exc)
    if statut is None and _est_refus_de_quota(str(exc)) and not _est_panne_reseau(
        exc, str(exc)
    ):
        statut = 429
    if statut is not None:
        if statut in (401, 403):
            return "cle_refusee"
        if statut == 429:
            return "quota_fournisseur"
        if 500 <= statut <= 599:
            return "fournisseur_en_erreur"
        return "requete_refusee"
    if _est_panne_reseau(exc, str(exc).strip()):
        return "modele_injoignable"
    return "erreur_run"


def _message_erreur(exc: BaseException) -> str:
    """Message du modèle d'échec : lisible, sans secret (AGENTS.md §9).

    Trois familles, trois traitements :

    1. **silence du flux** — la connexion vit, la génération s'est arrêtée ;
    2. **refus du fournisseur** (401/429/400/5xx…) — le message du fournisseur
       est repris, c'est lui qui dit la vraie cause (quota, modèle inconnu,
       clé refusée) ;
    3. **panne de transport** (DNS, TLS, socket) — traduite en français, la
       traceback brute restant dans les logs serveur.
    """
    brut = str(exc).strip()
    # Silence du flux (``StreamChunkTimeoutError`` de langchain_openai, borné par
    # ``AGENT_STREAM_CHUNK_TIMEOUT``) : la connexion est vivante, le fournisseur
    # a simplement cessé d'émettre. Ce n'est pas une panne réseau — le dire
    # « injoignable » enverrait l'utilisateur vérifier sa connectivité.
    if type(exc).__name__ == "StreamChunkTimeoutError" or (
        "no streaming chunk received" in brut.lower()
    ):
        outil = _TOOL_DERNIER.get()
        echec = bool(outil and outil[1] == "__echec__")
        if outil and echec:
            return (
                f"Le modèle a appelé l'outil « {outil[0]} » avec une entrée "
                "refusée et s'est arrêté avant de produire le document. "
                "Corrigez la demande à partir du message d'erreur de l'outil, "
                "puis relancez."
            )
        suffixe = (
            f" (dernier outil exécuté : « {outil[0]} »)" if outil else ""
        )
        return (
            "Le modèle d'IA a cessé d'émettre sa réponse en cours de route "
            f"(silence du fournisseur){suffixe}. Relancez la demande ; si le "
            "problème se répète, demandez une réponse plus courte."
        )
    statut = _statut_http(exc)
    if statut is None and _est_refus_de_quota(brut) and not _est_panne_reseau(exc, brut):
        statut = 429  # quota sans statut : même traitement qu'un 429 explicite
    if statut is not None:
        detail = _assainir_detail(_message_imbrique(brut)) if brut else ""
        return _message_selon_statut(statut, detail)
    if _est_panne_reseau(exc, brut):
        return (
            "Le modèle d'IA est injoignable (réseau ou fournisseur indisponible). "
            "Vérifiez la connexion du serveur et la configuration dans "
            "Paramètres, puis réessayez."
        )
    if brut:
        return _assainir_detail(brut)
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
        # Le contexte est lié à CETTE tâche : aucun croisement entre comptes.
        _TOOL_DERNIER.set(_dernier_outil(input_data))
        try:
            async for event in self._interne.run(input_data):
                yield event
        except BaseException as exc:
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            # exc_info : sans pile, une erreur de run (TypeError de reprise
            # HITL constatée en recette) est indélocalisable — le message seul
            # ne dit ni le fichier ni la ligne. Le traceback n'atteint jamais
            # l'utilisateur, il ne vit que dans le journal serveur.
            _logger.warning(
                "Run AG-UI interrompu : %s: %s",
                type(exc).__name__,
                exc,
                exc_info=exc,
            )
            if RunErrorEvent is not None:
                yield RunErrorEvent(
                    message=_message_erreur(exc), code=_code_erreur(exc)
                )


def chemin_agui(agent_id: str, api_prefix: str) -> str:
    """Chemin HTTP d'exposition d'un agent."""
    return f"{api_prefix}/agui/{agent_id}"


def _base_agui(api_prefix: str) -> str:
    """Préfixe commun des endpoints AG-UI (hors catalogue)."""
    return f"{api_prefix}/agui/"


@router.get("/", response_model=list[AgentCatalogueItem])
def lister_agents_agui(user: CurrentUser) -> list[AgentCatalogueItem]:
    """Catalogue des agents **ouverts à ce compte** — configuration effective.

    Un formateur ne voit que les agents de son contrat (assistant formateur,
    généraliste) : l'interface ne propose pas ce que le backend refuserait.
    """
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
        for definition in agents_autorises(user, AGENT_DEFINITIONS)
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
    """Appelle l'API d'``ag-ui-langgraph`` en tolérant ses variantes.

    La garde de périmètre (``_GARDE_AGUI``) est posée à chaque montage : le
    catalogue et les endpoints partagent la même règle de rôle.
    """
    add_endpoint = agui.add_langgraph_fastapi_endpoint
    wrapper = getattr(agui, "LangGraphAGUIAgent", None) or getattr(
        agui, "LangGraphAgent", None
    )
    if wrapper is not None:
        try:
            agent = wrapper(
                name=definition.agent_id,
                description=definition.description,
                # Topologie vide : ne résout pas le graphe (donc aucune base)
                # pendant le montage ; le proxy est rendu juste après.
                graph=_GrapheSansTopologie(),
                config=_config_agui(),
                # Interruptions HITL : émettre l'outcome structuré AG-UI
                # (RUN_FINISHED outcome.type="interrupt") pour que le frontend
                # CopilotKit v2 rende le questionnaire et reprenne via
                # RunAgentInput.resume[]. Le legacy on_interrupt est désactivé
                # : un seul canal, moins d'ambiguïté côté client.
                emit_interrupt_outcome=True,
                enable_legacy_on_interrupt_event=False,
            )
            agent.graph = graph
            # Au plus près de l'agent : le résumé de session du middleware de
            # summarisation (``lc_source="summarization"``) est un appel de
            # modèle **interne**, diffusé dans le même nœud que la réponse. Sans
            # ce filtre, il apparaît en tête de la bulle de l'assistant
            # (voir app/api/agui_resume_interne.py).
            agent = ResumeInterneMasque(agent)
            # Les providers réutilisent call_0 / call_1 d'un tour à l'autre.
            # On réécrit uniquement les ids d'appels d'outils (hors reprise
            # HITL ancien canal) : les ids de messages appartiennent au
            # reminting interne d'ag-ui-langgraph, toute réécriture en plus
            # désappariait le flux et tronquait la fin de stream (voir
            # app/api/agui_ids.py).
            agent = AgentFluxIdsUniques(agent)
            # La fin brutale d'un run (provider coupé…) devient un événement
            # RUN_ERROR : le client affiche l'échec au lieu d'un flux tronqué.
            # Un questionnaire HITL resté sans réponse bloquait le fil : tout
            # run sans `resume` sur un fil interrompu était court-circuité par
            # ag-ui-langgraph (message jamais traité). La purge refuse les
            # actions en attente avant de déléguer (voir agui_interrupt_orphelin).
            #
            # Le classificateur est celui du pont : une panne de **run**
            # survenue dans la reprise (le fournisseur répond 503 pendant que
            # la purge relance le graphe) est relancée, pas avalée. Elle
            # consomme un appel modèle à l'utilisateur — la masquer faisait
            # réussir le tour en silence (constat 16/11, sonde
            # scripts/repro_histoire_apres_503.py).
            agent = PurgeInterruptOrphelin(agent, graph, classer_erreur=_code_erreur)
            # Posé **après** la purge, donc à l'extérieur d'elle : c'est ce pont
            # qui transforme l'échec de la purge en RUN_ERROR visible.
            agent = StreamingRunError(agent)
            # Un run coupé en plein appel d'outil (flux SSE du fournisseur
            # interrompu) laisse des arguments JSON cassés dans l'historique du
            # client : ag-ui-langgraph les relisait à chaque tour (JSONDecodeError
            # avant même d'appeler le modèle) et le fil restait empoisonné.
            # L'entrée est assainie avant d'être relayée (voir agui_historique).
            agent = HistoriqueArgumentsSains(agent)
            # Dernier filet : chaque appel d'outil doit recevoir un résultat. Un
            # run coupé en plein appel (ou un appel déclaré jamais exécuté)
            # laissait une carte « Préparation » à vie dans le fil ; l'appel est
            # clos par une erreur explicite juste avant la fin du run (voir
            # app/api/agui_outils_complets.py). Posé en dernier : les ids sont
            # déjà publics et l'erreur de run déjà émise.
            agent = ResultatsOutilsComplets(agent)
            _monter_endpoint(add_endpoint, app, agent, path, definition.agent_id)
            return
        except TypeError:
            pass
    _monter_endpoint(add_endpoint, app, graph, path, definition.agent_id)


def _monter_endpoint(
    add_endpoint: Any, app: FastAPI, sujet: Any, path: str, agent_id: str
) -> None:
    """Monte un endpoint AG-UI **avec** la garde de périmètre de cet agent.

    ``ag-ui-langgraph`` transmet ses mots-clés à ``app.post`` : ``dependencies``
    est donc accepté et la garde est installée. Les variantes de signature sont
    tolérées (comme avant) ; si une version de l'extra refusait ce mot-clé,
    l'endpoint serait monté **sans** contrôle de rôle — l'incident est alors
    journalisé en avertissement plutôt que passé sous silence. L'extra est
    épinglé : ce chemin est un filet de compatibilité, pas la voie normale.

    ``agent_id`` est capturé par la garde (``garde_agui``) : le path étant un
    littéral, il ne peut pas venir d'un paramètre de chemin.

    Raises:
        TypeError: aucune signature connue acceptée — l'appelant journalise
            l'échec de montage de cet agent et continue.
    """
    garde: list[Any] = [Depends(garde_agui(agent_id))]
    essais_gardes: tuple[tuple[tuple[Any, ...], dict[str, Any]], ...] = (
        ((), {"app": app, "agent": sujet, "path": path, "dependencies": garde}),
        ((app, sujet, path), {"dependencies": garde}),
    )
    for args, kwargs in essais_gardes:
        try:
            add_endpoint(*args, **kwargs)
            return
        except TypeError:
            continue
    _logger.warning(
        "AG-UI : `dependencies` refusé par ag-ui-langgraph — %s monté sans "
        "garde de rôle (mise à jour de l'extra requise)",
        path,
    )
    essais_nus: tuple[tuple[tuple[Any, ...], dict[str, Any]], ...] = (
        ((), {"app": app, "agent": sujet, "path": path}),
        ((app, sujet, path), {}),
    )
    for args, kwargs in essais_nus:
        try:
            add_endpoint(*args, **kwargs)
            return
        except TypeError:
            continue
    raise TypeError(f"ag-ui-langgraph : signature d'ajout d'endpoint inconnue ({path})")


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
            # **Proxy** et non le graphe lui-même : l'endpoint est monté une
            # fois au démarrage, sans utilisateur. Le graphe doit donc être
            # résolu à chaque run, dans le contexte du compte appelant — c'est
            # ce qui rend les connecteurs personnels actifs ici. Résoudre ici
            # figerait le contexte global pour toute la vie du process.
            graphe = GrapheParUtilisateur(definition.agent_id)
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
