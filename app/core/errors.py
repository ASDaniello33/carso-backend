"""Business exceptions and their FastAPI HTTP mapping (instruction/09 §8)."""

from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


class CarsoError(Exception):
    """Base class for all explicit business errors."""

    status_code = 500
    code = "internal_error"
    message = "Internal error."

    def __init__(
        self,
        message: str | None = None,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        # ``message`` est aussi posé sur l'instance : ``str(exc)`` (ce que renvoie
        # l'API) et ``exc.message`` (ce que lisent les appelants et les tests)
        # doivent dire la même chose — un message précis ne peut pas se perdre
        # derrière le libellé générique de la classe.
        self.message = message or self.message
        super().__init__(self.message)
        self.details = details or {}


class NotFoundError(CarsoError):
    """Requested entity does not exist."""

    status_code = 404
    code = "not_found"
    message = "Entity not found."


class AuthenticationError(CarsoError):
    """Jeton absent, invalide ou expiré — distinct d'un refus de permission."""

    status_code = 401
    code = "unauthenticated"
    message = "Authentication required."


class PermissionDeniedError(CarsoError):
    """Actor lacks the required permission (instruction/08)."""

    status_code = 403
    code = "permission_denied"
    message = "Permission denied."


class ValidationError(CarsoError):
    """Business validation failure (distinct from Pydantic request validation)."""

    status_code = 422
    code = "business_validation_error"
    message = "Business validation failed."


class ConflictError(CarsoError):
    """Operation conflicts with current state (duplicate, concurrent change...)."""

    status_code = 409
    code = "conflict"
    message = "Conflicting state."


class GenerationError(CarsoError):
    """Échec de génération documentaire (bibliothèque absente ou erreur moteur).

    Jamais un fichier vide ni un échec silencieux : un moteur manquant est une
    erreur explicite (convention des extras, cf. ``app.documents.generation``).
    """

    status_code = 422
    code = "document_generation_error"
    message = "Document generation failed."


class ReadOnlyViolationError(CarsoError):
    """A read-only agent attempted a mutation (instruction/10 §7)."""

    status_code = 403
    code = "read_only_violation"
    message = "Read-only agent attempted a mutation."


class AgentLoopError(CarsoError):
    """Collaboration inter-agent refusée : auto-appel, cycle ou profondeur (instruction/10 §8)."""

    status_code = 409
    code = "agent_loop"
    message = "Inter-agent collaboration loop refused."


class AgentTimeoutError(CarsoError):
    """Tâche inter-agent au-delà de son budget de temps (instruction/10 §8)."""

    status_code = 504
    code = "agent_timeout"
    message = "Inter-agent task exceeded its time budget."


class InvalidAgentResponseError(CarsoError):
    """Réponse d'agent non conforme au contrat inter-agent (instruction/05 §7)."""

    status_code = 502
    code = "invalid_agent_response"
    message = "Agent response does not respect the inter-agent contract."


def register_exception_handlers(app: FastAPI) -> None:
    """Map CarsoError subclasses to structured JSON responses."""

    @app.exception_handler(CarsoError)
    async def _carso_error_handler(request: Request, exc: CarsoError) -> JSONResponse:  # noqa: ARG001
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": exc.code,
                    "message": str(exc),
                    "details": exc.details,
                }
            },
        )
