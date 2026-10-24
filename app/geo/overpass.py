"""Tags OSM des voies d'un itinéraire — API Overpass.

OpenRouteService ne rend que l'identifiant des voies traversées (``osmium``).
Le détail (``smoothness``, ``tracktype``, ``access``…) vit dans OpenStreetMap :
ce client interroge Overpass **par lots d'identifiants**, jamais voie par voie,
et rend un dictionnaire ``osmid → tags``.

Un échec Overpass n'est **pas** une erreur d'itinéraire : l'appelant continue
avec des tronçons de qualité inconnue. Le service reste sync, borné, sans clé.
"""

from __future__ import annotations

import logging
import math
import time
from collections.abc import Iterable, Sequence

import httpx

from app.core.errors import ServiceExterneError

logger = logging.getLogger(__name__)

#: Identifiants par requête : assez pour amortir l'appel, assez court pour
#: rester sous les limites publiques d'Overpass.
TAILLE_LOT = 50
#: Plafond de sécurité : un trajet de 900 km traverse bien moins de voies.
MAX_VOIES = 400


def distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distance haversine entre deux points WGS84, en mètres."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(
        dlambda / 2
    ) ** 2
    return 2 * 6_371_000 * math.asin(min(1.0, math.sqrt(a)))


def interpreter_reponse(donnees: dict) -> dict[int, dict[str, str]]:
    """Projette une réponse Overpass en ``{osmid: tags}`` (testable hors réseau)."""
    elements = donnees.get("elements") if isinstance(donnees, dict) else None
    if not isinstance(elements, list):
        raise ServiceExterneError(
            "Réponse Overpass illisible", details={"fournisseur": "overpass"}
        )
    voies: dict[int, dict[str, str]] = {}
    for element in elements:
        if not isinstance(element, dict) or element.get("type") != "way":
            continue
        identifiant = element.get("id")
        tags = element.get("tags")
        if not isinstance(identifiant, int) or not isinstance(tags, dict):
            continue
        voies[identifiant] = {
            str(cle): str(valeur)
            for cle, valeur in tags.items()
            if valeur is not None
        }
    return voies


