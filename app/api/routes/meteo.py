"""Routes—météo, imagerie satellite et veille cyclonique.

**Lecture seule** : aucune de ces routes n'écrit en base ni ne modifie une
donnée métier. Elles sont donc ouvertes à tout compte authentifié — un
formateur prépare son déplacement comme l'équipe interne.

Deux principes de conception :

1. **le backend est le seul à parler aux fournisseurs** — pas de clé, pas
   d'appel direct du navigateur à un service tiers, donc pas de contournement
   CORS : les images satellite reviennent en flux binaire par
   ``/meteo/satellite/image``. L'interface les charge en ``fetch`` authentifié
   (donc en ``blob:``), ce qui évite d'exposer un proxy ouvert ;
2. **une indisponibilité est une réponse, pas une erreur d'écran** — les
   routes de données rendent ``disponible: false`` avec un message ; seule
   l'image binaire (qui n'a pas de forme « vide ») répond 502.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response

from app.api.deps import CurrentUser, get_current_user
from app.api.schemas.meteo import (
    AlerteCycloneRead,
    CoucheSatelliteRead,
    CouchesSatelliteRead,
    CyclonesRead,
    FournisseurRead,
    MeteoPointRead,
    PointMeteoRead,
    PointRouteRead,
    RouteJourCreate,
    RouteJourRead,
    SourcesRead,
    SystemeTropicalRead,
    TrajetCreate,
    TrajetRead,
)
from app.application.services.meteo_service import Horizon, MeteoService
from app.core.errors import ServiceExterneError
from app.geo.meteo import FOURNISSEUR_OPEN_METEO, couche_par_id

router = APIRouter(
    prefix="/meteo",
    tags=["meteo"],
    dependencies=[Depends(get_current_user)],
)

#: Bulletins officiels — affichés même quand la veille automatique échoue. Un
#: lien vérifié vaut mieux qu'un message seul : l'utilisateur doit pouvoir lire
#: la source officielle, c'est elle qui fait foi.
LIENS_OFFICIELS: list[dict[str, str]] = [
    {
        "nom": "Météo Madagascar — cyclones",
        "url": "https://www.meteomadagascar.mg/cyclone/",
        "description": "Bulletins cycloniques de la météorologie nationale malgache.",
    },
    {
        "nom": "Météo Madagascar — vigilance",
        "url": "https://www.meteomadagascar.mg/vigilance/",
        "description": "Vigilance météorologique en cours à Madagascar.",
    },
    {
        "nom": "Météo-France La Réunion — cyclones",
        "url": "https://meteofrance.re/fr/cyclone",
        "description": (
            "Centre régional spécialisé de surveillance cyclonique du sud-ouest "
            "de l'océan Indien."
        ),
    },
    {
        "nom": "GDACS",
        "url": "https://www.gdacs.org/",
        "description": "Alertes mondiales de catastrophes (Commission européenne / JRC).",
    },
]


@router.get("/conditions", response_model=PointMeteoRead)
def conditions_point(
    _utilisateur: CurrentUser,
    latitude: float = Query(ge=-90, le=90),
    longitude: float = Query(ge=-180, le=180),
    heures: int = Query(default=24, ge=3, le=72, description="Profondeur de la prévision."),
) -> PointMeteoRead:
    """Conditions actuelles et prévision horaire d'un point de la carte.

    ``nature_actuelle`` distingue une **analyse modélisée** d'une mesure : le
    fournisseur restitue une estimation actualisée toutes les 15 minutes.
    """
    resultat = MeteoService().conditions(latitude, longitude, heures=heures)
    # Une liste vide n'est pas une réponse : on ne l'indexe pas, on la dit.
    if not resultat.disponible or not resultat.valeur:
        return PointMeteoRead(
            disponible=False,
            erreur=resultat.erreur or "Aucune condition renvoyée pour ce point.",
        )
    point = resultat.valeur[0]
    return PointMeteoRead(
        disponible=True,
        depuis_cache=resultat.depuis_cache,
        point=MeteoPointRead.depuis(point),
    )


@router.post("/trajet", response_model=TrajetRead)
def previsions_trajet(
    payload: TrajetCreate,
    _utilisateur: CurrentUser,
) -> TrajetRead:
    """Prévisions le long d'un déplacement, alignées sur les heures de passage.

    Le frontend envoie les points du tracé **et** l'heure prévue à chacun : le
    backend choisit l'heure de prévision la plus proche et dit l'écart, plutôt
    que d'annoncer une précision qu'une prévision horaire n'a pas.
    """
    horizons = [
        Horizon(
            latitude=point.latitude,
            longitude=point.longitude,
            heure=point.heure if point.heure.tzinfo else point.heure.replace(tzinfo=UTC),
            libelle=point.libelle,
        )
        for point in payload.points
    ]
    resultat = MeteoService().previsions_trajet(
        horizons, rayon_alerte_km=payload.rayon_alerte_km
    )
    return TrajetRead.depuis(resultat)


@router.post("/trajet/jour", response_model=RouteJourRead)
def previsions_route_jour(
    payload: RouteJourCreate,
    _utilisateur: CurrentUser,
) -> RouteJourRead:
    """Prévision **heure par heure d'une journée**, pour chaque point du tracé.

    Un déplacement ne se prépare pas à l'heure près : on connaît le jour du
    voyage, pas l'instant où l'on passera chaque point. Chaque point reçoit donc
    ses 24 heures — c'est la carte qui les ouvre au clic.
    """
    service = MeteoService()
    coordonnees = [(point.latitude, point.longitude) for point in payload.points]
    # Les alertes sont cherchées **avant** la prévision : une source météo
    # indisponible ne doit pas taire un avis cyclonique officiel.
    alertes = [
        AlerteCycloneRead.depuis(alerte)
        for alerte in service.alertes_du_jour(coordonnees, payload.date)
    ]
    avertissements = [
        "Prévision de la journée entière : elle ne dit pas l'heure de passage sur chaque point.",
        "Ces prévisions sont une aide à la préparation : elles ne garantissent ni la "
        "sécurité du déplacement ni l'état des routes.",
    ]
    if alertes:
        avertissements.insert(
            0,
            "Un phénomène tropical est signalé à proximité : consultez le bulletin "
            "officiel avant le départ.",
        )
    resultat = service.previsions_jour(coordonnees, payload.date)
    if not resultat.disponible or not resultat.valeur:
        return RouteJourRead(
            disponible=False,
            depuis_cache=resultat.depuis_cache,
            erreur=resultat.erreur or "Aucune prévision renvoyée pour ce trajet.",
            fournisseur=FournisseurRead.depuis(FOURNISSEUR_OPEN_METEO),
            alertes=alertes,
            avertissements=avertissements,
        )
    # L'âge se calcule dans le schéma, à partir de l'instant de validité : le
    # point métier ne le porte pas.
    premier = MeteoPointRead.depuis(resultat.valeur[0])
    return RouteJourRead(
        disponible=True,
        depuis_cache=resultat.depuis_cache,
        fournisseur=premier.fournisseur,
        recupere_le=premier.recupere_le,
        age_minutes=premier.age_minutes,
        points=[
            PointRouteRead.depuis(point, payload.points[index].libelle)
            for index, point in enumerate(resultat.valeur)
            if index < len(payload.points)
        ],
        alertes=alertes,
        avertissements=avertissements,
    )


@router.get("/satellite/couches", response_model=CouchesSatelliteRead)
def couches_satellite(_utilisateur: CurrentUser) -> CouchesSatelliteRead:
    """Couches d'imagerie **réellement publiées**, avec leur dernier créneau.

    Une couche absente du service ou sans créneau est rendue
    ``disponible: false`` avec son motif : l'interface ne propose pas un calque
    qui ne s'afficherait pas.
    """
    service = MeteoService()
    emprise = service.emprise
    return CouchesSatelliteRead(
        couches=[CoucheSatelliteRead.depuis(etat) for etat in service.couches_satellite()],
        emprise=[emprise.sud, emprise.ouest, emprise.nord, emprise.est],
    )


@router.get(
    "/satellite/image",
    response_class=Response,
    responses={200: {"content": {"image/png": {}, "image/jpeg": {}}}},
)
def image_satellite(
    _utilisateur: CurrentUser,
    couche: Annotated[str, Query(description="Identifiant de couche du catalogue.")],
    heure: Annotated[
        datetime | None,
        Query(description="Créneau demandé (ISO 8601). Omis : le dernier publié."),
    ] = None,
) -> Response:
    """Image d'un créneau, servie en flux binaire depuis le cache du backend.

    L'instant **réel** de l'image est renvoyé dans ``X-Donnee-Heure`` : il peut
    différer du créneau demandé quand le fournisseur est en retard, et
    l'interface affiche celui de l'image reçue, jamais celui qu'elle espérait.
    """
    service = MeteoService()
    resultat = service.image_satellite(couche_par_id(couche).id, heure)
    if not resultat.disponible or resultat.valeur is None:
        raise ServiceExterneError(
            resultat.erreur or "Image satellite indisponible",
            details={"couche": couche},
        )
    image = resultat.valeur
    return Response(
        content=image.contenu,
        media_type=image.mime,
        headers={
            # Une image d'un créneau passé ne change plus ; le cache du
            # navigateur évite de rejouer l'animation en re-téléchargeant tout.
            "Cache-Control": "private, max-age=600",
            "X-Donnee-Heure": image.heure.isoformat(),
            # Identifiant technique : un en-tête HTTP n'accepte pas l'ASCII
            # étendu, et le nom affiché contient un tiret cadratin.
            "X-Donnee-Source": image.couche.fournisseur.cle,
            "X-Donnee-Cache": "1" if resultat.depuis_cache else "0",
        },
    )


@router.get("/cyclones", response_model=CyclonesRead)
def systemes_tropicaux(_utilisateur: CurrentUser) -> CyclonesRead:
    """Systèmes tropicaux officiels de la région, avec leurs zones de vent.

    Quand le service de veille ne répond pas, la réponse reste utile :
    ``disponible: false``, un motif, et les liens officiels. Aucune trajectoire
    n'est reconstituée à partir d'une image satellite.
    """
    resultat = MeteoService().systemes_tropicaux()
    recupere = datetime.now(UTC)
    return CyclonesRead(
        disponible=resultat.disponible,
        erreur=resultat.erreur,
        depuis_cache=resultat.depuis_cache,
        recupere_le=recupere,
        systemes=[
            SystemeTropicalRead.depuis(systeme) for systeme in (resultat.valeur or [])
        ],
        liens_officiels=LIENS_OFFICIELS,
    )


@router.get("/sources", response_model=SourcesRead)
def sources(_utilisateur: CurrentUser) -> SourcesRead:
    """Fournisseurs utilisés, licences et limites d'usage (transparence)."""
    return SourcesRead(
        fournisseurs=[
            FournisseurRead(**fournisseur) for fournisseur in MeteoService().fournisseurs()
        ]
    )
