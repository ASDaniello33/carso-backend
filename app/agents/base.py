"""Définition d'un agent métier (instruction/05 §3).

Chaque agent est indépendant : identité propre, instructions propres, tools
autorisés, permissions explicites, politique d'approbation, schémas d'entrée et
de sortie. Pas de coordinateur central : un agent est instancié directement par
l'interface (ou par un autre agent via un tool).

``AgentDefinition`` est le **contrat** ; le harnais Deep Agents
(``app/agents/harness.py``) en fait une exécution. Un agent ne peut pas
s'attribuer un tool qu'il n'a pas déclaré ici : le harnais refuse.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.agents.hitl import HitlScenario
from app.tools.permissions import PermissionPolicy


@dataclass(frozen=True)
class CollaborationGrant:
    """Autorisation explicite de déléguer à un agent précis (instruction/05 §3, §5).

    La collaboration est **distribuée et explicite** : un agent ne peut appeler un
    autre agent que si son contrat le déclare, et seulement pour les
    ``task_types`` listés. Aucun grant ⇒ aucune délégation (refus par défaut).
    """

    agent_id: str
    task_types: frozenset[str]


@dataclass(frozen=True)
class AgentDefinition:
    """Contrat d'identité d'un agent (figé à la construction).

    Attributes:
        agent_id: identifiant unique, utilisé dans ``agent_tasks`` et l'audit.
        display_name: libellé affiché à l'utilisateur.
        description: ce que fait l'agent (affichée à l'interface, aux autres
            agents et au modèle lorsqu'il délègue).
        prompt: instructions de l'agent (system prompt du deep agent). Elles
            décrivent son périmètre et ses interdits, jamais des secrets.
        policy: capacités autorisées (allow-list, refus par défaut).
        capabilities: capacités déclarées (sous-ensemble de la politique).
        tools: noms des tools autorisés — le harnais refuse tout autre tool.
        collaboration: agents qu'il peut appeler et types de tâche autorisés
            (``instruction/05 §5``). Vide = agent autonome, sans délégation.
        approval_required: tools dont l'exécution est soumise à approbation
            humaine interactive (``interrupt_on`` du harnais, instruction/08 §7).
        output_schema_name: schéma de sortie structurée attendu (documentaire ;
            le schéma Pydantic lui-même est fourni au harnais).
        skills: noms des skills Deep Agents (répertoires ``SKILL.md``) que
            cet agent a le droit de charger. Vide = aucun skill. Le harnais
            résout ces noms via ``app.agents.skills`` — jamais un chemin
            libre fourni par le modèle.
        hitl_scenarios: moments où l'agent interrompt et questionne
            l'utilisateur dans le chat, avec les réponses prédéfinies proposées
            (``app/agents/hitl.py``). Le catalogue les expose à l'interface ;
            la réponse personnalisée (texte libre) s'ajoute toujours. Vide =
            agent sans action sensible déclarée.
    """

    agent_id: str
    display_name: str
    description: str
    policy: PermissionPolicy
    capabilities: tuple[str, ...] = ()
    tools: tuple[str, ...] = ()
    collaboration: tuple[CollaborationGrant, ...] = ()
    prompt: str = ""
    approval_required: tuple[str, ...] = field(default=())
    output_schema_name: str | None = None
    skills: tuple[str, ...] = field(default=())
    hitl_scenarios: tuple[HitlScenario, ...] = field(default=())

    def allowed_tools(self) -> frozenset[str]:
        """Ensemble des tools que cet agent a le droit d'utiliser."""
        return frozenset(self.tools)

    def granted_task_types(self, target_agent_id: str) -> frozenset[str]:
        """Types de tâche que cet agent peut déléguer à ``target_agent_id``.

        Renvoie l'ensemble **vide** si aucune autorisation n'existe : la
        délégation est refusée par défaut, jamais implicite.
        """
        autorisees: set[str] = set()
        for grant in self.collaboration:
            if grant.agent_id == target_agent_id:
                autorisees.update(grant.task_types)
        return frozenset(autorisees)

    def collaborates_with(self) -> tuple[str, ...]:
        """Agents appelables (identifiants triés), pour l'introspection/AG-UI."""
        return tuple(sorted({grant.agent_id for grant in self.collaboration}))
