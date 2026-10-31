"""Tests — météo, imagerie satellite et veille cyclonique.

Trois niveaux, tous **hors réseau** :

- les interpréteurs purs (réponses Open-Meteo, GDACS, GetCapabilities) : ce sont
  eux qui décident ce qui est lisible, donc ceux qui pourraient inventer ;
- le service : cache, limiteur, alignement des heures, dégradation ;
- les routes : forme de la réponse, en-tête d'horodatage de l'image, liens
  officiels conservés quand la veille est indisponible.

Aucun test ne contacte un fournisseur : une suite qui dépend de la météo du
jour n'est pas une suite.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient

from app.api.routes import meteo as routes_meteo
from app.application.services import meteo_service
from app.application.services.meteo_service import (
    Horizon,
    LimiteurDebit,
    MeteoService,
    vider_caches,
)
from app.core.config import Settings
from app.core.errors import ServiceExterneError
from app.geo.meteo import (
    FOURNISSEUR_OPEN_METEO,
    ClientEumetview,
    ClientOpenMeteo,
    Emprise,
    ImageSatellite,
    SystemeTropical,
    classer_code,
    couche_par_id,
    interpreter_points,
    interpreter_polygones,
    interpreter_systemes,
    lire_derniere_heure,
)

BASE = datetime(2026, 10, 29, 6, tzinfo=UTC)
EMPRISE = Emprise(sud=-30.0, ouest=30.0, nord=2.0, est=60.0)


@pytest.fixture(autouse=True)
def _caches_vides():
    """Chaque test part de caches vides : aucun test ne dépend d'un autre."""
    vider_caches()
    yield
    vider_caches()


# ---------------------------------------------------------------------------
# Doubles de test (aucun réseau)
# ---------------------------------------------------------------------------


def _reponse_open_meteo(latitude: float = -23.35, longitude: float = 43.66) -> dict:
    """Réponse Open-Meteo d'un lieu, 24 heures à partir de ``BASE``."""
    heures = [(BASE + timedelta(hours=index)) for index in range(24)]
    return {
        "latitude": latitude,
        "longitude": longitude,
        "timezone": "GMT",
        "elevation": 8.0,
        "current": {
            "time": BASE.strftime("%Y-%m-%dT%H:%M"),
            "interval": 900,
            "temperature_2m": 24.4,
            "apparent_temperature": 24.9,
            "relative_humidity_2m": 68,
            "wind_speed_10m": 18.3,
            "wind_direction_10m": 170,
            "wind_gusts_10m": 37.4,
            "precipitation": 0.1,
            "weather_code": 51,
            "cloud_cover": 33,
        },
        "hourly": {
            "time": [heure.strftime("%Y-%m-%dT%H:%M") for heure in heures],
            "temperature_2m": [24.0 + index * 0.2 for index in range(24)],
            "apparent_temperature": [24.5 + index * 0.2 for index in range(24)],
            "relative_humidity_2m": [70 - index for index in range(24)],
            "wind_speed_10m": [12.0 + index for index in range(24)],
            "wind_direction_10m": [180 for _ in range(24)],
            "wind_gusts_10m": [28.0 + index for index in range(24)],
            # Dernière valeur nulle : une série courte ou trouée ne doit pas
            # devenir zéro degré ni zéro millimètre.
            "precipitation": [0.0 for _ in range(23)] + [None],
            "precipitation_probability": [10 for _ in range(24)],
            "weather_code": [3] * 24,
            "cloud_cover": [40 for _ in range(24)],
        },
    }


class FauxConditions:
    """Client de conditions piloté par le test (compte ses appels)."""

    info = FOURNISSEUR_OPEN_METEO

    def __init__(self, reponse: dict | None = None, erreur: Exception | None = None) -> None:
        self.reponse = reponse or _reponse_open_meteo()
        self.erreur = erreur
        self.appels: list[tuple[tuple[tuple[float, float], ...], int]] = []
        #: Journées demandées (prévision d'une date, pas d'une fenêtre courante).
        self.jours: list[date | None] = []

    def points(
        self, coordonnees, *, heures: int = 24, jour: date | None = None
    ):  # noqa: ANN001, ANN201 - double de test
        self.appels.append((tuple(coordonnees), heures))
        self.jours.append(jour)
        if self.erreur is not None:
            raise self.erreur
        reponses = []
        for latitude, longitude in coordonnees:
            bloc = dict(self.reponse)
            bloc["latitude"] = latitude
            bloc["longitude"] = longitude
            reponses.append(bloc)
        return interpreter_points(reponses)


