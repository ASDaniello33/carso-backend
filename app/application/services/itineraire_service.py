"""Service — itinéraires routiers entre deux lieux.

Chaîne (AGENTS.md §2.5) :

```text
Route API → ItineraireService → cache PostgreSQL → Client OpenRouteService
```

Un calcul est **payant** (quota gratuit). Le cache est consulté avant tout
appel : la clé porte les coordonnées normalisées (6 décimales) et le profil —
déplacer un marqueur change la clé, donc invalide le trajet. L'aller-retour ne
fait **pas** partie de la clé : le même trajet simple sert les deux sens, et
l'aller-retour est un doublement calculé à l'affichage (une seule donnée
d'ORService pour deux questions).

Le carburant est calculé en ``Decimal`` : une distance routière et un prix
multipliés en flottants produisent des centimes glissés. Les paramètres
(consommation, prix) peuvent être surchargés par la demande ; la devise reste
celle de l'organisation.
"""

from __future__ import annotations

import bisect
import hashlib
import logging
import math
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.application.services.lieu_service import LieuService, valider_coordonnees
from app.application.trace import TraceContext
from app.core.config import Settings, get_settings
from app.core.errors import NotFoundError, ServiceExterneError, ValidationError
from app.domain.enums import ActionAudit, ActorType
from app.domain.execution import Lieu
from app.domain.geo import ItineraireCache
from app.geo.qualite_routes import (
    NatureBlocage,
    NiveauRoute,
    classifier_extras_ors,
    classifier_route,
)
from app.infrastructure.repositories.geo import (
    ItineraireCacheRepository,
    OsmVoieRepository,
    ParametresVoyageRepository,
)

#: Précision de la clé de cache : 6 décimales (~11 cm). Deux clics sur la même
#: adresse donnent la même clé, un déplacement de marqueur la change.
_PRECISION_CACHE = 6

#: Version de la classification portée par le cache : une entrée écrite avant
#: un changement de règle (ou sans portions) ne doit pas servir d'éternelle
#: « vérité inconnue ». L'incrément invalide les anciennes entrées sans
#: migration : elles expirent d'elles-mêmes.
_VERSION_CACHE = "v2"

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class PointItineraire:
    """Un bout du trajet : un lieu référencé **ou** des coordonnées libres.

    Un ``lieu_id`` vaut mieux que des coordonnées brutes quand le point existe
    déjà : la position reste ajustable, le libellé reste honnête, et le
    périmètre se vérifie sur l'entité. Des coordonnées seules décrivent un
    départ ponctuel (ex. le domicile d'une personne, hors référentiel).
    """

    lieu_id: UUID | None = None
    libelle: str | None = None
    latitude: float | None = None
    longitude: float | None = None

    def coordonnees(self, lieus: dict[UUID, Lieu]) -> tuple[float, float]:
        """Résout ``(latitude, longitude)`` — jamais deux sources à la fois.

        Raises:
            ValidationError: les deux sources sont fournies, ou aucune.
            KeyError-like: un ``lieu_id`` inconnu (résolu par l'appelant).
        """
        if self.lieu_id is not None:
            if self.latitude is not None or self.longitude is not None:
                raise ValidationError(
                    "Un point d'itinéraire désigne un lieu OU des coordonnées, pas les deux",
                )
            lieu = lieus[self.lieu_id]
            if lieu.latitude is None or lieu.longitude is None:
                raise ValidationError(
                    f"Le lieu « {lieu.nom} » n'a pas de position sur la carte : "
                    "ajustez son marqueur avant de calculer un itinéraire",
                    details={"lieu_id": str(lieu.id)},
                )
            return float(lieu.latitude), float(lieu.longitude)
        coordonnees = valider_coordonnees(self.latitude, self.longitude)
        if coordonnees is None:
            raise ValidationError(
                "Un point d'itinéraire exige un lieu ou une position complète"
            )
        return coordonnees


