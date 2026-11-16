"""Latence : backend ou frontend ? Mesure comparée sur un même run.

**Question posée (15/11).** « Vérifie bien que la latence est sur le backend ou
sur le frontend, ça peut arriver aussi, peut-être que la cause vient aussi du
contexte frontend. »

**Ce que ce script mesure.** Trois temps pour **un seul** run, afin de les
comparer sans ambiguïté :

1. **`POST → en-têtes`** : le délai avant que le serveur commence à répondre
   (montage du graphe, résolution des connecteurs). C'est le seul poste qui
   puisse être un « coût serveur » hors run.
2. **Serveur (`run terminé` du journal)** : la durée que le backend compte
   lui-même, du premier au dernier événement du graphe.
3. **Client (premier → dernier octet SSE)** : ce que le navigateur ou tout client
   AG-UI attend réellement.

Si (3) ≈ (2), le frontend n'ajoute rien : la latence est celle du run.
Si (3) ≫ (2), il faut chercher hors du run — attente réseau, proxy, ou rendu.
Si (2) ≈ (1), le coût est avant le run (démarrage à froid).

Le script envoie aussi un `context` AG-UI **volumineux** (`--gros-contexte`) pour
mesurer ce que coûte réellement le contexte frontend, plutôt que de le supposer.

Usage:
    python scripts/ou_est_la_latence.py                  # run normal
    python scripts/ou_est_la_latence.py --gros-contexte  # +200 000 car. de contexte
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

#: **127.0.0.1 et non `localhost`.** Constat du 15/11 : le backend est lié sur
#: `0.0.0.0` (IPv4 seul) alors que `localhost` résout vers `::1` **d'abord**. La
#: tentative IPv6 est refusée après ~1,0 s, puis le client essaie IPv4. Une sonde
#: en `localhost` mesure donc ~1,0 s d'attente qui n'appartient **pas** au
#: serveur : ses chiffres ne valent rien. C'est exactement l'erreur commise ici.
BASE = "http://127.0.0.1:8000"
AGENT = "agent_generaliste_readonly"
JOURNAL = "var/backend-8000.log"
MESSAGE = "Dis simplement bonjour, sans outil."

#: `run terminé : … duree=X s, etapes=N, …`
FIN_RUN = re.compile(r"run terminé : (.*)$")
CHAMP = re.compile(r"(\w+)=(.+?)(?=, \w+=|$)")


def _connexion() -> str:
    with _appel(
        "/api/v1/auth/login",
        {"email": "admin@carso.mg", "mot_de_passe": "CarsoAdmin2026!"},
        accept="application/json",
    ) as reponse:
        return json.load(reponse)["access_token"]


def _appel(chemin: str, corps: dict | None, jeton: str | None = None, *, accept: str | None = None):
    entetes = {
        "Content-Type": "application/json",
        "Accept": accept or "text/event-stream",
    }
    if jeton:
        entetes["Authorization"] = f"Bearer {jeton}"
    donnees = json.dumps(corps).encode() if corps is not None else None
    return urllib.request.urlopen(
        urllib.request.Request(f"{BASE}{chemin}", data=donnees, headers=entetes),
        timeout=600,
    )


def _taille() -> int:
    import os

    return os.path.getsize(JOURNAL) if os.path.exists(JOURNAL) else 0


def _bilan_depuis(offset: int) -> dict[str, float] | None:
    """Dernier `run terminé` écrit après le lancement, avec ses chiffres."""
    with open(JOURNAL, encoding="utf-8", errors="replace") as flux:
        flux.seek(offset)
        for ligne in reversed(flux.read().splitlines()):
            trouve = FIN_RUN.search(ligne)
            if not trouve:
                continue
            d = {cle: val.strip().rstrip(",") for cle, val in CHAMP.findall(trouve.group(1))}
            return {
                "duree": float(d.get("duree", "0").replace(" s", "")),
                "etapes": float(d.get("etapes", "0")),
                "outils": float(d.get("outils", "0")),
                "temps_outils": float(
                    d.get("temps_outils", "0").replace(" s", "")
                ),
            }
    return None


def _run(jeton: str, message: str, contexte: list | None) -> dict[str, object]:
    charge = {
        "threadId": str(uuid.uuid4()),
        "runId": f"latence-{uuid.uuid4().hex[:8]}",
        "messages": [{"id": "m0", "role": "user", "content": message}],
        "tools": [],
        "context": contexte or [],
        "forwardedProps": {},
        "state": {},
        "resume": [],
    }
    taille_charge = len(json.dumps(charge).encode())
    debut = time.monotonic()
    reponse = _appel(f"/api/v1/agui/{AGENT}", charge, jeton)
    entetes = time.monotonic() - debut
    premier = dernier = None
    evenements = 0
    total_evenements = 0
    erreur = None
    with reponse as flux:
        for brut in flux:
            if premier is None:
                premier = time.monotonic()
            dernier = time.monotonic()
            ligne = brut.decode("utf-8", "replace").strip()
            if not ligne.startswith("data:"):
                continue
            contenu = ligne[5:].strip()
            if not contenu or contenu == "[DONE]":
                continue
            evenements += 1
            total_evenements += len(brut)
            evenement = json.loads(contenu)
            if evenement.get("type") == "RUN_ERROR":
                erreur = evenement.get("message")

    fin = time.monotonic()
    return {
        "envoi": debut,
        "entetes": entetes,
        "serveur_avant_flux": (premier - debut) if premier else None,
        "flux": (dernier - premier) if premier and dernier else None,
        "total": fin - debut,
        "evenements": evenements,
        "kio_recus": round(total_evenements / 1024, 1),
        "kio_envoyes": round(taille_charge / 1024, 1),
        "erreur": erreur,
    }


def principal(argv: list[str]) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    analyseur = argparse.ArgumentParser(description=__doc__)
    analyseur.add_argument("--gros-contexte", action="store_true")
    analyseur.add_argument("--message", default=MESSAGE)
    options = analyseur.parse_args(argv)

    contexte = None
    if options.gros_contexte:
        # Contexte AG-UI volumineux, comme le ferait un client qui publie trop.
        contexte = [
            {
                "description": "Page ouverte dans l'interface CARSO",
                "value": "x" * 200_000,
            }
        ]

    jeton = _connexion()
    offset = _taille()
    resultat = _run(jeton, options.message, contexte)
    bilan = _bilan_depuis(offset)

    print(f"=== run {'avec gros contexte' if contexte else 'normal'} ===")
    print(f"requête envoyée   : {resultat['kio_envoyes']} Kio")
    print(f"POST → en-têtes   : {resultat['entetes']:.3f} s")
    print(f"en-têtes → 1er év : {resultat['serveur_avant_flux']:.3f} s")
    print(f"flux SSE (client) : {resultat['flux']:.3f} s")
    print(f"TOTAL client      : {resultat['total']:.3f} s")
    if bilan:
        print(f"SERVEUR (journal) : {bilan['duree']:.2f} s "
              f"({bilan['etapes']:.0f} étapes, {bilan['temps_outils']:.2f} s d'outils)")
        ecart = resultat["total"] - bilan["duree"]
        print(f"écart client−srv  : {ecart:+.2f} s")
        if abs(ecart) <= 1.0:
            print("-> le client n'ajoute rien : la latence est le run lui-même.")
        else:
            print("-> écart notable : chercher hors du run (réseau, proxy, rendu).")
    else:
        print("SERVEUR (journal) : introuvable (run non terminé ?)")
    print(f"reçus             : {resultat['kio_recus']} Kio, "
          f"{resultat['evenements']} événements")
    if resultat["erreur"]:
        print(f"RUN_ERROR         : {resultat['erreur']}")
    return 0


if __name__ == "__main__":  # pragma: no cover - outil en ligne de commande
    raise SystemExit(principal(sys.argv[1:]))
