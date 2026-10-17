"""Nettoyage du HTML de l'agent et construction de la page HTML complète (thème injecté).

Trois responsabilités, dans cet ordre : **nettoyer** ce que l'agent écrit
(`preparer` — HTML et SVG), **insérer** les graphiques déclarés
(`injecter_graphiques`) et **adapter les SVG au DOCX**
(`convertir_svg_en_png`), Word ne sachant pas lire un SVG.
"""

from __future__ import annotations

import base64
import html
import re
import unicodedata
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup, Comment, Tag

from app.core.errors import ValidationError

from .rasterisation import dimension_px, taille_svg
from .theme_css import THEME_CSS
from .themes import Theme, get_theme

BALISES_INTERDITES = [
    "script",
    "style",
    "link",
    "meta",
    "iframe",
    "object",
    "embed",
    "head",
    "title",
    "form",
    "noscript",
]

#: Balises interdites **dans un SVG** (en plus des précédentes, qui s'appliquent
#: déjà à tout l'arbre). L'agent a le droit d'écrire un schéma (organigramme,
#: flèches, diagramme) : ce qui reste interdit est ce qui n'est pas un dessin.
#:
#: Noms en **minuscules obligatoirement** : ``html.parser`` met les noms de
#: balises en minuscules, et ``find_all("foreignObject")`` ne matche alors
#: **rien** (mesuré : l'élément survivait au nettoyage).
BALISES_SVG_INTERDITES = [
    # du HTML ré-embarqué : tout le nettoyage précédent serait à refaire, et
    # c'est le contournement classique d'un assainisseur SVG.
    "foreignobject",
    # l'animation n'a aucun sens dans un livrable imprimé.
    "animate",
    "animatemotion",
    "animatetransform",
    "set",
]

#: Références admises dans un SVG : une ancre interne (`#id`, marqueurs,
#: dégradés) ou une image raster embarquée. Toute URL (http, file, data:text…)
#: est retirée : Chromium irait la chercher **côté serveur** au moment du rendu.
_RE_REF_SVG = re.compile(r"^(?:#|data:image/(?:png|jpe?g|gif|webp);base64,)", re.I)

#: Serveur de peinture **distant** dans un attribut (``fill="url(http://…)"``,
#: ``marker-end``, ``clip-path``, ``mask``, ``filter``…). Le filtre sur ``href``
#: ne suffit pas : le SVG accepte une URL dans la **valeur** d'autres attributs,
#: et Chromium irait la chercher au rendu. Seule la référence interne ``url(#…)``
#: est admise (dégradés, marqueurs, motifs).
_RE_PAINT_EXTERNE = re.compile(r"url\(\s*['\"]?\s*(?!#)", re.I)

#: Noms d'éléments SVG que `html.parser` met en minuscules alors que le SVG est
#: du **XML** (casse significative). Table volontairement courte : seuls les
#: noms dont la perte change le rendu. Un nom absent garde son défaut SVG.
_ELEMENTS_XML = {
    "clippath": "clipPath",
    "lineargradient": "linearGradient",
    "radialgradient": "radialGradient",
    "textpath": "textPath",
}

#: Attributs SVG dont la casse perdue **change le rendu**. `viewBox` d'abord :
#: resvg (XML strict) ignore `viewbox`, et un SVG qui ne porte qu'un viewBox
#: était mesuré **distordu** (220 × 160 au lieu de 400 × 160).
_ATTRIBUTS_XML = {
    "viewbox": "viewBox",
    "preserveaspectratio": "preserveAspectRatio",
    "gradientunits": "gradientUnits",
    "gradienttransform": "gradientTransform",
    "patternunits": "patternUnits",
    "patterncontentunits": "patternContentUnits",
    "patterntransform": "patternTransform",
    "clippathunits": "clipPathUnits",
    "maskunits": "maskUnits",
    "maskcontentunits": "maskContentUnits",
    "markerwidth": "markerWidth",
    "markerheight": "markerHeight",
    "markerunits": "markerUnits",
    "refx": "refX",
    "refy": "refY",
    "textlength": "textLength",
    "lengthadjust": "lengthAdjust",
    "spreadmethod": "spreadMethod",
    "stddeviation": "stdDeviation",
    "startoffset": "startOffset",
    "pathlength": "pathLength",
    "zoomandpan": "zoomAndPan",
}


