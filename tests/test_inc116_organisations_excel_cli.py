"""Tests INC-116 — modèles d'organisation, Excel professionnel, CLI par compte.

Trois volets, testés au niveau où ils agissent :

1. le **service** accepte les types d'actifs d'une organisation et refuse un
   type inventé ; les tools de l'agent listent et lisent un modèle ;
2. le **fichier produit** est relu avec ``openpyxl`` : formule, format de
   nombre, gras de total, fusion, largeur — et une valeur non convertible reste
   du texte avec un avertissement ;
3. la **garde d'exécution** : écriture dans l'espace acceptée, écriture hors
   espace refusée, absence de compte refusée, imports dangereux toujours
   interdits.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.sql.ddl import CreateColumn

from app.agents.agent_personnel import DEFINITION as PERSONNEL_DEF
from app.agents.contexte import (
    UserContext,
    poser_contexte_utilisateur,
    retablir_contexte_utilisateur,
)
from app.agents.generaliste_readonly import DEFINITION as GENERALISTE_DEF
from app.agents.harness import build_system_prompt
from app.agents.tools.agent_generateur_offre_tools import (
    _lire_modele_organisation,
    _lister_modeles_organisation,
)
from app.application.services import ModeleDocumentService
from app.core.errors import NotFoundError, PermissionDeniedError, ValidationError
from app.documents.professionnel.xlsx_renderer import rendre_xlsx
from app.domain.base import Base
from app.domain.document import Document
from app.domain.enums import StatutDocument, TypeDocument
from app.domain.organization import Organisation
from app.tools.permissions import AgentIdentity, PermissionPolicy
from app.tools.scripts import build_run_python_script, verifier_imports
from app.tools.workspace import espace_utilisateur, nom_espace


@compiles(JSONB, "sqlite")
def _jsonb_as_json(type_: JSONB, compiler: object, **kw: object) -> str:
    return "JSON"


@compiles(CreateColumn, "sqlite")
def _strip_pg_defaults(element: CreateColumn, compiler: object, **kw: object) -> str:
    import re

    ddl = compiler.visit_create_column(element)  # type: ignore[arg-type]
    return re.sub(r"DEFAULT gen_random_uuid\(\)", "", ddl)


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as s:
        yield s
    engine.dispose()


# ---------------------------------------------------------------------------
# 1. Modèles d'organisation
# ---------------------------------------------------------------------------


def _document_template(session: Session, nom: str = "Modele offre.docx") -> Document:
    document = Document(
        nom=nom,
        type_document=TypeDocument.TEMPLATE.value,
        statut=StatutDocument.APPROVED.value,
        storage_path=f"templates/{uuid4().hex}/{nom}",
        mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        taille_octets=2048,
    )
    session.add(document)
    session.flush()
    return document


def test_types_actifs_organisation_acceptes_et_type_invente_refuse(session: Session) -> None:
    """Un modèle d'offre, un logo ou des instructions sont des types valides."""
    organisation = Organisation(nom="Bailleur X")
    session.add(organisation)
    session.flush()
    service = ModeleDocumentService(session, actor_id="test")

    for type_modele in (
        "modele_offre_technique",
        "instructions_redaction",
        "logo",
    ):
        document = _document_template(session, f"{type_modele}.pdf")
        modele = service.enregistrer_modele(
            organisation.id,
            nom=f"{type_modele} v1",
            type_document=type_modele,
            document_template_id=document.id,
        )
        assert modele.type_document == type_modele

    with pytest.raises(ValidationError, match="Type de document inconnu"):
        service.enregistrer_modele(
            organisation.id,
            nom="Type inventé",
            type_document="modele_offre_magique",
            document_template_id=_document_template(session).id,
        )


def test_versionnement_et_resolution_du_modele_actif(session: Session) -> None:
    """Enregistrer un nouveau fichier crée une version, l'ancienne reste lisible."""
    organisation = Organisation(nom="Bailleur Y")
    session.add(organisation)
    session.flush()
    service = ModeleDocumentService(session, actor_id="test")

    premier = service.enregistrer_modele(
        organisation.id,
        nom="Instructions",
        type_document="instructions_redaction",
        document_template_id=_document_template(session, "instructions_v1.pdf").id,
    )
    second = service.enregistrer_modele(
        organisation.id,
        nom="Instructions",
        type_document="instructions_redaction",
        document_template_id=_document_template(session, "instructions_v2.pdf").id,
    )

    assert (premier.version, second.version) == (1, 2)
    actif = service.resoudre_modele_actif(organisation.id, "instructions_redaction")
    assert actif.id == second.id
    assert len(service.lister(organisation.id)) == 2


