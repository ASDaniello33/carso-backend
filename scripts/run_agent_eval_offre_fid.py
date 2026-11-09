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
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
BASE = "http://127.0.0.1:8000"
AGENT = "agent_generateur_offre"

#: Dossier FID réel (lecture seule pour ce run).
APPEL = "ba227e3f-0df4-4546-9ba6-fcf802c0e036"
LOT = "df5c5116-4407-4c20-b006-846d671a2272"
ORGANISATION = "13382ac8-d3ef-4cb1-a1ed-9d6c5da306d0"

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


def _run(token: str, message: str) -> tuple[list[dict], dict]:
    identifiant = f"run-{int(time.time())}"
    charge = {
        "threadId": f"eval-fid-{identifiant}",
        "runId": identifiant,
        "messages": [{"id": "m1", "role": "user", "content": message}],
        "tools": [],
        "context": [],
        "forwardedProps": {},
        "state": {},
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


def _resume(evenements: list[dict]) -> dict:
    """Ce que la trajectoire prouve : outils appelés, texte final, erreurs."""
    outils: list[str] = []
    texte: list[str] = []
    erreurs: list[str] = []
    for evenement in evenements:
        genre = evenement.get("type")
        if genre == "TOOL_CALL_START":
            outils.append(evenement.get("toolCallName", "?"))
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
    print(f"run sur {AGENT} — consigne de {len(CONSIGNE)} caractères", flush=True)
    debut = time.time()
    try:
        evenements, charge = _run(token, CONSIGNE)
    except urllib.error.HTTPError as exc:
        print(f"HTTP {exc.code} : {exc.read()[:500]!r}")
        return 2
    duree = time.time() - debut

    bilan = _resume(evenements)
    dossier = BACKEND / "var/eval_runs" / charge["runId"]
    dossier.mkdir(parents=True, exist_ok=True)
    (dossier / "trajectoire.json").write_text(
        json.dumps(
            {"charge": charge, "evenements": evenements, "bilan": bilan},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"durée {duree:.0f}s — {bilan['nb_evenements']} événements")
    print("outils :", bilan["outils"])
    if bilan["erreurs"]:
        print("erreurs :", bilan["erreurs"])
    print("texte final :\n", bilan["texte_final"][-1200:])
    print("trajectoire :", dossier / "trajectoire.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