@dataclass
class EntreeSommaire:
    id: str
    titre: str
    niveau: int = 1


@dataclass
class DocumentPrepare:
    """Résultat du nettoyage : corps, couverture séparée et entrées du sommaire."""

    corps: str
    couverture: str | None = None
    sommaire: list[EntreeSommaire] = field(default_factory=list)


def slugifier(texte: str, separateur: str = "-") -> str:
    texte = unicodedata.normalize("NFKD", texte).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", separateur, texte.lower()).strip(separateur)


def _racine(soup: BeautifulSoup) -> Tag | BeautifulSoup:
    if soup.body is not None:
        return soup.body
    if soup.html is not None:
        return soup.html
    return soup


def _assainir_svg(svg: Tag) -> None:
    """Nettoie un SVG **écrit par l'agent** (schéma, illustration, organigramme).

    Un SVG du modèle est une donnée non fiable rendue par deux moteurs
    (Chromium pour le PDF, resvg pour le DOCX) : ce qui est retiré ici l'est
    pour les deux. Le nettoyage global a déjà agi (``<script>``, ``<style>``,
    ``on*``) ; restent les vecteurs propres au SVG :

    - ``BALISES_SVG_INTERDITES`` — HTML ré-embarqué et animation ;
    - les **références distantes** (``href``/``xlink:href`` hors ancre interne
      et hors raster embarqué) : Chromium irait les chercher côté serveur au
      moment du rendu (AGENTS.md §7 et §9) ;
    - les attributs d'événement (repassés ici pour que la règle SVG tienne
      seule, sans dépendre de l'ordre du nettoyage).
    """
    for balise in svg.find_all(BALISES_SVG_INTERDITES):
        balise.decompose()
    for balise in [svg, *svg.find_all(True)]:
        for attribut in list(balise.attrs):
            nom = attribut.lower()
            if nom.startswith("on"):
                del balise[attribut]
            elif nom in ("href", "xlink:href"):
                if not _RE_REF_SVG.match(str(balise[attribut]).strip()):
                    del balise[attribut]
            elif _RE_PAINT_EXTERNE.search(str(balise[attribut])):
                del balise[attribut]


def preparer(contenu: str) -> DocumentPrepare:
    """Nettoie le HTML, sépare la couverture et prépare le sommaire."""
    soup = BeautifulSoup(contenu or "", "html.parser")
    racine = _racine(soup)

    # 1. Nettoyage de sécurité
    for balise in racine.find_all(BALISES_INTERDITES):
        # Le <title> d'un SVG n'est pas celui du document : c'est le **nom
        # accessible** du dessin, et la source du texte alternatif du DOCX.
        # Le supprimer rendait un schéma muet (mesuré par test).
        if balise.name == "title" and balise.find_parent("svg") is not None:
            continue
        balise.decompose()
    for commentaire in racine.find_all(string=lambda s: isinstance(s, Comment)):
        commentaire.extract()
    for balise in racine.find_all(True):
        for attribut in list(balise.attrs):
            if attribut.lower().startswith("on"):
                del balise[attribut]
        if str(balise.get("href", "")).strip().lower().startswith("javascript:"):
            del balise["href"]
    for svg in racine.find_all("svg"):
        _assainir_svg(svg)

    # 2. Couverture extraite (rendue sur une page séparée)
    couverture = None
    section = racine.find("section", class_="cover")
    if section is not None:
        couverture = str(section.extract())

    # 3. Identifiants uniques sur les titres
    utilises = {str(t["id"]) for t in racine.find_all(id=True)}
    for titre in racine.find_all(["h1", "h2"]):
        if titre.find_parent("nav") is not None or titre.get("id"):
            continue
        base = slugifier(titre.get_text(" ", strip=True)) or "section"
        candidat, k = base, 2
        while candidat in utilises:
            candidat, k = f"{base}-{k}", k + 1
        titre["id"] = candidat
        utilises.add(candidat)

    # 4. Sommaire : liens réécrits (titre ....... page), auto-rempli s'il est vide
    entrees: list[EntreeSommaire] = []
    nav = racine.find("nav", class_="toc")
    if nav is not None:
        liens = nav.find_all("a", href=True)
        if not liens:
            for titre in racine.find_all(["h1", "h2"]):
                if titre.find_parent("nav") is None:
                    lien = soup.new_tag("a", href=f"#{titre['id']}")
                    lien.string = titre.get_text(" ", strip=True)
                    nav.append(lien)
            liens = nav.find_all("a", href=True)
        for lien in liens:
            cible_id = str(lien["href"]).lstrip("#")
            cible = racine.find(id=cible_id) if cible_id else None
            if cible is None:
                lien.decompose()
                continue
            texte = lien.get_text(" ", strip=True) or cible.get_text(" ", strip=True)
            niveau = 1 if cible.name == "h1" else 2
            lien.clear()
            lien["class"] = f"toc-n{niveau}"
            for classe, valeur in (("toc-titre", texte), ("toc-points", ""), ("toc-page", "00")):
                span = soup.new_tag("span", attrs={"class": classe})
                if valeur:
                    span.string = valeur
                lien.append(span)
            entrees.append(EntreeSommaire(cible_id, texte, niveau))

    corps = "".join(str(enfant) for enfant in racine.contents)
    return DocumentPrepare(corps=corps.strip(), couverture=couverture, sommaire=entrees)