#: Extrait **réel** de GetCapabilities (deux couches, deux créneaux distincts) :
#: c'est la preuve que le dernier créneau est lu par couche, pas supposé commun.
CAPACITES_EXEMPLE = (
    "<WMS_Capabilities><Capability><Layer><Layer>"
    "<Name>msg_iodc:ir108</Name><Title>IR 10.8</Title>"
    '<Dimension name="time" default="2026-10-29T05:30:00Z" units="ISO8601">'
    "2026-10-29T00:00:00.000Z/2026-10-29T05:30:00.000Z/PT15M</Dimension>"
    "</Layer><Layer>"
    "<Name>msg_iodc:rgb_convection</Name><Title>Convection</Title>"
    '<Dimension name="time" default="2026-10-29T05:15:00Z" units="ISO8601">'
    "2026-10-29T00:00:00.000Z/2026-10-29T05:15:00.000Z/PT15M</Dimension>"
    "</Layer></Layer></Capability></WMS_Capabilities>"
)


class FauxEumetview:
    """GetMap simulé : seuls les créneaux déclarés existent."""

    def __init__(self, creneaux: set[datetime], *, xml: str = CAPACITES_EXEMPLE) -> None:
        self.creneaux = creneaux
        self.xml = xml
        self.demandes: list[datetime] = []

    def capacites(self) -> str:
        return self.xml

    def image(self, couche, heure, emprise, *, taille: int = 1024):  # noqa: ANN001, ANN201
        self.demandes.append(heure)
        if heure not in self.creneaux:
            raise ServiceExterneError("Aucune image publiée pour ce créneau")
        return ImageSatellite(
            couche=couche,
            heure=heure,
            contenu=b"\x89PNG-faux",
            mime="image/png",
            emprise=emprise,
        )


class FauxGdacs:
    def __init__(self, systemes: list[SystemeTropical] | None = None, erreur=None) -> None:  # noqa: ANN001
        self._systemes = systemes or []
        self._erreur = erreur

    def systemes(self, *, emprise, jours: int = 7, marge_deg: float = 4.0):  # noqa: ANN001, ANN201
        if self._erreur is not None:
            raise self._erreur
        return interpreter_systemes(
            {
                "features": [
                    {
                        "geometry": {
                            "type": "Point",
                            "coordinates": [
                                systeme.position[1] if systeme.position else 0.0,
                                systeme.position[0] if systeme.position else 0.0,
                            ],
                        },
                        "properties": {
                            "eventid": systeme.identifiant,
                            "eventname": systeme.nom,
                            "episodeid": systeme.episode,
                            "alertlevel": systeme.niveau.lower()
                            if systeme.niveau in {"Rouge", "Orange", "Vert"}
                            else "red",
                            "fromdate": "2026-10-27T00:00:00",
                            "todate": "2026-10-30T00:00:00",
                            "datemodified": "2026-10-29T05:00:00",
                            "source": systeme.source,
                            "country": systeme.pays or "",
                            "iscurrent": "true",
                            "severitydata": {
                                "severity": systeme.intensite_kmh,
                                "severitytext": systeme.intensite,
                            },
                            "url": {"report": systeme.url_bulletin},
                        },
                    }
                    for systeme in self._systemes
                ]
            },
            emprise=emprise,
            marge_deg=marge_deg,
        )

    def polygones(self, systeme):  # noqa: ANN001, ANN201
        if self._erreur is not None:
            raise self._erreur
        return list(systeme.polygones)


def _service(
    *,
    conditions: FauxConditions | None = None,
    satellite: FauxEumetview | None = None,
    gdacs: FauxGdacs | None = None,
    settings: Settings | None = None,
) -> MeteoService:
    return MeteoService(
        settings or Settings(),
        fournisseur=conditions or FauxConditions(),
        eumetview=satellite or FauxEumetview({BASE}),
        gdacs=gdacs or FauxGdacs(),
    )


# ---------------------------------------------------------------------------
# Interprétation pure
# ---------------------------------------------------------------------------


def test_interpreter_points_lit_une_serie_horaire_complete():
    points = interpreter_points(_reponse_open_meteo())

    assert len(points) == 1
    point = points[0]
    assert point.latitude == pytest.approx(-23.35)
    assert len(point.horaires) == 24
    assert point.horaires[0].heure == BASE
    assert point.horaires[0].temperature_c == pytest.approx(24.0)
    assert point.actuel is not None
    assert point.actuel.temperature_c == pytest.approx(24.4)
    # Une valeur absente reste absente : pas de zéro de remplissage.
    assert point.horaires[-1].pluie_mm is None
    assert point.nature_actuelle == "estimation"  # analyse modélisée, pas mesure


def test_interpreter_points_refuse_une_reponse_illisible():
    with pytest.raises(ServiceExterneError):
        interpreter_points({"features": []})


