"""Tests — coloration des itinéraires selon OpenStreetMap.

Trois couches, sans réseau :

1. **la règle** (``app.geo.qualite_routes``) : chaque valeur de ``smoothness``,
   chaque restriction d'accès, ``tracktype`` en secours, l'absence de données ;
2. **les réponses externes** : extras ORS (deux formes possibles) et tags
   Overpass normalisés ;
3. **la découpe du tracé** : indices continus, sans trou, sans dépassement, et
   dégradation en « inconnu » quand Overpass est indisponible.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.application.services.itineraire_service import ItineraireService
from app.core.errors import ServiceExterneError
from app.geo.openrouteservice import interpreter_reponse as interpreter_ors
from app.geo.overpass import interpreter_reponse as interpreter_overpass
from app.geo.qualite_routes import (
    classifier_extras_ors,
    classifier_route,
    classifier_type_route,
)

# --- 1. La règle ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("smoothness", "niveau"),
    [
        ("excellent", "bon"),
        ("good", "bon"),
        ("intermediate", "tolerable"),
        ("bad", "mauvais"),
        ("very_bad", "mauvais"),
        ("horrible", "mauvais"),
        ("very_horrible", "mauvais"),
        ("impassable", "impraticable"),
    ],
)
def test_smoothness_couvre_toutes_les_valeurs(smoothness: str, niveau: str) -> None:
    resultat = classifier_route({"highway": "residential", "smoothness": smoothness})
    assert resultat.niveau == niveau
    assert resultat.nature == "physique"
    assert resultat.estime is False


@pytest.mark.parametrize("cle", ["motorcar", "motor_vehicle", "vehicle", "access"])
@pytest.mark.parametrize("valeur", ["no", "private", "agricultural", "forestry"])
def test_acces_restreint_est_impraticable_et_reglementaire(cle: str, valeur: str) -> None:
    resultat = classifier_route(
        {"highway": "residential", "smoothness": "good", cle: valeur}
    )
    # Une route privée n'est pas un nid-de-poule : la raison doit le dire.
    assert resultat.niveau == "impraticable"
    assert resultat.nature == "reglementaire"
    assert cle in resultat.raison


def test_acces_explicitement_ouvert_ne_bloque_pas() -> None:
    resultat = classifier_route(
        {"highway": "residential", "smoothness": "good", "access": "yes"}
    )
    assert resultat.niveau == "bon"


def test_voie_en_construction_est_impraticable() -> None:
    resultat = classifier_route({"highway": "construction", "construction": "primary"})
    assert resultat.niveau == "impraticable"
    assert "construction" in resultat.raison


@pytest.mark.parametrize(
    ("tracktype", "niveau"),
    [
        ("grade1", "tolerable"),
        ("grade2", "tolerable"),
        ("grade3", "mauvais"),
        ("grade4", "mauvais"),
        ("grade5", "impraticable"),
    ],
)
def test_tracktype_est_un_indicateur_estime(tracktype: str, niveau: str) -> None:
    resultat = classifier_route({"highway": "track", "tracktype": tracktype})
    assert resultat.niveau == niveau
    assert resultat.estime is True
    assert "Estimé" in resultat.raison


def test_absence_de_donnees_reste_inconnue() -> None:
    # Un revêtement ne dit rien de l'état : « paved » n'est pas « bon ».
    resultat = classifier_route({"highway": "residential", "surface": "asphalt"})
    assert resultat.niveau == "inconnu"
    assert resultat.estime is True
    assert "surface=asphalt" in resultat.raison


def test_les_tags_affiches_sont_limites() -> None:
    resultat = classifier_route(
        {"smoothness": "bad", "surface": "gravel", "name": "Piste X", "source": "survey"}
    )
    assert set(resultat.tags) == {"smoothness", "surface"}


def test_check_date_est_conserve_pour_la_fraicheur() -> None:
    # La date de vérification dit si la donnée est fraîche : elle reste visible.
    resultat = classifier_route({"smoothness": "good", "check_date": "2024-03-01"})
    assert resultat.tags["check_date"] == "2024-03-01"


# --- 1 bis. Type de voie -------------------------------------------------------


@pytest.mark.parametrize(
    ("highway", "type_route"),
    [
        ("motorway", "autoroute"),
        ("motorway_link", "autoroute"),
        ("primary", "route_principale"),
        ("trunk_link", "route_principale"),
        ("secondary", "route_secondaire"),
        ("tertiary", "route_secondaire"),
        ("residential", "rue"),
        ("service", "rue"),
        ("track", "piste"),
        ("path", "chemin"),
        ("cycleway", "chemin"),
        ("steps", "chemin"),
        ("construction", "construction"),
    ],
)
def test_type_route_depuis_le_tag_highway(highway: str, type_route: str) -> None:
    assert classifier_type_route({"highway": highway}) == type_route


def test_type_route_bac_et_inconnu() -> None:
    assert classifier_type_route({"route": "ferry"}) == "bac"
    assert classifier_type_route({"highway": "valeur_inattendue"}) == "inconnu"
    assert classifier_type_route({}) == "inconnu"


@pytest.mark.parametrize(
    ("waytype", "type_route"),
    [
        (1, "route_principale"),
        (2, "route_secondaire"),
        (3, "rue"),
        (4, "chemin"),
        (5, "piste"),
        (9, "bac"),
        (10, "construction"),
        (99, "inconnu"),
    ],
)
def test_type_route_depuis_waytype_ors(waytype: int, type_route: str) -> None:
    # Le tag OSM est prioritaire ; sans tag, l'indice ORS prend le relais.
    assert classifier_type_route({}, waytype=waytype) == type_route
    assert classifier_type_route({"highway": "primary"}, waytype=3) == "route_principale"


# --- 2. Réponses externes -------------------------------------------------------


def test_ors_normalise_les_extras_deux_formes() -> None:
    base = {
        "features": [
            {
                "properties": {
                    "summary": {"distance": 1000, "duration": 60},
                    "extras": {
                        "osmium": {"values": [[0, 1, 42]]},
                        "surface": [[0, 1, "asphalt"]],
                        "waytype": {"valeurs": "ignoré"},
                    },
                },
                "geometry": {"coordinates": [[43.6, -23.3], [43.7, -23.4]]},
            }
        ]
    }
    itineraire = interpreter_ors(base)
    assert itineraire.extras["osmium"] == [[0, 1, 42]]
    assert itineraire.extras["surface"] == [[0, 1, "asphalt"]]
    assert "waytype" not in itineraire.extras


def test_overpass_normalise_les_voies() -> None:
    reponse = {
        "elements": [
            {"type": "way", "id": 42, "tags": {"smoothness": "good", "highway": "primary"}},
            {"type": "node", "id": 7, "tags": {"name": "x"}},
            {"type": "way", "id": 43},
        ]
    }
    voies = interpreter_overpass(reponse)
    assert voies == {42: {"smoothness": "good", "highway": "primary"}}


def test_overpass_illisible_est_une_erreur_explicite() -> None:
    with pytest.raises(ServiceExterneError):
        interpreter_overpass({"inattendu": True})


# --- 3. Découpe du tracé --------------------------------------------------------


class _VoiesFactices:
    def __init__(self, tags: dict[int, dict[str, str]]) -> None:
        self.tags = tags
        self.enregistre: dict[int, dict[str, str]] | None = None

    def lire_valides(self, osm_ids, maintenant):  # noqa: ANN001 - doublure
        return {osm_id: self.tags[osm_id] for osm_id in osm_ids if osm_id in self.tags}

    def enregistrer_tags(self, tags, *, expire_le, maintenant):  # noqa: ANN001
        self.enregistre = tags


class _OverpassEnPanne:
    def tags_voies(self, osm_ids):  # noqa: ANN001
        raise ServiceExterneError("indisponible", details={"fournisseur": "overpass"})

    def tags_autour(self, points, rayon_m=25):  # noqa: ANN001
        raise ServiceExterneError("indisponible", details={"fournisseur": "overpass"})


class _OverpassVide:
    def tags_voies(self, osm_ids):  # noqa: ANN001
        return {}

    def tags_autour(self, points, rayon_m=25):  # noqa: ANN001
        return [None] * len(points)


def _service(tags: dict[int, dict[str, str]], overpass=None) -> ItineraireService:
    service = ItineraireService.__new__(ItineraireService)
    service._voies = _VoiesFactices(tags)  # type: ignore[attr-defined]
    service._settings = SimpleNamespace(osm_tags_cache_jours=30)  # type: ignore[attr-defined]
    service._overpass = overpass or _OverpassVide()  # type: ignore[attr-defined]
    return service


COORDONNEES = [
    [43.60, -23.30],
    [43.61, -23.31],
    [43.62, -23.32],
    [43.63, -23.33],
    [43.64, -23.34],
    [43.65, -23.35],
]


def test_troncons_sont_continus_et_classes() -> None:
    service = _service(
        {
            101: {"smoothness": "good"},
            102: {"highway": "track", "tracktype": "grade3"},
            103: {"highway": "service", "access": "private"},
        }
    )
    extras = {"osmium": [[0, 2, 101], [2, 4, 102], [4, 5, 103]]}
    troncons = service._classer_troncons(COORDONNEES, extras)

    assert [t.niveau for t in troncons] == ["bon", "mauvais", "impraticable"]
    assert [t.nature for t in troncons] == ["physique", "physique", "reglementaire"]
    assert troncons[0].debut == 0 and troncons[-1].fin == len(COORDONNEES) - 1
    # Aucun trou : chaque portion reprend l'indice où la précédente s'arrête.
    for precedent, suivant in zip(troncons, troncons[1:], strict=False):
        assert suivant.debut == precedent.fin
    assert all(t.distance_m > 0 for t in troncons)


def test_le_type_de_route_est_independant_de_la_qualite() -> None:
    """Une qualité inconnue ne cache pas un type connu (et inversement)."""
    service = _service(
        {
            101: {"highway": "primary", "surface": "asphalt"},
            102: {"highway": "track", "tracktype": "grade3"},
            103: {"highway": "residential", "access": "private"},
        }
    )
    extras = {"osmium": [[0, 2, 101], [2, 4, 102], [4, 5, 103]]}
    troncons = service._classer_troncons(COORDONNEES, extras)

    assert [t.type_route for t in troncons] == ["route_principale", "piste", "rue"]
    # Le premier tronçon n'a pas de smoothness : qualité inconnue, type connu.
    assert troncons[0].niveau == "inconnu"


def test_type_route_par_waytype_quand_osm_ne_rend_pas_de_tag() -> None:
    service = _service({})
    extras = {"waytype": [[0, 3, 1], [3, 6, 5]], "surface": [[0, 6, 3]]}
    troncons = service._classer_troncons(COORDONNEES, extras)

    assert [t.type_route for t in troncons] == ["route_principale", "piste"]


def test_cache_sans_type_rederive_depuis_les_extras() -> None:
    """Une entrée de cache antérieure à l'axe « type » reste exploitable."""
    service = _service({})
    bruts = [
        {
            "debut": 0,
            "fin": 6,
            "niveau": "inconnu",
            "nature": "inconnue",
            "raison": "",
            "estime": True,
            "tags": {},
            "distance_m": 1000,
        }
    ]
    troncons = service._troncons_du_cache(
        bruts, COORDONNEES, {"waytype": [[0, 6, 3]]}
    )
    assert [t.type_route for t in troncons] == ["rue"]


