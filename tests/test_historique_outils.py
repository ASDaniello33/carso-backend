"""Tests — historique d'outils rendu conforme avant l'appel au modèle.

Constat du 31/10 :

    Invalid parameter: messages with role 'tool' must be a response to a
    preceeding message with 'tool_calls'.

Le fournisseur refuse la requête **entière** : le fil est bloqué, chaque
nouveau message échoue. La cause est un ``ToolMessage`` sans message assistant
porteur du ``tool_calls`` (run interrompu, message client fusionné sans son
prédécesseur), ou un ``tool_calls`` resté sans réponse.

Ces tests verrouillent :

- la fonction pure ``assainir_sequence_outils`` (orphelins, réponses
  manquantes, doublons, réponses éloignées de leur appel) ;
- l'invariant de grammaire, vérifié sur un historique volontairement désordonné ;
- le middleware : il réécrit la requête **seulement** quand c'est nécessaire ;
- le graphe réel : un fil amorcé avec un orphelin va au bout, et le modèle ne
  reçoit aucun message ``tool`` orphelin.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from pydantic import BaseModel

from app.agents.base import AgentDefinition
from app.agents.harness import AgentRuntimeConfig, create_carso_deep_agent
from app.agents.historique_outils import (
    REPONSE_ABSENTE,
    HistoriqueOutilsCoherentMiddleware,
    assainir_sequence_outils,
    completer_identifiants,
    completer_identifiants_sequence,
)
from app.tools.base import TypedTool
from app.tools.permissions import PermissionPolicy
from tests.agent_fixtures import ScriptedChatModel, final_message


def _assistant(*appels: tuple[str, str]) -> AIMessage:
    """Message assistant demandant des outils : paires (id, nom)."""
    return AIMessage(
        content="",
        tool_calls=[{"id": ident, "name": nom, "args": {}} for ident, nom in appels],
    )


def _tool(contenu: str, ident: str) -> ToolMessage:
    return ToolMessage(content=contenu, tool_call_id=ident)


def _verifier_grammaire(messages: list[Any]) -> None:
    """Invariant exigé par les fournisseurs OpenAI-compatibles."""
    index = 0
    while index < len(messages):
        message = messages[index]
        assert not isinstance(message, ToolMessage), (
            f"message tool orphelin en position {index} : {message.tool_call_id!r}"
        )
        if isinstance(message, AIMessage) and (
            message.tool_calls or message.invalid_tool_calls
        ):
            attendus = [
                appel["id"]
                for appel in (*message.tool_calls, *message.invalid_tool_calls)
            ]
            suivis: list[str] = []
            suivant = index + 1
            while suivant < len(messages) and isinstance(messages[suivant], ToolMessage):
                suivis.append(messages[suivant].tool_call_id)
                suivant += 1
            assert sorted(suivis) == sorted(attendus), (
                f"réponses {suivis} incohérentes avec les appels {attendus}"
            )
            index = suivant
            continue
        index += 1


# --- 1. La fonction pure ------------------------------------------------------------


def test_un_orphelin_en_tete_est_ecarte() -> None:
    """Sans message assistant porteur, le fournisseur rejette tout le run."""
    messages = [_tool("vieux résultat", "orphelin"), HumanMessage("bonjour")]

    assert [message.type for message in assainir_sequence_outils(messages)] == ["human"]


def test_un_tool_apres_un_message_utilisateur_est_ecarte() -> None:
    messages = [HumanMessage("bonjour"), _tool("sans appel", "orphelin")]

    assert assainir_sequence_outils(messages) == [messages[0]]


def test_un_appel_sans_reponse_recoit_une_reponse_synthetique() -> None:
    """Le trou est comblé par une réponse qui dit la vérité, jamais par un faux résultat."""
    assistant = _assistant(("a", "rechercher"))
    messages = [HumanMessage("go"), assistant]

    resultat = assainir_sequence_outils(messages)

    assert resultat[:2] == [messages[0], assistant]
    synthetique = resultat[2]
    assert isinstance(synthetique, ToolMessage)
    assert synthetique.tool_call_id == "a"
    assert synthetique.name == "rechercher"
    assert synthetique.status == "error"
    assert synthetique.content == REPONSE_ABSENTE


def test_deux_appels_une_seule_reponse() -> None:
    """L'ordre est celui des appels déclarés, pas celui des réponses reçues."""
    assistant = _assistant(("a", "un"), ("b", "deux"))
    messages = [HumanMessage("go"), assistant, _tool("réponse b", "b")]

    resultat = assainir_sequence_outils(messages)

    _verifier_grammaire(resultat)
    assert [m.tool_call_id for m in resultat if isinstance(m, ToolMessage)] == ["a", "b"]
    assert isinstance(resultat[2], ToolMessage) and resultat[2].content == REPONSE_ABSENTE
    assert isinstance(resultat[3], ToolMessage) and resultat[3].content == "réponse b"


