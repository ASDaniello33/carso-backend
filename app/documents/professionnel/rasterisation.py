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

#: Motif du ``viewBox`` : c'est lui qui décide de la taille de sortie.
_RE_VIEWBOX = re.compile(
    r'viewBox\s*=\s*"\s*([-\d.eE]+)[\s,]+([-\d.eE]+)[\s,]+([\d.eE]+)[\s,]+([\d.eE]+)\s*"'
)

#: Plafond de sortie : au-delà, le PNG devient énorme sans gain de netteté.
MAX_PIXELS = 4000
#: Échelle par défaut : 2.0 donne un rendu net à l'impression (≈ 192 dpi).
ECHELLE_DEFAUT = 2.0


class RasterisationIndisponible(RuntimeError):
    """Le SVG n'a pas pu être converti en PNG (dépendance absente ou SVG invalide)."""


def _dimension_attribut(svg: str, attribut: str) -> float | None:
    """Lit ``width``/``height`` en pixels, si présent et plausible."""
    motif = re.compile(r"\b" + attribut + r'\s*=\s*"\s*([\d.]+)\s*(?:px)?\s*"')
    correspondance = motif.search(svg or "")
    if not correspondance:
        return None
    try:
        valeur = float(correspondance.group(1))
    except ValueError:
        return None
    return valeur if valeur > 0 and math.isfinite(valeur) else None


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
    correspondance = _RE_VIEWBOX.search(svg or "")
    if correspondance:
        largeur, hauteur = float(correspondance.group(3)), float(correspondance.group(4))
        if largeur > 0 and hauteur > 0:
            return largeur, hauteur
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
    if largeur * echelle > MAX_PIXELS or hauteur * echelle > MAX_PIXELS:
        raise RasterisationIndisponible(
            f"Graphique trop grand pour la rasterisation : {largeur:.0f} × {hauteur:.0f} px "
            f"à l'échelle {echelle} dépasse {MAX_PIXELS} px."
        )

    resvg_py = _moteur()
    try:
        octets = bytes(
            resvg_py.svg_to_bytes(svg_string=svg, zoom=echelle, background=fond)
        )
    except Exception as erreur:  # noqa: BLE001 - la cause réelle est transmise
        raise RasterisationIndisponible(
            f"Rasterisation SVG refusée ({type(erreur).__name__}) : {erreur}"
        ) from erreur

    if not octets:
        raise RasterisationIndisponible("Rasterisation SVG impossible : PNG vide.")
    return octets