def test_un_trou_osm_devient_inconnu_sans_casser_la_continuite() -> None:
    service = _service({101: {"smoothness": "good"}})
    extras = {"osmium": [[1, 3, 101]]}
    troncons = service._classer_troncons(COORDONNEES, extras)

    assert [t.niveau for t in troncons] == ["inconnu", "bon", "inconnu"]
    assert troncons[0].debut == 0
    assert troncons[-1].fin == len(COORDONNEES) - 1


def test_overpass_indisponible_donne_une_qualite_inconnue() -> None:
    service = _service({}, overpass=_OverpassEnPanne())
    extras = {"osmium": [[0, 2, 101], [2, 5, 102]]}
    troncons = service._classer_troncons(COORDONNEES, extras)

    assert len(troncons) == 2
    assert all(t.niveau == "inconnu" for t in troncons)
    assert {t.nature for t in troncons} == {"inconnue"}


def test_sans_extras_le_trajet_reste_affiche_en_inconnu() -> None:
    service = _service({})
    troncons = service._classer_troncons(COORDONNEES, {})

    assert len(troncons) == 1
    assert troncons[0].niveau == "inconnu"
    assert troncons[0].estime is True
    assert troncons[0].debut == 0
    assert troncons[0].fin == len(COORDONNEES) - 1


