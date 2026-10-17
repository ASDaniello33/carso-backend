"""Point d'entrée CARSO du rendu professionnel—octets en mémoire, jamais un chemin.

Reprend ``outil.py`` du module d'origine avec trois différences assumées :

1. **Le rendu se fait dans un dossier temporaire interne**, effacé à la sortie :
   l'appelant reçoit des octets, le dépôt documentaire reste la seule écriture
   (``DocumentService``, statut ``proposed``) ;
2. **Un échec lève une erreur explicite** (``GenerationError`` / ``ValidationError``)
   au lieu d'un dictionnaire ``succes: false``—un agent ne peut pas présenter
   silencieusement un document qui n'a pas été produit ;
3. **Les avertissements qualité du moteur sont conservés** (``verification``)—ils
   apprennent à l'agent à corriger son HTML avant de livrer.

Pour un DOCX, le PDF jumeau est rendu **si un navigateur est disponible**—c'est
lui qui rend le livrable prévisualisable (``render_document`` ne rend que des PDF).
Sans navigateur, le DOCX est tout de même produit et l'absence d'aperçu est
signalée dans ``avertissements``—jamais un échec silencieux ni un fichier vide.
"""

from __future__ import annotations

import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.core.errors import GenerationError, ValidationError

from .docx_renderer import rendre_docx
from .graphiques import render_chart
from .html_builder import (
    DocumentPrepare,
    compter_svg,
    convertir_svg_en_png,
    injecter_graphiques,
    preparer,
)
from .pdf_renderer import (
    NavigateurIndisponible,
    _sans_chemin,
    navigateur_disponible,
    rendre_pdf,
)
from .presentation import lire_presentation, verifier_presentation
from .rasterisation import rasterisation_disponible, rasteriser_svg
from .themes import THEMES
from .verification import verifier

__all__ = ["FORMATS_PROFESSIONNEL", "ORIENTATIONS", "RenduProfessionnel", "rendre_professionnel"]

#: Formats de sortie du moteur (vocabulaire fermé : ni xlsx, ni html).
#: ``pptx`` est le deck natif 16:9 (``presentation`` + ``pptx_renderer``).
FORMATS_PROFESSIONNEL = ("pdf", "docx", "pptx")

#: Un deck ne se met pas en page A4 : l'orientation est ignorée (jamais refusée).
FORMATS_SANS_ORIENTATION = ("pptx",)

#: Orientations A4 acceptées.
ORIENTATIONS = ("portrait", "paysage")

#: Nom interne des fichiers rendus — le nom logique du document est décidé par
#: l'appelant (le tool), ce dossier est éphémère.
_NOM_INTERNE = "document"


@dataclass(frozen=True, slots=True)
class RenduProfessionnel:
    """Résultat d'un rendu : les octets, la pagination et les avertissements.

    Attributes:
        octets: octets du format demandé (``pdf`` ou ``docx``).
        format: format effectivement produit.
        pages: nombre de pages (exact pour un PDF ; ``None`` pour un DOCX sans
            navigateur, qui n'a pas de pages physiques fiables).
        avertissements: constats qualité du moteur, à lire et à corriger par
            l'agent avant de présenter le document.
        jumeau_pdf: pour un DOCX, le PDF du même contenu (aperçu), ou ``None``
            si aucun navigateur n'est disponible.
        theme: thème appliqué (après repli éventuel sur ``corporate``).
    """

    octets: bytes
    format: str
    pages: int | None
    avertissements: tuple[str, ...]
    jumeau_pdf: bytes | None = None
    theme: str = "corporate"

    @property
    def conseil(self) -> str:
        """Consigne de relecture, alignée sur les avertissements réellement remontés."""
        if self.avertissements:
            return "Corrige les avertissements puis regenere le document avant de le presenter."
        return "Rendu sans avertissement : verifie tout de meme l'apercu des pages."


