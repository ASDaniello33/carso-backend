"""Reproduction — un run qui échoue laisse-t-il sa demande dans le fil ?

**Constat du 16/11/2026 (navigateur + journal `var/backend-8000.log`).** Sur le
fil `b61f7ae7…`, deux runs `agent_rh` ont échoué en 503 fournisseur. Le run
suivant a appelé le modèle avec **1 seul message** alors que le run échoué en
avait 12. Question posée : la demande du tour échoué reste-t-elle dans le
checkpoint, ou est-elle perdue ?

**Ce que fait cette sonde.** Elle rejoue exactement la chaîne de production :

- graphe ``create_carso_deep_agent`` (pile complète de middlewares) ;
- adaptateur ``ag_ui_langgraph.LangGraphAgent`` monté sur FastAPI ;
- checkpointer fourni (``MemorySaver`` par défaut, ``PostgresSaver`` avec
  ``--postgres``) ;
- modèle scripté qui **lève une erreur 503** au premier appel.

Trois scénarios, pour séparer la cause :

- ``complet`` — le client renvoie tout l'historique (comportement du test
  ``test_page_courante_deux_tours``) ;
- ``delta``   — le client n'envoie que le message neuf (comportement observé
  en production : ``__start__.messages`` ne contenait que les nouveaux) ;
- ``temoin``  — aucune panne : même envoi en delta, pour distinguer une perte
  propre à l'échec d'une perte structurelle ;
- ``echec-apres-outil`` — la panne survient **après** une boucle d'outils, comme
  dans le journal du 16/11 (12 messages en main au moment du 503).

Usage ::

    cd backend
    PYTHONPATH="$(pwd)" ./.venv/Scripts/python.exe scripts/repro_histoire_apres_503.py
    PYTHONPATH="$(pwd)" ./.venv/Scripts/python.exe scripts/repro_histoire_apres_503.py --postgres

La sonde **ne modifie aucun état applicatif** : fils neufs, checkpointer en
mémoire ou fil PostgreSQL dédié (identifiant aléatoire).
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from typing import Any

from ag_ui_langgraph import LangGraphAgent, add_langgraph_fastapi_endpoint
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langchain_core.messages import BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import tool
from langgraph.checkpoint.memory import MemorySaver
from pydantic import BaseModel

from app.agents.generaliste_readonly import DEFINITION
from app.agents.harness import AgentRuntimeConfig, create_carso_deep_agent
from app.api.agui import StreamingRunError, _code_erreur
from app.api.agui_interrupt_orphelin import MOTIF_REFUS, PurgeInterruptOrphelin
from app.tools.base import TypedTool
from tests.agent_fixtures import ScriptedChatModel, final_message

PREMIER = "Analyse le lot 95 et donne-moi la liste des missions."
SECOND = "Continuer"


class ArgsProposition(BaseModel):
    """Schéma permissif : l'outil HITL n'est jamais exécuté par la sonde."""

    mission_id: str = ""
    equipe_id: str = ""


#: Outil portant un nom **réellement** sous approbation dans le contrat d'agent_rh
#: (``approval_required``) : la politique d'interruption le reconnaît, l'outil
#: n'est jamais exécuté (l'interruption survient avant).
OUTIL_PROPOSITION = TypedTool(
    name="create_assignment_proposal",
    description="Crée une proposition d'affectation (sous approbation humaine).",
    handler=lambda payload: {"ok": True, "affectation_id": "sonde"},
    input_schema=ArgsProposition,
)


#: Compteur d'exécutions de l'outil : une reprise ne doit pas le rejouer.
APPELS_OUTIL: dict[str, int] = {"lectures": 0}


@tool
def lire_lot(lot_id: str) -> str:
    """Lit la fiche d'un lot (outil de sonde, sans effet de bord)."""
    APPELS_OUTIL["lectures"] += 1
    return f'{{"ok": true, "lot": "{lot_id}", "missions": 3}}'


class FournisseurIndisponible(RuntimeError):
    """Échec de fournisseur simulé — même forme qu'un 503 OpenAI-compatible.

    ``status_code`` est l'attribut que ``app/api/agui._statut_http`` lit pour
    classer l'erreur : la panne est donc traitée comme un vrai 5xx.
    """

    status_code = 503