def test_outils_agent_listent_et_lisent_les_modeles(session: Session) -> None:
    """L'agent découvre les modèles d'une organisation (aucun type supposé)."""
    organisation = Organisation(nom="Bailleur Z")
    session.add(organisation)
    session.flush()
    document = _document_template(session, "modele_offre_technique.docx")
    ModeleDocumentService(session, actor_id="test").enregistrer_modele(
        organisation.id,
        nom="Modèle technique",
        type_document="modele_offre_technique",
        document_template_id=document.id,
    )

    listing = _lister_modeles_organisation(
        session, {"organisation_id": str(organisation.id)}
    )
    assert listing["nb_modeles"] == 1
    modele = listing["modeles"][0]
    assert modele["type_document"] == "modele_offre_technique"
    assert modele["document_id"] == str(document.id)
    assert "storage_path" not in modele  # jamais un chemin disque

    filtre = _lister_modeles_organisation(
        session,
        {"organisation_id": str(organisation.id), "type_document": "logo"},
    )
    assert filtre["nb_modeles"] == 0
    assert "Aucun modèle" in filtre["message"]

    # Un fichier disparu est signalé, jamais remplacé par un contenu inventé.
    document_id = modele["document_id"]
    session.query(Document).filter(Document.id == UUID(document_id)).delete()
    session.flush()
    session.expunge_all()
    with pytest.raises(NotFoundError):
        _lire_modele_organisation(session, {"modele_id": modele["id"]})


# ---------------------------------------------------------------------------
# 2. Excel : formules, formats, styles
# ---------------------------------------------------------------------------

_CONTENU = """
<h2>Budget prévisionnel</h2>
<table class="data">
  <thead>
    <tr><th>Poste</th><th class="num">Montant</th><th class="num">Part</th></tr>
  </thead>
  <tbody>
    <tr>
      <td data-largeur="28">Formation</td>
      <td class="num" data-format="devise:MGA">2 500 000</td>
      <td class="num" data-format="pourcentage">0,625</td>
    </tr>
    <tr>
      <td>Logistique</td>
      <td class="num" data-format="devise:MGA">1 500 000</td>
      <td class="num" data-format="pourcentage">0,375</td>
    </tr>
    <tr class="total">
      <td>Total</td>
      <td class="num" data-formule="=SUM(B3:B4)" data-format="devise:MGA">4 000 000</td>
      <td class="num" data-format="pourcentage">1</td>
    </tr>
  </tbody>
</table>
"""


def test_classeur_formules_formats_et_styles(tmp_path: Path) -> None:
    openpyxl = pytest.importorskip("openpyxl")
    chemin = tmp_path / "budget.xlsx"
    avertissements: list[str] = []

    rendre_xlsx(
        _CONTENU,
        chemin,
        titre="Budget",
        theme="corporate",
        avertissements=avertissements,
    )

    classeur = openpyxl.load_workbook(chemin)
    feuille = classeur["Budget prévisionnel"]
    assert feuille["B5"].value == "=SUM(B3:B4)"  # formule vivante
    assert feuille["B3"].value == 2_500_000
    assert feuille["B3"].number_format == '#,##0.00 "MGA"'
    assert feuille["C3"].value == pytest.approx(0.625)
    assert feuille["C3"].number_format == "0.00%"
    assert feuille["A5"].font.bold is True  # ligne de total
    assert feuille["B5"].fill.fgColor.rgb.endswith("F3F4F6")  # fond « doux » du thème
    assert feuille.freeze_panes == "A3"
    assert feuille.column_dimensions["A"].width == 28  # data-largeur
    assert feuille.print_title_rows == "$2:$2"
    assert avertissements == []


def test_valeur_non_convertible_reste_texte_et_avertit(tmp_path: Path) -> None:
    openpyxl = pytest.importorskip("openpyxl")
    contenu = """
    <table class="data">
      <thead><tr><th>Poste</th><th class="num">Montant</th></tr></thead>
      <tbody><tr><td>À chiffrer</td>
        <td data-format="decimal">n/a</td></tr></tbody>
    </table>
    """
    avertissements: list[str] = []
    chemin = tmp_path / "inconnu.xlsx"
    rendre_xlsx(contenu, chemin, titre="Budget", avertissements=avertissements)

    feuille = openpyxl.load_workbook(chemin).active
    assert feuille["B3"].value == "n/a"
    assert any("n'est pas un nombre" in constat for constat in avertissements)


