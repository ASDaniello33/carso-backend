"""Qualité des routes selon OpenStreetMap — classification **pure**.

Un itinéraire ORS est découpé en tronçons. Ce module traduit — sans réseau ni
base — les données de chaque tronçon en un niveau lisible, testable valeur par
valeur. Deux sources, jamais mélangées :

- ``classifier_route`` : les **tags OSM** d'une voie (``smoothness``,
  ``tracktype``, ``access``…), obtenus via Overpass ;
- ``classifier_extras_ors`` : les **extras ORS** (``surface``, ``waytype``,
  ``waycategory``), indices dérivés des tags OSM au moment du calcul — premier
  passage quand ORS ne rend pas d'identifiants de voies.

Règles (demande produit, profil automobile) :

- ``smoothness=excellent|good`` → **bon** ;
- ``smoothness=intermediate`` → **tolérable** ;
- ``smoothness=bad|very_bad|horrible|very_horrible`` → **mauvais** ;
- ``smoothness=impassable``, voie en construction, ou accès OSM incompatible
  avec une voiture → **impraticable** ;
- aucune donnée fiable → **inconnu**. ``tracktype``, ``surface`` et les extras
  ORS servent d'indicateurs **secondaires**, toujours marqués **estimés** ;
  un revêtement (asphalt…) ne suffit jamais à dire « bon ».

On distingue une route **physiquement** dégradée d'une route **réglementée**
(privée, agricole, en construction) : la carte ne doit pas laisser croire qu'un
panneau interdit est un nid-de-poule. Rien n'est présenté comme un état
temps réel : OSM peut être incomplet ou daté.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal

NiveauRoute = Literal["bon", "tolerable", "mauvais", "impraticable", "inconnu"]
NatureBlocage = Literal["physique", "reglementaire", "inconnue"]

#: Nature de la voie — axe **indépendant** de la qualité. Une route dont l'état
#: est inconnu garde un type connu : c'est précisément ce que l'utilisateur
#: demande en choisissant l'affichage « type de route ».
TypeRoute = Literal[
    "autoroute",
    "route_principale",
    "route_secondaire",
    "rue",
    "piste",
    "chemin",
    "bac",
    "construction",
    "inconnu",
]

NIVEAUX: tuple[NiveauRoute, ...] = (
    "bon",
    "tolerable",
    "mauvais",
    "impraticable",
    "inconnu",
)

TYPES: tuple[TypeRoute, ...] = (
    "autoroute",
    "route_principale",
    "route_secondaire",
    "rue",
    "piste",
    "chemin",
    "bac",
    "construction",
    "inconnu",
)

#: Libellés de type (la carte, la légende et le résumé emploient les mêmes mots).
LIBELLES_TYPE: dict[str, str] = {
    "autoroute": "Autoroute",
    "route_principale": "Route principale",
    "route_secondaire": "Route secondaire",
    "rue": "Rue / voie urbaine",
    "piste": "Piste",
    "chemin": "Chemin piéton ou cyclable",
    "bac": "Bac / ferry",
    "construction": "En construction",
    "inconnu": "Type inconnu",
}

#: Libellés affichés (la carte et le résumé emploient les mêmes mots).
LIBELLES_NIVEAU: dict[str, str] = {
    "bon": "Bon état",
    "tolerable": "Tolérable",
    "mauvais": "Mauvais état",
    "impraticable": "Impassable ou interdit",
    "inconnu": "Qualité inconnue",
}

_SMOOTHNESS: dict[str, NiveauRoute] = {
    "excellent": "bon",
    "good": "bon",
    "intermediate": "tolerable",
    "bad": "mauvais",
    "very_bad": "mauvais",
    "horrible": "mauvais",
    "very_horrible": "mauvais",
    "impassable": "impraticable",
}

#: ``tracktype`` (pistes) — indicateur **secondaire**, donc estimé.
_TRACKTYPE: dict[str, NiveauRoute] = {
    "grade1": "tolerable",
    "grade2": "tolerable",
    "grade3": "mauvais",
    "grade4": "mauvais",
    "grade5": "impraticable",
}

#: Valeurs d'accès qui ferment la voie à une voiture de service.
_ACCES_REFUSES = frozenset(
    {"no", "private", "agricultural", "forestry", "delivery", "customers"}
)
#: Valeurs qui ouvrent explicitement (une construction peut être praticable).
_ACCES_OUVERTS = frozenset({"yes", "permissive", "designated"})

#: Tags retenus pour l'infobulle — jamais recopiés en masse. ``check_date`` et
#: ``survey:date`` disent **quand** l'état a été vérifié : une donnée peut être
#: exacte mais ancienne, et l'interface doit pouvoir le montrer.
TAGS_AFFICHES = (
    "highway",
    "construction",
    "smoothness",
    "tracktype",
    "surface",
    "check_date",
    "survey:date",
    "access",
    "vehicle",
    "motor_vehicle",
    "motorcar",
)

# --- Type de voie ---------------------------------------------------------------
#
# Deux sources, dans cet ordre : le tag ``highway`` d'OpenStreetMap (le plus
# précis) puis l'indice ``waytype`` d'OpenRouteService, qui dérive du même tag
# au moment du calcul. Aucune des deux ne renseigne la qualité : le type et
# l'état restent deux axes séparés.

_HIGHWAY_VERS_TYPE: dict[str, TypeRoute] = {
    "motorway": "autoroute",
    "motorway_link": "autoroute",
    "trunk": "route_principale",
    "trunk_link": "route_principale",
    "primary": "route_principale",
    "primary_link": "route_principale",
    "secondary": "route_secondaire",
    "secondary_link": "route_secondaire",
    "tertiary": "route_secondaire",
    "tertiary_link": "route_secondaire",
    "unclassified": "rue",
    "residential": "rue",
    "service": "rue",
    "living_street": "rue",
    "road": "rue",
    "track": "piste",
    "path": "chemin",
    "footway": "chemin",
    "pedestrian": "chemin",
    "cycleway": "chemin",
    "bridleway": "chemin",
    "steps": "chemin",
    "crossing": "chemin",
    "ferry": "bac",
    "construction": "construction",
}

#: ``extras.waytype.values`` d'ORS — table officielle (vérifiée le 24/10/2026) :
#: 1 State Road, 2 Road, 3 Street, 4 Path, 5 Track, 6 Cycleway, 7 Footway,
#: 8 Steps, 9 Ferry, 10 Construction.
_WAYTYPE_VERS_TYPE: dict[int, TypeRoute] = {
    1: "route_principale",
    2: "route_secondaire",
    3: "rue",
    4: "chemin",
    5: "piste",
    6: "chemin",
    7: "chemin",
    8: "chemin",
    9: "bac",
    10: "construction",
}

# --- Décodage des extras OpenRouteService -------------------------------------
#
# ORS ne rend pas ``osmid`` en profil voiture (warning « not available ») mais
# renvoie, par portion de la géométrie, des indices dérivés des tags OSM :
# ``surface`` (tag surface=…), ``waytype`` (tag highway=…) et ``waycategory``
# (champs de bits). Les tables ci-dessous recopient les listes **officielles**
# d'ORS — giscience.github.io/openrouteservice, « Surface IDs », « WayType
# IDs », « WayCategory IDs ». Un indice inconnu de ces tables (nouvelle version
# d'ORS) reste « inconnu » : on ne décode pas à l'aveugle.

#: ``extras.surface.values`` → nom de tag OSM ``surface`` correspondant.
#: La valeur 5 (Cobblestone) a été retirée par ORS : elle n'est pas décodée.
_SURFACE_NOMS: dict[int, str] = {
    0: "inconnu",
    1: "paved",
    2: "unpaved",
    3: "asphalt",
    4: "concrete",
    6: "metal",
    7: "wood",
    8: "compacted",
    9: "fine_gravel",
    10: "gravel",
    11: "dirt",
    12: "ground",
    13: "ice/snow",
    14: "paving_stones",
    15: "sand",
    16: "woodchips",
    17: "grass",
    18: "grass_paver",
}

#: Revêtements roulables mais dont l'état reste inconnu — jamais « bon ».
_SURFACE_ROULABLES = frozenset({1, 3, 4, 6, 8, 14})
#: Revêtements dégradés ou hors route pour une voiture de service.
_SURFACE_DEGRADEES = frozenset({2, 7, 9, 10, 11, 12, 13, 15, 16, 17, 18})

#: ``extras.waytype.values`` (tag highway=…).
_WAYTYPE_NOMS: dict[int, str] = {
    0: "inconnu",
    1: "route principale",
    2: "route",
    3: "rue",
    4: "sentier",
    5: "piste",
    6: "piste cyclable",
    7: "trottoir",
    8: "escalier",
    9: "bac",
    10: "construction",
}

#: Types de voie non routiers — anormaux dans un itinéraire automobile.
_WAYTYPE_NON_ROUTIERS = frozenset({4, 6, 7, 8})

#: ``extras.waycategory.values`` — champs de bits (somme possible).
_WAYCATEGORY_BITS: dict[int, str] = {
    1: "autoroute",
    2: "escalier",
    4: "route non revêtue",
    8: "bac",
    16: "piste",
    32: "tunnel",
    64: "route revêtue",
    128: "gué (ford)",
}


@dataclass(frozen=True, slots=True)
class Classification:
    """Niveau d'un tronçon et ce qui le justifie."""

    niveau: NiveauRoute
    nature: NatureBlocage
    raison: str
    #: Vrai quand le niveau vient d'un indicateur secondaire (``tracktype``,
    #: extras ORS) plutôt que d'un tag explicite (``smoothness``, ``access``).
    estime: bool = False
    tags: dict[str, str] = field(default_factory=dict)

    @property
    def libelle(self) -> str:
        return LIBELLES_NIVEAU[self.niveau]