@dataclass(frozen=True, slots=True)
class ResultatItineraire:
    """Ce que l'interface affiche : trajet, carburant, tracé, provenance."""

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
    geometrie: list[list[float]] | None
    depuis_cache: bool
    #: Clé de la ligne de cache servie (permet l'affinage en arrière-plan).
    cle_cache: str | None = None
    #: Portions colorées : indices sur ``geometrie`` + niveau OSM.
    troncons: list[TronconItineraire] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class TronconItineraire:
    """Portion continue d'un itinéraire, classée d'après les tags OSM.

    ``debut``/``fin`` sont des indices **inclusifs** dans la géométrie : deux
    tronçons successifs partagent leur point de jonction, ce qui évite un trou
    visuel à la jointure sur la carte.
    """

    debut: int
    fin: int
    osmid: int | None
    niveau: NiveauRoute
    nature: NatureBlocage
    raison: str
    estime: bool
    tags: dict[str, str]
    distance_m: int


def _cle_cache(
    profil: str,
    origine: tuple[float, float],
    destination: tuple[float, float],
) -> str:
    def format_valeur(lat: float, lon: float) -> str:
        return f"{round(lat, _PRECISION_CACHE):.6f},{round(lon, _PRECISION_CACHE):.6f}"

    charge = f"{_VERSION_CACHE}|{profil}|{format_valeur(*origine)}|{format_valeur(*destination)}"
    return hashlib.sha256(charge.encode("utf-8")).hexdigest()


