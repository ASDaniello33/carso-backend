"""Tests — pagination des tableaux PDF (en-tête répété).

Défaut constaté sur une pièce réelle : un tableau qui commençait en bas de page
voyait sa ligne d'en-tête déclencher le saut de page ; le renderer répétait
alors l'en-tête **puis** réécrivait cette même ligne, d'où deux bandeaux tramés
collés en haut de page. Le test lit le texte réel du PDF, page par page, sur le
cas exact : un tableau repoussé seul sur la page suivante.
"""

from __future__ import annotations

import io

from pypdf import PdfReader

from app.documents.renderers import rendre
from app.documents.spec import DocumentSpec, Metadonnees, Paragraphe, Tableau


def _pages(nb_remplissage: int) -> list[str]:
    blocs: list[object] = [
        Paragraphe(texte="Ligne de remplissage pour atteindre le bas de page.")
        for _ in range(nb_remplissage)
    ]
    blocs.append(
        Tableau(entete=["Colonne A", "Colonne B"], lignes=[["valeur", "x"]])
    )
    octets = rendre(
        DocumentSpec(metadata=Metadonnees(titre="Pagination"), blocs=blocs), format="pdf"
    )
    return [page.extract_text() or "" for page in PdfReader(io.BytesIO(octets)).pages]


class TestEnteteRepete:
    """L'en-tête n'est écrit qu'une fois quand il ouvre lui-même une page."""

    def test_entete_repete_une_seule_fois(self) -> None:
        cas = None
        for nb_remplissage in range(1, 40):
            pages = _pages(nb_remplissage)
            if len(pages) == 2 and "Colonne A" not in pages[0]:
                cas = pages
                break

        assert cas is not None, "aucun remplissage ne repousse le tableau page 2"
        assert cas[1].count("Colonne A") == 1, "en-tête écrit deux fois en haut de page"
