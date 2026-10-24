"""Tools de lieux partagés par **tous les agents du catalogue UI**.

Deux besoins exprimés par les utilisateurs, deux tools :

- ``rechercher_lieu`` — trouver un lieu ou une adresse (Photon, via le backend) ;
  la réponse est lue par le **modèle**, qui peut ensuite la citer ;
- ``calculer_itineraire`` — distance routière, durée, litres et coût entre deux
  points (cache puis OpenRouteService) ; le calcul appartient au service, jamais
  au modèle.

Ces tools sont en **lecture seule** (aucune écriture de lieu ici) : ils sont
donc utilisables par l'agent généraliste, qui l'est aussi. L'affichage sur une
carte reste le role de l'outil frontend ``afficher_carte`` : on cherche, on
calcule, puis on montre.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.agents.toolkit import AgentTools, OperationSpec
from app.application.services.itineraire_service import ItineraireService, PointItineraire
from app.core.config import get_settings

NOMS_TOOLS: tuple[str, ...] = ("rechercher_lieu", "calculer_itineraire")

_LIMITE_MAX = 10
_REQUETE_MAX = 200


class RechercheLieuInput(BaseModel):
    """Recherche d'un lieu par nom ou adresse."""

    requete: str = Field(min_length=2, max_length=_REQUETE_MAX)
    limite: int = Field(default=5, ge=1, le=_LIMITE_MAX)


class PointLieuInput(BaseModel):
    """Un bout du trajet : un lieu du référentiel **ou** des coordonnées libres."""

    lieu_id: UUID | None = None
    libelle: str | None = Field(default=None, max_length=255)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)


class ItineraireLieuInput(BaseModel):
    """Trajet dont la distance et le carburant sont calculés par le service."""

    origine: PointLieuInput
    destination: PointLieuInput
    aller_retour: bool = False
    consommation_l_100km: float | None = Field(default=None, gt=0, le=60)
    prix_litre: float | None = Field(default=None, ge=0, le=10_000_000)


def _rechercher_lieu(_session: Session, payload: dict[str, Any]) -> dict[str, Any]:
    """Recherche Photon — aucune écriture, aucun lieu créé."""
    from app.geo.photon import RechercheLieux

    reglages = get_settings()
    trouves = RechercheLieux(
        reglages.photon_base_url, timeout_s=reglages.photon_timeout_s
    ).chercher(
        payload["requete"],
        limite=int(payload.get("limite") or 5),
    )
    return {
        "resultats": [
            {
                "nom": trouve.nom,
                "adresse": trouve.adresse,
                "ville": trouve.ville,
                "pays": trouve.pays,
                "latitude": trouve.latitude,
                "longitude": trouve.longitude,
                "libelle": trouve.libelle,
            }
            for trouve in trouves
        ],
        "note": (
            "Résultats de recherche d'adresses : une position proposée, pas un "
            "lieu officiel du référentiel. Citer le libellé tel quel."
        ),
    }


def _calculer_itineraire(session: Session, payload: dict[str, Any]) -> dict[str, Any]:
    """Distance et carburant via ``ItineraireService`` (cache ORS compris)."""
    resultat = ItineraireService(session).calculer(
        origine=PointItineraire(**payload["origine"]),
        destination=PointItineraire(**payload["destination"]),
        aller_retour=bool(payload.get("aller_retour")),
        consommation_l_100km=payload.get("consommation_l_100km"),
        prix_litre=payload.get("prix_litre"),
    )
    # Qualité et type par portion : les chiffres viennent des tronçons
    # classés, pas d'une estimation du modèle.
    qualite: dict[str, int] = {}
    types: dict[str, int] = {}
    for troncon in resultat.troncons:
        qualite[troncon.niveau] = qualite.get(troncon.niveau, 0) + troncon.distance_m
        types[troncon.type_route] = types.get(troncon.type_route, 0) + troncon.distance_m
    return {
        "distance_m": resultat.distance_m,
        "duree_s": resultat.duree_s,
        "aller_retour": resultat.aller_retour,
        "distance_totale_m": resultat.distance_totale_m,
        "duree_totale_s": resultat.duree_totale_s,
        "litres": resultat.litres,
        "cout": resultat.cout,
        "devise": resultat.devise,
        "origine_libelle": resultat.origine_libelle,
        "destination_libelle": resultat.destination_libelle,
        "depuis_cache": resultat.depuis_cache,
        # Deux axes distincts : état (smoothness/accès) et type de voie.
        "qualite": qualite,
        "types": types,
        "troncons": len(resultat.troncons),
        "note": (
            "Calcul du service (cache puis OpenRouteService). Un coût à 0 avec un "
            "prix du litre absent n'est pas un trajet gratuit : dire « non chiffré »."
        ),
    }


def construire_tools_lieux(definition: Any, session_factory: Any) -> tuple[Any, ...]:
    """Tools lieux du contrat de l'agent (allow-list appliquée par le harnais)."""
    autorises = set(definition.allowed_tools())
    if not autorises.intersection(NOMS_TOOLS):
        return ()
    kit = AgentTools(definition, readonly=True, session_factory=session_factory)
    if "rechercher_lieu" in autorises:
        kit.enregistrer(
            OperationSpec(
                name="rechercher_lieu",
                description=(
                    "Cherche un lieu ou une adresse (ville, quartier, rue) et rend "
                    "nom, adresse, ville, pays et coordonnées. Lecture seule : "
                    "choisir un résultat ne crée aucun lieu."
                ),
                input_schema=RechercheLieuInput,
                run=_rechercher_lieu,
            )
        )
    if "calculer_itineraire" in autorises:
        kit.enregistrer(
            OperationSpec(
                name="calculer_itineraire",
                description=(
                    "Calcule la distance routière, la durée, les litres de carburant "
                    "et le coût entre deux points (lieu_id, ou coordonnées), avec la "
                    "distance par état de route (qualite) et par type de voie (types). "
                    "Un lieu sans position est refusé avec un message actionnable."
                ),
                input_schema=ItineraireLieuInput,
                run=_calculer_itineraire,
            )
        )
    return kit.construire()


__all__ = [
    "NOMS_TOOLS",
    "ItineraireLieuInput",
    "PointLieuInput",
    "RechercheLieuInput",
    "construire_tools_lieux",
]
