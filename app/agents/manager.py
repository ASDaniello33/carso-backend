"""Runtime des agents rechargeable à chaud — sans redémarrage du backend.

Objectif (instruction/05 §3) : le **modèle** est une décision de déploiement,
pas une ligne de code métier. L'administrateur doit pouvoir :

1. consulter la configuration effective des agents ;
2. la **changer à chaud** (provider / modèle / ``base_url`` / température / borne
   d'itérations) sans redémarrer l'application ;
3. **redémarrer le runtime d'agents** seul (graphes reconstruits) sans toucher au
   processus FastAPI ;
4. retrouver automatiquement cette configuration au démarrage suivant : elle est
   persistée en base (``configurations_runtime_agent``).

Ce que ce module **ne fait pas** : aucun agent n'est orchestré par un
coordinateur (AGENTS.md §1.6). Le manager est de l'infrastructure : il détient la
configuration effective et les graphes compilés, il ne décide rien à la place des
agents.

Sécurité (instruction/08 §6) : la clé du provider (``AGENT_API_KEY``) reste dans
``backend/.env`` sous forme de ``SecretStr``. Elle n'est **jamais** persistée,
journalisée ni renvoyée par l'API. Seuls des paramètres non sensibles sont
modifiables à chaud.

Concurrence : les graphes sont construits et libérés sous un ``RLock`` unique.
Les tools des agents ouvrent **leur propre transaction** par appel
(``session_factory``), donc remplacer un graphe ne laisse pas de session partagée
entre deux conversations.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from threading import RLock
from typing import Any

from sqlalchemy.orm import Session

from app.agents.harness import AgentRuntimeConfig
from app.agents.providers import build_chat_model, is_agent_configured
from app.core.config import Settings, get_settings
from app.core.errors import ValidationError as BusinessValidationError

_logger = logging.getLogger(__name__)

#: Fabrique d'agent : reçoit la session de **construction** (les tools ouvrent la
#: leur propre transaction) et renvoie l'objet agent exposant ``build_agent``.
AgentBuilder = Callable[[Session], Any]


@dataclass(frozen=True, slots=True)
class AgentRuntimeValues:
    """Configuration effective du runtime, sans secret.

    Attributes:
        source: ``"base"`` (configuration administrée, persistée) ou ``"env"``
            (configuration de déploiement ``.env``).
    """

    provider: str
    model: str
    base_url: str | None
    temperature: float
    max_iterations: int
    source: str
    #: Vrai si une clé **administrée** est enregistrée (chiffrée en base).
    #: La valeur elle-même n'entre jamais dans cette projection.
    cle_administree: bool = False

    def as_dict(self) -> dict[str, Any]:
        """Projection sûre pour l'API (aucun secret, aucun objet interne)."""
        return {
            "provider": self.provider,
            "model": self.model,
            "base_url": self.base_url,
            "temperature": self.temperature,
            "max_iterations": self.max_iterations,
            "source": self.source,
            "cle_administree": self.cle_administree,
        }


