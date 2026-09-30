"""Harnais Deep Agents (instruction/05 §4) — AgentFactory, AgentRegistry, garde-fous.

L'indépendance métier des agents ne signifie pas duplication technique : ce
module est l'infrastructure commune qui transforme un ``AgentDefinition``
(contrat) en agent exécutable ``create_deep_agent`` (Deep Agents / LangGraph).

Ce que le harnais garantit :

1. **Allow-list de tools** — un agent ne reçoit que les tools déclarés dans son
   contrat ; tout autre tool fourni est refusé (moindre privilège, AGENTS.md §9).
2. **Backend sûr** — par défaut ``StateBackend`` : les tools fichiers du harness
   écrivent dans l'état du graphe, jamais sur le disque hôte (instruction/08 §4/§5).
3. **Approvisionnement humain** — les tools listés dans
   ``AgentDefinition.approval_required`` passent par ``interrupt_on`` : LangGraph
   interrompt avant exécution et attend une décision humaine. Cela impose un
   checkpointer, fourni automatiquement (``MemorySaver``) en mode interactif.
4. **Sortie structurée** — ``response_format`` Pydantic, jamais une réponse libre
   consommée par un service (instruction/05 §11).
5. **Provider configurable** — le modèle vient de la configuration runtime
   (``app/agents/providers.py``), pas du code de l'agent.

Les paquets ``deepagents`` / ``langchain`` sont importés paresseusement : le
backend démarre sans l'extra ``agents``, et un appel sans les paquets produit une
erreur explicite.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from app.agents.annuaire import bloc_annuaire
from app.agents.base import AgentDefinition
from app.agents.frontend_tools_catalogue import bloc_outils_frontend
from app.agents.pieces_jointes_contexte import BLOC_PIECES_JOINTES
from app.agents.providers import AgentConfigurationError, build_chat_model
from app.core.config import Settings, get_settings
from app.core.errors import ValidationError as BusinessValidationError
from app.tools.base import TypedTool, to_langchain_tool

#: Cadre commun non négociable, ajouté au prompt de chaque agent.
_CADRE_CARSO = """
## Cadre CARSO (non négociable)
- Tu produis des PROPOSITIONS. Aucune validation humaine n'est remplacée :
  un humain approuve ou refuse avant qu'une donnée devienne officielle.
- Tu n'inventes jamais une information absente de la source (règle métier,
  champ obligatoire, montant, disponibilité). Information manquante => tu le
  signales explicitement dans ta réponse.
- Tu n'écris, ne demandes et ne divulgues aucun secret (clé, mot de passe, jeton).
- Tu n'utilises que les tools fournis (métier + affichage listés ci-dessous).
  Tu n'en réclames pas d'autres.
- Tu restes strictement dans ton périmètre métier.
- Un document demandé sans objet métier identifié (organisation, appel, offre,
  mission, équipe, session) se génère quand même : omets l'ancre, n'invente
  aucun UUID. Après `generer_document`, appelle
  `proposer_telechargement_document` pour que l'utilisateur télécharge.
