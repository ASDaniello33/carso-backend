"""Helpers partagés des modules de tools d'agents (refactor outils, docs/agents).

Les modules ``agent_*_tools.py`` n'importent leur plomberie que d'ici et du
kit (``app.agents.toolkit``) : pagination bornée, sérialisation entité→dict,
limites communes. Aucune logique métier — les requêtes nommées restent dans
le module de l'agent qui les expose, les règles dans les services.
"""

from __future__ import annotations

from app.agents.toolkit import (
    LIMITE_MAX_RECHERCHE,
    PageSearchInput,
    serialiser_entite,
)

__all__ = [
    "LIMITE_MAX_RECHERCHE",
    "PageSearchInput",
    "serialiser_entite",
]
