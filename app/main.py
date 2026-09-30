"""FastAPI application factory."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.agents.manager import get_runtime_manager
from app.api.agui import monter_endpoints_agui
from app.api.agui import router as agui_router
from app.api.agui_pieces_jointes import MiddlewarePiecesJointes
from app.api.routes import (
    affectations,
    agents,
    appels_a_proposition,
    auth,
    beneficiaires,
    documents,
    equipes,
    evenements,
    health,
    lieux,
    lots,
    missions,
    modeles_documents,
    notifications,
    offres,
    organisations,
    parametres,
    recherche,
    sessions,
    social,
    tableau_bord,
)
from app.core.config import get_settings
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging
from app.infrastructure.database import SessionLocal


def restaurer_runtime_agents() -> bool:
    """Applique la configuration d'agents mémorisée en base (au démarrage).

    Best-effort assumé : si la base n'est pas joignable (ou la table absente),
    le backend démarre quand même avec la configuration ``.env``. Aucune erreur
    de configuration d'agent ne doit empêcher le CRUD métier de servir.
    """
    logger = logging.getLogger(__name__)
    try:
        session = SessionLocal()
    except Exception as exc:  # pragma: no cover - dépend de l'environnement
        logger.warning("Runtime agents : session indisponible (%s)", exc)
        return False
    try:
        return get_runtime_manager().restore_from_db(session)
    finally:
        session.close()


def bootstrap_admin() -> bool:
    """Crée le premier administrateur si BOOTSTRAP_ADMIN_* est défini.

    Best-effort (même contrat que ``restaurer_runtime_agents``) : une base
    injoignable au démarrage ne bloque pas l'API ; le compte est créé au
    démarrage suivant. Documenté dans docs/api/AUTH.md.
    """
    from app.application.services import UtilisateurService

    logger = logging.getLogger(__name__)
    try:
        session = SessionLocal()
    except Exception as exc:  # pragma: no cover - dépend de l'environnement
        logger.warning("Bootstrap admin : session indisponible (%s)", exc)
        return False
    try:
        admin = UtilisateurService(session).assurer_bootstrap_admin()
        if admin is not None:
            session.commit()
            logger.info("Bootstrap admin : compte prêt pour %s", admin.email)
        return admin is not None
    except Exception as exc:  # pragma: no cover - dépend de l'environnement
        logger.warning("Bootstrap admin non appliqué (%s)", exc)
        session.rollback()
        return False
    finally:
        session.close()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Charge la config mémorisée, le premier admin et le checkpointer, puis libère."""
    from app.agents.checkpointer import liberer_checkpointer, prechauffer_checkpointer

    prechauffer_checkpointer()
    restaurer_runtime_agents()
    bootstrap_admin()
    yield
    get_runtime_manager().release()
    liberer_checkpointer()


def create_app() -> FastAPI:
    """Build and configure the CARSO backend application."""
    settings = get_settings()
    configure_logging(level=logging_level(settings.debug))

    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        openapi_url=f"{settings.api_v1_prefix}/openapi.json",
        docs_url=f"{settings.api_v1_prefix}/docs",
        lifespan=lifespan,
    )
    register_exception_handlers(app)
    # Pièces jointes du chat : le corps des requêtes AG-UI est normalisé avant
    # routage (le binaire devient une référence Document). Ajouté avant CORS
    # pour que le préflight OPTIONS ne traverse jamais la lecture de corps.
    app.add_middleware(MiddlewarePiecesJointes, prefixe=settings.api_v1_prefix)
    # Frontend CopilotKit / Next (origines locales). Pas de wildcard + credentials.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:3000",
            "http://127.0.0.1:3000",
            "http://localhost:3950",
            "http://127.0.0.1:3950",
        ],
        allow_credentials=True,
        # CopilotKit / AG-UI envoie des en-têtes x-copilotkit-* (et parfois
        # last-event-id) : une allowlist figée fait échouer le préflight OPTIONS
        # et le navigateur annule le POST → TypeError: network error.
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(health.router, prefix=settings.api_v1_prefix)
    app.include_router(auth.router, prefix=settings.api_v1_prefix)
    app.include_router(appels_a_proposition.router, prefix=settings.api_v1_prefix)
    app.include_router(offres.router, prefix=settings.api_v1_prefix)
    app.include_router(lieux.router, prefix=settings.api_v1_prefix)
    app.include_router(affectations.router, prefix=settings.api_v1_prefix)
    app.include_router(documents.router, prefix=settings.api_v1_prefix)
    app.include_router(modeles_documents.router, prefix=settings.api_v1_prefix)
    # Lot C1 — CRUD des entités jusqu'ici sans service (instruction/02).
    app.include_router(organisations.router, prefix=settings.api_v1_prefix)
    app.include_router(equipes.router, prefix=settings.api_v1_prefix)
    app.include_router(lots.router, prefix=settings.api_v1_prefix)
    # ``router_lot`` : lecture/impact/suppression d'un lot par identifiant
    # (``/lots/{id}``, ``/lots/{id}/impact``, ``DELETE /lots/{id}``).
    app.include_router(lots.router_lot, prefix=settings.api_v1_prefix)
    app.include_router(missions.router, prefix=settings.api_v1_prefix)
    app.include_router(beneficiaires.router, prefix=settings.api_v1_prefix)
    app.include_router(sessions.router, prefix=settings.api_v1_prefix)
    app.include_router(agents.router, prefix=settings.api_v1_prefix)
    app.include_router(parametres.router, prefix=settings.api_v1_prefix)
    app.include_router(evenements.router, prefix=settings.api_v1_prefix)
    app.include_router(tableau_bord.router, prefix=settings.api_v1_prefix)
    app.include_router(recherche.router, prefix=settings.api_v1_prefix)
    app.include_router(notifications.router, prefix=settings.api_v1_prefix)
    app.include_router(social.router, prefix=settings.api_v1_prefix)
    app.include_router(agui_router, prefix=settings.api_v1_prefix)
    monter_endpoints_agui(app, settings)

    return app


def logging_level(debug: bool) -> int:
    """Map the debug flag to a logging level."""
    return logging.DEBUG if debug else logging.INFO


app = create_app()

def _demarrer() -> None:
    """Point d'entrée ``python main.py`` / ``uv run main.py``.

    L'absence de modèle agent ne doit jamais empêcher uvicorn de rester en vie.
    """
    uvicorn.run(app, host="0.0.0.0", port=8000)


if __name__ == "__main__":
    _demarrer()
