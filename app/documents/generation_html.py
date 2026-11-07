"""Génération de livrables depuis du **HTML** (tool ``generer_document_html``).

Pourquoi ce chemin—le HTML est un type **standard** que le modèle produit
avec aisance et qui offre des styles très variés (couleurs, encadrés, images,
tableaux stylés) sans les contraintes de structure du ``DocumentSpec``. La
chaîne ``DocumentSpec``/``profiles`` reste disponible et inchangée pour les
livrables à structure imposée—ce module est le chemin **prioritaire** pour
un document personnalisé, pas un remplacement de la chaîne structurée.

Deux sorties (vocabulaire fermé ``FORMATS_SORTIE_HTML``) :

- **docx**—moteur ``app.documents.html_to_docx``—conversion fidèle du
  HTML+CSS (couleurs, polices, encadrés, images, listes, tableaux, liens) ;
- **pdf**—le système ne dépend d'aucun convertisseur bureautique
  (LibreOffice, pandoc)—limite documentée du projet. Le PDF est donc rendu
  par le renderer CARSO depuis un **DocumentSpec minimal** extrait du HTML
  (titres, paragraphes, listes, tableaux, **figures**)—même contenu, mise en
  page réaliste du moteur PDF plutôt qu'une copie pixel du DOCX.

**Figures SVG (INC-123).** Avant cet incrément, un ``<svg>`` fourni par
l'agent était **supprimé** par la conversion DOCX (balise ignorée) et ignoré par
le PDF : un schéma demandé disparaissait sans un mot. Désormais :

- les ``<svg>`` sont **conservés** — rasterisés en PNG pour le DOCX (Word ne
  lit pas le SVG) et insérés comme blocs image dans le PDF ;
- ``graphiques`` et ``illustrations`` remplissent les ancres
  ``<figure data-graphique="id">`` / ``<figure data-illustration="id">`` avec
  les dessins du serveur, exactement comme l'outil professionnel ;
- l'assainissement et la sérialisation sont **ceux du moteur professionnel**
  (``assainir_svg_tag``, ``svg_pour_rasterisation``) : une seule règle de
  sécurité, un seul rendu.

Le circuit documentaire CARSO est **inchangé** (règle 6 [C])—la sortie est
déposée via ``DocumentService.enregistrer_document`` avec
``proposed_by_agent`` → statut ``proposed`` (validation humaine requise) ;
une ancre métier réelle classe le fichier sous l'objet, une ancre absente le
range sous ``generated/{id}``. Aucune écriture disque directe—tout passe
par le service (AGENTS.md §2.5).
"""

from __future__ import annotations

import base64
from collections.abc import Mapping
from typing import Any

from bs4 import Tag

from app.core.errors import ValidationError
from app.documents.assets import ImagesMemoire
from app.documents.spec import Image

__all__ = [
    "convertir_html_bytes",
    "embarquer_svg_pour_docx",
    "FORMATS_SORTIE_HTML",
    "integrer_figures",
]


#: Formats de sortie supportés par la conversion HTML (vocabulaire fermé).
FORMATS_SORTIE_HTML = ("docx", "pdf")


def convertir_html_bytes(
    html: str | bytes,
    *,
    format: str = "docx",
    css: str | None = None,
    graphiques: Mapping[str, Mapping[str, Any]] | None = None,
    illustrations: Mapping[str, Mapping[str, Any]] | None = None,
) -> bytes:
    """Convertit du HTML en octets du format demandé.

    Args:
        html: page HTML complète (ou fragment) avec ses ``<style>``. Une
            chaîne encodée autrement qu'en UTF-8 est décodée permissivement.
        format: ``docx`` (moteur HTML→DOCX natif) ou ``pdf`` (DocumentSpec
            minimal extrait du HTML, rendu par le moteur PDF CARSO).
        css: CSS additionnel appliqué après celui du HTML (facultatif).
        graphiques: ``{id: spec}`` — chaque spec remplace l'ancre
            ``<figure data-graphique="id">`` de même identifiant ; le SVG est
            dessiné par le serveur (``figures.svg_graphique``).
        illustrations: ``{id: spec}`` — même contrat pour
            ``<figure data-illustration="id">``.

    Returns:
        Les octets du document produit.

    Raises:
        ValidationError: format hors vocabulaire, ancre de figure inconnue ou
            figure déclarée sans ancre.
        GenerationError: SVG impossible à rasteriser (resvg absent, SVG refusé).

    Raises:
        ValidationError: format hors vocabulaire fermé.
        ConversionHtmlError: HTML vide ou sans contenu rendable.
    """
    cle = str(format).strip().lower().lstrip(".")
    if cle not in FORMATS_SORTIE_HTML:
        from app.core.errors import ValidationError

        raise ValidationError(
            f"Format de sortie inconnu : {format!r}",
            details={"formats_disponibles": list(FORMATS_SORTIE_HTML)},
        )

    from app.documents.html_to_docx import html_string_to_docx_bytes

    html_texte = (
        html.decode("utf-8", errors="replace") if isinstance(html, bytes) else html
    )
    html_texte = _injecter_figures(html_texte, graphiques, illustrations)
    # Word ne lit pas le SVG : les figures deviennent des PNG embarqués AVANT la
    # conversion (et non « ignorées », ce qui faisait disparaître un schéma).
    html_texte = _svg_vers_png_data_uri(html_texte)
    if cle == "docx":
        return html_string_to_docx_bytes(html_texte, css_string=css)
    return _html_vers_pdf(html_texte)