def classifier_type_route(
    tags: Mapping[str, str], *, waytype: int | None = None
) -> TypeRoute:
    """Type de voie d'après les tags OSM, à défaut l'indice ``waytype`` ORS.

    Args:
        tags: tags OSM de la voie (``highway``, ``route=ferry``…).
        waytype: indice de ``extras.waytype`` ORS, utilisé seulement quand les
            tags ne disent rien. Un indice hors table reste ``inconnu``.

    Returns:
        Un type de voie. L'absence d'information n'est jamais comblée par une
        supposition : ce sera ``inconnu``.
    """
    highway = (tags.get("highway") or "").strip().lower()
    if highway in _HIGHWAY_VERS_TYPE:
        return _HIGHWAY_VERS_TYPE[highway]
    if (tags.get("route") or "").strip().lower() == "ferry":
        return "bac"
    if isinstance(waytype, int):
        return _WAYTYPE_VERS_TYPE.get(waytype, "inconnu")
    return "inconnu"


def _tags_retenus(tags: Mapping[str, str]) -> dict[str, str]:
    return {
        cle: str(tags[cle])
        for cle in TAGS_AFFICHES
        if tags.get(cle) not in (None, "")
    }


def _acces_refuse(tags: Mapping[str, str]) -> str | None:
    """Premier tag d'accès qui ferme la voie (``motorcar`` prioritaire)."""
    for cle in ("motorcar", "motor_vehicle", "vehicle", "access"):
        valeur = (tags.get(cle) or "").strip().lower()
        if valeur in _ACCES_REFUSES:
            return f"{cle}={valeur}"
        if valeur in _ACCES_OUVERTS:
            return None
    return None


