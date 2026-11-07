"""Figures partagées par **tous** les outils de documents (INC-123).

Pourquoi ce module : le moteur professionnel savait déjà dessiner des graphiques,
des illustrations et des schémas SVG ; le ``DocumentSpec`` et la conversion
HTML→DOCX/PDF, non. Résultat mesuré avant cet incrément : un agent qui écrivait
un ``<svg>`` dans ``generer_document_html`` obtenait un document **sans** le
schéma (balise retirée silencieusement), et un ``document_spec`` ne pouvait
qu'insérer une image existante — jamais un graphique.

Ce module est la **seule porte des figures**. Il ne dessine rien lui-même : il
compose les briques déjà validées du moteur professionnel.

- ``graphiques.render_chart`` — graphiques de données (bar, line, pie, donut),
  SVG construit par le serveur, textes échappés, rendu déterministe ;
- ``illustrations.render_illustration`` — compositions (icônes, étapes, cycle,
  pyramide, frise, comparaison) où l'agent ne fournit que des libellés courts ;
- ``html_builder.assainir_svg`` — nettoyage d'un SVG **écrit par l'agent**
  (balises interdites, références distantes, attributs d'événement, ``style``
  filtré) : c'est la condition pour accepter du SVG libre ;
- ``rasterisation.rasteriser_svg`` — SVG → PNG par ``resvg-py``, sans navigateur,
  ce qui rend un DOCX/PDF/XLSX indépendant de Playwright.

Règles tenues (les mêmes que le moteur d'origine) :

- **aucun chemin disque ne sort d'ici** : on rend des octets et du balisage ;
- **aucune exception avalée** : une spec invalide lève ``ValidationError``, une
  rasterisation impossible lève ``GenerationError`` avec la consigne exacte —
  jamais une figure absente d'un livrable sans le dire ;
- **tout texte traverse un échappement** : un libellé ne peut pas fermer une
  balise (les générateurs s'en chargent, ce module ne ré-introduit pas de
  concaténation non échappée).

Voir ``docs/analysis/ETUDE-INC-123-SVG-GRAPHIQUES-TOUS-OUTILS-DOCUMENTS.md``.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.core.errors import GenerationError, ValidationError
from app.documents.spec import Forme, Graphique, Illustration, Image

__all__ = [
    "ECHELLE_PNG",
    "TYPES_GRAPHIQUES",
    "TYPES_ILLUSTRATIONS",
    "png_depuis_svg",
    "spec_graphique",
    "spec_illustration",
    "svg_depuis_bloc",
    "svg_forme",
    "svg_graphique",
    "svg_illustration",
]

#: Échelle de rasterisation : 2× donne un rendu net à l'impression (~192 dpi).
#: Alignée sur ``professionnel.rasterisation.ECHELLE_DEFAUT``.
ECHELLE_PNG = 2.0

#: Vocabulaire fermé des graphiques (ré-exporté pour les schémas de tools).
TYPES_GRAPHIQUES = ("bar", "line", "pie", "donut")

#: Vocabulaire fermé des illustrations (ré-exporté pour les schémas de tools).
TYPES_ILLUSTRATIONS = (
    "icones",
    "etapes",
    "cycle",
    "pyramide",
    "frise",
    "comparaison",
)


# --- Specs -------------------------------------------------------------------


def spec_graphique(bloc: Graphique) -> dict[str, Any]:
    """Traduit un bloc ``graphique`` en spec attendue par ``render_chart``.

    La traduction est explicite (et non un ``model_dump``) : le bloc parle
    français (``series: [{nom, valeurs}]``), le moteur attend un dictionnaire
    ``{nom: valeurs}``. Une seule correspondance, donc un seul comportement.
    """
    return {
        "type": bloc.variante,
        "title": bloc.titre or "",
        "labels": list(bloc.labels),
        "series": {serie.nom: list(serie.valeurs) for serie in bloc.series or []},
        "values": list(bloc.valeurs or []),
        "y_label": bloc.y_label or "",
        "width": bloc.largeur_px,
        "height": bloc.hauteur_px,
        "total_reference": bloc.total_reference,
        "afficher_valeurs": bloc.afficher_valeurs,
    }


def spec_illustration(bloc: Illustration) -> dict[str, Any]:
    """Traduit un bloc ``illustration`` en spec attendue par ``render_illustration``."""
    return {
        "type": bloc.variante,
        "titre": bloc.titre or "Illustration",
        "items": [
            {
                cle: valeur
                for cle, valeur in (
                    ("titre", item.titre),
                    ("texte", item.texte),
                    ("icone", item.icone),
                )
                if valeur
            }
            for item in bloc.items
        ],
    }


# --- SVG ---------------------------------------------------------------------


def svg_graphique(spec: Mapping[str, Any]) -> str:
    """SVG d'un graphique de données (le serveur dessine, l'agent fournit les chiffres)."""
    from app.documents.professionnel.graphiques import render_chart

    return render_chart(spec)


def svg_illustration(spec: Mapping[str, Any], theme: str | Any | None = None) -> str:
    """SVG d'une illustration de composition (géométrie et couleurs serveur)."""
    from app.documents.professionnel.illustrations import render_illustration

    return render_illustration(spec, theme)


