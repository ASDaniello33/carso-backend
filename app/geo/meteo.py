"""Météo, imagerie satellite et systèmes tropicaux — fournisseurs publics.

Pourquoi ici : ``app.geo`` regroupe les clients des services géographiques
(Photon, OpenRouteService, Overpass) et **normalise** leurs réponses. La météo
suit la même règle — le frontend ne connaît ni Open-Meteo, ni WMS, ni GDACS.

Trois principes non négociables, hérités des exigences CARSO :

1. **Aucune donnée inventée.** Une valeur absente reste ``None`` ; elle n'est
   jamais remplacée par zéro. Une image satellite n'est jamais une prévision de
   nébulosité, et une trajectoire n'est jamais déduite d'une image.
2. **La provenance voyage avec la donnée.** Chaque résultat porte son
   fournisseur, sa licence et les instants ``émis_le`` / ``récupéré_le`` : sans
   eux, l'interface ne peut pas dire l'âge d'une observation, et l'exigence
   « ne jamais présenter une donnée ancienne comme du temps réel » devient
   inapplicable.
3. **Un fournisseur défaillant ne casse rien.** Chaque client lève
   ``ServiceExterneError`` avec un message lisible ; c'est le service
   applicatif (``app.application.services.meteo_service``) qui décide de
   dégrader, de servir un cache encore valide ou de dire l'indisponibilité.

Sources retenues (vérifiées en direct le 29/10/2026, sans clé) :

============================  ==========================================  ===============
Fournisseur                   Usage                                       Latence
============================  ==========================================  ===============
Open-Meteo                    conditions + prévision horaire (modèle)     15 min
EUMETSAT EUMETView (WMS)      imagerie MSG Indian Ocean (15 min)          15–75 min
NASA GIBS (WMS)               réflectance vraie couleur VIIRS (jour)      ≈ 1 jour
GDACS (JRC/Commission UE)     cyclones tropicaux officiels + vents        ~ minutes
============================  ==========================================  ===============

Licences et limites d'usage : elles sont **exposées** (``FOURNISSEURS``) pour
que l'écran les affiche, et documentées dans
``docs/design/METEO_ET_RISQUES.md``. En résumé : Open-Meteo est gratuit pour un
usage **non commercial** (l'API commerciale est payante) — le fournisseur est
donc interchangeable par configuration, jamais supposé autorisé en production.
EUMETSAT et NASA GIBS publient sous licence ouverte avec attribution. GDACS est
un service public de la Commission européenne ; ses bulletins restent la seule
source officielle de trajectoire.
"""

from __future__ import annotations

import math
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Literal, Protocol

import httpx

from app.core.errors import ServiceExterneError

__all__ = [
    "COUCHES_SATELLITE",
    "ClientEumetview",
    "ClientGdacs",
    "ClientGibs",
    "ClientOpenMeteo",
    "Condition",
    "CoucheSatellite",
    "Emprise",
    "FournisseurInfo",
    "ImageSatellite",
    "MeteoPoint",
    "PolygoneVent",
    "SystemeTropical",
    "classer_code",
    "distance_km",
    "interpreter_points",
    "interpreter_polygones",
    "interpreter_systemes",
    "lire_derniere_heure",
]

#: Nombre maximal de points demandés d'un coup à Open-Meteo (un trajet = un
#: appel : 12 points couvrent un déplacement long sans multiplier les quotas).
POINTS_MAX = 12
#: Fenêtre horaire maximale d'une prévision (3 jours : au-delà, la valeur n'est
#: plus une prévision utile pour préparer un déplacement).
HEURES_MAX = 72


@dataclass(frozen=True, slots=True)
class Emprise:
    """Rectangle géographique ``[sud, ouest, nord, est]`` (degrés décimaux)."""

    sud: float
    ouest: float
    nord: float
    est: float

    @property
    def bbox_wms(self) -> str:
        """``minx,miny,maxx,maxy`` — ordre lon/lat attendu par ``CRS:84``."""
        return f"{self.ouest},{self.sud},{self.est},{self.nord}"

    def contient(self, latitude: float, longitude: float, marge_deg: float = 0.0) -> bool:
        return (
            self.sud - marge_deg <= latitude <= self.nord + marge_deg
            and self.ouest - marge_deg <= longitude <= self.est + marge_deg
        )


@dataclass(frozen=True, slots=True)
class FournisseurInfo:
    """Provenance et conditions d'usage d'une source (affichées à l'écran)."""

    cle: str
    nom: str
    url: str
    licence: str
    #: ``non_commercial`` : gratuit pour un usage non commercial seulement.
    usage: Literal["libre", "non_commercial", "service_public", "a_verifier"]
    remarque: str = ""


FOURNISSEUR_OPEN_METEO = FournisseurInfo(
    cle="open-meteo",
    nom="Open-Meteo",
    url="https://open-meteo.com/",
    licence="Données sous licence CC BY 4.0 — attribution obligatoire",
    usage="non_commercial",
    remarque=(
        "API gratuite réservée à un usage non commercial ; un usage commercial "
        "de CARSO exige l'offre payante du fournisseur. Remplaçable par "
        "configuration (METEO_FOURNISSEUR) sans toucher à la carte."
    ),
)