def test_sans_osmid_les_extras_ors_classent_les_portions_en_estime() -> None:
    """ORS ne rend pas d'osmid en profil voiture : la segmentation vient des
    extras ORS eux-mêmes, et chaque portion reste **estimée** (jamais « bon »)."""

    service = _service({})
    extras = {
        "surface": [[0, 3, 3], [3, 6, 2]],  # asphalt puis unpaved
        "waytype": [[0, 6, 2]],  # route
        "waycategory": [[0, 6, 64]],  # route revêtue
    }
    troncons = service._classer_troncons(COORDONNEES, extras)

    assert [t.niveau for t in troncons] == ["tolerable", "mauvais"]
    assert all(t.estime for t in troncons)
    assert all(t.niveau != "bon" for t in troncons)
    assert troncons[0].debut == 0 and troncons[-1].fin == len(COORDONNEES) - 1
    for precedent, suivant in zip(troncons, troncons[1:], strict=False):
        assert suivant.debut == precedent.fin


def test_overpass_en_panne_ne_rend_pas_les_extras_inconnus() -> None:
    """Un échec Overpass ne doit pas annuler l'estimation des extras ORS."""
    service = _service({}, overpass=_OverpassEnPanne())
    extras = {"surface": [[0, 6, 3]], "waytype": [[0, 6, 2]]}
    troncons = service._classer_troncons(COORDONNEES, extras)

    assert [t.niveau for t in troncons] == ["tolerable"]
    assert troncons[0].estime is True