""".strip()


@dataclass(frozen=True)
class AgentRuntimeConfig:
    """Paramètres d'exécution, indépendants du contrat métier de l'agent.

    Attributes:
        model: instance de modèle déjà construite (prioritaire sur la config).
        backend: backend de fichiers du harness (par défaut ``StateBackend``).
        checkpointer: persistance LangGraph (obligatoire pour les interruptions).
        interactive: active l'approbation humaine interactive sur les tools
            déclarés ``approval_required``. Sans checkpointer fourni, un
            ``MemorySaver`` est utilisé.
        permissions: règles de fichiers (``FilesystemPermission``) si l'on veut
            restreindre le backend au-delà du défaut.
        max_iterations: borne d'itérations du graphe (anti-boucle).
        debug: verbosité LangGraph.
        definition: contrat **effectif** à utiliser lorsqu'il diffère de celui du
            code (surcharge administrée : outils retirés, instructions ajoutées,
            skills ajoutés). ``None`` = contrat déclaré, inchangé.
        extra_tools: outils LangChain ajoutés **en plus** des tools du contrat.
            Ils viennent d'une déclaration explicite de l'administrateur (un
            connecteur MCP autorisant cet agent), jamais du modèle — c'est
            pourquoi ils ne passent pas par l'allow-list du contrat, qui garde
            son rôle : refuser un tool que l'agent n'a pas déclaré.
    """

    model: Any | None = None
    backend: Any | None = None
    checkpointer: Any | None = None
    interactive: bool = False
    permissions: Sequence[Any] | None = None
    max_iterations: int | None = None
    debug: bool = False
    definition: AgentDefinition | None = None
    extra_tools: tuple[Any, ...] = ()


def build_system_prompt(definition: AgentDefinition, *, extra: str | None = None) -> str:
    """Prompt système d'un agent : instructions + cadre commun + annuaire.

    L'annuaire (refactor collaboration) liste les agents que celui-ci peut
    appeler via ``request_agent_task`` (identifiant + description + types de
    tâche), issus de ses ``CollaborationGrant`` — rien d'inventé. Vide pour
    un agent sans collaboration. Le bloc d'affichage liste les outils
    frontend réellement enregistrés pour cet agent — jamais un nom inventé.
    """
    annuaire = bloc_annuaire(definition)
    affichage = bloc_outils_frontend(definition.agent_id)
    parties = [
        definition.prompt.strip() or definition.description.strip(),
        _CADRE_CARSO,
        BLOC_PIECES_JOINTES,
        affichage,
        annuaire,
    ]
    if extra:
        parties.append(extra.strip())
    return "\n\n".join(partie for partie in parties if partie)


def create_carso_deep_agent(
    *,
    definition: AgentDefinition,
    tools: Sequence[TypedTool],
    runtime: AgentRuntimeConfig | None = None,
    response_format: Any | None = None,
    settings: Settings | None = None,
    system_prompt_extra: str | None = None,
) -> Any:
    """Construit l'agent exécutable (AgentFactory) d'un agent métier CARSO.

    Args:
       
        definition: contrat de l'agent (identité, prompt, tools, approbations).
        tools: tools typés **fournis** à l'agent — sous-ensemble déclaré.
        runtime: paramètres d'exécution (modèle, backend, interruptions).
        response_format: schéma Pydantic de sortie structurée.
        settings: configuration (provider/modèle) si ``runtime.model`` est absent.
        system_prompt_extra: précisions d'exécution ajoutées au prompt.

    Returns:
        Le graphe LangGraph compilé prêt à ``.invoke()``.

    Raises:
        BusinessValidationError: tool non déclaré, tool dupliqué ou tool non typé.
        AgentConfigurationError: provider/modèle/clé manquant, ou extra
            ``agents`` non installé.
    """
    config = runtime or AgentRuntimeConfig()
    # Contrat effectif : une surcharge administrée peut avoir **désactivé** des
    # outils ou ajouté des instructions. Un outil désactivé est une décision
    # légitime de l'administrateur : l'agent doit monter **sans** cet outil —
    # on l'écarte ici, pas d'erreur. La garde stricte (``allowed_tools_only``)
    # reste en place pour le cas d'une erreur de code : un tool **jamais
    # déclaré** au contrat ne peut pas se glisser dans le graphe.
    definition = config.definition or definition
    if config.definition is not None:
        declares = set(definition.tools)
        tools = tuple(tool for tool in tools if tool.name in declares)
    tools_autorises = allowed_tools_only(definition, tools)

    deux_paquets = _import_deepagents()
    create_deep_agent = deux_paquets["create_deep_agent"]
    # Pas de modèle au montage si la config est incomplète : le graphe
    # existe quand même ; l'invocation échouera explicitement plus tard.
    from app.agents.providers import is_agent_configured

    if config.model is not None:
        modele = config.model
    elif is_agent_configured(settings):
        modele = build_chat_model(settings)
    else:
        modele = None
    interrupt_on = _politique_approbation(definition, tools, config)
    checkpointer = _checkpointer(config, interrupt_on)

    from app.agents.skills import resoudre_skills

    skills_resolus = resoudre_skills(definition.skills, settings=settings)
    kwargs: dict[str, Any] = {
        "name": definition.agent_id,
        "model": modele,
        "tools": [
            *(to_langchain_tool(tool) for tool in tools_autorises),
            *config.extra_tools,
        ],
        "system_prompt": build_system_prompt(definition, extra=system_prompt_extra),
        "backend": config.backend or _backend_fichiers(settings),
        "permissions": list(config.permissions) if config.permissions else None,
        "interrupt_on": interrupt_on or None,
        "response_format": response_format,
        "checkpointer": checkpointer,
        "debug": config.debug,
    }
    # SkillsMiddleware exige un backend filesystem. StateBackend suffit :
    # les skills sont lus au chargement, pas écrits par l'agent.
    if skills_resolus:
        kwargs["skills"] = skills_resolus
    middleware = _middleware_frontend(definition.agent_id)
    if middleware is not None:
        kwargs["middleware"] = [middleware]
    return create_deep_agent(**kwargs)


def recursion_limit(runtime: AgentRuntimeConfig | None, settings: Settings | None = None) -> int:
    """Borne d'itérations à passer au ``config`` d'invocation LangGraph."""
    if runtime is not None and runtime.max_iterations is not None:
        return runtime.max_iterations
    from app.core.config import get_settings

    return (settings or get_settings()).agent_max_iterations


class AgentRegistry:
    """Registre des contrats d'agents (instruction/05 §4).

    Volontairement sans logique de coordination : il permet à une interface de
    découvrir les agents disponibles et leurs capacités — il n'orchestre rien.
    """

    def __init__(self) -> None:
        self._definitions: dict[str, AgentDefinition] = {}

    def register(self, definition: AgentDefinition) -> AgentDefinition:
        """Enregistre un contrat ; refuse un identifiant déjà pris."""
        if definition.agent_id in self._definitions:
            msg = f"Agent déjà enregistré : {definition.agent_id!r}"
            raise BusinessValidationError(msg)
        self._definitions[definition.agent_id] = definition
        return definition

    def get(self, agent_id: str) -> AgentDefinition:
        """Contrat d'un agent, ou erreur explicite."""
        definition = self._definitions.get(agent_id)
        if definition is None:
            msg = f"Agent inconnu : {agent_id!r}"
            raise BusinessValidationError(msg)
        return definition

    def ids(self) -> list[str]:
        return sorted(self._definitions)

    def __len__(self) -> int:
        return len(self._definitions)