def _rendre_presentation(
    contenu_html: str,
    titre: str,
    *,
    theme: str,
    auteur: str,
    base_dir: str | Path | None,
    graphiques: Mapping[str, Mapping[str, Any]] | None,
) -> RenduProfessionnel:
    """Corps HTML de diapositives → PPTX natif, plus les constats qualité.

    Le HTML suit le même contrat que ``generate_document_professionnel`` :
    ``<section class="cover">`` puis une ``<section class="slide">`` par
    diapositive. Les ancres ``<figure data-graphique>`` sont remplies par le
    moteur ; les SVG (graphiques comme schémas) deviennent des **PNG** —
    PowerPoint ne lit pas le SVG.

    Raises:
        ValidationError: contenu vide, thème inconnu, aucune diapositive, SVG
            présent sans ``resvg-py`` (aucun repli muet), diapositive trop
            chargée ou image introuvable.
    """
    from .pptx_renderer import rendre_pptx

    if not contenu_html or not contenu_html.strip():
        raise ValidationError("contenu_html est vide : rien à rendre.")
    if theme not in THEMES:
        raise ValidationError(
            f"Thème inconnu : {theme!r}",
            details={"themes_disponibles": list(THEMES)},
        )
    corps = contenu_html
    if graphiques:
        corps = injecter_graphiques(corps, graphiques, produire_svg=render_chart)
    # ``preparer`` applique le nettoyage de sécurité (scripts, on*, SVG) ; il
    # sort la couverture à part, on la remet en tête : une diapositive de titre
    # ouvre le deck.
    document = preparer(corps)
    corps = (document.couverture or "") + document.corps

    avertissements: list[str] = []
    if compter_svg(corps):
        if not rasterisation_disponible():
            raise ValidationError(
                "Un PPTX ne peut pas contenir de SVG (PowerPoint ne le lit pas). "
                "Installe 'resvg-py' pour convertir les figures en images, ou "
                "choisis le format 'pdf'."
            )
        corps = convertir_svg_en_png(corps, rasteriser_svg)

    diapositives = lire_presentation(corps)
    avertissements.extend(verifier_presentation(diapositives))

    with tempfile.TemporaryDirectory(prefix="carso_gendoc_") as dossier:
        chemin = rendre_pptx(
            diapositives,
            Path(dossier) / f"{_NOM_INTERNE}.pptx",
            theme=theme,
            titre=titre,
            auteur=auteur,
            base_dir=Path(base_dir) if base_dir else None,
        )
        octets = Path(chemin).read_bytes()

    return RenduProfessionnel(
        octets=octets,
        format="pptx",
        pages=len(diapositives),
        avertissements=tuple(avertissements),
        theme=theme,
    )


def _preparer_document(
    contenu_html: str,
    format: str,
    orientation: str,
    theme: str,
    graphiques: Mapping[str, Mapping[str, Any]] | None = None,
) -> tuple[DocumentPrepare, tuple[str, ...]]:
    """Vérifie les paramètres (vocabulaire fermé), insère les graphiques, nettoie,
    puis adapte les SVG au DOCX.

    L'ordre compte : le **nettoyage précède la rasterisation** — l'assainissement
    du SVG (``html_builder._assainir_svg``) doit passer avant que resvg ne voie le
    document, exactement comme avant que Chromium ne le rende.

    Retourne le document préparé et les avertissements nés de l'adaptation (des
    SVG retirés d'un DOCX faute de resvg-py, par exemple).
    """
    if not contenu_html or not contenu_html.strip():
        raise ValidationError("contenu_html est vide : rien à rendre.")
    if format not in FORMATS_PROFESSIONNEL:
        raise ValidationError(
            f"Format inconnu : {format!r}",
            details={"formats_disponibles": list(FORMATS_PROFESSIONNEL)},
        )
    if format not in FORMATS_SANS_ORIENTATION and orientation not in ORIENTATIONS:
        raise ValidationError(
            f"Orientation inconnue : {orientation!r}",
            details={"orientations_disponibles": list(ORIENTATIONS)},
        )
    if theme not in THEMES:
        raise ValidationError(
            f"Thème inconnu : {theme!r}",
            details={"themes_disponibles": list(THEMES)},
        )
    avertissements: list[str] = []
    corps = contenu_html
    if graphiques:
        corps = injecter_graphiques(corps, graphiques, produire_svg=render_chart)
    document = preparer(corps)
    if format == "docx":
        # Word ne lit pas un SVG : chaque SVG du corps (graphique du moteur comme
        # schéma de l'agent) devient un PNG. La conversion passe par resvg-py
        # (1,2 Mo, sans navigateur) — PAS par Chromium, qui ne sert qu'au PDF.
        # Sans ce paquet, les SVG sont RETIRÉS et l'omission est ANNONCÉE : les
        # laisser dans le corps répandrait le texte de leurs <text> en paragraphes.
        rasteriser = rasteriser_svg if rasterisation_disponible() else None
        if rasteriser is None:
            omis = compter_svg(document.corps) + compter_svg(document.couverture or "")
            if omis:
                avertissements.append(
                    f"{omis} SVG non intégré(s) au DOCX (rasterisation "
                    "indisponible) : le paquet 'resvg-py' n'est pas installé — "
                    "le PDF n'a pas cette limite."
                )
        document.corps = convertir_svg_en_png(document.corps, rasteriser)
        if document.couverture:
            document.couverture = convertir_svg_en_png(document.couverture, rasteriser)
    return document, tuple(avertissements)


