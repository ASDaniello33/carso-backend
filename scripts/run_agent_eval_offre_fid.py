"""Pilote un run AG-UI réel de l'agent générateur sur le dossier FID.

But : obtenir un vrai run (harnais complet, modèle, outils, base) sur la tâche
d'éval `offre-technique-fid-modele`, et enregistrer la trajectoire pour audit.

Ce script n'écrit aucune donnée métier de lui-même : il envoie une consigne à
l'agent, qui agit par ses propres outils. La trajectoire est conservée hors du
dépôt, sous ``var/eval_runs/``.

Usage :
    ./.venv/Scripts/python.exe -m scripts.run_agent_eval_offre_fid
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
#: Instance interrogée : le serveur de développement tourne sur 8000, mais un
#: serveur jetable sur un autre port permet de comparer deux versions du prompt
#: sans arrêter celui de l'utilisateur.
BASE = os.environ.get("CARSO_BASE", "http://127.0.0.1:8000")
AGENT = "agent_generateur_offre"

#: Dossier réel du run (surchargeable : un lot neuf se valide sans toucher au
#: dossier d'origine).
APPEL = os.environ.get("CARSO_APPEL", "ba227e3f-0df4-4546-9ba6-fcf802c0e036")
LOT = os.environ.get("CARSO_LOT", "df5c5116-4407-4c20-b006-846d671a2272")
ORGANISATION = os.environ.get(
    "CARSO_ORGANISATION", "13382ac8-d3ef-4cb1-a1ed-9d6c5da306d0"
)

CONSIGNE = f"""Reprends l'offre technique du lot {LOT} de l'appel {APPEL}
(FID, AMI N°59/AMI/FID/FSR/SQC_CAB_FORM_IA).

Le FID impose son propre modèle DOCX. Vérifie d'abord le modèle de l'organisation
{ORGANISATION} au registre, puis produis l'offre technique DANS ce modèle :
copie le modèle, renseigne ses emplacements, et rédige le contenu à partir des
termes de référence de l'appel.

Le contenu doit couvrir, sans rien inventer :
- le public et les volumes : deux vagues, environ 24 participants pour la vague
  SI/PSE et environ 30 pour la vague Directeurs/Chefs de service/Responsables ;
- la durée : sept jours ouvrables au total, dont quatre (04) jours pour la
  vague 1 et trois (03) jours pour la vague 2 ;
- les six livrables attendus par les termes de référence, dont la note
  méthodologique et la note de bonnes pratiques ;
- le programme par vague et le format présentiel, pratique et participatif.

Ne régénère pas un document au thème CARSO : le document doit rester le modèle
du client, avec ses styles, sa couverture et son logo. Le document reste une
proposition, non officialisée.

