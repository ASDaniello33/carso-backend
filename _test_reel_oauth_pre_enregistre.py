"""Test réel — chemin « client OAuth pré-enregistré » (incrément 47).

Ce test répond à une situation concrète : Meta refuse l'enregistrement dynamique
(``invalid_client_metadata``) et le conseil affiché est « renseignez le client
pré-enregistré ». Ce chemin n'était couvert qu'en test in-process ; ici il est
exercé **contre le serveur en cours** (vraie base, vrai chiffrement, vrai
serveur OAuth HTTP local qui compte les enregistrements).

Ce qui est vérifié :

1. serveur fermé (comme Meta) sans client fourni → refus explicite, un appel
   d'enregistrement ;
2. le même serveur avec un client pré-enregistré → autorisation obtenue,
   **zéro** enregistrement dynamique ;
3. révocation puis nouvelle autorisation → le client est **réutilisé**, toujours
   zéro enregistrement ;
4. double clic sur « S'authentifier » → la tentative remplacée ne doit pas
   écraser l'état de la tentative en cours ;
5. « S'authentifier » sur un connecteur **déjà autorisé** → réponse immédiate
   qui dit l'état, au lieu d'attendre trente secondes un consentement qui ne
   viendra jamais (défaut trouvé par ce test le 05/10) ;
6. un client pré-enregistré renseigné **après** un enregistrement dynamique
   réussi remplace bien le client dynamique.

Exécution : serveur démarré, puis
``./.venv/Scripts/python.exe _test_reel_oauth_pre_enregistre.py``
"""

import sys
import time
from urllib.parse import parse_qs, urlparse

import httpx

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Le faux serveur OAuth est celui des tests de flux : un vrai serveur HTTP
# (uvicorn) qui exige PKCE, refuse le jeton sans Authorization et **compte** les
# enregistrements dynamiques. Le réutiliser évite deux implémentations.
sys.path.insert(0, "tests")
from test_mcp_oauth_flux import _demarrer_faux_serveur  # noqa: E402

BASE = "http://127.0.0.1:8000/api/v1"
AGENT = "agent_generaliste_readonly"
NOM = "faux-pre-enregistre"

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


def corps(url: str, client_id: str | None = None) -> dict:
    return {
        "transport": "streamable_http",
        "url": url,
        "commande": None,
        "arguments": [],
        "agents": [AGENT],
        "en_tetes": None,
        "mode_authentification": "oauth",
        "oauth_client_id": client_id,
        "actif": True,
    }


def connecteur(client: httpx.Client) -> dict:
    liste = client.get("/parametres/mcp/personnel").json()
    return next((c for c in liste["connecteurs"] if c["nom"] == NOM), {})


def purger(client: httpx.Client) -> None:
    """Supprime le connecteur de test et **attend qu'il soit vraiment parti**.

    Pourquoi l'attente : ``get_session`` valide (commit) dans la sortie de la
    dépendance, donc **après** l'envoi de la réponse. Un ``DELETE`` renvoie
    204 alors que la suppression n'est pas encore durable ; le ``PUT`` suivant
    se heurte alors à la ligne (``uq_serveurs_mcp_proprietaire_nom``) ou écrit
    dans une ligne que la suppression en attente efface. Défaut **préexistant**,
    hors OAuth, reproduit le 05/10/2026 par ``_diag_client_ignore.py`` et
    documenté dans ``docs/validation/INCREMENT-47-MCP-OAUTH.md``.

    Ce test mesure le chemin OAuth : on sérialise donc ses propres écritures
    plut\u00f4t que d'attribuer cette course au flux. Un echec ici reste
    signale (voir ``ecrire``).
    """
    client.delete(f"/parametres/mcp/personnel/{NOM}")
    for _ in range(100):
        if not connecteur(client):
            return
        time.sleep(0.05)
    raise AssertionError(
        "le connecteur de test n'a pas disparu : la suppression n'est pas "
        "durable (défaut de transaction connu, voir purger())"
    )


