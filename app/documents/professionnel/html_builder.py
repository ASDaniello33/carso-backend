"""Nettoyage du HTML de l'agent et construction de la page HTML complète (thème injecté)."""

from __future__ import annotations

import html
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from bs4 import BeautifulSoup, Comment, Tag

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


def preparer(contenu: str) -> DocumentPrepare:
    """Nettoie le HTML, sépare la couverture et prépare le sommaire."""
    soup = BeautifulSoup(contenu or "", "html.parser")
    racine = _racine(soup)

    # 1. Nettoyage de sécurité
    for balise in racine.find_all(BALISES_INTERDITES):
        balise.decompose()
    for commentaire in racine.find_all(string=lambda s: isinstance(s, Comment)):
        commentaire.extract()
    for balise in racine.find_all(True):
        for attribut in list(balise.attrs):
            if attribut.lower().startswith("on"):
                del balise[attribut]
        if str(balise.get("href", "")).strip().lower().startswith("javascript:"):
            del balise["href"]

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
