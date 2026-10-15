"""Incrément 52 — recherche ciblée dans un document + pagination honnête.

Ce que ces tests protègent :

- l'agent localise un contenu par mot-clé et reçoit des **pages + un extrait
  borné** (jamais le document entier), pour les trois formats ;
- ``page`` est le repère **exact** de ``lire_*_plage`` : un hit se relit avec
  ``read_document_range(from_page=page, to_page=page)`` — y compris un hit de
  **tableau** (incrément 53 : l'unité d'un DOCX est l'élément de flux, pas le
  paragraphe, sinon un tableau restait irrelisible et l'agent bouclait) ;
- chaque hit porte ``relire`` : les arguments exacts de ``lire_element_document``
  pour le relire à l'identique (tableau, ligne de tableau, en-tête, page, ligne) ;
- les tableaux **et les en-têtes/pieds** d'un DOCX sont parcourus ;
- les bornes sont tenues (plafond serveur, contexte court, mot-clé trop court) ;
- ``get_document_structure`` rend un nombre de pages honnête : exact pour un
  PDF, ``null`` + unité de lecture pour un DOCX/XLSX sans pages physiques.
"""

from __future__ import annotations

from pathlib import Path

import docx as python_docx  # le module est masqué par la fixture ``docx``
import pytest
from document_fixtures import (
    build_docx,
    build_pdf,
    build_pdf_sans_texte,
    build_pdf_stylise,
    build_xlsx,
    write,
)

from app.core.errors import ValidationError
from app.documents.elements import lire_element
from app.documents.inspection import (
    inspecter_docx,
    inspecter_pdf,
    inspecter_xlsx,
    lire_docx_plage,
    lire_pdf_plage,
    lire_xlsx_plage,
)
from app.documents.recherche import (
    LIMITE_RESULTATS_RECHERCHE,
    chercher_dans_docx,
    chercher_dans_pdf,
    chercher_dans_xlsx,
)


@pytest.fixture
def docx(tmp_path: Path) -> Path:
    return write(
        tmp_path / "offre.docx",
        build_docx(
            paragraphes=[
                "Offre technique — Lot 3",
                "Le budget total de la mission est de 12 000 euros.",
                "Méthodologie",
            ],
            tableau=[
                ["Poste", "Tarif"],
                ["Formateur", "450 euros par jour"],
            ],
        ),
    )


@pytest.fixture
def pdf(tmp_path: Path) -> Path:
    return write(tmp_path / "reference.pdf", build_pdf_stylise(pages=3))


