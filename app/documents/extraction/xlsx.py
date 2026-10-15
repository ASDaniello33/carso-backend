"""Extraction de contenu XLSX (openpyxl).

Les listes de bénéficiaires CARSO sont des classeurs (instruction/06 §8). On en
extrait une représentation **tabulée** : une ligne par ligne de feuille, colonnes
séparées par une tabulation, en-têtes conservés. C'est la base réutilisable par
l'import bénéficiaires d'une phase ultérieure (analyse de structure et mapping
de colonnes), mais **aucun import n'est réalisé ici** : l'extraction est une
lecture.

L'extraction s'arrête dès que le plafond de caractères est atteint
(``tronque=True``) : un classeur volumineux ne produit jamais un texte non borné.
"""

from __future__ import annotations

from pathlib import Path

from app.core.errors import ValidationError
from app.documents.extraction.base import ExtractedContent, require_library

_COLUMN_SEPARATOR = "\t"


class XlsxExtractor:
    """Extracteur XLSX basé sur openpyxl."""

    extension = "xlsx"
    adaptateur = "openpyxl"
    librairie: str = "openpyxl"

    def extract(self, path: Path, *, max_chars: int) -> ExtractedContent:
        openpyxl = require_library(self.librairie)

        try:
            classeur = openpyxl.load_workbook(
                filename=str(path), read_only=True, data_only=True
            )
        except Exception as exc:  # openpyxl lève des erreurs variées si corrompu
            msg = "Classeur XLSX illisible (fichier corrompu)"
            raise ValidationError(msg) from exc

        lignes: list[str] = []
        feuilles: list[str] = []
        longueur = 0
        tronque = False

        try:
            for feuille in classeur.worksheets:
                if tronque:
                    break
                feuilles.append(feuille.title)
                lignes.append(f"# Feuille : {feuille.title}")
                for ligne in feuille.iter_rows(values_only=True):
                    texte = _render_row(ligne)
                    if not texte:
                        continue
                    lignes.append(texte)
                    longueur += len(texte) + 1
                    if longueur >= max_chars:
                        tronque = True
                        break
        finally:
            classeur.close()

        contenu = "\n".join(lignes).strip()
        if not contenu:
            msg = "Aucun contenu extractible dans ce classeur XLSX"
            raise ValidationError(msg)

        return ExtractedContent(
            texte=contenu,
            adaptateur=self.adaptateur,
            nb_feuilles=len(feuilles),
            feuilles=tuple(feuilles),
            tronque=tronque,
        )


def _render_row(valeurs: tuple[object, ...]) -> str:
    """Rend une ligne de feuille en texte tabulé (lignes vides ignorées)."""
    cellules = ["" if valeur is None else str(valeur).strip() for valeur in valeurs]
    if not any(cellules):
        return ""
    return _COLUMN_SEPARATOR.join(cellules).rstrip()
