"""Recherche de lieux — API Photon (Komoot), sans clé.

Le client est **sync** : les routes FastAPI de ce projet sont sync et les
appels distants restent bornés (timeout court, une seule requête par saisie).
La clé n'existe pas ici — Photon est public ; c'est le backend qui l'interroge,
jamais le navigateur, pour garder une seule file et ne pas exposer l'adresse
d'un fournisseur interne.

Le résultat est **normalisé** : le frontend ne doit pas connaître le format
Photon. Un résultat sans coordonnées est ignoré — un lieu sans position
n'a pas de carte.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from app.core.errors import ServiceExterneError

#: Bornes de service : une recherche est une aide à la saisie, pas un export.
LIMITE_MAX = 10
REQUETE_MAX = 200


@dataclass(frozen=True, slots=True)
class LieuTrouve:
    """Un résultat de recherche, prêt à préremplir le formulaire."""

    nom: str
    adresse: str | None
    ville: str | None
    pays: str | None
    latitude: float
    longitude: float
    #: Référence OSM de la source (traçabilité : d'où vient cette position).
    osm_type: str | None
    osm_id: int | None

    @property
    def libelle(self) -> str:
        """Texte affiché dans la liste des résultats."""
        parties = [self.nom]
        details = [partie for partie in (self.adresse, self.ville, self.pays) if partie]
        if details:
            parties.append(", ".join(details))
        return " — ".join(parties)


def _texte(valeur: object) -> str | None:
    if not isinstance(valeur, str) or not valeur.strip():
        return None
    return valeur.strip()


def _construire_adresse(proprietes: dict) -> str | None:
    """Adresse lisible sans ``name`` : rue + numéro (ordre Photon)."""
    parties = []
    numero = _texte(proprietes.get("housenumber"))
    rue = _texte(proprietes.get("street"))
    if rue:
        parties.append(f"{numero or ''} {rue}".strip())
    code = _texte(proprietes.get("postcode"))
    quartier = _texte(proprietes.get("district")) or _texte(proprietes.get("county"))
    for partie in (code, quartier):
        if partie:
            parties.append(partie)
    return ", ".join(parties) or None


def interpreter_reponse(donnees: dict) -> list[LieuTrouve]:
    """Projette une réponse Photon en résultats normalisés (testable sans réseau)."""
    features = donnees.get("features")
    if not isinstance(features, list):
        raise ServiceExterneError(
            "Réponse de recherche illisible", details={"fournisseur": "photon"}
        )
    trouves: list[LieuTrouve] = []
    for feature in features[:LIMITE_MAX]:
        if not isinstance(feature, dict):
            continue
        proprietes = feature.get("properties") or {}
        geometrie = feature.get("geometry") or {}
        coordonnees = geometrie.get("coordinates")
        if (
            not isinstance(coordonnees, list)
            or len(coordonnees) != 2
            or not all(isinstance(c, (int, float)) for c in coordonnees)
        ):
            continue  # un résultat sans position n'a pas de carte
        nom = _texte(proprietes.get("name"))
        if nom is None:
            continue
        trouves.append(
            LieuTrouve(
                nom=nom,
                adresse=_construire_adresse(proprietes),
                ville=_texte(proprietes.get("city")) or _texte(proprietes.get("state")),
                pays=_texte(proprietes.get("country")),
                latitude=float(coordonnees[1]),
                longitude=float(coordonnees[0]),
                osm_type=_texte(proprietes.get("osm_type")),
                osm_id=(
                    int(proprietes["osm_id"])
                    if isinstance(proprietes.get("osm_id"), int)
                    else None
                ),
            )
        )
    return trouves


class RechercheLieux:
    """Client Photon : une requête, un timeout, zéro secret."""

    def __init__(
        self,
        base_url: str,
        *,
        timeout_s: float = 8.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_s
        self._transport = transport

    def chercher(
        self,
        requete: str,
        *,
        limite: int = 5,
        latitude: float | None = None,
        longitude: float | None = None,
    ) -> list[LieuTrouve]:
        """Recherche un lieu par nom ou adresse.

        ``latitude``/``longitude`` orientent la recherche (Photon ``lat``/``lon``)
        : la première suggestion gagne à être la bonne, pas la première
        alphabétiquement. Une requête vide ou trop longue est refusée ici.

        Raises:
            ValidationError-like (ServiceExterneError): réseau, délai ou
                réponse illisible — le message reste lisible pour l'utilisateur.
        """
        q = requete.strip()
        if not q:
            raise ServiceExterneError("Requête de recherche vide")
        if len(q) > REQUETE_MAX:
            q = q[:REQUETE_MAX]
        parametres: dict[str, str] = {"q": q, "limit": str(max(1, min(limite, LIMITE_MAX))), "lang": "fr"}
        if latitude is not None and longitude is not None:
            parametres["lat"] = f"{latitude}"
            parametres["lon"] = f"{longitude}"
        try:
            # Un `httpx.Client` (et non `httpx.get`) : seul le premier accepte
            # un transport injecté (tests hors réseau). Passer `transport` à
            # `httpx.get` lève un TypeError — l'erreur ne se voyait qu'en vrai.
            with httpx.Client(
                timeout=self._timeout,
                transport=self._transport,
                headers={"User-Agent": "CARSO-AI/1.0 (geocodage)"},
            ) as client:
                reponse = client.get(f"{self._base_url}/", params=parametres)
        except httpx.HTTPError as erreur:
            raise ServiceExterneError(
                "Recherche de lieu indisponible : le service ne répond pas",
                details={"fournisseur": "photon", "cause": type(erreur).__name__},
            ) from erreur
        if reponse.status_code == 429:
            raise ServiceExterneError(
                "Trop de recherches pour le moment : réessayez dans quelques secondes",
                details={"fournisseur": "photon", "http": 429},
            )
        if reponse.status_code >= 400:
            raise ServiceExterneError(
                "Recherche de lieu indisponible",
                details={"fournisseur": "photon", "http": reponse.status_code},
            )
        try:
            donnees = reponse.json()
        except ValueError as erreur:
            raise ServiceExterneError(
                "Réponse de recherche illisible",
                details={"fournisseur": "photon"},
            ) from erreur
        return interpreter_reponse(donnees)


__all__ = ["LIMITE_MAX", "LieuTrouve", "REQUETE_MAX", "RechercheLieux", "interpreter_reponse"]
