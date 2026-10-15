"""Lot T1 — inspection, clonage, template, scripts, skills."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.agents.skills import lister_skills, racine_skills, resoudre_skills
from app.core.errors import PermissionDeniedError, ValidationError
from app.documents.cloning import cloner_docx, remplir_template_docx
from app.documents.generation import build_docx, build_pdf, build_xlsx
from app.documents.inspection import inspecter_docx, inspecter_pdf, inspecter_xlsx, lire_pdf_plage
from app.tools.scripts import verifier_imports


def test_inspecter_docx_styles(tmp_path: Path) -> None:
    octets = build_docx(titre="Offre", paragraphes=["Un", "Deux"])
    chemin = tmp_path / "offre.docx"
    chemin.write_bytes(octets)
    structure = inspecter_docx(chemin)
    assert structure["format"] == "docx"
    assert structure["nb_paragraphes"] >= 2
    assert any(s["nom"] for s in structure["styles"])


def test_inspecter_pdf_pages(tmp_path: Path) -> None:
    octets = build_pdf(titre="Rapport", paragraphes=["Page unique"])
    chemin = tmp_path / "rapport.pdf"
    chemin.write_bytes(octets)
    structure = inspecter_pdf(chemin)
    assert structure["nb_pages"] >= 1
    plage = lire_pdf_plage(chemin, from_page=1, to_page=1)
    assert "Rapport" in plage["texte"] or plage["texte"] != ""


def test_inspecter_xlsx_feuilles(tmp_path: Path) -> None:
    octets = build_xlsx(titre_feuille="Benef", lignes=[["nom", "prenom"], ["Tazi", "Sara"]])
    chemin = tmp_path / "liste.xlsx"
    chemin.write_bytes(octets)
    structure = inspecter_xlsx(chemin)
    assert structure["nb_feuilles"] == 1
    assert structure["feuilles"][0]["nom"] == "Benef"


def test_xlsm_refuse(tmp_path: Path) -> None:
    chemin = tmp_path / "macro.xlsm"
    chemin.write_bytes(b"PK")
    with pytest.raises(ValidationError):
        inspecter_xlsx(chemin)


def test_cloner_docx_et_template(tmp_path: Path) -> None:
    ref = tmp_path / "ref.docx"
    ref.write_bytes(build_docx(titre="Modele", paragraphes=["Hello"]))
    clone = cloner_docx(ref, titre="Clone", paragraphes=["Nouveau"])
    assert clone.startswith(b"PK")

    modele = tmp_path / "tpl.docx"
    modele.write_bytes(build_docx(titre="T", paragraphes=["Bonjour {{prenom}}"]))
    rempli = remplir_template_docx(modele, {"prenom": "Sara"})
    # Le DOCX rempli reste un ZIP OOXML.
    assert rempli.startswith(b"PK")


def test_script_imports_deny() -> None:
    verifier_imports("import json\nprint(1)")
    with pytest.raises(PermissionDeniedError):
        verifier_imports("import os\nos.system('dir')")
    with pytest.raises(PermissionDeniedError):
        verifier_imports("import subprocess")
    with pytest.raises(ValidationError):
        verifier_imports("def (")


def test_skills_resolus() -> None:
    noms = lister_skills()
    assert "docx-officiels" in noms
    assert "scripts-python" in noms
    chemins = resoudre_skills(("docx-officiels", "pdf-officiels"))
    assert len(chemins) == 2
    assert all(Path(c).joinpath("SKILL.md").is_file() for c in chemins)
    with pytest.raises(ValidationError):
        resoudre_skills(("inexistant",))
    with pytest.raises(ValidationError):
        resoudre_skills(("../etc",))
    assert racine_skills().name == "agents"
