"""Test réel — connecteur Meta « WhatsApp Business Tools » vu par l'API (incr. 47).

Ce que ce test vérifie, sur le **vrai** serveur Meta et par HTTP :

1. l'état d'un connecteur OAuth avant autorisation : la sonde dit
   « Autorisation OAuth requise », elle n'invente pas un succès ;
2. le bouton « S'authentifier » rend le **refus exact de Meta** (400
   ``invalid_client_metadata``) **immédiatement**, avec le conseil d'utiliser un
   client pré-enregistré — au lieu d'un « injoignable » opaque après 30 s ;
3. l'état est conservé et relu (statut ``erreur`` + motif) ;
4. le connecteur est nettoyé à la fin (aucun résidu dans la base).

Exécution : serveur démarré, puis
``./.venv/Scripts/python.exe _test_reel_oauth_meta.py``
"""

import sys
import time

import httpx

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = "http://127.0.0.1:8000/api/v1"
AGENT = "agent_generaliste_readonly"
NOM = "whatsapp-business-tools"
URL_META = "https://mcp.facebook.com/whatsapp_business_tools"

echecs: list[str] = []


def verifier(condition: bool, message: str) -> None:
    if condition:
        print(f"  OK    {message}")
    else:
        print(f"  ECHEC {message}")
        echecs.append(message)


def connecter(email: str, mot_de_passe: str) -> httpx.Client:
    reponse = httpx.post(
        f"{BASE}/auth/login",
        json={"email": email, "mot_de_passe": mot_de_passe},
        timeout=30,
    )
    reponse.raise_for_status()
    return httpx.Client(
        base_url=BASE,
        headers={"Authorization": f"Bearer {reponse.json()['access_token']}"},
        timeout=120,
    )


client = connecter("formateur.demo@carso.mg", "CarsoDemo2026!")

print("\n0. Nettoyage préalable")
suppression = client.delete(f"/parametres/mcp/personnel/{NOM}")
verifier(suppression.status_code in (204, 404), f"aucun résidu : {suppression.status_code}")

print("\n1. Déclaration du connecteur Meta en mode OAuth")
ecriture = client.put(
    f"/parametres/mcp/personnel/{NOM}",
    json={
        "transport": "streamable_http",
        "url": URL_META,
        "commande": None,
        "arguments": [],
        "agents": [AGENT],
        "en_tetes": None,
        "mode_authentification": "oauth",
        "oauth_client_id": None,
        "actif": True,
    },
)
verifier(ecriture.status_code == 200, f"PUT personnel OAuth : {ecriture.status_code}")
if ecriture.status_code != 200:
    print(ecriture.text[:400])
    sys.exit(1)
corps = ecriture.json()
verifier(corps["mode_authentification"] == "oauth", "le mode OAuth est enregistré")
verifier(corps["oauth_statut"] == "jamais", "aucune autorisation n'existe encore")

liste = client.get("/parametres/mcp/personnel").json()
verifier(
    liste["uri_redirection"].endswith("/parametres/mcp/personnel/oauth/callback"),
    f"l'URI à déclarer à Meta est exposée : {liste['uri_redirection']}",
)

print("\n2. Sonde avant autorisation")
sonde = client.post(f"/parametres/mcp/personnel/{NOM}/tester")
verifier(sonde.status_code == 200, f"POST tester : {sonde.status_code}")
resultat = sonde.json()["sonde"]
print(f"  info  sonde = {resultat['erreur']}")
verifier(resultat["ok"] is False, "le connecteur n'est pas déclaré utilisable")
verifier(
    "Autorisation OAuth requise" in resultat["erreur"],
    "la cause est l'autorisation, pas le réseau",
)

print("\n3. « S'authentifier » face au refus de Meta")
debut = time.monotonic()
refus = client.post(f"/parametres/mcp/personnel/{NOM}/autoriser")
duree = time.monotonic() - debut
verifier(refus.status_code == 422, f"le refus est un 422 explicite : {refus.status_code}")
message = refus.json()["error"]["message"] if refus.status_code == 422 else refus.text
print(f"  info  message = {message[:300]}")
verifier("Registration failed" in message, "le motif du serveur est rendu tel quel")
verifier("invalid_client_metadata" in message, "l'erreur OAuth de Meta est conservée")
verifier("pré-enregistré" in message, "le remède est nommé (client pré-enregistré)")
verifier(duree < 20, f"le refus est immédiat ({duree:.1f} s), sans attendre le consentement")

print("\n4. L'état est conservé et relu")
relu = next(
    c for c in client.get("/parametres/mcp/personnel").json()["connecteurs"] if c["nom"] == NOM
)
verifier(relu["oauth_statut"] == "erreur", "le statut dit l'échec d'autorisation")
verifier(
    "Registration failed" in (relu["oauth_derniere_erreur"] or ""),
    "le motif est conservé pour l'écran",
)

print("\n5. Nettoyage")
verifier(client.delete(f"/parametres/mcp/personnel/{NOM}").status_code == 204, "connecteur retiré")
tous = client.get("/parametres/mcp/personnel").json()["connecteurs"]
reste = [c["nom"] for c in tous if c["personnel"]]
verifier(NOM not in reste, f"plus aucune trace (restants : {reste})")

print(f"\n{'ECHECS: ' + str(len(echecs)) if echecs else 'TOUT OK'}")
for echec in echecs:
    print(f"  - {echec}")
sys.exit(1 if echecs else 0)
