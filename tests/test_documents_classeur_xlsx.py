"""Classeur Excel professionnel (``format='xlsx'``) + visibilité « genere_pour ».

Trois points de contrôle :

1. **moteur** (``xlsx_renderer``) : chaque ``table.data`` devient une feuille
   stylée nommée d'après le titre qui la précède, les ``table.kpi`` une feuille
   « Chiffres clés », en-tête figé et filtre automatique ; sans tableau, refus
   explicite (jamais un .xlsx vide livré) ;
2. **rendu** (``rendre_professionnel(format='xlsx')``) : octets OOXML valides
   (+ PDF jumeau quand un navigateur est présent) et avertissement explicite si
   ``graphiques`` est passé (sans effet sur un classeur) ;
3. **visibilité** (incrément 74) : un document créé sous périmètre restreint
   (généré par l'agent, ou déposé) reste visible de **son** compte, et de lui
   seul — même sans ancre métier.
"""

from __future__ import annotations

import io
import zipfile
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import pytest
from openpyxl import load_workbook
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.application.dto import DocumentUploadInput
from app.application.perimetre import Perimetre
from app.application.services import DocumentService
from app.core.errors import NotFoundError, ValidationError
from app.documents.professionnel import rendre_professionnel
from app.documents.storage import LocalDocumentStorage
from app.domain.base import Base

HTML_TABLEAUX = """
<h1>Budget prévisionnel</h1>
<table class="data">
  <thead><tr><th>Poste</th><th class="num">Montant</th></tr></thead>
  <tbody>
    <tr><td>Formation</td><td class="num">4 800</td></tr>
    <tr><td>Logistique</td><td class="num">1 200</td></tr>
  </tbody>
</table>
<h2>Effectifs</h2>
<table class="data">
  <thead><tr><th>Session</th><th class="num">Inscrits</th></tr></thead>
  <tbody><tr><td>Lot 1</td><td class="num">12</td></tr></tbody>
</table>
<table class="kpi"><tr>
  <td><span class="kpi-value">+24 %</span><span class="kpi-label">Croissance</span></td>
</tr></table>
"""


# --- Moteur --------------------------------------------------------------------


def test_xlsx_une_feuille_par_tableau(tmp_path: Path) -> None:
    chemin = rendre_professionnel(HTML_TABLEAUX, "Budget", format="xlsx").octets
    classeur = load_workbook(io.BytesIO(chemin))

    assert classeur.sheetnames[:2] == ["Budget prévisionnel", "Effectifs"]
    assert classeur.sheetnames[-1] == "Chiffres clés"
    feuille = classeur["Budget prévisionnel"]
    assert feuille["A2"].value == "Poste"
    # Une colonne declaree numerique est ecrite comme un nombre : un texte
    # rendait toute formule (=SOMME(...)) egale a 0.
    assert feuille["B3"].value == 4800
    assert isinstance(feuille["B3"].value, (int, float))
    assert feuille.freeze_panes == "A3"
    assert feuille.auto_filter.ref is not None
    # En-tête stylé : fond primaire, blanc gras (thème corporate).
    assert feuille["A2"].fill.start_color.rgb == "001E3A5F"
    assert feuille["A2"].font.bold is True


def test_xlsx_ecrit_les_nombres_comme_des_nombres() -> None:
    """Un montant est un nombre, pas du texte : sinon les formules valent 0.

    Constat utilisateur (12/11) : dans un classeur généré, les cellules
    contenaient ``2 500 000`` en texte et ``=SOMME(...)`` ne renvoyait aucune
    valeur. Les colonnes numériques sont désormais converties, la formule est
    conservée telle quelle et le classeur demande un recalcul à l'ouverture.
    """
    html = """<h1>Budget</h1>
    <table class="data">
      <thead><tr><th>Poste</th><th class="num">Montant</th><th class="num">Part</th></tr></thead>
      <tbody>
        <tr><td>Formation</td><td class="num">2 500 000</td>
            <td class="num" data-format="pourcentage">0.25</td></tr>
        <tr><td>Logistique</td><td class="num">1 500 000</td>
            <td class="num" data-format="pourcentage">0.15</td></tr>
        <tr class="total"><td>Total</td>
            <td data-formule="=SUM(B3:B4)" data-format="devise:MGA">4 000 000</td>
            <td></td></tr>
      </tbody>
    </table>"""
    rendu = rendre_professionnel(html, "Budget", format="xlsx")
    classeur = load_workbook(io.BytesIO(rendu.octets))
    feuille = classeur["Budget"]

    assert feuille["B3"].value == 2_500_000
    assert isinstance(feuille["B3"].value, (int, float))
    assert abs(float(feuille["C3"].value) - 0.25) < 1e-9
    # La formule reste une formule ; sa valeur d'affichage ne l'écrase pas.
    assert feuille["B5"].value == "=SUM(B3:B4)"
    assert feuille["B5"].number_format.startswith("#,##0.00")
    # Sans résultat mémorisé, le tableur doit recalculer en ouvrant le fichier.
    assert classeur.calculation.fullCalcOnLoad is True


