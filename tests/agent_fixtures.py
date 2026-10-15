"""Fixtures d'agents : modèle de chat **scripté**, sans appel réseau.

Les tests d'agents ne doivent dépendre d'aucun provider : ce modèle reproduit
exactement ce dont le harnais a besoin (messages, ``tool_calls``, ``bind_tools``)
et permet de vérifier la boucle complète — décision du modèle → tool → service →
zone de proposition — de façon déterministe.

Les appels réseau réels sont réservés au fichier ``test_agent_live.py``, opt-in
(``RUN_AGENT_TESTS=1``).
"""

from __future__ import annotations

from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field

_CALL_ID = 0


def next_call_id(prefix: str = "call") -> str:
    """Identifiant de tool call unique (le compteur global évite les collisions)."""
    global _CALL_ID
    _CALL_ID += 1
    return f"{prefix}-{_CALL_ID}"


def tool_call(name: str, arguments: dict[str, Any]) -> AIMessage:
    """Message d'agent demandant l'exécution d'un tool."""
    return AIMessage(
        content="",
        tool_calls=[
            {"name": name, "args": arguments, "id": next_call_id(), "type": "tool_call"}
        ],
    )


def final_message(texte: str) -> AIMessage:
    """Message final d'agent (fin de boucle)."""
    return AIMessage(content=texte)


class ScriptedChatModel(BaseChatModel):
    """Modèle scripté : rejoue une liste de réponses prédéfinies.

    Attributes:
        responses: réponses successives ; la dernière est répétée.
        invocations: nombre d'appels effectivement reçus.
        bound_tools: noms des tools liés par le harnais (``bind_tools``).
        received: historique des messages reçus (liste de listes), pour vérifier
            ce que l'agent a réellement vu (ex. contenu d'un tool de lecture).
    """

    responses: list[AIMessage] = Field(default_factory=list)
    invocations: int = 0
    bound_tools: list[str] = Field(default_factory=list)
    received: list[list[BaseMessage]] = Field(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools: Any, **kwargs: Any) -> ScriptedChatModel:
        self.bound_tools = [
            str(getattr(tool, "name", tool)) for tool in tools
        ]
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        self.received.append(list(messages))
        index = min(self.invocations, len(self.responses) - 1)
        self.invocations += 1
        return ChatResult(generations=[ChatGeneration(message=self.responses[index])])

    def tool_messages_recus(self) -> str:
        """Concaténation des sorties de tools déjà reçues par le modèle."""
        contenus: list[str] = []
        for messages in self.received:
            for message in messages:
                if message.__class__.__name__ == "ToolMessage":
                    contenus.append(str(message.content))
        return "\n".join(contenus)
