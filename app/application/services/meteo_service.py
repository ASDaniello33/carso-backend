"""Service — météo, imagerie satellite et veille cyclonique.

Ce service est le seul endroit qui décide **ce qui est affichable** : il
interroge les fournisseurs publics, met en cache ce qui peut l'être, borne le
débit des appels et, quand une source manque, rend une indisponibilité
explicite au lieu d'une erreur qui viderait la carte.

Trois mécanismes, trois raisons distinctes :

- **cache par nature de donnée** — une prévision horaire ne se rafraîchit pas
  comme une image satellite d'un créneau passé (immuable) ni comme un avis
  cyclonique (qui doit être frais). Chaque TTL vient de la configuration ;
- **limiteur de débit** — un utilisateur qui fait glisser le curseur temporel
  demande huit images d'affilée ; sans plafond, le service externe voit une
  rafale et l'instance entière en subit le quota ;
- **dégradation explicite** — ``Resultat`` porte soit une valeur, soit un
  message. L'interface affiche « indisponible, réessayez » plutôt que zéro
  degré ou une trajectoire inventée.

Aucune donnée météo n'est écrite en base : ce sont des observations externes
périssables, pas des données métier CARSO. Le cache est en mémoire, par
processus — comme ``app.tools.cache_ttl`` pour les recherches web.
"""

from __future__ import annotations

import math
import threading
import time
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Generic, TypeVar

from app.core.config import Settings, get_settings
from app.core.errors import ServiceExterneError
from app.geo.meteo import (
    COUCHES_SATELLITE,
    FOURNISSEURS,
    ClientEumetview,
    ClientGdacs,
    ClientGibs,
    ClientOpenMeteo,
    Condition,
    CoucheSatellite,
    Emprise,
    FournisseurInfo,
    ImageSatellite,
    MeteoPoint,
    SystemeTropical,
    couche_par_id,
    couches_publiees,
    distance_km,
)
from app.tools.cache_ttl import CacheTTL

__all__ = [
    "AlerteCyclone",
    "EtatCouche",
    "Horizon",
    "LimiteurDebit",
    "MeteoService",
    "PointPrevu",
    "Resultat",
    "ResultatTrajet",
    "vider_caches",
]

T = TypeVar("T")


# ---------------------------------------------------------------------------
# Résultats : une valeur, ou la raison de son absence
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Resultat(Generic[T]):
    """Valeur disponible ou indisponibilité motivée — jamais les deux."""

    valeur: T | None = None
    erreur: str | None = None
    depuis_cache: bool = False
    #: Vrai quand la valeur vient d'un cache encore valide après un échec.
    valeur_ancienne: bool = False

    @property
    def disponible(self) -> bool:
        return self.valeur is not None


@dataclass(frozen=True, slots=True)
class EtatCouche:
    """Disponibilité réelle d'une couche satellite."""

    couche: CoucheSatellite
    disponible: bool
    dernier_creneau: datetime | None = None
    erreur: str | None = None


@dataclass(frozen=True, slots=True)
class Horizon:
    """Un point d'un déplacement et l'heure à laquelle on s'y trouvera."""

    latitude: float
    longitude: float
    heure: datetime
    #: Libellé facultatif (nom du lieu, étape du trajet).
    libelle: str | None = None


@dataclass(frozen=True, slots=True)
class PointPrevu:
    """Prévision retenue pour un point du trajet.

    ``ecart_minutes`` dit de combien l'heure retenue s'écarte de l'heure
    souhaitée : une prévision horaire ne tombe pas sur un départ à 06:20, et le
    taire donnerait une précision qu'on n'a pas.
    """

    latitude: float
    longitude: float
    libelle: str | None
    heure_souhaitee: datetime
    condition: Condition | None
    ecart_minutes: int | None
    hors_fenetre: bool


@dataclass(frozen=True, slots=True)
class AlerteCyclone:
    """Système tropical proche d'un point — une alerte, pas un pronostic."""

    systeme: SystemeTropical
    distance_km: float
    #: Vrai si une zone prévue tombe dans la fenêtre de déplacement.
    dans_la_periode: bool