def test_classer_code_couvre_les_familles_utiles():
    assert classer_code(0) == ("ciel_clair", "Ciel dégagé")
    assert classer_code(95)[0] == "orage"
    assert classer_code(None) == ("inconnu", "Conditions non fournies")
    assert classer_code(4242)[0] == "inconnu"


def test_lire_derniere_heure_ne_devine_pas_un_creneau_absent():
    assert lire_derniere_heure(CAPACITES_EXEMPLE, "msg_iodc:ir108") == datetime(
        2026, 10, 29, 5, 30, tzinfo=UTC
    )
    assert lire_derniere_heure(CAPACITES_EXEMPLE, "msg_iodc:inexistante") is None


def test_interpreter_systemes_filtre_hors_region():
    donnees = {
        "features": [
            {
                "geometry": {"type": "Point", "coordinates": [55.0, -18.0]},
                "properties": {
                    "eventid": 1,
                    "eventname": "PROCHE-26",
                    "episodeid": 3,
                    "alertlevel": "Orange",
                    "fromdate": "2026-10-27T00:00:00",
                    "todate": "2026-10-30T00:00:00",
                    "datemodified": "2026-10-29T05:00:00",
                    "source": "Météo-France",
                    "country": "Maurice",
                    "iscurrent": "true",
                    "severitydata": {"severity": 150.0, "severitytext": "Cyclone tropical"},
                    "url": {"report": "https://www.gdacs.org/report"},
                },
            },
            {
                "geometry": {"type": "Point", "coordinates": [127.4, 31.2]},
                "properties": {
                    "eventid": 2,
                    "eventname": "LOIN-26",
                    "episodeid": 1,
                    "alertlevel": "Red",
                    "iscurrent": "true",
                },
            },
        ]
    }

    systemes = interpreter_systemes(donnees, emprise=EMPRISE)

    assert [systeme.nom for systeme in systemes] == ["PROCHE-26"]
    assert systemes[0].niveau == "Orange"
    assert systemes[0].intensite_kmh == pytest.approx(150.0)
    assert systemes[0].url_bulletin == "https://www.gdacs.org/report"
    assert systemes[0].actuel is True


def test_interpreter_polygones_lit_les_zones_et_ignore_le_centre():
    donnees = {
        "features": [
            {
                "geometry": {"type": "Point", "coordinates": [55.0, -18.0]},
                "properties": {"Class": "Point_Centroid", "polygonlabel": "Centroid"},
            },
            {
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [
                        [[54.0, -19.0], [56.0, -19.0], [56.0, -17.0], [54.0, -19.0]]
                    ],
                },
                "properties": {"Class": "Poly_Red", "polygonlabel": "120 km/h"},
            },
            {
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [
                        [[53.0, -20.0], [57.0, -20.0], [57.0, -16.0], [53.0, -20.0]]
                    ],
                },
                "properties": {"Class": "Poly_Orange", "polygonlabel": "30/10 06:00"},
            },
            {
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [
                        [[53.0, -20.0], [57.0, -20.0], [57.0, -16.0], [53.0, -20.0]]
                    ],
                },
                "properties": {"Class": "Poly_Green", "polygonlabel": "libellé inattendu"},
            },
        ]
    }

    polygones = interpreter_polygones(donnees, annee=2026)

    assert len(polygones) == 3
    assert polygones[0].niveau == "Rouge"
    assert polygones[0].prevu_le is None  # zone actuelle, pas une prévision
    assert polygones[1].prevu_le == datetime(2026, 10, 30, 6, 0, tzinfo=UTC)
    # Un libellé non reconnu ne produit pas de date inventée.
    assert polygones[2].prevu_le is None
    # Coordonnées remises en (latitude, longitude) pour Leaflet.
    assert polygones[0].positions[0] == (-19.0, 54.0)


# ---------------------------------------------------------------------------
# Service — conditions, cache, dégradation
# ---------------------------------------------------------------------------


def test_conditions_sont_mises_en_cache():
    client = FauxConditions()
    service = _service(conditions=client)

    premier = service.conditions(-23.35, 43.66)
    second = service.conditions(-23.35, 43.66)

    assert premier.disponible and second.disponible
    assert second.depuis_cache is True
    assert len(client.appels) == 1


def test_conditions_degradees_ne_levent_pas_et_ne_cachent_pas():
    client = FauxConditions(erreur=ServiceExterneError("Météo indisponible"))
    service = _service(conditions=client)

    resultat = service.conditions(-23.35, 43.66)

    assert resultat.disponible is False
    assert "indisponible" in (resultat.erreur or "").lower()
    # Un échec n'est jamais mémorisé : la tentative suivante réessaie.
    assert len(service.conditions(-23.35, 43.66).erreur or "") > 0
    assert len(client.appels) == 2


