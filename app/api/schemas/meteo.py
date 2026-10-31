"""Schémas API — météo, imagerie satellite et phénomènes tropicaux.

Deux familles de champs, toujours ensemble :

- la **donnée** (température, image, position d'un système) ;
- sa **provenance** (fournisseur, licence, instant de validité, âge).

L'absence est explicite : ``disponible: false`` avec un ``erreur`` lisible, ou
un champ météo à ``null``. Aucun zéro de remplissage — l'interface ne doit
jamais afficher « 0 °C » là où le fournisseur n'a rien dit.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.application.services.meteo_service import (
    AlerteCyclone,
    EtatCouche,
    PointPrevu,
    ResultatTrajet,
)
from app.geo.meteo import Condition, FournisseurInfo, MeteoPoint, SystemeTropical


def _age_minutes(instant: datetime | None) -> int | None:
    """Âge d'une donnée, en minutes (``None`` si l'instant est absent)."""
    if instant is None:
        return None
    ecart = datetime.now(UTC) - instant
    return max(0, int(round(ecart.total_seconds() / 60)))


class FournisseurRead(BaseModel):
    """Provenance et conditions d'usage d'une source."""

    cle: str
    nom: str
    url: str
    licence: str
    usage: Literal["libre", "non_commercial", "service_public", "a_verifier"]
    remarque: str = ""
    actif: bool = True

    @classmethod
    def depuis(cls, info: FournisseurInfo, *, actif: bool = True) -> FournisseurRead:
        return cls(
            cle=info.cle,
            nom=info.nom,
            url=info.url,
            licence=info.licence,
            usage=info.usage,
            remarque=info.remarque,
            actif=actif,
        )


class SourcesRead(BaseModel):
    """Catalogue des fournisseurs et de leur état (écran « sources »)."""

    fournisseurs: list[FournisseurRead]


class ConditionRead(BaseModel):
    """État de l'atmosphère à un instant (UTC)."""

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
    libelle: str
    categorie: str

    @classmethod
    def depuis(cls, condition: Condition) -> ConditionRead:
        return cls(
            heure=condition.heure,
            temperature_c=condition.temperature_c,
            ressenti_c=condition.ressenti_c,
            humidite_pct=condition.humidite_pct,
            vent_kmh=condition.vent_kmh,
            rafales_kmh=condition.rafales_kmh,
            direction_deg=condition.direction_deg,
            pluie_mm=condition.pluie_mm,
            probabilite_pluie_pct=condition.probabilite_pluie_pct,
            couverture_pct=condition.couverture_pct,
            code=condition.code,
            libelle=condition.libelle,
            categorie=condition.categorie,
        )


class MeteoPointRead(BaseModel):
    """Conditions et prévision d'un point, avec l'âge de la donnée."""

    latitude: float
    longitude: float
    fuseau: str
    altitude_m: float | None = None
    nature_actuelle: Literal["estimation", "observation"]
    actuel: ConditionRead | None = None
    horaires: list[ConditionRead] = Field(default_factory=list)
    emis_le: datetime | None = None
    recupere_le: datetime
    age_minutes: int | None = None
    fournisseur: FournisseurRead

    @classmethod
    def depuis(cls, point: MeteoPoint) -> MeteoPointRead:
        actuel = ConditionRead.depuis(point.actuel) if point.actuel else None
        return cls(
            latitude=point.latitude,
            longitude=point.longitude,
            fuseau=point.fuseau,
            altitude_m=point.altitude_m,
            nature_actuelle=point.nature_actuelle,
            actuel=actuel,
            horaires=[ConditionRead.depuis(condition) for condition in point.horaires],
            emis_le=point.emis_le,
            recupere_le=point.recupere_le,
            age_minutes=_age_minutes(point.actuel.heure if point.actuel else point.emis_le),
            fournisseur=FournisseurRead.depuis(point.fournisseur),
        )


class PointMeteoRead(BaseModel):
    """Réponse d'un point : la donnée **ou** la raison de son absence."""

    disponible: bool
    erreur: str | None = None
    depuis_cache: bool = False
    point: MeteoPointRead | None = None


class HorizonCreate(BaseModel):
    """Un point du déplacement et l'heure à laquelle on y sera."""

    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    heure: datetime
    libelle: str | None = Field(default=None, max_length=120)


class TrajetCreate(BaseModel):
    """Demande de prévisions le long d'un déplacement."""

    points: list[HorizonCreate] = Field(min_length=1, max_length=24)
    rayon_alerte_km: float = Field(default=150.0, ge=10, le=1000)


