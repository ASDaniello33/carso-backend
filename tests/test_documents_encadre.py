"""Tests — libellé d'encadré non dupliqué (PDF, DOCX, HTML).

Défaut constaté sur une pièce réelle (INC-131) : un encadré dont le texte
commence par son propre libellé — « Note : … », « Information manquante : … » —
donnait « Note — Note : … ». Le libellé du genre ne doit s'écrire que s'il
n'est pas déjà dans le texte.
"""

from __future__ import annotations

import io
import re
import zipfile

from app.documents.renderers import rendre
from app.documents.spec import DocumentSpec, Encadre, Metadonnees


def _spec(encadre: Encadre) -> DocumentSpec:
    return DocumentSpec(metadata=Metadonnees(titre="Encadrés"), blocs=[encadre])


def _pdf(encadre: Encadre) -> str:
    from pypdf import PdfReader

    octets = rendre(_spec(encadre), format="pdf")
    return "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(octets)).pages)


def _docx(encadre: Encadre) -> str:
    octets = rendre(_spec(encadre), format="docx")
    xml = zipfile.ZipFile(io.BytesIO(octets)).read("word/document.xml").decode("utf-8")
    return " ".join(re.findall(r"<w:t[^>]*>([^<]*)</w:t>", xml))


def _html(encadre: Encadre) -> str:
    return rendre(_spec(encadre), format="html").decode("utf-8")


class TestLibelleNonDuplique:
    """Le libellé déjà présent dans le texte n'est pas réécrit."""

    def test_pdf(self) -> None:
        texte = _pdf(Encadre(genre="note", texte="Note : le lieu reste à confirmer."))
        assert "Note : le lieu" in texte
        assert texte.count("Note") == 1

    def test_docx(self) -> None:
        texte = _docx(Encadre(genre="note", texte="Note : le lieu reste à confirmer."))
        assert "Note : le lieu" in texte
        assert texte.count("Note") == 1

    def test_html(self) -> None:
        texte = _html(Encadre(genre="note", texte="Note : le lieu reste à confirmer."))
        assert "Note : le lieu" in texte
        assert texte.count("Note") == 1

    def test_genre_avertissement(self) -> None:
        texte = _pdf(
            Encadre(
                genre="avertissement",
                texte="Information manquante : l'e-mail n'est pas enregistré.",
            )
        )
        assert texte.count("Information manquante") == 1


class TestLibelleConserve:
    """Le libellé s'écrit quand le texte ne le porte pas déjà."""

    def test_libelle_ajoute(self) -> None:
        texte = _pdf(Encadre(genre="note", texte="Le lieu reste à confirmer."))
        assert "Note" in texte

    def test_pas_de_confusion_de_prefixe(self) -> None:
        """« Info » ne doit pas masquer « Informations générales »."""
        texte = _pdf(Encadre(genre="info", texte="Informations générales sur la mission."))
        assert texte.count("Info") >= 1
        assert "Informations générales" in texte
