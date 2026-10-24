"""Routes—lieux d'exécution, recherche cartographique et itinéraires.

Le lieu n'est jamais la nature d'un lot—il est créé ici puis référencé par une
mission (``lieu_id``), une organisation ou un lot (décision du 22/10).

Périmètre (même règle partout) :

- **interne** : gestion complète du référentiel (créer, renommer, positionner) ;
- **formateur** : lit les lieux de **ses** missions, ajuste la position du
  marqueur et calcule un itinéraire ; il ne crée pas de lieu et ne renomme
  rien — un lieu hors périmètre est introuvable (404), jamais « refusé ».
"""

from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, Query, status

from app.api.deps import CurrentUser, DbSession, get_current_user
from app.api.permissions import InterneUser, PerimetreDep
from app.api.schemas import (
    ItineraireCreate,
    ItineraireRead,
    LieuCreate,
    LieuRead,
    LieuTrouveRead,
    LieuUpdate,
    PositionLieuUpdate,
    TronconItineraireRead,
)
from app.application.dto import LieuInput, LieuUpdateInput
from app.application.services import LieuService
from app.application.services.itineraire_service import (
    ItineraireService,
    PointItineraire,
)
from app.core.config import get_settings
from app.domain.execution import Lieu

router = APIRouter(
    prefix="/lieux",
    tags=["lieux"],
    dependencies=[Depends(get_current_user)],
)


def _lieu_read(lieu: Lieu) -> LieuRead:
    return LieuRead.model_validate(lieu)


@router.get("/", response_model=list[LieuRead])
def list_lieux(session: DbSession, perimetre: PerimetreDep) -> list[LieuRead]:
    """Lieux visibles par le compte : tous pour l'interne, ses missions sinon."""
    return [
        _lieu_read(lieu)
        for lieu in LieuService(session).lister_pour_perimetre(perimetre)
    ]


@router.get("/recherche", response_model=list[LieuTrouveRead])
def rechercher_lieu(
    session: DbSession,
    _utilisateur: CurrentUser,
    q: str = Query(
        min_length=2,
        max_length=200,
        description="Nom ou adresse (API Photon, appelée par le backend).",
    ),
    limite: int = Query(default=5, ge=1, le=10),
) -> list[LieuTrouveRead]:
    """Suggère des lieux (nom, adresse, position) pour préremplir le formulaire.

    Aucune écriture, aucune donnée CARSO : la recherche interroge **Photon**,
    un service public d'adresses. Elle est donc ouverte à tout compte
    authentifié — un formateur cartographie aussi ses lieux de mission, et
    l'outil d'affichage de l'agent a besoin de résoudre un nom de ville pour
    dessiner une carte. La création et le renommage d'un lieu restent, eux,
    réservés à l'interne (``InterneUser``).
    """
    cfg = get_settings()
    from app.geo.photon import RechercheLieux

    results = RechercheLieux(cfg.photon_base_url, timeout_s=cfg.photon_timeout_s).chercher(
        q, limite=limite
    )
    return [
        LieuTrouveRead(
            nom=trouve.nom,
            adresse=trouve.adresse,
            ville=trouve.ville,
            pays=trouve.pays,
            latitude=trouve.latitude,
            longitude=trouve.longitude,
            osm_type=trouve.osm_type,
            osm_id=trouve.osm_id,
            libelle=trouve.libelle,
        )
        for trouve in results
    ]


@router.post("/", response_model=LieuRead, status_code=status.HTTP_201_CREATED)
def create_lieu(payload: LieuCreate, session: DbSession, user: InterneUser) -> LieuRead:
    """Crée un lieu (nom obligatoire, coordonnées facultatives mais paires)."""
    lieu = LieuService(session, actor_id=str(user.id)).creer(
        LieuInput(
            nom=payload.nom,
            adresse=payload.adresse,
            ville=payload.ville,
            pays=payload.pays,
            zone=payload.zone,
            latitude=payload.latitude,
            longitude=payload.longitude,
        )
    )
    return _lieu_read(lieu)


