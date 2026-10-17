"""Contrôles qualité : avertissements renvoyés à l'agent pour qu'il se corrige."""

from __future__ import annotations

import re

from bs4 import BeautifulSoup

FORMULES_CREUSES = (
    "il est important de noter",
    "dans un monde en constante évolution",
    "jouer un rôle crucial",
    "joue un rôle crucial",
    "en somme",
    "plongeons",
    "n'hésitez pas à",
)
RESTES_ASSISTANT = ("bien sûr !", "voici votre", "en tant qu'ia", "en tant qu'assistant")

_RE_LAYOUT = re.compile(r"display\s*:\s*(flex|grid)|position\s*:\s*absolute|float\s*:", re.I)
_RE_STYLE_INLINE = re.compile(r"""style\s*=\s*["'][^"']*(color|font|background)""", re.I)
_RE_MARKDOWN = re.compile(r"\*\*[^*\n]+\*\*|^\s*#{1,3}\s+\w", re.M)
_RE_PLACEHOLDER = re.compile(r"\[[A-ZÀ-ÖØ-Ý][^\]\n<>]{2,40}\]|\bXXX+\b|lorem ipsum", re.I)

#: Références SVG locales : ancre interne ou image raster embarquée (mêmes
#: règles que ``html_builder._RE_REF_SVG``, appliquées au HTML brut).
_RE_REF_LOCALE_SVG = re.compile(r"^(?:#|data:image/(?:png|jpe?g|gif|webp);base64,)", re.I)

#: Serveur de peinture distant dans un attribut (``fill="url(http://…)"``,
#: ``marker-end``, ``clip-path``…), retiré par ``html_builder``.
_RE_PAINT_EXTERNE = re.compile(r"url\(\s*['\"]?\s*(?!#)", re.I)

#: Primitives qui dessinent réellement : leur absence = schéma vide (mesuré :
#: un `<svg>` sans dessin produit un PNG valide mais blanc, donc une figure
#: blanche silencieuse dans le livrable).
DESSIN_SVG = (
    "rect", "circle", "ellipse", "line", "polyline", "polygon",
    "path", "text", "image", "use",
)


def _colonnes(table) -> int:
    ligne = table.find("tr")
    if ligne is None:
        return 0
    total = 0
    for cellule in ligne.find_all(["td", "th"], recursive=False):
        try:
            total += max(1, int(cellule.get("colspan", 1)))
        except ValueError:
            total += 1
    return total


def verifier(contenu: str, format: str = "pdf", orientation: str = "portrait") -> list[str]:
    """Analyse le HTML fourni par l'agent et retourne une liste d'avertissements lisibles."""
    avertissements: list[str] = []
    if not contenu or not contenu.strip():
        return ["Contenu HTML vide."]

    soup = BeautifulSoup(contenu, "html.parser")
    texte = soup.get_text(" ", strip=True)
    texte_min = texte.lower()

    # Les contrôles de mise en page et de CSS visent le DOCUMENT, pas le SVG :
    # dans un schéma, `style="font-…"` et une couleur explicite sont légitimes
    # et n'affectent en rien le thème (le SVG est un langage graphique à part).
    hors_svg = BeautifulSoup(contenu, "html.parser")
    for trace in hors_svg.find_all("svg"):
        trace.decompose()
    hors_svg_texte = str(hors_svg)

    if soup.find("style"):
        avertissements.append("Balise <style> ignorée : le thème est injecté automatiquement.")
    if soup.find("script"):
        avertissements.append("Balise <script> supprimée (interdite).")
    if _RE_LAYOUT.search(hors_svg_texte):
        avertissements.append(
            "flex/grid/absolute/float détecté : utilise table.layout ou table.kpi."
        )
    if _RE_STYLE_INLINE.search(hors_svg_texte):
        avertissements.append("Styles inline de couleur/police détectés : laisse le thème gérer.")
    if _RE_MARKDOWN.search(texte if "<" in contenu else contenu):
        avertissements.append(
            "Syntaxe Markdown détectée (** ou #) : utilise <strong> et <h1>/<h2>."
        )

    placeholders = sorted({m.group(0) for m in _RE_PLACEHOLDER.finditer(texte)})
    if placeholders:
        avertissements.append(
            "Placeholder détecté : "
            + ", ".join(placeholders[:5])
            + " -> remplace par une vraie valeur."
        )

    if not soup.find("h1"):
        avertissements.append("Aucun titre <h1> : la hiérarchie du document est absente.")

    creuses = [f for f in FORMULES_CREUSES if f in texte_min]
    if creuses:
        avertissements.append(
            "Formules creuses à reformuler : " + ", ".join(f"« {f} »" for f in creuses)
        )
    restes = [r for r in RESTES_ASSISTANT if r in texte_min]
    if restes:
        avertissements.append(
            "Restes de conversation d'assistant : " + ", ".join(f"« {r} »" for r in restes)
        )

    for table in soup.find_all("table", class_="data"):
        n = _colonnes(table)
        if n > 6 and orientation == "portrait":
            avertissements.append(f"Tableau de {n} colonnes : envisage orientation='paysage'.")
        if table.find("thead") is None:
            avertissements.append("Tableau table.data sans <thead> : ajoute une ligne d'en-tête.")

    if format == "docx":
        for img in soup.find_all("img"):
            if re.match(r"^https?://", str(img.get("src") or "")):
                avertissements.append(
                    "Image distante non intégrée dans le DOCX : utilise un fichier "
                    "local ou data:image."
                )
                break

    avertissements.extend(verifier_graphiques(soup))
    avertissements.extend(verifier_svg(soup, format))
    return avertissements