def test_module_desactive_repond_explicitement():
    service = _service(settings=Settings(meteo_actif=False))

    resultat = service.conditions(-23.35, 43.66)

    assert resultat.disponible is False
    assert "désactivé" in (resultat.erreur or "")


# ---------------------------------------------------------------------------
# Service — prévisions le long d'un trajet
# ---------------------------------------------------------------------------


def test_trajet_tient_en_un_seul_appel_et_aligne_les_heures():
    client = FauxConditions()
    service = _service(conditions=client)
    horizons = [
        Horizon(
            latitude=-23.35,
            longitude=43.66,
            heure=BASE + timedelta(hours=2),
            libelle="Départ",
        ),
        Horizon(
            latitude=-22.0,
            longitude=44.5,
            heure=BASE + timedelta(hours=5),
            libelle="Mi-parcours",
        ),
        Horizon(
            latitude=-20.0,
            longitude=45.0,
            heure=BASE + timedelta(hours=9),
            libelle="Arrivée",
        ),
    ]

    resultat = service.previsions_trajet(horizons)

    assert resultat.erreur is None
    assert len(client.appels) == 1  # un seul appel pour tout le trajet
    assert [point.condition.heure for point in resultat.points] == [
        BASE + timedelta(hours=2),
        BASE + timedelta(hours=5),
        BASE + timedelta(hours=9),
    ]
    assert all(point.ecart_minutes == 0 for point in resultat.points)
    assert any("aide à la préparation" in texte for texte in resultat.avertissements)


def test_trajet_hors_fenetre_ne_produit_pas_de_prevision():
    service = _service()
    horizon = Horizon(
        latitude=-23.35, longitude=43.66, heure=BASE + timedelta(days=4), libelle="Trop loin"
    )

    resultat = service.previsions_trajet([horizon])

    point = resultat.points[0]
    assert point.condition is None
    assert point.hors_fenetre is True
    assert any("Trop loin" in texte for texte in resultat.avertissements)


def test_trajet_borne_le_nombre_de_points():
    service = _service()
    horizons = [
        Horizon(latitude=-23.0 + index * 0.1, longitude=44.0, heure=BASE + timedelta(hours=1))
        for index in range(20)
    ]

    resultat = service.previsions_trajet(horizons)

    assert len(resultat.points) == 12
    assert any("12 premiers points" in texte for texte in resultat.avertissements)


def test_trajet_signale_un_fournisseur_qui_rend_moins_de_points():
    class Partiel(FauxConditions):
        """Ne répond que pour le premier point (comportement d'un fournisseur fautif)."""

        def points(self, coordonnees, *, heures: int = 24, jour: date | None = None):  # noqa: ANN001, ANN201
            return super().points(list(coordonnees)[:1], heures=heures, jour=jour)

    service = _service(conditions=Partiel())
    horizons = [
        Horizon(latitude=-23.0 + index * 0.2, longitude=44.0, heure=BASE + timedelta(hours=index))
        for index in range(3)
    ]

    resultat = service.previsions_trajet(horizons)

    assert len(resultat.points) == 1
    assert any("n'a renvoyé que 1 point" in texte for texte in resultat.avertissements)


def test_previsions_jour_demande_la_journee_et_la_met_en_cache():
    """Un trajet se prépare par journée : le fournisseur reçoit la date, pas une heure."""
    faux = FauxConditions()
    service = _service(conditions=faux)
    jour = date(2026, 11, 3)
    points = [(-21.45, 47.08), (-18.9, 47.5)]

    premier = service.previsions_jour(points, jour)

    assert premier.disponible
    assert faux.jours == [jour]
    assert len(premier.valeur) == 2
    # Chaque point porte sa journée entière : c'est la carte qui choisira l'heure.
    assert all(len(point.horaires) == 24 for point in premier.valeur)

    second = service.previsions_jour(points, jour)

    assert second.depuis_cache
    assert len(faux.jours) == 1, "une journée déjà demandée ne rappelle pas le fournisseur"


def test_alertes_du_jour_couvrent_la_journee_sans_demander_de_prevision():
    """La veille d'un trajet se lit sur **la journée** choisie, pas sur une heure.

    Une alerte officielle ne dépend pas de la disponibilité du fournisseur de
    prévisions : la chercher ne doit consommer aucun appel météo.
    """
    systeme = SystemeTropical(
        identifiant="1003",
        nom="JOUR-26",
        episode=1,
        niveau="Orange",
        intensite="Tempête tropicale",
        intensite_kmh=90.0,
        position=(-21.0, 45.5),
        du=datetime(2026, 11, 2, tzinfo=UTC),
        au=datetime(2026, 11, 4, tzinfo=UTC),
        publie_le=datetime(2026, 11, 2, 5, tzinfo=UTC),
        source="Météo-France",
        url_bulletin="https://www.gdacs.org/report?id=1003",
        pays="Madagascar",
        actuel=True,
    )
    conditions = FauxConditions()
    service = _service(conditions=conditions, gdacs=FauxGdacs([systeme]))

    alertes = service.alertes_du_jour(
        [(-21.5, 45.0)], date(2026, 11, 3), rayon_km=150
    )

    assert len(alertes) == 1
    assert alertes[0].systeme.nom == "JOUR-26"
    assert alertes[0].distance_km <= 150
    assert conditions.appels == [], "la veille seule ne demande aucune prévision"
    assert service.alertes_du_jour([], date(2026, 11, 3)) == ()