À la fin, indique le nom du document produit et l'identifiant du modèle utilisé.
"""


def _auth() -> str:
    corps = json.dumps({"email": "admin@carso.mg", "mot_de_passe": "CarsoAdmin2026!"}).encode()
    requete = urllib.request.Request(
        f"{BASE}/api/v1/auth/login",
        data=corps,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(requete) as reponse:
        return json.load(reponse)["access_token"]


def _run(
    token: str,
    message: str | None,
    *,
    thread_id: str,
    run_id: str,
    resume: list[dict] | None = None,
) -> tuple[list[dict], dict]:
    charge = {
        "threadId": thread_id,
        "runId": run_id,
        "messages": (
            [{"id": "m1", "role": "user", "content": message}]
            if message is not None
            else []
        ),
        "tools": [],
        "context": [],
        "forwardedProps": {},
        "state": {},
        "resume": resume or [],
    }
    requete = urllib.request.Request(
        f"{BASE}/api/v1/agui/{AGENT}",
        data=json.dumps(charge).encode(),
        headers={
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "Authorization": f"Bearer {token}",
        },
    )
    evenements: list[dict] = []
    with urllib.request.urlopen(requete, timeout=2400) as flux:
        for brut in flux:
            ligne = brut.decode("utf-8", "replace").strip()
            if not ligne.startswith("data:"):
                continue
            contenu = ligne[5:].strip()
            if not contenu or contenu == "[DONE]":
                continue
            try:
                evenements.append(json.loads(contenu))
            except json.JSONDecodeError:
                evenements.append({"type": "brut", "data": contenu[:400]})
    return evenements, charge


def _saturation(evenements: list[dict]) -> bool:
    """Run refusé par la limite de concurrence du provider (429).

    Le provider de développement n'accepte **qu'une requête à la fois** : un run
    lancé pendant qu'un autre travaille reçoit un 429 immédiat, qui n'est pas un
    défaut de l'agent. Le pilote réessaie plutôt que d'enregistrer un run vide.
    """
    for evenement in evenements:
        if evenement.get("type") != "RUN_ERROR":
            continue
        message = str(evenement.get("message") or "")
        if "429" in message or "limite d'utilisation" in message:
            return True
    return False


def _run_avec_reprise(
    token: str,
    message: str | None,
    *,
    thread_id: str,
    run_id: str,
    resume: list[dict] | None = None,
    essais: int = 4,
) -> tuple[list[dict], dict]:
    """Lance un run, en réessayant sur saturation du provider."""
    lot: list[dict] = []
    charge: dict = {}
    for essai in range(essais):
        try:
            lot, charge = _run(
                token, message, thread_id=thread_id, run_id=f"{run_id}-e{essai}", resume=resume
            )
        except urllib.error.HTTPError as exc:
            if exc.code == 429 and essai + 1 < essais:
                attente = 20 * (essai + 1)
                print(f"provider saturé (HTTP 429), nouvelle tentative dans {attente}s", flush=True)
                time.sleep(attente)
                continue
            raise
        if not _saturation(lot):
            return lot, charge
        if essai + 1 < essais:
            attente = 20 * (essai + 1)
            print(f"provider saturé, nouvelle tentative dans {attente}s", flush=True)
            time.sleep(attente)
    return lot, charge


def _interrupts(evenements: list[dict]) -> list[dict]:
    """Interruptions en attente d'une décision humaine, à la fin d'un run."""
    for evenement in reversed(evenements):
        if evenement.get("type") == "RUN_FINISHED":
            outcome = evenement.get("outcome") or {}
            if outcome.get("type") == "interrupt":
                return list(outcome.get("interrupts") or [])
            return []
    return []


def _decisions(interrupt: dict) -> list[dict]:
    """Une décision `approve` par action demandée par l'interruption."""
    brut = interrupt.get("metadata") or {}
    langgraph = (brut.get("langgraph") or {}).get("raw") or {}
    actions = langgraph.get("action_requests") or [{"name": "?"}]
    return [{"type": "approve"} for _ in actions]


def _compacts(evenements: list[dict], limite: int = 1200) -> list[dict]:
    """Événements utiles seulement, contenus tronqués.

    Les trames RAW portent la charge complète de chaque étape (messages,
    schémas d'outils) : plusieurs centaines de mégaoctets sur un run entier, au
    point de remplir le disque. Ce qui sert à l'audit tient dans l'ordre des
    outils, leurs résultats et le texte produit.
    """
    gardes = {
        "TOOL_CALL_START",
        "TOOL_CALL_ARGS",
        "TOOL_CALL_RESULT",
        "TOOL_CALL_END",
        "TEXT_MESSAGE_CONTENT",
        "RUN_STARTED",
        "RUN_FINISHED",
        "RUN_ERROR",
        "STEP_STARTED",
        "STEP_FINISHED",
    }
    compacts: list[dict] = []
    for evenement in evenements:
        if evenement.get("type") not in gardes:
            continue
        allege = {}
        for cle, valeur in evenement.items():
            if isinstance(valeur, str) and len(valeur) > limite:
                allege[cle] = valeur[:limite] + "…"
            else:
                allege[cle] = valeur
        compacts.append(allege)
    return compacts


