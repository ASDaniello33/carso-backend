"""Appel d'agent **réel** via l'API en cours, pour obtenir une ligne `outil=…`.

**Question posée (15/11).** « Confirme que la mesure du temps des outils
fonctionne en production et pas seulement en test. » Un test unitaire appelle
`wrap_tool_call` directement : il ne prouve pas que l'exécuteur de LangGraph
l'invoque réellement dans un run servi par le backend.

**Ce que fait ce script.** Il interroge l'API en cours (`:8000`) comme le ferait
l'interface :

1. `POST /api/v1/auth/login` (session administrateur) ;
2. `POST /api/v1/agui/agent_generaliste_readonly` en SSE, avec un message qui
   **oblige** l'agent à appeler un outil de lecture (l'agent est lecture seule,
   donc l'outil est un `search_*` / `read_*` qui touche la base) ;
3. il lit le journal du backend et affiche les lignes `outil : …` produites
   **pendant** ce run, avec leur durée et le bilan `temps_outils=…`.

Aucun secret n'est affiché : le jeton d'authentification et la clé fournisseur
ne sortent pas de l'en-tête HTTP. Usage:

    python scripts/appel_agent_outil_reel.py            # question de lecture
    python scripts/appel_agent_outil_reel.py --message "…"
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.request
import uuid

sys.path.insert(0, ".")

#: **127.0.0.1 et non `localhost`.** `localhost` résout vers `::1` d'abord, et le
#: backend lié sur `0.0.0.0` n'écoute qu'en IPv4 : chaque appel paie ~1,0 s de
#: refus IPv6 avant de réessayer (constat du 15/11).
BASE = "http://127.0.0.1:8000"
AGENT = "agent_generaliste_readonly"
JOURNAL = "var/backend-8000.log"

#: Message qui oblige l'agent lecture seule à interroger la base : sans outil,
#: il n'a aucun moyen de répondre (constat déjà fait sur cet agent, 15/11).
MESSAGE_DEFAUT = (
    "Combien d'appels sont enregistrés dans la base ? Utilise un outil de "
    "recherche pour le compter, puis donne le nombre."
)

#: `outil : agent=…, outil=nom, duree=X s, …`
LIGNE_OUTIL = re.compile(r"outil : (.*)$")


def _appel(chemin: str, corps: dict | None, jeton: str | None = None):
    entetes = {"Content-Type": "application/json", "Accept": "text/event-stream"}
    if jeton:
        entetes["Authorization"] = f"Bearer {jeton}"
    donnees = json.dumps(corps).encode() if corps is not None else None
    return urllib.request.urlopen(
        urllib.request.Request(f"{BASE}{chemin}", data=donnees, headers=entetes),
        timeout=600,
    )


def _connexion() -> str:
    with _appel(
        "/api/v1/auth/login",
        {"email": "admin@carso.mg", "mot_de_passe": "CarsoAdmin2026!"},
    ) as reponse:
        return json.load(reponse)["access_token"]


def _taille_journal() -> int:
    import os

    return os.path.getsize(JOURNAL) if os.path.exists(JOURNAL) else 0


def _lignes_depuis(offset: int) -> list[str]:
    """Lignes écrites **après** le lancement du run, lues telles quelles."""
    with open(JOURNAL, encoding="utf-8", errors="replace") as flux:
        flux.seek(offset)
        return flux.read().splitlines()


def _derouler(jeton: str, message: str, run_id: str) -> dict[str, object]:
    """Lance le run réel et compte les événements jusqu'à la fin.

    Trois temps sont mesurés séparément, parce qu'ils ne disent pas la même
    chose : **avant le premier événement** (bootstrap côté serveur : montage du
    graphe, résolution des connecteurs, authentification), **entre le premier et
    le dernier événement** (le travail), et **le total** du flux. Un total très
    supérieur au run du backend désigne une attente hors du run.
    """
    from datetime import datetime
    charge = {
        "threadId": str(uuid.uuid4()),
        "runId": run_id,
        "messages": [{"id": "m0", "role": "user", "content": message}],
        "tools": [],
        "context": [],
        "forwardedProps": {},
        "state": {},
        "resume": [],
    }
    debut = time.monotonic()
    envoye = datetime.now()
    premier = None
    evenements = 0
    textes: list[str] = []
    erreur: str | None = None
    appels_vus: list[str] = []

    with _appel(f"/api/v1/agui/{AGENT}", charge, jeton) as flux:
        for brut in flux:
            ligne = brut.decode("utf-8", "replace").strip()
            if not ligne.startswith("data:"):
                continue
            contenu = ligne[5:].strip()
            if not contenu or contenu == "[DONE]":
                continue
            if premier is None:
                premier = time.monotonic() - debut
            evenements += 1
            evenement = json.loads(contenu)
            type_ = evenement.get("type")
            if type_ == "TEXT_MESSAGE_CONTENT":
                textes.append(evenement.get("delta", ""))
            elif type_ == "TOOL_CALL_START":
                appels_vus.append(evenement.get("toolCallName", "?"))
            elif type_ == "RUN_ERROR":
                erreur = evenement.get("message")

    total = time.monotonic() - debut
    return {
        "total": round(total, 2),
        "avant_premier_evenement": round(premier, 2) if premier is not None else None,
        "apres_dernier_evenement": round(total - (premier or 0), 2),
        "envoye": envoye.isoformat(timespec="seconds"),
        "evenements": evenements,
        "appels_outils": appels_vus,
        "texte": "".join(textes)[:400],
        "erreur": erreur,
    }


def principal(argv: list[str]) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    analyseur = argparse.ArgumentParser(description=__doc__)
    analyseur.add_argument("--message", default=MESSAGE_DEFAUT)
    options = analyseur.parse_args(argv)

    run_id = f"outil-reel-{uuid.uuid4().hex[:8]}"
    offset = _taille_journal()

    print(f"1. Connexion à {BASE} …")
    try:
        jeton = _connexion()
    except Exception as erreur:  # noqa: BLE001
        print(f"   ECHEC : {type(erreur).__name__} — {str(erreur)[:200]}")
        return 2
    print("   session administrateur obtenue")

    print(f"2. Run réel sur /agui/{AGENT} (run={run_id}) …")
    try:
        resultat = _derouler(jeton, options.message, run_id)
    except Exception as erreur:  # noqa: BLE001
        print(f"   ECHEC : {type(erreur).__name__} — {str(erreur)[:200]}")
        return 2

    print(
        f"   durée {resultat['total']} s · {resultat['evenements']} événements SSE "
        f"(envoyé à {resultat['envoye']})"
    )
    print(
        f"   avant le 1er événement : {resultat['avant_premier_evenement']} s · "
        f"premier → dernier : {resultat['apres_dernier_evenement']} s"
    )
    print(f"   appels d'outils annoncés par le flux : {resultat['appels_outils']}")
    if resultat["erreur"]:
        print(f"   RUN_ERROR : {resultat['erreur']}")
    if resultat["texte"]:
        print(f"   réponse : {resultat['texte'][:200]}")

    # Les lignes de journal ne portent pas le `runId` de l'API : on prend celles
    # écrites pendant le run. Le filtre par agent évite le bruit des autres
    # agents (l'interface peut interroger le catalogue en parallèle).
    print()
    print("3. Lignes de production écrites pendant ce run :")
    lignes = [
        ligne
        for ligne in _lignes_depuis(offset)
        if "mesure_modele" in ligne or "résumé historique" in ligne
    ]
    outils = [ligne for ligne in lignes if "outil :" in ligne or "outil lent" in ligne]
    bilans = [ligne for ligne in lignes if "run terminé" in ligne or "run interrompu" in ligne]
    etapes = [ligne for ligne in lignes if "étape model" in ligne]

    for ligne in etapes:
        print(f"   {ligne.split('| ')[-1]}")
    for ligne in outils:
        print(f"   {ligne.split('| ')[-1]}")
    for ligne in bilans:
        print(f"   {ligne.split('| ')[-1]}")

    print()
    if not outils:
        print(
            "   AUCUNE ligne `outil=…` : soit l'agent n'a appelé aucun outil sur ce\n"
            "   message, soit la mesure ne s'exécute pas en production. Relancer avec\n"
            "   un message qui exige explicitement un outil."
        )
        return 1
    print(f"   OK : {len(outils)} ligne(s) `outil=…` écrite(s) par le backend en cours.")
    if bilans:
        bilan = bilans[-1]
        if "temps_outils" in bilan and "outils=" in bilan:
            print("   OK : le bilan du run porte `outils=` et `temps_outils=`.")
        else:
            print("   PROBLEME : bilan sans compteur d'outils.")
            return 1
    else:
        print("   (pas de bilan : le run n'a pas encore terminé)")
    return 0


if __name__ == "__main__":  # pragma: no cover - outil en ligne de commande
    raise SystemExit(principal(sys.argv[1:]))