def rendre_professionnel(
    contenu_html: str,
    titre: str,
    *,
    format: str = "pdf",
    auteur: str = "",
    theme: str = "corporate",
    orientation: str = "portrait",
    css_supplementaire: str | None = None,
    base_dir: str | Path | None = None,
    graphiques: Mapping[str, Mapping[str, Any]] | None = None,
) -> RenduProfessionnel:
    """Rend le document demandé et retourne ses octets (aucune écriture persistante).

    Args:
        contenu_html: **corps** du document (les ``<style>``/``<script>`` sont
            retirés : le thème est injecté par le moteur). Chaque emplacement de
            graphique s'écrit ``<figure data-graphique="id"></figure>``.
        titre: titre du document (en-tête de page, métadonnées).
        format: ``pdf`` (défaut), ``docx`` ou ``pptx`` (diapositives 16:9).
        auteur: auteur inscrit dans les métadonnées.
        theme: ``corporate``, ``moderne`` ou ``nature``.
        orientation: ``portrait`` (défaut) ou ``paysage``.
        css_supplementaire: micro-ajustements CSS — **PDF uniquement** (sans effet
            sur le DOCX, rendu natif).
        base_dir: dossier de base pour résoudre les images relatives du HTML.
        graphiques: ``{id: spec}`` — chaque spec alimente ``render_chart`` et
            remplace l'ancre de même ``id`` (SVG inline en PDF, PNG en DOCX).

    Returns:
        ``RenduProfessionnel`` — octets, pagination, avertissements, PDF jumeau.

    Raises:
        ValidationError: contenu vide, format/orientation/thème hors vocabulaire,
            ancre de graphique inconnue ou graphique déclaré mais non ancré.
        GenerationError: le rendu a échoué (navigateur absent, HTML sans contenu
            rendable) — le message porte la cause réelle, jamais un succès vide.
    """
    format = (format or "pdf").strip().lower().lstrip(".")
    orientation = (orientation or "portrait").strip().lower()
    theme = (theme or "corporate").strip().lower()
    titre = (titre or "").strip() or "Document"

    if format == "pptx":
        return _rendre_presentation(
            contenu_html,
            titre,
            theme=theme,
            auteur=auteur,
            base_dir=base_dir,
            graphiques=graphiques,
        )

    doc, avertissements_insertion = _preparer_document(
        contenu_html, format, orientation, theme, graphiques
    )
    avertissements = [
        *avertissements_insertion,
        # Le contrôle qualité porte sur le HTML ORIGINAL (et non le corps nettoyé) :
        # preparer retire <style> et consomme les ancres de graphique, donc les
        # constats (« style ignoré », ancre vide/doublée) n'existeraient plus.
        *verifier(contenu_html, format, orientation),
    ]

    options: dict[str, Any] = dict(
        theme=theme,
        titre=titre,
        auteur=auteur,
        orientation=orientation,
        base_dir=Path(base_dir) if base_dir else None,
    )

    with tempfile.TemporaryDirectory(prefix="carso_gendoc_") as dossier:
        racine = Path(dossier)
        octets: bytes
        pdf_jumeau: bytes | None = None
        pages: int | None = None

        try:
            if format == "docx":
                chemin_docx = rendre_docx(doc, racine / f"{_NOM_INTERNE}.docx", **options)
                octets = Path(chemin_docx).read_bytes()
            else:
                chemin_pdf = racine / f"{_NOM_INTERNE}.pdf"
                pages = rendre_pdf(
                    doc,
                    chemin_pdf,
                    css_supplementaire=css_supplementaire or "",
                    **options,
                )
                octets = chemin_pdf.read_bytes()
        except NavigateurIndisponible as erreur:
            raise GenerationError(
                "Rendu PDF impossible : aucun navigateur Chromium utilisable. "
                f"{erreur}"
            ) from erreur
        except (ValidationError, GenerationError):
            raise
        except Exception as erreur:  # noqa: BLE001 - la cause exacte est transmise
            raise GenerationError(
                "Rendu du document impossible "
                f"({type(erreur).__name__}) : {_sans_chemin(str(erreur))}"
            ) from erreur

        if format == "docx":
            # Le PDF jumeau rend le DOCX prévisualisable (render_document ne rend
            # que des PDF). Son absence est annoncée, jamais silencieuse.
            disponible, raison = navigateur_disponible()
            if disponible:
                try:
                    chemin_pdf = racine / f"{_NOM_INTERNE}_apercu.pdf"
                    pages = rendre_pdf(
                        doc,
                        chemin_pdf,
                        css_supplementaire=css_supplementaire or "",
                        **options,
                    )
                    pdf_jumeau = chemin_pdf.read_bytes()
                except Exception as erreur:  # noqa: BLE001 - dégradation annoncée
                    avertissements.append(
                        "PDF jumeau indisponible (le DOCX reste livrable) : "
                        f"{type(erreur).__name__}: {_sans_chemin(str(erreur))}"
                    )
            else:
                avertissements.append(
                    "PDF jumeau indisponible (le DOCX reste livrable, sans aperçu "
                    f"des pages) : {raison}"
                )

    return RenduProfessionnel(
        octets=octets,
        format=format,
        pages=pages,
        avertissements=tuple(avertissements),
        jumeau_pdf=pdf_jumeau,
        theme=theme,
    )
