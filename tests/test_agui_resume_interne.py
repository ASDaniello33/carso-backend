"""Résumé de session du middleware : invisible dans le flux AG-UI (INC-127).

Recette du 06/11 : sur un fil long, l'agent a répondu par le **résumé de
session** de ``SummarizationMiddleware`` (``## SESSION INTENT`` / ``## SUMMARY``
/ ``## ARTIFACTS`` / ``## NEXT STEPS``) collé devant sa vraie réponse.

Trois niveaux sont verrouillés ici :

1. la **preuve du mécanisme** — sur un graphe LangGraph réel
   (``create_agent`` + ``SummarizationMiddleware``), l'appel de résumé produit
   des ``on_chat_model_stream`` marqués ``lc_source="summarization"`` et
   ``lc_internal_call``, **avant** ceux de la réponse ;
2. la **reconnaissance** de ces événements par :func:`est_appel_interne` ;
3. le **filtrage** : le décorateur retire ces événements du flux et laisse
   intacte la réponse de l'agent (paires START/END préservées).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator

from ag_ui.core.events import (
    RunFinishedEvent,
    TextMessageContentEvent,
    TextMessageEndEvent,
    TextMessageStartEvent,
)

from app.api.agui_resume_interne import (
    ResumeInterneMasque,
    est_appel_interne,
)

SUMMARY = "## SESSION INTENT\nRésumé interne du middleware"
REPLY = "Réponse réelle de l'agent"


def _evenement_flux(contenu: str, *, interne: bool) -> TextMessageContentEvent:
    """Événement AG-UI tel que le produit ``ag_ui_langgraph``."""
    meta: dict[str, object] = {}
    if interne:
        meta = {"lc_source": "summarization", "lc_internal_call": "jeton-aleatoire"}
    return TextMessageContentEvent(
        type="TEXT_MESSAGE_CONTENT",
        message_id="m1" if interne else "m2",
        delta=contenu,
        raw_event={"event": "on_chat_model_stream", "metadata": meta},
    )


# --- 1. Preuve du mécanisme (graphe LangGraph réel) -------------------------


def _construire_modele_flux():
    """Faux modèle qui distingue l'appel de résumé de la réponse de l'agent.

    Le prompt de résumé contient « Context Extraction Assistant » (prompt par
    défaut de LangChain) : le modèle répond alors :data:`SUMMARY`, sinon
    :data:`REPLY`. Il diffuse ses tokens, comme un provider réel — condition du
    défaut observé.
    """
    from langchain_core.language_models.chat_models import BaseChatModel
    from langchain_core.messages import AIMessage, AIMessageChunk
    from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult

    class ModeleFlux(BaseChatModel):
        @property
        def _llm_type(self) -> str:
            return "carso-modele-flux-test"

        def _choix(self, messages) -> str:  # noqa: ANN001
            texte = " ".join(str(getattr(m, "content", "")) for m in messages)
            return SUMMARY if "Context Extraction Assistant" in texte else REPLY

        def _generate(  # noqa: ANN001
            self, messages, stop=None, run_manager=None, **kwargs
        ) -> ChatResult:
            return ChatResult(
                generations=[ChatGeneration(message=AIMessage(content=self._choix(messages)))]
            )

        def _stream(  # noqa: ANN001
            self, messages, stop=None, run_manager=None, **kwargs
        ) -> Iterator[ChatGenerationChunk]:
            for mot in self._choix(messages).split(" "):
                yield ChatGenerationChunk(message=AIMessageChunk(content=mot + " "))

        async def _astream(  # noqa: ANN001
            self, messages, stop=None, run_manager=None, **kwargs
        ) -> AsyncIterator[ChatGenerationChunk]:
            for morceau in self._stream(messages, stop, run_manager, **kwargs):
                yield morceau

    return ModeleFlux()


def test_l_appel_de_resume_diffuse_des_tokens_marques_summarization() -> None:
    """Le résumé sort en ``on_chat_model_stream`` marqués — avant la réponse."""
    asyncio.run(_verifier_le_flux_reel())


async def _verifier_le_flux_reel() -> None:
    from langchain.agents import create_agent
    from langchain.agents.middleware import SummarizationMiddleware
    from langchain_core.messages import AIMessage, HumanMessage

    modele = _construire_modele_flux()
    graphe = create_agent(
        model=modele,
        tools=[],
        middleware=[
            SummarizationMiddleware(
                model=modele, trigger=("tokens", 1), keep=("messages", 2)
            )
        ],
    )
    historique = [
        HumanMessage(content="tour 1"),
        AIMessage(content="ok 1"),
        HumanMessage(content="tour 2"),
        AIMessage(content="ok 2"),
        HumanMessage(content="tour 3 " * 5),
    ]

    vus: list[tuple[bool, str]] = []
    async for evenement in graphe.astream_events({"messages": historique}, version="v2"):
        if evenement["event"] != "on_chat_model_stream":
            continue
        contenu = evenement["data"]["chunk"].content or ""
        if not contenu:
            continue
        # Reconnaissance par la même règle que le décorateur.
        vu = TextMessageContentEvent(
            type="TEXT_MESSAGE_CONTENT",
            message_id="m",
            delta=contenu,
            raw_event=evenement,
        )
        vus.append((est_appel_interne(vu), contenu))

    assert vus, "aucun token diffusé : le test ne prouverait rien"
    resume = "".join(c for interne, c in vus if interne)
    reponse = "".join(c for interne, c in vus if not interne)

    assert "SESSION INTENT" in resume, "l'appel de résumé doit être reconnu"
    assert reponse.strip().startswith("Réponse réelle"), reponse
    assert "SESSION INTENT" not in reponse
    # Ordre observé en production : le résumé sort avant la réponse.
    assert vus.index(next(v for v in vus if v[0])) < len(vus) - 1


# --- 2. Reconnaissance -------------------------------------------------------


def test_reconnait_un_appel_interne_par_la_marque_de_source() -> None:
    assert est_appel_interne(_evenement_flux(SUMMARY, interne=True))


def test_reconnait_un_appel_interne_par_la_marque_generique_de_middleware() -> None:
    """``lc_internal_call`` couvre aussi les futurs appels internes."""
    evenement = TextMessageContentEvent(
        type="TEXT_MESSAGE_CONTENT",
        message_id="m3",
        delta="compaction",
        raw_event={"event": "on_chat_model_stream", "metadata": {"lc_internal_call": "jeton"}},
    )
    assert est_appel_interne(evenement)


def test_ne_reconnait_pas_la_reponse_de_l_agent() -> None:
    assert not est_appel_interne(_evenement_flux(REPLY, interne=False))


def test_un_evenement_sans_evenement_brut_n_est_pas_interne() -> None:
    assert not est_appel_interne(
        RunFinishedEvent(type="RUN_FINISHED", thread_id="t", run_id="r")
    )
    assert not est_appel_interne(TextMessageContentEvent(
        type="TEXT_MESSAGE_CONTENT", message_id="m", delta="x"
    ))


def test_lit_aussi_un_evenement_en_dict() -> None:
    """Robustesse : un événement relayé en dict porte la même marque."""
    assert est_appel_interne(
        {
            "type": "TEXT_MESSAGE_CONTENT",
            "raw_event": {"metadata": {"lc_source": "summarization"}},
        }
    )


# --- 3. Filtrage -------------------------------------------------------------


class _AgentFactice:
    """Agent intérieur : un flux contenant le résumé puis la réponse."""

    def __init__(self, evenements: list[object]) -> None:
        self._evenements = evenements
        self.clone_appele = False

    def clone(self) -> _AgentFactice:
        self.clone_appele = True
        return _AgentFactice(self._evenements)

    async def run(self, _input: object):  # noqa: ANN201
        for evenement in self._evenements:
            yield evenement


def _flux() -> list[object]:
    return [
        TextMessageStartEvent(
            type="TEXT_MESSAGE_START", message_id="m1", role="assistant",
            raw_event={"event": "on_chat_model_stream", "metadata": {"lc_source": "summarization"}},
        ),
        _evenement_flux(SUMMARY, interne=True),
        TextMessageEndEvent(
            type="TEXT_MESSAGE_END", message_id="m1",
            raw_event={"event": "on_chat_model_end", "metadata": {"lc_source": "summarization"}},
        ),
        TextMessageStartEvent(
            type="TEXT_MESSAGE_START", message_id="m2", role="assistant",
            raw_event={"event": "on_chat_model_stream", "metadata": {}},
        ),
        _evenement_flux(REPLY, interne=False),
        TextMessageEndEvent(
            type="TEXT_MESSAGE_END", message_id="m2",
            raw_event={"event": "on_chat_model_end", "metadata": {}},
        ),
    ]


def test_le_decorateur_retire_le_resume_et_garde_la_reponse() -> None:
    masque = ResumeInterneMasque(_AgentFactice(_flux()))

    async def _collecter() -> list:
        return [e async for e in masque.run({})]

    sortie = asyncio.run(_collecter())

    ids = [getattr(e, "message_id", None) for e in sortie]
    assert "m1" not in ids, "le message du résumé ne doit jamais sortir"
    assert ids == ["m2", "m2", "m2"]
    deltas = "".join(getattr(e, "delta", "") for e in sortie)
    assert deltas == REPLY


def test_les_paires_start_end_de_la_reponse_restent_equilibrees() -> None:
    masque = ResumeInterneMasque(_AgentFactice(_flux()))

    async def _collecter() -> list:
        return [e async for e in masque.run({})]

    types = [e.type for e in asyncio.run(_collecter())]

    assert types == ["TEXT_MESSAGE_START", "TEXT_MESSAGE_CONTENT", "TEXT_MESSAGE_END"]


def test_clone_isole_l_etat_et_conserve_le_decorateur() -> None:
    interieur = _AgentFactice([])
    clone = ResumeInterneMasque(interieur).clone()

    assert isinstance(clone, ResumeInterneMasque)
    assert interieur.clone_appele is True


def test_le_decorateur_transmet_les_attributs_de_l_agent() -> None:
    class _Agent:
        name = "agent_generateur_offre"

    assert ResumeInterneMasque(_Agent()).name == "agent_generateur_offre"
