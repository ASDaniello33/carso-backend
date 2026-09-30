"""Collaboration inter-agent (instruction/05 §5/§6/§7/§8, instruction/10 §8).

Les agents CARSO collaborent de façon **explicite et distribuée** : il n'existe
aucun coordinateur central. Un agent délègue une tâche à un autre agent via le
tool typé ``request_agent_task`` ; chacun conserve son contrat, ses permissions,
son état de tâche et sa stratégie d'erreur.

```text
Agent A (appelant)
  ↓ request_agent_task                    (tool typé déclaré au contrat de A)
InterAgentGateway                         (runtime commun, instruction/05 §4)
  ↓ autorisation : capacité + grant       → refus par défaut
  ↓ garde anti-boucle : auto-appel / cycle / profondeur
  ↓ trace agent_tasks (task_id + correlation_id, payload sans secrets)
AgentHandler de l'agent B                 (l'agent B revérifie SES permissions)
  ↓ réponse au contrat inter-agent        (instruction/05 §7)
```

Garanties :

- **Aucun transfert de privilèges** : la demande ne porte jamais les capacités de
  l'appelant ; l'agent appelé re-vérifie ses propres permissions à sa frontière.
- **Périmètre minimal** : la délégation exige un ``CollaborationGrant`` explicite
  portant le couple (agent cible, type de tâche) — sinon ``PermissionDeniedError``.
- **Traçabilité** : une ligne ``agent_tasks`` par délégation, avec ``task_id`` et
  ``correlation_id`` partagés par toute la chaîne.
- **Anti-boucle** : auto-délégation, cycle de *lineage* et profondeur maximale
  par ``correlation_id`` (``AgentLoopError``).
- **Réponse validée** : une réponse hors contrat (champ manquant, ``task_id``
  incohérent, statut non terminal) est refusée (``InvalidAgentResponseError``).

Choix *tools vs A2A* (instruction/05 §6) : un appel local synchrone reste un tool
typé (ADR 0002). Un adaptateur A2A réutilisera ``AgentRequest`` / ``AgentResponse``
sans modifier les agents.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Protocol
from uuid import UUID

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.agents.base import AgentDefinition
from app.agents.runtime import AgentTaskService
from app.core.config import Settings, get_settings
from app.core.errors import (
    AgentLoopError,
    AgentTimeoutError,
    CarsoError,
    ConflictError,
    InvalidAgentResponseError,
    PermissionDeniedError,
)
from app.core.errors import ValidationError as BusinessValidationError
from app.domain.document import AgentTask
from app.domain.enums import StatutAgentTask
from app.infrastructure.database import SessionLocal
from app.tools.base import TypedTool

__all__ = [
    "GET_RESULT_CAPABILITY",
    "REQUEST_TASK_CAPABILITY",
    "AgentHandler",
    "AgentHandlerRegistry",
    "AgentRequest",
    "AgentResponse",
    "InterAgentGateway",
    "build_collaboration_tools",
]

_logger = logging.getLogger(__name__)

#: Capacités à déclarer dans le contrat d'un agent qui délègue / relit une tâche.
REQUEST_TASK_CAPABILITY = "request_agent_task"
GET_RESULT_CAPABILITY = "get_agent_task_result"

_STATUTS_VALIDES: frozenset[str] = frozenset(item.value for item in StatutAgentTask)
_STATUTS_TERMINAUX: frozenset[str] = frozenset(
    {
        StatutAgentTask.COMPLETED.value,
        StatutAgentTask.FAILED.value,
        StatutAgentTask.TIMEOUT.value,
    }
)


def _valeur_statut(statut: Any) -> str:
    """Statut sous forme de chaîne (les colonnes ``agent_tasks`` sont des String)."""
    return statut.value if hasattr(statut, "value") else str(statut)


# --- Contrat inter-agent (instruction/05 §7) -----------------------------------


@dataclass(frozen=True)
class AgentRequest:
    """Demande inter-agent *résolue* (contrat ``instruction/05 §7``).

    Elle est construite par ``InterAgentGateway`` : ``task_id`` et
    ``correlation_id`` viennent de la trace ``agent_tasks``, jamais du modèle.
    """

    task_id: str
    correlation_id: str
    from_agent: str
    to_agent: str
    task_type: str
    payload: dict[str, Any] = field(default_factory=dict)
    constraints: dict[str, Any] = field(default_factory=dict)
    requires_approval: bool = True

    def as_payload(self) -> dict[str, Any]:
        """Représentation sérialisable (trace, message inter-agent, A2A)."""
        return {
            "task_id": self.task_id,
            "correlation_id": self.correlation_id,
            "from_agent": self.from_agent,
            "to_agent": self.to_agent,
            "task_type": self.task_type,
            "payload": dict(self.payload),
            "constraints": dict(self.constraints),
            "requires_approval": self.requires_approval,
        }


@dataclass(frozen=True)
class AgentResponse:
    """Réponse inter-agent (contrat ``instruction/05 §7``), validée à la frontière."""

    task_id: str
    status: str
    result: dict[str, Any] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()
    artifacts: tuple[str, ...] = ()
    requires_approval: bool = False

    def as_payload(self) -> dict[str, Any]:
        """Représentation sérialisable (sortie de tool, trace, A2A)."""
        return {
            "task_id": self.task_id,
            "status": self.status,
            "result": dict(self.result),
            "warnings": list(self.warnings),
            "artifacts": list(self.artifacts),
            "requires_approval": self.requires_approval,
        }

    @classmethod
    def from_task(cls, task: AgentTask) -> AgentResponse:
        """Reconstruit la réponse depuis une trace ``agent_tasks``."""
        return cls(
            task_id=str(task.id),
            status=_valeur_statut(task.status),
            result=dict(task.result or {}),
            requires_approval=bool(task.requires_approval),
        )

    @classmethod
    def from_payload(cls, payload: Any) -> AgentResponse:
        """Valide une réponse reçue (handler local, futur adaptateur A2A).

        Raises:
            InvalidAgentResponseError: objet illisible, champ obligatoire absent,
                statut inconnu ou champ mal typé.
        """
        if not isinstance(payload, Mapping):
            raise InvalidAgentResponseError(
                "Réponse d'agent illisible : objet structuré attendu"
            )

        manquants = [
            cle
            for cle in ("task_id", "status", "result", "requires_approval")
            if cle not in payload
        ]
        if manquants:
            raise InvalidAgentResponseError(
                f"Réponse d'agent incomplète : champs manquants {manquants}",
                details={"champs_manquants": manquants},
            )

        statut = str(payload["status"])
        if statut not in _STATUTS_VALIDES:
            raise InvalidAgentResponseError(
                f"Statut d'agent inconnu : {statut!r}",
                details={"statuts_valides": sorted(_STATUTS_VALIDES)},
            )

        resultat = payload["result"]
        if not isinstance(resultat, Mapping):
            raise InvalidAgentResponseError("Champ 'result' non structuré")

        return cls(
            task_id=str(payload["task_id"]),
            status=statut,
            result=dict(resultat),
            warnings=_liste_de_chaines(payload.get("warnings"), "warnings"),
            artifacts=_liste_de_chaines(payload.get("artifacts"), "artifacts"),
            requires_approval=bool(payload["requires_approval"]),
        )


def _liste_de_chaines(valeur: Any, champ: str) -> tuple[str, ...]:
    """Valide une liste de chaînes optionnelle du contrat de réponse."""
    if valeur is None:
        return ()
    if isinstance(valeur, (str, bytes)) or not isinstance(valeur, (list, tuple)):
        raise InvalidAgentResponseError(f"Champ {champ!r} : liste de chaînes attendue")
    if not all(isinstance(item, str) for item in valeur):
        raise InvalidAgentResponseError(f"Champ {champ!r} : éléments non textuels")
    return tuple(valeur)


# --- Agent appelé (côté exécution) ---------------------------------------------


class AgentHandler(Protocol):
    """Ce qu'un agent expose pour être appelé par un autre agent.

    L'implémentation est **propre à l'agent** : elle porte ses permissions et ses
    services. Le gateway ne lui transmet ni session, ni politique, ni credential.
    """

    @property
    def agent_id(self) -> str:
        """Identifiant de l'agent appelé (celui de son contrat)."""
        ...

    def supported_task_types(self) -> frozenset[str]:
        """Types de tâche que cet agent sait traiter."""
        ...

    def handle(self, request: AgentRequest) -> AgentResponse:
        """Traite la demande et renvoie une réponse au contrat §7.

        Un échec se signale par une **exception** (la tâche est alors tracée
        ``failed``), jamais par un statut non terminal dans la réponse.
        """
        ...