def test_une_reponse_eloignee_est_replacée_avec_son_contenu() -> None:
    """Une réponse séparée de son appel est déplacée, pas remplacée par du vide."""
    assistant = _assistant(("a", "rechercher"))
    messages = [
        HumanMessage("go"),
        assistant,
        HumanMessage("intercalé"),
        _tool("vrai résultat", "a"),
    ]

    resultat = assainir_sequence_outils(messages)

    _verifier_grammaire(resultat)
    assert resultat[2].content == "vrai résultat"
    assert resultat[2].tool_call_id == "a"
    assert resultat[3].content == "intercalé"


def test_un_doublon_de_reponse_est_reduit_a_un() -> None:
    assistant = _assistant(("a", "rechercher"))
    messages = [
        HumanMessage("go"),
        assistant,
        _tool("première", "a"),
        _tool("seconde", "a"),
    ]

    resultat = assainir_sequence_outils(messages)

    _verifier_grammaire(resultat)
    assert [m.content for m in resultat if isinstance(m, ToolMessage)] == ["première"]


def test_un_historique_sain_revient_inchange() -> None:
    """Aucune réécriture inutile : les objets sont les mêmes."""
    assistant = _assistant(("a", "rechercher"))
    messages = [
        HumanMessage("go"),
        assistant,
        _tool("résultat", "a"),
        AIMessage(content="terminé"),
    ]

    resultat = assainir_sequence_outils(messages)

    assert len(resultat) == len(messages)
    assert all(a is b for a, b in zip(resultat, messages, strict=True))


def test_invariant_sur_un_historique_desordonne() -> None:
    """Mélange de tous les défauts constatés : la grammaire tient à la sortie."""
    messages = [
        _tool("orphelin initial", "x"),
        HumanMessage("prépare la mission"),
        _assistant(("a", "un"), ("b", "deux")),
        _tool("réponse b", "b"),
        HumanMessage("continue"),
        _tool("réponse a arrivée trop tard", "a"),
        _tool("orphelin final", "y"),
        _assistant(("c", "trois")),
        AIMessage(content="terminé"),
    ]

    resultat = assainir_sequence_outils(messages)

    _verifier_grammaire(resultat)
    contenus = [m.content for m in resultat if isinstance(m, ToolMessage)]
    assert "réponse a arrivée trop tard" in contenus
    assert "réponse b" in contenus
    assert REPONSE_ABSENTE in contenus  # l'appel « c » n'a jamais eu de réponse
    assert "orphelin final" not in contenus
    assert "orphelin initial" not in contenus


# --- 2. Le middleware ---------------------------------------------------------------


class _Requete:
    """Requête de modèle minimale : messages + override, comme ``ModelRequest``."""

    def __init__(self, messages: list[Any], journal: list[dict[str, Any]] | None = None):
        self.messages = messages
        self._journal = journal if journal is not None else []

    def override(self, **champs: Any) -> _Requete:
        self._journal.append(champs)
        return _Requete(champs.get("messages", self.messages), self._journal)


def test_le_middleware_reecrit_une_requete_incoherente() -> None:
    journal: list[dict[str, Any]] = []
    requete = _Requete([HumanMessage("go"), _tool("orphelin", "z")], journal)
    middleware = HistoriqueOutilsCoherentMiddleware()
    vus: dict[str, Any] = {}

    def handler(recue: Any) -> str:
        vus["messages"] = recue.messages
        return "réponse"

    assert middleware.wrap_model_call(requete, handler) == "réponse"
    assert journal, "la requête doit être réécrite"
    _verifier_grammaire(vus["messages"])
    assert [m.type for m in vus["messages"]] == ["human"]


