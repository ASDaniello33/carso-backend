"""Assemblage des tools extra (documents, scripts) pour un agent.

Les contrats déclarent ``fill_template``, ``generer_document``, etc.
Sans cet assembleur, AG-UI exposait un graphe **sans** ces tools.

**Une transaction par appel de tool.** Les tools documentaires délèguent à
``DocumentService``, qui ne fait ni ``commit`` ni ``rollback`` : la transaction
appartient à l'appelant (instruction/04 §5). Dans le chemin agent, l'appelant
n'est pas une route FastAPI — c'est le tool lui-même. L'assembleur ouvre donc
la session, la commite en cas de succès et la ferme : sans cela, un document
généré par un agent restait non commité (et disparaissait avec la session).

La session de l'appel en cours est portée par une ``ContextVar`` : deux appels
de tool simultanés (le harnais peut paralléliser) ne partagent jamais la même
session, et un tool qui ignore le contexte retombe sur une session neuve.
"""

from __future__ import annotations

from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import replace
from typing import Any

from sqlalchemy.orm import Session

from app.agents.base import AgentDefinition
from app.application.services import DocumentService
from app.core.config import Settings, get_settings
from app.tools.base import TypedTool
from app.tools.documents import build_document_tools
from app.tools.permissions import AgentIdentity
from app.tools.scripts import build_run_python_script

#: Session de l'appel de tool documentaire en cours (jamais partagée entre appels).
_SESSION_OUTIL: ContextVar[Session | None] = ContextVar(
    "session_outil_documentaire", default=None
)


def tools_documents_pour(
    definition: AgentDefinition,
    session_factory: Callable[[], Session],
    *,
    settings: Settings | None = None,
) -> tuple[TypedTool, ...]:
    """Tools documentaires dont le nom est dans ``definition.tools``.

    Chaque tool invoqué s'exécute dans **une** transaction commitée (voir la
    note du module) : un document généré est réellement enregistré.
    """
    _ = settings or get_settings()

    def _service() -> DocumentService:
        """Service sur la session de l'appel en cours (sinon : session neuve)."""
        session = _SESSION_OUTIL.get() or session_factory()
        return DocumentService(session, actor_id=definition.agent_id)

    construits = build_document_tools(
        identite=AgentIdentity(agent_id=definition.agent_id),
        policy=definition.policy,
        service_factory=_service,
    )
    autorises = definition.allowed_tools()
    return tuple(
        _dans_une_transaction(tool, session_factory)
        for tool in construits
        if tool.name in autorises
    )


def _dans_une_transaction(
    tool: TypedTool, session_factory: Callable[[], Session]
) -> TypedTool:
    """Enveloppe un tool documentaire : un appel = une transaction commitée."""

    def handler(payload: dict[str, Any]) -> dict[str, Any]:
        session = session_factory()
        jeton = _SESSION_OUTIL.set(session)
        try:
            resultat = tool.handler(payload)
            session.commit()
            return resultat
        except Exception:
            session.rollback()
            raise
        finally:
            _SESSION_OUTIL.reset(jeton)
            session.close()

    return replace(tool, handler=handler)


def tools_scripts_pour(
    definition: AgentDefinition,
    *,
    settings: Settings | None = None,
) -> tuple[TypedTool, ...]:
    """``run_python_script`` seulement s'il est au contrat et si sandbox OK."""
    if "run_python_script" not in definition.allowed_tools():
        return ()
    cfg = settings or get_settings()
    if not getattr(cfg, "scripts_root", "") and not getattr(cfg, "storage_root", ""):
        return ()
    try:
        tool = build_run_python_script(
            identite=AgentIdentity(agent_id=definition.agent_id),
            policy=definition.policy,
            settings=cfg,
        )
    except Exception:
        return ()
    return (tool,)


__all__ = ["tools_documents_pour", "tools_scripts_pour"]
