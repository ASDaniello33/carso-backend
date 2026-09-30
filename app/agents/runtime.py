"""AgentTaskService — traçabilité inter-agents (instruction/05 §4, §7).

Chaque exécution d'agent laisse une trace dans ``agent_tasks`` :
``correlation_id`` obligatoire, payload/result sans secrets (AGENTS.md §9),
statuts gouvernés par la machine à états ``agent_task``.

Note de conception : si le travail échoue, la ligne est marquée ``failed``
dans la session courante puis l'exception est relancée. Dans un flux API la
transaction est annulée par l'appelant — la trace d'échec persiste seulement
si l'appelant décide de la committer (politique d'erreur par agent, §3).
"""

import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.core.errors import NotFoundError
from app.core.errors import ValidationError as BusinessValidationError
from app.domain.document import AgentTask
from app.domain.enums import StatutAgentTask
from app.domain.state_machines import validate_transition
from app.infrastructure.repositories import AgentTaskRepository

_SECRET_HINTS = ("secret", "password", "passwd", "token", "api_key", "apikey", "credential")


def strip_secrets(payload: dict[str, Any]) -> dict[str, Any]:
    """Retire récursivement les clés ressemblant à des secrets.

    Défense en profondeur : les agents ne doivent JAMAIS recevoir ni
    transmettre de secrets dans les tâches inter-agents.
    """

    def _clean(value: Any) -> Any:
        if isinstance(value, dict):
            return {
                k: _clean(v)
                for k, v in value.items()
                if not any(hint in k.lower() for hint in _SECRET_HINTS)
            }
        if isinstance(value, list):
            return [_clean(v) for v in value]
        return value

    cleaned: dict[str, Any] = _clean(payload)
    return cleaned


class AgentTaskService:
    """Cycle de vie des AgentTask, dans la session de l'appelant (pas de commit)."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._repo = AgentTaskRepository(session)

    def start(
        self,
        *,
        from_agent: str,
        task_type: str,
        payload: dict[str, Any],
        to_agent: str | None = None,
        correlation_id: str | None = None,
        requires_approval: bool = False,
    ) -> AgentTask:
        """Ouvre une tâche : pending → running (machine à états)."""
        task = AgentTask(
            correlation_id=correlation_id or str(uuid.uuid4()),
            from_agent=from_agent,
            to_agent=to_agent,
            task_type=task_type,
            status=StatutAgentTask.PENDING.value,
            payload=strip_secrets(payload),
            requires_approval=requires_approval,
        )
        self._repo.add(task)
        validate_transition(
            "agent_task", task.status, StatutAgentTask.RUNNING.value
        )
        task.status = StatutAgentTask.RUNNING
        # Flush (jamais commit : la transaction appartient à l'appelant) pour que
        # l'id généré soit disponible dans la réponse inter-agent (§7).
        self._session.flush()
        return task

    def complete(self, task: AgentTask, result: dict[str, Any]) -> None:
        """running → completed avec résultat (sans secrets)."""
        validate_transition(
            "agent_task", task.status, StatutAgentTask.COMPLETED.value
        )
        task.status = StatutAgentTask.COMPLETED
        task.result = strip_secrets(result)

    def fail(self, task: AgentTask, error: str) -> None:
        """running → failed avec un message d'erreur synthétique."""
        validate_transition("agent_task", task.status, StatutAgentTask.FAILED.value)
        task.status = StatutAgentTask.FAILED
        task.result = {"error": error}

    def timeout(self, task: AgentTask, error: str) -> None:
        """running → timeout : budget de temps dépassé (instruction/10 §8)."""
        validate_transition("agent_task", task.status, StatutAgentTask.TIMEOUT.value)
        task.status = StatutAgentTask.TIMEOUT
        task.result = {"error": error}

    def get(self, task_id: uuid.UUID | str) -> AgentTask:
        """Charge une tâche par identifiant ; ``NotFoundError`` sinon."""
        identifiant = task_id if isinstance(task_id, uuid.UUID) else uuid.UUID(str(task_id))
        task = self._repo.get(identifiant)
        if task is None:
            msg = f"Tâche inter-agent introuvable : {identifiant}"
            raise NotFoundError(msg, details={"task_id": str(identifiant)})
        return task

    def by_correlation(self, correlation_id: str) -> list[AgentTask]:
        """Toutes les tâches d'une même chaîne de collaboration, dans l'ordre."""
        return self._repo.get_by_correlation(correlation_id)

    @staticmethod
    def as_response(
        task: AgentTask,
        *,
        warnings: list[str] | None = None,
        artifacts: list[str] | None = None,
    ) -> dict[str, Any]:
        """Réponse au contrat inter-agent (instruction/05 §7)."""
        return {
            "task_id": str(task.id),
            "status": task.status,
            "result": task.result or {},
            "warnings": warnings or [],
            "artifacts": artifacts or [],
            "requires_approval": task.requires_approval,
        }


def require_non_empty(value: str | None, field_name: str) -> str:
    """Validation utilitaire : chaîne non vide obligatoire."""
    if not value or not value.strip():
        msg = f"{field_name} obligatoire"
        raise BusinessValidationError(msg)
    return value.strip()
