"""Géographie : recherche de lieux et itinéraires routiers.

Deux clients isolés (Photon, OpenRouteService), appelés par les services
applicatifs — jamais par une route ni par un agent directement. La clé
OpenRouteService vit dans la configuration serveur et ne sort jamais du
backend.
"""

from app.geo.openrouteservice import ClientItineraires, ItineraireCalcule
from app.geo.photon import LieuTrouve, RechercheLieux

__all__ = [
    "ClientItineraires",
    "ItineraireCalcule",
    "LieuTrouve",
    "RechercheLieux",
]