def test_xlsx_convertit_aussi_une_colonne_numerique_non_marquee() -> None:
    """Dans une colonne numérique déclarée, une cellule sans ``class`` est un nombre.

    L'inverse compte autant : un code d'une colonne texte (``0001``) reste du
    texte, il n'est pas transformé en 1.
    """
    html = """<h1>Suivi</h1>
    <table class="data">
      <thead><tr><th class="num">Inscrits</th><th>Code</th></tr></thead>
      <tbody><tr><td>1 200</td><td>0001</td></tr></tbody>
    </table>"""
    rendu = rendre_professionnel(html, "Suivi", format="xlsx")
    feuille = load_workbook(io.BytesIO(rendu.octets))["Suivi"]

    assert feuille["A3"].value == 1200
    assert feuille["B3"].value == "0001"


def test_xlsx_refuse_sans_tableau(tmp_path: Path) -> None:
    with pytest.raises(ValidationError) as excinfo:
        rendre_professionnel("<h1>Rapport</h1><p>Du texte, aucun tableau.</p>",
                             "Rapport", format="xlsx")
    assert "tableau" in str(excinfo.value.message).lower()


def test_xlsx_pose_un_graphique_natif_lie_a_ses_donnees(tmp_path: Path) -> None:
    """Un classeur reçoit un vrai graphique Excel, pas une image.

    Jusqu'au 11/11, le paramètre ``graphiques`` était **sans effet** sur un
    classeur (mesuré : 0 graphique sur 1 354 classeurs produits). Il devient un
    objet Excel natif avec sa feuille de données, donc modifiable et filtrable
    dans le tableur.
    """
    html = (
        HTML_TABLEAUX
        + '<figure data-graphique="repartition"></figure>'
    )
    rendu = rendre_professionnel(
        html,
        "Budget",
        format="xlsx",
        graphiques={
            "repartition": {
                "type": "bar",
                "labels": ["A", "B"],
                "series": {"Montant": [10, 20]},
                "title": "Répartition",
            }
        },
    )
    assert not any("sans effet" in a for a in rendu.avertissements)

    with zipfile.ZipFile(io.BytesIO(rendu.octets)) as archive:
        noms = archive.namelist()
        # La partie graphique native existe...
        graphiques_xml = [n for n in noms if n.startswith("xl/charts/chart")]
        assert graphiques_xml, noms
        # ...et aucune image n'est embarquée : ce n'est pas un PNG collé.
        assert not [n for n in noms if n.startswith("xl/media/")], noms

    from openpyxl import load_workbook

    classeur = load_workbook(io.BytesIO(rendu.octets))
    porteurs = [feuille for feuille in classeur.worksheets if feuille._charts]
    assert len(porteurs) == 1, classeur.sheetnames
    # Le graphique lit une feuille de données dédiée, il ne recopie pas les
    # chiffres dans son propre XML.
    donnees = [nom for nom in classeur.sheetnames if "données" in nom]
    assert len(donnees) == 1, classeur.sheetnames


def test_xlsx_signale_une_ancre_sans_definition() -> None:
    """Une ancre de graphique sans spec ne passe pas en silence.

    Le document montrerait un trou sans que l'agent puisse le savoir : le
    avertissement le lui dit, avec la cause exacte.
    """
    rendu = rendre_professionnel(
        HTML_TABLEAUX + '<figure data-graphique="oublie"></figure>',
        "Budget",
        format="xlsx",
    )
    assert any("sans définition" in a and "oublie" in a for a in rendu.avertissements)


def test_xlsx_ne_fabrique_pas_un_docx(tmp_path: Path) -> None:
    rendu = rendre_professionnel(HTML_TABLEAUX, "Budget", format="xlsx")
    assert rendu.format == "xlsx"
    assert rendu.octets[:2] == b"PK"

    with zipfile.ZipFile(io.BytesIO(rendu.octets)) as archive:
        assert "xl/workbook.xml" in archive.namelist()
        assert "word/document.xml" not in archive.namelist()


# --- Visibilité « genere_pour » (incrément 74) ---------------------------------


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as active:
        yield active
    engine.dispose()


@pytest.fixture
def storage(tmp_path: Path) -> LocalDocumentStorage:
    return LocalDocumentStorage(tmp_path / "carso", max_bytes=5 * 1024 * 1024)


def test_document_genere_visible_de_son_compte_seulement(
    session: Session, storage: LocalDocumentStorage
) -> None:
    """Le formateur retrouve ce qu'il fait générer par l'agent, sans ancre."""
    utilisateur = uuid4()
    restreint = DocumentService(
        session,
        storage=storage,
        actor_id="agent_assistant_formateur",
        perimetre=Perimetre(role="formateur", utilisateur_id=utilisateur, est_complet=False),
    )
    document = restreint.enregistrer_document(
        DocumentUploadInput(
            type_document="rapport",
            nom="note_agent.txt",
            stream=io.BytesIO("Note générée par l'agent.".encode("utf-8")),
            created_by="agent_assistant_formateur",
        )
    )

    # La trace du demandeur est écrite, et lui seul y a accès.
    assert document.doc_metadata is not None
    assert document.doc_metadata["genere_pour"] == str(utilisateur)
    assert restreint.obtenir(document.id).id == document.id
    # Document personnel privé (décision du 18/11) : il se lit dans « Mon
    # dossier », plus dans la liste Documents où il tombait sous « Chat ».
    assert [d.id for d in restreint.lister_personnels()] == [document.id]
    assert restreint.lister_tous() == []

    autre = DocumentService(
        session,
        storage=storage,
        actor_id="observateur",
        perimetre=Perimetre(role="formateur", utilisateur_id=uuid4(), est_complet=False),
    )
    with pytest.raises(NotFoundError):
        autre.obtenir(document.id)
    assert autre.lister_tous() == []