class AgentRuntimeManager:
    """Détient la configuration effective et les graphes compilés des agents."""

    def __init__(self, builders: dict[str, AgentBuilder] | None = None) -> None:
        self._builders: dict[str, AgentBuilder] = dict(builders or {})
        self._lock = RLock()
        self._values: AgentRuntimeValues | None = None
        #: Clé administrée déchiffrée, gardée en mémoire seulement. Jamais
        #: journalisée, jamais projetée dans l'état exposé à l'API.
        self._api_key: str | None = None
        self._graphs: dict[str, Any] = {}
        self._sessions: dict[str, Session] = {}
        self._listeners: list[Callable[[], None]] = []
        self._last_agents: tuple[str, ...] = ()
        self._last_error: str | None = None

    # --- Contrat de découverte ------------------------------------------------

    def register_builder(self, agent_id: str, builder: AgentBuilder) -> None:
        """Déclare la fabrique d'un agent exposé par l'interface."""
        with self._lock:
            self._builders[agent_id] = builder

    def agent_ids(self) -> tuple[str, ...]:
        """Identifiants des agents dont le runtime est géré (ordre stable)."""
        with self._lock:
            return tuple(sorted(self._builders))

    def add_listener(self, listener: Callable[[], None]) -> None:
        """Enregistre un rappel déclenché après chaque reconstruction du runtime.

        Utilisé par l'exposition AG-UI pour remonter ses endpoints sur les
        nouveaux graphes — sans que le manager ne connaisse FastAPI.
        """
        with self._lock:
            if listener not in self._listeners:
                self._listeners.append(listener)

    # --- Configuration effective ---------------------------------------------

    def values(self) -> AgentRuntimeValues | None:
        """Configuration effective, ou ``None`` si aucun modèle n'est exploitable."""
        with self._lock:
            if self._values is not None:
                return self._values
        return _valeurs_env()

    def effective_settings(self) -> Settings:
        """Réglages appliqués au runtime : base ``.env`` + surcharge administrée.

        ``Settings`` est mis en cache par ``get_settings()`` : on renvoie une
        **copie** surchargée plutôt que de muter le singleton (aucun effet de bord
        sur le reste de l'application).
        """
        base = get_settings()
        with self._lock:
            valeurs = self._values
            cle = self._api_key
        if valeurs is None:
            return base
        champs: dict[str, Any] = {
            "agent_provider": valeurs.provider,
            "agent_model": valeurs.model,
            "agent_base_url": valeurs.base_url,
            "agent_temperature": valeurs.temperature,
            "agent_max_iterations": valeurs.max_iterations,
        }
        if cle:
            # Clé administrée : elle prime sur celle du ``.env``. Le runtime ne
            # la connaît qu'ici, et seulement déchiffrée.
            from pydantic import SecretStr

            champs["agent_api_key"] = SecretStr(cle)
        return base.model_copy(update=champs)

    def is_configured(self) -> bool:
        """Vrai si un modèle d'agent est exploitable avec la configuration effective."""
        return is_agent_configured(self.effective_settings())

    # --- Application / rechargement ------------------------------------------

    def apply_values(self, valeurs: AgentRuntimeValues) -> tuple[str, ...]:
        """Applique une configuration et reconstruit le runtime."""
        with self._lock:
            self._values = valeurs
            return self._reload_locked()

    def apply_persisted(self, ligne: Any) -> tuple[str, ...]:
        """Applique une ligne ``ConfigurationRuntimeAgent`` persistée.

        La clé administrée (si présente) est déchiffrée ici et **seulement** ici :
        un jeton illisible (clé de chiffrement changée) retombe sur la clé du
        ``.env`` plutôt que de faire échouer le runtime avec une valeur vide.
        """
        from app.core.chiffrement import dechiffrer

        cle = dechiffrer(getattr(ligne, "api_key_chiffree", None))
        with self._lock:
            self._api_key = cle
        return self.apply_values(
            AgentRuntimeValues(
                provider=ligne.provider,
                model=ligne.model,
                base_url=ligne.base_url,
                temperature=float(ligne.temperature),
                max_iterations=int(ligne.max_iterations),
                source="base",
                cle_administree=bool(ligne.api_key_chiffree),
            )
        )

    def reset_to_env(self) -> tuple[str, ...]:
        """Abandonne la surcharge administrée et revient à la configuration ``.env``."""
        with self._lock:
            self._values = None
            self._api_key = None
            return self._reload_locked()

    def reload(self) -> tuple[str, ...]:
        """Redémarre le runtime (graphes reconstruits) sans redémarrer le backend."""
        with self._lock:
            return self._reload_locked()

    def restore_from_db(self, session: Session) -> bool:
        """Charge la dernière configuration persistée et l'applique.

        Appelé au démarrage : le système « mémorise » la configuration. Toute
        erreur (base indisponible, table absente) est **journalisée sans faire
        échouer le démarrage** : le backend reste utilisable avec ``.env``.
        """
        from app.application.services import AgentRuntimeService

        try:
            ligne = AgentRuntimeService(session).obtenir_active()
        except Exception as exc:  # pragma: no cover - dépend de l'environnement
            _logger.warning("Configuration des agents non rechargée : %s", exc)
            return False

        if ligne is None:
            return False

        try:
            self.apply_persisted(ligne)
        except Exception as exc:  # pragma: no cover - dépend de l'environnement
            _logger.warning("Configuration des agents persistée inapplicable : %s", exc)
            return False

        _logger.info(
            "Configuration des agents restaurée depuis la base (provider=%s, model=%s)",
            ligne.provider,
            ligne.model,
        )
        return True

    # --- Graphes ---------------------------------------------------------------

    def graph(self, agent_id: str) -> Any:
        """Graphe compilé d'un agent, construit à la demande puis mémorisé."""
        with self._lock:
            existant = self._graphs.get(agent_id)
            if existant is not None:
                return existant

            builder = self._builders.get(agent_id)
            if builder is None:
                msg = f"Agent inconnu du runtime : {agent_id!r}"
                raise BusinessValidationError(msg, details={"agent_id": agent_id})

            from app.infrastructure.database import SessionLocal

            session = SessionLocal()
            try:
                settings = self.effective_settings()
                modele = None
                if is_agent_configured(settings):
                    modele = build_chat_model(settings)
                # Contrat effectif (surcharge administrée) et outils distants
                # (connecteurs MCP autorisant **cet** agent). Résolus AVANT le
                # builder : le harnais doit recevoir le contrat effectif ET les
                # instructions administrées — un réglage posé dans Paramètres
                # s'applique au prochain graphe, sans redémarrage.
                definition, outils_distants, instructions = _effectif_pour(
                    session, agent_id
                )
                # Checkpointer **partagé** (ADR 0009) : PostgresSaver process-
                # wide quand la persistance est établie — les threads (contexte
                # de conversation, HITL en attente) survivent aux rebuilds et
                # aux redémarrages. ``None`` = MemorySaver par graphe
                # (fallback harnais, comportement historique).
                from app.agents.checkpointer import get_checkpointer

                runtime = AgentRuntimeConfig(
                    # Modèle résolu ici s'il est configuré ; sinon le harnais
                    # le construira à l'invocation (pas au lifespan).
                    model=modele,
                    interactive=True,
                    max_iterations=settings.agent_max_iterations,
                    definition=definition,
                    extra_tools=outils_distants,
                    checkpointer=get_checkpointer(),
                )
                agent = builder(session)
                # ``instructions`` passe en supplément du prompt système : les
                # agents construisent avec leur contrat déclaré (self.definition),
                # le runtime porte le contrat effectif et le harnais reçoit les
                # deux. Les instructions brutes sont les consignes administrées.
                graphe = agent.build_agent(
                    runtime=runtime, system_prompt_extra=instructions
                )
            except Exception:
                session.close()
                raise

            self._sessions[agent_id] = session
            self._graphs[agent_id] = graphe
            return graphe

    def state(self) -> dict[str, Any]:
        """État du runtime pour l'API d'administration (aucun secret)."""
        with self._lock:
            valeurs = self._values or _valeurs_env()
            return {
                "configured": self.is_configured(),
                "values": valeurs.as_dict() if valeurs else None,
                "agents": list(self.agent_ids()),
                "built_agents": list(self._last_agents),
                "last_error": self._last_error,
            }

    def release(self) -> None:
        """Libère graphes et sessions (arrêt de l'application)."""
        with self._lock:
            self._close_graphs()

    # --- internes --------------------------------------------------------------

    def _reload_locked(self) -> tuple[str, ...]:
        self._close_graphs()
        self._last_agents = ()
        self._last_error = None

        # Toujours notifier AG-UI : l'absence de modèle ne bloque pas le montage.
        if not self.is_configured():
            _logger.info(
                "Aucun modèle agent configuré : graphes construits sans LLM"
            )

        construits: list[str] = []
        for agent_id in sorted(self._builders):
            try:
                self.graph(agent_id)
            except Exception as exc:
                self._last_error = f"{agent_id}: {exc}"
                _logger.warning("Graphe agent non construit (%s) : %s", agent_id, exc)
                continue
            construits.append(agent_id)

        self._last_agents = tuple(construits)
        self._notify_listeners()
        return self._last_agents

    def _close_graphs(self) -> None:
        """Ferme les sessions de construction des graphes remplacés."""
        for session in self._sessions.values():
            try:
                session.close()
            except Exception:  # pragma: no cover - fermeture best-effort
                _logger.debug("Fermeture de session de graphe ignorée")
        self._sessions.clear()
        self._graphs.clear()

    def _notify_listeners(self) -> None:
        for listener in self._listeners:
            try:
                listener()
            except Exception as exc:  # pragma: no cover - un écouteur ne casse rien
                _logger.warning("Écouteur de runtime en échec : %s", exc)


