"""ADR 0005 Phase 3 — lecture structurée (hiérarchie, style, tableaux, formules).

Ce que ces tests protègent : un agent doit pouvoir **comprendre la structure**
d'un document réel — hiérarchie des titres, dimensions de tableau, typographie,
fusions, formules — et pas seulement lire une longue chaîne de texte.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from document_fixtures import (
    build_docx_riche,
    build_pdf_stylise,
    build_xlsx_riche,
    write,
)

from app.core.errors import ValidationError
from app.documents.inspection import (
    inspecter_docx,
    inspecter_pdf,
    inspecter_xlsx,
    lire_xlsx_plage,
)


@pytest.fixture
def docx_riche(tmp_path: Path) -> Path:
    return write(tmp_path / "complexe.docx", build_docx_riche())


@pytest.fixture
def pdf_stylise(tmp_path: Path) -> Path:
    return write(tmp_path / "reference.pdf", build_pdf_stylise())


@pytest.fixture
def xlsx_riche(tmp_path: Path) -> Path:
    return write(tmp_path / "beneficiaires.xlsx", build_xlsx_riche())


class TestDocxStructure:
    def test_hierarchie_respecte_l_ordre_reel(self, docx_riche: Path) -> None:
        """Titres, listes et tableaux sont restitués dans l'ordre du document."""
        structure = inspecter_docx(docx_riche)
        types = [bloc["type"] for bloc in structure["hierarchie"]]
        assert types[0] == "titre"
        assert "liste" in types and "tableau" in types
        titres = [bloc for bloc in structure["hierarchie"] if bloc["type"] == "titre"]
        assert titres[0] == {
            "type": "titre",
            "element": 1,  # index de flux : ce que lit read_document_range
            "niveau": 1,
            "texte": "Offre technique — Lot 3",
        }
        assert titres[1]["niveau"] == 2 and titres[1]["texte"] == "Méthodologie"
        listes = [bloc for bloc in structure["hierarchie"] if bloc["type"] == "liste"]
        assert listes[0]["items"] == ["Phase 1 — cadrage", "Phase 2 — exécution"]
        assert listes[0]["numerotee"] is False

    def test_tableau_dimensions_entete_largeurs(self, docx_riche: Path) -> None:
        structure = inspecter_docx(docx_riche)
        tableau = structure["tableaux"][0]
        assert (tableau["nb_lignes"], tableau["nb_colonnes"]) == (3, 3)
        assert tableau["entete"] == ["Poste", "Quantité", "Montant"]
        assert tableau["largeurs_mm"] == pytest.approx([50.0, 50.0, 50.0], abs=0.1)
        assert tableau["nb_fusions"] == 0
        assert "lignes" not in tableau  # résumé : pas de matrice complète

    def test_tableau_complet_rend_les_cellules(self, docx_riche: Path) -> None:
        structure = inspecter_docx(docx_riche, niveau="complet")
        lignes = structure["tableaux"][0]["lignes"]
        assert lignes[1] == ["Formateur", "2", "12 000,00"]

    def test_saut_de_page_images_et_entetes(self, docx_riche: Path) -> None:
        structure = inspecter_docx(docx_riche)
        sauts = [
            bloc
            for bloc in structure["hierarchie"]
            if bloc["type"] == "paragraphe" and bloc.get("saut_de_page")
        ]
        assert sauts, "le saut de page doit être visible dans la hiérarchie"
        assert len(structure["images"]) == 1
        assert structure["images"][0]["largeur_mm"] == pytest.approx(30.0, abs=0.1)
        assert structure["has_headers_footers"] is True
        assert "Offre technique" in structure["entetes"][0]
        assert "proposé" in structure["pieds"][0]

    def test_sections_marges_et_metadonnees(self, docx_riche: Path) -> None:
        structure = inspecter_docx(docx_riche)
        section = structure["sections_detaillees"][0]
        assert section["orientation"] == "portrait"
        assert section["largeur_mm"] == pytest.approx(215.9, abs=0.5)  # Letter (modèle python-docx)
        assert section["marges_mm"]["gauche"] == pytest.approx(31.75, abs=0.5)  # 1,25" par défaut
        assert "title" in structure["metadata"]
        # Clés historiques conservées (Lot T1).
        assert structure["format"] == "docx"
        assert structure["nb_tableaux"] == 1
        assert any(style["nom"].startswith("Heading") for style in structure["styles"])

    def test_runs_en_mode_complet(self, docx_riche: Path) -> None:
        structure = inspecter_docx(docx_riche, niveau="complet")
        paragraphes = structure["paragraphes"]
        assert paragraphes, "le mode complet expose les paragraphes"
        runs = [
            run
            for paragraphe in paragraphes
            for run in paragraphe["runs"]
            if run["texte"] == "Contexte de la mission et attentes du commanditaire."
        ]
        assert runs
        # Aucune valeur inventée : une police laissée au style Word reste ``None``.
        assert runs[0]["police"] is None and runs[0]["taille_pt"] is None

    def test_runs_explicites_lus(self, tmp_path: Path) -> None:
        """Une police/une taille déclarée sur le run est restituée telle quelle."""
        from document_fixtures import build_reference_stylisee

        chemin = write(tmp_path / "reference.docx", build_reference_stylisee())
        structure = inspecter_docx(chemin, niveau="complet")
        runs = [
            run
            for paragraphe in structure["paragraphes"]
            for run in paragraphe["runs"]
            if run["police"] == "Georgia"
        ]
        assert runs, "la police explicite doit être identifiée"
        assert any(run["taille_pt"] == 24.0 for run in runs)
        assert any(run["couleur"] == "#0B2B4A" for run in runs)

    def test_niveau_inconnu_refuse(self, docx_riche: Path) -> None:
        with pytest.raises(ValidationError, match="Niveau"):
            inspecter_docx(docx_riche, niveau="integral")