def _figure_ancre(soup: BeautifulSoup, ancre: Tag, contenu: str, legende: str) -> None:
    """Remplit une ancre ``<figure data-graphique>`` et pose sa légende."""
    ancre.clear()
    ancre.attrs.pop("data-graphique", None)
    ancre.attrs.pop("data-legende", None)
    fragment = BeautifulSoup(contenu, "html.parser")
    for enfant in list(fragment.contents):
        ancre.append(enfant.extract())
    if legende:
        cap = soup.new_tag("figcaption")
        cap.string = legende
        ancre.append(cap)


def _injecter_figures(
    corps: str,
    specs: Mapping[str, Mapping[str, Any]],
    *,
    attribut: str,
    produire_svg: Callable[[Mapping[str, Any]], str],
    libelle: str,
    cle_disponibles: str,
    cle_sans_ancre: str,
) -> str:
    """Insère des SVG à la place d'ancres ``<figure data-…="id">``.

    Pourquoi une ancre plutôt qu'un simple paramètre de liste : l'ancre donne à
    l'agent le contrôle de **l'emplacement** (après le paragraphe qui commente
    la figure) sans lui donner le contrôle du **contenu**. Le SVG est construit
    par le serveur (graphique ou illustration).

    Le visuel est inséré en **SVG inline quelle que soit la cible** : Chromium
    (PDF) le rend nativement, et ``convertir_svg_en_png`` l'adapte ensuite pour
    Word et PowerPoint. Une seule voie d'insertion, une seule voie d'adaptation.
    """
    if not specs:
        return corps
    soup = BeautifulSoup(corps or "", "html.parser")
    ancres = soup.select(f"[{attribut}]")
    vues: set[str] = set()
    for ancre in ancres:
        identifiant = str(ancre.get(attribut) or "").strip()
        if not identifiant or identifiant not in specs:
            raise ValidationError(
                f"{libelle} inconnu référencé par le corps : {identifiant!r}",
                details={cle_disponibles: sorted(specs)},
            )
        if identifiant in vues:
            raise ValidationError(f"Ancre de {libelle.lower()} en double : {identifiant!r}")
        vues.add(identifiant)
        spec = specs[identifiant]
        _figure_ancre(
            soup,
            ancre,
            produire_svg(spec),
            str(spec.get("legende") or spec.get("titre") or spec.get("title") or ""),
        )
    manquants = sorted(set(specs) - vues)
    if manquants:
        raise ValidationError(
            f"{libelle}(s) déclaré(s) sans ancre dans le corps : " + ", ".join(manquants),
            details={cle_sans_ancre: manquants},
        )
    return str(soup)


def injecter_graphiques(
    corps: str,
    graphiques: Mapping[str, Mapping[str, Any]],
    *,
    produire_svg: Callable[[Mapping[str, Any]], str],
) -> str:
    """Remplace chaque ancre ``<figure data-graphique="id">`` par son graphique.

    Args:
        corps: corps HTML de l'agent.
        graphiques: ``{id: spec}`` du tool (spec vue par ``render_chart``).
        produire_svg: générateur de SVG (injecté pour rester testable sans moteur).

    Returns:
        Le corps avec les ancres remplacées, et **seulement** les ancres : le
        reste du HTML est inchangé à l'octet près.

    Raises:
        ValidationError: ancre sans graphique déclaré, identifiant déclaré mais
            aucune ancre dans le corps (le document mentirait sur son contenu).
    """
    return _injecter_figures(
        corps,
        graphiques,
        attribut="data-graphique",
        produire_svg=produire_svg,
        libelle="Graphique",
        cle_disponibles="graphiques_disponibles",
        cle_sans_ancre="graphiques_sans_ancre",
    )