def classifier_route(tags: Mapping[str, str]) -> Classification:
    """Traduit les tags OSM d'une voie en niveau de qualité.

    Args:
        tags: tags OSM de la voie (``smoothness``, ``tracktype``, ``access``…).

    Returns:
        Une classification motivée. ``inconnu`` quand aucune donnée fiable
        n'existe — jamais « bon » par défaut.
    """
    retenus = _tags_retenus(tags)

    acces = _acces_refuse(tags)
    if acces is not None:
        return Classification(
            niveau="impraticable",
            nature="reglementaire",
            raison=f"Accès restreint par OpenStreetMap ({acces}) : trajet non autorisé.",
            tags=retenus,
        )

    if (tags.get("highway") or "").strip().lower() == "construction":
        return Classification(
            niveau="impraticable",
            nature="reglementaire",
            raison="Voie en construction d'après OpenStreetMap : non ouverte à la circulation.",
            tags=retenus,
        )

    smoothness = (tags.get("smoothness") or "").strip().lower()
    if smoothness in _SMOOTHNESS:
        niveau = _SMOOTHNESS[smoothness]
        if niveau == "impraticable":
            return Classification(
                niveau=niveau,
                nature="physique",
                raison="smoothness=impassable : route non praticable en voiture.",
                tags=retenus,
            )
        return Classification(
            niveau=niveau,
            nature="physique",
            raison=f"smoothness={smoothness}.",
            tags=retenus,
        )

    tracktype = (tags.get("tracktype") or "").strip().lower()
    if tracktype in _TRACKTYPE:
        return Classification(
            niveau=_TRACKTYPE[tracktype],
            nature="physique",
            raison=(
                f"Estimé d'après tracktype={tracktype} (pas de smoothness) : "
                "l'état réel peut différer."
            ),
            estime=True,
            tags=retenus,
        )

    surface = (tags.get("surface") or "").strip().lower()
    raison = (
        f"Aucune donnée de qualité fiable (surface={surface} ne suffit pas)."
        if surface
        else "Aucune donnée de qualité dans OpenStreetMap pour cette voie."
    )
    return Classification(
        niveau="inconnu",
        nature="inconnue",
        raison=raison,
        estime=True,
        tags=retenus,
    )


