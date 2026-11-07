"""Rasterisation SVG → PNG, **sans navigateur** (incrément 69).

Pourquoi ce module : Word ne sait pas intégrer un SVG dans un ``.docx``
produit par ``python-docx``. Un graphique doit donc devenir un PNG **avant**
d'entrer dans le DOCX. La première version passait par le Chromium déjà utilisé
pour le PDF ; c'était un mauvais choix de déploiement — Playwright pèse 107 Mo,
tire ``ms-playwright``, et installer un navigateur sur une plateforme cloud est
exactement le genre de dépendance qu'on cherche à éviter.

``resvg-py`` fait le même travail mieux :

- **1,2 Mo** de wheel natif (contre 107 Mo pour Playwright) ;
- **aucune dépendance Python** (queues de rendu Rust compilées) ;
- **aucun binaire système** à installer (pas de navigateur, pas de GTK) ;
- rendu en ~130 ms par graphique (mesuré), contre le lancement d'un navigateur ;
- parseur **strict** : resvg rejette ce que Chromium tolère, et a d'ailleurs
  immédiatement révélé un attribut ``d`` mal formé que le navigateur masquait.

Le PDF, lui, garde Chromium : il rend le SVG nativement et n'a donc **jamais**
besoin de ce module. C'est pour cela que ``rendu.py`` ne l'appelle que pour un
DOCX.

Import paresseux et exception explicite, comme ``pdf_renderer`` : le backend doit
démarrer sans ``resvg-py`` installé (extra ``documents_pro``), et son absence doit
produire une erreur lisible au premier appel — jamais un graphique silencieusement
absent d'un livrable.
"""

from __future__ import annotations

import math
import re

from app.core.errors import ValidationError

__all__ = [
    "RasterisationIndisponible",
    "dimension_px",
    "rasterisation_disponible",
    "rasteriser_svg",
    "taille_svg",
]

#: Valeur d'attribut SVG : guillemets doubles **ou** simples. Les deux écritures
#: sont légales et resvg honore les deux — une garde qui n'en lit qu'une seule se
#: contourne par un simple changement de guillemets (voir ``_valeur_attribut``).
_RE_VALEUR_ATTRIBUT = r'''(?:"([^"]*)"|'([^']*)')'''