FOURNISSEUR_EUMETSAT = FournisseurInfo(
    cle="eumetsat",
    nom="EUMETSAT — EUMETView",
    url="https://view.eumetsat.int/",
    licence="Données EUMETSAT, réutilisation libre avec attribution",
    usage="libre",
    remarque="Imagerie MSG Indian Ocean : pas de 15 minutes, pas de clé.",
)

FOURNISSEUR_GIBS = FournisseurInfo(
    cle="nasa-gibs",
    nom="NASA GIBS",
    url="https://nasa-gibs.github.io/gibs-api-docs/",
    licence="Données NASA EOSDIS, domaine public",
    usage="libre",
    remarque=(
        "Réflectance vraie couleur journalière : couverture utile le jour, "
        "image du jour précédent — jamais du temps réel."
    ),
)

FOURNISSEUR_GDACS = FournisseurInfo(
    cle="gdacs",
    nom="GDACS (Commission européenne / JRC)",
    url="https://www.gdacs.org/",
    licence="Service public, réutilisation avec attribution",
    usage="service_public",
    remarque=(
        "Systèmes tropicaux officiels et zones de vent prévues. Le service est "
        "parfois lent ou indisponible : l'écran le signale et renvoie au bulletin."
    ),
)

#: Catalogue des fournisseurs exposé à l'interface (licences et limites).
FOURNISSEURS: tuple[FournisseurInfo, ...] = (
    FOURNISSEUR_OPEN_METEO,
    FOURNISSEUR_EUMETSAT,
    FOURNISSEUR_GIBS,
    FOURNISSEUR_GDACS,
)


def distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distance orthodromique (km) — suffisante pour « à quelle distance ? »."""
    rayon = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = p2 - p1
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * rayon * math.asin(min(1.0, math.sqrt(a)))


# ---------------------------------------------------------------------------
# Codes WMO — libellés et catégories (une seule table, côté serveur)
# ---------------------------------------------------------------------------

#: Catégories d'affichage : l'interface choisit une icône par catégorie, jamais
#: par code, pour ne pas dupliquer cette table en TypeScript.
_WMO: dict[int, tuple[str, str]] = {
    0: ("ciel_clair", "Ciel dégagé"),
    1: ("peu_nuageux", "Peu nuageux"),
    2: ("nuageux", "Partiellement nuageux"),
    3: ("couvert", "Couvert"),
    45: ("brouillard", "Brouillard"),
    48: ("brouillard", "Brouillard givrant"),
    51: ("bruine", "Bruine faible"),
    53: ("bruine", "Bruine"),
    55: ("bruine", "Bruine forte"),
    56: ("bruine", "Bruine verglaçante"),
    57: ("bruine", "Bruine verglaçante forte"),
    61: ("pluie", "Pluie faible"),
    63: ("pluie", "Pluie"),
    65: ("pluie", "Pluie forte"),
    66: ("pluie", "Pluie verglaçante"),
    67: ("pluie", "Pluie verglaçante forte"),
    71: ("neige", "Neige faible"),
    73: ("neige", "Neige"),
    75: ("neige", "Neige forte"),
    77: ("neige", "Grains de neige"),
    80: ("averse", "Averses faibles"),
    81: ("averse", "Averses"),
    82: ("averse", "Averses violentes"),
    85: ("neige", "Averses de neige"),
    86: ("neige", "Averses de neige fortes"),
    95: ("orage", "Orage"),
    96: ("orage", "Orage avec grêle"),
    99: ("orage", "Orage violent avec grêle"),
}


def classer_code(code: int | None) -> tuple[str, str]:
    """``(catégorie, libellé)`` d'un code WMO ; inconnu reste inconnu."""
    if code is None:
        return ("inconnu", "Conditions non fournies")
    return _WMO.get(code, ("inconnu", f"Condition météo {code} (code WMO)"))


# ---------------------------------------------------------------------------
# Conditions et prévisions (Open-Meteo)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Condition:
    """État de l'atmosphère à un instant précis (UTC)."""

    heure: datetime
    temperature_c: float | None = None
    ressenti_c: float | None = None
    humidite_pct: int | None = None
    vent_kmh: float | None = None
    rafales_kmh: float | None = None
    direction_deg: int | None = None
    pluie_mm: float | None = None
    probabilite_pluie_pct: int | None = None
    couverture_pct: int | None = None
    code: int | None = None

    @property
    def categorie(self) -> str:
        return classer_code(self.code)[0]

    @property
    def libelle(self) -> str:
        return classer_code(self.code)[1]


@dataclass(frozen=True, slots=True)
class MeteoPoint:
    """Conditions et prévision d'un point, avec sa provenance.

    ``nature_actuelle`` distingue explicitement ce que l'on affiche en tête :
    Open-Meteo restitue une **analyse modélisée** (``estimation``) actualisée
    toutes les 15 minutes, pas une mesure de station. L'interface le dit.
    """

    latitude: float
    longitude: float
    fuseau: str
    altitude_m: float | None
    actuel: Condition | None
    horaires: tuple[Condition, ...]
    emis_le: datetime | None
    recupere_le: datetime
    fournisseur: FournisseurInfo
    nature_actuelle: Literal["estimation", "observation"] = "estimation"


def _nombre(valeur: object) -> float | None:
    if isinstance(valeur, bool) or not isinstance(valeur, (int, float)):
        return None
    return float(valeur)