def svg_depuis_bloc(bloc: Graphique | Illustration | Image) -> str:
    """SVG d'un bloc de figure (graphique, illustration ou SVG écrit par l'agent).

    Un ``Image`` porteur de ``svg`` est **assaini** ici : c'est le seul endroit
    où un balisage fourni par le modèle entre dans la chaîne de rendu, donc le
    seul endroit où le nettoyage doit être certain.
    """
    if isinstance(bloc, Graphique):
        return svg_graphique(spec_graphique(bloc))
    if isinstance(bloc, Illustration):
        return svg_illustration(spec_illustration(bloc))
    if isinstance(bloc, Image) and bloc.svg:
        from app.documents.professionnel.html_builder import assainir_svg

        return assainir_svg(bloc.svg)
    raise ValidationError(
        "Bloc sans figure SVG",
        details={"type": getattr(bloc, "type", type(bloc).__name__)},
    )


# --- Rasterisation -----------------------------------------------------------


#: 1 mm en pixels CSS (96 dpi) : les formes se mesurent en mm, resvg en px.
_PX_PAR_MM = 96.0 / 25.4


def _luminance(couleur: str) -> float:
    """Luminance perçue d'une couleur ``#RRGGBB`` (choix du texte contrasté)."""
    rouge, vert, bleu = (int(couleur[index : index + 2], 16) for index in (1, 3, 5))
    return (0.299 * rouge + 0.587 * vert + 0.114 * bleu) / 255.0


def _texte_sur(fond: str) -> str:
    """Couleur de texte lisible sur un fond donné (jamais un texte invisible)."""
    return "#FFFFFF" if _luminance(fond) < 0.6 else "#1F2937"


def svg_forme(bloc: Forme, *, largeur_mm: float | None = None) -> str:
    """SVG d'une forme de mise en page (bandeau, filet, étiquette).

    La géométrie est calculée ici : l'agent donne une intention et un texte, pas
    des coordonnées. Le texte est échappé, la couleur de texte est choisie pour
    rester lisible sur le fond — un bandeau bleu foncé porte du blanc.
    """
    from xml.sax.saxutils import escape

    largeur = largeur_mm or bloc.largeur_mm or 160.0
    largeur_px = max(24, round(largeur * _PX_PAR_MM))
    hauteur_mm = bloc.hauteur_mm or (10.0 if bloc.variante == "etiquette" else 16.0)
    hauteur_px = max(6, round(hauteur_mm * _PX_PAR_MM))
    fond = bloc.fond or bloc.couleur
    interieur: list[str] = []
    if bloc.variante == "separateur":
        interieur.append(
            f'<rect x="0" y="0" width="{largeur_px}" height="{hauteur_px}" '
            f'fill="{fond}"/>'
        )
    elif bloc.variante == "etiquette":
        interieur.append(
            f'<rect x="0.75" y="0.75" width="{largeur_px - 1.5:.1f}" '
            f'height="{hauteur_px - 1.5:.1f}" rx="{hauteur_px / 2:.1f}" '
            f'fill="{fond}"/>'
        )
    else:
        interieur.append(
            f'<rect x="0" y="0" width="{largeur_px}" height="{hauteur_px}" '
            f'fill="{fond}"/>'
        )
    if bloc.variante != "separateur" and (bloc.texte or "").strip():
        taille = max(8, round(hauteur_px * 0.40))
        interieur.append(
            f'<text x="{largeur_px / 2:.1f}" '
            f'y="{hauteur_px / 2 + taille * 0.35:.1f}" text-anchor="middle" '
            'font-family="Calibri, Arial, sans-serif" font-weight="bold" '
            f'font-size="{taille}" fill="{_texte_sur(fond)}">'
            f"{escape(bloc.texte or '')}</text>"
        )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{largeur_px}" '
        f'height="{hauteur_px}" viewBox="0 0 {largeur_px} {hauteur_px}">'
        f"{''.join(interieur)}</svg>"
    )


def png_depuis_svg(
    svg: str, *, echelle: float = ECHELLE_PNG
) -> tuple[bytes, int, int]:
    """Convertit un SVG en PNG : ``(octets, largeur_px, hauteur_px)``.

    Raises:
        ValidationError: SVG vide ou sans balise ``<svg>``.
        GenerationError: ``resvg-py`` absent (avec la consigne d'installation) ou
            SVG que le moteur refuse (avec sa cause) — jamais un PNG vide.
    """
    from app.documents.professionnel.rasterisation import (
        RasterisationIndisponible,
        rasterisation_disponible,
        rasteriser_svg,
        taille_svg,
    )

    if not svg or "<svg" not in svg:
        raise ValidationError("Figure sans SVG : rien à rasteriser.")

    largeur, hauteur = taille_svg(svg)
    if not rasterisation_disponible():
        raise GenerationError(
            "Figure non rasterisable : le paquet 'resvg-py' n'est pas installé. "
            "Installe l'extra documents_pro (pip install -e '.[documents_pro]') "
            "— sans lui, Word, le PDF du spec et Excel ne peuvent pas recevoir un SVG.",
            details={"largeur_px": round(largeur), "hauteur_px": round(hauteur)},
        )
    try:
        octets = rasteriser_svg(svg, echelle=echelle)
    except RasterisationIndisponible as erreur:
        raise GenerationError(
            f"Figure refusée par le moteur de rasterisation : {erreur}",
            details={"largeur_px": round(largeur), "hauteur_px": round(hauteur)},
        ) from erreur

    return octets, round(largeur * echelle), round(hauteur * echelle)
