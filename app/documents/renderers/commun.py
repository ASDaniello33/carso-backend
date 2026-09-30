"""Règles de présentation **partagées** par les renderers (DOCX, PDF, XLSX).

Un tableau doit se lire de la même façon quel que soit le format de sortie :
l'en-tête est coloré et gras, les lignes alternées sont tramées, la ligne de
total ressort, et une cellule peut toujours décider pour elle-même. Ces règles
vivaient dans le renderer DOCX ; elles sont ici parce que le renderer XLSX en a
besoin **des mêmes** — dupliquer la décision produirait deux vérités visuelles.

Aucune librairie tierce n'est importée ici : ces fonctions ne manipulent que le
``DocumentSpec`` et ses tokens de style.
"""

from __future__ import annotations

from typing import Any

from app.documents.styles import DocumentStyle, StyleTableau

__all__ = [
    "alignement_cellule",
    "couleur_cellule",
    "fond_cellule",
    "gras_cellule",
    "est_ligne_de_donnee_alternee",
]

#: Alignement du spec → valeur neutre, traduite par chaque renderer.
ALIGNEMENTS: dict[str, str] = {
    "gauche": "gauche",
    "centre": "centre",
    "droite": "droite",
}


def est_ligne_de_donnee_alternee(index_donnee: int) -> bool:
    """Vrai si la ligne de données (0-based, en-tête exclue) porte la bande alternée."""
    return index_donnee % 2 == 1


def gras_cellule(
    cellule: Any, *, en_entete: bool, total: bool, tokens: StyleTableau
) -> bool:
    """Graisse effective d'une cellule (la cellule décide, sinon l'en-tête/le total)."""
    if cellule.gras is not None:
        return bool(cellule.gras)
    return bool(tokens.entete_gras) if en_entete else total


def fond_cellule(
    cellule: Any,
    *,
    en_entete: bool,
    total: bool,
    alternance: bool,
    tokens: StyleTableau,
) -> str | None:
    """Trame effective d'une cellule (cellule > en-tête > total > bande alternée)."""
    if cellule.fond:
        return cellule.fond
    if en_entete:
        return tokens.entete_fond or None
    if total or alternance:
        return tokens.bande_fond or None
    return None


def couleur_cellule(
    cellule: Any, *, en_entete: bool, style: DocumentStyle, tokens: StyleTableau
) -> str:
    """Couleur de texte effective d'une cellule (cellule > en-tête > corps)."""
    if cellule.couleur_texte:
        return cellule.couleur_texte
    return tokens.entete_couleur if en_entete else style.corps.couleur


def alignement_cellule(
    cellule: Any, alignements: list[str] | None, colonne: int
) -> str | None:
    """Alignement de la cellule, sinon celui de sa colonne de départ."""
    if cellule.alignement:
        return cellule.alignement
    if alignements and colonne < len(alignements):
        return alignements[colonne]
    return None
