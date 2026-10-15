"""Test réel — surface MCP personnelle (serveur démarré, PostgreSQL).

Ce n'est pas un test unitaire : tout passe par HTTP sur le serveur réel, avec
deux comptes distincts. Vérifie ce que l'interface va réellement consommer.

Exécution : ./.venv/Scripts/python.exe _test_reel_mcp.py
"""

import json
import sys
from typing import Any

import httpx

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = "http://127.0.0.1:8000/api/v1"
AGENT = "agent_generaliste_readonly"

echecs: list[str] = []


def verifier(condition: bool, message: str) -> None:
    if condition:
        print(f"  OK   {message}")
    else:
        print(f"  ECHEC {message}")
        echecs.append(message)


def connecter(email: str, mot_de_passe: str) -> httpx.Client:
    reponse = httpx.post(
        f"{BASE}/auth/login", json={"email": email, "mot_de_passe": mot_de_passe}, timeout=30
    )
    reponse.raise_for_status()
    return httpx.Client(
        base_url=BASE,
        headers={"Authorization": f"Bearer {reponse.json()['access_token']}"},
        timeout=60,
    )


def corps(client: httpx.Client) -> dict[str, Any]:
    return client.get("/parametres/mcp/personnel").json()


admin = connecter("admin@carso.mg", "CarsoAdmin2026!")
formateur = connecter("formateur.demo@carso.mg", "CarsoDemo2026!")

print("\n1. Surface personnelle de l'administrateur")
avant = corps(admin)
verifier("connecteurs" in avant, f"la réponse est un objet ({sorted(avant)})")
verifier("chiffrement_disponible" in avant, "le chiffrement est annoncé avant saisie")
verifier(
    all("personnel" in c for c in avant["connecteurs"]),
    "chaque connecteur porte son drapeau de périmètre",
)
verifier(
    all("personnel" in c for c in avant["connecteurs"]),
    "chaque connecteur annonce son espace",
)
globaux = {c["nom"] for c in admin.get("/parametres/mcp").json()}
visibles = {c["nom"] for c in avant["connecteurs"]}
verifier(globaux.issubset(visibles), f"les {len(globaux)} connecteur(s) globaux sont tous visibles")
perso_admin = sorted(c["nom"] for c in avant["connecteurs"] if c["personnel"])
print(f"  info  connecteurs globaux : {sorted(globaux)}")
print(f"  info  connecteurs personnels de l'admin : {perso_admin}")

print("\n2. Un collaborateur déclare son connecteur")
nom = "outil-demo"
suppression = formateur.delete(f"/parametres/mcp/personnel/{nom}")
verifier(suppression.status_code in (204, 404), f"nettoyage préalable : {suppression.status_code}")
ecriture = formateur.put(
    f"/parametres/mcp/personnel/{nom}",
    json={
        "transport": "streamable_http",
        "url": "https://mcp.exemple.test/mcp",
        "commande": None,
        "arguments": [],
        "agents": [AGENT],
        "en_tetes": {"Authorization": "Bearer jeton-personnel"},
        "actif": True,
    },
)
verifier(ecriture.status_code == 200, f"PUT personnel : {ecriture.status_code}")
lu = ecriture.json()
verifier(lu["personnel"] is True, "la réponse marque le connecteur comme personnel")
verifier(lu["en_tetes"] == ["Authorization"], "seuls les noms d'en-têtes sortent")
verifier("jeton-personnel" not in ecriture.text, "le jeton ne figure jamais dans la réponse")

print("\n3. Isolation : le collaborateur voit le sien, l'admin ne le voit pas")
sa_liste = corps(formateur)
verifier(
    any(c["nom"] == nom and c["personnel"] for c in sa_liste["connecteurs"]),
    "le collaborateur voit son connecteur personnel",
)
verifier(
    not any(c["nom"] == nom for c in corps(admin)["connecteurs"]),
    "l'administration ne liste pas le connecteur d'un compte",
)
verifier(
    nom not in [c["nom"] for c in admin.get("/parametres/mcp").json()],
    "l'onglet Connecteurs MCP (global) l'ignore",
)

print("\n4. L'administration ne peut pas s'approprier ce nom")
conflit = admin.put(
    f"/parametres/mcp/{nom}",
    json={
        "transport": "streamable_http",
        "url": "https://mcp.exemple.test/autre",
        "commande": None,
        "arguments": [],
        "agents": [],
        "en_tetes": None,
        "actif": True,
    },
)
verifier(conflit.status_code == 422, f"PUT global sous un nom personnel refusé : {conflit.status_code}")
verifier(nom in conflit.json()["error"]["message"], "le refus nomme le connecteur en cause")
verifier(
    not any(c["nom"] == nom for c in admin.get("/parametres/mcp").json()),
    "aucun connecteur global n'a été créé",
)

print("\n5. Agents proposés : ceux ouverts au rôle du collaborateur")
catalogue = formateur.get("/agents/catalogue").json()
ids = [a["agent_id"] for a in catalogue]
verifier(AGENT in ids, f"le catalogue du formateur contient {ids}")
verifier(
    "agent_rh" not in ids,
    "un agent hors rôle n'est pas proposé (le backend refuserait la connexion)",
)
verifier(
    ecriture.status_code == 200 and lu["agents"] == [AGENT],
    "le connecteur est bien rattaché à l'agent choisi",
)

print("\n6. Suppression par son propriétaire")
verifier(
    formateur.delete(f"/parametres/mcp/personnel/{nom}").status_code == 204,
    "DELETE personnel",
)
verifier(
    not any(c["nom"] == nom for c in corps(formateur)["connecteurs"]),
    "le connecteur a disparu de son espace",
)
verifier(
    formateur.put(
        f"/parametres/mcp/personnel/{nom}",
        json={"transport": "streamable_http", "url": "https://mcp.exemple.test/mcp", "agents": []},
    ).status_code
    == 200,
    "recreation possible apres suppression (meme nom)",
)
formateur.delete(f"/parametres/mcp/personnel/{nom}")

print("\n7. Etat final de la base")
restants = [c["nom"] for c in corps(admin)["connecteurs"] if c["personnel"]]
verifier(sorted(restants) == sorted(perso_admin), "la liste n'a pas bougé pendant le test")
print(f"  connecteurs personnels de l'admin : {restants}")
print(json.dumps({"echecs": echecs}, ensure_ascii=False))
sys.exit(1 if echecs else 0)