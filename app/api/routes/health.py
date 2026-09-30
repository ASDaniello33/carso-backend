"""Health and readiness endpoints."""

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

from app.core.config import get_settings

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: Literal["ok"]
    app: str
    environment: str
    # Persistance LangGraph (incrément 32, ADR 0009) : ``postgres`` ou
    # ``memoire`` (fallback) + cause éventuelle. Simple lecture d'état process
    # — aucune dépendance I/O ajoutée au liveness.
    checkpointer: dict[str, str | bool | None] | None = None


@router.get("/health", response_model=HealthResponse)
def get_health() -> HealthResponse:
    """Liveness probe: the API is up. No database dependency."""
    from app.agents.checkpointer import etat_checkpointer

    settings = get_settings()
    return HealthResponse(
        status="ok",
        app=settings.app_name,
        environment=settings.environment,
        checkpointer=etat_checkpointer(),
    )