def _bits_waycategory(valeur: int) -> dict[int, str]:
    """Bits réellement portés par une valeur ``waycategory`` ORS."""
    return {bit: nom for bit, nom in _WAYCATEGORY_BITS.items() if valeur & bit}


def classifier_extras_ors(
    *,
    surface: int | None,
    waytype: int | None,
    waycategory: int | None,
) -> Classification:
    """Classe une portion d'après les extras **ORS** (indicateurs secondaires).

    Les extras ORS sont dérivés des tags OSM au moment du calcul, mais ne
    donnent ni ``smoothness`` ni l'état réel : toute classification produite
    ici est marquée **estimée** et jamais « bon ». Elle sert de premier
    passage quand ORS ne rend pas d'identifiants de voies ; l'affinage
    Overpass (``classifier_route``) la remplace quand il aboutit, portion par
    portion.

    Ordre de priorité (le plus grave d'abord, sans invention) :

    1. ``waytype=10`` (construction) → impraticable ;
    2. ``waycategory`` bit 128 (gué) / bits 4+16 (non revêtu, piste) ou
       ``surface`` dégradée → mauvais ;
    3. type de voie non routier (sentier, trottoir…) → mauvais (anormal pour
       un véhicule) ;
    4. ``surface`` roulable (asphalt, paved…) → tolérable, jamais « bon » ;
    5. rien d'exploitable → inconnu.
    """
    nom_surface = _SURFACE_NOMS.get(surface) if isinstance(surface, int) else None
    nom_waytype = _WAYTYPE_NOMS.get(waytype) if isinstance(waytype, int) else None
    bits = _bits_waycategory(waycategory) if isinstance(waycategory, int) else {}

    if waytype == 10:
        return Classification(
            niveau="impraticable",
            nature="reglementaire",
            raison=(
                "Estimé : portion en construction d'après OpenStreetMap "
                "(via OpenRouteService) : non ouverte à la circulation."
            ),
            estime=True,
            tags={},
        )

    motifs: list[str] = []
    if 128 in bits:
        motifs.append("gué (ford) signalé")
    if surface in _SURFACE_DEGRADEES:
        motifs.append(f"revêtement non carrossable (surface={nom_surface})")
    if 4 in bits:
        motifs.append("route non revêtue signalée")
    if 16 in bits or nom_waytype == "piste":
        motifs.append("piste (track)")
    if motifs:
        return Classification(
            niveau="mauvais",
            nature="physique",
            raison=(
                "Estimé d'après OpenStreetMap (via OpenRouteService) : "
                + " ; ".join(motifs)
                + "."
            ),
            estime=True,
            tags={},
        )

    if waytype in _WAYTYPE_NON_ROUTIERS:
        return Classification(
            niveau="mauvais",
            nature="physique",
            raison=(
                f"Estimé : portion de type « {nom_waytype} » signalée par "
                "OpenStreetMap (via OpenRouteService) — anormal pour un véhicule."
            ),
            estime=True,
            tags={},
        )

    if surface in _SURFACE_ROULABLES:
        return Classification(
            niveau="tolerable",
            nature="physique",
            raison=(
                f"Estimé d'après surface={nom_surface} (pas de smoothness) : "
                "un revêtement ne dit rien de l'état d'usure."
            ),
            estime=True,
            tags={},
        )

    return Classification(
        niveau="inconnu",
        nature="inconnue",
        raison=(
            "Aucun indicateur de qualité exploitable dans les données "
            "OpenStreetMap renvoyées par OpenRouteService pour cette portion."
        ),
        estime=True,
        tags={},
    )


__all__ = [
    "LIBELLES_NIVEAU",
    "LIBELLES_TYPE",
    "NIVEAUX",
    "TAGS_AFFICHES",
    "TYPES",
    "Classification",
    "NatureBlocage",
    "NiveauRoute",
    "TypeRoute",
    "classifier_extras_ors",
    "classifier_route",
    "classifier_type_route",
]