class AgentHandlerRegistry:
    """Registre des agents appelables (instruction/05 §4). Refuse les doublons."""

    def __init__(self) -> None:
        self._handlers: dict[str, AgentHandler] = {}

    def register(self, handler: AgentHandler) -> AgentHandler:
        """Enregistre un handler ; refuse un identifiant déjà pris."""
        if handler.agent_id in self._handlers:
            msg = f"Agent déjà enregistré comme appelable : {handler.agent_id!r}"
            raise BusinessValidationError(msg, details={"agent_id": handler.agent_id})
        self._handlers[handler.agent_id] = handler
        return handler

    def resolve(self, agent_id: str) -> AgentHandler:
        """Handler d'un agent cible ; ``ValidationError`` s'il est inconnu."""
        handler = self._handlers.get(agent_id)
        if handler is None:
            msg = f"Agent cible inconnu : {agent_id!r}"
            raise BusinessValidationError(msg, details={"agents_disponibles": self.ids()})
        return handler

    def ids(self) -> list[str]:
        return sorted(self._handlers)

    def __len__(self) -> int:
        return len(self._handlers)


# --- Passerelle de collaboration ------------------------------------------------


class InterAgentGateway:
    """Runtime commun d'appel inter-agent (autorisation, trace, garde-fous).

    Le gateway ouvre **une transaction par étape** et ne garde jamais une
    transaction ouverte pendant l'exécution de l'agent appelé : un run d'agent
    dure, appelle un modèle et peut confier le travail à un autre thread.
    """

    def __init__(
        self,
        session_factory: Callable[[], Session] | None = None,
        *,
        handlers: AgentHandlerRegistry,
        settings: Settings | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """
        Args:
            session_factory: fabrique de session pour les traces ``agent_tasks``
                (défaut : celle de l'application).
            handlers: agents appelables enregistrés.
            settings: configuration (profondeur max, budget de temps).
            clock: horloge monotone injectable (tests déterministes du budget).
        """
        self._session_factory: Callable[[], Session] = session_factory or SessionLocal
        self._handlers = handlers
        self._settings = settings or get_settings()
        self._clock = clock

    @property
    def handlers(self) -> AgentHandlerRegistry:
        """Agents appelables enregistrés."""
        return self._handlers

    # --- Appel (tool request_agent_task) -----------------------------------

    def request(
        self,
        caller: AgentDefinition,
        *,
        to_agent: str,
        task_type: str,
        payload: dict[str, Any] | None = None,
        constraints: dict[str, Any] | None = None,
        correlation_id: str | None = None,
        requires_approval: bool = True,
    ) -> AgentResponse:
        """Délègue une tâche à un agent appelable et renvoie sa réponse validée.

        Raises:
            PermissionDeniedError: capacité de délégation absente ou couple
                (agent cible, type de tâche) non autorisé par le contrat.
            ValidationError: agent cible inconnu ou type de tâche non supporté.
            AgentLoopError: auto-délégation, cycle de lineage ou profondeur max.
            AgentTimeoutError: budget de temps dépassé.
            InvalidAgentResponseError: réponse hors contrat.
        """
        charge = dict(payload or {})
        contraintes = dict(constraints or {})

        self._authorize(caller, to_agent, task_type)
        if to_agent == caller.agent_id:
            msg = "Auto-délégation refusée : un agent ne se délègue pas une tâche"
            raise AgentLoopError(msg, details={"agent_id": caller.agent_id})

        handler = self._handlers.resolve(to_agent)
        supportees = handler.supported_task_types()
        if task_type not in supportees:
            msg = f"L'agent {to_agent!r} ne traite pas la tâche {task_type!r}"
            raise BusinessValidationError(
                msg, details={"taches_supportees": sorted(supportees)}
            )

        correlation = correlation_id or str(uuid.uuid4())

        with self._unit_of_work() as session:
            tasks = AgentTaskService(session)
            self._guard_loop(tasks, to_agent, correlation, contraintes)
            task = tasks.start(
                from_agent=caller.agent_id,
                to_agent=to_agent,
                task_type=task_type,
                payload=charge,
                correlation_id=correlation,
                requires_approval=requires_approval,
            )
            task_id = str(task.id)

        demande = AgentRequest(
            task_id=task_id,
            correlation_id=correlation,
            from_agent=caller.agent_id,
            to_agent=to_agent,
            task_type=task_type,
            payload=charge,
            constraints=contraintes,
            requires_approval=requires_approval,
        )

        debut = self._clock()
        try:
            reponse = self._reponse_validee(handler.handle(demande), task_id)
        except Exception as exc:
            self._clore(task_id, error=f"{type(exc).__name__}", original=exc)
            raise

        ecoule = self._clock() - debut
        if ecoule > self._settings.agent_task_timeout_seconds:
            self._clore(task_id, error="timeout", statut=StatutAgentTask.TIMEOUT)
            msg = (
                f"Tâche {task_type!r} confiée à {to_agent!r} au-delà du budget de "
                f"{self._settings.agent_task_timeout_seconds}s ({ecoule:.3f}s)"
            )
            raise AgentTimeoutError(
                msg,
                details={
                    "task_id": task_id,
                    "correlation_id": correlation,
                    "to_agent": to_agent,
                    "duree_secondes": round(ecoule, 3),
                },
            )

        self._clore(task_id, resultat=reponse.result)
        return reponse

    # --- Relecture (tool get_agent_task_result) ----------------------------

    def get_result(self, caller: AgentDefinition, task_id: str | UUID) -> AgentResponse:
        """Relit le résultat d'une tâche à laquelle l'appelant a participé.

        L'isolation est stricte : un agent ne lit que ses propres tâches, en
        émission comme en réception — aucun privilège n'est hérité d'un appel.

        Raises:
            PermissionDeniedError: tâche d'un autre agent.
            NotFoundError: tâche inexistante.
            ConflictError: tâche encore en cours.
        """
        caller.policy.require(caller.agent_id, GET_RESULT_CAPABILITY)
        with self._unit_of_work() as session:
            task = AgentTaskService(session).get(task_id)
            if caller.agent_id not in {task.from_agent, task.to_agent}:
                msg = (
                    f"Agent {caller.agent_id!r} n'a pas accès à la tâche "
                    f"{task.id} d'un autre agent"
                )
                raise PermissionDeniedError(msg, details={"task_id": str(task.id)})

            statut = _valeur_statut(task.status)
            if statut not in _STATUTS_TERMINAUX:
                msg = f"Tâche {task.id} encore en cours ({statut})"
                raise ConflictError(
                    msg, details={"task_id": str(task.id), "statut": statut}
                )
            return AgentResponse.from_task(task)

    # --- Autorisation / garde-fous ------------------------------------------

    @staticmethod
    def _authorize(caller: AgentDefinition, to_agent: str, task_type: str) -> None:
        """Capacité de délégation + grant explicite (sinon refus)."""
        caller.policy.require(caller.agent_id, REQUEST_TASK_CAPABILITY)
        autorisees = caller.granted_task_types(to_agent)
        if task_type not in autorisees:
            msg = (
                f"Agent {caller.agent_id!r} n'est pas autorisé à déléguer "
                f"{task_type!r} à {to_agent!r}"
            )
            raise PermissionDeniedError(
                msg,
                details={
                    "to_agent": to_agent,
                    "task_type": task_type,
                    "delegations_autorisees": sorted(autorisees),
                },
            )

    def _guard_loop(
        self,
        tasks: AgentTaskService,
        to_agent: str,
        correlation_id: str,
        constraints: dict[str, Any],
    ) -> None:
        """Refuse un cycle déclaré puis une profondeur de chaîne excessive."""
        lineage = constraints.get("lineage")
        if isinstance(lineage, (list, tuple)) and to_agent in lineage:
            msg = (
                f"Cycle de collaboration : {to_agent!r} intervient déjà dans la "
                "chaîne déclarée"
            )
            raise AgentLoopError(msg, details={"lineage": list(lineage)})

        profondeur = len(tasks.by_correlation(correlation_id))
        maximum = self._settings.agent_max_collaboration_depth
        if profondeur >= maximum:
            msg = (
                f"Profondeur maximale de collaboration atteinte ({maximum}) pour "
                f"correlation_id={correlation_id!r}"
            )
            raise AgentLoopError(
                msg,
                details={
                    "correlation_id": correlation_id,
                    "profondeur": profondeur,
                    "profondeur_max": maximum,
                },
            )

    @staticmethod
    def _reponse_validee(reponse: Any, task_id: str) -> AgentResponse:
        """Valide la réponse du handler (contrat §7, corrélation, statut)."""
        validee = (
            reponse
            if isinstance(reponse, AgentResponse)
            else AgentResponse.from_payload(reponse)
        )

        if validee.task_id != task_id:
            msg = (
                f"Réponse d'agent incohérente : task_id {validee.task_id!r} "
                f"attendu {task_id!r}"
            )
            raise InvalidAgentResponseError(
                msg,
                details={"task_id_attendu": task_id, "task_id_recu": validee.task_id},
            )

        if validee.status != StatutAgentTask.COMPLETED.value:
            msg = (
                "Réponse d'agent non terminale : un échec doit être levé comme "
                f"exception, statut reçu {validee.status!r}"
            )
            raise InvalidAgentResponseError(
                msg, details={"statut": validee.status, "task_id": task_id}
            )
        return validee

    # --- Traces et transactions ----------------------------------------------

    def _clore(
        self,
        task_id: str,
        *,
        resultat: dict[str, Any] | None = None,
        error: str | None = None,
        statut: StatutAgentTask | None = None,
        original: BaseException | None = None,
    ) -> None:
        """Clôt la trace (completed / failed / timeout) dans sa transaction.

        En cours de traitement d'une exception, un échec secondaire de clôture
        est journalisé puis ignoré : il ne doit jamais masquer l'erreur d'origine.
        """
        try:
            with self._unit_of_work() as session:
                tasks = AgentTaskService(session)
                task = tasks.get(task_id)
                if error is not None:
                    if statut is StatutAgentTask.TIMEOUT:
                        tasks.timeout(task, error)
                    else:
                        tasks.fail(task, error)
                else:
                    tasks.complete(task, resultat or {})
        except Exception:  # pragma: no cover - la trace ne masque pas l'erreur d'origine
            if original is None:
                raise
            _logger.warning(
                "Clôture de la tâche inter-agent %s impossible après une erreur",
                task_id,
                exc_info=True,
            )

    @contextmanager
    def _unit_of_work(self) -> Iterator[Session]:
        """Une transaction (commit/rollback) par étape de collaboration."""
        session = self._session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()


# --- Tools exposés à un agent (instruction/05 §10) ------------------------------


class RequestAgentTaskInput(BaseModel):
    """Entrée du tool ``request_agent_task`` : demande inter-agent structurée."""

    to_agent: str = Field(description="Identifiant de l'agent cible (enregistré comme appelable)")
    task_type: str = Field(description="Type de tâche demandée (supporté par la cible)")
    payload: dict[str, Any] = Field(
        default_factory=dict, description="Charge métier transmise à l'agent cible"
    )
    correlation_id: str | None = Field(
        default=None,
        description="Corrélation d'une chaîne existante ; absent = nouvelle chaîne",
    )
    constraints: dict[str, Any] = Field(
        default_factory=dict, description="Contraintes d'exécution (lineage, …)"
    )
    requires_approval: bool = Field(
        default=True,
        description="La cible produit une proposition : décision humaine requise",
    )


class GetAgentTaskResultInput(BaseModel):
    """Entrée du tool ``get_agent_task_result``."""

    task_id: UUID = Field(description="Identifiant de la tâche inter-agent (agent_tasks)")


def build_collaboration_tools(
    *, caller: AgentDefinition, gateway: InterAgentGateway
) -> tuple[TypedTool, TypedTool]:
    """Construit les tools de collaboration d'un agent (instruction/05 §10).

    Les tools doivent aussi être déclarés dans ``caller.tools`` : le harnais
    n'expose que les tools inscrits au contrat (allow-list, moindre privilège).

    Args:
        caller: contrat de l'agent appelant (capacités + grants de délégation).
        gateway: passerelle de collaboration (runtime commun).

    Returns:
        ``(request_agent_task, get_agent_task_result)`` — tools typés, sortie
        JSON-sérialisable (contrat inter-agent §7).
    """

    def _request(payload: dict[str, Any]) -> dict[str, Any]:
        # Toute exception non métier (LLM, timeout interne, KeyError) devient
        # une CarsoError : TypedTool.invoke la convertit en Tool Message
        # {ok: false} au lieu de crasher le stream AG-UI.
        try:
            reponse = gateway.request(
                caller,
                to_agent=str(payload["to_agent"]),
                task_type=str(payload["task_type"]),
                payload=dict(payload.get("payload") or {}),
                constraints=dict(payload.get("constraints") or {}),
                correlation_id=payload.get("correlation_id"),
                requires_approval=bool(payload.get("requires_approval", True)),
            )
        except CarsoError:
            raise
        except Exception as exc:
            raise BusinessValidationError(
                f"La délégation vers {payload.get('to_agent')!r} a échoué : "
                f"{type(exc).__name__}: {exc}",
                details={
                    "to_agent": payload.get("to_agent"),
                    "task_type": payload.get("task_type"),
                },
            ) from exc
        return reponse.as_payload()

    def _get_result(payload: dict[str, Any]) -> dict[str, Any]:
        reponse = gateway.get_result(caller, UUID(str(payload["task_id"])))
        return reponse.as_payload()

    return (
        TypedTool(
            name=REQUEST_TASK_CAPABILITY,
            description=(
                "Délègue une tâche à un autre agent CARSO et attend sa réponse. "
                "La délégation doit être autorisée par ton contrat ; l'agent appelé "
                "revérifie ses propres permissions (aucun privilège n'est transmis)."
            ),
            input_schema=RequestAgentTaskInput,
            handler=_request,
            tags=("collaboration",),
        ),
        TypedTool(
            name=GET_RESULT_CAPABILITY,
            description=(
                "Relit la réponse d'une tâche inter-agent déjà terminée à laquelle "
                "tu participes. Lecture seule."
            ),
            input_schema=GetAgentTaskResultInput,
            handler=_get_result,
            tags=("collaboration",),
        ),
    )