class ModeleQuiEchoue(ScriptedChatModel):
    """Modèle scripté dont l'appel d'indice ``panne_a`` lève un 503."""

    panne_a: int | None = 0
    appels: int = 0

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        self.received.append(list(messages))
        index = self.appels
        self.appels += 1
        if self.panne_a is not None and index == self.panne_a:
            raise FournisseurIndisponible(
                "Error code: 503 - Internal server error (simulé)"
            )
        # Réponses consommées dans l'ordre, hors appel de la panne.
        rang = index - 1 if self.panne_a is not None and index > self.panne_a else index
        rang = min(max(rang, 0), len(self.responses) - 1)
        return ChatResult(generations=[ChatGeneration(message=self.responses[rang])])


def _client(
    modele: Any, checkpointer: Any, *, avec_outil: bool = False
) -> tuple[TestClient, Any]:
    runtime = AgentRuntimeConfig(
        model=modele,
        checkpointer=checkpointer,
        extra_tools=(lire_lot,) if avec_outil else (),
    )
    graphe = create_carso_deep_agent(
        definition=DEFINITION,
        tools=[],
        runtime=runtime,
    )
    # Même pont que la production (``app/api/agui._ajouter_endpoint``) : une
    # exception du fournisseur devient un RUN_ERROR, elle ne casse pas le flux.
    agent = StreamingRunError(
        LangGraphAgent(
            name="agent_generaliste_readonly", graph=graphe, description="sonde"
        )
    )
    application = FastAPI()
    add_langgraph_fastapi_endpoint(application, agent, path="/agui/sonde")
    return TestClient(application), graphe


def _client_rh(modele: Any, checkpointer: Any) -> tuple[TestClient, Any]:
    """Agent RH de production : outils HITL + purge d'interrupt orphelin."""
    from app.agents import agent_rh

    graphe = create_carso_deep_agent(
        definition=agent_rh.DEFINITION,
        tools=[OUTIL_PROPOSITION],
        runtime=AgentRuntimeConfig(
            model=modele, checkpointer=checkpointer, interactive=True
        ),
    )
    # Même ordre que ``app/api/agui._ajouter_endpoint`` : la purge est **dans**
    # le pont RUN_ERROR, donc une panne survenue pendant la reprise de
    # l'interrupt orphelin devient un RUN_ERROR visible (correctif 16/11).
    agent = StreamingRunError(
        PurgeInterruptOrphelin(
            LangGraphAgent(name="agent_rh", graph=graphe, description="sonde"),
            graphe,
            classer_erreur=_code_erreur,
        )
    )
    application = FastAPI()
    add_langgraph_fastapi_endpoint(application, agent, path="/agui/sonde")
    return TestClient(application), graphe