def test_overpass_proximite_affine_une_portion_sans_toucher_aux_autres() -> None:
    """L'affinage par proximité est un plus, portion par portion : la portion
    trouvée passe au niveau exact, les autres gardent leur estimation."""

    class _OverpassProche:
        def tags_autour(self, points, rayon_m=25):  # noqa: ANN001
            # Les segments sont interrogés du plus long au plus court : le
            # second point correspond ici à la portion [0, 2].
            return [None, {"osm_id": 101, "tags": {"smoothness": "good"}}]

    service = _service({}, overpass=_OverpassProche())
    # Deux portions distinctes (asphalt, concrete) : la première est affinée
    # par Overpass, la seconde garde son estimation d'après les extras.
    extras = {"surface": [[0, 2, 3], [2, 6, 4]], "waytype": [[0, 6, 2]]}
    troncons = service._classer_troncons(COORDONNEES, extras)

    assert troncons[0].niveau == "bon"
    assert troncons[0].estime is False  # smoothness explicite
    assert troncons[1].niveau == "tolerable"
    assert troncons[1].estime is True


def test_extras_non_decodes_restent_inconnus_sans_invention() -> None:
    service = _service({})
    troncons = service._classer_troncons(
        COORDONNEES, {"surface": [[0, 6, 99]], "waytype": [[0, 6, 99]]}
    )

    assert [t.niveau for t in troncons] == ["inconnu"]
    assert troncons[0].estime is True


# --- 5. Réponse rapide : l'affinage vit en arrière-plan ------------------------


def test_hors_affinage_aucun_appel_overpass() -> None:
    """Le chemin de la requête HTTP ne doit jamais attendre Overpass."""

    class _Espion:
        def tags_autour(self, points, rayon_m=25):  # noqa: ANN001
            raise AssertionError("Overpass appelé dans la requête HTTP")

        def tags_voies(self, osm_ids):  # noqa: ANN001
            raise AssertionError("Overpass appelé dans la requête HTTP")

    service = _service({}, overpass=_Espion())
    extras = {"surface": [[0, 6, 3]], "waytype": [[0, 6, 2]]}
    troncons = service._classer_troncons(COORDONNEES, extras, affiner=False)

    assert [t.niveau for t in troncons] == ["tolerable"]
    assert troncons[0].estime is True