def test_alerte_cyclone_proche_est_signalee_avec_distance():
    systeme = SystemeTropical(
        identifiant="1001",
        nom="TEST-26",
        episode=4,
        niveau="Orange",
        intensite="Cyclone tropical",
        intensite_kmh=150.0,
        position=(-21.0, 45.5),
        du=datetime(2026, 10, 27, tzinfo=UTC),
        au=datetime(2026, 10, 30, tzinfo=UTC),
        publie_le=datetime(2026, 10, 29, 5, tzinfo=UTC),
        source="Météo-France",
        url_bulletin="https://www.gdacs.org/report?id=1001",
        pays="Madagascar",
        actuel=True,
    )
    service = _service(gdacs=FauxGdacs([systeme]))

    resultat = service.previsions_trajet(
        [Horizon(latitude=-21.5, longitude=45.0, heure=BASE + timedelta(hours=3))],
        rayon_alerte_km=150,
    )

    assert len(resultat.alertes) == 1
    alerte = resultat.alertes[0]
    assert alerte.systeme.nom == "TEST-26"
    assert 0 < alerte.distance_km <= 150
    assert alerte.systeme.url_bulletin.startswith("https://www.gdacs.org/")
    assert any("bulletin officiel" in texte for texte in resultat.avertissements)


def test_cyclone_lointain_ne_declenche_pas_d_alerte():
    systeme = SystemeTropical(
        identifiant="1002",
        nom="LOIN-26",
        episode=2,
        niveau="Rouge",
        intensite=None,
        intensite_kmh=None,
        position=(-18.0, 58.0),
        du=None,
        au=None,
        publie_le=datetime(2026, 10, 29, 5, tzinfo=UTC),
        source="GDACS",
        url_bulletin="https://www.gdacs.org/report?id=1002",
        pays=None,
        actuel=True,
    )
    service = _service(gdacs=FauxGdacs([systeme]))

    resultat = service.previsions_trajet(
        [Horizon(latitude=-23.5, longitude=43.5, heure=BASE + timedelta(hours=2))],
        rayon_alerte_km=150,
    )

    assert resultat.alertes == ()


def test_veille_indisponible_laisse_la_meteo_utilisable():
    service = _service(gdacs=FauxGdacs(erreur=ServiceExterneError("GDACS ne répond pas")))

    resultat = service.previsions_trajet(
        [Horizon(latitude=-23.5, longitude=43.5, heure=BASE + timedelta(hours=2))]
    )

    assert resultat.erreur is None
    assert len(resultat.points) == 1
    assert resultat.alertes == ()  # rien d'inventé quand la veille est muette


# ---------------------------------------------------------------------------
# Service — satellite
# ---------------------------------------------------------------------------


def test_couches_satellite_exposent_le_creneau_reel():
    service = _service(satellite=FauxEumetview({BASE}))

    etats = {etat.couche.id: etat for etat in service.couches_satellite()}

    infrarouge = etats["infrarouge"]
    assert infrarouge.disponible is True
    assert infrarouge.dernier_creneau == datetime(2026, 10, 29, 5, 30, tzinfo=UTC)
    # La couche GIBS est journalière : son créneau est une date, pas une heure.
    assert etats["reflectance_journaliere"].disponible is True
    assert etats["reflectance_journaliere"].dernier_creneau is not None


def test_image_recule_d_un_pas_quand_le_creneau_annonce_manque():
    # Les capacités annoncent 05:30, mais seule 05:15 est réellement servie.
    satellite = FauxEumetview({datetime(2026, 10, 29, 5, 15, tzinfo=UTC)})
    service = _service(satellite=satellite)

    resultat = service.image_satellite("infrarouge")

    assert resultat.disponible
    assert resultat.valeur is not None
    # L'heure affichée est celle de l'image reçue, pas celle espérée.
    assert resultat.valeur.heure == datetime(2026, 10, 29, 5, 15, tzinfo=UTC)
    assert len(satellite.demandes) == 2