class PointPrevuRead(BaseModel):
    """Prévision retenue pour une étape, avec l'écart à l'heure voulue."""

    latitude: float
    longitude: float
    libelle: str | None = None
    heure_souhaitee: datetime
    condition: ConditionRead | None = None
    ecart_minutes: int | None = None
    hors_fenetre: bool

    @classmethod
    def depuis(cls, point: PointPrevu) -> PointPrevuRead:
        return cls(
            latitude=point.latitude,
            longitude=point.longitude,
            libelle=point.libelle,
            heure_souhaitee=point.heure_souhaitee,
            condition=ConditionRead.depuis(point.condition) if point.condition else None,
            ecart_minutes=point.ecart_minutes,
            hors_fenetre=point.hors_fenetre,
        )


class AlerteCycloneRead(BaseModel):
    """Phénomène tropical proche du trajet, avec son bulletin officiel."""

    identifiant: str
    nom: str
    niveau: str
    intensite: str | None = None
    intensite_kmh: float | None = None
    distance_km: float
    dans_la_periode: bool
    source: str
    url_bulletin: str
    publie_le: datetime | None = None
    age_heures: float | None = None

    @classmethod
    def depuis(cls, alerte: AlerteCyclone) -> AlerteCycloneRead:
        systeme = alerte.systeme
        return cls(
            identifiant=systeme.identifiant,
            nom=systeme.nom,
            niveau=systeme.niveau,
            intensite=systeme.intensite,
            intensite_kmh=systeme.intensite_kmh,
            distance_km=alerte.distance_km,
            dans_la_periode=alerte.dans_la_periode,
            source=systeme.source,
            url_bulletin=systeme.url_bulletin,
            publie_le=systeme.publie_le,
            age_heures=systeme.age_heures,
        )


class TrajetRead(BaseModel):
    """Prévisions d'un déplacement : étapes, alertes, avertissements."""

    disponible: bool
    erreur: str | None = None
    depuis_cache: bool = False
    fournisseur: FournisseurRead
    recupere_le: datetime | None = None
    #: Âge de la donnée au moment de la réponse (calculé côté serveur : le
    #: client n'a pas à deviner l'horloge).
    age_minutes: int | None = None
    points: list[PointPrevuRead] = Field(default_factory=list)
    alertes: list[AlerteCycloneRead] = Field(default_factory=list)
    avertissements: list[str] = Field(default_factory=list)

    @classmethod
    def depuis(cls, resultat: ResultatTrajet) -> TrajetRead:
        return cls(
            disponible=resultat.erreur is None and bool(resultat.points),
            erreur=resultat.erreur,
            depuis_cache=resultat.depuis_cache,
            fournisseur=FournisseurRead.depuis(resultat.fournisseur),
            recupere_le=resultat.recupere_le,
            age_minutes=_age_minutes(resultat.recupere_le),
            points=[PointPrevuRead.depuis(point) for point in resultat.points],
            alertes=[AlerteCycloneRead.depuis(alerte) for alerte in resultat.alertes],
            avertissements=list(resultat.avertissements),
        )


class CoucheSatelliteRead(BaseModel):
    """Une couche d'imagerie et son dernier créneau réellement publié."""

    id: str
    libelle: str
    description: str
    famille: Literal["couleurs", "infrarouge", "nuages", "convection", "precipitation"]
    pas_minutes: int
    visible_de_nuit: bool
    journaliere: bool
    fournisseur: FournisseurRead
    disponible: bool
    dernier_creneau: datetime | None = None
    age_minutes: int | None = None
    erreur: str | None = None

    @classmethod
    def depuis(cls, etat: EtatCouche) -> CoucheSatelliteRead:
        couche = etat.couche
        return cls(
            id=couche.id,
            libelle=couche.libelle,
            description=couche.description,
            famille=couche.famille,
            pas_minutes=couche.pas_minutes,
            visible_de_nuit=couche.visible_de_nuit,
            journaliere=couche.journaliere,
            fournisseur=FournisseurRead.depuis(couche.fournisseur),
            disponible=etat.disponible,
            dernier_creneau=etat.dernier_creneau,
            age_minutes=_age_minutes(etat.dernier_creneau),
            erreur=etat.erreur,
        )


class CouchesSatelliteRead(BaseModel):
    """Catalogue des couches + emprise des images (pour la carte)."""

    couches: list[CoucheSatelliteRead]
    emprise: list[float] = Field(
        description="[sud, ouest, nord, est] — bornes de l'image demandée.",
    )


class PolygoneVentRead(BaseModel):
    """Zone de vent publiée par GDACS (actuelle ou prévue)."""

    niveau: str
    libelle: str
    prevu_le: datetime | None = None
    #: Anneau extérieur en ``[latitude, longitude]`` (format Leaflet).
    positions: list[list[float]]


