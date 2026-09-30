"""Façade synchrone des connecteurs MCP pour les agents (``langchain-mcp-adapters``).

Pourquoi une façade et pas un simple appel : la construction des graphes
d'agents est **synchrone** (``create_deep_agent`` est appelé au montage du
runtime, pas dans une coroutine), alors que le client MCP est **asynchrone**. On
ne peut ni bloquer la boucle d'événements de FastAPI, ni demander au harnais de
devenir asynchrone pour un connecteur optionnel. Un fil dédié porte donc une
boucle d'événements persistante : ``get_tools()`` y est exécuté et le résultat
est rendu au fil appelant.

Ce que le module garantit :

- **par agent** : seuls les connecteurs actifs qui autorisent l'agent sont
  chargés. Un agent ne reçoit jamais les outils d'un serveur qu'il n'a pas au
  contrat de connecteurs ;
- **paquet absent ⇒ dégradation explicite** : sans ``langchain-mcp-adapters``,
  le backend démarre normalement et l'agent fonctionne sans outils MCP ; le
  manque est journalisé, jamais silencieux ;
- **échec d'un connecteur ⇒ les autres survivent** : un serveur injoignable ne
  prive pas l'agent de ses autres outils distants ;
- **en-têtes déchiffrés ici seulement** : ils ne quittent jamais ce module sous
  forme lisible (ni logs, ni prompts, ni ``AgentTask``).
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Sequence
from typing import Any

from app.domain.mcp import ServeurMcp

_logger = logging.getLogger(__name__)

#: Cache des outils par (agent, signature des connecteurs) : reconstruire les
#: graphes ne doit pas relancer un serveur ``stdio`` à chaque fois.
_CACHE: dict[tuple[str, str], tuple[Any, ...]] = {}
_VERROU = threading.RLock()

_boucle: asyncio.AbstractEventLoop | None = None
_fil: threading.Thread | None = None
_verrou_boucle = threading.Lock()


def paquet_disponible() -> bool:
    """Vrai si ``langchain-mcp-adapters`` est installé."""
    try:
        import langchain_mcp_adapters.client  # noqa: F401
    except ImportError:
        return False
    return True


def _boucle_dediee() -> asyncio.AbstractEventLoop:
    """Boucle d'événements persistante sur un fil dédié (créée une fois)."""
    global _boucle, _fil
    with _verrou_boucle:
        if _boucle is not None and _boucle.is_running():
            return _boucle

        pret = threading.Event()

        def _servir() -> None:
            global _boucle
            boucle = asyncio.new_event_loop()
            asyncio.set_event_loop(boucle)
            _boucle = boucle
            pret.set()
            boucle.run_forever()

        _fil = threading.Thread(
            target=_servir, name="carso-mcp", daemon=True
        )
        _fil.start()
        pret.wait(timeout=5)
        if _boucle is None:  # pragma: no cover - dépend de l'OS
            raise RuntimeError("Boucle MCP indisponible")
        return _boucle


def _executer(coroutine: Any) -> Any:
    """Exécute une coroutine sur la boucle dédiée et rend son résultat."""
    return asyncio.run_coroutine_threadsafe(coroutine, _boucle_dediee()).result()


def _connexion(serveur: ServeurMcp) -> dict[str, Any]:
    """Configuration d'un connecteur au format attendu par les adaptateurs."""
    from app.application.services.parametres_service import ServeurMcpService

    entetes = ServeurMcpService.en_tetes(serveur)
    if serveur.transport == "stdio":
        return {
            "transport": "stdio",
            "command": serveur.commande,
            "args": list(serveur.arguments or []),
        }
    connexion: dict[str, Any] = {
        "transport": serveur.transport,
        "url": serveur.url,
    }
    if entetes:
        connexion["headers"] = entetes
    return connexion


def signature(serveurs: Sequence[ServeurMcp]) -> str:
    """Empreinte stable des connecteurs (sert de clé de cache)."""
    parties = [
        "|".join(
            [
                serveur.nom,
                serveur.transport,
                serveur.url or "",
                serveur.commande or "",
                ",".join(serveur.arguments or []),
            ]
        )
        for serveur in sorted(serveurs, key=lambda s: s.nom)
    ]
    return "§".join(parties)


def outils_mcp_pour(agent_id: str, serveurs: Sequence[ServeurMcp]) -> tuple[Any, ...]:
    """Outils MCP d'un agent, ou tuple vide si aucun connecteur exploitable.

    Args:
        agent_id: agent pour lequel les outils sont chargés (cache et journal).
        serveurs: connecteurs actifs autorisant cet agent.

    Returns:
        Les outils distants prêts à être ajoutés au graphe de l'agent. Une liste
        vide n'est jamais une erreur : elle signifie « aucun connecteur » ou
        « connecteurs injoignables », et c'est journalisé.
    """
    if not serveurs:
        return ()
    if not paquet_disponible():
        _logger.warning(
            "Connecteurs MCP déclarés pour %s mais langchain-mcp-adapters absent "
            "(pip install 'carso-backend[mcp]') : outils distants non chargés.",
            agent_id,
        )
        return ()

    cle = (agent_id, signature(serveurs))
    with _VERROU:
        if cle in _CACHE:
            return _CACHE[cle]

    from langchain_mcp_adapters.client import MultiServerMCPClient

    outils: list[Any] = []
    for serveur in sorted(serveurs, key=lambda s: s.nom):
        try:
            # Typé ``Any`` : la forme exacte attendue (StdioConnection,
            # StreamableHttpConnection…) diffère selon la version des
            # adaptateurs ; ``_connexion`` produit la variante du transport.
            connexions: dict[str, Any] = {serveur.nom: _connexion(serveur)}
            client = MultiServerMCPClient(
                connexions,
                tool_name_prefix=True,
                handle_tool_errors=True,
            )
            outils.extend(_executer(client.get_tools()))
        except Exception as exc:
            # Un connecteur en échec ne prive pas l'agent de ses autres outils.
            _logger.warning(
                "Connecteur MCP %s injoignable pour %s : %s",
                serveur.nom,
                agent_id,
                exc,
            )

    resultat = tuple(outils)
    with _VERROU:
        _CACHE[cle] = resultat
    return resultat


def vider_cache() -> None:
    """Vide le cache des outils MCP (changement de connecteurs, tests)."""
    with _VERROU:
        _CACHE.clear()


__all__ = ["outils_mcp_pour", "paquet_disponible", "signature", "vider_cache"]