def test_formule_externe_refusee(tmp_path: Path) -> None:
    """Une formule d'exfiltration n'est jamais écrite dans le classeur."""
    openpyxl = pytest.importorskip("openpyxl")
    contenu = """
    <table class="data">
      <thead><tr><th>Poste</th><th class="num">Valeur</th></tr></thead>
      <tbody><tr><td>X</td>
        <td data-formule="=WEBSERVICE(&quot;https://exemple.test&quot;)">1</td></tr></tbody>
    </table>
    """
    avertissements: list[str] = []
    chemin = tmp_path / "refus.xlsx"
    rendre_xlsx(contenu, chemin, titre="Refus", avertissements=avertissements)

    feuille = openpyxl.load_workbook(chemin).active
    assert feuille["B3"].value != "=WEBSERVICE(\"https://exemple.test\")"
    assert any("refusée" in constat for constat in avertissements)


def test_fusion_colspan_reportee(tmp_path: Path) -> None:
    openpyxl = pytest.importorskip("openpyxl")
    contenu = """
    <table class="data">
      <thead><tr><th colspan="2">Synthèse</th></tr></thead>
      <tbody><tr><td>A</td><td>B</td></tr></tbody>
    </table>
    """
    chemin = tmp_path / "fusion.xlsx"
    rendre_xlsx(contenu, chemin, titre="Fusion")
    feuille = openpyxl.load_workbook(chemin).active
    assert "A2:B2" in {str(plage) for plage in feuille.merged_cells.ranges}


# ---------------------------------------------------------------------------
# 3. Espace de travail et garde d'exécution
# ---------------------------------------------------------------------------

_IDENTITE = AgentIdentity(agent_id="agent_personnel")
_POLICY = PermissionPolicy(allowed_capabilities=frozenset({"script_python"}))


def _settings(tmp_path: Path) -> SimpleNamespace:
    return SimpleNamespace(
        storage_root=str(tmp_path / "storage"),
        workdirs_root=str(tmp_path / "workdirs"),
        scripts_root="",
        scripts_timeout_seconds=120,
        scripts_max_output_bytes=80_000,
        scripts_max_source_chars=60_000,
        scripts_confiance="eleve",
    )


def _outil(settings: SimpleNamespace):
    return build_run_python_script(
        identite=_IDENTITE, policy=_POLICY, settings=settings
    )