def test_le_middleware_ne_touche_pas_une_requete_saine() -> None:
    journal: list[dict[str, Any]] = []
    messages = [HumanMessage("go"), AIMessage(content="bonjour")]
    requete = _Requete(messages, journal)
    middleware = HistoriqueOutilsCoherentMiddleware()

    recue = middleware.wrap_model_call(requete, lambda r: r.messages)

    assert recue == messages
    assert journal == [], "aucun override pour un historique sain"


def test_le_middleware_est_asynchrone_aussi() -> None:
    """Le chemin AG-UI est asynchrone : les deux variantes doivent exister."""
    journal: list[dict[str, Any]] = []
    requete = _Requete([HumanMessage("go"), _tool("orphelin", "z")], journal)
    middleware = HistoriqueOutilsCoherentMiddleware()

    async def handler(recue: Any) -> list[Any]:
        return recue.messages

    recue = asyncio.run(middleware.awrap_model_call(requete, handler))

    _verifier_grammaire(recue)


# --- 3. Le graphe réel --------------------------------------------------------------


def _contrat_minimal() -> AgentDefinition:
    return AgentDefinition(
        agent_id="agent_historique_test",
        display_name="Agent historique de test",
        description="Agent de test sans tools métier",
        prompt="Tu es un agent de test.",
        tools=(),
        approval_required=(),
        collaboration=(),
        policy=PermissionPolicy(allowed_capabilities=frozenset()),
        skills=(),
    )


def test_graphe_avec_un_tool_orphelin_va_au_bout() -> None:
    """Le fil bloqué redevient utilisable : le modèle ne voit pas l'orphelin."""
    modele = ScriptedChatModel(responses=[final_message("Bonjour.")])
    graphe = create_carso_deep_agent(
        definition=_contrat_minimal(),
        tools=(),
        runtime=AgentRuntimeConfig(model=modele),
    )

    etat = graphe.invoke(
        {
            "messages": [
                HumanMessage("bonjour"),
                ToolMessage(content="résultat d'un tour interrompu", tool_call_id="orphelin"),
            ]
        },
        config={"configurable": {"thread_id": "historique-orphelin"}},
    )

    assert etat["messages"][-1].content == "Bonjour."
    assert modele.received, "le modèle a bien été appelé"
    for messages in modele.received:
        _verifier_grammaire(messages)
        assert all(
            not (isinstance(m, ToolMessage) and m.tool_call_id == "orphelin")
            for m in messages
        )


# --- ids d'appels absents (constat du 06/11) --------------------------------


def _assistant_sans_id(*noms: str) -> AIMessage:
    """Message assistant dont les appels n'ont **aucun** identifiant.

    ``AIMessage`` accepte ``id: None`` ; c'est ``ToolMessage`` qui refuse, et
    le run entier échoue alors au premier outil exécuté.
    """
    return AIMessage(
        content="",
        tool_calls=[{"name": nom, "args": {}, "id": None} for nom in noms],
    )


def test_un_appel_sans_id_recoit_un_id_non_vide() -> None:
    message = _assistant_sans_id("lire_donnees")
    repare, ajoutes = completer_identifiants(message)

    assert ajoutes == 1
    assert repare is not message
    assert repare.id == message.id
    ident = repare.tool_calls[0]["id"]
    assert isinstance(ident, str) and ident
    # Le reste de l'appel est intact : on ne réécrit que l'id.
    assert repare.tool_calls[0]["name"] == "lire_donnees"
    assert repare.tool_calls[0]["args"] == {}


def test_un_id_declare_n_est_jamais_reecrit() -> None:
    message = _assistant(("call_du_fournisseur", "lire_donnees"))
    repare, ajoutes = completer_identifiants(message)

    assert ajoutes == 0
    assert repare is message


def test_deux_appels_sans_id_recoivent_des_ids_distincts() -> None:
    repare, ajoutes = completer_identifiants(_assistant_sans_id("a", "b"))

    assert ajoutes == 2
    premier, second = (appel["id"] for appel in repare.tool_calls)
    assert premier != second