def injecter_illustrations(
    corps: str,
    illustrations: Mapping[str, Mapping[str, Any]],
    *,
    produire_svg: Callable[[Mapping[str, Any]], str],
) -> str:
    """Remplace chaque ancre ``<figure data-illustration="id">`` par son dessin.

    Même contrat que ``injecter_graphiques`` : l'agent choisit l'emplacement, le
    moteur choisit le dessin (``illustrations.render_illustration``).
    """
    return _injecter_figures(
        corps,
        illustrations,
        attribut="data-illustration",
        produire_svg=produire_svg,
        libelle="Illustration",
        cle_disponibles="illustrations_disponibles",
        cle_sans_ancre="illustrations_sans_ancre",
    )


# ---------------------------------------------------------------------------
# SVG → DOCX : Word ne lit pas le SVG
# ---------------------------------------------------------------------------


def compter_svg(corps: str) -> int:
    """Nombre de SVG inline d'un corps — pour **annoncer** une omission."""
    return len(BeautifulSoup(corps or "", "html.parser").find_all("svg"))


def _taille_logique_svg(trace: Tag, xml: str) -> tuple[float, float]:
    """Taille logique d'un SVG, en pixels — même priorité que le HTML.

    ``width``/``height`` déclarés d'abord : c'est la taille **d'affichage** que
    l'agent a demandée, et la seule que Chromium respecte dans le PDF.
    ``viewBox`` ensuite : ce n'est qu'un système de coordonnées, mais son ratio
    et ses dimensions donnent une taille crédible quand rien n'est déclaré.
    Sans cette priorité, un schéma ``viewBox="0 0 600 600" width="200"`` aurait
    été rendu 3 fois plus grand en DOCX qu'en PDF.
    """
    largeur, hauteur = dimension_px(trace.get("width")), dimension_px(trace.get("height"))
    if largeur is not None and hauteur is not None:
        return largeur, hauteur
    return taille_svg(xml)


def _libelle_svg(trace: Tag) -> str:
    """Libellé d'un SVG : ``<title>``, sinon ``<desc>``, sinon ``aria-label``.

    Ce libellé devient l'attribut ``alt`` de l'image du DOCX : un schéma sans
    texte alternatif est un schéma inaccessible.
    """
    for nom in ("title", "desc"):
        noeud = trace.find(nom)
        texte = noeud.get_text(" ", strip=True) if noeud is not None else ""
        if texte:
            return texte[:200]
    return str(trace.get("aria-label") or "")[:200]


def _svg_pour_xml(trace: Tag) -> str:
    """Sérialise un SVG tel qu'un parseur **XML** le lira (resvg).

    ``BeautifulSoup`` analyse le document avec ``html.parser``, qui met les noms
    d'éléments et d'attributs en minuscules — correct pour du HTML, faux pour du
    SVG, qui est du XML à casse significative. Mesuré : resvg **ignore** un
    ``viewbox`` minuscule, et un SVG qui ne portait qu'un ``viewBox`` sortait
    distordu (220 × 160 au lieu de 400 × 160). Chromium, lui, applique la table
    d'ajustement HTML5 et n'a donc pas besoin de cette réparation : elle est
    faite ici, au seul endroit qui exige du XML strict.

    ``xmlns`` est posé s'il manque : sans lui, le document n'est pas un SVG
    pour un parseur XML — l'agent n'a pas à le savoir.
    """
    if not trace.get("xmlns"):
        trace["xmlns"] = "http://www.w3.org/2000/svg"
    for balise in [trace, *trace.find_all(True)]:
        balise.name = _ELEMENTS_XML.get(balise.name, balise.name)
        balise.attrs = {
            _ATTRIBUTS_XML.get(nom.lower(), nom): valeur
            for nom, valeur in balise.attrs.items()
        }
    return str(trace)


