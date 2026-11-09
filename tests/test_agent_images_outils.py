"""Livraison des images d'outil au modèle — tests **hors ligne**.

Constat de recette (09/11) : une image placée dans un message ``tool`` est
ignorée par la passerelle (le modèle répond « je ne vois aucune image », y
compris en HTTP brut hors de CARSO), alors que la même image dans un message
``user`` est lue correctement. Ces tests fixent le correctif : l'image du
résultat d'outil est replacée dans un message utilisateur, sans rien retirer de
la fiche du ``ToolMessage``.
"""

from __future__ import annotations

from typing import Any

import pytest

pytest.importorskip("langchain_core")
pytest.importorskip("langchain_openai")

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage  # noqa: E402

from app.agents.images_outils import (  # noqa: E402
    TEXTE_SANS_FICHE,
    deplacer_images_outils,
)
from app.agents.providers import build_chat_model  # noqa: E402

IMAGE: dict[str, Any] = {
    "type": "image_url",
    "image_url": {"url": "data:image/png;base64,iVBORw0KGgo="},
}
FICHE = '{"ok": true, "nom": "plan.png", "vision_activee": true}'


def _resultat_image(contenu: list[Any] | None = None, nom: str = "lire_image") -> ToolMessage:
    return ToolMessage(
        content=contenu if contenu is not None else [{"type": "text", "text": FICHE}, IMAGE],
        tool_call_id="c1",
        name=nom,
    )


def _sequence(contenu: list[Any] | None = None) -> list[Any]:
    return [
        HumanMessage("Lis l'image du document."),
        AIMessage(content="", tool_calls=[{"id": "c1", "name": "lire_image", "args": {}}]),
        _resultat_image(contenu),
    ]


class TestDeplacementImagesOutils:
    def test_l_image_d_un_outil_part_dans_un_message_utilisateur(self) -> None:
        sequence = _sequence()

        resultat = deplacer_images_outils(sequence)

        assert [type(message).__name__ for message in resultat] == [
            "HumanMessage",
            "AIMessage",
            "ToolMessage",
            "HumanMessage",
        ]
        porteur = resultat[-1]
        types = [bloc.get("type") for bloc in porteur.content]
        assert "image_url" in types
        assert any("lire_image" in bloc.get("text", "") for bloc in porteur.content)

    def test_la_fiche_du_tool_est_conservee_en_texte(self) -> None:
        """Rien n'est retiré du résultat : l'audit et le modèle lisent la même fiche."""
        resultat = deplacer_images_outils(_sequence())

        assert resultat[2].content == FICHE
        assert resultat[2].tool_call_id == "c1"

    def test_un_resultat_sans_image_est_laisse_intact(self) -> None:
        sequence = [
            HumanMessage("question"),
            ToolMessage(content='{"ok": true}', tool_call_id="c1", name="un_outil"),
        ]

        resultat = deplacer_images_outils(sequence)

        assert resultat == sequence
        assert all(a is b for a, b in zip(resultat, sequence, strict=True))

    def test_un_resultat_fait_uniquement_d_images_n_est_jamais_vide(self) -> None:
        resultat = deplacer_images_outils(_sequence([IMAGE]))

        assert resultat[2].content == TEXTE_SANS_FICHE

    def test_plusieurs_images_donnent_un_seul_message_utilisateur(self) -> None:
        resultat = deplacer_images_outils(
            _sequence([{"type": "text", "text": FICHE}, IMAGE, IMAGE])
        )

        assert len(resultat) == 4
        images = [bloc for bloc in resultat[-1].content if bloc.get("type") == "image_url"]
        assert len(images) == 2