def _injecter_figures(
    html_texte: str,
    graphiques: Mapping[str, Mapping[str, Any]] | None,
    illustrations: Mapping[str, Mapping[str, Any]] | None,
) -> str:
    """Remplit les ancres de figures avec les SVG du serveur (INC-123).

    Même vocabulaire que l'outil professionnel : l'agent place
    ``<figure data-graphique="id">`` et déclare les données dans ``graphiques``.
    """
    if graphiques:
        from app.documents.figures import svg_graphique
        from app.documents.professionnel.html_builder import injecter_graphiques

        html_texte = injecter_graphiques(
            html_texte, graphiques, produire_svg=svg_graphique
        )
    if illustrations:
        from app.documents.figures import svg_illustration
        from app.documents.professionnel.html_builder import injecter_illustrations

        html_texte = injecter_illustrations(
            html_texte, illustrations, produire_svg=svg_illustration
        )
    return html_texte


def integrer_figures(
    html: str,
    graphiques: Mapping[str, Mapping[str, Any]] | None = None,
    illustrations: Mapping[str, Mapping[str, Any]] | None = None,
) -> str:
    """Remplit les ancres ``data-graphique`` / ``data-illustration`` du HTML.

    Point d'entrée public de l'étape partagée par la conversion HTML et le
    document libre (INC-137) : le serveur dessine, l'agent ne fournit que les
    chiffres.
    """
    return _injecter_figures(html, graphiques, illustrations)


def embarquer_svg_pour_docx(html_texte: str) -> str:
    """Remplace chaque ``<svg>`` du HTML par un PNG en data URI.

    Word ne lit pas le SVG : sans cette étape un schéma serait **perdu** dans le
    DOCX. L'étape est publique pour être partagée par ``convertir_html_bytes``
    et par le document libre.
    """
    return _svg_vers_png_data_uri(html_texte)


def _svg_vers_png_data_uri(html_texte: str) -> str:
    """Remplace chaque ``<svg>`` par son PNG en ``data:`` (Word ne lit pas le SVG).

    Le nettoyage et la sérialisation XML sont ceux du moteur professionnel
    (``assainir_svg_tag`` / ``svg_pour_rasterisation``) : un ``viewbox``
    minuscule ferait ignorer le ``viewBox`` par resvg, et un SVG non assaini
    ferait entrer du balisage non fiable dans le livrable.

    Raises:
        GenerationError: rasterisation impossible — le document n'est **pas**
            produit sans sa figure (jamais de schéma disparu en silence).
    """
    from bs4 import BeautifulSoup

    from app.documents.figures import png_depuis_svg
    from app.documents.professionnel.html_builder import (
        assainir_svg_tag,
        svg_pour_rasterisation,
        taille_logique_svg,
    )

    soup = BeautifulSoup(html_texte or "", "html.parser")
    traces = soup.find_all("svg")
    if not traces:
        return html_texte
    for trace in traces:
        assainir_svg_tag(trace)
        xml = svg_pour_rasterisation(trace)
        octets, _largeur_px, _hauteur_px = png_depuis_svg(xml)
        largeur, hauteur = taille_logique_svg(trace, xml)
        image = soup.new_tag(
            "img",
            src="data:image/png;base64," + base64.b64encode(octets).decode("ascii"),
        )
        # La taille d'affichage déclarée par l'agent est conservée (px HTML).
        image["width"] = str(round(largeur))
        image["height"] = str(round(hauteur))
        trace.replace_with(image)
    return str(soup)