def _effectif_pour(
    session: Session, agent_id: str
) -> tuple[Any | None, tuple[Any, ...], str | None]:
    """Contrat effectif, outils MCP et instructions administrées (jamais bloquant).

    Un incident de lecture (table absente, base indisponible) ne doit pas
    empêcher un agent de fonctionner : on retombe alors sur le contrat déclaré
    et aucun outil distant, en le journalisant.

    Le troisième élément porte les ``instructions_supplementaires`` **brutes** :
    ``definition_effective`` les fusionne dans ``definition.prompt`` (contrat
    résolu), mais les builders construisent leur agent avec le contrat **de
    base** — le harnais reçoit donc ce supplément via ``system_prompt_extra``
    pour qu'il atteigne réellement le prompt système. Sans cela, une consigne
    administrée dans Paramètres restait sans effet jusqu'au redémarrage.
    """
    try:
        from app.agents.surcharges import definition_effective
        from app.application.services.parametres_service import definition_du_contrat
        from app.infrastructure.repositories import (
            ServeurMcpRepository,
            SurchargeAgentRepository,
        )

        surcharge = SurchargeAgentRepository(session).get_par_agent(agent_id)
        base = definition_du_contrat(agent_id)
        definition = definition_effective(base, surcharge)
        instructions = (
            (surcharge.instructions_supplementaires or "").strip() or None
            if surcharge is not None and surcharge.actif
            else None
        )
        serveurs = ServeurMcpRepository(session).lister_pour_agent(agent_id)
        from app.agents.mcp import outils_mcp_pour

        return definition, outils_mcp_pour(agent_id, serveurs), instructions
    except Exception as exc:  # pragma: no cover - dépend de la base
        _logger.warning(
            "Surcharge/connecteurs non résolus pour %s, contrat déclaré utilisé : %s",
            agent_id,
            exc,
        )
        return None, (), None


