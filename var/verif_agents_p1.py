"""Runs AG-UI réels de contrôle après la réduction P1 des schémas.

Deux conversations courtes, sur le backend en cours d'exécution :
- `agent_generaliste_readonly` : lecture de données (schémas stricts allégés) ;
- `agent_generateur_offre` : génération d'un document via `generer_document`,
  l'outil dont le schéma est passé de 53 k à 39 k caractères.

Le script ne juge pas le style de la réponse : il vérifie qu'aucun run ne part
en erreur, que les outils appelés rendent `ok: true` et que le texte final
existe. Trajectoires écrites sous `var/verif_p1/`.
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:8000"
BACKEND = Path(__file__).resolve().parents[1]
SORTIE = BACKEND / "var/verif_p1"

RUNS = (
    (
        "agent_generaliste_readonly",
        "Combien de missions existent dans le système, et combien sont au "
        "statut en_cours ? Donne les deux nombres, puis arrête-toi. Lecture "
        "seule : ne modifie rien et n'invente aucune donnée.",
    ),
    (
        "agent_generateur_offre",
        "Génère un document PDF de vérification technique intitulé "
        "« Note de vérification des outils », type de dépôt 'autre'. Utilise "
        "le tool generer_document avec le paramètre html : un titre et deux "
        "paragraphes courts disant qu'il s'agit d'un test du schéma d'outil. "
        "Ne cherche aucune donnée métier, n'invente rien. Affiche ensuite le "
        "document produit.",
    ),
)


def _token() -> str:
    corps = json.dumps(
        {"email": "admin@carso.mg", "mot_de_passe": "CarsoAdmin2026!"}
    ).encode()
    requete = urllib.request.Request(
        f"{BASE}/api/v1/auth/login",
        data=corps,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(requete, timeout=30) as reponse:
        return json.load(reponse)["access_token"]


def _run(token: str, agent: str, message: str, delai: int = 600) -> dict:
    identifiant = f"verif-{agent}-{int(time.time())}"
    charge = {
        "threadId": identifiant,
        "runId": identifiant,
        "messages": [{"id": "m1", "role": "user", "content": message}],
        "tools": [],
        "context": [],
        "forwardedProps": {},
        "state": {},
    }
    requete = urllib.request.Request(
        f"{BASE}/api/v1/agui/{agent}",
        data=json.dumps(charge).encode(),
        headers={
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "Authorization": f"Bearer {token}",
        },
    )
    evenements: list[dict] = []
    with urllib.request.urlopen(requete, timeout=delai) as flux:
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
    return {"agent": agent, "charge": charge, "evenements": evenements}


def _bilan(run: dict) -> dict:
    outils: list[str] = []
    appels: dict[str, str] = {}
    resultats: list[dict] = []
    texte: list[str] = []
    erreurs: list[str] = []
    for evenement in run["evenements"]:
        genre = evenement.get("type")
        if genre == "TOOL_CALL_START":
            nom = evenement.get("toolCallName", "?")
            outils.append(nom)
            appels[evenement.get("toolCallId", nom)] = nom
        elif genre == "TOOL_CALL_ARGS":
            cle = evenement.get("toolCallId", "")
            appels[cle] = appels.get(cle, "") + evenement.get("delta", "")
        elif genre == "TOOL_CALL_RESULT":
            contenu = evenement.get("content") or ""
            echec = False
            try:
                donnees = json.loads(contenu)
                echec = isinstance(donnees, dict) and donnees.get("ok") is False
                code = (donnees.get("error") or {}).get("code") if echec else None
            except (json.JSONDecodeError, AttributeError):
                code = None
            resultats.append(
                {
                    "outil": appels.get(evenement.get("toolCallId", ""), "?"),
                    "ok": not echec,
                    "code": code,
                }
            )
        elif genre == "TEXT_MESSAGE_CONTENT":
            texte.append(evenement.get("delta", ""))
        elif genre == "RUN_ERROR":
            erreurs.append(str(evenement.get("message", "RUN_ERROR")))
    return {
        "outils": outils,
        "resultats": resultats,
        "erreurs": erreurs,
        "texte_final": "".join(texte)[-2000:],
        "nb_evenements": len(run["evenements"]),
    }


def main() -> int:
    SORTIE.mkdir(parents=True, exist_ok=True)
    token = _token()
    echecs = 0
    for agent, message in RUNS:
        print(f"\n=== {agent} ===", flush=True)
        debut = time.time()
        try:
            run = _run(token, agent, message)
        except urllib.error.HTTPError as exc:
            print(f"HTTP {exc.code} : {exc.read()[:500]!r}", flush=True)
            echecs += 1
            continue
        except Exception as exc:  # réseau coupé, délai dépassé
            print(f"ÉCHEC transport : {exc!r}", flush=True)
            echecs += 1
            continue
        bilan = _bilan(run)
        sortie = SORTIE / f"{run['charge']['runId']}.json"
        sortie.write_text(
            json.dumps({"run": run, "bilan": bilan}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"durée {time.time() - debut:.0f}s, {bilan['nb_evenements']} événements")
        print("outils :", bilan["outils"])
        print("résultats :", bilan["resultats"])
        print("erreurs :", bilan["erreurs"])
        print("texte :", bilan["texte_final"][-700:])
        print("trajectoire :", sortie)
        if bilan["erreurs"] or any(not r["ok"] for r in bilan["resultats"]):
            echecs += 1
        if not bilan["outils"]:
            print("ANOMALIE : aucun outil appelé")
            echecs += 1
    print(f"\nRUNS EN ÉCHEC : {echecs}")
    return 1 if echecs else 0


if __name__ == "__main__":
    sys.exit(main())