class TestRechercheDocx:
    def test_hit_paragraphe_et_relisible(self, docx: Path) -> None:
        """Le hit porte ``page``/``content`` et cet élément se relit tel quel."""
        resultat = chercher_dans_docx(docx, "budget")

        assert resultat["format"] == "docx"
        assert resultat["unite"] == "elements"
        assert resultat["nb_resultats"] == 1
        hit = resultat["resultats"][0]
        assert hit["page"] == 2  # index 1-indexé de l'élément contenant « budget »
        assert hit["element"] == 2
        assert hit["type_bloc"] == "paragraphe"
        assert "budget" in hit["content"].lower()
        assert "12 000 euros" in hit["content"]
        assert hit["occurrences"] == 1
        assert hit["relire"]["arguments"]["index"] == 2
        # Le repère est bien celui de la lecture bornée : même unité, même index.
        plage = lire_docx_plage(docx, from_page=hit["page"], to_page=hit["page"])
        assert "budget total" in plage["texte"]
        assert plage["unite"] == "elements"

    def test_hit_dans_un_tableau_est_relisible(self, docx: Path) -> None:
        """Régression incrément 53 : un hit de tableau se relit **à l'identique**.

        Avant, ``page`` désignait l'index du paragraphe voisin : relire la page
        rendait un texte sans le mot-clé, et l'agent relançait la recherche en
        boucle. L'unité est désormais l'élément de flux, et ``relire`` pointe la
        ligne exacte du tableau.
        """
        resultat = chercher_dans_docx(docx, "Formateur")

        assert resultat["nb_resultats"] == 1
        hit = resultat["resultats"][0]
        assert hit["type_bloc"] == "tableau"
        assert hit["tableau"] == 1
        assert hit["ligne_tableau"] == 2
        assert hit["page"] == 4  # 4e élément du flux : le tableau, pas un voisin
        assert hit["element"] == 4
        assert "450 euros" in hit["content"]

        # Chemin 1 : la plage d'éléments (read_document_range).
        plage = lire_docx_plage(docx, from_page=hit["page"], to_page=hit["page"])
        assert "Formateur" in plage["texte"] and "450 euros" in plage["texte"]
        assert plage["elements"][0]["type"] == "tableau"

        # Chemin 2 : le locator rendu par la recherche (lire_element_document).
        relu = lire_element(docx, **hit["relire"]["arguments"])
        assert relu["type_element"] == "tableau"
        assert relu["tableau"] == 1
        assert "Formateur | 450 euros par jour" in relu["texte"]

    def test_hit_dans_un_entete_est_relisible(self, tmp_path: Path) -> None:
        """Un en-tête de DOCX est cherché et relu par section (aucun élément de flux)."""
        chemin = tmp_path / "entete.docx"
        chemin.write_bytes(
            build_docx(paragraphes=["Corps du document"], tableau=None)
        )
        document = python_docx.Document(str(chemin))
        document.sections[0].header.paragraphs[0].text = "Appel CARSO-2026-77"
        document.save(str(chemin))

        resultat = chercher_dans_docx(chemin, "CARSO-2026-77")

        assert resultat["nb_resultats"] == 1
        hit = resultat["resultats"][0]
        assert hit["type_bloc"] == "entete"
        assert hit["section"] == 1
        assert hit["page"] is None  # hors flux : le repère est la section
        relu = lire_element(chemin, **hit["relire"]["arguments"])
        assert relu["type_element"] == "entete"
        assert "CARSO-2026-77" in relu["texte"]

    def test_insensible_a_la_casse(self, docx: Path) -> None:
        resultat = chercher_dans_docx(docx, "BUDGET")
        assert resultat["nb_resultats"] == 1

    def test_aucun_resultat_ne_tronque_rien(self, docx: Path) -> None:
        resultat = chercher_dans_docx(docx, "indemnite")

        assert resultat["nb_resultats"] == 0
        assert resultat["resultats"] == []
        assert resultat["tronque"] is False
        assert resultat["total"] == 4  # éléments du flux : 3 paragraphes + 1 tableau
        assert resultat["nb_paragraphes"] == 3
        assert resultat["nb_tableaux"] == 1

    def test_plafond_de_resultats_signale(self, tmp_path: Path) -> None:
        """Plafond atteint => ``tronque`` remonté (jamais de troncature silencieuse)."""
        paragraphes = [f"Ligne {index} avec le mot-cle obligatoire" for index in range(5)]
        chemin = write(tmp_path / "plafond.docx", build_docx(paragraphes=paragraphes))

        resultat = chercher_dans_docx(chemin, "mot-cle", max_resultats=2)

        assert resultat["nb_resultats"] == 2
        assert resultat["tronque"] is True

    def test_mot_cle_trop_court_refuse(self, docx: Path) -> None:
        with pytest.raises(ValidationError) as excinfo:
            chercher_dans_docx(docx, " a ")
        assert "Mot-clé trop court" in str(excinfo.value)


class TestRecherchePdf:
    def test_hits_portent_le_numero_de_page_reel(self, pdf: Path) -> None:
        """Sur trois pages, ``page`` est le numéro réel de chaque page PDF."""
        resultat = chercher_dans_pdf(pdf, "Pied de page")

        assert resultat["unite"] == "pages"
        assert resultat["total"] == 3
        assert [hit["page"] for hit in resultat["resultats"]] == [1, 2, 3]
        for hit in resultat["resultats"]:
            assert hit["type_bloc"] == "page"
            assert "Pied de page" in hit["content"]

    def test_page_ciblee_relisible(self, pdf: Path) -> None:
        resultat = chercher_dans_pdf(pdf, "Titre de page 3")
        assert [hit["page"] for hit in resultat["resultats"]] == [3]

        plage = lire_pdf_plage(pdf, from_page=3, to_page=3)
        assert plage["unite"] == "pages"
        assert "Titre de page 3" in plage["texte"]

    def test_pdf_sans_texte_renvoie_zero_resultat(self, tmp_path: Path) -> None:
        chemin = write(tmp_path / "scan.pdf", build_pdf_sans_texte())
        resultat = chercher_dans_pdf(chemin, "budget")

        assert resultat["nb_resultats"] == 0
        assert resultat["tronque"] is False

    def test_pdf_simple(self, tmp_path: Path) -> None:
        chemin = write(tmp_path / "une_page.pdf", build_pdf("MISSION CARSO 2026"))
        resultat = chercher_dans_pdf(chemin, "carso")

        assert resultat["nb_resultats"] == 1
        assert resultat["resultats"][0]["page"] == 1
        assert "carso" in resultat["resultats"][0]["content"].lower()