@dataclass(frozen=True, slots=True)
class ResultatTrajet:
    """Prévisions le long d'un déplacement, avec alertes et avertissements."""

    points: tuple[PointPrevu, ...]
    alertes: tuple[AlerteCyclone, ...]
    avertissements: tuple[str, ...]
    fournisseur: FournisseurInfo
    recupere_le: datetime | None
    erreur: str | None = None
    depuis_cache: bool = False


# ---------------------------------------------------------------------------
# Limiteur de débit (fenêtre glissante, par fournisseur)
# ---------------------------------------------------------------------------


class LimiteurDebit:
    """Plafond d'appels par minute, par clé — protège les quotas gratuits.

    Fenêtre glissante simple : on garde les horodatages des appels autorisés
    dans la dernière minute. Une rafale d'images pour l'animation en consomme
    plusieurs d'un coup, d'où le plafond plutôt qu'une pause imposée.
    """

    def __init__(
        self,
        maximum: int,
        *,
        fenetre_s: float = 60.0,
        horloge: Callable[[], float] = time.monotonic,
    ) -> None:
        self._maximum = max(1, int(maximum))
        self._fenetre = float(fenetre_s)
        self._horloge = horloge
        self._appels: dict[str, deque[float]] = {}
        self._verrou = threading.Lock()

    def autoriser(self, cle: str) -> bool:
        """Réserve un appel ; ``False`` quand le plafond est atteint."""
        with self._verrou:
            maintenant = self._horloge()
            file = self._appels.setdefault(cle, deque())
            while file and maintenant - file[0] >= self._fenetre:
                file.popleft()
            if len(file) >= self._maximum:
                return False
            file.append(maintenant)
            return True

    def vider(self) -> None:
        with self._verrou:
            self._appels.clear()


# ---------------------------------------------------------------------------
# Caches partagés (un par processus, comme le cache des recherches web)
# ---------------------------------------------------------------------------

_CACHE_CONDITIONS: CacheTTL[list[MeteoPoint]] | None = None
_CACHE_SATELLITE: CacheTTL[ImageSatellite] | None = None
_CACHE_CAPACITES: CacheTTL[dict[str, datetime]] | None = None
_CACHE_SYSTEMES: CacheTTL[list[SystemeTropical]] | None = None
_LIMITEUR: LimiteurDebit | None = None
_VERROU_CACHES = threading.Lock()


def _caches(settings: Settings) -> tuple[
    CacheTTL[list[MeteoPoint]],
    CacheTTL[ImageSatellite],
    CacheTTL[dict[str, datetime]],
    CacheTTL[list[SystemeTropical]],
    LimiteurDebit,
]:
    """Construit (une fois) les caches aux TTL configurés."""
    global _CACHE_CONDITIONS, _CACHE_SATELLITE, _CACHE_CAPACITES, _CACHE_SYSTEMES
    global _LIMITEUR
    with _VERROU_CACHES:
        if _CACHE_CONDITIONS is None:
            _CACHE_CONDITIONS = CacheTTL(ttl=settings.meteo_conditions_ttl_s, taille_max=256)
        if _CACHE_SATELLITE is None:
            _CACHE_SATELLITE = CacheTTL(
                ttl=settings.meteo_satellite_ttl_s,
                # Une image ~250 Ko : 40 entrées ≈ 10 Mo, borné volontairement.
                taille_max=settings.meteo_satellite_images_max,
            )
        if _CACHE_CAPACITES is None:
            _CACHE_CAPACITES = CacheTTL(ttl=settings.eumetview_capacites_ttl_s, taille_max=4)
        if _CACHE_SYSTEMES is None:
            _CACHE_SYSTEMES = CacheTTL(ttl=settings.meteo_cyclones_ttl_s, taille_max=8)
        if _LIMITEUR is None:
            _LIMITEUR = LimiteurDebit(settings.meteo_requetes_par_minute)
        return (
            _CACHE_CONDITIONS,
            _CACHE_SATELLITE,
            _CACHE_CAPACITES,
            _CACHE_SYSTEMES,
            _LIMITEUR,
        )


def vider_caches() -> None:
    """Vide les caches météo (tests, ou rechargement explicite)."""
    global _CACHE_CONDITIONS, _CACHE_SATELLITE, _CACHE_CAPACITES, _CACHE_SYSTEMES
    global _LIMITEUR
    with _VERROU_CACHES:
        _CACHE_CONDITIONS = None
        _CACHE_SATELLITE = None
        _CACHE_CAPACITES = None
        _CACHE_SYSTEMES = None
        _LIMITEUR = None


