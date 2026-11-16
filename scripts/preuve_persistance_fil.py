"""Preuve : un fil de conversation survit à un **redémarrage du backend**.

**Question posée (15/11).** Après correction du repli mémoire du checkpointer
(alignement de ``langgraph-checkpoint`` sur la plage exigée par
``langgraph-checkpoint-postgres``), prouver qu'un fil de conversation de
production est réellement durable — et pas seulement que ``setup()`` fonctionne.

**Protocole (le seul qui démontre quelque chose).**

1. **Tour 1** — run AG-UI réel sur ``/agui/{agent}``, fil neuf, avec un message
   qui demande à l'agent de **retenir une valeur non déductible** (« retiens le
   code X »). L'agent répond ; la valeur est écrite dans le checkpoint.
2. **Redémarrage du backend** — le processus est arrêté puis relancé par
   l'appelant (ce script ne le fait pas : il ne doit pas tuer un serveur qu'il
   n'a pas lancé).
3. **Tour 2** — **même** ``threadId``, et la requête ne porte **que** le nouveau
   message : aucun historique n'est renvoyé. Si le modèle retrouve la valeur,
   c'est qu'elle vient du **checkpoint PostgreSQL** — un ``MemorySaver`` aurait
   perdu la mémoire du fil au redémarrage.

Le second point est ce qui rend la preuve concluante : renvoyer l'historique
complet ne prouverait rien du backend (voir
``docs/validation/HISTORIQUE-CHECKPOINTER-OU-NAVIGATEUR-2026-11-15.md``).

**Ce qui est prouvé en plus.** ``/api/v1/health`` expose le mode réel du
checkpointer (``postgres``/``memoire``). Il est lu **après** chaque run, jamais
avant : la fabrique est paresseuse par contrat (créée au premier graphe, et le
liveness ne doit pas ouvrir de connexion PostgreSQL), donc l'état n'est résolu
qu'une fois un agent construit. Le script refuse de conclure si le mode lu
n'est pas ``postgres``.

Usage (le fil survit au redémarrage, donc les deux tours sont deux commandes) :

    # 1. backend démarré, puis :
    python scripts/preuve_persistance_fil.py tour1 --code CARSO-7412
    # 2. redémarrer le backend, puis :
    python scripts/preuve_persistance_fil.py tour2 --code CARSO-7412 \
        --fil <threadId affiché par le tour 1>

Le ``threadId`` du tour 1 est affiché et (par commodité) écrit dans
``var/preuve-persistance.fil`` — jamais un secret.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request
import uuid

sys.path.insert(0, ".")

#: ``127.0.0.1`` et non ``localhost`` : le backend n'écoute qu'en IPv4 et
#: ``localhost`` résout ``::1`` d'abord (constat du 15/11, ~1,0 s perdue).
BASE = "http://127.0.0.1:8000"
AGENT = "agent_generaliste_readonly"
FICHIER_FIL = "var/preuve-persistance.fil"


def _appel(chemin: str, corps: dict | None = None, jeton: str | None = None):
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


def _mode_checkpointer() -> dict:
    """Mode réel du checkpointer (aucun secret) tel que le backend le publie."""
    with urllib.request.urlopen(f"{BASE}/api/v1/health", timeout=30) as flux:
        return json.load(flux).get("checkpointer") or {}


def _derouler(jeton: str, message: str, fil: str) -> dict:
    """Un run AG-UI ; ne transmet **que** le message du tour (pas d'historique)."""
    charge = {
        "threadId": fil,
        "runId": f"preuve-{uuid.uuid4().hex[:8]}",
        "messages": [{"id": f"m-{uuid.uuid4().hex[:8]}", "role": "user", "content": message}],
        "tools": [],
        "context": [],
        "forwardedProps": {},
        "state": {},
        "resume": [],
    }
    textes: list[str] = []
    erreur: str | None = None
    with _appel(f"/api/v1/agui/{AGENT}", charge, jeton) as flux:
        for brut in flux:
            ligne = brut.decode("utf-8", "replace").strip()
            if not ligne.startswith("data:"):
                continue
            contenu = ligne[5:].strip()
            if not contenu or contenu == "[DONE]":
                continue
            evenement = json.loads(contenu)
            if evenement.get("type") == "TEXT_MESSAGE_CONTENT":
                textes.append(evenement.get("delta", ""))
            elif evenement.get("type") == "RUN_ERROR":
                erreur = evenement.get("message")
    return {"texte": "".join(textes).strip(), "erreur": erreur}


def _message_tour1(code: str) -> str:
    return (
        f"Retiens bien ce code de mission : {code}. "
        "Ne le recherche nulle part, contente-toi de le mémoriser et de me le "
        "confirmer en une phrase."
    )


def _message_tour2() -> str:
    return (
        "Quel code de mission exact t'ai-je demandé de retenir juste avant ? "
        "Réponds uniquement par ce code, sans explication."
    )


def principal(argv: list[str]) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    analyseur = argparse.ArgumentParser(description=__doc__)
    analyseur.add_argument("tour", choices=["tour1", "tour2"])
    analyseur.add_argument("--code", default="CARSO-7412", help="valeur à retenir (tour 1)")
    analyseur.add_argument("--fil", default=None, help="threadId du tour 1 (tour 2)")
    options = analyseur.parse_args(argv)

    try:
        jeton = _connexion()
    except Exception as erreur:  # noqa: BLE001
        print(f"ECHEC connexion : {type(erreur).__name__} — {str(erreur)[:200]}")
        return 2

    if options.tour == "tour1":
        fil = str(uuid.uuid4())
        print(f"fil (threadId) = {fil}")
        with open(FICHIER_FIL, "w", encoding="utf-8") as flux:
            flux.write(fil)
        debut = time.monotonic()
        resultat = _derouler(jeton, _message_tour1(options.code), fil)
        print(f"tour 1 en {time.monotonic() - debut:.1f} s")
        if resultat["erreur"]:
            print(f"  RUN_ERROR : {resultat['erreur']}")
            return 1
        print(f"  réponse : {resultat['texte'][:300]}")
        etat = _mode_checkpointer()
        print(f"mode checkpointer (lu après le run) = {json.dumps(etat, ensure_ascii=False)}")
        if etat.get("mode") != "postgres":
            print("  REFUS : le mode n'est pas postgres — le fil ne serait pas durable.")
            return 3
        print()
        print("Redémarrer le backend, puis relancer avec --fil " + fil)
        return 0

    fil = options.fil
    if not fil and os.path.exists(FICHIER_FIL):
        with open(FICHIER_FIL, encoding="utf-8") as flux:
            fil = flux.read().strip()
    if not fil:
        print("ECHEC : --fil requis (threadId du tour 1)")
        return 2
    print(f"fil (threadId) = {fil}  ← repris APRÈS redémarrage du backend")
    debut = time.monotonic()
    resultat = _derouler(jeton, _message_tour2(), fil)
    print(f"tour 2 en {time.monotonic() - debut:.1f} s")
    if resultat["erreur"]:
        print(f"  RUN_ERROR : {resultat['erreur']}")
        return 1
    print(f"  réponse : {resultat['texte'][:300]}")
    etat = _mode_checkpointer()
    print(f"mode checkpointer (lu après le run) = {json.dumps(etat, ensure_ascii=False)}")
    print()
    if etat.get("mode") != "postgres":
        print("REFUS : le mode n'est pas postgres — la conclusion ne tiendrait pas.")
        return 3
    if options.code in resultat["texte"]:
        print(f"OK : le fil a survécu au redémarrage — « {options.code} » retrouvé")
        print("     depuis le checkpoint PostgreSQL (aucun historique renvoyé).")
        return 0
    print(f"ECHEC : « {options.code} » absent de la réponse — le fil n'a pas survécu")
    print("        (ou le modèle n'a pas restitué la valeur ; relancer pour écarter")
    print("        un aléa de génération).")
    return 1


if __name__ == "__main__":  # pragma: no cover - outil en ligne de commande
    raise SystemExit(principal(sys.argv[1:]))