# --- PDF : DocumentSpec minimal extrait du HTML -----------------------------


def _html_vers_pdf(html_texte: str) -> bytes:
    """Extrait une structure minimale du HTML puis rend un PDF CARSO.

    La fidélité pixel n'est pas l'objectif (pas de convertisseur bureautique
    dans ce projet, limite documentée) : le contenu — titres, paragraphes,
    listes, tableaux, **figures** — est transmis au renderer PDF du système,
    qui applique la mise en page professionnelle standard.

    Les figures arrivent ici déjà converties en ``<img src="data:…">``
    (``_svg_vers_png_data_uri``) : chaque image embarquée devient un bloc
    ``Image`` du spec, résolu par ``ImagesMemoire``. Une image dont la source
    n'est pas embarquée (URL distante) n'est **pas** récupérée par le serveur
    (SSRF) : sa place est **annoncée** par un paragraphe, jamais vide.
    """
    from bs4 import BeautifulSoup

    from app.documents.spec import (
        DocumentSpec,
        Liste,
        Metadonnees,
        Paragraphe,
        Tableau,
        Titre,
    )

    soup = BeautifulSoup(html_texte, "html.parser")
    body = soup.find("body") or soup
    titre = body.find(["h1"])
    titre_texte = titre.get_text(strip=True) if titre else None

    images = ImagesMemoire()
    blocs: list[Titre | Paragraphe | Liste | Tableau | Image] = []
    if titre is not None:
        blocs.append(Titre(niveau=1, texte=titre_texte or ""))

    puces: list[str] = []  # les <li> consécutifs forment une liste

    def _vider_puces() -> None:
        if puces:
            blocs.append(Liste(items=list(puces), numerotee=False))
            puces.clear()

    for element in body.find_all(["h2", "h3", "h4", "p", "li", "table", "img"]):
        # Ne pas descendre deux fois dans le contenu d'un tableau.
        if element.find_parent("table") is not None and element.name != "table":
            continue
        if element.name == "img":
            _vider_puces()
            figure = _figure_html(element, images, len(blocs))
            if figure is not None:
                blocs.append(figure)
            else:
                # Une source distante n'est pas récupérée par le serveur (SSRF) :
                # la place est **annoncée** — jamais une figure disparue en
                # silence (la conversion DOCX, elle, sait charger les URL).
                blocs.append(
                    Paragraphe(
                        texte=(
                            "[Image non reportée dans le PDF : source non "
                            "embarquée — utiliser une image data: ou un fichier "
                            "du stockage]"
                        )
                    )
                )
            continue
        if element.name in ("h2", "h3", "h4"):
            _vider_puces()
            blocs.append(
                Titre(
                    niveau=int(element.name[1]),
                    texte=element.get_text(strip=True),
                )
            )
        elif element.name == "p":
            _vider_puces()
            texte = element.get_text(" ", strip=True)
            if texte:
                blocs.append(Paragraphe(texte=texte))
        elif element.name == "li":
            texte = element.get_text(" ", strip=True)
            if texte:
                puces.append(texte)
        elif element.name == "table":
            _vider_puces()
            entete, lignes = _grille_tableau(element)
            if lignes:
                blocs.append(Tableau(entete=entete, lignes=lignes))

    _vider_puces()
    if not blocs:
        from app.documents.html_to_docx import ConversionHtmlError

        raise ConversionHtmlError(
            "Le HTML fourni ne contient aucun contenu convertible en PDF."
        )

    spec = DocumentSpec(
        metadata=Metadonnees(titre=titre_texte or "Document"),
        blocs=blocs,
    )
    from app.documents.renderers.pdf import rendre_pdf

    return rendre_pdf(spec, images=images)


