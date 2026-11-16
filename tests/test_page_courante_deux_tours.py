"""Régression du 15/11 — le message du second tour disparaissait.

**Symptôme observé en navigateur** (CARSO, agent_generaliste_readonly) : tour 1
« Bonjour » → réponse normale ; tour 2 « Dis exactement "CARSO" » → le message
n'apparaît plus et l'agent réaffiche la réponse du tour 1.

**Cause** : ``injecter_page_dans_payload`` retirait l'en-tête ``[Page ouverte …]``
des messages **antérieurs**. Le client, lui, renvoie l'historique tel que
l'instantané le lui a rendu, donc **avec** l'en-tête. ``ag_ui_langgraph`` compare
les messages entrants à ceux du checkpoint par identifiant
(``_detect_edited_human_message``) : même identifiant, contenu différent ⇒ édition
de l'utilisateur ⇒ retour arrière et rejeu du tour précédent. La réponse était
régénérée et le message neuf n'était jamais traité.

Ce test rejoue exactement les deux tours à travers ``ag_ui_langgraph``, avec un
modèle scripté : aucun réseau, aucune base. Le graphe est celui de la
**production** — ``create_carso_deep_agent`` et sa pile de middlewares complète
(contexte utilisateur, affichage, exclusion d'outils, cohérence de l'historique,
page courante, images d'outils) — et non un ``create_agent`` nu : l'ordre des
middlewares et les convertisseurs de messages sont ceux du produit.
"""

from __future__ import annotations

import json
from typing import Any

from ag_ui_langgraph import LangGraphAgent, add_langgraph_fastapi_endpoint
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import MemorySaver

from app.agents.generaliste_readonly import DEFINITION
from app.agents.harness import AgentRuntimeConfig, create_carso_deep_agent
from app.agents.page_courante import DESCRIPTION_CONTEXTE, injecter_page_dans_payload
from tests.agent_fixtures import ScriptedChatModel, final_message

PREMIER = "Bonjour"
SECOND = 'Dis exactement "CARSO"'

CONTEXTE_PAGE = [
    {
        "description": DESCRIPTION_CONTEXTE,
        "value": json.dumps({"type": "page", "id": None, "route": "/dashboard"}),
    }
]


def _client(modele: ScriptedChatModel) -> TestClient:
    """Agent de production (agent généraliste), modèle scripté, sans réseau."""
    graphe = create_carso_deep_agent(
        definition=DEFINITION,
        tools=[],
        runtime=AgentRuntimeConfig(model=modele, checkpointer=MemorySaver()),
    )
    agent = LangGraphAgent(name="agent_generaliste_readonly", graph=graphe, description="test")
    application = FastAPI()
    add_langgraph_fastapi_endpoint(application, agent, path="/agui/test")
    return TestClient(application)


def _tour(client: TestClient, thread_id: str, messages: list[dict[str, Any]]) -> dict[str, Any]:
    """Un tour AG-UI : le payload passe par le middleware de page, comme en prod."""
    charge = injecter_page_dans_payload(
        {
            "threadId": thread_id,
            "runId": f"run-{len(messages)}",
            "messages": messages,
            "tools": [],
            "context": CONTEXTE_PAGE,
            "forwardedProps": {},
            "state": {},
            "resume": [],
        }
    )
    reponse = client.post(
        "/agui/test", json=charge, headers={"Accept": "text/event-stream"}
    )
    assert reponse.status_code == 200, reponse.text[:400]

    evenements: list[dict[str, Any]] = []
    for ligne in reponse.text.splitlines():
        if not ligne.startswith("data:"):
            continue
        contenu = ligne[5:].strip()
        if not contenu or contenu == "[DONE]":
            continue
        evenements.append(json.loads(contenu))

    erreurs = [e for e in evenements if e.get("type") == "RUN_ERROR"]
    assert not erreurs, erreurs[0].get("message")

    textes: list[str] = []
    instantane: list[dict[str, Any]] = []
    for evenement in evenements:
        if evenement.get("type") == "TEXT_MESSAGE_CONTENT":
            textes.append(str(evenement.get("delta") or ""))
        if evenement.get("type") == "MESSAGES_SNAPSHOT":
            instantane = list(evenement.get("messages") or [])
    # Le modèle scripté ne diffuse pas de jetons : la réponse se lit dans
    # l'instantané final (dernier message assistant).
    assistants = [m for m in instantane if m.get("role") == "assistant"]
    texte = str(assistants[-1].get("content") or "").strip() if assistants else ""
    return {"texte": "".join(textes).strip() or texte, "messages": instantane}


def _historique(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Ce que le client renvoie au tour suivant : l'instantané tel quel."""
    return [
        {"id": m.get("id"), "role": m.get("role"), "content": m.get("content")}
        for m in messages
        if m.get("role") in {"user", "assistant"}
    ]


def test_le_second_message_est_traite_et_non_rejoue() -> None:
    modele = ScriptedChatModel(
        responses=[final_message("Bonjour Daniello !"), final_message("CARSO")]
    )
    client = _client(modele)
    thread_id = "fil-deux-tours"

    tour1 = _tour(
        client, thread_id, [{"id": "m0", "role": "user", "content": PREMIER}]
    )
    assert "Bonjour Daniello" in tour1["texte"]
    assert [m["role"] for m in tour1["messages"]] == ["user", "assistant"]

    tour2 = _tour(
        client,
        thread_id,
        [*_historique(tour1["messages"]), {"id": "m2", "role": "user", "content": SECOND}],
    )

    # Le second message est bien traité…
    assert tour2["texte"] == "CARSO"
    ids = [m["id"] for m in tour2["messages"]]
    assert "m2" in ids
    # …et l'état contient les deux tours, sans rejeu ni doublon.
    assert len(tour2["messages"]) == 4
    assert len(ids) == len(set(ids))
    assert modele.invocations == 2

    # La pile de middlewares de production a bien fait son travail : le modèle
    # ne lit qu'**une** page, celle du dernier message (l'en-tête périmé du
    # message antérieur est retiré de la requête, pas de l'état du fil).
    recus = modele.received[-1]
    entetes = sum(
        str(message.content).count("[Page ouverte") for message in recus if message.type == "human"
    )
    assert entetes == 1
    assert "[Page ouverte" in str(recus[0].content)  # l'ancien message est encore là
    assert tour2["messages"][0]["content"].startswith("[Page ouverte")


def test_le_message_deja_envoye_garde_son_contenu_a_l_identique() -> None:
    """Le payload ne doit jamais diverger de ce que le client renverra."""
    modele = ScriptedChatModel(responses=[final_message("Bonjour Daniello !")])
    client = _client(modele)

    tour1 = _tour(client, "fil-identite", [{"id": "m0", "role": "user", "content": PREMIER}])
    envoye = _historique(tour1["messages"])

    # Tour 2 : le payload réellement transmis au graphe.
    charge = injecter_page_dans_payload(
        {
            "threadId": "fil-identite",
            "runId": "run-2",
            "messages": [*envoye, {"id": "m2", "role": "user", "content": SECOND}],
            "tools": [],
            "context": CONTEXTE_PAGE,
            "forwardedProps": {},
            "state": {},
            "resume": [],
        }
    )

    assert charge["messages"][0]["content"] == envoye[0]["content"]
    assert charge["messages"][1]["content"] == envoye[1]["content"]