#: Nombre SVG : décimal, signé, ou en notation scientifique (``9e4``).
_RE_NOMBRE_SVG = re.compile(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")

#: Balise racine ``<svg …>`` : seule elle porte la taille du document. Chercher
#: ``width`` dans tout le fichier tombait sur le premier ``<rect width=…>``.
_RE_BALISE_RACINE = re.compile(r"<svg\b[^>]*>", re.IGNORECASE)

#: Plafond de sortie : au-delà, le PNG devient énorme sans gain de netteté.
MAX_PIXELS = 4000
#: Échelle par défaut : 2.0 donne un rendu net à l'impression (≈ 192 dpi).
ECHELLE_DEFAUT = 2.0


class RasterisationIndisponible(RuntimeError):
    """Le SVG n'a pas pu être converti en PNG (dépendance absente ou SVG invalide)."""


def _balise_racine(svg: str) -> str:
    """Balise d'ouverture ``<svg …>`` (chaîne vide si absente)."""
    correspondance = _RE_BALISE_RACINE.search(svg or "")
    return correspondance.group(0) if correspondance else ""


def _valeur_attribut(svg: str, nom: str) -> str | None:
    """Valeur brute d'un attribut SVG, **quel que soit** le type de guillemets.

    Une lecture limitée aux guillemets doubles laissait passer un SVG déclarant
    ``width='44000' height='51200'`` : la taille retombait sur 640×400, la garde
    de plafond était satisfaite, et resvg rendait réellement 88 000 × 102 400 px
    à l'échelle 2 — soit 36 Go de pixmap, allocation qui a tué le processus
    backend (INC-135, suivi).
    """
    motif = re.compile(
        r"\b" + re.escape(nom) + r"\s*=\s*" + _RE_VALEUR_ATTRIBUT, re.IGNORECASE
    )
    correspondance = motif.search(_balise_racine(svg))
    if not correspondance:
        return None
    valeur = correspondance.group(1)
    if valeur is None:
        valeur = correspondance.group(2)
    return valeur


def _viewbox(svg: str) -> tuple[float, float] | None:
    """Largeur/hauteur du ``viewBox`` (``0 0 640 400``), si présentes et plausibles."""
    brut = _valeur_attribut(svg, "viewBox")
    if not brut:
        return None
    nombres: list[float] = []
    for nombre in _RE_NOMBRE_SVG.findall(brut):
        try:
            nombres.append(float(nombre))
        except ValueError:
            return None
    if len(nombres) < 4:
        return None
    largeur, hauteur = nombres[2], nombres[3]
    if not (math.isfinite(largeur) and math.isfinite(hauteur)):
        return None
    if largeur <= 0 or hauteur <= 0:
        return None
    return largeur, hauteur


def _dimension_attribut(svg: str, attribut: str) -> float | None:
    """Lit ``width``/``height`` en pixels, si présent et plausible."""
    return dimension_px(_valeur_attribut(svg, attribut))


def dimension_px(valeur: object) -> float | None:
    """Lit une dimension CSS en pixels : ``400``, ``400px``, ``400.5``.

    Unité unique **pixels** (convention 96 dpi du HTML) : une valeur en ``%``,
    ``em`` ou ``mm`` n'est pas exploitable ici et retourne ``None`` — l'appelant
    retombe alors sur sa propre mesure. Un seul analyseur partagé par les deux
    lecteurs d'image (``docx_renderer`` pour ``<img>``, ``html_builder`` pour
    ``<svg>``), pour que PDF et DOCX donnent la même place au même schéma.
    """
    brut = str(valeur or "").strip().lower().removesuffix("px").strip()
    try:
        nombre = float(brut)
    except ValueError:
        return None
    return nombre if nombre > 0 and math.isfinite(nombre) else None


def taille_svg(svg: str) -> tuple[float, float]:
    """Dimensions du graphique en pixels (viewBox, sinon ``width``/``height``).

    Un SVG du moteur déclare toujours un ``viewBox`` : ce repli sert aux SVG
    écrits à la main (tests, graphiques futurs). Une dimension illisible retombe
    sur la taille par défaut plutôt que d'échouer sur un détail d'analyse.
    """
    boite = _viewbox(svg)
    if boite is not None:
        return boite
    return (
        _dimension_attribut(svg, "width") or 640.0,
        _dimension_attribut(svg, "height") or 400.0,
    )


def rasterisation_disponible() -> bool:
    """``resvg-py`` est-il importable ? (sonde sans effet de bord.)"""
    try:
        _moteur()
    except RasterisationIndisponible:
        return False
    return True


def _moteur():
    """Import paresseux de ``resvg_py`` — le backend démarre sans lui."""
    try:
        import resvg_py
    except ImportError as erreur:  # pragma: no cover - dépend du déploiement
        raise RasterisationIndisponible(
            "Rasterisation SVG indisponible : le paquet 'resvg-py' n'est pas "
            "installé. Installe l'extra documents_pro (pip install -e '.[documents_pro]')."
        ) from erreur
    return resvg_py


def rasteriser_svg(
    svg: str,
    *,
    echelle: float = ECHELLE_DEFAUT,
    fond: str | None = "white",
) -> bytes:
    """Convertit un SVG en PNG (octets), via ``resvg-py``.

    La taille de sortie est **imposée** à ``largeur × échelle`` par
    ``largeur × hauteur`` explicites : ``zoom`` laissait resvg choisir d'après la
    taille déclarée dans le fichier et allouer le pixmap correspondant, sans
    plafond (un SVG de 44 000 × 51 200 px à l'échelle 2 demandait 36 Go et tuait
    le processus). Avec ``width``/``height``, l'allocation est bornée par
    ``MAX_PIXELS`` quel que soit le contenu du SVG.

    Args:
        svg: document SVG produit par le moteur (``graphiques.py``).
        echelle: multiplicateur de taille (2.0 ⇒ net à l'impression).
        fond: couleur de fond (``None`` ⇒ transparence). Blanc par défaut : un
            graphique de livrable doit rester lisible sur une page imprimée.

    Returns:
        Octets PNG.

    Raises:
        ValidationError: SVG vide ou sans balise ``<svg>`` (erreur d'appel).
        RasterisationIndisponible: dépendance absente, SVG que resvg refuse, ou
            sortie démesurée — jamais un PNG vide ou un succès silencieux.
    """
    if not svg or "<svg" not in svg:
        raise ValidationError("Rasterisation demandée sur un SVG vide ou invalide.")

    largeur, hauteur = taille_svg(svg)
    cible_largeur = max(1, round(largeur * echelle))
    cible_hauteur = max(1, round(hauteur * echelle))
    if cible_largeur > MAX_PIXELS or cible_hauteur > MAX_PIXELS:
        raise RasterisationIndisponible(
            f"Graphique trop grand pour la rasterisation : {largeur:.0f} × {hauteur:.0f} px "
            f"à l'échelle {echelle} dépasse {MAX_PIXELS} px."
        )

    resvg_py = _moteur()
    try:
        octets = bytes(
            resvg_py.svg_to_bytes(
                svg_string=svg,
                width=cible_largeur,
                height=cible_hauteur,
                background=fond,
            )
        )
    except Exception as erreur:  # noqa: BLE001 - la cause réelle est transmise
        raise RasterisationIndisponible(
            f"Rasterisation SVG refusée ({type(erreur).__name__}) : {erreur}"
        ) from erreur

    if not octets:
        raise RasterisationIndisponible("Rasterisation SVG impossible : PNG vide.")
    return octets
