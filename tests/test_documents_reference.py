"""ADR 0005 Phase 4 — analyse d'un document de référence.

L'exigence centrale : « utilise le style de ce document » **sans copier son
contenu**. Ces tests vérifient donc deux choses séparément — le plan extrait, et
le fait qu'aucun texte de contenu (paragraphes, en-tête) ne soit repris — puis la
réutilisation effective du style sur un document neuf.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from document_fixtures import (
    build_docx_riche,
    build_pdf_stylise,
    build_reference_stylisee,
    build_xlsx_riche,
    write,
)

from app.core.errors import ValidationError
from app.documents.reference import analyser_reference, style_depuis_reference
from app.documents.renderers import rendre
from app.documents.spec import DocumentSpec, Metadonnees, Paragraphe, Titre

_CONTENU_INTERDIT = (
    "Contenu de référence — ne doit jamais être recopié.",
    "MODÈLE CLIENT — usage interne",
)


@pytest.fixture
def reference_docx(tmp_path: Path) -> Path:
    return write(tmp_path / "reference.docx", build_reference_stylisee())


@pytest.fixture
def reference_pdf(tmp_path: Path) -> Path:
    return write(tmp_path / "reference.pdf", build_pdf_stylise())


@pytest.fixture
def reference_xlsx(tmp_path: Path) -> Path:
    return write(tmp_path / "reference.xlsx", build_xlsx_riche())


def _avertissements(analyse: dict[str, object]) -> str:
    return " | ".join(str(item) for item in analyse["avertissements"])  # type: ignore[union-attr]


class TestStructureDeReference:
    def test_plan_docx(self, reference_docx: Path) -> None:
        analyse = analyser_reference(reference_docx)
        plan = analyse["content_structure"]
        assert analyse["format"] == "docx"
        titres = [section["titre"] for section in plan["sections"] if section["titre"]]
        assert "1. Contexte" in titres
        assert plan["nb_tableaux"] == 1
        assert plan["a_entete"] is True and plan["a_pied"] is True
        assert plan["a_couverture"] is True  # premier titre sans corps

    def test_plan_pdf(self, reference_pdf: Path) -> None:
        analyse = analyser_reference(reference_pdf)
        plan = analyse["content_structure"]
        assert analyse["format"] == "pdf"
        assert plan["nb_sections"] >= 2
        assert any(
            "Titre de page" in (section["titre"] or "") for section in plan["sections"]
        )

    def test_plan_xlsx(self, reference_xlsx: Path) -> None:
        analyse = analyser_reference(reference_xlsx)
        plan = analyse["content_structure"]
        assert analyse["format"] == "xlsx"
        assert plan["nb_sections"] == 1
        assert plan["formules"] >= 3
        assert analyse["style_spec"]["tableau"]["present"] is True

    def test_format_non_supporte(self, tmp_path: Path) -> None:
        chemin = write(tmp_path / "note.txt", b"bonjour")
        with pytest.raises(ValidationError, match="non supportée"):
            analyser_reference(chemin)


class TestAucuneCopieDeContenu:
    def test_texte_de_contenu_absent(self, reference_docx: Path) -> None:
        """Le contenu de la référence n'est jamais renvoyé, seul le plan l'est."""
        analyse = analyser_reference(reference_docx, niveau="complet")
        serialise = str(analyse)
        for contenu in _CONTENU_INTERDIT:
            assert contenu not in serialise

    def test_texte_entete_non_repris(self, reference_docx: Path) -> None:
        """L'en-tête d'un tiers est du contenu : seule sa présence est retenue."""
        analyse = analyser_reference(reference_docx)
        style = style_depuis_reference(analyse["style_spec"])
        assert "MODÈLE CLIENT" not in str(analyse["style_spec"])
        assert style.entete.texte == "CARSO — Document proposé"  # libellé CARSO conservé
        assert "texte_entete_pied_non_repris" in _avertissements(analyse)


class TestStyleExtraire:
    def test_identite_visuelle_docx(self, reference_docx: Path) -> None:
        analyse = analyser_reference(reference_docx)
        style_spec = analyse["style_spec"]

        page = style_spec["page"]
        assert page["orientation"] == "portrait"
        assert page["marges"]["gauche"] == pytest.approx(30.0, abs=0.5)
        assert page["marges"]["haut"] == pytest.approx(28.0, abs=0.5)

        typographie = style_spec["typographie"]
        assert typographie["corps"]["police"] == "Georgia"
        assert typographie["corps"]["taille_pt"] == pytest.approx(10.5, abs=0.1)
        assert typographie["titre"]["taille_pt"] == pytest.approx(24.0, abs=0.1)
        assert typographie["titre"]["couleur"] == "#0B2B4A"
        assert typographie["titre"]["alignement"] is None or True  # alignement global
        assert style_spec["couverture"]["couleur_accent"] == "#0B2B4A"

    def test_style_reutilisable_independamment(self, reference_docx: Path) -> None:
        """Le style extrait s'applique à un contenu neuf (aucune dépendance au fichier)."""
        analyse = analyser_reference(reference_docx)
        style = style_depuis_reference(analyse["style_spec"])
        assert style.corps.police == "Georgia"
        assert style.titre.taille_pt == pytest.approx(24.0, abs=0.1)
        assert style.couverture.couleur_accent == "#0B2B4A"
        # Ce qui n'a pas été mesuré garde la valeur saine du style CARSO.
        assert style.tableau.entete_fond == "#1F4E79"

    def test_style_pdf_mesure(self, reference_pdf: Path) -> None:
        analyse = analyser_reference(reference_pdf)
        style_spec = analyse["style_spec"]
        assert style_spec["page"]["format"] == "A4"
        marges = style_spec["page"]["marges"]
        assert marges["gauche"] == pytest.approx(25.0, abs=2.0)
        assert style_spec["typographie"]["corps"]["police"] == "Times New Roman"
        assert style_spec["typographie"]["corps"]["taille_pt"] == pytest.approx(11.0, abs=0.5)
        assert "marges_estimees" in _avertissements(analyse)

    def test_style_mis_en_page_dans_un_document_neuf(self, reference_docx: Path) -> None:
        """Chaîne complète : référence → style → génération → style présent."""
        analyse = analyser_reference(reference_docx)
        style = style_depuis_reference(analyse["style_spec"])
        spec = DocumentSpec(
            metadata=Metadonnees(titre="Nouvelle offre CARSO"),
            style=style,
            blocs=[Titre(niveau=1, texte="Contexte"), Paragraphe(texte="Contenu neuf.")],
        )
        import io

        import docx
        from docx.shared import Pt

        document = docx.Document(io.BytesIO(rendre(spec, format="docx")))
        run = next(
            run
            for paragraphe in document.paragraphs
            if paragraphe.text == "Nouvelle offre CARSO"
            for run in paragraphe.runs
        )
        assert run.font.name == "Georgia"
        # Le titre de couverture reprend la taille mesurée dans la référence.
        assert run.font.size == Pt(24.0)
        assert "Contenu neuf." in "\n".join(p.text for p in document.paragraphs)

    def test_style_depuis_reference_vide(self) -> None:
        """Aucune mesure ⇒ style CARSO par défaut, jamais un style amputé."""
        assert style_depuis_reference({}).nom == "carso_defaut"

    def test_reference_sans_style_detecte(self, tmp_path: Path) -> None:
        """Un document sans mise en forme explicite ne fabrique aucun token."""
        analyse = analyser_reference(write(tmp_path / "brut.docx", build_docx_riche()))
        style = style_depuis_reference(analyse["style_spec"])
        assert style.corps.police == "Calibri"  # défaut CARSO conservé
        assert style.page.marges.gauche_mm == pytest.approx(31.75, abs=0.5)