def ecrire(client: httpx.Client, corps_connecteur: dict) -> httpx.Response:
    """PUT, puis **attente de sa lisibilité** par la requête suivante.

    Même cause que ``purger`` : la validation a lieu dans la sortie de la
    dépendance, donc après l'envoi de la réponse. Un ``PUT`` renvoie 200 sans
    que la ligne soit encore visible par la requête suivante, qui peut alors
    lire l'ancienne valeur (et repartir en enregistrement dynamique) ou ne pas
    trouver du tout le connecteur. On relit donc jusqu'à voir l'écrit.

    Un 500 reste un échec explicite : c'est une violation de unicité, pas un
    aléa de course qu'il faudrait absorber.
    """
    reponse = client.put(f"/parametres/mcp/personnel/{NOM}", json=corps_connecteur)
    if reponse.status_code == 500:
        raise AssertionError(
            "PUT → 500 : violation de unicité sur le couple (propriétaire, nom). "
            "Défaut de transaction préexistant, sans rapport avec OAuth."
        )
    attendu = corps_connecteur.get("oauth_client_id")
    for _ in range(100):
        lue = connecteur(client)
        if lue and lue.get("oauth_client_id") == (attendu or None):
            return reponse
        time.sleep(0.05)
    raise AssertionError(
        f"l'écriture du client OAuth {attendu!r} n'est jamais devenue visible : "
        "défaut de transaction préexistant (voir ecrire())"
    )


def consentir(client: httpx.Client, url_autorisation: str) -> httpx.Response:
    """Joue le navigateur : suit la redirection, puis rappelle le backend."""
    redirection = httpx.get(url_autorisation, follow_redirects=False, timeout=10)
    if redirection.status_code != 302:
        raise AssertionError(f"l'autorisation n'a pas redirigé : {redirection.status_code}")
    params = parse_qs(urlparse(redirection.headers["location"]).query)
    return client.get(
        "/parametres/mcp/personnel/oauth/callback",
        params={"code": params["code"][0], "state": params["state"][0]},
        follow_redirects=False,
    )


client = connecter("formateur.demo@carso.mg", "CarsoDemo2026!")
purger(client)

# --- 1 à 3 : serveur fermé (cas Meta), client pré-enregistré -------------------
print("\n1. Serveur fermé (comme Meta), aucun client fourni")
flux_ferme = _demarrer_faux_serveur(refuser_enregistrement=True)
etat = next(flux_ferme)
try:
    ecrit = ecrire(client, corps(f"{etat.base}/mcp"))
    verifier(ecrit.status_code == 200, f"connecteur déclaré : {ecrit.status_code}")

    refus = client.post(f"/parametres/mcp/personnel/{NOM}/autoriser")
    message = refus.json().get("error", {}).get("message", "")
    verifier(refus.status_code == 422, f"refus explicite : {refus.status_code}")
    verifier("invalid_client_metadata" in message, "le motif du serveur est rendu")
    verifier("pré-enregistré" in message, "le remède est nommé")
    verifier(len(etat.registrations) == 1, "un enregistrement dynamique a bien été tenté")

    print("\n2. Le même serveur, client pré-enregistré fourni")
    ecrit = ecrire(
        client, corps(f"{etat.base}/mcp", "client-fourni-par-le-serveur")
    )
    verifier(ecrit.json().get("oauth_client_id") == "client-fourni-par-le-serveur",
             "le client fourni est enregistré sur le connecteur")

    demarre = client.post(f"/parametres/mcp/personnel/{NOM}/autoriser")
    detail = "".join(demarre.text.split())
    verifier(
        demarre.status_code == 200,
        f"autorisation démarrée : {demarre.status_code} — {detail[:220]}",
    )
    url = demarre.json().get("url_autorisation", "")
    verifier(
        parse_qs(urlparse(url).query).get("client_id") == ["client-fourni-par-le-serveur"],
        "l'URL de consentement porte le client fourni",
    )
    verifier(len(etat.registrations) == 1, "aucun enregistrement dynamique supplémentaire")

    retour = consentir(client, url)
    verifier(retour.status_code == 303, f"retour du navigateur : {retour.status_code}")
    verifier("mcp=autorise" in retour.headers.get("location", ""), "consentement accepté")
    verifier(len(etat.registrations) == 1, "l'échange du code n'a rien enregistré")

    statut = connecteur(client)["oauth_statut"]
    verifier(statut == "autorise", f"statut après consentement : {statut}")

    sonde = client.post(f"/parametres/mcp/personnel/{NOM}/tester").json()["sonde"]
    verifier(sonde["ok"] is True, "la sonde charge les outils du faux serveur")
    verifier(sonde["nb_outils"] == 1, f"outils chargés : {sonde['nb_outils']}")
    verifier(any("ping" in nom for nom in sonde["outils"]), f"outils : {sonde['outils']}")

    print("\n3. Révocation puis nouvelle autorisation : le client est réutilisé")
    revoque = client.post(f"/parametres/mcp/personnel/{NOM}/revoquer")
    verifier(revoque.json()["oauth_statut"] == "jamais", "la révocation remet le statut à zéro")

    redemarre = client.post(f"/parametres/mcp/personnel/{NOM}/autoriser")
    verifier(redemarre.status_code == 200, f"seconde autorisation : {redemarre.status_code}")
    url2 = redemarre.json()["url_autorisation"]
    verifier(
        parse_qs(urlparse(url2).query).get("client_id") == ["client-fourni-par-le-serveur"],
        "le client fourni est réutilisé après révocation",
    )
    consentir(client, url2)
    verifier(connecteur(client)["oauth_statut"] == "autorise", "seconde autorisation acceptée")
    verifier(len(etat.registrations) == 1, "toujours aucun enregistrement dynamique")

    print("\n4. Double clic sur « S'authentifier » : la tentative remplacée ne décide pas")
    # Sans jeton : sinon le fournisseur réutilise le jeton valide et n'ouvre
    # aucune demande de consentement (c'est l'objet de l'étape 5).
    client.post(f"/parametres/mcp/personnel/{NOM}/revoquer")
    premiere = client.post(f"/parametres/mcp/personnel/{NOM}/autoriser")
    seconde = client.post(f"/parametres/mcp/personnel/{NOM}/autoriser")
    verifier(
        premiere.status_code == 200 and seconde.status_code == 200,
        "les deux demandes partent",
    )
    time.sleep(2.0)
    statut = connecteur(client)["oauth_statut"]
    verifier(
        statut == "en_attente",
        f"l'état reste en attente de la demande en cours : {statut}",
    )
    consentir(client, seconde.json()["url_autorisation"])
    verifier(connecteur(client)["oauth_statut"] == "autorise", "la seconde demande aboutit")

    print("\n5. « S'authentifier » sur un connecteur déjà autorisé : réponse immédiate")
    debut = time.monotonic()
    deja = client.post(f"/parametres/mcp/personnel/{NOM}/autoriser")
    duree = time.monotonic() - debut
    message = deja.json().get("error", {}).get("message", "")
    verifier(duree < 5, f"la réponse ne fait pas attendre 30 s : {duree:.1f} s")
    verifier(deja.status_code == 422, f"statut explicite : {deja.status_code}")
    verifier("déjà autorisé" in message, f"le message dit l'état : {message[:120]}")
    verifier("Révoquer" in message, "le message nomme l'action utile")