def test_image_est_mise_en_cache_par_creneau():
    satellite = FauxEumetview({BASE})
    service = _service(satellite=satellite)

    premier = service.image_satellite("infrarouge", BASE)
    second = service.image_satellite("infrarouge", BASE)

    assert premier.disponible and second.disponible
    assert second.depuis_cache is True
    assert len(satellite.demandes) == 1


def test_image_sans_aucun_creneau_disponible_est_expliquee():
    service = _service(satellite=FauxEumetview(set()))

    resultat = service.image_satellite("infrarouge")

    assert resultat.disponible is False
    assert resultat.erreur


def test_couche_inconnue_est_refusee_explicitement():
    with pytest.raises(ServiceExterneError) as erreur:
        couche_par_id("cyclone_imaginaire")

    assert "Couche satellite inconnue" in str(erreur.value)


def test_satellite_desactive_est_explique():
    service = _service(settings=Settings(meteo_satellite_actif=False))

    resultat = service.image_satellite("infrarouge")

    assert resultat.disponible is False
    assert "désactivée" in (resultat.erreur or "")


# ---------------------------------------------------------------------------
# Limiteur de débit
# ---------------------------------------------------------------------------


def test_limiteur_refuse_au_dela_du_plafond_puis_libere():
    seconde = [0.0]

    def horloge() -> float:
        return seconde[0]

    limiteur = LimiteurDebit(2, fenetre_s=60, horloge=horloge)

    assert limiteur.autoriser("eumetsat") is True
    assert limiteur.autoriser("eumetsat") is True
    assert limiteur.autoriser("eumetsat") is False
    assert limiteur.autoriser("gdacs") is True  # compteurs indépendants

    seconde[0] = 61.0
    assert limiteur.autoriser("eumetsat") is True


# ---------------------------------------------------------------------------
# Routes API
# ---------------------------------------------------------------------------


class _ServiceFactice:
    """Service minimal pour exercer les routes sans réseau."""

    def __init__(self) -> None:
        self.conditions_appels: list[tuple[float, float, int]] = []
        self.jour_appels: list[tuple[tuple[tuple[float, float], ...], date]] = []
        self.veille_appels: list[tuple[tuple[tuple[float, float], ...], date]] = []

    @property
    def emprise(self) -> Emprise:
        return EMPRISE

    def conditions(self, latitude: float, longitude: float, *, heures: int = 24):  # noqa: ANN201
        self.conditions_appels.append((latitude, longitude, heures))
        points = interpreter_points(_reponse_open_meteo(latitude, longitude))
        return meteo_service.Resultat(valeur=points)

    def previsions_jour(self, coordonnees, jour):  # noqa: ANN001, ANN201
        self.jour_appels.append((tuple(coordonnees), jour))
        points = [
            point
            for latitude, longitude in coordonnees
            for point in interpreter_points(_reponse_open_meteo(latitude, longitude))
        ]
        return meteo_service.Resultat(valeur=points)

    def alertes_du_jour(self, coordonnees, jour, *, rayon_km: float = 150.0):  # noqa: ANN001, ANN201
        self.veille_appels.append((tuple(coordonnees), jour))
        return ()

    def previsions_trajet(self, horizons, *, rayon_alerte_km: float = 150.0):  # noqa: ANN001, ANN201
        return meteo_service.ResultatTrajet(
            points=(),
            alertes=(),
            avertissements=("attention",),
            fournisseur=FOURNISSEUR_OPEN_METEO,
            recupere_le=BASE,
        )

    def couches_satellite(self):  # noqa: ANN201
        return [
            meteo_service.EtatCouche(
                couche=couche_par_id("infrarouge"),
                disponible=True,
                dernier_creneau=BASE,
            )
        ]

    def image_satellite(self, identifiant: str, heure: datetime | None = None):  # noqa: ANN201
        if identifiant == "infrarouge":
            return meteo_service.Resultat(
                valeur=ImageSatellite(
                    couche=couche_par_id("infrarouge"),
                    heure=BASE,
                    contenu=b"\x89PNG-faux",
                    mime="image/png",
                    emprise=EMPRISE,
                )
            )
        return meteo_service.Resultat(erreur="Image satellite indisponible")

    def systemes_tropicaux(self, *, jours: int | None = None):  # noqa: ANN201
        return meteo_service.Resultat(erreur="Veille cyclonique indisponible")

    def fournisseurs(self):  # noqa: ANN201
        return [
            {
                "cle": "open-meteo",
                "nom": "Open-Meteo",
                "url": "https://open-meteo.com/",
                "licence": "CC BY 4.0",
                "usage": "non_commercial",
                "remarque": "usage non commercial",
                "actif": True,
            }
        ]


@pytest.fixture
def service_factice(monkeypatch: pytest.MonkeyPatch) -> _ServiceFactice:
    factice = _ServiceFactice()
    monkeypatch.setattr(routes_meteo, "MeteoService", lambda *args, **kwargs: factice)
    return factice