class TestChargeUtileEnvoyee:
    """Ce que le provider reçoit réellement, pas ce qu'on croit lui envoyer."""

    def test_une_image_survit_a_la_normalisation_du_provider(self) -> None:
        modele = build_chat_model()
        sequence = deplacer_images_outils(_sequence())

        payload = modele._get_request_payload(sequence)

        porteurs = [
            message for message in payload["messages"] if isinstance(message.get("content"), list)
        ]
        assert len(porteurs) == 1
        assert porteurs[0]["role"] == "user"
        types = [bloc.get("type") for bloc in porteurs[0]["content"]]
        assert "image_url" in types

    def test_le_raisonnement_rejoue_reste_retire(self) -> None:
        """Non-régression INC-140 : le raisonnement ne retourne pas au provider."""
        modele = build_chat_model()
        sequence = [
            HumanMessage("bonjour"),
            AIMessage(
                content=[
                    {"type": "reasoning", "text": "je réfléchis"},
                    {"type": "text", "text": "Bonjour !"},
                ]
            ),
        ]

        payload = modele._get_request_payload(sequence)

        assert payload["messages"][1]["content"] == "Bonjour !"

    def test_le_raisonnement_part_meme_avec_une_image(self) -> None:
        modele = build_chat_model()
        sequence = [
            HumanMessage(content=[{"type": "text", "text": "couleur ?"}, IMAGE]),
            AIMessage(
                content=[
                    {"type": "reasoning", "text": "je réfléchis"},
                    {"type": "text", "text": "vu"},
                ]
            ),
        ]

        payload = modele._get_request_payload(sequence)

        assert isinstance(payload["messages"][0]["content"], list)
        assert payload["messages"][1]["content"] == "vu"


class TestDansLeGrapheReel:
    """Bout en bout : un outil rend une image, le modèle la reçoit vraiment.

    Vérifie la **pile de middlewares** du harnais, pas la fonction seule : un
    correctif correct mais jamais branché ne servirait à rien.
    """

    @staticmethod
    def _definition() -> Any:
        from app.agents.base import AgentDefinition
        from app.tools.permissions import PermissionPolicy

        return AgentDefinition(
            agent_id="AgentTestImages",
            display_name="Agent de test",
            description="d",
            policy=PermissionPolicy(allowed_capabilities=frozenset({"image"})),
            tools=("outil_image",),
        )

    def test_le_modele_recoit_l_image_d_un_outil(self) -> None:
        from langchain_core.messages import HumanMessage
        from pydantic import BaseModel, Field

        from app.agents.harness import AgentRuntimeConfig, create_carso_deep_agent
        from app.tools.base import TypedTool
        from tests.agent_fixtures import ScriptedChatModel, final_message, tool_call

        class _Entree(BaseModel):
            """Aucun paramètre utile : l'outil rend toujours la même image."""

            detail: str = Field(default="", description="ignoré")

        outil = TypedTool(
            name="outil_image",
            description="Rend une image",
            input_schema=_Entree,
            handler=lambda _payload: {
                "ok": True,
                "message": "image jointe",
                "images": [{"mime": "image/png", "base64": "iVBORw0KGgo=", "legende": "vue"}],
            },
            multimodal=True,
        )
        modele = ScriptedChatModel(
            responses=[tool_call("outil_image", {}), final_message("image reçue")]
        )
        graphe = create_carso_deep_agent(
            definition=self._definition(),
            tools=[outil],
            runtime=AgentRuntimeConfig(model=modele),
        )

        graphe.invoke(
            {"messages": [HumanMessage("montre l'image")]},
            config={"configurable": {"thread_id": "images-outils"}},
        )

        # Ce que le modèle a réellement vu au tour suivant l'outil.
        dernier = modele.received[-1]
        porteurs = [
            message
            for message in dernier
            if isinstance(getattr(message, "content", None), list)
            and any(
                isinstance(bloc, dict) and bloc.get("type") == "image_url"
                for bloc in message.content
            )
        ]
        assert porteurs, [type(m).__name__ for m in dernier]
        assert type(porteurs[0]).__name__ == "HumanMessage"


class TestOrdreAvecLAssainissement:
    def test_un_resultat_orphelin_est_ecarte_puis_l_image_est_livree(self) -> None:
        """Ordre du harnais : assainir d'abord, déplacer l'image ensuite.

        Un ``ToolMessage`` sans appel déclaré serait écarté par
        l'assainissement ; l'image qu'il portait ne doit pas produire un message
        utilisateur orphelin.
        """
        from app.agents.historique_outils import assainir_sequence_outils

        orphelin = _resultat_image()
        sequence = [HumanMessage("question"), orphelin]

        assaini = assainir_sequence_outils(sequence)
        resultat = deplacer_images_outils(assaini)

        assert not any(isinstance(m, ToolMessage) for m in resultat)
        assert not any(
            isinstance(m, HumanMessage) and isinstance(m.content, list) for m in resultat
        )