finally:
    purger(client)
    try:
        next(flux_ferme)
    except StopIteration:
        pass

# --- 5 : client pré-enregistré après un enregistrement dynamique réussi -------
print("\n6. Client pré-enregistré renseigné après un enregistrement dynamique")
flux_ouvert = _demarrer_faux_serveur(refuser_enregistrement=False)
etat2 = next(flux_ouvert)
try:
    ecrire(client, corps(f"{etat2.base}/mcp"))
    demarre = client.post(f"/parametres/mcp/personnel/{NOM}/autoriser")
    verifier(demarre.status_code == 200, "première autorisation dynamique démarrée")
    consentir(client, demarre.json()["url_autorisation"])
    verifier(connecteur(client)["oauth_statut"] == "autorise", "autorisation dynamique acceptée")
    verifier(len(etat2.registrations) == 1, "un client dynamique a été enregistré")

    # Révoquer d'abord : sans cela, le jeton valide ferait répondre « déjà
    # autorisé » (étape 5) et le client effectivement présenté resterait invisible.
    client.post(f"/parametres/mcp/personnel/{NOM}/revoquer")

    ecrit = ecrire(
        client, corps(f"{etat2.base}/mcp", "client-impose-par-l-utilisateur")
    )
    verifier(ecrit.json()["oauth_client_id"] == "client-impose-par-l-utilisateur",
             "le connecteur porte le client fourni")

    redemarre = client.post(f"/parametres/mcp/personnel/{NOM}/autoriser")
    verifier(redemarre.status_code == 200, f"autorisation relancée : {redemarre.status_code}")
    url = redemarre.json()["url_autorisation"]
    utilise = parse_qs(urlparse(url).query).get("client_id")
    verifier(
        utilise == ["client-impose-par-l-utilisateur"],
        f"le client fourni remplace celui enregistré dynamiquement : {utilise}",
    )
    consentir(client, url)
    verifier(connecteur(client)["oauth_statut"] == "autorise", "le client fourni est accepté")
    verifier(
        len(etat2.registrations) == 1,
        "aucun nouvel enregistrement : le client fourni a servi tel quel",
    )
finally:
    purger(client)
    try:
        next(flux_ouvert)
    except StopIteration:
        pass

client.close()

print(f"\n{'TOUT OK' if not echecs else 'ECHECS: ' + str(len(echecs))}")
for motif in echecs:
    print(f"  - {motif}")
sys.exit(1 if echecs else 0)
