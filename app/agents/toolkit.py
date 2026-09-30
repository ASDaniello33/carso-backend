"""Kit de construction des tools d'un agent métier (Lot A1).

L'``AgentAnalyseurAppelAProposition`` (627 lignes) porte cinq mécanismes de plomberie
que chacun des six agents devrait réécrire : transaction par appel de tool,
vérification de capacité, enveloppe ``TypedTool``, sérialisation entité→dict,
trace ``AgentTask``. Ce module est la **seam profonde unique** : l'agent
n'écrit que son contrat et ses opérations métier ; la plomberie vit et se
teste ici.

Chaîne conservée (clinrules ``08-tools-contracts``) :

```text
Agent → Tool → Application Service → Domain → Repository → PostgreSQL/Storage
```

Sécurité read-only (clinrules ``06-agent-generaliste-readonly``) : un
``AgentTools`` déclaré ``readonly=True`` refuse toute opération de mutation à
l'enregistrement (exception de construction, pas comportement silencieux).
"""

from __future__ import annotations

import datetime
import decimal
import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, model_validator
from sqlalchemy.orm import Session

from app.agents.base import AgentDefinition
from app.agents.runtime import AgentTaskService
from app.core.errors import PermissionDeniedError
from app.core.errors import ValidationError as BusinessValidationError
from app.infrastructure.database import SessionLocal
from app.tools.base import TypedTool

_logger = logging.getLogger(__name__)

#: Capacité portée par les opérations du kit (vérifiée au contrat de l'agent).
KIT_CAPABILITY = "agent_toolkit"

#: Plafond de lignes lues par une recherche d'agent (resserré, jamais rejeté).
LIMITE_MAX_RECHERCHE = 100


class PageSearchInput(BaseModel):
    """Pagination bornée des recherches d'agent (clinrules 08 : pas de
    ``execute_any_sql`` — les lectures passent par des requêtes nommées).

    Une limite au-delà du plafond est **resserrée** (jamais rejetée) : le
    modèle ne doit pas échouer pour avoir demandé trop large.
    """

    recherche: str | None = Field(default=None, max_length=255)
    limite: int = Field(default=20, ge=1)

    @model_validator(mode="after")
    def _resserrer_limite(self) -> PageSearchInput:
        """Plafonne la limite demandée (défense : le modèle ne contrôle pas
        le volume lu)."""
        if self.limite > LIMITE_MAX_RECHERCHE:
            self.limite = LIMITE_MAX_RECHERCHE
        return self


def _serialiser(valeur: Any) -> Any:
    """Rend une valeur ORM/scalaire sérialisable JSON (UUID, date, Decimal)."""
    if isinstance(valeur, UUID):
        return str(valeur)
    if isinstance(valeur, (datetime.date, datetime.datetime, datetime.time)):
        return valeur.isoformat()
    if isinstance(valeur, decimal.Decimal):
        return float(valeur)
    if isinstance(valeur, dict):
        return {cle: _serialiser(element) for cle, element in valeur.items()}
    if isinstance(valeur, (list, tuple)):
        return [_serialiser(element) for element in valeur]
    return valeur


def serialiser_entite(entite: Any, *champs: str) -> dict[str, Any]:
    """Projette une entité (objet ORM **ou mapping**) sur les champs autorisés
    (intersection stricte : un champ demandé mais absent est ignoré, aucune
    fuite)."""
    resultat: dict[str, Any] = {}
    if isinstance(entite, dict):
        for nom in champs:
            if nom in entite:
                resultat[nom] = _serialiser(entite[nom])
        return resultat
    for nom in champs:
        if hasattr(entite, nom):
            resultat[nom] = _serialiser(getattr(entite, nom))
    return resultat


OperationFn = Callable[[Session, dict[str, Any]], Any]


class OperationSpec:
    """Opération métier exposable comme tool.

    ``mutation=False`` (défaut) = lecture pure. Toute opération déclarée
    ``mutation=True`` est refusée par un ``AgentTools`` en mode ``readonly``.
    """

    def __init__(
        self,
        *,
        name: str,
        description: str,
        input_schema: type[BaseModel],
        mutation: bool = False,
        run: OperationFn,
    ) -> None:
        self.name = name
        self.description = description
        self.input_schema = input_schema
        self.mutation = mutation
        self.run = run

    def __repr__(self) -> str:
        return f"OperationSpec({self.name!r}, mutation={self.mutation})"


