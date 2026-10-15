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

    if soup.find("style"):
        avertissements.append("Balise <style> ignorée : le thème est injecté automatiquement.")
    if soup.find("script"):
        avertissements.append("Balise <script> supprimée (interdite).")
    if _RE_LAYOUT.search(contenu):
        avertissements.append(
            "flex/grid/absolute/float détecté : utilise table.layout ou table.kpi."
        )
    if _RE_STYLE_INLINE.search(contenu):
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

    return avertissements