@router.post("/itineraire", response_model=ItineraireRead)
def calculer_itineraire(
    payload: ItineraireCreate,
    background: BackgroundTasks,
    session: DbSession,
    perimetre: PerimetreDep,
) -> ItineraireRead:
    """Itinéraire routier entre deux points (cache avant appel, carburant inclus).

    Un formateur ne peut visiter que les lieux de ses missions ; des
    coordonnées libres restent possibles (un départ ponctuel hors référentiel).

    La réponse part avec la classification issue des extras OpenRouteService
    (estimations) : l'affinage exact par OpenStreetMap (Overpass, service
    public lent) se fait **après** la réponse, en arrière-plan, et met le
    cache à jour — la requête ne doit jamais attendre 30 s de plus.
    """
    resultat = ItineraireService(session).calculer(
        origine=PointItineraire(**payload.origine.model_dump()),
        destination=PointItineraire(**payload.destination.model_dump()),
        aller_retour=payload.aller_retour,
        consommation_l_100km=payload.consommation_l_100km,
        prix_litre=payload.prix_litre,
        perimetre=perimetre,
    )
    # La tâche de fond ouvre sa **propre** session : sans ce commit, elle
    # démarre avant que la session de la requête ne valide la ligne de cache
    # (``get_session`` ne commit qu'après la réponse) et ne trouve rien —
    # l'affinage Overpass ne s'exécutait jamais, constaté sur une route de
    # 928 km restée à 0 voie lue. On valide donc explicitement le trajet
    # avant de programmer l'affinage.
    if resultat.cle_cache:
        session.commit()
        background.add_task(ItineraireService.affiner_tache, resultat.cle_cache)
    return ItineraireRead(
        distance_m=resultat.distance_m,
        duree_s=resultat.duree_s,
        aller_retour=resultat.aller_retour,
        distance_totale_m=resultat.distance_totale_m,
        duree_totale_s=resultat.duree_totale_s,
        litres=resultat.litres,
        cout=resultat.cout,
        devise=resultat.devise,
        origine_libelle=resultat.origine_libelle,
        destination_libelle=resultat.destination_libelle,
        geometrie=resultat.geometrie,
        depuis_cache=resultat.depuis_cache,
        troncons=[
            TronconItineraireRead(
                debut=troncon.debut,
                fin=troncon.fin,
                osmid=troncon.osmid,
                niveau=troncon.niveau,
                type_route=troncon.type_route,
                nature=troncon.nature,
                raison=troncon.raison,
                estime=troncon.estime,
                tags=troncon.tags,
                distance_m=troncon.distance_m,
            )
            for troncon in resultat.troncons
        ],
    )


@router.get("/{lieu_id}", response_model=LieuRead)
def get_lieu(
    lieu_id: UUID, session: DbSession, perimetre: PerimetreDep
) -> LieuRead:
    """Consultation d'un lieu du périmètre (404 si absent ou hors périmètre)."""
    return _lieu_read(LieuService(session).obtenir_pour_perimetre(lieu_id, perimetre))


@router.patch("/{lieu_id}", response_model=LieuRead)
def update_lieu(
    lieu_id: UUID,
    payload: LieuUpdate,
    session: DbSession,
    user: InterneUser,
) -> LieuRead:
    """Modification partielle d'un lieu (interne : nom, adresse, coordonnées)."""
    lieu = LieuService(session, actor_id=str(user.id)).modifier(
        lieu_id,
        LieuUpdateInput(
            nom=payload.nom,
            adresse=payload.adresse,
            ville=payload.ville,
            pays=payload.pays,
            zone=payload.zone,
            latitude=payload.latitude,
            longitude=payload.longitude,
        ),
    )
    return _lieu_read(lieu)


@router.patch("/{lieu_id}/position", response_model=LieuRead)
def positionner_lieu(
    lieu_id: UUID,
    payload: PositionLieuUpdate,
    session: DbSession,
    user: CurrentUser,
    perimetre: PerimetreDep,
) -> LieuRead:
    """Ajuste la position d'un marqueur (interne, ou formateur sur ses missions)."""
    lieu_service = LieuService(session, actor_id=str(user.id))
    if not perimetre.est_complet:
        # Un formateur ne déplace que le marqueur d'un lieu de ses missions.
        lieu_service.obtenir_pour_perimetre(lieu_id, perimetre)
    lieu = lieu_service.positionner(lieu_id, payload.latitude, payload.longitude)
    return _lieu_read(lieu)
