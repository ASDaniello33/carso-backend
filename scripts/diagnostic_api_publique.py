"""Diagnostic d'une API CARSO publiée (tunnel, reverse proxy, URL Render).

Trois pannes très différentes produisent le **même** symptôme dans la console du
navigateur — « CORS error » sur ``POST /auth/login`` :

1. le tunnel renvoie une **page d'avertissement HTML** avant d'atteindre le
   backend (Localtonet et ngrok gratuits) : aucune en-tête CORS, préflight
   refusé, et un en-tête de contournement est impossible à envoyer sur un
   préflight CORS — donc un frontend déployé ne peut pas fonctionner ainsi ;
2. l'origine du frontend est **absente de ``CORS_ORIGINS``** : préflight 400
   « Disallowed CORS origin » ;
3. le backend est **injoignable** ou répond une erreur d'initialisation (5xx).

Le script reproduit exactement ce qu'envoie le navigateur (aucun en-tête
spécial), puis — s'il détecte une page d'avertissement — refait la requête avec
l'en-tête de contournement du tunnel : si le backend répond alors correctement,
la cause est **le tunnel**, pas le code. Il vérifie ensuite le préflight réel
pour l'origine du frontend, puis l'allowlist (une origine inconnue doit être
refusée : un ``200`` signalerait un wildcard).

Usage :

    python scripts/diagnostic_api_publique.py \\
        --api https://atdaulsg7o.localto.net/api/v1 \\
        --origine https://carso-ai.onrender.com

Codes de sortie : 0 sain, 2 page d'avertissement du tunnel, 3 CORS, 4 backend.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

#: Fragments qui trahissent la page d'avertissement d'un tunnel gratuit.
MOTIFS_INTERSTITIEL = ("localtonet", "ngrok", "you are about to navigate")

#: En-tête qui traverse la page d'avertissement Localtonet. Un navigateur ne
#: peut pas l'envoyer sur un préflight : c'est pourquoi la page bloque tout.
ENTETE_CONTOURNEMENT = {"localtonet-skip-warning": "1"}

#: Origine volontairement invalide, pour vérifier que l'allowlist est active.
ORIGINE_TEMOIN = "https://diagnostic.carso.invalid"

DELAI_SECONDES = 15.0
PREFIXE = "/api/v1"
AGENT = "Mozilla/5.0 (compatible; diagnostic-carso)"


@dataclass(frozen=True)
class Reponse:
    """Réponse HTTP minimale ; ``statut=0`` signifie « aucun serveur atteint »."""

    statut: int
    type_contenu: str = ""
    corps: str = ""
    entetes: Mapping[str, str] = field(default_factory=dict)
    #: Raison réseau/TLS quand ``statut == 0`` (sinon vide).
    erreur: str = ""

    @property
    def json(self) -> dict[str, object] | None:
        try:
            charge = json.loads(self.corps)
        except (TypeError, ValueError):
            return None
        return charge if isinstance(charge, dict) else None


def executer(methode: str, url: str, entetes: Mapping[str, str] | None = None) -> Reponse:
    """Exécute une requête et **rend** les erreurs comme des réponses."""
    demandes = {"User-Agent": AGENT, "Accept": "application/json", **(entetes or {})}
    requete = Request(url, method=methode, headers=demandes)
    try:
        with urlopen(requete, timeout=DELAI_SECONDES) as reponse:  # noqa: S310 — URL fournie par l'opérateur
            return _lire(reponse.status, reponse.headers, reponse.read())
    except HTTPError as erreur:
        return _lire(erreur.code, erreur.headers, erreur.read())
    except URLError as erreur:
        return Reponse(statut=0, erreur=str(erreur.reason)[:200])
    except (TimeoutError, OSError) as erreur:
        return Reponse(statut=0, erreur=str(erreur)[:200])


def _lire(statut: int, entetes: Mapping[str, str], corps: bytes) -> Reponse:
    return Reponse(
        statut=statut,
        type_contenu=str(entetes.get("Content-Type", "")),
        corps=corps.decode("utf-8", errors="replace")[:4000],
        entetes={cle.lower(): valeur for cle, valeur in entetes.items()},
    )


def est_interstitiel(reponse: Reponse) -> bool:
    """Vrai si la réponse est la page d'avertissement d'un tunnel gratuit."""
    if "html" not in reponse.type_contenu.lower():
        return False
    corps = reponse.corps.lower()
    return any(motif in corps for motif in MOTIFS_INTERSTITIEL)


def classer_sante(reponse: Reponse) -> str:
    """État du point d'entrée, avant toute question de CORS."""
    if reponse.statut == 0:
        return "injoignable"
    if est_interstitiel(reponse):
        return "interstitiel"
    if reponse.statut >= 500:
        return "erreur_serveur"
    if reponse.statut != 200:
        return "inattendu"
    return "ok" if reponse.json is not None else "non_json"


