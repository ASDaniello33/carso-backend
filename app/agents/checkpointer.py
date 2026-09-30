"""Persistance LangGraph des threads d'agents — PostgreSQL (incrément 32).

**Problème** : le harnais fournit un ``MemorySaver`` par défaut — un checkpointer
**en mémoire process**. La continuité de contexte (threads de conversation,
interruptions HITL en attente) disparaît à chaque redémarrage du backend, et
deux graphes reconstruits (changement de modèle à chaud) ne partagent aucune
mémoire : un fil AG-UI repris après un reload repartait à zéro.

**Décision (ADR 0009)** : un **unique** ``PostgresSaver`` (paquet
``langgraph-checkpoint-postgres``, MIT, psycopg 3 — déjà dépendance du projet)
est partagé par tous les graphes du ``AgentRuntimeManager``. Il est :

- **paresseux** : créé au premier graphe construit, pas au démarrage (une base
  injoignable ne bloque jamais l'API — même contrat que
  ``restaurer_runtime_agents``) ;
- **partagé** : un seul saver par processus, donc les threads survivent à un
  changement de modèle à chaud et aux redémarrages (les lignes vivent dans
  PostgreSQL) ;
- **best-effort** : tout échec (extra absent, base injoignable) retombe sur
  ``MemorySaver`` (comportement d'avant l'incrément) avec un avertissement
  journalisé — jamais une dégradation silencieuse : l'état réel est exposé
  par ``etat_checkpointer()`` ;
- **sécurisé** : désérialisation msgpack **strict** — sérialiseur explicite
  (``JsonPlusSerializer(allowed_msgpack_modules=None)``) **et** variable
  ``LANGGRAPH_STRICT_MSGPACK`` posée par précaution : seuls les types sûrs
  (messages langchain-core, ``Command`` / ``Interrupt``, datetime, UUID…)
  désérialisent — une base compromise ne peut pas déclencher d'exécution de
  code à la désérialisation (AGENTS.md §9).

Cohérence sync/async : ``PostgresSaver`` est un saver **synchrone** — les
``aget_tuple`` / ``aput`` / … de la classe de base lèvent
``NotImplementedError`` (vérifié dans la source langgraph-checkpoint 4.2.0).
Or le projet invoque les graphes sur les DEUX chemins : ``graphe.invoke``
(générateur d'offres, reprise HITL) et ``graphe.astream`` (chat social,
pont AG-UI). La fabrique complète donc le saver avec une surface async propre
(délégation ``asyncio.to_thread``) pour un seul et même saver bidirectionnel.

Connexions : pool psycopg dédié (``psycopg_pool.ConnectionPool``), indépendant
de l'engine SQLAlchemy applicatif — le saver ne partage aucune ressource avec
les transactions métier (AGENTS.md §2.4). Les opérations du saver extraient
une connexion du pool par appel (``_internal.get_connection``), le verrou
interne ne couvrant que ``setup()``.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import urlunparse

from app.core.config import Settings, get_settings

_logger = logging.getLogger(__name__)

#: État d'un checkpointer résolu (ou tenté) : aucun secret, projetable en API.
#: ``None`` signale le fallback mémoire (défaut historique).
_ETAT: dict[str, str | bool | None] = {"mode": "memoire", "erreur": None}

#: Un seul saver par processus : la résolution (et son échec éventuel) est
#: tentée une fois, jamais à chaque construction de graphe.
_verrou = threading.Lock()
_saver: object | None = None
_resolu = False


def etat_checkpointer() -> dict[str, str | bool | None]:
    """État courant (aucun secret) : ``memoire`` ou ``postgres``, + erreur."""
    with _verrou:
        return dict(_ETAT)


def _verrouiller_mode_strict() -> None:
    """Active le mode strict msgpack **avant tout import de langgraph**.

    Le sérialiseur par défaut de ``BaseCheckpointSaver`` est construit à
    l'import du module (attribut de classe) : la variable doit donc être posée
    avant le premier import de ``langgraph.checkpoint``. Une valeur explicite
    de l'opérateur (``.env``) a toujours priorité — ``setdefault`` ne la touche
    pas.
    """
    os.environ.setdefault("LANGGRAPH_STRICT_MSGPACK", "true")


def url_checkpoint_depuis_db(url_sqlalchemy: str) -> str:
    """Dérive l'URL psycopg (checkpointer) de l'URL SQLAlchemy applicative.

    ``postgresql+psycopg://u:p@h:5432/b`` → ``postgresql://u:p@h:5432/b``.
    Le serveur, les identifiants et la base sont ceux de l'application : les
    tables de checkpointing vivent à côté des données métier (ADR 0009) et
    aucune credential supplémentaire n'est introduite.

    Raises:
        ValueError: schéma non PostgreSQL (l'application est PostgreSQL-only,
            ADR 0001 — inutile d'imaginer d'autres backends).
    """
    from urllib.parse import urlparse

    analysee = urlparse(url_sqlalchemy)
    if analysee.scheme == "postgresql+psycopg":
        scheme = "postgresql"
    elif analysee.scheme == "postgresql":
        scheme = "postgresql"
    else:
        msg = (
            "URL de checkpointing non dérivable (schéma "
            f"{analysee.scheme!r} non PostgreSQL) : {type(url_sqlalchemy).__name__}"
        )
        raise ValueError(msg)
    return urlunparse(analysee._replace(scheme=scheme))


def _construire_saver(settings: Settings) -> object | None:
    """Construit le ``PostgresSaver`` partagé, ou ``None`` en cas d'échec.

    Tout incident est journalisé en avertissement (cause lisible) et fait
    retomber sur le ``MemorySaver`` du harnais : un agent doit rester
    utilisable même sans persistance.
    """
    _verrouiller_mode_strict()
    try:
        from langgraph.checkpoint.postgres import PostgresSaver
        from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
        from psycopg.rows import dict_row
        from psycopg_pool import ConnectionPool
    except ImportError as exc:  # pragma: no cover - extra agents absent
        _logger.warning(
            "Checkpointer persistant indisponible (paquet absent) : %s — "
            "fallback mémoire",
            exc,
        )
        _ETAT["erreur"] = "paquet langgraph-checkpoint-postgres absent"
        return None

    try:
        url = url_checkpoint_depuis_db(settings.database_url)
        # Contrat de la bibliothèque (documenté, vérifié en intégration) :
        # les connexions du pool doivent naître avec ``autocommit=True``
        # (``setup()`` exécute ``CREATE INDEX CONCURRENTLY``, interdit dans un
        # bloc transactionnel) et ``row_factory=dict_row`` (le saver lit les
        # lignes par nom de colonne — un tuple lève ``TypeError``).
        pool = ConnectionPool(
            url,
            name="carso-checkpoints",
            min_size=1,
            max_size=8,
            open=False,
            kwargs={"autocommit": True, "row_factory": dict_row},
            check=ConnectionPool.check_connection,
        )
        pool.open(wait=False)  # Sans attente : un serveur down échoue vite.
        # Sérialiseur **strict explicite** (sémantique ``None`` vérifiée dans la
        # source : seuls les types sûrs désérialisent, le reste est bloqué avec
        # un log ``msgpack_blocked``) — l'env-var seule dépend de l'ordre
        # d'import de langgraph, ici la garantie est déterministe. Extension
        # point documenté (ADR 0009) : ajouter un couple (module, classe) si un
        # état métier l'exige un jour — jamais une ouverture générale.
        #
        # Surface **async** : ``PostgresSaver`` n'implémente que le sync (les
        # ``a*`` de la classe de base lèvent ``NotImplementedError``) alors que
        # ``graphe.astream`` (chat social, AG-UI) passe par elles — observé en
        # réel (incr. 32). La sous-classe ci-dessous fait le pont standard :
        # chaque ``a*`` exécute son équivalent sync dans un thread (pool psycopg
        # = plusieurs connexions, donc exécutions concurrentes sûres).
        class _SaverAsync(PostgresSaver):
            async def aget_tuple(self, config: dict) -> Any:  # type: ignore[override]
                return await asyncio.to_thread(self.get_tuple, config)

            async def alist(
                self,
                config: dict | None,
                *,
                filter: dict[str, Any] | None = None,
                before: dict[str, Any] | None = None,
                limit: int | None = None,
            ) -> AsyncIterator[Any]:
                tuples = await asyncio.to_thread(
                    lambda: list(
                        self.list(config, filter=filter, before=before, limit=limit)
                    )
                )
                for element in tuples:
                    yield element

            async def aput(
                self,
                config: dict,
                checkpoint: Any,
                metadata: dict[str, Any],
                new_versions: dict[str, Any],
            ) -> Any:  # type: ignore[override]
                return await asyncio.to_thread(
                    self.put, config, checkpoint, metadata, new_versions
                )

            async def aput_writes(
                self,
                config: dict,
                writes: Any,
                task_id: str,
                task_path: str = "",
            ) -> None:  # type: ignore[override]
                await asyncio.to_thread(
                    self.put_writes, config, writes, task_id, task_path
                )

            async def adelete_thread(self, thread_id: str) -> None:  # type: ignore[override]
                await asyncio.to_thread(self.delete_thread, thread_id)

        saver = _SaverAsync(
            pool, serde=JsonPlusSerializer(allowed_msgpack_modules=None)
        )
        # Crée les tables au premier lancement (idempotent) — requiert un
        # pool fonctionnel, d'où sa place ici et pas avant.
        saver.setup()
    except Exception as exc:  # noqa: BLE001 - best-effort assumé (ADR 0009)
        _logger.warning(
            "Checkpointer persistant non établi : %s — fallback mémoire",
            exc,
        )
        _ETAT["erreur"] = f"{type(exc).__name__}: {exc}"
        try:
            pool.close()  # type: ignore[possibly-undefined]
        except Exception:  # noqa: BLE001 - fermeture best-effort
            pass
        return None

    _logger.info(
        "Checkpointer LangGraph persistant : PostgreSQL (threads des agents "
        "conservés entre les redémarrages)"
    )
    return saver


def get_checkpointer(settings: Settings | None = None) -> object | None:
    """Checkpointer partagé du processus, ou ``None`` pour le fallback mémoire.

    Résolu une seule fois (premier graphe construit) puis mis en cache : un
    échec initial désactive la persistance **jusqu'au redémarrage du backend**
    (limitation documentée, ADR 0009) — on évite de marteler une base
    injoignable à chaque reconstruction de graphe.

    Le réglage ``AGENT_CHECKPOINT_PERSISTANT=0`` (``.env`` ou environnement)
    force le fallback mémoire — la suite de tests le pose pour ne jamais
    toucher la base PostgreSQL réelle.
    """
    global _saver, _resolu
    reglages = settings or get_settings()
    with _verrou:
        if _resolu:
            return _saver
        _resolu = True
        if not reglages.agent_checkpoint_persistant:
            _ETAT["mode"] = "memoire"
            _ETAT["erreur"] = "désactivé par configuration"
            _logger.info("Checkpointer persistant désactivé : MemorySaver par graphe")
            return None
        _saver = _construire_saver(reglages)
        _ETAT["mode"] = "postgres" if _saver is not None else "memoire"
        return _saver


def liberer_checkpointer() -> None:
    """Ferme le pool de connexions du saver (arrêt de l'application)."""
    global _saver, _resolu
    with _verrou:
        saver = _saver
        _saver = None
        _resolu = False
        _ETAT["mode"] = "memoire"
        _ETAT["erreur"] = None
    if saver is None:
        return
    fermer = getattr(saver, "conn", None)
    try:
        if fermer is not None and hasattr(fermer, "close"):
            fermer.close()
    except Exception:  # noqa: BLE001 - fermeture best-effort à l'arrêt
        _logger.debug("Fermeture du checkpointer ignorée")


def prechauffer_checkpointer() -> None:
    """Établit (ou tente) la persistance avant le premier run d'agent.

    Appelé au démarrage (lifespan) : sans lui, le saver serait créé au premier
    graphe — correct mais moins lisible dans les logs. Best-effort total :
    aucun incident ne doit empêcher l'API de servir.
    """
    try:
        get_checkpointer()
    except Exception as exc:  # noqa: BLE001 - jamais bloquant au démarrage
        _logger.warning("Préchauffage du checkpointer ignoré : %s", exc)


def reinitialiser_pour_tests() -> None:
    """Réinitialise le cache process (réservé aux tests)."""
    global _saver, _resolu
    with _verrou:
        _saver = None
        _resolu = False
        _ETAT["mode"] = "memoire"
        _ETAT["erreur"] = None


__all__ = [
    "etat_checkpointer",
    "get_checkpointer",
    "liberer_checkpointer",
    "prechauffer_checkpointer",
    "reinitialiser_pour_tests",
    "url_checkpoint_depuis_db",
]