def _arrondi(valeur: float) -> float:
    """3 décimales ≈ 100 m : deux clics voisins partagent la même prévision."""
    return round(valeur, 3)


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class MeteoService:
    """Point d'entrée unique de l'interface météo (lecture seule, sans base).

    Args:
        settings: configuration ; ``None`` prend celle de l'application.
        fournisseur: client de conditions (défaut : Open-Meteo).
        eumetview, gibs, gdacs: clients d'imagerie et de veille ; injectables
            pour les tests (aucun réseau).
        horloge: source de temps injectable (tests d'ancienneté).
    """

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        fournisseur: ClientOpenMeteo | None = None,
        eumetview: ClientEumetview | None = None,
        gibs: ClientGibs | None = None,
        gdacs: ClientGdacs | None = None,
        horloge: Callable[[], datetime] | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self._horloge = horloge or (lambda: datetime.now(UTC))
        self._conditions = (
            fournisseur
            if fournisseur is not None
            else ClientOpenMeteo(
                self.settings.open_meteo_base_url,
                timeout_s=self.settings.open_meteo_timeout_s,
            )
        )
        self._satellite = eumetview or ClientEumetview(
            self.settings.eumetview_wms_url, timeout_s=self.settings.eumetview_timeout_s
        )
        self._gibs = gibs or ClientGibs(
            self.settings.gibs_wms_url, timeout_s=self.settings.gibs_timeout_s
        )
        self._cyclones = gdacs or ClientGdacs(
            self.settings.gdacs_api_url, timeout_s=self.settings.gdacs_timeout_s
        )
        (
            self._cache_conditions,
            self._cache_images,
            self._cache_capacites,
            self._cache_systemes,
            self._limiteur,
        ) = _caches(self.settings)

    # -- configuration ------------------------------------------------------

    @property
    def emprise(self) -> Emprise:
        """Emprise de référence : Madagascar et l'océan Indien sud-ouest."""
        return Emprise(
            sud=self.settings.meteo_region_sud,
            ouest=self.settings.meteo_region_ouest,
            nord=self.settings.meteo_region_nord,
            est=self.settings.meteo_region_est,
        )

    @property
    def actif(self) -> bool:
        return self.settings.meteo_actif

    def fournisseurs(self) -> list[dict[str, object]]:
        """Catalogue des sources, licences et état d'activation (pour l'écran)."""
        etats = {
            "open-meteo": self.settings.meteo_actif,
            "eumetsat": self.settings.meteo_satellite_actif,
            "nasa-gibs": self.settings.meteo_satellite_actif and self.settings.gibs_actif,
            "gdacs": self.settings.meteo_cyclones_actif,
        }
        return [
            {
                "cle": info.cle,
                "nom": info.nom,
                "url": info.url,
                "licence": info.licence,
                "usage": info.usage,
                "remarque": info.remarque,
                "actif": etats.get(info.cle, False),
            }
            for info in FOURNISSEURS
        ]

    # -- conditions d'un point ---------------------------------------------

    def conditions(
        self, latitude: float, longitude: float, *, heures: int = 24
    ) -> Resultat[list[MeteoPoint]]:
        """Conditions actuelles et prévision horaire d'un point."""
        if not self.actif:
            return Resultat(erreur="Le module météo est désactivé sur cette instance.")
        cle = ("point", _arrondi(latitude), _arrondi(longitude), heures)
        cached = self._cache_conditions.obtenir(cle)
        if cached is not None:
            return Resultat(valeur=cached, depuis_cache=True)
        try:
            points = self._interroger([(latitude, longitude)], heures=heures)
        except ServiceExterneError as erreur:
            return Resultat(erreur=str(erreur))
        self._cache_conditions.memoriser(cle, points)
        return Resultat(valeur=points)

    def previsions_jour(
        self, coordonnees: Sequence[tuple[float, float]], jour: date
    ) -> Resultat[list[MeteoPoint]]:
        """Prévision d'une **journée entière**, pour chaque point d'un trajet.

        Un déplacement ne se prépare pas à l'heure près : on connaît le jour,
        pas l'instant de passage. Chaque point reçoit donc ses 24 heures, et
        c'est la carte qui les montre. Un seul appel fournisseur couvre tous
        les points — le quota n'est pas consommé une fois par point.
        """
        if not self.actif:
            return Resultat(erreur="Le module météo est désactivé sur cette instance.")
        cle = (
            "jour",
            jour,
            tuple((_arrondi(latitude), _arrondi(longitude)) for latitude, longitude in coordonnees),
        )
        cached = self._cache_conditions.obtenir(cle)
        if cached is not None:
            return Resultat(valeur=cached, depuis_cache=True)
        try:
            if not self._limiteur.autoriser(self._conditions.info.cle):
                raise ServiceExterneError(
                    "Trop de demandes météo pour le moment : réessayez dans un instant",
                    details={"fournisseur": self._conditions.info.cle, "limite": True},
                )
            points = self._conditions.points(coordonnees, jour=jour)
        except ServiceExterneError as erreur:
            return Resultat(erreur=str(erreur))
        self._cache_conditions.memoriser(cle, points)
        return Resultat(valeur=points)

    def alertes_du_jour(
        self,
        coordonnees: Sequence[tuple[float, float]],
        jour: date,
        *,
        rayon_km: float = 150.0,
    ) -> tuple[AlerteCyclone, ...]:
        """Systèmes tropicaux proches des points d'un trajet, pour **un jour**.

        La prévision du trajet ne dit plus l'heure de passage : la fenêtre
        testée est donc celle du jour demandé, du premier au dernier instant de
        la journée. Aucune trajectoire n'est interpolée entre deux positions
        publiées.
        """
        debut = _minuit(jour)
        fin = debut + timedelta(hours=23)
        horizons = [
            Horizon(latitude=latitude, longitude=longitude, heure=instant)
            for latitude, longitude in coordonnees
            for instant in (debut, fin)
        ]
        if not horizons:
            return ()
        return self._alertes(horizons, rayon_km)

    def previsions_trajet(
        self,
        horizons: Sequence[Horizon],
        *,
        rayon_alerte_km: float = 150.0,
    ) -> ResultatTrajet:
        """Prévision le long d'un déplacement, alignée sur les heures de passage.

        Un **seul** appel couvre tous les points (l'API accepte plusieurs
        coordonnées) : le quota gratuit n'est pas consommé une fois par étape.
        """
        if not self.actif:
            return ResultatTrajet(
                points=(),
                alertes=(),
                avertissements=(),
                fournisseur=self._conditions.info,
                recupere_le=None,
                erreur="Le module météo est désactivé sur cette instance.",
            )
        if not horizons:
            return ResultatTrajet(
                points=(),
                alertes=(),
                avertissements=("Aucun point de trajet à évaluer.",),
                fournisseur=self._conditions.info,
                recupere_le=None,
            )
        maximum = min(len(horizons), 12)
        retenus = list(horizons)[:maximum]
        avertissements: list[str] = []
        if len(horizons) > maximum:
            avertissements.append(
                f"Seuls les {maximum} premiers points du trajet sont évalués "
                f"({len(horizons)} demandés)."
            )
        try:
            points = self._interroger(
                [(horizon.latitude, horizon.longitude) for horizon in retenus],
                heures=self._heures_necessaires(retenus),
            )
        except ServiceExterneError as erreur:
            alertes = self._alertes(retenus, rayon_alerte_km)
            return ResultatTrajet(
                points=(),
                alertes=alertes,
                avertissements=tuple(avertissements),
                fournisseur=self._conditions.info,
                recupere_le=None,
                erreur=str(erreur),
            )
        if len(points) < len(retenus):
            # Un fournisseur qui rend moins de points que demandé laisse des
            # étapes sans prévision : on le dit plutôt que de les taire.
            avertissements.append(
                f"Le fournisseur n'a renvoyé que {len(points)} point(s) sur "
                f"{len(retenus)} demandés."
            )
        prevus: list[PointPrevu] = []
        for horizon, point in zip(retenus, points, strict=False):
            condition, ecart = _au_plus_proche(point.horaires, horizon.heure)
            hors_fenetre = condition is None
            if hors_fenetre:
                avertissements.append(
                    f"{_nom(horizon)} : aucune prévision horaire à cette heure "
                    "(hors de la fenêtre publiée par le fournisseur)."
                )
            prevus.append(
                PointPrevu(
                    latitude=horizon.latitude,
                    longitude=horizon.longitude,
                    libelle=horizon.libelle,
                    heure_souhaitee=horizon.heure,
                    condition=condition,
                    ecart_minutes=ecart,
                    hors_fenetre=hors_fenetre,
                )
            )
        alertes = self._alertes(retenus, rayon_alerte_km)
        if alertes:
            avertissements.append(
                "Un phénomène tropical est signalé à proximité : consultez le "
                "bulletin officiel avant le départ."
            )
        avertissements.append(
            "Ces prévisions sont une aide à la préparation : elles ne garantissent "
            "ni la sécurité du déplacement ni l'état des routes."
        )
        return ResultatTrajet(
            points=tuple(prevus),
            alertes=alertes,
            avertissements=tuple(avertissements),
            fournisseur=points[0].fournisseur,
            recupere_le=points[0].recupere_le,
        )

    # -- imagerie satellite --------------------------------------------------

    def couches_satellite(self) -> list[EtatCouche]:
        """Catalogue des couches avec leur dernier créneau **réellement publié**."""
        derniers: dict[str, datetime] = {}
        erreur_capacites: str | None = None
        if self.settings.meteo_satellite_actif:
            try:
                derniers, erreur_capacites = self._capacites()
            except ServiceExterneError as erreur:
                erreur_capacites = str(erreur)
        etats: list[EtatCouche] = []
        for couche in COUCHES_SATELLITE:
            if couche.fournisseur.cle == "eumetsat":
                if not self.settings.meteo_satellite_actif:
                    etats.append(
                        EtatCouche(
                            couche=couche,
                            disponible=False,
                            erreur="Imagerie satellite désactivée sur cette instance.",
                        )
                    )
                    continue
                creneau = derniers.get(couche.id)
                etats.append(
                    EtatCouche(
                        couche=couche,
                        disponible=creneau is not None,
                        dernier_creneau=creneau,
                        erreur=None if creneau else erreur_capacites,
                    )
                )
                continue
            if not (self.settings.meteo_satellite_actif and self.settings.gibs_actif):
                etats.append(
                    EtatCouche(
                        couche=couche,
                        disponible=False,
                        erreur="Fournisseur secondaire désactivé.",
                    )
                )
                continue
            # GIBS publie une image par jour : la dernière est celle de la veille.
            etats.append(
                EtatCouche(
                    couche=couche,
                    disponible=True,
                    dernier_creneau=_minuit(self._horloge().date() - timedelta(days=1)),
                )
            )
        return etats

    def image_satellite(
        self, identifiant: str, heure: datetime | None = None
    ) -> Resultat[ImageSatellite]:
        """Image d'une couche, avec l'instant qu'elle représente.

        Sans ``heure``, on prend le dernier créneau publié ; s'il est en retard
        côté fournisseur, on recule d'un pas **jusqu'à en trouver un** — l'heure
        affichée est celle de l'image reçue, jamais celle qu'on espérait.
        """
        if not self.settings.meteo_satellite_actif:
            return Resultat(erreur="Imagerie satellite désactivée sur cette instance.")
        couche = couche_par_id(identifiant)
        if couche.journaliere and not self.settings.gibs_actif:
            return Resultat(erreur="Le fournisseur NASA GIBS est désactivé.")
        if couche.journaliere:
            jour = heure.date() if heure else self._horloge().date() - timedelta(days=1)
            return self._image_journaliere(couche, jour)
        return self._image_eumetsat(couche, heure)

    def _image_journaliere(self, couche: CoucheSatellite, jour: date) -> Resultat[ImageSatellite]:
        cle = (couche.id, jour.isoformat())
        cached = self._cache_images.obtenir(cle)
        if cached is not None:
            return Resultat(valeur=cached, depuis_cache=True)
        if not self._limiteur.autoriser(couche.fournisseur.cle):
            return Resultat(
                erreur="Trop de demandes d'images pour le moment : patientez un instant."
            )
        try:
            image = self._gibs.image(
                couche, jour, self.emprise, taille=self.settings.meteo_satellite_taille
            )
        except ServiceExterneError as erreur:
            return Resultat(erreur=str(erreur))
        self._cache_images.memoriser(cle, image)
        return Resultat(valeur=image)

    def _image_eumetsat(
        self, couche: CoucheSatellite, heure: datetime | None
    ) -> Resultat[ImageSatellite]:
        try:
            derniers, erreur = self._capacites()
        except ServiceExterneError as erreur_client:
            return Resultat(erreur=str(erreur_client))
        if erreur and couche.id not in derniers:
            return Resultat(erreur=erreur)
        reference = heure or derniers.get(couche.id)
        if reference is None:
            return Resultat(erreur=erreur or "Aucun créneau publié pour cette couche.")
        pas = timedelta(minutes=couche.pas_minutes)
        derniere_erreur: str | None = None
        # Trois créneaux au plus : une image ne descend pas indéfiniment dans le
        # passé, et chaque essai coûte un appel au fournisseur.
        for tentative in range(3):
            creneau = reference - tentative * pas
            cle = (couche.id, creneau.astimezone(UTC).isoformat())
            cached = self._cache_images.obtenir(cle)
            if cached is not None:
                return Resultat(valeur=cached, depuis_cache=True)
            if not self._limiteur.autoriser(couche.fournisseur.cle):
                return Resultat(
                    erreur=derniere_erreur
                    or "Trop de demandes d'images pour le moment : patientez un instant."
                )
            try:
                image = self._satellite.image(
                    couche, creneau, self.emprise, taille=self.settings.meteo_satellite_taille
                )
            except ServiceExterneError as erreur:
                derniere_erreur = str(erreur)
                continue
            self._cache_images.memoriser(cle, image)
            return Resultat(valeur=image)
        return Resultat(erreur=derniere_erreur or "Aucune image disponible pour cette couche.")

    def _capacites(self) -> tuple[dict[str, datetime], str | None]:
        """Derniers créneaux publiés, cache 6 h ; l'erreur n'annule pas un cache."""
        cached = self._cache_capacites.obtenir("eumetsat")
        if cached is not None:
            return cached, None
        if not self._limiteur.autoriser("eumetview-capacites"):
            return {}, "Capacités satellite momentanément inaccessibles (débit limité)."
        try:
            xml = self._satellite.capacites()
        except ServiceExterneError as erreur:
            return {}, str(erreur)
        publiees = couches_publiees(xml)
        self._cache_capacites.memoriser("eumetsat", publiees)
        return publiees, None

    # -- systèmes tropicaux --------------------------------------------------

    def systemes_tropicaux(self, *, jours: int | None = None) -> Resultat[list[SystemeTropical]]:
        """Systèmes tropicaux officiels de la région (avec leurs zones de vent)."""
        if not self.settings.meteo_cyclones_actif:
            return Resultat(erreur="La veille cyclonique est désactivée sur cette instance.")
        horizon = jours or self.settings.meteo_cyclones_jours
        cle = ("systemes", horizon)
        cached = self._cache_systemes.obtenir(cle)
        if cached is not None:
            return Resultat(valeur=cached, depuis_cache=True)
        if not self._limiteur.autoriser("gdacs"):
            return Resultat(erreur="Veille cyclonique momentanément limitée : réessayez.")
        try:
            systemes = self._cyclones.systemes(emprise=self.emprise, jours=horizon)
        except ServiceExterneError as erreur:
            return Resultat(erreur=str(erreur))
        enrichis: list[SystemeTropical] = []
        # Deux épisodes au plus : chaque géométrie coûte un appel de 5 à 10 s.
        for systeme in systemes[:2]:
            if not self._limiteur.autoriser("gdacs"):
                enrichis.append(systeme)
                continue
            try:
                polygones = self._cyclones.polygones(systeme)
            except ServiceExterneError:
                # Un système sans géométrie reste un système : position et
                # bulletin suffisent, et l'absence de zone est dite.
                polygones = []
            enrichis.append(
                SystemeTropical(
                    identifiant=systeme.identifiant,
                    nom=systeme.nom,
                    episode=systeme.episode,
                    niveau=systeme.niveau,
                    intensite=systeme.intensite,
                    intensite_kmh=systeme.intensite_kmh,
                    position=systeme.position,
                    du=systeme.du,
                    au=systeme.au,
                    publie_le=systeme.publie_le,
                    source=systeme.source,
                    url_bulletin=systeme.url_bulletin,
                    pays=systeme.pays,
                    actuel=systeme.actuel,
                    polygones=tuple(polygones),
                )
            )
        enrichis.extend(systemes[2:])
        self._cache_systemes.memoriser(cle, enrichis)
        return Resultat(valeur=enrichis)

    def _alertes(
        self, horizons: Sequence[Horizon], rayon_km: float
    ) -> tuple[AlerteCyclone, ...]:
        """Systèmes à moins de ``rayon_km`` d'un point du trajet.

        On mesure la distance au centre publié **et** aux zones de vent : une
        zone peut frôler un point alors que le centre passe plus loin. Aucune
        trajectoire n'est tracée entre deux positions.
        """
        resultat = self.systemes_tropicaux()
        if not resultat.disponible:
            return ()
        alertes: list[AlerteCyclone] = []
        debut = min(horizon.heure for horizon in horizons)
        fin = max(horizon.heure for horizon in horizons)
        for systeme in resultat.valeur or []:
            meilleure = math.inf
            dans_la_periode = False
            if systeme.position is not None:
                for horizon in horizons:
                    meilleure = min(
                        meilleure,
                        distance_km(
                            horizon.latitude,
                            horizon.longitude,
                            systeme.position[0],
                            systeme.position[1],
                        ),
                    )
            for polygone in systeme.polygones:
                if polygone.prevu_le is not None and debut <= polygone.prevu_le <= fin:
                    dans_la_periode = True
                for latitude, longitude in polygone.positions:
                    for horizon in horizons:
                        meilleure = min(
                            meilleure,
                            distance_km(
                                horizon.latitude, horizon.longitude, latitude, longitude
                            ),
                        )
            if meilleure <= rayon_km:
                alertes.append(
                    AlerteCyclone(
                        systeme=systeme,
                        distance_km=round(meilleure, 1),
                        dans_la_periode=dans_la_periode,
                    )
                )
        alertes.sort(key=lambda alerte: alerte.distance_km)
        return tuple(alertes)

    # -- interne -------------------------------------------------------------

    def _interroger(
        self, coordonnees: Sequence[tuple[float, float]], *, heures: int
    ) -> list[MeteoPoint]:
        cle = (
            "points",
            tuple((_arrondi(latitude), _arrondi(longitude)) for latitude, longitude in coordonnees),
            heures,
        )
        cached = self._cache_conditions.obtenir(cle)
        if cached is not None:
            return cached
        if not self._limiteur.autoriser(self._conditions.info.cle):
            raise ServiceExterneError(
                "Trop de demandes météo pour le moment : réessayez dans un instant",
                details={"fournisseur": self._conditions.info.cle, "limite": True},
            )
        points = self._conditions.points(coordonnees, heures=heures)
        self._cache_conditions.memoriser(cle, points)
        return points

    def _heures_necessaires(self, horizons: Sequence[Horizon]) -> int:
        """Fenêtre à demander : de maintenant au dernier passage, borné à 72 h."""
        maintenant = self._horloge()
        dernier = max(horizon.heure for horizon in horizons)
        ecart = (dernier - maintenant).total_seconds() / 3600
        return max(3, min(int(ecart) + 2, 72))


def _nom(horizon: Horizon) -> str:
    return horizon.libelle or f"{horizon.latitude:.2f}, {horizon.longitude:.2f}"


def _minuit(jour: date) -> datetime:
    return datetime.combine(jour, datetime.min.time(), tzinfo=UTC)


def _au_plus_proche(
    horaires: Sequence[Condition], cible: datetime
) -> tuple[Condition | None, int | None]:
    """Condition de l'heure la plus proche, et l'écart en minutes.

    Au-delà de trois heures d'écart, on ne rend **rien** : une prévision de
    milieu de nuit ne décrit pas un départ de fin de matinée, et l'annoncer
    comme telle serait faux.
    """
    if not horaires:
        return None, None
    reference = cible.astimezone(UTC)
    meilleure: Condition | None = None
    ecart_min: int | None = None
    for condition in horaires:
        delta = abs((condition.heure - reference).total_seconds())
        if ecart_min is None or delta < ecart_min * 60:
            meilleure = condition
            ecart_min = int(round(delta / 60))
    if ecart_min is None or ecart_min > 180:
        return None, ecart_min
    return meilleure, ecart_min