def verifier_svg(soup: BeautifulSoup, format: str = "pdf") -> list[str]:
    """Constats sur les SVG **écrits par l'agent** (schémas, illustrations).

    L'agent a le droit d'en écrire : ces constats lui disent ce qui a été retiré
    (nettoyage) ou ce qui manque pour que son schéma survive au rendu — DOCX
    compris, où le SVG devient une image.

    Note : ``verifier`` analyse le HTML **brut** de l'agent, donc déjà analysé
    par ``html.parser`` — les noms y sont en minuscules (``viewbox``,
    ``foreignobject``).
    """
    constats: list[str] = []
    for svg in soup.find_all("svg"):
        if not svg.get("viewbox") and not svg.get("width"):
            constats.append(
                "SVG sans viewBox ni largeur : donne-lui un viewBox (par exemple "
                "viewBox=\"0 0 800 400\") pour qu'il se mette à l'échelle."
            )
        if not svg.find(DESSIN_SVG):
            constats.append(
                "SVG sans dessin : aucun <rect>, <path>, <text>… — un schéma "
                "vide apparaîtrait comme une figure blanche dans le document."
            )
        if svg.find("foreignobject"):
            constats.append(
                "<foreignObject> retiré du SVG : le HTML embarqué n'est pas "
                "accepté dans un schéma — dessine avec les primitives SVG."
            )
        distantes: set[str] = set()
        for balise in [svg, *svg.find_all(True)]:
            reference = str(balise.get("href") or balise.get("xlink:href") or "").strip()
            if reference and not _RE_REF_LOCALE_SVG.match(reference):
                distantes.add(reference)
            distantes.update(
                valeur
                for valeur in (str(v) for v in balise.attrs.values())
                if _RE_PAINT_EXTERNE.search(valeur)
            )
        if distantes:
            constats.append(
                "Référence distante retirée du SVG ("
                + ", ".join(sorted(distantes)[:3])
                + ") : un schéma ne peut pas dépendre d'une ressource externe."
            )
        evenements = sorted(
            {
                attribut
                for balise in [svg, *svg.find_all(True)]
                for attribut in balise.attrs
                if attribut.lower().startswith("on")
            }
        )
        if evenements:
            constats.append(
                "Attribut(s) d'événement retiré(s) du SVG : "
                + ", ".join(evenements)
                + " (sans effet dans un document)."
            )
    if format == "docx" and soup.find("svg"):
        constats.append(
            "SVG dans un DOCX : converti en image PNG haute résolution (Word ne lit "
            "pas le SVG). Pour un schéma vectoriel net à l'impression, choisis "
            "format='pdf'."
        )
    return constats


def verifier_graphiques(soup: BeautifulSoup) -> list[str]:
    """Constats sur les ancres ``<figure data-graphique>`` du corps.

    Le tool refusera de toute façon un id sans spec (et inversement) : ces
    avertissements apprennent à l'agent à écrire le bon HTML AVANT l'appel —
    une figure non remplie dans un livrable est un échec, pas un silence.
    """
    constats: list[str] = []
    ancres = soup.select("[data-graphique]")
    ids = [str(a.get("data-graphique") or "").strip() for a in ancres]
    vides = [i for i, a in zip(ids, ancres, strict=True) if not i]
    if vides:
        constats.append(
            "Ancre de graphique sans id (data-graphique vide) : le graphique ne "
            "sera pas placé."
        )
    doublons = sorted({i for i in ids if i and ids.count(i) > 1})
    if doublons:
        constats.append(
            "Ancre de graphique en double : " + ", ".join(doublons)
            + " — chaque id doit apparaître une seule fois."
        )
    return constats