def _figure_html(element: Tag, images: ImagesMemoire, position: int) -> Image | None:
    """Bloc ``Image`` d'un ``<img>`` HTML (data-URI seulement), légende comprise.

    Returns:
        Le bloc image, ou ``None`` si la source n'est pas embarquée : l'appelant
        décide alors d'annoncer la figure plutôt que de la perdre en silence.
    """
    from app.documents.spec import Image

    source = str(element.get("src") or "")
    if not source.startswith("data:image/"):
        return None
    _entete, _, donnees = source.partition(",")
    try:
        octets = base64.b64decode(donnees, validate=False)
    except (ValueError, TypeError) as erreur:
        raise ValidationError("Image embarquée illisible (base64 invalide)") from erreur
    if not octets:
        return None

    cle = f"figure-{position}.png"
    images.ajouter(cle, octets)
    figure = element.find_parent("figure")
    figcaption = figure.find("figcaption") if figure is not None else None
    legende = figcaption.get_text(" ", strip=True)[:300] if figcaption else None
    largeur_px = _entier(element.get("width"))
    # Convention 96 dpi du HTML ; plafonnée à la largeur utile d'une page A4.
    largeur_mm = min(largeur_px * 25.4 / 96, 165.0) if largeur_px else 120.0
    return Image(chemin=cle, legende=legende, largeur_mm=round(largeur_mm, 1))


def _entier(valeur: Any) -> int | None:
    """Entier positif d'un attribut HTML (``"640"``, ``"640px"``), sinon ``None``."""
    if valeur is None:
        return None
    brut = str(valeur).strip().lower().removesuffix("px").strip()
    try:
        nombre = int(float(brut))
    except ValueError:
        return None
    return nombre if nombre > 0 else None


def _grille_tableau(table_el: Tag) -> tuple[list[str] | None, list[list[str]]]:
    """Grille texte d'un ``<table>`` : (en-tête si ``<th>``, lignes de données).

    Les fusions HTML ``colspan`` sont **résolues en grille complète** : chaque
    ligne couvre exactement la largeur du tableau — condition du validateur
    ``DocumentSpec``. Sans cela, une ligne de total ``<td colspan="n">``
    produisait une grille incohérente et la génération PDF échouait
    (« colonne(s) non couverte(s) »). Les ``rowspan`` sont aplatis : la valeur
    reste sur sa ligne d'origine, les lignes couvertes portent une chaîne vide
    (rendu visuel identique après fusion gérée par le renderer).
    """
    # --- Passe 1 : lecture brute des lignes (texte + colspan) --------------
    brutes: list[tuple[list[bool], list[tuple[str, int]]]] = []
    for tr in table_el.find_all("tr"):
        ths: list[bool] = []
        cellules: list[tuple[str, int]] = []
        for cellule in tr.find_all(["th", "td"], recursive=False):
            try:
                colspan = max(1, int(cellule.get("colspan", 1)))
            except (TypeError, ValueError):
                colspan = 1
            ths.append(cellule.name == "th")
            cellules.append((cellule.get_text(" ", strip=True), colspan))
        if cellules:
            brutes.append((ths, cellules))
    if not brutes:
        return None, []

    largeur = max(sum(colspan for _texte, colspan in cellules) for _ths, cellules in brutes)

    # --- Passe 2 : expansion des colspan en grille pleine largeur ----------
    grille: list[list[str]] = []
    est_th: list[list[bool]] = []
    for ths, cellules in brutes:
        ligne: list[str] = []
        ths_ligne: list[bool] = []
        for (texte, colspan), th in zip(cellules, ths, strict=True):
            ligne.append(texte)
            ths_ligne.append(th)
            # Expansion : les colonnes fusionnées portent une chaîne vide
            # (continuité de grille ; le DOCX gère la vraie fusion, le PDF
            # affiche la cellule sur sa première colonne puis des vides).
            ligne.extend([""] * (colspan - 1))
            ths_ligne.extend([th] * (colspan - 1))
        # Normalise (HTML mal formé : ligne trop courte ou trop longue).
        del ligne[largeur:]
        del ths_ligne[largeur:]
        ligne.extend([""] * (largeur - len(ligne)))
        ths_ligne.extend([False] * (largeur - len(ths_ligne)))
        grille.append(ligne)
        est_th.append(ths_ligne)

    # --- Passe 3 : en-tête (toutes les cellules de la 1re ligne en th) -----
    entete: list[str] | None = None
    lignes: list[list[str]] = []
    if all(est_th[0]):
        entete = grille[0]
        lignes = grille[1:]
    else:
        lignes = grille

    # Nettoie : supprime les lignes entièrement vides (résidus de fusions).
    lignes = [ligne for ligne in lignes if any(texte.strip() for texte in ligne)]
    return entete, lignes
