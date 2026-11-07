"""Classeur Excel **sans** openpyxl (garde d'exécution des scripts).

Pourquoi ces tests : ``openpyxl`` est bloqué dans l'espace de travail de
l'agent, et son installation au démarrage d'un script n'est pas une option
acceptable sur le serveur. Un classeur d'une table n'en a pourtant pas besoin.

Trois points de contrôle :

1. ``app.documents.xlsx_tables`` écrit un vrai ``.xlsx`` (relu par openpyxl) :
   valeurs, en-tête gras, bandes, filets, figeage et filtre ;
2. ``build_xlsx`` bascule sur ce chemin quand openpyxl est indisponible — même
   contenu, jamais un échec ni un fichier vide ;
3. l'aide copiée dans l'espace de travail de l'agent produit le même fichier,
   sans importer de paquet tiers.
"""

from __future__ import annotations

import io
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from openpyxl import load_workbook

from app.agents.contexte import (
    UserContext,
    poser_contexte_utilisateur,
    retablir_contexte_utilisateur,
)
from app.core.errors import GenerationError, PermissionDeniedError
from app.documents.generation import build_xlsx
from app.documents.styles import CARSO_DEFAUT
from app.documents.xlsx_tables import classeur_depuis_table
from app.tools.permissions import AgentIdentity, PermissionPolicy
from app.tools.scripts import build_run_python_script

LIGNES = [
    ["Bénéficiaire", "Village", "Présences"],
    ["Rakoto", "Toliara I", "3"],
    ["Sarra", "Toliara II", "4"],
    ["Nguyen", "Ampanihy", "2"],
]


def _feuille(octets: bytes):
    return load_workbook(io.BytesIO(octets)).active


# ---------------------------------------------------------------------------
# 1. Le fichier produit
# ---------------------------------------------------------------------------


def test_classeur_relu_par_openpyxl() -> None:
    feuille = _feuille(
        classeur_depuis_table(titre_feuille="Présences", lignes=LIGNES, style=CARSO_DEFAUT)
    )

    assert feuille.title == "Présences"
    assert [list(ligne) for ligne in feuille.iter_rows(values_only=True)] == LIGNES
    assert feuille["A1"].font.bold is True
    assert feuille["A1"].fill.fgColor.rgb == "FF" + CARSO_DEFAUT.tableau.entete_fond.lstrip("#")
    assert feuille["A2"].fill.fgColor.rgb == "FF" + CARSO_DEFAUT.tableau.bande_fond.lstrip("#")
    assert feuille["A3"].fill.fgColor.rgb in ("00000000", None) or (
        feuille["A3"].fill.patternType in (None, "none")
    )
    assert feuille.freeze_panes == "A2"
    assert feuille.auto_filter.ref == "A1:C4"


def test_classeur_ne_convertit_ni_ninvente() -> None:
    """Une valeur fournie reste du texte ; une cellule vide reste vide."""
    feuille = _feuille(
        classeur_depuis_table(
            titre_feuille="Budget",
            lignes=[["Poste", "Montant"], ["Formation", "1 250,50"], ["", None]],
            style=CARSO_DEFAUT,
        )
    )
    valeurs = [list(ligne) for ligne in feuille.iter_rows(values_only=True)]
    assert valeurs[1] == ["Formation", "1 250,50"]
    assert valeurs[2] == [None, None]


def test_nom_onglet_invalide_est_nettoye() -> None:
    feuille = _feuille(
        classeur_depuis_table(titre_feuille="Liste [2026]:/*?", lignes=LIGNES, style=CARSO_DEFAUT)
    )
    assert "[" not in feuille.title and ":" not in feuille.title
    assert len(feuille.title) <= 31


# ---------------------------------------------------------------------------
# 2. Repli de build_xlsx
# ---------------------------------------------------------------------------


def test_build_xlsx_bascule_sans_openpyxl(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.documents.renderers import xlsx as renderer

    def _indisponible():
        raise GenerationError("openpyxl indisponible (simulation)")

    monkeypatch.setattr(renderer, "_libs", _indisponible)

    feuille = _feuille(build_xlsx(titre_feuille="Liste", lignes=LIGNES))
    assert [list(ligne) for ligne in feuille.iter_rows(values_only=True)] == LIGNES


# ---------------------------------------------------------------------------
# 3. L'aide de l'espace de travail de l'agent
# ---------------------------------------------------------------------------


def test_script_ecrit_un_classeur_sans_openpyxl(tmp_path: Path) -> None:
    settings = SimpleNamespace(
        storage_root=str(tmp_path / "storage"),
        workdirs_root=str(tmp_path / "workdirs"),
        scripts_root="",
        scripts_timeout_seconds=120,
        scripts_max_output_bytes=80_000,
        scripts_max_source_chars=60_000,
        scripts_confiance="eleve",
    )
    outil = build_run_python_script(
        identite=AgentIdentity(agent_id="agent_personnel"),
        policy=PermissionPolicy(allowed_capabilities=frozenset({"script_python"})),
        settings=settings,
    )
    compte = uuid4()
    jeton = poser_contexte_utilisateur(UserContext(utilisateur_id=str(compte)))
    try:
        sortie = outil.handler(
            {
                "source": (
                    "from classeur import ecrire_classeur\n"
                    "ecrire_classeur('presences.xlsx', 'Presences', [\n"
                    "    ['Beneficiaire', 'Village'],\n"
                    "    ['Rakoto', 'Toliara I'],\n"
                    "])\n"
                    "print('classeur ecrit')\n"
                ),
                "nom": "classeur",
            }
        )
    finally:
        retablir_contexte_utilisateur(jeton)

    assert sortie["code_retour"] == 0, sortie["stderr"]
    assert "classeur ecrit" in sortie["stdout"]
    # Les aides ne sont pas du travail du compte : elles n'apparaissent pas.
    assert [f["chemin"] for f in sortie["fichiers"]] == ["presences.xlsx"]

    fichier = tmp_path / "workdirs" / f"user-{compte}" / "presences.xlsx"
    feuille = load_workbook(fichier).active
    assert [list(ligne) for ligne in feuille.iter_rows(values_only=True)] == [
        ["Beneficiaire", "Village"],
        ["Rakoto", "Toliara I"],
    ]


def test_script_sans_module_supplementaire_reste_bloque(tmp_path: Path) -> None:
    """Les aides ne doivent pas ouvrir la boîte de Pandore des paquets natifs."""
    settings = SimpleNamespace(
        storage_root=str(tmp_path / "storage"),
        workdirs_root=str(tmp_path / "workdirs"),
        scripts_root="",
        scripts_timeout_seconds=60,
        scripts_max_output_bytes=80_000,
        scripts_max_source_chars=60_000,
        scripts_confiance="eleve",
    )
    outil = build_run_python_script(
        identite=AgentIdentity(agent_id="agent_personnel"),
        policy=PermissionPolicy(allowed_capabilities=frozenset({"script_python"})),
        settings=settings,
    )
    compte = uuid4()
    jeton = poser_contexte_utilisateur(UserContext(utilisateur_id=str(compte)))
    try:
        # ``ctypes`` est refuse des la source ; la garde d'execution reste la
        # barriere de fond (meme importe par une bibliotheque).
        with pytest.raises(PermissionDeniedError, match="ctypes"):
            outil.handler({"source": "import ctypes\nprint('dll')\n", "nom": "ctypes"})
    finally:
        retablir_contexte_utilisateur(jeton)
