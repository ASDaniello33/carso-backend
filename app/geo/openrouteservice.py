"""Itinéraires routiers — OpenRouteService hébergé par HeiGIT.

La clé vit dans ``HEIGIT_API_KEY`` (SecretStr, jamais renvoyée par l'API) ; le
frontend n'appelle jamais le fournisseur. La base est surchargeable
(``HEIGIT_BASE_URL``) : le certificat de ``api.heigit.org`` était expiré au
22/10/2026, et l'API officielle ``https://api.openrouteservice.org`` accepte la
même clé et la même forme de chemins (``/v2/directions/{profil}/geojson``).

Le profil est unique (``driving-car``) : les missions CARSO se déplacent en
véhicule léger. Étendre à d'autres profils serait un paramètre du contrat, pas
un champ libre.

Le calcul est **sync** comme le reste du projet : un calcul par clic, timeout
court, aucune file.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import httpx

from app.core.errors import ServiceExterneError

_QUOTA_HTTP = {402: "Quota épuisé", 403: "Clé refusée", 429: "Trop de requêtes"}


@dataclass(frozen=True, slots=True)
class ItineraireCalcule:
    """Résultat brut d'un appel ORS : ce que le service mettra en cache."""

    distance_m: int
    duree_s: int
    #: Tracé [longitude, latitude] — ordre GeoJSON (et ordre Leaflet attendu).
    coordonnees: list[list[float]]
    #: Extras ORS (``osmium``, ``surface``, ``waytype``) : chaque clé porte des
    #: intervalles ``[index_debut, index_fin, valeur]`` sur la géométrie.
    extras: dict[str, list[list]] = field(default_factory=dict)


def _extras_normalises(proprietes: dict) -> dict[str, list[list]]:
    """Extras ORS sous forme ``{cle: [[debut, fin, valeur], ...]}``.

    ORS peut rendre ``{"values": [...]}`` ou directement la liste : les deux
    formes sont acceptées, et une forme inattendue est ignorée (elle ne doit
    pas faire échouer un itinéraire déjà calculé).
    """
    bruts = proprietes.get("extras")
    if not isinstance(bruts, dict):
        return {}
    extras: dict[str, list[list]] = {}
    for cle, contenu in bruts.items():
        valeurs = contenu.get("values") if isinstance(contenu, dict) else contenu
        if not isinstance(valeurs, list):
            continue
        # ORS renvoie parfois ``osmId`` (camelCase) selon le profil.
        cle = "osmium" if str(cle).lower() == "osmid" else str(cle)
        intervalles = [
            valeur
            for valeur in valeurs
            if isinstance(valeur, list)
            and len(valeur) == 3
            and isinstance(valeur[0], int)
            and isinstance(valeur[1], int)
        ]
        if intervalles:
            extras[str(cle)] = intervalles
    return extras


def interpreter_reponse(donnees: dict) -> ItineraireCalcule:
    """Extrait distance, durée et tracé d'une réponse GeoJSON ORS."""
    features = donnees.get("features") if isinstance(donnees, dict) else None
    if not isinstance(features, list) or not features:
        raise ServiceExterneError(
            "Aucun itinéraire trouvé entre ces deux points",
            details={"fournisseur": "openrouteservice"},
        )
    premier = features[0]
    proprietes = premier.get("properties") or {}
    sommaire = proprietes.get("summary") or {}
    distance = sommaire.get("distance")
    duree = sommaire.get("duration")
    geometrie = (premier.get("geometry") or {}).get("coordinates")
    if not isinstance(distance, (int, float)) or not isinstance(duree, (int, float)):
        raise ServiceExterneError(
            "Réponse d'itinéraire illisible (distance ou durée absente)",
            details={"fournisseur": "openrouteservice"},
        )
    if (
        not isinstance(geometrie, list)
        or not all(
            isinstance(point, list) and len(point) == 2 for point in geometrie
        )
    ):
        raise ServiceExterneError(
            "Tracé d'itinéraire illisible",
            details={"fournisseur": "openrouteservice"},
        )
    return ItineraireCalcule(
        distance_m=int(round(float(distance))),
        duree_s=int(round(float(duree))),
        coordonnees=[[float(point[0]), float(point[1])] for point in geometrie],
        extras=_extras_normalises(proprietes),
    )


class ClientItineraires:
    """Client OpenRouteService : un trajet, un profil, une clé serveur."""

    def __init__(
        self,
        base_url: str,
        api_key: str | None,
        *,
        profil: str = "driving-car",
        timeout_s: float = 15.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = (api_key or "").strip()
        self._profil = profil
        self._timeout = timeout_s
        self._transport = transport

    def calculer(
        self,
        origine: tuple[float, float],
        destination: tuple[float, float],
    ) -> ItineraireCalcule:
        """Calcule l'itinéraire routier entre deux points WGS84.

        Args:
            origine, destination: couples ``(latitude, longitude)``.

        Raises:
            ServiceExterneError: clé absente, quota épuisé, réseau, délai ou
                réponse illisible. Le message reste actionnable.
        """
        if not self._api_key:
            raise ServiceExterneError(
                "Service d'itinéraires non configuré : renseignez HEIGIT_API_KEY",
                details={"fournisseur": "openrouteservice", "http": 401},
            )
        chemin = f"{self._base_url}/v2/directions/{self._profil}/geojson"
        charge = {
            # ORS attend [longitude, latitude] (ordre GeoJSON).
            "coordinates": [
                [origine[1], origine[0]],
                [destination[1], destination[0]],
            ],
            "instructions": False,
            # Extras ORS : ``osmid`` est officiel mais **non exposé en profil
            # voiture** (warning « not available », vérifié) ; ``surface``/
            # ``waytype``/``waycategory`` le sont et permettent une première
            # classification par portion. On demande les cinq : un extra absent
            # est simplement omis de la réponse, sans erreur.
            "extra_info": [
                "osmid",
                "surface",
                "waytype",
                "waycategory",
                "suitability",
            ],
        }
        try:
            # Comme pour Photon : `httpx.post` ne prend pas de ``transport``,
            # seul un ``Client`` en accepte un (tests hors réseau).
            with httpx.Client(
                timeout=self._timeout,
                transport=self._transport,
                headers={"Authorization": self._api_key},
            ) as client:
                reponse = client.post(chemin, json=charge)
        except httpx.HTTPError as erreur:
            raise ServiceExterneError(
                "Calcul d'itinéraire indisponible : le service ne répond pas",
                details={"fournisseur": "openrouteservice", "cause": type(erreur).__name__},
            ) from erreur
        if reponse.status_code in _QUOTA_HTTP:
            raise ServiceExterneError(
                f"{_QUOTA_HTTP[reponse.status_code]} : le calcul d'itinéraire est "
                "temporairement indisponible",
                details={"fournisseur": "openrouteservice", "http": reponse.status_code},
            )
        if reponse.status_code >= 400:
            raise ServiceExterneError(
                "Calcul d'itinéraire refusé par le service",
                details={"fournisseur": "openrouteservice", "http": reponse.status_code},
            )
        try:
            donnees = reponse.json()
        except ValueError as erreur:
            raise ServiceExterneError(
                "Réponse d'itinéraire illisible",
                details={"fournisseur": "openrouteservice"},
            ) from erreur
        return interpreter_reponse(donnees)


__all__ = [
    "ClientItineraires",
    "ItineraireCalcule",
    "interpreter_reponse",
]