# --- internes ---------------------------------------------------------------


def _backend_fichiers(settings: Settings | None):
    """Backend de fichiers du harnais : état du graphe + storage CARSO routé.

    Correction du sandbox (refactor, docs/agents/REFACTOR-OUTILS-AGENTS.md §4) :
    avec le seul ``StateBackend``, les tools fichiers du harness ne voient que
    l'état virtuel du graphe — le modèle tentait des chemins Windows réels,
    était rejeté et bouclait ("chemins virtuels du workspace").

    Désormais : ``CompositeBackend`` route le préfixe ``/storage/`` vers un
    ``FilesystemBackend`` **rooté sur ``STORAGE_ROOT``** (``virtual_mode=True`` :
    path-traversal bloqué, le modèle ne voit que des chemins virtuels stables
    sous ``/storage/...``). Tout le reste reste en mémoire d'état (artifacts,
    fichiers de travail du graphe). Aucun chemin libre hors storage.
    """
    try:
        from deepagents.backends import CompositeBackend, FilesystemBackend, StateBackend
    except ImportError:  # pragma: no cover - extra agents absent
        return state_backend_fallback()

    racine_storage = (settings or get_settings()).storage_root
    from pathlib import Path

    chemin = Path(racine_storage)
    if not chemin.is_absolute():
        chemin = Path.cwd() / chemin
    try:
        chemin.mkdir(parents=True, exist_ok=True)
        fs = FilesystemBackend(root_dir=str(chemin), virtual_mode=True)
        return CompositeBackend(default=StateBackend(), routes={"/storage/": fs})
    except OSError:  # pragma: no cover - storage non montable
        return StateBackend()


def state_backend_fallback():
    """StateBackend pur (extra agents absent ou storage indisponible)."""
    from deepagents.backends import StateBackend

    return StateBackend()