class ClientOverpass:
    """Interrogation groupée des tags de voies, sans secret."""

    def __init__(
        self,
        base_url: str,
        *,
        timeout_s: float = 45.0,
        transport: httpx.BaseTransport | None = None,
        tentatives: int = 2,
        delai_reessai_s: float = 1.5,
    ) -> None:
        self._base_url = base_url
        self._timeout = timeout_s
        self._transport = transport
        # L'instance publique sature par vagues (502 « at capacity ») : un
        # réessai court récupère la plupart des requêtes sans changer l'appelant.
        self._tentatives = max(1, tentatives)
        self._delai_reessai_s = delai_reessai_s

    def tags_voies(self, osm_ids: Iterable[int]) -> dict[int, dict[str, str]]:
        """Tags des voies demandées, par lots ; un lot en échec est ignoré.

        Un identifiant absent de la réponse (voie supprimée, hors base) reste
        simplement inconnu — ce n'est pas une erreur.
        """
        uniques: list[int] = []
        for osm_id in osm_ids:
            if isinstance(osm_id, int) and osm_id > 0 and osm_id not in uniques:
                uniques.append(osm_id)
        if not uniques:
            return {}
        voies: dict[int, dict[str, str]] = {}
        for debut in range(0, min(len(uniques), MAX_VOIES), TAILLE_LOT):
            lot = uniques[debut : debut + TAILLE_LOT]
            try:
                voies.update(self._demander(lot))
            except ServiceExterneError:
                # Un lot indisponible ne bloque pas les autres : les voies
                # manquantes seront classées « inconnu ».
                continue
        return voies

    def tags_autour(
        self,
        points: Sequence[tuple[float, float]],
        *,
        rayon_m: int = 25,
    ) -> list[dict[str, object] | None]:
        """Voie OSM la plus proche de chaque point, dans **une** requête.

        ORS n'expose pas les identifiants de voies en profil voiture : le
        rattachement se fait donc par proximité du tracé. Chaque point reçoit
        la voie (et ses tags) dont un nœud est le plus proche, dans le rayon
        demandé ; ``None`` quand aucune voie n'est assez proche.

        Une requête pour tous les points (clauses ``around``) : Overpass est un
        service public, on ne fait pas soixante appels pour un trajet.
        """
        if not points:
            return []
        clauses = "".join(
            f"way(around:{int(rayon_m)},{lat:.6f},{lon:.6f})[highway];"
            for lat, lon in points
        )
        requete = f"[out:json][timeout:60];({clauses});out tags geom;"
        reponse = self._poster(requete)
        if reponse.status_code >= 400:
            raise ServiceExterneError(
                "OpenStreetMap a refusé la requête de proximité",
                details={"fournisseur": "overpass", "http": reponse.status_code},
            )
        try:
            elements = reponse.json().get("elements")
        except ValueError as erreur:
            raise ServiceExterneError(
                "Réponse OpenStreetMap illisible",
                details={"fournisseur": "overpass"},
            ) from erreur
        if not isinstance(elements, list):
            raise ServiceExterneError(
                "Réponse OpenStreetMap illisible",
                details={"fournisseur": "overpass"},
            )

        meilleurs: list[tuple[float, dict[str, object]] | None] = [None] * len(points)
        for element in elements:
            if not isinstance(element, dict) or element.get("type") != "way":
                continue
            osm_id = element.get("id")
            tags = element.get("tags")
            geometrie = element.get("geometry")
            if not isinstance(osm_id, int) or not isinstance(tags, dict):
                continue
            if not isinstance(geometrie, list):
                continue
            noeuds = [
                (point["lat"], point["lon"])
                for point in geometrie
                if isinstance(point, dict)
                and isinstance(point.get("lat"), (int, float))
                and isinstance(point.get("lon"), (int, float))
            ]
            if not noeuds:
                continue
            voie = {
                "osm_id": osm_id,
                "tags": {str(cle): str(valeur) for cle, valeur in tags.items()},
            }
            for index, (lat, lon) in enumerate(points):
                distance = min(distance_m(lat, lon, nlat, nlon) for nlat, nlon in noeuds)
                if distance > rayon_m:
                    continue
                courant = meilleurs[index]
                if courant is None or distance < courant[0]:
                    meilleurs[index] = (distance, voie)
        return [meilleur[1] if meilleur else None for meilleur in meilleurs]

    def _poster(self, requete: str) -> httpx.Response:
        """POST Overpass avec réessai court en cas de saturation.

        L'instance publique renvoie parfois 502 « at capacity » ou répond
        après plusieurs dizaines de secondes : on réessaie une fois après une
        courte pause. Aucune clé n'est en jeu — rien de secret dans les logs.
        """
        erreur_derniere: httpx.HTTPError | None = None
        for tentative in range(self._tentatives):
            if tentative:
                time.sleep(self._delai_reessai_s)
            try:
                with httpx.Client(
                    timeout=self._timeout, transport=self._transport
                ) as client:
                    reponse = client.post(
                        self._base_url,
                        data={"data": requete},
                        headers={"User-Agent": "CARSO-AI/1.0 (qualite des routes)"},
                    )
            except httpx.HTTPError as erreur:
                erreur_derniere = erreur
                logger.warning(
                    "overpass: requête %d/%d en échec réseau (%s)",
                    tentative + 1,
                    self._tentatives,
                    type(erreur).__name__,
                )
                continue
            if reponse.status_code in (502, 503, 504) and tentative < self._tentatives - 1:
                logger.warning(
                    "overpass: instance saturée (HTTP %d), réessai %d/%d",
                    reponse.status_code,
                    tentative + 1,
                    self._tentatives,
                )
                continue
            return reponse
        raise ServiceExterneError(
            "OpenStreetMap indisponible : la qualité des routes n'a pas pu être lue",
            details={
                "fournisseur": "overpass",
                "cause": type(erreur_derniere).__name__ if erreur_derniere else "saturation",
            },
        )

    def _demander(self, osm_ids: Sequence[int]) -> dict[int, dict[str, str]]:
        requete = (
            "[out:json][timeout:25];way(id:"
            + ",".join(str(identifiant) for identifiant in osm_ids)
            + ");out tags;"
        )
        reponse = self._poster(requete)
        if reponse.status_code == 429:
            raise ServiceExterneError(
                "Trop de requêtes OpenStreetMap : réessayez dans quelques minutes",
                details={"fournisseur": "overpass", "http": 429},
            )
        if reponse.status_code >= 400:
            raise ServiceExterneError(
                "OpenStreetMap a refusé la requête",
                details={"fournisseur": "overpass", "http": reponse.status_code},
            )
        try:
            donnees = reponse.json()
        except ValueError as erreur:
            raise ServiceExterneError(
                "Réponse OpenStreetMap illisible",
                details={"fournisseur": "overpass"},
            ) from erreur
        return interpreter_reponse(donnees)


__all__ = [
    "MAX_VOIES",
    "TAILLE_LOT",
    "ClientOverpass",
    "distance_m",
    "interpreter_reponse",
]