class TestPdfStructure:
    def test_pages_et_typographie(self, pdf_stylise: Path) -> None:
        structure = inspecter_pdf(pdf_stylise)
        assert structure["nb_pages"] == 2
        assert structure["orientation"] == "portrait"
        assert len(structure["pages"]) == 2
        typographie = structure["typographie"]
        assert typographie["taille_titre_probable"] == pytest.approx(18.0, abs=0.1)
        assert "18.0" in typographie["tailles"]
        assert typographie["polices"], "les polices doivent être identifiées"

    def test_titres_probables_et_tableaux(self, pdf_stylise: Path) -> None:
        structure = inspecter_pdf(pdf_stylise)
        assert "Titre de page 1" in structure["pages"][0]["titres_probables"]
        assert structure["pages"][0]["nb_tableaux"] == 0
        assert structure["pages"][0]["nb_caracteres"] > 0

    def test_texte_uniquement_en_complet(self, pdf_stylise: Path) -> None:
        resume = inspecter_pdf(pdf_stylise)
        assert "texte" not in resume["pages"][0]
        complet = inspecter_pdf(pdf_stylise, niveau="complet")
        assert "Corps de texte du document de reference." in complet["pages"][0]["texte"]

    def test_limites_signalees_pour_grand_document(self, tmp_path: Path) -> None:
        document = write(tmp_path / "long.pdf", build_pdf_stylise(pages=12))
        structure = inspecter_pdf(document)
        assert any("analyse_detaillee_limitee" in limite for limite in structure["limites"])

    def test_pdf_chiffre_refuse(self, tmp_path: Path) -> None:
        from pypdf import PdfWriter

        writer = PdfWriter()
        writer.add_blank_page(width=200, height=200)
        writer.encrypt("secret")
        chemin = tmp_path / "chiffre.pdf"
        with chemin.open("wb") as flux:
            writer.write(flux)
        with pytest.raises(ValidationError, match="chiffré"):
            inspecter_pdf(chemin)


class TestXlsxStructure:
    def test_feuille_structuree(self, xlsx_riche: Path) -> None:
        structure = inspecter_xlsx(xlsx_riche)
        feuille = structure["feuilles"][0]
        assert structure["nb_feuilles"] == 1
        assert feuille["dimensions"] == "A1:D5"
        assert feuille["entete"] == [
            "Liste des bénéficiaires — lot 3",
            "",
            "",
            "",
        ]
        assert feuille["fusions"] == ["A1:D1"]
        assert feuille["nb_fusions"] == 1

    def test_formules_et_colonnes(self, xlsx_riche: Path) -> None:
        structure = inspecter_xlsx(xlsx_riche)
        feuille = structure["feuilles"][0]
        assert feuille["formules"]["D3"] == "=C3*10"
        assert feuille["formules"]["D5"] == "=SUM(D3:D4)"
        assert feuille["nb_formules"] == 3
        largeurs = {colonne["lettre"]: colonne["largeur"] for colonne in feuille["colonnes"]}
        assert largeurs["A"] == pytest.approx(22.0)

    def test_styles_detectes(self, xlsx_riche: Path) -> None:
        feuille = inspecter_xlsx(xlsx_riche)["feuilles"][0]
        assert feuille["styles"]["cellules_gras"] >= 2
        assert feuille["styles"]["cellules_remplies"] >= 4

    def test_apercu_en_complet_et_valeurs_par_plage(self, xlsx_riche: Path) -> None:
        complet = inspecter_xlsx(xlsx_riche, niveau="complet")
        apercu = complet["feuilles"][0]["apercu"]
        assert apercu[0][0] == "Liste des bénéficiaires — lot 3"
        assert "C3*10" in apercu[2][3]  # formule conservée : la structure décrit la feuille

        plage = lire_xlsx_plage(xlsx_riche, from_row=2, to_row=3)
        assert plage["lignes"][0][0] == "Nom"
        assert plage["lignes"][1][0] == "Karimi"