def test_route_conditions(api_client: TestClient, service_factice: _ServiceFactice):
    reponse = api_client.get(
        "/api/v1/meteo/conditions", params={"latitude": -23.35, "longitude": 43.66}
    )

    assert reponse.status_code == 200
    corps = reponse.json()
    assert corps["disponible"] is True
    assert corps["point"]["nature_actuelle"] == "estimation"
    assert corps["point"]["actuel"]["temperature_c"] == pytest.approx(24.4)
    assert len(corps["point"]["horaires"]) == 24
    assert corps["point"]["fournisseur"]["licence"].startswith("Données sous licence")
    assert corps["point"]["age_minutes"] is not None
    assert service_factice.conditions_appels == [(-23.35, 43.66, 24)]


def test_interpreter_points_ramene_une_journee_locale_en_utc():
    """Une prévision de journée locale revient en heures locales naïves."""
    bloc = _reponse_open_meteo()
    bloc["timezone"] = "Africa/Nairobi"
    bloc["utc_offset_seconds"] = 10800
    bloc["hourly"]["time"] = [
        f"2026-11-03T{heure:02d}:00" for heure in range(len(bloc["hourly"]["time"]))
    ]

    point = interpreter_points(bloc)[0]

    # 00:00 à Toliara (UTC+3) = 21:00 UTC la veille, et la journée garde ses 24 heures.
    assert point.fuseau == "Africa/Nairobi"
    assert point.horaires[0].heure == datetime(2026, 11, 2, 21, tzinfo=UTC)
    assert len(point.horaires) == 24
    assert point.horaires[-1].heure == datetime(2026, 11, 3, 20, tzinfo=UTC)


def test_client_open_meteo_demande_la_journee_locale():
    """Le fournisseur reçoit la date et le fuseau local : pas une fenêtre GMT."""
    vues: list[dict[str, str]] = []

    def repondre(requete: httpx.Request) -> httpx.Response:
        vues.append(dict(requete.url.params))
        return httpx.Response(200, json=_reponse_open_meteo())

    client = ClientOpenMeteo(
        "https://exemple.test", timeout_s=5.0, transport=httpx.MockTransport(repondre)
    )

    client.points([(-23.35, 43.66)], jour=date(2026, 11, 3))

    assert vues and vues[0]["start_date"] == "2026-11-03"
    assert vues[0]["end_date"] == "2026-11-03"
    assert vues[0]["timezone"] == "auto"
    assert "current" not in vues[0], "« maintenant » ne décrit pas une journée à venir"


def test_route_trajet_jour_rend_24_heures_par_point(
    api_client: TestClient, service_factice: _ServiceFactice
):
    """Le trajet par jour : 24 h par point, libellé conservé, âge calculé."""
    reponse = api_client.post(
        "/api/v1/meteo/trajet/jour",
        json={
            "date": "2026-11-03",
            "points": [
                {"latitude": -23.35, "longitude": 43.66, "libelle": "Départ"},
                {"latitude": -21.45, "longitude": 47.08, "libelle": "Arrivée"},
            ],
        },
    )

    assert reponse.status_code == 200
    corps = reponse.json()
    assert corps["disponible"] is True
    assert [point["libelle"] for point in corps["points"]] == ["Départ", "Arrivée"]
    assert all(len(point["horaires"]) == 24 for point in corps["points"])
    # L'âge vient du schéma, pas du point métier : c'est ce qui manquait.
    assert corps["age_minutes"] is not None
    assert corps["alertes"] == []
    assert any("journée entière" in texte for texte in corps["avertissements"])
    assert service_factice.jour_appels[0][1] == date(2026, 11, 3)
    assert service_factice.veille_appels[0][1] == date(2026, 11, 3)


def test_route_conditions_ne_casse_pas_sur_une_reponse_vide(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch
):  # noqa: ANN001
    class Vide(_ServiceFactice):
        def conditions(self, latitude: float, longitude: float, *, heures: int = 24):  # noqa: ANN201
            return meteo_service.Resultat(valeur=[])

    monkeypatch.setattr(routes_meteo, "MeteoService", lambda *args, **kwargs: Vide())

    reponse = api_client.get(
        "/api/v1/meteo/conditions", params={"latitude": -23.35, "longitude": 43.66}
    )

    assert reponse.status_code == 200
    assert reponse.json()["disponible"] is False
    assert reponse.json()["erreur"]


def test_route_conditions_valide_les_bornes(api_client: TestClient, service_factice):  # noqa: ANN001
    reponse = api_client.get(
        "/api/v1/meteo/conditions", params={"latitude": 120, "longitude": 43.66}
    )

    assert reponse.status_code == 422