def _entier(valeur: object) -> int | None:
    if isinstance(valeur, bool) or not isinstance(valeur, (int, float)):
        return None
    return int(round(valeur))


def _colonne(series: dict[str, object], cle: str, index: int) -> object:
    """Valeur d'une colonne horaire, ou ``None`` si la série est plus courte."""
    valeurs = series.get(cle)
    if isinstance(valeurs, list) and index < len(valeurs):
        return valeurs[index]
    return None


def _instant(valeur: object, decalage: timedelta | None = None) -> datetime | None:
    """Horodatage ISO ⇒ instant UTC.

    Sans fuseau explicite, ``decalage`` dit de combien le fournisseur est en
    avance sur UTC. C'est le cas d'Open-Meteo quand on lui demande une
    **journée locale** : il rend des heures locales naïves et son
    ``utc_offset_seconds``. Omis, l'instant est lu comme UTC.
    """
    if not isinstance(valeur, str) or not valeur.strip():
        return None
    texte = valeur.strip().replace("Z", "+00:00")
    try:
        instant = datetime.fromisoformat(texte)
    except ValueError:
        return None
    if instant.tzinfo:
        return instant
    return instant.replace(tzinfo=UTC) - (decalage or timedelta(0))


def _decalage(bloc: dict[str, object]) -> timedelta:
    """Décalage du bloc par rapport à UTC (``0`` quand le fournisseur le tait)."""
    secondes = _nombre(bloc.get("utc_offset_seconds"))
    if secondes is None:
        return timedelta(0)
    return timedelta(seconds=secondes)


def interpreter_points(
    donnees: object,
    *,
    fournisseur: FournisseurInfo = FOURNISSEUR_OPEN_METEO,
    recupere_le: datetime | None = None,
) -> list[MeteoPoint]:
    """Projette une réponse Open-Meteo (1 ou N lieux) en points normalisés.

    Fonction **pure** : les tests l'exercent sans réseau. Un lieu dont le bloc
    horaire est illisible est ignoré — mieux vaut un point en moins qu'un point
    inventé.
    """
    if isinstance(donnees, list):
        blocs = donnees
    elif isinstance(donnees, dict):
        blocs = [donnees]
    else:
        raise ServiceExterneError(
            "Réponse météo illisible", details={"fournisseur": fournisseur.cle}
        )
    recu = recupere_le or datetime.now(UTC)
    points: list[MeteoPoint] = []
    for bloc in blocs:
        if not isinstance(bloc, dict):
            continue
        latitude = _nombre(bloc.get("latitude"))
        longitude = _nombre(bloc.get("longitude"))
        if latitude is None or longitude is None:
            continue
        horaire = bloc.get("hourly") or {}
        temps = horaire.get("time") if isinstance(horaire, dict) else None
        if not isinstance(temps, list):
            continue
        series = {cle: horaire.get(cle) for cle in horaire if cle != "time"}
        # Une journée demandée localement revient en heures locales : le
        # décalage du bloc les ramène en UTC, seul format stocké et affiché.
        decalage = _decalage(bloc)
        horaires: list[Condition] = []
        for index, marque in enumerate(temps):
            heure = _instant(marque, decalage)
            if heure is None:
                continue
            horaires.append(
                Condition(
                    heure=heure,
                    temperature_c=_nombre(_colonne(series, "temperature_2m", index)),
                    ressenti_c=_nombre(_colonne(series, "apparent_temperature", index)),
                    humidite_pct=_entier(_colonne(series, "relative_humidity_2m", index)),
                    vent_kmh=_nombre(_colonne(series, "wind_speed_10m", index)),
                    rafales_kmh=_nombre(_colonne(series, "wind_gusts_10m", index)),
                    direction_deg=_entier(_colonne(series, "wind_direction_10m", index)),
                    pluie_mm=_nombre(_colonne(series, "precipitation", index)),
                    probabilite_pluie_pct=_entier(
                        _colonne(series, "precipitation_probability", index)
                    ),
                    couverture_pct=_entier(_colonne(series, "cloud_cover", index)),
                    code=_entier(_colonne(series, "weather_code", index)),
                )
            )
        actuel = None
        courant = bloc.get("current")
        if isinstance(courant, dict):
            heure = _instant(courant.get("time"), decalage)
            if heure is not None:
                actuel = Condition(
                    heure=heure,
                    temperature_c=_nombre(courant.get("temperature_2m")),
                    ressenti_c=_nombre(courant.get("apparent_temperature")),
                    humidite_pct=_entier(courant.get("relative_humidity_2m")),
                    vent_kmh=_nombre(courant.get("wind_speed_10m")),
                    rafales_kmh=_nombre(courant.get("wind_gusts_10m")),
                    direction_deg=_entier(courant.get("wind_direction_10m")),
                    pluie_mm=_nombre(courant.get("precipitation")),
                    probabilite_pluie_pct=_entier(
                        courant.get("precipitation_probability")
                    ),
                    couverture_pct=_entier(courant.get("cloud_cover")),
                    code=_entier(courant.get("weather_code")),
                )
        points.append(
            MeteoPoint(
                latitude=latitude,
                longitude=longitude,
                fuseau=str(bloc.get("timezone") or "GMT"),
                altitude_m=_nombre(bloc.get("elevation")),
                actuel=actuel,
                horaires=tuple(horaires),
                emis_le=actuel.heure if actuel else (horaires[0].heure if horaires else None),
                recupere_le=recu,
                fournisseur=fournisseur,
            )
        )
    if not points:
        raise ServiceExterneError(
            "Réponse météo illisible", details={"fournisseur": fournisseur.cle}
        )
    return points


