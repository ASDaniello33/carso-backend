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
    # Plafond de dépôt documentaire, en octets — **source unique** du plafond.
    # L'interface refuse un fichier avant l'envoi et annonce la limite : elle
    # doit donc lire la même valeur que le service de stockage, sinon elle
    # bloque sur un nombre devine (le defaut etait 20 Mio code en dur dans
    # trois composants). Le liveness etant public, cette borne n'expose aucune
    # donnee metier : c'est une limite technique, pas une information interne.
    max_upload_bytes: int
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
        max_upload_bytes=settings.max_upload_bytes,
        checkpointer=etat_checkpointer(),
    )