def test_les_appels_invalides_sont_completes_aussi() -> None:
    """Le fournisseur exige une réponse **aussi** pour un appel mal formé."""
    message = AIMessage(
        content="",
        invalid_tool_calls=[{"name": "x", "args": "{", "id": None, "error": None}],
    )
    repare, ajoutes = completer_identifiants(message)

    assert ajoutes == 1
    assert repare.invalid_tool_calls[0]["id"]


def test_un_historique_sans_id_reste_inchange_par_identite() -> None:
    sain = [_assistant(("call_1", "lire_donnees")), _tool("ok", "call_1")]
    complets, ajoutes = completer_identifiants_sequence(sain)

    assert ajoutes == 0
    assert all(a is b for a, b in zip(sain, complets, strict=True))


def test_l_assainissement_complete_l_id_avant_de_paire_la_reponse() -> None:
    """La réponse synthétique porte l'id complété, jamais ``None``."""
    message = _assistant_sans_id("lire_donnees")
    propre = assainir_sequence_outils([message])

    assert len(propre) == 2
    appel = propre[0].tool_calls[0]["id"]
    reponse = propre[1]
    assert isinstance(reponse, ToolMessage)
    assert reponse.tool_call_id == appel
    assert reponse.tool_call_id  # non vide : ToolMessage l'exige
    assert reponse.content == REPONSE_ABSENTE


def test_le_middleware_rend_la_requete_exploitable() -> None:
    middleware = HistoriqueOutilsCoherentMiddleware()
    vu: dict[str, Any] = {}

    def handler(request: Any) -> AIMessage:
        vu["messages"] = list(request.messages)
        return final_message("fini")

    middleware.wrap_model_call(_Requete([_assistant_sans_id("lire_donnees")]), handler)

    appels = vu["messages"][0].tool_calls
    assert appels[0]["id"]
    assert isinstance(vu["messages"][1], ToolMessage)
    assert vu["messages"][1].tool_call_id == appels[0]["id"]


def test_le_middleware_complete_la_reponse_du_modele() -> None:
    """Sans cela, ToolNode lève ``tool_call_id … input_value=None``."""
    middleware = HistoriqueOutilsCoherentMiddleware()

    sortie = middleware.wrap_model_call(
        _Requete([HumanMessage(content="salut")]),
        lambda _request: _assistant_sans_id("lire_donnees"),
    )

    assert sortie.tool_calls[0]["id"]
    ToolMessage(content="ok", tool_call_id=sortie.tool_calls[0]["id"])  # ne lève pas


def test_l_etat_du_fil_est_complete_au_debut_du_run() -> None:
    middleware = HistoriqueOutilsCoherentMiddleware()
    message = _assistant_sans_id("lire_donnees")

    mise_a_jour = middleware.before_agent({"messages": [message]}, None)

    assert mise_a_jour is not None
    assert mise_a_jour["messages"][0].tool_calls[0]["id"]
    assert middleware.before_agent({"messages": [final_message("ok")]}, None) is None


class _EntreeEcho(BaseModel):
    """Entrée minimale du tool de test."""

    texte: str = "a"


ECHO = TypedTool(
    name="echo",
    description="Renvoie le texte reçu (outil de test).",
    input_schema=_EntreeEcho,
    handler=lambda payload: {"ok": True, "texte": payload["texte"]},
)


def _contrat_avec_echo() -> AgentDefinition:
    return replace(
        _contrat_minimal(),
        tools=("echo",),
        policy=PermissionPolicy(allowed_capabilities=frozenset({"document_read"})),
    )


def test_le_graphe_execute_un_outil_appele_sans_id() -> None:
    """Le cas réel : le fournisseur omet l'id, le run doit aboutir."""
    modele = ScriptedChatModel(
        responses=[_assistant_sans_id("echo"), final_message("terminé")]
    )
    graphe = create_carso_deep_agent(
        definition=_contrat_avec_echo(),
        tools=(ECHO,),
        runtime=AgentRuntimeConfig(model=modele),
    )

    etat = graphe.invoke(
        {"messages": [HumanMessage("salut")]},
        config={"configurable": {"thread_id": "fil-ids"}},
    )

    assert etat["messages"][-1].content == "terminé"
    # Le modèle a reçu une réponse d'outil rattachée à un id **valide**.
    outils = [m for m in modele.received[-1] if isinstance(m, ToolMessage)]
    assert outils
    assert all(isinstance(m.tool_call_id, str) and m.tool_call_id for m in outils)