class FournisseurMeteo(Protocol):
    """Contrat d'un fournisseur de conditions — point d'extension documenté."""

    info: FournisseurInfo

    def points(
        self,
        coordonnees: Sequence[tuple[float, float]],
        *,
        heures: int = 24,
        jour: date | None = None,
    ) -> list[MeteoPoint]: ...


class ClientOpenMeteo:
    """Client Open-Meteo : un appel pour N points, une seule forme normalisée.

    L'API accepte ``latitude=a,b,c`` : **un** appel couvre tout un trajet, ce qui
    protège le quota gratuit (10 000 appels/jour) plutôt qu'une rafale par point.
    """

    info = FOURNISSEUR_OPEN_METEO

    _CURRENT = (
        "temperature_2m,apparent_temperature,relative_humidity_2m,wind_speed_10m,"
        "wind_direction_10m,wind_gusts_10m,precipitation,weather_code,cloud_cover"
    )
    _HOURLY = (
        "temperature_2m,apparent_temperature,relative_humidity_2m,wind_speed_10m,"
        "wind_direction_10m,wind_gusts_10m,precipitation,precipitation_probability,"
        "weather_code,cloud_cover"
    )

    def __init__(
        self,
        base_url: str,
        *,
        timeout_s: float = 10.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_s
        self._transport = transport

    def points(
        self,
        coordonnees: Sequence[tuple[float, float]],
        *,
        heures: int = 24,
        jour: date | None = None,
    ) -> list[MeteoPoint]:
        """Conditions actuelles + prévision horaire pour chaque coordonnée.

        ``jour`` demande la prévision d'une **journée** (00–23 h, fuseau du
        fournisseur) au lieu de la fenêtre qui commence maintenant : c'est ce
        qu'il faut pour un trajet dont on ne connaît que la date. Le bloc
        ``current`` est alors omis — « maintenant » ne décrit pas une journée à
        venir, et l'afficher comme telle mentirait.

        Raises:
            ServiceExterneError: réseau, délai, quota ou réponse illisible.
        """
        if not coordonnees:
            raise ServiceExterneError("Aucun point demandé")
        if len(coordonnees) > POINTS_MAX:
            raise ServiceExterneError(
                f"Trop de points demandés ({len(coordonnees)} > {POINTS_MAX})",
                details={"maximum": POINTS_MAX},
            )
        latitudes = ",".join(f"{latitude:.4f}" for latitude, _ in coordonnees)
        longitudes = ",".join(f"{longitude:.4f}" for _, longitude in coordonnees)
        parametres = {
            "latitude": latitudes,
            "longitude": longitudes,
            "hourly": self._HOURLY,
            "timezone": "GMT",
        }
        if jour is None:
            parametres["current"] = self._CURRENT
            # ``forecast_hours`` part de **maintenant** : la fenêtre demandée
            # couvre le départ le plus tardif sans déborder sur la veille.
            parametres["forecast_hours"] = str(max(1, min(heures, HEURES_MAX)))
        else:
            # Journée **locale** : sans cela, la « journée » demandée serait
            # découpée en GMT et l'utilisateur verrait 03:00 → 02:00 chez lui.
            # Le fournisseur rend alors ``utc_offset_seconds``, que
            # ``interpreter_points`` retranche pour revenir en UTC.
            parametres["timezone"] = "auto"
            parametres["start_date"] = jour.isoformat()
            parametres["end_date"] = jour.isoformat()
        try:
            with httpx.Client(
                timeout=self._timeout,
                transport=self._transport,
                headers={"User-Agent": "CARSO-AI/1.0 (meteo)"},
            ) as client:
                reponse = client.get(f"{self._base_url}/forecast", params=parametres)
        except httpx.HTTPError as erreur:
            raise ServiceExterneError(
                "Météo indisponible : le service ne répond pas",
                details={"fournisseur": self.info.cle, "cause": type(erreur).__name__},
            ) from erreur
        if reponse.status_code == 429:
            raise ServiceExterneError(
                "Trop de demandes météo pour le moment : réessayez dans un instant",
                details={"fournisseur": self.info.cle, "http": 429},
            )
        if reponse.status_code >= 400:
            raise ServiceExterneError(
                "Météo indisponible",
                details={"fournisseur": self.info.cle, "http": reponse.status_code},
            )
        try:
            donnees = reponse.json()
        except ValueError as erreur:
            raise ServiceExterneError(
                "Réponse météo illisible", details={"fournisseur": self.info.cle}
            ) from erreur
        return interpreter_points(donnees)


# ---------------------------------------------------------------------------
# Imagerie satellite (EUMETSAT EUMETView, NASA GIBS)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CoucheSatellite:
    """Une couche d'imagerie réelle — jamais une prévision déguisée."""

    id: str
    wms: str
    libelle: str
    description: str
    famille: Literal["couleurs", "infrarouge", "nuages", "convection", "precipitation"]
    #: Cadence réellement publiée par le fournisseur (minutes).
    pas_minutes: int
    #: Faux pour une image visible : inutilisable la nuit.
    visible_de_nuit: bool
    fournisseur: FournisseurInfo
    #: Vrai pour une image journalière (NASA GIBS) : pas du temps réel.
    journaliere: bool = False


#: Couches **vérifiées** dans le GetCapabilities du 29/10/2026 (MSG Indian
#: Ocean : couverture Madagascar et océan Indien). Un nom absent du service
#: n'est jamais proposé par l'API `/meteo/satellite/couches` (voir
#: ``lire_derniere_heure``).
COUCHES_SATELLITE: tuple[CoucheSatellite, ...] = (
    CoucheSatellite(
        id="couleurs_naturelles",
        wms="msg_iodc:rgb_naturalenhncd",
        libelle="Couleurs naturelles (jour)",
        description=(
            "Composite RGB proche de la vision humaine. N'utilisable que de jour : "
            "la nuit, la couche apparaît noire."
        ),
        famille="couleurs",
        pas_minutes=15,
        visible_de_nuit=False,
        fournisseur=FOURNISSEUR_EUMETSAT,
    ),
    CoucheSatellite(
        id="infrarouge",
        wms="msg_iodc:ir108",
        libelle="Infrarouge 10,8 µm (jour et nuit)",
        description=(
            "Température de brillance : les sommets froids (nuages hauts, systèmes "
            "convectifs) ressortent en blanc. Seule couche exploitable la nuit."
        ),
        famille="infrarouge",
        pas_minutes=15,
        visible_de_nuit=True,
        fournisseur=FOURNISSEUR_EUMETSAT,
    ),
    CoucheSatellite(
        id="convection",
        wms="msg_iodc:rgb_convection",
        libelle="Convection (nuages d'orage)",
        description=(
            "Composite conçu pour distinguer les nuages convectifs profonds et la "
            "taille des cristaux de glace — utile pour suivre une perturbation."
        ),
        famille="convection",
        pas_minutes=15,
        visible_de_nuit=True,
        fournisseur=FOURNISSEUR_EUMETSAT,
    ),
    CoucheSatellite(
        id="nuages_hauts",
        wms="msg_iodc:cth",
        libelle="Hauteur des sommets nuageux",
        description="Altitude estimée du sommet des nuages (jour et nuit).",
        famille="nuages",
        pas_minutes=15,
        visible_de_nuit=True,
        fournisseur=FOURNISSEUR_EUMETSAT,
    ),
    CoucheSatellite(
        id="masque_nuages",
        wms="msg_iodc:clm",
        libelle="Masque nuageux",
        description="Détection de la couverture nuageuse : nuage, sol, ou indéterminé.",
        famille="nuages",
        pas_minutes=15,
        visible_de_nuit=True,
        fournisseur=FOURNISSEUR_EUMETSAT,
    ),
    CoucheSatellite(
        id="precipitation",
        wms="msg_iodc:h63",
        libelle="Intensité de précipitation",
        description=(
            "Taux de précipitation au sol estimé à partir de l'infrarouge géostationnaire."
        ),
        famille="precipitation",
        pas_minutes=15,
        visible_de_nuit=True,
        fournisseur=FOURNISSEUR_EUMETSAT,
    ),
    CoucheSatellite(
        id="reflectance_journaliere",
        wms="VIIRS_SNPP_CorrectedReflectance_TrueColor",
        libelle="Réflectance vraie couleur (NASA GIBS, journalière)",
        description=(
            "Image journalière du satellite VIIRS : résolution fine, mais il s'agit "
            "de la journée précédente — ce n'est pas du temps réel."
        ),
        famille="couleurs",
        pas_minutes=24 * 60,
        visible_de_nuit=False,
        fournisseur=FOURNISSEUR_GIBS,
        journaliere=True,
    ),
)

_COUCHES_PAR_ID = {couche.id: couche for couche in COUCHES_SATELLITE}


def couche_par_id(identifiant: str) -> CoucheSatellite:
    """Couche du catalogue, sinon refus explicite (jamais un repli silencieux)."""
    couche = _COUCHES_PAR_ID.get(identifiant)
    if couche is None:
        raise ServiceExterneError(
            f"Couche satellite inconnue : {identifiant}",
            details={"couches": sorted(_COUCHES_PAR_ID)},
        )
    return couche


@dataclass(frozen=True, slots=True)
class ImageSatellite:
    """Image réellement obtenue, avec l'instant **de l'observation**."""

    couche: CoucheSatellite
    heure: datetime
    contenu: bytes
    mime: str
    emprise: Emprise


_DIMENSION = re.compile(
    r'<Dimension\s+name="time"[^>]*?default="([^"]+)"[^>]*>(.*?)</Dimension>', re.S
)


def lire_derniere_heure(xml: str, couche_wms: str) -> datetime | None:
    """Dernier créneau publié pour ``couche_wms``, lu dans le GetCapabilities.

    On ne devine pas une heure : elle vient du document du service. Absente,
    la fonction rend ``None`` et l'image n'est pas proposée.
    """
    index = xml.find(f"<Name>{couche_wms}</Name>")
    if index < 0:
        return None
    fenetre = xml[index : index + 4000]
    trouve = _DIMENSION.search(fenetre)
    if not trouve:
        return None
    return _instant(trouve.group(1))


def couches_publiees(xml: str) -> dict[str, datetime]:
    """``{id de couche: dernier créneau}`` pour les couches du catalogue."""
    publiees: dict[str, datetime] = {}
    for couche in COUCHES_SATELLITE:
        if couche.fournisseur.cle != FOURNISSEUR_EUMETSAT.cle:
            continue
        heure = lire_derniere_heure(xml, couche.wms)
        if heure is not None:
            publiees[couche.id] = heure
    return publiees


class ClientEumetview:
    """GetCapabilities + GetMap sur EUMETView (WMS 1.3.0, ``CRS:84``).

    Une image est demandée **pour une emprise et un créneau**, pas en tuiles :
    le service met 3 à 40 s à rendre un GetMap, une carte Leaflet tuilée
    déclencherait vingt appels simultanés. L'image rendue est mise en cache par
    le service applicatif, avec l'instant qu'elle représente.
    """

    info = FOURNISSEUR_EUMETSAT

    def __init__(
        self,
        base_url: str,
        *,
        timeout_s: float = 45.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_s
        self._transport = transport

    def capacites(self) -> str:
        """Document GetCapabilities brut (mis en cache par l'appelant)."""
        try:
            with httpx.Client(
                timeout=self._timeout,
                transport=self._transport,
                headers={"User-Agent": "CARSO-AI/1.0 (satellite)"},
            ) as client:
                reponse = client.get(
                    self._base_url,
                    params={"service": "WMS", "version": "1.3.0", "request": "GetCapabilities"},
                )
        except httpx.HTTPError as erreur:
            raise ServiceExterneError(
                "Imagerie satellite indisponible : le service ne répond pas",
                details={"fournisseur": self.info.cle, "cause": type(erreur).__name__},
            ) from erreur
        if reponse.status_code >= 400:
            raise ServiceExterneError(
                "Imagerie satellite indisponible",
                details={"fournisseur": self.info.cle, "http": reponse.status_code},
            )
        return reponse.text

    def image(
        self,
        couche: CoucheSatellite,
        heure: datetime,
        emprise: Emprise,
        *,
        taille: int = 1024,
    ) -> ImageSatellite:
        """GetMap pour ``couche`` à ``heure`` — l'instant demandé fait foi."""
        parametres = {
            "service": "WMS",
            "version": "1.3.0",
            "request": "GetMap",
            "layers": couche.wms,
            "styles": "",
            "crs": "CRS:84",
            "bbox": emprise.bbox_wms,
            "width": str(taille),
            "height": str(taille),
            "format": "image/png",
            "transparent": "true",
            # WMS 1.3 attend un ISO 8601 en UTC ; le service ignore les
            # créneaux non publiés (il répond alors une exception XML).
            "time": heure.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        try:
            with httpx.Client(
                timeout=self._timeout,
                transport=self._transport,
                headers={"User-Agent": "CARSO-AI/1.0 (satellite)"},
            ) as client:
                reponse = client.get(self._base_url, params=parametres)
        except httpx.HTTPError as erreur:
            raise ServiceExterneError(
                "Image satellite indisponible : le service ne répond pas",
                details={"fournisseur": self.info.cle, "cause": type(erreur).__name__},
            ) from erreur
        mime = reponse.headers.get("content-type", "")
        if reponse.status_code >= 400 or not mime.startswith("image/"):
            # GeoServer répond une exception XML en 200/400 : sans ce contrôle,
            # le navigateur afficherait un cadre cassé au lieu d'un message.
            raise ServiceExterneError(
                "Aucune image publiée pour ce créneau",
                details={
                    "fournisseur": self.info.cle,
                    "couche": couche.id,
                    "heure": parametres["time"],
                    "http": reponse.status_code,
                },
            )
        return ImageSatellite(
            couche=couche,
            heure=heure.astimezone(UTC),
            contenu=reponse.content,
            mime=mime.split(";")[0],
            emprise=emprise,
        )


class ClientGibs:
    """GetMap sur NASA GIBS (WMS 1.1.1, ``EPSG:4326``).

    Le service est rapide et sans clé, mais son GetCapabilities pèse 2,2 Mo et
    met une trentaine de secondes : on n'interroge que la couche **vérifiée**
    du catalogue, jamais une liste dynamique à chaque requête.
    """

    info = FOURNISSEUR_GIBS

    def __init__(
        self,
        base_url: str,
        *,
        timeout_s: float = 20.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_s
        self._transport = transport

    def image(
        self, couche: CoucheSatellite, jour: date, emprise: Emprise, *, taille: int = 1024
    ) -> ImageSatellite:
        parametres = {
            "SERVICE": "WMS",
            "REQUEST": "GetMap",
            "VERSION": "1.1.1",
            "LAYERS": couche.wms,
            "STYLES": "",
            "FORMAT": "image/jpeg",
            "SRS": "EPSG:4326",
            # WMS 1.1.1 : ordre lon/lat pour EPSG:4326.
            "BBOX": emprise.bbox_wms,
            "WIDTH": str(taille),
            "HEIGHT": str(taille),
            "TIME": jour.strftime("%Y-%m-%d"),
        }
        try:
            with httpx.Client(
                timeout=self._timeout,
                transport=self._transport,
                headers={"User-Agent": "CARSO-AI/1.0 (satellite)"},
            ) as client:
                reponse = client.get(self._base_url, params=parametres)
        except httpx.HTTPError as erreur:
            raise ServiceExterneError(
                "Image GIBS indisponible : le service ne répond pas",
                details={"fournisseur": self.info.cle, "cause": type(erreur).__name__},
            ) from erreur
        mime = reponse.headers.get("content-type", "")
        if reponse.status_code >= 400 or not mime.startswith("image/"):
            raise ServiceExterneError(
                "Aucune image GIBS pour ce jour",
                details={"fournisseur": self.info.cle, "jour": jour.isoformat(),
                         "http": reponse.status_code},
            )
        return ImageSatellite(
            couche=couche,
            heure=datetime.combine(jour, datetime.min.time(), tzinfo=UTC),
            contenu=reponse.content,
            mime=mime.split(";")[0],
            emprise=emprise,
        )


# ---------------------------------------------------------------------------
# Systèmes tropicaux (GDACS)
# ---------------------------------------------------------------------------

_NIVEAUX = {"red": "Rouge", "orange": "Orange", "green": "Vert"}


@dataclass(frozen=True, slots=True)
class PolygoneVent:
    """Zone de vent d'un système, telle que GDACS la publie."""

    #: Niveau d'alerte du polygone (``Rouge``/``Orange``/``Vert``).
    niveau: str
    #: Libellé brut du fournisseur (« 120 km/h », « 28/10 06:00 », « Centroid »).
    libelle: str
    #: Instant prévu quand GDACS le publie ; ``None`` pour une zone actuelle.
    prevu_le: datetime | None
    #: Anneau extérieur en ``(latitude, longitude)``.
    positions: tuple[tuple[float, float], ...]


@dataclass(frozen=True, slots=True)
class SystemeTropical:
    """Système identifié par un centre officiel — jamais déduit d'une image."""

    identifiant: str
    nom: str
    episode: int
    niveau: str
    intensite: str | None
    intensite_kmh: float | None
    position: tuple[float, float] | None
    du: datetime | None
    au: datetime | None
    publie_le: datetime | None
    source: str
    url_bulletin: str
    pays: str | None
    actuel: bool
    polygones: tuple[PolygoneVent, ...] = ()
    fournisseur: FournisseurInfo = FOURNISSEUR_GDACS

    @property
    def age_heures(self) -> float | None:
        if self.publie_le is None:
            return None
        ecart = datetime.now(UTC) - self.publie_le
        return round(ecart.total_seconds() / 3600, 1)


def _niveau(depuis: object) -> str:
    return _NIVEAUX.get(str(depuis or "").strip().lower(), "Inconnu")


_DATE_GDACS = re.compile(r"(\d{2})/(\d{2})\s+(\d{1,2}):(\d{2})")


def interpreter_systemes(
    donnees: object, *, emprise: Emprise, marge_deg: float = 0.0
) -> list[SystemeTropical]:
    """Systèmes tropicaux de la zone, projetés depuis la réponse GDACS.

    Les valeurs absentes restent ``None`` : un vent inconnu ne devient pas zéro.
    """
    if not isinstance(donnees, dict):
        raise ServiceExterneError("Réponse GDACS illisible", details={"fournisseur": "gdacs"})
    features = donnees.get("features")
    if not isinstance(features, list):
        raise ServiceExterneError("Réponse GDACS illisible", details={"fournisseur": "gdacs"})
    systemes: list[SystemeTropical] = []
    for feature in features:
        if not isinstance(feature, dict):
            continue
        proprietes = feature.get("properties") or {}
        geometrie = feature.get("geometry") or {}
        coordonnees = geometrie.get("coordinates")
        position = None
        if (
            geometrie.get("type") == "Point"
            and isinstance(coordonnees, list)
            and len(coordonnees) == 2
            and all(isinstance(valeur, (int, float)) for valeur in coordonnees)
        ):
            position = (float(coordonnees[1]), float(coordonnees[0]))
        if position is not None and not emprise.contient(*position, marge_deg=marge_deg):
            continue
        urls = proprietes.get("url") or {}
        gravite = proprietes.get("severitydata") or {}
        systemes.append(
            SystemeTropical(
                identifiant=str(proprietes.get("eventid") or ""),
                nom=str(proprietes.get("eventname") or "Système tropical"),
                episode=int(proprietes.get("episodeid") or 0),
                niveau=_niveau(proprietes.get("alertlevel")),
                intensite=(
                    str(gravite["severitytext"]) if gravite.get("severitytext") else None
                ),
                intensite_kmh=_nombre(gravite.get("severity")),
                position=position,
                du=_instant(proprietes.get("fromdate")),
                au=_instant(proprietes.get("todate")),
                publie_le=_instant(proprietes.get("datemodified")),
                source=str(proprietes.get("source") or "GDACS"),
                url_bulletin=str(urls.get("report") or "https://www.gdacs.org/"),
                pays=str(proprietes.get("country") or "") or None,
                actuel=str(proprietes.get("iscurrent") or "").lower() == "true",
            )
        )
    return systemes


def interpreter_polygones(donnees: object, *, annee: int) -> list[PolygoneVent]:
    """Zones de vent publiées par GDACS pour un épisode.

    Deux familles coexistent dans la même réponse :

    - ``Point_Centroid`` : la position publiée du centre ;
    - ``Poly_*`` : une zone de vent, actuelle (libellé en km/h) ou **prévue**
      (libellé ``JJ/MM HH:MM``).

    Le libellé temporel arrive sans année : il n'est interprété qu'au format
    attendu, sinon ``prevu_le`` reste ``None`` — une date inventée vaudrait
    moins qu'une date absente.
    """
    if not isinstance(donnees, dict):
        raise ServiceExterneError("Réponse GDACS illisible", details={"fournisseur": "gdacs"})
    features = donnees.get("features")
    if not isinstance(features, list):
        raise ServiceExterneError("Réponse GDACS illisible", details={"fournisseur": "gdacs"})
    polygones: list[PolygoneVent] = []
    for feature in features:
        if not isinstance(feature, dict):
            continue
        proprietes = feature.get("properties") or {}
        classe = str(proprietes.get("Class") or "")
        if not classe.lower().startswith("poly"):
            continue
        geometrie = feature.get("geometry") or {}
        if geometrie.get("type") != "Polygon":
            continue
        anneaux = geometrie.get("coordinates")
        if not isinstance(anneaux, list) or not anneaux:
            continue
        anneau = anneaux[0]
        if not isinstance(anneau, list):
            continue
        positions = tuple(
            (float(point[1]), float(point[0]))
            for point in anneau
            if isinstance(point, list)
            and len(point) == 2
            and all(isinstance(valeur, (int, float)) for valeur in point)
        )
        if len(positions) < 3:
            continue
        libelle = str(proprietes.get("polygonlabel") or "")
        prevu_le = None
        trouve = _DATE_GDACS.search(libelle)
        if trouve:
            jour, mois, heure, minute = (int(valeur) for valeur in trouve.groups())
            prevu_le = _date_sans_erreur(annee, mois, jour, heure, minute)
        polygones.append(
            PolygoneVent(
                niveau=_niveau(classe.replace("Poly_", "")),
                libelle=libelle,
                prevu_le=prevu_le,
                positions=positions,
            )
        )
    return polygones


def _date_sans_erreur(annee: int, mois: int, jour: int, heure: int, minute: int) -> datetime | None:
    try:
        return datetime(annee, mois, jour, heure, minute, tzinfo=UTC)
    except ValueError:
        return None


class ClientGdacs:
    """Liste des systèmes tropicaux + géométrie d'un épisode.

    Le service est **lent et instable** (mesuré : 5 à 60 s, connexions parfois
    coupées). Chaque appel est borné et traduit en ``ServiceExterneError`` ;
    c'est l'appelant qui décide de dégrader. Aucune donnée n'est reconstituée
    à partir d'un échec.
    """

    info = FOURNISSEUR_GDACS

    def __init__(
        self,
        base_url: str,
        *,
        timeout_s: float = 60.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_s
        self._transport = transport

    def _get(self, chemin: str, *, params: dict[str, str]) -> dict:
        try:
            with httpx.Client(
                timeout=self._timeout,
                transport=self._transport,
                headers={"User-Agent": "CARSO-AI/1.0 (risques)"},
            ) as client:
                reponse = client.get(f"{self._base_url}/{chemin}", params=params)
        except httpx.HTTPError as erreur:
            raise ServiceExterneError(
                "Veille cyclonique indisponible : le service ne répond pas",
                details={"fournisseur": self.info.cle, "cause": type(erreur).__name__},
            ) from erreur
        if reponse.status_code >= 400:
            raise ServiceExterneError(
                "Veille cyclonique indisponible",
                details={"fournisseur": self.info.cle, "http": reponse.status_code},
            )
        try:
            donnees = reponse.json()
        except ValueError as erreur:
            raise ServiceExterneError(
                "Réponse GDACS illisible", details={"fournisseur": self.info.cle}
            ) from erreur
        return donnees if isinstance(donnees, dict) else {}

    def systemes(
        self, *, emprise: Emprise, jours: int = 7, marge_deg: float = 4.0
    ) -> list[SystemeTropical]:
        """Épisodes tropicaux actifs dans la zone (fenêtre glissante en jours)."""
        aujourdhui = datetime.now(UTC).date()
        donnees = self._get(
            "events/geteventlist/SEARCH",
            params={
                "eventlist": "TC",
                # Paramètres **en minuscules** : c'est la forme acceptée par
                # l'API (``fromDate`` renvoie un délai puis une erreur).
                "fromdate": (aujourdhui - timedelta(days=max(1, jours))).isoformat(),
                "todate": aujourdhui.isoformat(),
            },
        )
        return interpreter_systemes(donnees, emprise=emprise, marge_deg=marge_deg)

    def polygones(self, systeme: SystemeTropical) -> list[PolygoneVent]:
        """Zones de vent d'un épisode (géométrie publiée, jamais extrapolée)."""
        donnees = self._get(
            "polygons/getgeometry",
            params={
                "eventtype": "TC",
                "eventid": systeme.identifiant,
                "episodeid": str(systeme.episode),
            },
        )
        annee = (systeme.publie_le or systeme.du or datetime.now(UTC)).year
        return interpreter_polygones(donnees, annee=annee)
