"""Schémas API — recherche de lieux, itinéraires, paramètres de voyage.

Les bornes exposées ici sont **volontairement larges** : la règle (paires de
coordonnées complètes, consommation bornée, point ambigu) vit dans les
services et rend un message actionnable. Ici, on refuse un corps absurde.
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class LieuTrouveRead(BaseModel):
    """Résultat de recherche normalisé (le format Photon reste au backend)."""

    nom: str
    adresse: str | None = None
    ville: str | None = None
    pays: str | None = None
    latitude: float
    longitude: float
    osm_type: str | None = None
    osm_id: int | None = None
    libelle: str


class PointItineraire(BaseModel):
    """Un bout du trajet : un lieu référencé OU des coordonnées libres."""

    lieu_id: UUID | None = None
    libelle: str | None = Field(default=None, max_length=255)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)


class ItineraireCreate(BaseModel):
    """Demande d'itinéraire : départ, arrivée, aller-retour, carburant."""

    origine: PointItineraire
    destination: PointItineraire
    aller_retour: bool = False
    consommation_l_100km: float | None = Field(default=None, gt=0, le=60)
    prix_litre: float | None = Field(default=None, ge=0, le=10_000_000)


class TronconItineraireRead(BaseModel):
    """Portion continue d'un itinéraire, classée d'après OpenStreetMap.

    ``debut``/``fin`` sont des indices **inclusifs** dans ``geometrie`` : la
    carte découpe le tracé sans trou à la jointure.
    """

    debut: int
    fin: int
    osmid: int | None = None
    niveau: Literal["bon", "tolerable", "mauvais", "impraticable", "inconnu"]
    #: Nature de la voie — axe indépendant de la qualité (affichage « type »).
    type_route: Literal[
        "autoroute",
        "route_principale",
        "route_secondaire",
        "rue",
        "piste",
        "chemin",
        "bac",
        "construction",
        "inconnu",
    ] = "inconnu"
    nature: Literal["physique", "reglementaire", "inconnue"]
    raison: str
    #: Vrai quand le niveau vient d'un indicateur secondaire (``tracktype``).
    estime: bool = False
    tags: dict[str, str] = Field(default_factory=dict)
    distance_m: int = 0


class ItineraireRead(BaseModel):
    """Résultat affichable : trajet, carburant, tracé, provenance."""

    distance_m: int
    duree_s: int
    aller_retour: bool
    distance_totale_m: int
    duree_totale_s: int
    litres: float
    cout: float
    devise: str
    origine_libelle: str
    destination_libelle: str
    geometrie: list[list[float]] | None = None
    depuis_cache: bool
    #: Qualité des portions selon OSM (vide si aucune donnée exploitable).
    troncons: list[TronconItineraireRead] = Field(default_factory=list)


class ParametresVoyageRead(BaseModel):
    """Réglages de voyage — lecture pour tous, modification par l'admin."""

    model_config = ConfigDict(from_attributes=True)

    origine_libelle: str | None = None
    origine_latitude: float | None = None
    origine_longitude: float | None = None
    consommation_l_100km: float
    prix_litre: float
    devise: str


class ParametresVoyageUpdate(BaseModel):
    """Modification des réglages — un champ absent est conservé.

    Une origine complète à ``null`` retire le départ par défaut : le formulaire
    demandera alors une origine à chaque calcul (décision explicite).
    """

    origine_libelle: str | None = Field(default=None, max_length=255)
    origine_latitude: float | None = Field(default=None, ge=-90, le=90)
    origine_longitude: float | None = Field(default=None, ge=-180, le=180)
    consommation_l_100km: float | None = Field(default=None, gt=0, le=60)
    prix_litre: float | None = Field(default=None, ge=0, le=10_000_000)
    devise: str | None = Field(default=None, min_length=1, max_length=10)


__all__ = [
    "ItineraireCreate",
    "ItineraireRead",
    "LieuTrouveRead",
    "ParametresVoyageRead",
    "ParametresVoyageUpdate",
    "PointItineraire",
    "TronconItineraireRead",
]
