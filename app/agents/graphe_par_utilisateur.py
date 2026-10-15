"""Graphe résolu dans le contexte de l'appelant—pour les endpoints AG-UI.

**Pourquoi un proxy.** Un endpoint AG-UI est monté **une fois au démarrage**,
sans utilisateur—le graphe y était capturé, fige au contexte global. Rendre le
connecteur personnel actif dans ce canal suppose donc de résoudre le graphe à
**chaque run**, pas au montage.

**Ce que vérifiait l'inquiétude « schemas lus au montage ».** Elle était
infondée—`ag_ui_langgraph` lit `input_schema`, `output_schema`,
`config_schema` et `context_schema` dans ``get_schema_keys(self, config)``—
une méthode **par run**, qui reçoit la configuration. Le seul accès fait à la
construction est ``graph.nodes``, qui décrit la topologie du graphe
(« model », « tools »…)—identique pour tous, la forme de l'agent ne change
pas selon le compte. Aucune divergence de schéma n'est donc introduite.

**Résolution par ``delegation d'attributs``** plutôt qu'une liste de méthodes
énumérées—la bibliothèque peut evoluer, le proxy suit. Tout ce qu'elle
demande au graphe—``astream_events``, ``aget_state``, ``aupdate_state``,
les schémas, ``nodes``—est relaie.

**Identité.** Lue dans ``demande_par_effectif()``, pose par la garde AG-UI
(``preparer_acces_agent``) **dans la tache qui execute l'endpoint et itere le
flux SSE**—les taches filles, dont l'execution des tools, en heritent. Hors
requete (``None``), le graphe global est servi—le comportement historique.
"""

from __future__ import annotations

import time
from typing import Any

from app.application.trace import demande_par_effectif

#: Duree de memorisation de la resolution. Un run touche le graphe plusieurs
#: fois (schemas, etat, evenements) ; sans ce courtcache, chaque acces
#: relancerait une lecture de base pour recomputer l'empreinte. Court par
#: choix : un changement de connecteur personnel prend effet au run suivant,
#: pas ``DUREE`` secondes plus tard.
DUREE_MEMO_S = 5.0


class GrapheParUtilisateur:
    """Relaie chaque acces au graphe **du contexte de l'appelant**."""

    def __init__(self, agent_id: str) -> None:
        self.agent_id = agent_id
        self._memo: tuple[str | None, float, Any] | None = None

    def resoudre(self) -> Any:
        """Graphe effectif du compte courant (jamais celui d'un autre)."""
        utilisateur_id = demande_par_effectif()
        maintenant = time.monotonic()
        if self._memo is not None:
            cache_utilisateur, instant, graphe = self._memo
            if (
                cache_utilisateur == utilisateur_id
                and maintenant - instant < DUREE_MEMO_S
            ):
                return graphe

        from app.agents.manager import get_runtime_manager

        graphe = get_runtime_manager().graph(self.agent_id, utilisateur_id)
        self._memo = (utilisateur_id, maintenant, graphe)
        return graphe

    def __getattr__(self, nom: str) -> Any:
        """Relaie l'attribut au graphe resolu (methodes et proprietes)."""
        # ``__getattr__`` n'est appele que pour un attribut absent de
        # l'instance : les champs poses au ``__init__`` restent accessibles.
        if nom.startswith("__"):
            raise AttributeError(nom)
        return getattr(self.resoudre(), nom)


__all__ = ["DUREE_MEMO_S", "GrapheParUtilisateur"]