def classer_preflight(reponse: Reponse, origine: str) -> str:
    """Verdict du préflight comparé à l'origine attendue."""
    if reponse.statut == 0:
        return "injoignable"
    if est_interstitiel(reponse):
        return "interstitiel"
    if reponse.statut == 400:
        return "origine_refusee"
    if reponse.statut != 200:
        return "erreur_serveur"
    if reponse.entetes.get("access-control-allow-origin") != origine:
        return "sans_cors"
    return "ok"


def normaliser_base(api: str) -> str:
    """Accepte ``https://hôte``, ``https://hôte/api/v1`` et la barre finale."""
    base = api.strip().rstrip("/")
    if base.endswith(PREFIXE):
        base = base[: -len(PREFIXE)]
    return base


def _preflight(base: str, origine: str) -> Reponse:
    """Préflight **sans** en-tête de contournement : ce que le navigateur envoie."""
    return executer(
        "OPTIONS",
        f"{base}{PREFIXE}/auth/login",
        {
            "Origin": origine,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )


def diagnostiquer(api: str, origine: str) -> int:
    """Imprime le diagnostic complet et retourne le code de sortie."""
    base = normaliser_base(api)
    print(f"API       : {base}{PREFIXE}")
    print(f"Origine   : {origine}")
    print()

    sante = executer("GET", f"{base}{PREFIXE}/health")
    verdict = classer_sante(sante)

    if verdict == "interstitiel":
        print("ÉCHEC — le tunnel renvoie une page d'avertissement HTML.")
        print(f"         Statut {sante.statut}, {sante.type_contenu or 'sans type'}.")
        print("         Aucune en-tête CORS n'est émise : le préflight du navigateur")
        print("         échoue avant même d'atteindre le backend.")
        # Preuve : le backend est-il sain derrière la page ?
        derriere = executer("GET", f"{base}{PREFIXE}/health", ENTETE_CONTOURNEMENT)
        if classer_sante(derriere) == "ok":
            print()
            print("         Le backend répond correctement avec l'en-tête")
            print("         « localtonet-skip-warning » : la page est la SEULE cause.")
        print()
        print("Correction : exposer l'API avec un tunnel sans page d'avertissement")
        print("             (Cloudflare : cloudflared tunnel --url http://127.0.0.1:8000),")
        print("             ou passer Localtonet en offre payante.")
        print("             Détail : docs/api/TUNNEL-DEPLOIEMENT.md §5")
        return 2

    if verdict == "injoignable":
        print(f"ÉCHEC — la requête n'a pas abouti. Cause : {sante.erreur or 'inconnue'}")
        if "CERTIFICATE_VERIFY_FAILED" in sante.erreur:
            print("         Chaîne TLS refusée côté client : le tunnel sert un certificat")
            print("         (ou une chaîne) invalide pour ce magasin de confiance. Un")
            print("         navigateur peut passer, un serveur Node/Python non.")
        else:
            print("         Vérifier que le backend écoute et que le tunnel tourne.")
        return 4

    if verdict == "erreur_serveur":
        print(f"ÉCHEC — le backend répond {sante.statut} : initialisation en échec.")
        print(f"         Corps : {sante.corps[:300] or '(vide)'}")
        return 4

    if verdict != "ok":
        print(f"ÉCHEC — réponse inattendue ({verdict}, statut {sante.statut}).")
        print(f"         Corps : {sante.corps[:300] or '(vide)'}")
        if sante.statut == 404:
            print("         404 sur /health : le tunnel répond mais ne route pas vers le")
            print("         backend (edge orphelin d'un quick tunnel) — le recréer.")
        return 4

    print("OK — /health répond un JSON valide.")

    preflight = _preflight(base, origine)
    resultat = classer_preflight(preflight, origine)
    if resultat != "ok":
        print()
        print(f"ÉCHEC — préflight refusé ({resultat}, statut {preflight.statut}).")
        if resultat == "origine_refusee":
            print(f"         Ajouter « {origine} » à CORS_ORIGINS (backend/.env),")
            print("         puis REDÉMARRER le backend (valeur lue au démarrage).")
        elif resultat == "sans_cors":
            print("         La réponse n'annonce pas cette origine : le middleware CORS")
            print("         n'a pas vu cette requête (proxy intermédiaire ?).")
        return 3

    print(f"OK — préflight autorisé pour {origine}.")

    temoin = _preflight(base, ORIGINE_TEMOIN)
    if temoin.statut == 200:
        print()
        print("ÉCHEC — une origine inconnue est acceptée : l'allowlist est ouverte.")
        print("         Retirer tout wildcard de CORS_ORIGINS.")
        return 3

    print("OK — une origine inconnue est refusée (allowlist active).")
    print()
    print("Verdict : la connexion navigateur est correctement configurée.")
    return 0


def main(argv: list[str] | None = None) -> int:
    analyseur = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    analyseur.add_argument("--api", required=True, help="URL publique de l'API (…/api/v1).")
    analyseur.add_argument(
        "--origine",
        required=True,
        help="Origine de la page frontend, ex. https://carso-ai.onrender.com.",
    )
    arguments = analyseur.parse_args(argv)
    return diagnostiquer(arguments.api, arguments.origine)


if __name__ == "__main__":
    sys.exit(main())