def _valeurs_env() -> AgentRuntimeValues | None:
    """Configuration issue du ``.env`` (aucune surcharge administrée)."""
    settings = get_settings()
    if not is_agent_configured(settings):
        return None
    return AgentRuntimeValues(
        provider=(settings.agent_provider or "").strip().lower(),
        model=(settings.agent_model or "").strip(),
        base_url=(settings.agent_base_url or "").strip() or None,
        temperature=settings.agent_temperature,
        max_iterations=settings.agent_max_iterations,
        source="env",
    )


def build_default_manager() -> AgentRuntimeManager:
    """Manager câblé sur le runtime (UI + sous-agents internes)."""
    from app.agents.agent_rh import DEFINITION as RH_DEF
    from app.agents.agent_rh import build_rh
    from app.agents.agent_statistique import DEFINITION as STAT_DEF
    from app.agents.agent_statistique import build_statistique
    from app.agents.assistant_formateur import DEFINITION as ASSISTANT_DEF
    from app.agents.assistant_formateur import build_assistant
    from app.agents.generaliste_readonly import DEFINITION as GENERALISTE_DEF
    from app.agents.generaliste_readonly import READ_DOCUMENT_TOOL, build_generaliste
    from app.agents.generateur_offre import DEFINITION as GENERATEUR_DEF
    from app.agents.generateur_offre import build_generateur
    from app.agents.outillage import tools_documents_pour
    from app.infrastructure.database import SessionLocal

    def _wrap(builder: Any) -> AgentBuilder:
        def _fabrique(session: Session) -> Any:
            return builder(session, session_factory=SessionLocal)

        return _fabrique

    def _generaliste(session: Session) -> Any:
        # ``read_document`` est injecté par l'appelant : l'agent reste indépendant
        # du câblage documentaire (voir generaliste_readonly).
        lecture = next(
            (
                tool
                for tool in tools_documents_pour(GENERALISTE_DEF, SessionLocal)
                if tool.name == READ_DOCUMENT_TOOL
            ),
            None,
        )
        return build_generaliste(
            session, session_factory=SessionLocal, read_document_tool=lecture
        )

    return AgentRuntimeManager(
        {
            GENERATEUR_DEF.agent_id: _wrap(build_generateur),
            ASSISTANT_DEF.agent_id: _wrap(build_assistant),
            GENERALISTE_DEF.agent_id: _generaliste,
            RH_DEF.agent_id: _wrap(build_rh),
            STAT_DEF.agent_id: _wrap(build_statistique),
        }
    )


@lru_cache
def get_runtime_manager() -> AgentRuntimeManager:
    """Manager applicatif unique (cache : un seul runtime par processus)."""
    return build_default_manager()


__all__ = [
    "AgentBuilder",
    "AgentRuntimeManager",
    "AgentRuntimeValues",
    "build_default_manager",
    "get_runtime_manager",
]
