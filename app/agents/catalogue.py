"""Catalogue des agents — exposition UI vs runtime interne.

Deux listes distinctes, **sans coordinateur** (AGENTS.md §1.6) :

- ``AGENT_DEFINITIONS`` : agents **exposés** à l'interface (AG-UI, catalogue).
- ``AGENT_RUNTIME`` : tous les agents **exécutables**. La distinction reste le
  contrat : un agent dont le contrat ne prévoit pas l'exposition à l'UI vit ici
  seulement (appelé par un autre agent via ``request_agent_task``).

Aujourd'hui les deux listes coïncident : l'ancien agent analyseur d'appel à
proposition a été supprimé et ses responsabilités fusionnées dans
``agent_generateur_offre``, qui est exposé à l'UI (décision utilisateur ;
voir ``docs/adr/0004-agent-generateur-unique.md``). La structure à deux listes
est conservée : elle n'existe pas pour l'analyseur, mais pour tout futur agent
interne justifié par l'analyse des besoins.
"""

from __future__ import annotations

from app.agents.agent_rh import DEFINITION as RH_DEFINITION
from app.agents.agent_statistique import DEFINITION as STATISTIQUE_DEFINITION
from app.agents.assistant_formateur import DEFINITION as ASSISTANT_DEFINITION
from app.agents.base import AgentDefinition
from app.agents.generaliste_readonly import DEFINITION as GENERALISTE_DEFINITION
from app.agents.generateur_offre import DEFINITION as GENERATEUR_DEFINITION

#: Agents visibles par l'utilisateur (ordre du flux métier).
AGENT_DEFINITIONS: tuple[AgentDefinition, ...] = (
    GENERATEUR_DEFINITION,
    RH_DEFINITION,
    ASSISTANT_DEFINITION,
    GENERALISTE_DEFINITION,
    STATISTIQUE_DEFINITION,
)

#: Tous les agents du runtime (UI + éventuels sous-agents internes).
AGENT_RUNTIME: tuple[AgentDefinition, ...] = (*AGENT_DEFINITIONS,)

AGENT_DEFINITIONS_BY_ID: dict[str, AgentDefinition] = {
    definition.agent_id: definition for definition in AGENT_DEFINITIONS
}

AGENT_RUNTIME_BY_ID: dict[str, AgentDefinition] = {
    definition.agent_id: definition for definition in AGENT_RUNTIME
}


def agent_ids() -> tuple[str, ...]:
    """Identifiants exposés à l'UI, ordre stable (trié)."""
    return tuple(sorted(AGENT_DEFINITIONS_BY_ID))


def runtime_agent_ids() -> tuple[str, ...]:
    """Identifiants du runtime (UI + internes), ordre stable (trié)."""
    return tuple(sorted(AGENT_RUNTIME_BY_ID))


__all__ = [
    "AGENT_DEFINITIONS",
    "AGENT_DEFINITIONS_BY_ID",
    "AGENT_RUNTIME",
    "AGENT_RUNTIME_BY_ID",
    "agent_ids",
    "runtime_agent_ids",
]