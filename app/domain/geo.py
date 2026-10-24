"""Géographie — paramètres de voyage et cache des itinéraires.

Deux agrégats, deux raisons distinctes :

- ``ParametresVoyage`` porte les **valeurs par défaut d'une organisation** :
  point de départ (siège CARSO) et coût du carburant. Une seule ligne : ce sont
  des réglages d'administration, pas une donnée métier par mission. Le calcul
  peut les surcharger ponctuellement (consommation d'un autre véhicule).
- ``ItineraireCache`` conserve le **résultat payant** d'un calcul
  OpenRouteService : distance, durée et tracé. Le quota gratuit est limité, un
  même trajet est consulté plusieurs fois (carte, fiche mission, réimpression),
  et un itinéraire ne change pas tant que les coordonnées ne changent pas.

Le cache est une table, pas un dictionnaire en mémoire : il survit aux
redémarrages et se partage entre instances. Une ligne expirée n'est jamais
servie ; elle est remplacée au prochain calcul du même trajet.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import BigInteger, Boolean, DateTime, Float, Index, Numeric, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.base import Base, TimestampMixin, UuidPkMixin

#: Identifiant fixe de la ligne unique de paramètres (migration 0025).
PARAMETRES_VOYAGE_ID = UUID("00000000-0000-4000-8000-00000000a11e")

#: Profil de calcul par défaut. Un seul profil est exposé : les missions CARSO
#: se déplacent en véhicule léger.
PROFIL_ITINERAIRE = "driving-car"


class ParametresVoyage(Base, UuidPkMixin, TimestampMixin):
    """Réglages de voyage de l'organisation (une seule ligne)."""

    __tablename__ = "parametres_voyage"

    #: Libellé du point de départ (« Siège CARSO — Toliara »), pour l'affichage.
    origine_libelle: Mapped[str | None] = mapped_column(String(255))
    origine_latitude: Mapped[float | None] = mapped_column(Float)
    origine_longitude: Mapped[float | None] = mapped_column(Float)
    #: Consommation par défaut du véhicule (L/100 km), bornée par le service.
    consommation_l_100km: Mapped[float] = mapped_column(
        Numeric(5, 2), nullable=False, default=8.0
    )
    #: Prix du litre (devise de l'organisation), borné par le service.
    prix_litre: Mapped[float] = mapped_column(
        Numeric(12, 2), nullable=False, default=0
    )
    devise: Mapped[str] = mapped_column(String(10), nullable=False, default="MGA")


class ItineraireCache(Base, UuidPkMixin, TimestampMixin):
    """Résultat d'un calcul d'itinéraire, réutilisable jusqu'à expiration.

    ``cle`` est l'empreinte des **entrées du calcul** (profil, coordonnées
    normalisées, aller-retour). Deux demandes identiques partagent la ligne ;
    un déplacement du marqueur change les coordonnées, donc la clé : l'ancien
    résultat n'est jamais servi pour un autre point.
    """

    __tablename__ = "itineraires_cache"
    __table_args__ = (
        Index("uq_itineraires_cache_cle", "cle", unique=True),
        Index("ix_itineraires_cache_expire_le", "expire_le"),
    )

    cle: Mapped[str] = mapped_column(String(64), nullable=False)
    profil: Mapped[str] = mapped_column(String(30), nullable=False)
    origine_latitude: Mapped[float] = mapped_column(Float, nullable=False)
    origine_longitude: Mapped[float] = mapped_column(Float, nullable=False)
    destination_latitude: Mapped[float] = mapped_column(Float, nullable=False)
    destination_longitude: Mapped[float] = mapped_column(Float, nullable=False)
    aller_retour: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    #: Distance du trajet simple (mètres) ; l'aller-retour se calcule à l'affichage.
    distance_m: Mapped[int] = mapped_column(nullable=False)
    duree_s: Mapped[int] = mapped_column(nullable=False)
    #: Tracé GeoJSON de la géométrie ORS (liste de [longitude, latitude]).
    geometrie: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    expire_le: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class OsmVoie(Base, UuidPkMixin, TimestampMixin):
    """Tags OpenStreetMap d'une voie, gardés pour la qualité des routes.

    Une seule ligne par ``osm_id`` : un trajet réemprunté ne redemande pas les
    mêmes tags à Overpass (service public, à ménager). Les tags sont la donnée
    source — la **classification** est recalculée à la lecture, pour qu'une
    règle corrigée s'applique sans vider le cache.

    Expiration courte et séparée du cache d'itinéraires : OSM change (une piste
    goudronnée le devient), un trajet ne change pas.
    """

    __tablename__ = "osm_voies"
    __table_args__ = (Index("uq_osm_voies_osm_id", "osm_id", unique=True),)

    osm_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    tags: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    expire_le: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


__all__ = [
    "PARAMETRES_VOYAGE_ID",
    "PROFIL_ITINERAIRE",
    "ItineraireCache",
    "OsmVoie",
    "ParametresVoyage",
]