def scenario_hitl(
    nom: str, *, panne_a: int | None, checkpointer: Any
) -> dict[str, Any]:
    """Interruption HITL, purge de l'orphelin, puis échec (ou non) au modèle.

    Tour 1 : le modèle demande ``create_assignment_proposal`` → interruption.
    Tour 2 : message neuf **sans reprise** → la purge abandonne l'action en
    attente, puis le modèle est appelé (et échoue en 503 à l'appel ``panne_a``).

    **Attention à l'indice** : la purge reprend le graphe elle-même
    (``graph.ainvoke(Command(resume=...))``), ce qui consomme un appel modèle.
    Une panne posée trop tôt est donc avalée par la purge (best-effort) et
    n'atteint jamais le run de l'utilisateur — mesuré au premier essai.
    Tour 3 : message neuf → que voit le modèle de la demande du tour 2 ?
    """
    from tests.agent_fixtures import tool_call

    reponses = [
        tool_call("create_assignment_proposal", {"mission_id": "m", "equipe_id": "e"}),
        final_message("Proposition abandonnée, rien exécuté."),
        final_message("Réponse du tour 3"),
    ]
    modele = ModeleQuiEchoue(responses=reponses, panne_a=panne_a)
    client, graphe = _client_rh(modele, checkpointer)
    fil = f"sonde-{nom}-{uuid.uuid4().hex[:8]}"

    tour1 = _tour(client, fil, [{"id": "m0", "role": "user", "content": PREMIER}])
    etat_apres_interrupt = _etat(graphe, fil)

    tour2 = _tour(client, fil, [{"id": "m2", "role": "user", "content": SECOND}])
    etat_apres_echec = _etat(graphe, fil)

    recu_avant = len(modele.received)
    tour3 = _tour(
        client, fil, [{"id": "m3", "role": "user", "content": "Termine."}]
    )
    vus_par_le_modele = (
        modele.received[recu_avant] if len(modele.received) > recu_avant else []
    )

    def _present(fragment: str) -> bool:
        return any(
            fragment in (m.content if isinstance(m.content, str) else str(m.content))
            for m in vus_par_le_modele
        )

    rejet = any(
        isinstance(getattr(m, "content", None), str) and MOTIF_REFUS in m.content
        for m in etat_apres_echec
    )
    return {
        "scenario": nom,
        "tour1_erreur": tour1["erreur"],
        "etat_apres_interrupt": len(etat_apres_interrupt),
        "tour2_erreur": tour2["erreur"],
        "appels_modele": modele.appels,
        "purge_a_rejete_l_action": rejet,
        "etat_apres_echec": len(etat_apres_echec),
        "etat_apres_echec_detail": _resume(etat_apres_echec),
        "tour3_erreur": tour3["erreur"],
        "modele_voit_tour3": len(vus_par_le_modele),
        "modele_voit_tour3_detail": _resume(vus_par_le_modele),
        "demande_tour1_presente_dans_tour3": _present(PREMIER),
        "demande_du_tour_echoue_presente_dans_tour3": _present(SECOND),
    }


def _tour(client: TestClient, fil: str, messages: list[dict[str, Any]]) -> dict[str, Any]:
    charge = {
        "threadId": fil,
        "runId": f"run-{uuid.uuid4().hex[:8]}",
        "messages": messages,
        "tools": [],
        "context": [],
        "forwardedProps": {},
        "state": {},
        "resume": [],
    }
    reponse = client.post(
        "/agui/sonde", json=charge, headers={"Accept": "text/event-stream"}
    )
    evenements: list[dict[str, Any]] = []
    for ligne in reponse.text.splitlines():
        if not ligne.startswith("data:"):
            continue
        contenu = ligne[5:].strip()
        if not contenu or contenu == "[DONE]":
            continue
        evenements.append(json.loads(contenu))

    erreurs = [e for e in evenements if e.get("type") == "RUN_ERROR"]
    instantane: list[dict[str, Any]] = []
    for evenement in evenements:
        if evenement.get("type") == "MESSAGES_SNAPSHOT":
            instantane = list(evenement.get("messages") or [])
    return {
        "statut": reponse.status_code,
        "erreur": (erreurs[0].get("message") if erreurs else None),
        "code": (erreurs[0].get("code") if erreurs else None),
        "messages": instantane,
    }


def _etat(graphe: Any, fil: str) -> list[Any]:
    """Messages réellement présents dans le fil (source de vérité du run suivant)."""
    instantane = graphe.get_state({"configurable": {"thread_id": fil}})
    valeurs = getattr(instantane, "values", None) or {}
    messages = valeurs.get("messages")
    return list(messages) if isinstance(messages, list) else []


def _resume(messages: list[Any]) -> str:
    if not messages:
        return "aucun message"
    morceaux: list[str] = []
    for message in messages:
        contenu = message.content if isinstance(message.content, str) else str(message.content)
        morceaux.append(f"{getattr(message, 'type', '?')}:{contenu[:40]!r}")
    return " | ".join(morceaux)