def _resume(evenements: list[dict]) -> dict:
    """Ce que la trajectoire prouve : outils appelés, texte final, erreurs."""
    outils: list[str] = []
    texte: list[str] = []
    erreurs: list[str] = []
    for evenement in evenements:
        genre = evenement.get("type")
        if genre == "TOOL_CALL_START":
            outils.append(evenement.get("toolCallName", "?"))
        elif genre == "TOOL_CALL_RESULT":
            contenu = str(evenement.get("content") or "")
            if '"ok": false' in contenu:
                erreurs.append(contenu[:300])
        elif genre == "TEXT_MESSAGE_CONTENT":
            texte.append(evenement.get("delta", ""))
        elif genre in {"RUN_ERROR", "RUN_FINISHED"} and evenement.get("message"):
            if genre == "RUN_ERROR":
                erreurs.append(evenement["message"])
    return {
        "nb_evenements": len(evenements),
        "outils": outils,
        "texte_final": "".join(texte)[-2500:],
        "erreurs": erreurs,
    }


def main() -> int:
    token = _auth()
    print(f"appel {APPEL} — lot {LOT}", flush=True)
    print(f"run sur {AGENT} — consigne de {len(CONSIGNE)} caractères", flush=True)
    debut = time.time()
    thread_id = f"eval-fid-{int(time.time())}"
    evenements: list[dict] = []
    rounds: list[dict] = []

    try:
        lot, charge = _run_avec_reprise(
            token, CONSIGNE, thread_id=thread_id, run_id=f"{thread_id}-0"
        )
    except urllib.error.HTTPError as exc:
        print(f"HTTP {exc.code} : {exc.read()[:500]!r}")
        return 2
    evenements.extend(lot)
    rounds.append({"run": charge["runId"], "outils": _resume(lot)["outils"]})

    # Un run réel s'arrête sur chaque écriture soumise à approbation. Le pilote
    # tient le rôle de l'utilisateur : il approuve, puis reprend le fil, jusqu'à
    # ce que l'agent conclue ou que la garde anti-boucle ne le stoppe.
    for tour in range(1, 13):
        interruptions = _interrupts(lot)
        if not interruptions:
            break
        resume = [
            {
                "interruptId": interruption["id"],
                "status": "resolved",
                "payload": {"decisions": _decisions(interruption)},
            }
            for interruption in interruptions
        ]
        noms = [
            action.get("name")
            for interruption in interruptions
            for action in (
                ((interruption.get("metadata") or {}).get("langgraph") or {})
                .get("raw", {})
                .get("action_requests")
                or []
            )
        ]
        print(f"reprise {tour} — approbation de {noms}", flush=True)
        try:
            lot, charge = _run_avec_reprise(
                token, None, thread_id=thread_id, run_id=f"{thread_id}-{tour}", resume=resume
            )
        except urllib.error.HTTPError as exc:
            print(f"HTTP {exc.code} sur reprise : {exc.read()[:500]!r}")
            break
        evenements.extend(lot)
        rounds.append({"run": charge["runId"], "outils": _resume(lot)["outils"]})

    duree = time.time() - debut

    bilan = _resume(evenements)
    bilan["rounds"] = rounds
    bilan["interruptions_restantes"] = [
        interruption.get("id") for interruption in _interrupts(lot)
    ]
    dossier = BACKEND / "var/eval_runs" / thread_id
    dossier.mkdir(parents=True, exist_ok=True)
    (dossier / "trajectoire.json").write_text(
        json.dumps(
            {
                "charge": charge,
                "thread": thread_id,
                "evenements": _compacts(evenements),
                "bilan": bilan,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"durée {duree:.0f}s — {bilan['nb_evenements']} événements")
    print("outils :", bilan["outils"])
    print("tours :", [(r["run"], len(r["outils"])) for r in rounds])
    if bilan["interruptions_restantes"]:
        print("interruptions restantes :", bilan["interruptions_restantes"])
    if bilan["erreurs"]:
        print("erreurs :", bilan["erreurs"])
    print("texte final :\n", bilan["texte_final"][-1200:])
    print("trajectoire :", dossier / "trajectoire.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