def _middleware_frontend(agent_id: str) -> Any | None:
    """Middleware d'affichage : stubs allowlistés, hors contrat métier.

    Absent (``None``) si l'agent n'a aucun outil frontend — l'analyseur
    interne n'en a pas. Import paresseux : le backend démarre sans Deep Agents.
    """
    from app.agents.frontend_tools_catalogue import noms_pour_agent

    if not noms_pour_agent(agent_id):
        return None
    from app.agents.frontend_tools_middleware import FrontendToolsMiddleware

    return FrontendToolsMiddleware(agent_id)


def _import_deepagents() -> dict[str, Any]:
    """Importe Deep Agents paresseusement (extra ``agents``)."""
    try:
        from deepagents import create_deep_agent
        from deepagents.backends import StateBackend
    except ImportError as exc:  # pragma: no cover - dépend de l'installation
        msg = (
            "Deep Agents n'est pas installé : installer l'extra agents "
            "(pip install -e '.[agents]')"
        )
        raise AgentConfigurationError(msg, details={"paquet": "deepagents"}) from exc

    return {"create_deep_agent": create_deep_agent, "StateBackend": StateBackend}


def allowed_tools_only(
    definition: AgentDefinition, tools: Sequence[TypedTool]
) -> tuple[TypedTool, ...]:
    """Contrôle d'accès : ne renvoie que les tools déclarés dans le contrat.

    Fonction publique et sans effet de bord : elle constitue le point unique où
    l'allow-list d'un agent est appliquée, et reste testable sans construire de
    graphe ni appeler de modèle.

    Raises:
        BusinessValidationError: tool non déclaré ou fourni deux fois.
    """
    autorises = definition.allowed_tools()
    retenus: list[TypedTool] = []
    vus: set[str] = set()

    for tool in tools:
        if tool.name in vus:
            msg = f"Tool fourni deux fois à {definition.agent_id!r} : {tool.name!r}"
            raise BusinessValidationError(msg)
        vus.add(tool.name)

        if tool.name not in autorises:
            msg = (
                f"Tool {tool.name!r} non déclaré dans le contrat de "
                f"{definition.agent_id!r} (autorisés : {sorted(autorises)})"
            )
            raise BusinessValidationError(msg, details={"tool": tool.name})

        retenus.append(tool)

    return tuple(retenus)


def _politique_approbation(
    definition: AgentDefinition, tools: Sequence[TypedTool], config: AgentRuntimeConfig
) -> dict[str, Any]:
    """``interrupt_on`` : tools métier sensibles + outils frontend HITL."""
    if not config.interactive:
        return {}
    fournis = {tool.name for tool in tools}
    politique: dict[str, Any] = {
        nom: True for nom in definition.approval_required if nom in fournis
    }
    from app.agents.frontend_tools_catalogue import politique_interrupt_frontend

    politique.update(politique_interrupt_frontend(definition.agent_id))
    return politique


def _checkpointer(config: AgentRuntimeConfig, interrupt_on: dict[str, Any]) -> Any:
    """Un checkpointer est obligatoire pour les interruptions **et** pour AG-UI.

    Deux raisons, dont une seule dépend des interruptions :

    1. HITL : une interruption LangGraph ne peut se reprendre sans
       checkpointer (persistance de l'état suspendu) ;
    2. AG-UI (``ag-ui-langgraph``) : l'adaptateur appelle ``aget_state``
       au premier tour d'un fil pour réhydrater l'historique du thread —
       sans checkpointer le run échoue avec « No checkpointer set » même
       quand aucun tool sensible n'est en jeu.

    Un ``MemorySaver`` est donc fourni **par défaut** ; un checkpointer
    persistant (PostgreSQL) peut le remplacer via ``config.checkpointer``
    quand les fils doivent survivre au processus.
    """
    if config.checkpointer is not None:
        return config.checkpointer

    try:
        from langgraph.checkpoint.memory import MemorySaver
    except ImportError as exc:  # pragma: no cover - dépend de l'installation
        msg = "LangGraph n'est pas installé : installer l'extra agents"
        raise AgentConfigurationError(msg, details={"paquet": "langgraph"}) from exc

    return MemorySaver()


__all__ = [
    "AgentRegistry",
    "AgentRuntimeConfig",
    "allowed_tools_only",
    "build_system_prompt",
    "create_carso_deep_agent",
    "recursion_limit",
]