def test_route_image_satellite_porte_l_horodatage(
    api_client: TestClient, service_factice
):  # noqa: ANN001
    reponse = api_client.get("/api/v1/meteo/satellite/image", params={"couche": "infrarouge"})

    assert reponse.status_code == 200
    assert reponse.headers["content-type"].startswith("image/png")
    assert reponse.headers["x-donnee-heure"].startswith("2026-10-29T06:00")
    assert reponse.content.startswith(b"\x89PNG")


def test_route_image_indisponible_repond_502(api_client: TestClient, service_factice):  # noqa: ANN001
    reponse = api_client.get(
        "/api/v1/meteo/satellite/image", params={"couche": "couleurs_naturelles"}
    )

    assert reponse.status_code == 502
    assert reponse.json()["error"]["code"] == "service_externe"


def test_route_image_refuse_une_couche_hors_catalogue(
    api_client: TestClient, service_factice
):  # noqa: ANN001
    reponse = api_client.get("/api/v1/meteo/satellite/image", params={"couche": "truc"})

    assert reponse.status_code == 502
    assert "Couche satellite inconnue" in reponse.json()["error"]["message"]


def test_route_cyclones_conserve_les_liens_officiels(api_client: TestClient, service_factice):  # noqa: ANN001
    reponse = api_client.get("/api/v1/meteo/cyclones")

    assert reponse.status_code == 200
    corps = reponse.json()
    assert corps["disponible"] is False
    assert "indisponible" in corps["erreur"].lower()
    assert corps["systemes"] == []
    urls = {lien["url"] for lien in corps["liens_officiels"]}
    assert "https://www.meteomadagascar.mg/cyclone/" in urls
    assert "https://meteofrance.re/fr/cyclone" in urls


def test_route_couches_satellite_donne_l_emprise(api_client: TestClient, service_factice):  # noqa: ANN001
    reponse = api_client.get("/api/v1/meteo/satellite/couches")

    assert reponse.status_code == 200
    corps = reponse.json()
    assert corps["emprise"] == [-30.0, 30.0, 2.0, 60.0]
    assert corps["couches"][0]["disponible"] is True
    assert corps["couches"][0]["dernier_creneau"].startswith("2026-10-29T06:00")


def test_route_sources_expose_licences(api_client: TestClient, service_factice):  # noqa: ANN001
    reponse = api_client.get("/api/v1/meteo/sources")

    assert reponse.status_code == 200
    fournisseurs = reponse.json()["fournisseurs"]
    assert fournisseurs[0]["usage"] == "non_commercial"


def test_route_trajet_accepte_des_points_et_heures(api_client: TestClient, service_factice):  # noqa: ANN001
    reponse = api_client.post(
        "/api/v1/meteo/trajet",
        json={
            "points": [
                {
                    "latitude": -23.35,
                    "longitude": 43.66,
                    "heure": "2026-10-29T09:00:00",
                    "libelle": "Départ",
                }
            ],
            "rayon_alerte_km": 120,
        },
    )

    assert reponse.status_code == 200
    corps = reponse.json()
    assert corps["avertissements"] == ["attention"]
    assert corps["fournisseur"]["cle"] == "open-meteo"


# ---------------------------------------------------------------------------
# Contrat du client EUMETView (sans réseau)
# ---------------------------------------------------------------------------


def test_client_eumetview_transmet_l_heure_et_l_emprise():
    demandes: list[dict] = []

    def repondre(request: httpx.Request) -> httpx.Response:
        demandes.append(dict(request.url.params))
        return httpx.Response(200, content=b"image", headers={"content-type": "image/png"})

    client = ClientEumetview(
        "https://exemple.test/wms", timeout_s=5.0, transport=httpx.MockTransport(repondre)
    )
    image = client.image(
        couche_par_id("infrarouge"), datetime(2026, 10, 29, 5, 30, tzinfo=UTC), EMPRISE
    )

    assert image.mime == "image/png"
    assert demandes[0]["time"] == "2026-10-29T05:30:00Z"
    assert demandes[0]["bbox"] == "30.0,-30.0,60.0,2.0"
    assert demandes[0]["crs"] == "CRS:84"
    assert demandes[0]["layers"] == "msg_iodc:ir108"


def test_client_eumetview_refuse_une_reponse_non_image():
    def repondre(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        return httpx.Response(
            200, content=b"<ExceptionReport/>", headers={"content-type": "text/xml"}
        )

    client = ClientEumetview(
        "https://exemple.test/wms", timeout_s=5.0, transport=httpx.MockTransport(repondre)
    )

    with pytest.raises(ServiceExterneError):
        client.image(
            couche_par_id("infrarouge"), datetime(2026, 10, 29, 5, 30, tzinfo=UTC), EMPRISE
        )