def convertir_svg_en_png(
    corps: str, rasteriser: Callable[[str], bytes] | None
) -> str:
    """Adapte les SVG inline au DOCX : PNG embarqué, ou retrait annoncé.

    Word (``python-docx``) ne sait pas lire un SVG. Sans cette conversion, le
    moteur DOCX **descendrait dans le SVG** et répandrait le texte de ses
    ``<text>`` en paragraphes : mesuré — un organigramme devenait une suite de
    mots épars (« Direction », « Formation »…), sans image. Une corruption
    silencieuse, jamais un livrable.

    Elle traite **tous** les SVG du corps, sans distinguer ceux du moteur
    (graphiques) de ceux de l'agent (schémas) : une seule voie d'adaptation.

    Args:
        corps: corps déjà nettoyé (``preparer``) et graphiques injectés.
        rasteriser: convertisseur SVG→PNG. ``None`` ⇒ aucun SVG ne peut entrer
            dans le DOCX : ils sont **retirés** (leur texte ne doit pas se
            répandre) et l'appelant annonce leur nombre dans les avertissements.

    Returns:
        Le corps adapté : chaque ``<svg>`` remplacé par ``<img>`` (PNG embarqué,
        ``alt`` = libellé du SVG, taille logique du SVG en ``width``/``height``).

    Raises:
        ValidationError: le SVG n'a pas pu être rasterisé. Aucun repli muet :
            le message nomme la figure et propose la sortie PDF.
    """
    soup = BeautifulSoup(corps or "", "html.parser")
    for trace in soup.find_all("svg"):
        if rasteriser is None:
            trace.decompose()
            continue
        xml = _svg_pour_xml(trace)
        libelle = _libelle_svg(trace)
        try:
            png = rasteriser(xml)
        except Exception as erreur:  # noqa: BLE001 - la cause exacte est transmise
            raise ValidationError(
                "SVG non rasterisable pour le DOCX "
                f"({libelle or 'figure sans titre'}) : "
                f"{type(erreur).__name__} : {erreur}",
                details={
                    "conseil": "Génère un PDF (le SVG y reste vectoriel) ou "
                    "corrige le SVG signalé.",
                },
            ) from erreur
        largeur, hauteur = _taille_logique_svg(trace, xml)
        image = soup.new_tag(
            "img",
            src=f"data:image/png;base64,{base64.b64encode(png).decode('ascii')}",
            alt=libelle,
            width=str(int(round(largeur))),
            height=str(int(round(hauteur))),
        )
        trace.replace_with(image)
    return str(soup)


def remplir_numeros(corps: str, numeros: dict[str, int]) -> str:
    """Écrit les numéros de page calculés dans le sommaire."""
    soup = BeautifulSoup(corps, "html.parser")
    for lien in soup.select("nav.toc a[href]"):
        span = lien.find("span", class_="toc-page")
        if span is not None:
            numero = numeros.get(str(lien["href"]).lstrip("#"))
            span.string = str(numero) if numero else ""
    return str(soup)


def construire_html(
    corps: str,
    *,
    theme: str | Theme = "corporate",
    titre: str = "",
    auteur: str = "",
    orientation: str = "portrait",
    css_supplementaire: str = "",
    couverture: bool = False,
    base_dir: str | Path | None = None,
) -> str:
    """Assemble une page HTML complète : thème + variables de couleur + contenu."""
    t = get_theme(theme)
    hauteur = "209mm" if orientation == "paysage" else "296mm"
    variables = (
        f":root{{--primary:{t.primary};--accent:{t.accent};--soft:{t.soft};"
        f"--callout:{t.callout};--cover-h:{hauteur};}}"
    )
    extra = (css_supplementaire or "").replace("</", "<\\/")
    base = f'<base href="{Path(base_dir).resolve().as_uri()}/">' if base_dir else ""
    classe = "page-couverture" if couverture else "page-corps"
    return (
        '<!DOCTYPE html><html lang="fr"><head><meta charset="utf-8">'
        f"{base}<title>{html.escape(titre)}</title>"
        f'<meta name="author" content="{html.escape(auteur)}">'
        f"<style>{THEME_CSS}\n{variables}\n{extra}</style></head>"
        f'<body class="{classe}">{corps}</body></html>'
    )