def _arrondi_2(valeur: Decimal) -> float:
    return float(valeur.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


class ItineraireService:
    """Calcul d'un itinéraire, avec cache et estimation du coût du carburant."""

    def __init__(
        self,
        session: Session,
        *,
        actor_id: str | None = None,
        settings: Settings | None = None,
        client: object | None = None,
        overpass: object | None = None,
    ) -> None:
        self._session = session
        self._trace = TraceContext(session, actor_id=actor_id)
        self._settings = settings or get_settings()
        self._cache = ItineraireCacheRepository(session)
        self._voies = OsmVoieRepository(session)
        self._parametres = ParametresVoyageRepository(session)
        self._client = client
        self._overpass = overpass

    # --- lecture du calcul ------------------------------------------------------

    def calculer(
        self,
        *,
        origine: PointItineraire,
        destination: PointItineraire,
        aller_retour: bool = False,
        consommation_l_100km: float | None = None,
        prix_litre: float | None = None,
        perimetre: object | None = None,
    ) -> ResultatItineraire:
        """Calcule (ou relit) l'itinéraire entre deux points.

        Raises:
            ValidationError: point ambigu, coordonnées invalides ou lieu sans
                position sur la carte.
            NotFoundError: lieu référencé introuvable ou hors périmètre.
            ServiceExterneError: ORS indisponible, refusé ou quota épuisé.
        """
        lieus: dict[UUID, Lieu] = {}
        for point in (origine, destination):
            if point.lieu_id is not None:
                if point.lieu_id in lieus:
                    continue
                if perimetre is not None:
                    lieus[point.lieu_id] = LieuService(self._session).obtenir_pour_perimetre(
                        point.lieu_id, perimetre
                    )
                else:
                    lieu = self._session.get(Lieu, point.lieu_id)
                    if lieu is None:
                        raise NotFoundError(f"Lieu {point.lieu_id} introuvable")
                    lieus[point.lieu_id] = lieu

        depart = origine.coordonnees(lieus)
        arrivee = destination.coordonnees(lieus)
        profil = "driving-car"

        resultat = self._cache.get_valide(
            _cle_cache(profil, depart, arrivee), datetime.now(UTC)
        )
        depuis_cache = resultat is not None
        if resultat is None:
            calcul = self._client_ors().calculer(depart, arrivee)
            # La classification **exacte** coûte un appel Overpass (30 s et
            # plus quand l'instance publique sature) : elle ne doit jamais
            # bloquer la requête HTTP, dont le frontend attend une réponse en
            # 20 s. La requête part avec l'estimation issue des extras ORS ;
            # l'affinage se fait en arrière-plan (`affiner_tache`) et met le
            # cache à jour pour les consultations suivantes.
            troncons_calcules = self._classer_troncons(
                calcul.coordonnees, calcul.extras, affiner=False
            )
            duree_validite = timedelta(days=self._settings.itineraires_cache_jours)
            expire_le = datetime.now(UTC) + duree_validite
            resultat = self._cache.enregistrer(
                ItineraireCache(
                    cle=_cle_cache(profil, depart, arrivee),
                    profil=profil,
                    origine_latitude=depart[0],
                    origine_longitude=depart[1],
                    destination_latitude=arrivee[0],
                    destination_longitude=arrivee[1],
                    aller_retour=False,
                    distance_m=calcul.distance_m,
                    duree_s=calcul.duree_s,
                    # Les extras ORS et les portions classées sont gardés avec
                    # le tracé : un cache relu ne redemande ni ORS ni Overpass.
                    geometrie={
                        "coordonnees": calcul.coordonnees,
                        "extras": calcul.extras,
                        "troncons": [self._troncon_dict(t) for t in troncons_calcules],
                    },
                    expire_le=expire_le,
                )
            )
            self._trace.record_event(
                actor_type=ActorType.HUMAIN.value,
                action=ActionAudit.ITINERAIRE_CALCULE.value,
                entity_type="itineraire",
                entity_id=resultat.id,
                after={
                    "profil": profil,
                    "distance_m": resultat.distance_m,
                    "duree_s": resultat.duree_s,
                },
            )

        donnees_geometrie = resultat.geometrie or {}
        coordonnees = [list(point) for point in donnees_geometrie.get("coordonnees", [])]
        if depuis_cache:
            troncons = self._troncons_du_cache(
                donnees_geometrie.get("troncons"), coordonnees
            )
        else:
            troncons = troncons_calcules

        distance_m = int(resultat.distance_m)
        duree_s = int(resultat.duree_s)
        facteur = 2 if aller_retour else 1
        lignes = self._estimer_carburant(
            distance_km=distance_m / 1000.0 * facteur,
            consommation_l_100km=consommation_l_100km,
            prix_litre=prix_litre,
        )

        return ResultatItineraire(
            distance_m=distance_m,
            duree_s=duree_s,
            aller_retour=aller_retour,
            distance_totale_m=distance_m * facteur,
            duree_totale_s=duree_s * facteur,
            litres=lignes[0],
            cout=lignes[1],
            devise=self._parametres.lire_unique().devise,
            origine_libelle=self._libelle(origine, lieus),
            destination_libelle=self._libelle(destination, lieus),
            geometrie=coordonnees or None,
            depuis_cache=depuis_cache,
            cle_cache=resultat.cle,
            troncons=troncons,
        )

    # --- qualité des routes (OpenStreetMap) -------------------------------------

    def _intervalles_extra(
        self, extras: dict[str, Any], cle: str
    ) -> list[tuple[int, int, int]]:
        """Intervalles ``[debut, fin, valeur]`` d'un extra ORS, normalisés.

        Les bornes sont ramenées dans la géométrie : un extra incohérent ne
        doit pas produire un index hors tableau. La valeur reste un entier —
        c'est un indice de catégorie (surface, waytype, waycategory), pas un
        texte à décoder ici.
        """
        bruts = extras.get(cle) if isinstance(extras, dict) else None
        if not isinstance(bruts, list):
            return []
        intervalles: list[tuple[int, int, int]] = []
        for brut in bruts:
            if not isinstance(brut, list) or len(brut) != 3:
                continue
            debut, fin, valeur = brut
            if not isinstance(debut, int) or not isinstance(fin, int):
                continue
            if not isinstance(valeur, int) or fin <= debut:
                continue
            intervalles.append((max(0, debut), fin, valeur))
        intervalles.sort(key=lambda item: item[0])
        return intervalles

    def _intervalles_osm(self, extras: dict[str, Any]) -> list[tuple[int, int, int]]:
        """Intervalles d'identifiants de voies ORS (``osmium``), triés.

        Les bornes sont ramenées dans la géométrie : un extra incohérent ne
        doit pas produire un index hors tableau.
        """
        return [
            (debut, fin, osmid)
            for debut, fin, osmid in self._intervalles_extra(extras, "osmium")
            if osmid > 0
        ]

    @staticmethod
    def _valeur_a(
        intervalles: list[tuple[int, int, int]], index: int
    ) -> int | None:
        """Valeur de l'extra couvrant l'index donné (recherche dichotomique)."""
        position = bisect.bisect_right(
            [debut for debut, _fin, _valeur in intervalles], index
        ) - 1
        if position < 0:
            return None
        debut, fin, valeur = intervalles[position]
        return valeur if debut <= index < fin else None

    def _segmentation_extras(
        self, extras: dict[str, Any], dernier: int
    ) -> list[tuple[int, int, int | None, int | None, int | None]]:
        """Découpe la géométrie d'après les extras ORS eux-mêmes.

        ``surface``, ``waytype`` et ``waycategory`` décrivent la même géométrie
        avec leurs propres frontières : on coupe sur l'union des bornes, lit la
        valeur de chaque extra au milieu de chaque segment, et fusionne les
        segments adjacents de même contenu. Sans ``osmid``, c'est la seule
        segmentation disponible — et elle suffit à un premier passage.
        """
        surfaces = self._intervalles_extra(extras, "surface")
        types = self._intervalles_extra(extras, "waytype")
        categories = self._intervalles_extra(extras, "waycategory")
        if not (surfaces or types or categories):
            return []
        bornes = {0, dernier}
        for intervalles in (surfaces, types, categories):
            for debut, fin, _valeur in intervalles:
                if debut < dernier:
                    bornes.add(min(debut, dernier))
                if 0 < fin <= dernier:
                    bornes.add(fin)
        ordonnees = sorted(bornes)
        segments: list[tuple[int, int, int | None, int | None, int | None]] = []
        for debut, fin in zip(ordonnees, ordonnees[1:], strict=False):
            if fin <= debut:
                continue
            milieu = (debut + fin) // 2
            segments.append(
                (
                    debut,
                    fin,
                    self._valeur_a(surfaces, milieu),
                    self._valeur_a(types, milieu),
                    self._valeur_a(categories, milieu),
                )
            )
        fusionnes: list[tuple[int, int, int | None, int | None, int | None]] = []
        for segment in segments:
            if fusionnes and fusionnes[-1][2:] == segment[2:]:
                fusionnes[-1] = (fusionnes[-1][0], segment[1], *segment[2:])
            else:
                fusionnes.append(segment)
        return fusionnes

    def _tags_voies(self, osm_ids: list[int]) -> dict[int, dict[str, str]]:
        """Tags des voies : cache PostgreSQL d'abord, Overpass pour le reste.

        Overpass indisponible n'est **pas** une erreur d'itinéraire : les voies
        manquantes seront classées « inconnu », la carte reste utilisable.
        """
        uniques = sorted({osm_id for osm_id in osm_ids if osm_id > 0})
        if not uniques:
            return {}
        maintenant = datetime.now(UTC)
        connus = self._voies.lire_valides(uniques, maintenant)
        manquants = [osm_id for osm_id in uniques if osm_id not in connus]
        if manquants:
            try:
                nouveaux = self._client_overpass().tags_voies(manquants)
            except ServiceExterneError:
                nouveaux = {}
            if nouveaux:
                self._voies.enregistrer_tags(
                    nouveaux,
                    expire_le=maintenant
                    + timedelta(days=self._settings.osm_tags_cache_jours),
                    maintenant=maintenant,
                )
                connus.update(nouveaux)
        return connus

    def _classer_troncons(
        self,
        coordonnees: list[list[float]],
        extras: dict[str, Any],
        affiner: bool = True,
    ) -> list[TronconItineraire]:
        """Découpe le tracé en portions et classe chaque portion.

        Deux passes, portion par portion (jamais tout ou rien) :

        1. quand ORS rend des identifiants de voies (``osmium``), les tags OSM
           (Overpass) donnent la classification exacte ; une portion sans tag
           exploitable garde l'estimation déduite des extras ORS — un échec
           Overpass ne transforme pas la portion en « inconnu » ;
        2. sinon (ORS ne rend pas ``osmid`` en profil voiture), la segmentation
           vient des extras ORS eux-mêmes ; Overpass peut affiner quelques
           segments par proximité, sans rendre les autres « inconnus ».

        ``affiner=False`` interdit tout appel réseau : c'est le mode de la
        requête HTTP, dont le frontend attend une réponse en 20 s. L'affinage
        exact se fait ensuite en arrière-plan (``affiner_tache``).

        Les portions non couvertes (début, fin, trou) restent « inconnu » :
        la carte reste continue et ne fabrique ni trou ni couleur inventée.
        """
        if len(coordonnees) < 2:
            return []
        dernier = len(coordonnees) - 1
        intervalles = [
            (debut, min(fin, dernier), osmid)
            for debut, fin, osmid in self._intervalles_osm(extras)
            if debut < dernier
        ]
        if intervalles:
            return self._troncons_par_voie(
                coordonnees, intervalles, extras, affiner=affiner
            )
        segments = self._segmentation_extras(extras, dernier)
        if not segments:
            # Ni identifiants ni extras exploitables : un seul tronçon inconnu,
            # la carte reste continue.
            return [
                self._troncon_inconnu(
                    0,
                    dernier,
                    coordonnees,
                    "Aucune donnée de qualité disponible pour ce tracé.",
                )
            ]
        return self._troncons_par_extras(coordonnees, segments, affiner=affiner)

    def _troncons_par_voie(
        self,
        coordonnees: list[list[float]],
        intervalles: list[tuple[int, int, int]],
        extras: dict[str, Any],
        *,
        affiner: bool = True,
    ) -> list[TronconItineraire]:
        """Classe chaque portion par ses tags OSM, à défaut par ses extras ORS."""
        dernier = len(coordonnees) - 1
        surfaces = self._intervalles_extra(extras, "surface")
        types = self._intervalles_extra(extras, "waytype")
        categories = self._intervalles_extra(extras, "waycategory")
        if affiner:
            tags_voies = self._tags_voies([osmid for _, _, osmid in intervalles])
        else:
            # Hors affinage : uniquement le cache PostgreSQL — aucun appel
            # réseau dans le chemin de la requête HTTP.
            tags_voies = self._voies.lire_valides(
                sorted({osmid for _, _, osmid in intervalles}), datetime.now(UTC)
            )
        troncons: list[TronconItineraire] = []
        couvert = 0
        for debut, fin, osmid in intervalles:
            if debut > couvert:
                troncons.append(
                    self._troncon_inconnu(
                        couvert,
                        debut,
                        coordonnees,
                        "Portion sans identifiant OSM : qualité non évaluée.",
                    )
                )
            classification = None
            tags = tags_voies.get(osmid)
            if tags:
                classee = classifier_route(tags)
                if classee.niveau != "inconnu":
                    classification = classee
                else:
                    # Les tags existent mais ne disent rien de l'état : on
                    # garde leurs libellés pour l'infobulle, et l'estimation
                    # extras pour le niveau.
                    milieu = (debut + fin) // 2
                    classification = replace(
                        classifier_extras_ors(
                            surface=self._valeur_a(surfaces, milieu),
                            waytype=self._valeur_a(types, milieu),
                            waycategory=self._valeur_a(categories, milieu),
                        ),
                        tags=classee.tags,
                    )
            if classification is None:
                milieu = (debut + fin) // 2
                classification = classifier_extras_ors(
                    surface=self._valeur_a(surfaces, milieu),
                    waytype=self._valeur_a(types, milieu),
                    waycategory=self._valeur_a(categories, milieu),
                )
            troncons.append(
                TronconItineraire(
                    debut=debut,
                    fin=fin,
                    osmid=osmid,
                    niveau=classification.niveau,
                    nature=classification.nature,
                    raison=classification.raison,
                    estime=classification.estime,
                    tags=classification.tags,
                    distance_m=self._longueur_m(coordonnees, debut, fin),
                )
            )
            couvert = max(couvert, fin)
        if couvert < dernier:
            troncons.append(
                self._troncon_inconnu(
                    couvert,
                    dernier,
                    coordonnees,
                    "Fin de tracé sans identifiant OSM : qualité non évaluée.",
                )
            )
        self._journaliser(troncons, len(tags_voies))
        return troncons

    def _troncons_par_extras(
        self,
        coordonnees: list[list[float]],
        segments: list[tuple[int, int, int | None, int | None, int | None]],
        *,
        affiner: bool = True,
    ) -> list[TronconItineraire]:
        """Classe chaque segment d'après ses extras ORS, affiné par proximité."""
        affinage = (
            self._tags_par_proximite(coordonnees, segments) if affiner else {}
        )
        troncons: list[TronconItineraire] = []
        for debut, fin, surface, waytype, waycategory in segments:
            classification = classifier_extras_ors(
                surface=surface, waytype=waytype, waycategory=waycategory
            )
            osmid = None
            voie = affinage.get((debut, fin))
            if isinstance(voie, dict) and voie.get("osm_id"):
                osmid = int(voie["osm_id"])  # type: ignore[arg-type]
                classee = classifier_route(
                    {str(c): str(v) for c, v in (voie.get("tags") or {}).items()}
                )
                if classee.niveau != "inconnu":
                    # Les tags de la voie proche disent mieux que les extras :
                    # smoothness/access explicites remplacent l'estimation.
                    classification = classee
                elif classee.tags:
                    classification = replace(
                        classification, tags=classee.tags
                    )
            troncons.append(
                TronconItineraire(
                    debut=debut,
                    fin=fin,
                    osmid=osmid,
                    niveau=classification.niveau,
                    nature=classification.nature,
                    raison=classification.raison,
                    estime=classification.estime,
                    tags=classification.tags,
                    distance_m=self._longueur_m(coordonnees, debut, fin),
                )
            )
        self._journaliser(troncons, len(affinage))
        return troncons

    def _tags_par_proximite(
        self,
        coordonnees: list[list[float]],
        segments: list[tuple[int, int, int | None, int | None, int | None]],
    ) -> dict[tuple[int, int], dict[str, object]]:
        """Tags OSM de la voie la plus proche du milieu de quelques segments.

        Au plus 20 segments (les plus longs d'abord) : Overpass est un service
        public lent, et l'affinage est un **plus**, pas une condition. Un échec
        renvoie un dictionnaire vide — les segments gardent leur estimation
        issue des extras, jamais « inconnu » par ricochet.
        """
        retenus = sorted(segments, key=lambda s: s[1] - s[0], reverse=True)[:20]
        if not retenus:
            return {}
        points = [
            (
                coordonnees[(debut + fin) // 2][1],
                coordonnees[(debut + fin) // 2][0],
            )
            for debut, fin, *_reste in retenus
        ]
        try:
            trouves = self._client_overpass().tags_autour(points, rayon_m=25)
        except ServiceExterneError:
            logger.warning(
                "qualite_routes: Overpass indisponible, les portions restent "
                "sur l'estimation issue des extras OpenRouteService"
            )
            return {}
        return {
            (segment[0], segment[1]): voie
            for segment, voie in zip(retenus, trouves, strict=False)
            if isinstance(voie, dict) and voie.get("osm_id")
        }

    @staticmethod
    def _journaliser(troncons: list[TronconItineraire], voies_trouvees: int) -> None:
        """Diagnostic de la classification — aucune donnée sensible (pas de clé)."""
        if not troncons:
            return
        distance_totale = sum(t.distance_m for t in troncons) or 1

        def part(predicat) -> int:  # noqa: ANN001 - interne
            distance = sum(t.distance_m for t in troncons if predicat(t))
            return round(100 * distance / distance_totale)

        logger.info(
            "qualite_routes: portions=%d voies_overpass=%d smoothness=%d "
            "tracktype=%d surface=%d distance: explicite=%d%% estime=%d%% "
            "inconnu=%d%%",
            len(troncons),
            voies_trouvees,
            sum(1 for t in troncons if "smoothness" in t.tags),
            sum(1 for t in troncons if "tracktype" in t.tags),
            sum(1 for t in troncons if "surface" in t.tags),
            part(lambda t: t.niveau != "inconnu" and not t.estime),
            part(lambda t: t.niveau != "inconnu" and t.estime),
            part(lambda t: t.niveau == "inconnu"),
        )

    @staticmethod
    def _troncon_dict(troncon: TronconItineraire) -> dict[str, Any]:
        """Forme sérialisable d'un tronçon, telle qu'elle est mise en cache."""
        return {
            "debut": troncon.debut,
            "fin": troncon.fin,
            "osmid": troncon.osmid,
            "niveau": troncon.niveau,
            "nature": troncon.nature,
            "raison": troncon.raison,
            "estime": troncon.estime,
            "tags": troncon.tags,
            "distance_m": troncon.distance_m,
        }

    def _troncons_du_cache(
        self, bruts: Any, coordonnees: list[list[float]]
    ) -> list[TronconItineraire]:
        """Relit les portions mises en cache, sans rappeler Overpass.

        Un cache antérieur à la fonctionnalité n'a pas de portions : le trajet
        s'affiche alors en qualité inconnue, sans bloquer la carte.
        """
        if not isinstance(bruts, list) or not bruts:
            if len(coordonnees) < 2:
                return []
            return [
                self._troncon_inconnu(
                    0,
                    len(coordonnees) - 1,
                    coordonnees,
                    "Trajet relu d'un cache antérieur : la qualité n'a pas été évaluée.",
                )
            ]
        troncons: list[TronconItineraire] = []
        for brut in bruts:
            if not isinstance(brut, dict):
                continue
            try:
                troncons.append(
                    TronconItineraire(
                        debut=int(brut["debut"]),
                        fin=int(brut["fin"]),
                        osmid=int(brut["osmid"]) if brut.get("osmid") else None,
                        niveau=brut["niveau"],
                        nature=brut["nature"],
                        raison=str(brut.get("raison") or ""),
                        estime=bool(brut.get("estime")),
                        tags={str(c): str(v) for c, v in (brut.get("tags") or {}).items()},
                        distance_m=int(brut.get("distance_m") or 0),
                    )
                )
            except (KeyError, TypeError, ValueError):
                continue
        return troncons

    @staticmethod
    def affiner_tache(cle: str) -> None:
        """Raffine en arrière-plan les portions d'une entrée de cache.

        Appelé hors de la requête HTTP (``BackgroundTasks``) : la réponse est
        déjà partie avec les estimations issues des extras ORS. Quand Overpass
        répond, les portions sont reclassées exactement (tags de voies) et le
        cache est mis à jour — les consultations suivantes en profitent.

        Une session **propre** est ouverte : la session de la requête est déjà
        fermée quand la tâche s'exécute. Un échec (réseau, base) est journalisé
        et n'affecte ni la réponse déjà envoyée ni les données métier.
        """
        from app.infrastructure.database import SessionLocal

        try:
            with SessionLocal() as session:
                service = ItineraireService(session)
                ligne = service._cache.get_valide(cle, datetime.now(UTC))
                if ligne is None:
                    return
                # Copie **obligatoire** : muter le dictionnaire chargé en place
                # ferait comparer SQLAlchemy à lui-même (ancienne = nouvelle
                # valeur) et l'UPDATE ne partirait jamais.
                donnees = dict(ligne.geometrie or {})
                coordonnees = [
                    list(point) for point in donnees.get("coordonnees", [])
                ]
                if len(coordonnees) < 2:
                    return
                # Une seule tentative par jour : la saturation d'Overpass ne
                # doit pas transformer chaque consultation en attente cachée.
                affine_le = str(donnees.get("affine_le") or "")
                if affine_le:
                    try:
                        dernier = datetime.fromisoformat(affine_le)
                    except ValueError:
                        dernier = None
                    if dernier and datetime.now(UTC) - dernier < timedelta(days=1):
                        return
                troncons = service._classer_troncons(
                    coordonnees, donnees.get("extras") or {}, affiner=True
                )
                if not troncons:
                    return
                donnees["troncons"] = [service._troncon_dict(t) for t in troncons]
                donnees["affine_le"] = datetime.now(UTC).isoformat()
                ligne.geometrie = donnees
                session.commit()
                logger.info(
                    "qualite_routes: affinage en arrière-plan terminé (%d portions)",
                    len(troncons),
                )
        except Exception:
            # La réponse HTTP est déjà partie : une erreur ici ne doit faire
            # ni planter le worker ni corrompre le cache — on la journalise.
            logger.exception(
                "qualite_routes: affinage en arrière-plan échoué pour %s", cle
            )

    def _troncon_inconnu(
        self,
        debut: int,
        fin: int,
        coordonnees: list[list[float]],
        raison: str,
    ) -> TronconItineraire:
        return TronconItineraire(
            debut=debut,
            fin=fin,
            osmid=None,
            niveau="inconnu",
            nature="inconnue",
            raison=raison,
            estime=True,
            tags={},
            distance_m=self._longueur_m(coordonnees, debut, fin),
        )

    @staticmethod
    def _longueur_m(coordonnees: list[list[float]], debut: int, fin: int) -> int:
        """Longueur cumulée d'une portion (haversine, mètres arrondis)."""
        total = 0.0
        for index in range(debut, min(fin, len(coordonnees) - 1)):
            lon1, lat1 = coordonnees[index][0], coordonnees[index][1]
            lon2, lat2 = coordonnees[index + 1][0], coordonnees[index + 1][1]
            phi1, phi2 = math.radians(lat1), math.radians(lat2)
            dphi = math.radians(lat2 - lat1)
            dlambda = math.radians(lon2 - lon1)
            a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(
                dlambda / 2
            ) ** 2
            total += 2 * 6_371_000 * math.asin(min(1.0, math.sqrt(a)))
        return int(round(total))

    def _client_overpass(self):
        """Client Overpass à la demande (un client injecté l'emporte en test)."""
        if self._overpass is None:
            from app.geo.overpass import ClientOverpass

            self._overpass = ClientOverpass(
                self._settings.overpass_base_url,
                timeout_s=self._settings.overpass_timeout_s,
            )
        return self._overpass

    # --- internes ---------------------------------------------------------------

    def _client_ors(self):
        """Client ORS construit à la demande (test injecte le sien)."""
        if self._client is None:
            from app.geo.openrouteservice import ClientItineraires

            self._client = ClientItineraires(
                self._settings.heigit_base_url,
                self._settings.heigit_api_key.get_secret_value()
                if self._settings.heigit_api_key
                else None,
                timeout_s=self._settings.itineraires_timeout_s,
            )
        return self._client

    def _estimer_carburant(
        self,
        *,
        distance_km: float,
        consommation_l_100km: float | None,
        prix_litre: float | None,
    ) -> tuple[float, float]:
        """Litres et coût, en ``Decimal`` (les centimes ne glissent pas)."""
        ligne = self._parametres.lire_unique()
        conso = Decimal(str(consommation_l_100km or float(ligne.consommation_l_100km)))
        prix = Decimal(str(prix_litre if prix_litre is not None else float(ligne.prix_litre)))
        if conso <= 0 or prix < 0:
            raise ValidationError(
                "Consommation et prix doivent être positifs pour estimer un coût"
            )
        litres = (Decimal(str(distance_km)) * conso / Decimal(100)).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        cout = (litres * prix).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        return _arrondi_2(litres), _arrondi_2(cout)

    @staticmethod
    def _libelle(point: PointItineraire, lieus: dict[UUID, Lieu]) -> str:
        if point.lieu_id is not None:
            return lieus[point.lieu_id].nom
        return point.libelle or f"{point.latitude}, {point.longitude}"


__all__ = [
    "ItineraireService",
    "PointItineraire",
    "ResultatItineraire",
    "TronconItineraire",
]
