"""Latence d'une conversation réelle : le client grossit-il la requête ?

**Question posée (15/11).** « Vérifie bien que la latence est sur le backend ou
sur le frontend, ça peut arriver aussi, peut-être que la cause vient aussi du
contexte frontend. »

**Ce que ce script mesure.** Une conversation de plusieurs tours sur le **même
fil**, exactement comme le fait le chat : le client renvoie l'historique qu'il a
reçu, qui grandit. À chaque tour, on relève :

- ce que le client **envoie** (Kio) et l'entrée que le **modèle** voit ;
- ce que le client **attend** (premier chunk → dernier) ;
- la durée du **run côté serveur** (journal), pour attribuer la latence.

Deux mesures d'attribution :

1. si l'entrée du modèle reste bornée alors que le client envoie de plus en
   plus, le backend **coupe** ce qui n'est pas utile — le client n'est pas en
   cause ;
2. si le temps d'attente suit la durée du run (et non la taille envoyée), la
   latence est celle du run.

Usage:
    python scripts/latence_conversation_reelle.py [--tours 5]
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

#: **127.0.0.1 et non `localhost`.** `localhost` résout vers `::1` d'abord ;
#: le backend lié sur `0.0.0.0` n'écoute qu'en IPv4, donc la tentative IPv6 est
#: refusée après ~1,0 s avant le repli — un délai qui n'appartient pas au
#: serveur et fausserait toutes les mesures (constat du 15/11).
BASE = "http://127.0.0.1:8000"
AGENT = "agent_generaliste_readonly"
JOURNAL = "var/backend-8000.log"

FIN_RUN = re.compile(r"run terminé : (.*)$")
ETAPE = re.compile(r"étape model : (.*)$")
CHAMP = re.compile(r"(\w+)=(.+?)(?=, \w+=|$)")

QUESTIONS = (
    "Bonjour.",
    "Cite une mission enregistrée.",
    "Et une offre ?",
    "Combien de sessions ?",
    "Résume en une phrase.",
)


def _appel(chemin: str, corps: dict | None, jeton: str | None = None, *, accept: str | None = None):
    entetes = {"Content-Type": "application/json", "Accept": accept or "text/event-stream"}
    if jeton:
        entetes["Authorization"] = f"Bearer {jeton}"
    donnees = json.dumps(corps).encode() if corps is not None else None
    return urllib.request.urlopen(
        urllib.request.Request(f"{BASE}{chemin}", data=donnees, headers=entetes), timeout=600
    )


def _connexion() -> str:
    with _appel(
        "/api/v1/auth/login",
        {"email": "admin@carso.mg", "mot_de_passe": "CarsoAdmin2026!"},
        accept="application/json",
    ) as reponse:
        return json.load(reponse)["access_token"]


def _taille() -> int:
    import os

    return os.path.getsize(JOURNAL) if os.path.exists(JOURNAL) else 0


def _mesures_depuis(offset: int) -> list[dict[str, object]]:
    """Étapes model et bilan écrits après le lancement."""
    trouves: list[dict[str, object]] = []
    with open(JOURNAL, encoding="utf-8", errors="replace") as flux:
        flux.seek(offset)
        for ligne in flux.read().splitlines():
            for motif, genre in ((ETAPE, "etape"), (FIN_RUN, "run")):
                m = motif.search(ligne)
                if not m:
                    continue
                d = {cle: val.strip().rstrip(",") for cle, val in CHAMP.findall(m.group(1))}
                d["genre"] = genre
                trouves.append(d)
    return trouves


def _tour(jeton: str, fil: str, historique: list[dict]) -> dict[str, object]:
    corps = {
        "threadId": fil,
        "runId": f"conv-{uuid.uuid4().hex[:8]}",
        "messages": historique,
        "tools": [],
        "context": [],
        "forwardedProps": {},
        "state": {},
        "resume": [],
    }
    taille = len(json.dumps(corps).encode())
    debut = time.monotonic()
    reponse = _appel(f"/api/v1/agui/{AGENT}", corps, jeton)
    entetes = time.monotonic() - debut
    premier = dernier = None
    textes: list[str] = []
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
            evenement = json.loads(contenu)
            if evenement.get("type") == "TEXT_MESSAGE_CONTENT":
                textes.append(evenement.get("delta", ""))
    return {
        "octets_envoyes": taille,
        "entetes": entetes,
        "flux": (dernier - premier) if premier and dernier else None,
        "total": time.monotonic() - debut,
        "texte": "".join(textes),
    }


def principal(argv: list[str]) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    analyseur = argparse.ArgumentParser(description=__doc__)
    analyseur.add_argument("--tours", type=int, default=5)
    options = analyseur.parse_args(argv)

    jeton = _connexion()
    fil = str(uuid.uuid4())
    historique: list[dict] = []
    offset = _taille()
    lignes: list[dict[str, object]] = []

    print(f"{'tour':>4} {'envoyé Kio':>10} {'modèle voit':>12} {'en-têtes':>9} "
          f"{'flux client':>11} {'run serveur':>11}")
    for i in range(options.tours):
        question = QUESTIONS[i % len(QUESTIONS)]
        historique.append({"id": f"u{i}", "role": "user", "content": question})
        resultat = _tour(jeton, fil, historique)
        # Le client renvoie au tour suivant l'histoire telle qu'il l'a reçue.
        if resultat["texte"]:
            historique.append({"id": f"a{i}", "role": "assistant", "content": resultat["texte"]})

        nouvelles = _mesures_depuis(offset)
        offset = _taille()
        entrees = [
            int(m["entree"].replace(" car.", "").replace(",", ""))
            for m in nouvelles
            if m["genre"] == "etape" and "entree" in m
        ]
        bilan = next((m for m in reversed(nouvelles) if m["genre"] == "run"), None)
        modele_voit = max(entrees) if entrees else 0
        run_serveur = (
            float(str(bilan["duree"]).replace(" s", "")) if bilan and "duree" in bilan else 0.0
        )
        lignes.append(
            {
                "tour": i + 1,
                "envoye": resultat["octets_envoyes"],
                "modele_voit": modele_voit,
                "flux": resultat["flux"],
                "run": run_serveur,
            }
        )
        print(
            f"{i + 1:>4} {resultat['octets_envoyes'] / 1024:>10.1f} {modele_voit:>12,} "
            f"{resultat['entetes']:>8.2f}s "
            f"{(resultat['flux'] or 0):>10.2f}s {run_serveur:>10.2f}s"
        )

    print()
    croissances = [tour for tour in lignes if tour["modele_voit"]]
    if croissances:
        premier = croissances[0]["modele_voit"]
        dernier = croissances[-1]["modele_voit"]
        print(
            f"Entrée du modèle : {premier:,} car. (tour 1) -> {dernier:,} car. "
            f"(tour {croissances[-1]['tour']})"
        )
        facteur = dernier / premier if premier else 0
        print(f"Facteur de croissance : x{facteur:.2f}")
    envoyes = [tour["envoye"] for tour in lignes]
    print(
        f"Requête client    : {envoyes[0] / 1024:.1f} Kio -> {envoyes[-1] / 1024:.1f} Kio "
        f"(x{envoyes[-1] / envoyes[0]:.2f})"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - outil en ligne de commande
    raise SystemExit(principal(sys.argv[1:]))
