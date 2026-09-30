"""Lie les outils frontend AG-UI au modèle, sans les exécuter métier.

AG-UI pose les hooks client dans ``state["tools"]``. Deep Agents n'en lit
aucun : le modèle ne voit que le contrat backend et répond « je n'ai pas
ces outils ». Ce middleware :

1. enregistre un **stub** LangChain par nom allowlisté de l'agent ;
2. au tour modèle, fusionne les schémas AG-UI filtrés dans ``request.tools``.

Le stub renvoie ``{ok: true, rendu: "client"}``. Zéro session SQL, zéro
service. Un nom hors catalogue n'est jamais stubé. Un outil ``hitl`` est
stubé **et** interrompu (``interrupt_on`` / ``respond``) : le modèle le voit,
le ToolNode n'écrit rien, l'humain répond à la place.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.agents.frontend_tools_catalogue import (
    SCHEMAS_FRONTEND,
    extraire_outils_etat,
    filtrer_outils_agui,
    outils_pour_agent,
)
from app.tools.base import TypedTool, to_langchain_tool

try:
    from langchain.agents.middleware.types import AgentMiddleware as _AgentMiddleware
except ImportError:  # pragma: no cover - extra agents absent
    _AgentMiddleware = object  # type: ignore[misc,assignment]


class PayloadFrontend(BaseModel):
    """Schéma permissif : les arguments sont ceux du hook client, pas les nôtres."""

    model_config = ConfigDict(extra="allow")

    note: str | None = Field(
        default=None,
        description="Ignoré. Les champs métier viennent du schéma client.",
    )


def _handler_stub(payload: dict[str, Any]) -> dict[str, Any]:
    """Aucune mutation : le rendu réel est dans le navigateur.

    On **relaye** le payload validé. Après ``MESSAGES_SNAPSHOT``, CopilotKit
    parse parfois ``arguments`` (déjà un objet) en ``{}`` : le chat n'a plus
    que ``result`` pour peindre le tableau.
    """
    relais = {
        cle: valeur
        for cle, valeur in payload.items()
        if cle not in {"ok", "rendu", "error"}
    }
    return {"ok": True, "rendu": "client", **relais}


def _schema_frontend(name: str) -> type[BaseModel]:
    """Schéma réel de l'outil — le modèle doit envoyer les mêmes champs que le hook."""
    return SCHEMAS_FRONTEND.get(name, PayloadFrontend)


def stub_frontend(name: str, description: str) -> TypedTool:
    """Tool typé de présentation — exécutable par le ToolNode, inoffensif."""
    return TypedTool(
        name=name,
        description=description,
        input_schema=_schema_frontend(name),
        handler=_handler_stub,
        tags=("frontend", "affichage"),
    )


def stubs_pour_agent(agent_id: str) -> tuple[Any, ...]:
    """Stubs LangChain des outils d'affichage de cet agent.

    Les outils HITL sont stubés aussi : sans ça le modèle ne les voit pas.
    L'exécution est coupée par ``interrupt_on`` (décision ``respond``).
    """
    return tuple(
        to_langchain_tool(stub_frontend(outil.name, outil.description))
        for outil in outils_pour_agent(agent_id)
    )


def _nom_outil(item: object) -> str | None:
    if isinstance(item, dict):
        nom = item.get("name")
        return nom if isinstance(nom, str) else None
    return getattr(item, "name", None)


def fusionner_outils_modele(
    request_tools: list[Any],
    state: object,
    agent_id: str,
) -> list[Any]:
    """Ajoute les schémas AG-UI allowlistés absents de ``request.tools``."""
    deja = {nom for item in request_tools if (nom := _nom_outil(item))}
    extra = filtrer_outils_agui(extraire_outils_etat(state), agent_id)
    fusion = list(request_tools)
    for item in extra:
        nom_extra = item.get("name")
        if isinstance(nom_extra, str) and nom_extra not in deja:
            fusion.append(item)
            deja.add(nom_extra)
    return fusion


class FrontendToolsMiddleware(_AgentMiddleware):
    """Middleware Deep Agents : stubs d'affichage + fusion AG-UI au bind.

    Hérite d'``AgentMiddleware`` quand l'extra ``agents`` est installé ;
    sinon de ``object``. ``create_carso_deep_agent`` n'instancie cette
    classe que si l'agent a des outils d'affichage.
    """

    def __init__(self, agent_id: str) -> None:
        self.agent_id = agent_id
        self.tools = list(stubs_pour_agent(agent_id))

    @property
    def name(self) -> str:
        return "FrontendToolsMiddleware"

    def wrap_model_call(self, request: Any, handler: Any) -> Any:
        fusion = fusionner_outils_modele(list(request.tools), request.state, self.agent_id)
        if fusion == list(request.tools):
            return handler(request)
        return handler(request.override(tools=fusion))

    async def awrap_model_call(self, request: Any, handler: Any) -> Any:
        fusion = fusionner_outils_modele(list(request.tools), request.state, self.agent_id)
        if fusion == list(request.tools):
            return await handler(request)
        return await handler(request.override(tools=fusion))


def serialiser_stub(resultat: dict[str, Any]) -> str:
    """Sortie JSON du stub (tests / messages outil)."""
    return json.dumps(resultat, ensure_ascii=False)