def test_espace_par_compte_et_identifiant_invalide(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    compte = uuid4()

    espace = espace_utilisateur(settings, compte)
    assert espace == (tmp_path / "workdirs" / f"user-{compte}").resolve()
    assert espace.is_dir()
    assert nom_espace(str(compte)) == f"user-{compte}"

    with pytest.raises(ValidationError):
        espace_utilisateur(settings, "../../etc")
    with pytest.raises(ValidationError):
        nom_espace("")


def test_script_ecrit_dans_son_espace_et_le_fichier_persiste(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    compte = uuid4()
    jeton = poser_contexte_utilisateur(UserContext(utilisateur_id=str(compte)))
    try:
        sortie = _outil(settings).handler(
            {
                "source": (
                    "from pathlib import Path\n"
                    "Path('budget.csv').write_text('poste,montant\\nA,10\\n',"
                    " encoding='utf-8')\n"
                    "print('ecrit')\n"
                ),
                "nom": "budget",
            }
        )
    finally:
        retablir_contexte_utilisateur(jeton)

    assert sortie["code_retour"] == 0, sortie["stderr"]
    assert "ecrit" in sortie["stdout"]
    assert sortie["espace"] == f"user-{compte}"
    assert sortie["script"].startswith(".scripts/budget_")
    assert {f["chemin"] for f in sortie["fichiers"]} == {"budget.csv"}
    assert (tmp_path / "workdirs" / f"user-{compte}" / "budget.csv").is_file()


def test_script_ne_peut_pas_ecrire_hors_de_son_espace(tmp_path: Path) -> None:
    """La garde d'audit refuse une écriture hors espace (fichier sensible)."""
    settings = _settings(tmp_path)
    compte = uuid4()
    interdit = tmp_path / "storage" / "secrets.txt"
    interdit.parent.mkdir(parents=True, exist_ok=True)
    jeton = poser_contexte_utilisateur(UserContext(utilisateur_id=str(compte)))
    try:
        sortie = _outil(settings).handler(
            {
                "source": (
                    "from pathlib import Path\n"
                    f"Path(r'{interdit}').write_text('pirate', encoding='utf-8')\n"
                ),
                "nom": "evasion",
            }
        )
    finally:
        retablir_contexte_utilisateur(jeton)

    assert sortie["code_retour"] != 0
    assert "espace de travail" in sortie["stderr"].lower()
    assert not interdit.exists()


def test_script_refuse_sans_compte_identifie(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    with pytest.raises(PermissionDeniedError, match="compte identifié"):
        _outil(settings).handler({"source": "print('bonjour')"})


def test_imports_dangereux_toujours_refuses() -> None:
    for source in ("import os\n", "import subprocess\n", "import socket\n"):
        with pytest.raises(PermissionDeniedError):
            verifier_imports(source)
    # Un module de réseau est refusé dès la source, avec un motif lisible.
    with pytest.raises(PermissionDeniedError, match="reseau"):
        verifier_imports("import requests\n")


def test_script_peut_importer_un_module_installe(tmp_path: Path) -> None:
    """Régression : tout import de paquet échouait sur Windows.

    Le lanceur ne pouvait lire que ``sys.prefix`` ; l'import commence par lire
    ``Lib/pkgutil.py`` de la distribution système : « Accès refusé hors de
    l'espace de travail » pour ``pypdf`` comme pour ``csv``.
    """
    settings = _settings(tmp_path)
    compte = uuid4()
    jeton = poser_contexte_utilisateur(UserContext(utilisateur_id=str(compte)))
    try:
        sortie = _outil(settings).handler(
            {
                "source": (
                    "import pypdf\n"
                    "import traceback\n"
                    "print('modules ok')\n"
                ),
                "nom": "modules",
            }
        )
    finally:
        retablir_contexte_utilisateur(jeton)

    assert sortie["code_retour"] == 0, sortie["stderr"]
    assert "modules ok" in sortie["stdout"]


def test_script_ne_peut_pas_lire_un_fichier_de_secrets(tmp_path: Path) -> None:
    """La lecture des paquets ne doit pas ouvrir le dossier du projet."""
    settings = _settings(tmp_path)
    compte = uuid4()
    secrets = tmp_path / ".env"
    secrets.write_text("CLE=secret\n", encoding="utf-8")
    jeton = poser_contexte_utilisateur(UserContext(utilisateur_id=str(compte)))
    try:
        # Le refus tombe avant toute execution : motif « fichier de secrets »
        # sur la source, donc rien n'est ecrit dans l'espace du compte.
        with pytest.raises(PermissionDeniedError, match="secrets"):
            _outil(settings).handler(
                {
                    "source": (
                        "from pathlib import Path\n"
                        f"print(Path(r'{secrets}').read_text(encoding='utf-8'))\n"
                    ),
                    "nom": "secrets",
                }
            )
    finally:
        retablir_contexte_utilisateur(jeton)


# ---------------------------------------------------------------------------
# 4. Contrats d'agents et prompt
# ---------------------------------------------------------------------------


def test_cinq_agents_portent_le_tool_et_la_capacite() -> None:
    from app.agents.agent_rh import DEFINITION as RH_DEF
    from app.agents.agent_statistique import DEFINITION as STAT_DEF
    from app.agents.assistant_formateur import DEFINITION as ASSISTANT_DEF
    from app.agents.generateur_offre import DEFINITION as GENERATEUR_DEF

    for definition in (PERSONNEL_DEF, RH_DEF, STAT_DEF, ASSISTANT_DEF, GENERATEUR_DEF):
        assert "run_python_script" in definition.tools, definition.agent_id
        assert "script_python" in definition.capabilities, definition.agent_id
    # Le généraliste n'a pas été demandé : sa surface ne change pas.
    assert "run_python_script" not in GENERALISTE_DEF.tools


def test_bloc_espace_de_travail_injecte_uniquement_aux_agents_concernes() -> None:
    avec = build_system_prompt(PERSONNEL_DEF)
    sans = build_system_prompt(GENERALISTE_DEF)
    assert "Espace de travail et scripts" in avec
    assert "Espace de travail et scripts" not in sans
