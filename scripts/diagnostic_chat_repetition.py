"""Diagnostic : un second message est-il bien traité (et non rejoué) ?

Reproduit **exactement** ce que fait le client AG-UI entre deux tours :

1. tour 1 — le client envoie un seul message utilisateur ;
2. tour 2 — le client rejoue l'historique reçu dans le ``MESSAGES_SNAPSHOT``
   du tour 1, **avec les ids renvoyés par le serveur**, puis ajoute le nouveau
   message utilisateur.

Ce que le script imprime, sur chaque tour : le texte de l'assistant, puis la
liste des messages du ``MESSAGES_SNAPSHOT`` final (id, rôle, début du contenu).
Un second message perdu se voit immédiatement : son id n'apparaît pas dans le
snapshot du tour 2, et la réponse répète celle du tour 1.

Usage ::

    ./.venv/Scripts/python.exe -m scripts.diagnostic_chat_repetition

Variables : ``CARSO_BASE`` (défaut ``http://127.0.0.1:8000``),
``CARSO_AGENT`` (défaut ``agent_personnel``), ``CARSO_EMAIL``,
``CARSO_MOT_DE_PASSE``.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid

BASE = os.environ.get("CARSO_BASE", "http://127.0.0.1:8000")
AGENT = os.environ.get("CARSO_AGENT", "agent_personnel")
EMAIL = os.environ.get("CARSO_EMAIL", "admin@carso.mg")
MOT_DE_PASSE = os.environ.get("CARSO_MOT_DE_PASSE", "CarsoAdmin2026!")

PREMIER = "Bonjour"
SECOND = 'Dis exactement "CARSO"'


def _auth() -> str:
    corps = json.dumps({"email": EMAIL, "mot_de_passe": MOT_DE_PASSE}).encode()
    requete = urllib.request.Request(
        f"{BASE}/api/v1/auth/login",
        data=corps,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(requete) as reponse:
        return json.load(reponse)["access_token"]


def _run(token: str, thread_id: str, messages: list[dict]) -> tuple[list[dict], float]:
    charge = {
        "threadId": thread_id,
        "runId": f"diag-{uuid.uuid4().hex[:8]}",
        "messages": messages,
        "tools": [],
        "context": [],
        "forwardedProps": {},
        "state": {},
        "resume": [],
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
    debut = time.monotonic()
    evenements: list[dict] = []
    with urllib.request.urlopen(requete, timeout=600) as flux:
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
                continue
    return evenements, time.monotonic() - debut


def _duree_carres(evenements: list[dict]) -> tuple[str, float | None, list[dict]]:
    """Texte de l'assistant, instant du premier octet de texte, snapshot final."""
    texte: list[str] = []
    premier_texte: float | None = None
    snapshot: list[dict] = []
    for evenement in evenements:
        if evenement.get("type") == "TEXT_MESSAGE_CONTENT":
            if premier_texte is None:
                premier_texte = 0.0
            texte.append(str(evenement.get("delta") or ""))
        if evenement.get("type") == "MESSAGES_SNAPSHOT":
            snapshot = list(evenement.get("messages") or [])
    return "".join(texte).strip(), premier_texte, snapshot


def _resume(snapshot: list[dict]) -> list[str]:
    lignes = []
    for message in snapshot:
        role = message.get("role")
        ident = message.get("id")
        contenu = message.get("content")
        if not isinstance(contenu, str):
            contenu = json.dumps(contenu, ensure_ascii=False)[:80]
        contenu = " ".join(contenu.split())[:70]
        appels = message.get("toolCalls") or message.get("tool_calls") or []
        marque = f" [{len(appels)} appel(s)]" if appels else ""
        lignes.append(f"  {role:<9} {ident}  « {contenu} »{marque}")
    return lignes


def main() -> int:
    token = _auth()
    thread_id = str(uuid.uuid4())
    print(f"agent {AGENT} — fil {thread_id}", flush=True)

    # --- Tour 1 : un seul message -------------------------------------------
    evenements, duree = _run(
        token, thread_id, [{"id": "u-bonjour", "role": "user", "content": PREMIER}]
    )
    reponse1, _, snapshot1 = _duree_carres(evenements)
    erreurs = [e for e in evenements if e.get("type") == "RUN_ERROR"]
    print(f"\n=== tour 1 ({duree:.1f} s) ===")
    print(f"réponse : « {' '.join(reponse1.split())[:120]} »")
    for erreur in erreurs:
        print(f"ERREUR : {erreur.get('code')} {erreur.get('message')}")
    print("snapshot :")
    for ligne in _resume(snapshot1):
        print(ligne)

    # --- Tour 2 : historique du snapshot + nouveau message -------------------
    historique = [
        {
            "id": message.get("id"),
            "role": message.get("role"),
            "content": message.get("content"),
        }
        for message in snapshot1
        if message.get("role") in ("user", "assistant")
    ]
    historique.append({"id": "u-carso", "role": "user", "content": SECOND})
    evenements, duree = _run(token, thread_id, historique)
    reponse2, _, snapshot2 = _duree_carres(evenements)
    erreurs = [e for e in evenements if e.get("type") == "RUN_ERROR"]
    print(f"\n=== tour 2 ({duree:.1f} s) ===")
    print(f"message envoyé : {historique[-1]}")
    print(f"réponse : « {' '.join(reponse2.split())[:120]} »")
    for erreur in erreurs:
        print(f"ERREUR : {erreur.get('code')} {erreur.get('message')}")
    print("snapshot :")
    for ligne in _resume(snapshot2):
        print(ligne)

    ids2 = [message.get("id") for message in snapshot2]
    print("\n--- verdict ---")
    print(f"nouveau message présent dans l'état : {'u-carso' in ids2}")
    print(f"réponse identique au tour 1 : {reponse1.strip() == reponse2.strip()}")
    print(f"messages dans l'état : {len(snapshot2)} (doublons : {len(ids2) - len(set(ids2))})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