class AgentTools:
    """Fabrique des tools d'un agent à partir de ses opérations métier.

    Args:
        definition: contrat de l'agent (capacité ``agent_toolkit`` vérifiée
            une seule fois à la construction, pas dans chaque tool).
        readonly: l'agent est en lecture seule — toute opération ``mutation``
            lève ``PermissionDeniedError`` à l'enregistrement.
        session_factory: fabrique de session — une transaction par appel de
            tool (l'agent n'appartient pas à la transaction de l'appelant).
    """

    def __init__(
        self,
        definition: AgentDefinition,
        *,
        readonly: bool = False,
        capability: str = KIT_CAPABILITY,
        session_factory: Callable[[], Session] | None = None,
    ) -> None:
        definition.policy.require(definition.agent_id, capability)
        self._definition = definition
        self._readonly = readonly
        self._capability = capability
        self._session_factory = session_factory or SessionLocal
        self._operations: dict[str, OperationSpec] = {}

    @property
    def readonly(self) -> bool:
        """L'agent est déclaré en lecture seule (clinrules 06)."""
        return self._readonly

    def enregistrer(self, operation: OperationSpec) -> OperationSpec:
        """Ajoute une opération. Refuse immédiatement (construction) :

        - une opération de mutation dans un kit ``readonly`` ;
        - un nom déjà enregistré (défense contre le shadow de tool).
        """
        if self._readonly and operation.mutation:
            msg = (
                f"Tool de mutation {operation.name!r} interdit à l'agent "
                f"{self._definition.agent_id!r} (lecture seule)"
            )
            raise PermissionDeniedError(msg, details={"tool": operation.name})
        if operation.name in self._operations:
            msg = (
                f"Opération {operation.name!r} déjà enregistrée pour {self._definition.agent_id!r}"
            )
            raise BusinessValidationError(msg, details={"tool": operation.name})
        self._operations[operation.name] = operation
        return operation

    def construire(self) -> tuple[TypedTool, ...]:
        """Matérialise les tools typés (capacité vérifiée à la construction)."""
        return tuple(self._construire_tool(operation) for operation in self._operations.values())

    def noms(self) -> list[str]:
        """Noms des opérations enregistrées (déclarables au contrat ``tools``)."""
        return sorted(self._operations)

    # --- internes ------------------------------------------------------

    @contextmanager
    def _unit_of_work(self) -> Iterator[Session]:
        """Une transaction par appel de tool (instruction/04 §5)."""
        session = self._session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def _construire_tool(self, operation: OperationSpec) -> TypedTool:
        fabrique = self

        def handler(payload: dict[str, Any]) -> dict[str, Any]:
            fabrique._definition.policy.require(fabrique._definition.agent_id, fabrique._capability)
            with fabrique._unit_of_work() as session:
                resultat = operation.run(session, payload)
            if isinstance(resultat, dict):
                return {cle: _serialiser(valeur) for cle, valeur in resultat.items()}
            if isinstance(resultat, list):
                return {"elements": _serialiser(resultat)}
            return {"resultat": _serialiser(resultat)}

        return TypedTool(
            name=operation.name,
            description=operation.description,
            input_schema=operation.input_schema,
            handler=handler,
        )


def ouvrir_trace(
    taches: AgentTaskService,
    *,
    from_agent: str,
    task_type: str,
    payload: dict[str, Any],
    correlation_id: str | None = None,
    requires_approval: bool = False,
) -> Any:
    """Ouvre une ``AgentTask`` (pending → running) ; helper partagé du kit."""
    return taches.start(
        from_agent=from_agent,
        task_type=task_type,
        payload=payload,
        correlation_id=correlation_id,
        requires_approval=requires_approval,
    )


__all__ = [
    "KIT_CAPABILITY",
    "LIMITE_MAX_RECHERCHE",
    "AgentTools",
    "OperationSpec",
    "PageSearchInput",
    "ouvrir_trace",
    "serialiser_entite",
]