class TestRechercheXlsx:
    @pytest.fixture
    def classeur(self, tmp_path: Path) -> Path:
        return write(
            tmp_path / "beneficiaires.xlsx",
            build_xlsx(
                feuille="Bénéficiaires",
                lignes=[
                    ["Nom", "Fonction"],
                    ["Rakoto", "Formateur"],
                    ["Rasoa", "Assistant logistique"],
                ],
            ),
        )

    def test_hit_ligne_relisible(self, classeur: Path) -> None:
        resultat = chercher_dans_xlsx(classeur, "Rakoto")

        assert resultat["format"] == "xlsx"
        assert resultat["unite"] == "lignes"
        assert resultat["nb_resultats"] == 1
        hit = resultat["resultats"][0]
        assert hit["page"] == 2
        assert hit["feuille"] == "Bénéficiaires"
        assert "Rakoto" in hit["content"]

        plage = lire_xlsx_plage(classeur, from_row=hit["page"], to_row=hit["page"])
        assert plage["unite"] == "lignes"
        assert "Rakoto" in plage["texte"]

    def test_recherche_dans_le_tableau_du_classeur(self, classeur: Path) -> None:
        resultat = chercher_dans_xlsx(classeur, "Assistant logistique")
        assert resultat["nb_resultats"] == 1
        assert resultat["resultats"][0]["page"] == 3

    def test_feuille_inconnue_refusee(self, classeur: Path) -> None:
        with pytest.raises(ValidationError) as excinfo:
            chercher_dans_xlsx(classeur, "Rakoto", feuille="Budget")
        assert "Feuille inconnue" in str(excinfo.value)

    def test_plafond_serveur_tenu(self, tmp_path: Path) -> None:
        """``max_resultats`` est borné côté serveur (200 au plus), même demandé plus."""
        lignes: list[list[object]] = [["Nom"]] + [
            [f"Beneficiaire {index:03d} a confirmer"] for index in range(250)
        ]
        chemin = write(tmp_path / "gros.xlsx", build_xlsx(feuille="Liste", lignes=lignes))

        resultat = chercher_dans_xlsx(chemin, "Beneficiaire", max_resultats=999)

        assert resultat["nb_resultats"] == LIMITE_RESULTATS_RECHERCHE
        assert resultat["tronque"] is True


class TestPaginationHonneete:
    def test_docx_sans_pages_physiques(self, docx: Path) -> None:
        """Un DOCX n'a pas de pages : son unité de lecture est l'**élément**."""
        structure = inspecter_docx(docx)
        assert structure["nb_pages"] is None
        assert structure["unite_pagination"] == "elements"
        assert structure["nb_elements"] == 4
        assert structure["nb_paragraphes"] == 3
        assert structure["nb_tableaux"] == 1

    def test_pdf_nombre_de_pages_exact(self, pdf: Path) -> None:
        structure = inspecter_pdf(pdf)
        assert structure["nb_pages"] == 3
        assert structure["unite_pagination"] == "pages"

    def test_xlsx_sans_pages_physiques(self, tmp_path: Path) -> None:
        chemin = write(
            tmp_path / "liste.xlsx",
            build_xlsx(feuille="Liste", lignes=[["Nom"], ["Rakoto"]]),
        )
        structure = inspecter_xlsx(chemin)
        assert structure["nb_pages"] is None
        assert structure["unite_pagination"] == "lignes"
        assert structure["nb_lignes_total"] == 2
        assert structure["nb_feuilles"] == 1
