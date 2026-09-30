"""PermissionPolicy (instruction/05 §4) : liste explicite d'allow, refuse par défaut.

Principe de moindre privilège (AGENTS.md §9) : un agent n'a que les
capacités déclarées dans sa définition — toute autre action est refusée.
"""

from dataclasses import dataclass, field

from app.core.errors import PermissionDeniedError


@dataclass(frozen=True)
class AgentIdentity:
    """Identité d'appel d'un agent (pas d'agent global implicite)."""

    agent_id: str


@dataclass(frozen=True)
class PermissionPolicy:
    """Capacités autorisées d'un agent. Vide = agent purement consultation."""

    allowed_capabilities: frozenset[str] = field(default_factory=frozenset)

    def require(self, agent_id: str, capability: str) -> None:
        """Vérifie la capacité ; lève PermissionDeniedError sinon (403)."""
        if capability not in self.allowed_capabilities:
            msg = (
                f"Agent {agent_id!r} n'a pas la capacité {capability!r} "
                f"(autorisées: {sorted(self.allowed_capabilities)})"
            )
            raise PermissionDeniedError(msg)

    def allows(self, capability: str) -> bool:
        return capability in self.allowed_capabilities


ANONYMOUS_POLICY = PermissionPolicy(allowed_capabilities=frozenset())