def _historique(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Ce qu'un client qui renvoie tout l'historique transmet au tour suivant."""
    return [
        {"id": m.get("id"), "role": m.get("role"), "content": m.get("content")}
        for m in messages
        if m.get("role") in {"user", "assistant"}
    ]


def scenario(
    nom: str,
    *,
    client_mode: str,
    panne: bool,
    checkpointer: Any,
    avec_outil: bool = False,
) -> dict[str, Any]:
    """Un scénario complet : tour 1 (panne ou non), puis tour 2 de suivi."""
    from tests.agent_fixtures import tool_call

    reponses = (
        [tool_call("lire_lot", {"lot_id": "95"}), final_message("Réponse du tour 2")]
        if avec_outil
        else [final_message("Réponse du tour 1"), final_message("Réponse du tour 2")]
    )
    modele = ModeleQuiEchoue(
        responses=reponses,
        panne_a=(1 if avec_outil else 0) if panne else None,
    )
    client, graphe = _client(modele, checkpointer, avec_outil=avec_outil)
    fil = f"sonde-{nom}-{uuid.uuid4().hex[:8]}"

    envoye = [{"id": "m0", "role": "user", "content": PREMIER}]
    tour1 = _tour(client, fil, envoye)
    etat_apres_echec = _etat(graphe, fil)

    neuf = {"id": "m2", "role": "user", "content": SECOND}
    if client_mode == "complet":
        suivi = [*_historique(tour1["messages"] or envoye), neuf]
    else:
        suivi = [neuf]

    recu_avant = len(modele.received)
    tour2 = _tour(client, fil, suivi)
    vus_par_le_modele = modele.received[recu_avant] if len(modele.received) > recu_avant else []
    etat_final = _etat(graphe, fil)

    return {
        "scenario": nom,
        "panne": tour1["erreur"],
        "code": tour1["code"],
        "etat_apres_echec": len(etat_apres_echec),
        "etat_apres_echec_detail": _resume(etat_apres_echec),
        "tour2_erreur": tour2["erreur"],
        "modele_voit_tour2": len(vus_par_le_modele),
        "modele_voit_tour2_detail": _resume(vus_par_le_modele),
        "demande_tour1_presente_dans_tour2": any(
            PREMIER[:20] in (m.content if isinstance(m.content, str) else str(m.content))
            for m in vus_par_le_modele
        ),
        "etat_final": len(etat_final),
    }


def scenario_reprise(checkpointer: Any) -> dict[str, Any]:
    """Échec **au milieu** d'une exécution, puis reprise là où elle s'est arrêtée.

    Question posée : après une erreur, les messages produits sont-ils conservés
    et l'agent peut-il continuer exactement où il en était ?

    Tour 1 : le modèle demande un outil (exécuté), puis son appel suivant échoue
    en 503 — l'exécution s'arrête **après** le résultat d'outil. Puis l'appelant
    reprend sur le même fil (``invoke(None)``, sans nouveau message) : le graphe
    doit repartir de l'état checkpointé, avec le résultat d'outil déjà en main,
    et **sans rejouer l'outil**.
    """
    from tests.agent_fixtures import tool_call

    APPELS_OUTIL["lectures"] = 0
    modele = ModeleQuiEchoue(
        responses=[
            tool_call("lire_lot", {"lot_id": "95"}),
            final_message("Voici le bilan du lot 95."),
        ],
        panne_a=1,
    )
    client, graphe = _client(modele, checkpointer, avec_outil=True)
    fil = f"sonde-reprise-{uuid.uuid4().hex[:8]}"

    tour1 = _tour(client, fil, [{"id": "m0", "role": "user", "content": PREMIER}])
    etat_interrompu = _etat(graphe, fil)
    outils_apres_echec = APPELS_OUTIL["lectures"]

    # Reprise : même fil, aucun message neuf. Le modèle est « réparé ».
    modele.panne_a = None
    recu_avant = len(modele.received)
    graphe.invoke(None, {"configurable": {"thread_id": fil}})
    vus_a_la_reprise = modele.received[recu_avant] if len(modele.received) > recu_avant else []
    etat_final = _etat(graphe, fil)

    def _texte(message: Any) -> str:
        return message.content if isinstance(message.content, str) else str(message.content)

    return {
        "scenario": "reprise-apres-503",
        "panne": tour1["erreur"],
        "outils_executes_avant_echec": outils_apres_echec,
        "etat_apres_echec": len(etat_interrompu),
        "etat_apres_echec_detail": _resume(etat_interrompu),
        "resultat_outil_conserve": any(m.type == "tool" for m in etat_interrompu),
        "modele_voit_le_resultat_a_la_reprise": any(m.type == "tool" for m in vus_a_la_reprise),
        "modele_voit": len(vus_a_la_reprise),
        "etat_final": len(etat_final),
        "etat_final_detail": _resume(etat_final),
        "reprise_a_repondu": any(
            m.type == "ai" and _texte(m).strip() == "Voici le bilan du lot 95."
            for m in etat_final
        ),
        "outil_rejoue_par_la_reprise": APPELS_OUTIL["lectures"] != outils_apres_echec,
        "demande_conservee": any(PREMIER[:20] in _texte(m) for m in etat_final),
    }


def main() -> int:
    parseur = argparse.ArgumentParser(description=__doc__)
    parseur.add_argument(
        "--postgres",
        action="store_true",
        help="checkpointer PostgreSQL de production",
    )
    parseur.add_argument(
        "--agent-rh",
        action="store_true",
        help="agent_rh : outils HITL + purge d'interrupt orphelin",
    )
    options = parseur.parse_args()

    fabrique = MemorySaver
    if options.postgres:
        from app.agents.checkpointer import etat_checkpointer, get_checkpointer

        # La fabrique est paresseuse : l'état n'est renseigné qu'après le
        # premier appel, d'où la construction avant lecture.
        saver = get_checkpointer()
        etat = etat_checkpointer()
        if etat.get("mode") != "postgres" or saver is None:
            print(f"checkpointer indisponible : {etat}", file=sys.stderr)
            return 2
        fabrique = get_checkpointer

    resultats = []
    if options.agent_rh:
        # Indice 0 = outil demandé, 1 = reprise par la purge, 2 = run de
        # l'utilisateur. ``hitl-echec-purge`` vérifie le correctif : la panne
        # de la purge doit atteindre le client au lieu d'être avalée (le run
        # s'arrête donc avant de traiter le message — d'où l'exclusion du
        # verdict sur la conservation de la demande).
        for nom, panne_a, compte in (
            ("hitl-temoin", None, True),
            ("hitl-echec", 2, True),
            ("hitl-echec-purge", 1, False),
        ):
            resultat = scenario_hitl(nom, panne_a=panne_a, checkpointer=fabrique())
            resultat["compte_dans_le_verdict"] = compte
            resultats.append(resultat)
    else:
        resultats.append(scenario_reprise(fabrique()))
        for nom, client_mode, panne, avec_outil in (
            ("temoin-delta", "delta", False, False),
            ("echec-complet", "complet", True, False),
            ("echec-delta", "delta", True, False),
            ("echec-apres-outil", "delta", True, True),
        ):
            resultats.append(
                scenario(
                    nom,
                    client_mode=client_mode,
                    panne=panne,
                    checkpointer=fabrique(),
                    avec_outil=avec_outil,
                )
            )

    for resultat in resultats:
        print(f"\n=== {resultat['scenario']} ===")
        for cle, valeur in resultat.items():
            if cle == "scenario":
                continue
            print(f"  {cle}: {valeur}")

    if options.agent_rh:
        perdus = [
            r
            for r in resultats
            if r.get("compte_dans_le_verdict")
            and r["tour2_erreur"]
            and not r["demande_du_tour_echoue_presente_dans_tour3"]
        ]
    else:
        perdus = [
            r
            for r in resultats
            if "demande_tour1_presente_dans_tour2" in r
            and r["panne"]
            and not r["demande_tour1_presente_dans_tour2"]
        ]
        reprises = [r for r in resultats if r.get("scenario") == "reprise-apres-503"]
        if reprises:
            reprise = reprises[0]
            print(
                "\nREPRISE : "
                + (
                    "l'agent reprend là où il s'est arrêté (résultat d'outil "
                    "conservé, réponse produite, outil non rejoué)"
                    if reprise["resultat_outil_conserve"]
                    and reprise["reprise_a_repondu"]
                    and not reprise["outil_rejoue_par_la_reprise"]
                    else "ÉCHEC — voir le détail ci-dessus"
                )
            )
    print(
        "\nVERDICT : "
        + (
            f"{len(perdus)} scénario(s) en échec perdent la demande du tour échoué : "
            + ", ".join(r["scenario"] for r in perdus)
            if perdus
            else "aucun scénario ne perd la demande du tour échoué"
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