class SystemeTropicalRead(BaseModel):
    """Système tropical officiel — jamais déduit d'une image satellite."""

    identifiant: str
    nom: str
    episode: int
    niveau: str
    intensite: str | None = None
    intensite_kmh: float | None = None
    latitude: float | None = None
    longitude: float | None = None
    du: datetime | None = None
    au: datetime | None = None
    publie_le: datetime | None = None
    age_heures: float | None = None
    source: str
    url_bulletin: str
    pays: str | None = None
    actuel: bool
    polygones: list[PolygoneVentRead] = Field(default_factory=list)
    fournisseur: FournisseurRead

    @classmethod
    def depuis(cls, systeme: SystemeTropical) -> SystemeTropicalRead:
        return cls(
            identifiant=systeme.identifiant,
            nom=systeme.nom,
            episode=systeme.episode,
            niveau=systeme.niveau,
            intensite=systeme.intensite,
            intensite_kmh=systeme.intensite_kmh,
            latitude=systeme.position[0] if systeme.position else None,
            longitude=systeme.position[1] if systeme.position else None,
            du=systeme.du,
            au=systeme.au,
            publie_le=systeme.publie_le,
            age_heures=systeme.age_heures,
            source=systeme.source,
            url_bulletin=systeme.url_bulletin,
            pays=systeme.pays,
            actuel=systeme.actuel,
            polygones=[
                PolygoneVentRead(
                    niveau=polygone.niveau,
                    libelle=polygone.libelle,
                    prevu_le=polygone.prevu_le,
                    positions=[[latitude, longitude] for latitude, longitude in polygone.positions],
                )
                for polygone in systeme.polygones
            ],
            fournisseur=FournisseurRead.depuis(systeme.fournisseur),
        )


class CyclonesRead(BaseModel):
    """Veille cyclonique de la région."""

    disponible: bool
    erreur: str | None = None
    depuis_cache: bool = False
    #: Horodatage de la dernière lecture du fournisseur (fraîcheur).
    recupere_le: datetime
    systemes: list[SystemeTropicalRead] = Field(default_factory=list)
    #: Liens officiels à consulter quoi qu'il arrive (aucune donnée inventée).
    liens_officiels: list[dict[str, str]] = Field(default_factory=list)


class PointRouteCreate(BaseModel):
    """Un point du tracé dont on demande la journée de prévision."""

    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    libelle: str | None = Field(default=None, max_length=120)


class RouteJourCreate(BaseModel):
    """Une journée demandée et les points du tracé (un seul appel fournisseur)."""

    date: date
    #: Le fournisseur accepte un lot : on ne le dépasse pas (12 points).
    points: list[PointRouteCreate] = Field(min_length=1, max_length=12)


class PointRouteRead(BaseModel):
    """Un point du tracé et ses 24 heures prévues."""

    latitude: float
    longitude: float
    libelle: str | None = None
    horaires: list[ConditionRead] = Field(default_factory=list)

    @classmethod
    def depuis(cls, point: MeteoPoint, libelle: str | None) -> PointRouteRead:
        return cls(
            latitude=point.latitude,
            longitude=point.longitude,
            libelle=libelle,
            horaires=[ConditionRead.depuis(condition) for condition in point.horaires],
        )


class RouteJourRead(BaseModel):
    """Prévision d'une journée pour chaque point d'un trajet.

    Les alertes cycloniques accompagnent la réponse même quand la prévision
    manque : une source indisponible ne doit pas faire disparaître un avis de
    sécurité officiel.
    """

    disponible: bool
    erreur: str | None = None
    depuis_cache: bool = False
    fournisseur: FournisseurRead
    recupere_le: datetime | None = None
    age_minutes: int | None = None
    points: list[PointRouteRead] = Field(default_factory=list)
    alertes: list[AlerteCycloneRead] = Field(default_factory=list)
    avertissements: list[str] = Field(default_factory=list)


__all__ = [
    "AlerteCycloneRead",
    "ConditionRead",
    "CoucheSatelliteRead",
    "CouchesSatelliteRead",
    "CyclonesRead",
    "FournisseurRead",
    "HorizonCreate",
    "MeteoPointRead",
    "PointMeteoRead",
    "PointPrevuRead",
    "PointRouteCreate",
    "PointRouteRead",
    "PolygoneVentRead",
    "RouteJourCreate",
    "RouteJourRead",
    "SourcesRead",
    "SystemeTropicalRead",
    "TrajetCreate",
    "TrajetRead",
]