def test_affiner_tache_met_le_cache_a_jour_avec_garde_quotidienne(
    monkeypatch: pytest.MonkeyPatch,
    api_session_factory,
) -> None:
    """La tâche d'arrière-plan reclasse les portions et se garde un essai/jour."""
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import select

    import app.infrastructure.database as base
    from app.domain.geo import PROFIL_ITINERAIRE, ItineraireCache

    monkeypatch.setattr(base, "SessionLocal", api_session_factory)
    cle = "test-affinage-arriere-plan"
    with api_session_factory() as session:
        session.add(
            ItineraireCache(
                cle=cle,
                profil=PROFIL_ITINERAIRE,
                origine_latitude=-23.35,
                origine_longitude=43.66,
                destination_latitude=-23.36,
                destination_longitude=43.67,
                distance_m=8000,
                duree_s=600,
                geometrie={
                    "coordonnees": [list(point) for point in COORDONNEES],
                    "extras": {"surface": [[0, 6, 3]], "waytype": [[0, 6, 2]]},
                },
                expire_le=datetime.now(UTC) + timedelta(days=1),
            )
        )
        session.commit()

    classifications: list[int] = []
    original = ItineraireService._classer_troncons

    def _compte(self, coordonnees, extras, affiner=True):  # noqa: ANN001
        classifications.append(1)
        return original(self, coordonnees, extras, affiner=affiner)

    monkeypatch.setattr(ItineraireService, "_classer_troncons", _compte)

    ItineraireService.affiner_tache(cle)
    assert len(classifications) == 1

    with api_session_factory() as session:
        ligne = session.scalars(
            select(ItineraireCache).where(ItineraireCache.cle == cle)
        ).first()
        assert ligne is not None
        donnees = ligne.geometrie or {}
        assert donnees.get("troncons"), "les portions doivent être enregistrées"
        assert donnees["troncons"][0]["niveau"] == "tolerable"
        assert donnees.get("affine_le")

    # Dans les 24 h : aucun nouveau travail (garde anti-saturation).
    ItineraireService.affiner_tache(cle)
    assert len(classifications) == 1


# --- 4. Classificateur extras ORS ----------------------------------------------


def test_extras_ors_construction_est_impraticable_et_estime() -> None:
    resultat = classifier_extras_ors(surface=0, waytype=10, waycategory=0)
    assert resultat.niveau == "impraticable"
    assert resultat.nature == "reglementaire"
    assert resultat.estime is True
    assert "construction" in resultat.raison


def test_extras_ors_asphalt_n_est_jamais_bon() -> None:
    resultat = classifier_extras_ors(surface=3, waytype=2, waycategory=64)
    assert resultat.niveau == "tolerable"
    assert resultat.estime is True
    assert "asphalt" in resultat.raison


def test_extras_ors_unpaved_est_mauvais_estime() -> None:
    for surface in (2, 10, 11, 12):
        resultat = classifier_extras_ors(surface=surface, waytype=2, waycategory=0)
        assert resultat.niveau == "mauvais", surface
        assert resultat.estime is True


def test_extras_ors_gue_et_piste_par_waycategory() -> None:
    gue = classifier_extras_ors(surface=None, waytype=None, waycategory=128)
    assert gue.niveau == "mauvais"
    assert "gué" in gue.raison
    piste = classifier_extras_ors(surface=None, waytype=None, waycategory=16)
    assert piste.niveau == "mauvais"


def test_extras_ors_voie_non_routiere_est_mauvaise_estimee() -> None:
    for waytype in (4, 6, 7, 8):
        resultat = classifier_extras_ors(surface=None, waytype=waytype, waycategory=0)
        assert resultat.niveau == "mauvais", waytype
        assert resultat.estime is True


def test_extras_ors_rien_d_exploitable_reste_inconnu() -> None:
    resultat = classifier_extras_ors(surface=0, waytype=2, waycategory=0)
    assert resultat.niveau == "inconnu"
    assert resultat.estime is True


def test_indices_hors_geometrie_sont_ramenes_dans_le_trace() -> None:
    service = _service({101: {"smoothness": "good"}})
    extras = {"osmium": [[0, 99, 101]]}
    troncons = service._classer_troncons(COORDONNEES, extras)

    assert all(0 <= t.debut <= t.fin <= len(COORDONNEES) - 1 for t in troncons)


def test_longueur_troncon_ne_depend_pas_du_sens_de_lecture() -> None:
    avance = ItineraireService._longueur_m(COORDONNEES, 0, 3)
    recule = ItineraireService._longueur_m(list(reversed(COORDONNEES)), 2, 5)
    assert avance == pytest.approx(recule, abs=2)
