"""Tests INC-118 — livrables de l'agent personnel et scripts d'utilitaire.

Deux constats du compte, testés au niveau où ils se produisent :

1. **l'outil n'existait pas pour l'agent personnel** : ``generer_document_
   professionnel`` n'était déclaré à aucun contrat de cet agent. On vérifie le
   **contrat** (outil + capacité), le **montage réel** (``deep_tools`` le
   contient, et le service refuse une approbation), et l'absence d'effet de
   bord sur les deux autres agents lecture seule ;
2. **les scripts étaient trop courts** : 30 s et 20 000 caractères interdisaient
   un utilitaire. On vérifie les nouveaux budgets, le niveau de confiance
   (``standard`` / ``eleve``) et le **refus explicite** d'un script qui arrête
   ou redémarre le serveur — un refus de code, pas seulement un prompt.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.sql.ddl import CreateColumn

from app.agents.agent_personnel import DEFINITION as PERSONNEL_DEF
from app.agents.agent_personnel import (
    NOMS_TOOLS_LECTURE_SEULE as _NOMS_LECTURE_SEULE_PERSONNEL,
)
from app.agents.agent_personnel import (
    TOOLS_DOCUMENTS_PROPOSE,
    build_agent_personnel,
)
from app.agents.contexte import (
    UserContext,
    poser_contexte_utilisateur,
    retablir_contexte_utilisateur,
)
from app.agents.generaliste_readonly import DEFINITION as GENERALISTE_DEF
from app.agents.harness import build_system_prompt
from app.core.config import Settings
from app.core.errors import PermissionDeniedError
from app.domain.base import Base
from app.tools.permissions import AgentIdentity, PermissionPolicy
from app.tools.scripts import build_run_python_script, verifier_script_sur


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
# 1. Livrables de l'agent personnel
# ---------------------------------------------------------------------------


def test_contrat_agent_personnel_declare_les_livrables() -> None:
    """Le contrat porte la génération et la correction, en proposition."""
    for nom in TOOLS_DOCUMENTS_PROPOSE:
        assert nom in PERSONNEL_DEF.tools, nom
    assert "document_propose" in PERSONNEL_DEF.policy.allowed_capabilities
    # Aucune capacité d'approbation : un livrable reste proposé.
    assert not any(
        capacite.startswith("document_approve")
        for capacite in PERSONNEL_DEF.policy.allowed_capabilities
    )


def test_les_autres_agents_lecture_seule_ne_gagnent_rien() -> None:
    """Élargir le contrat personnel ne doit rien ajouter aux autres contrats.

    Cause de la panne corrigée : ``generer_document_professionnel`` existait
    dans la liste partagée ``NOMS_TOOLS`` (donc au généraliste, au RH, à
    l'assistant et au générateur), mais **pas** au contrat de l'agent
    personnel. La correction nomme les livrables au contrat personnel et
    n'ajoute ``corriger_document`` nulle part ailleurs.
    """
    from app.agents.agent_rh import DEFINITION as RH_DEF
    from app.agents.agent_statistique import DEFINITION as STAT_DEF

    assert "corriger_document" in PERSONNEL_DEF.tools
    for definition in (GENERALISTE_DEF, STAT_DEF, RH_DEF):
        assert "corriger_document" not in definition.tools, definition.agent_id
    # La liste partagée est conservée telle quelle pour les autres agents.
    from app.agents.tools.agent_generaliste_tools import NOMS_TOOLS

    assert "generer_document_professionnel" in NOMS_TOOLS
    assert all(nom in PERSONNEL_DEF.tools for nom in _NOMS_LECTURE_SEULE_PERSONNEL)


def test_deep_tools_de_l_agent_personnel_portent_la_generation(session: Session) -> None:
    """Le montage réel contient l'outil : c'est ce que le modèle voit."""
    agent = build_agent_personnel(session, session_factory=lambda: session)
    noms = {outil.name for outil in agent.deep_tools()}
    assert "generer_document_professionnel" in noms
    assert "corriger_document" in noms
    assert "lire_document" in noms
    # Pas de doublon de nom : deux outils homonymes seraient indiscernables.
    tous = [outil.name for outil in agent.deep_tools()]
    assert len(tous) == len(set(tous))


def test_prompt_personnel_annonce_les_livrables_et_le_refus_d_exploitation() -> None:
    prompt = build_system_prompt(PERSONNEL_DEF)
    assert "corriger_document" in prompt
    assert "Exploitation de l'application" in prompt


# ---------------------------------------------------------------------------
# 2. Scripts : budgets, confiance, refus d'exploitation
# ---------------------------------------------------------------------------

_IDENTITE = AgentIdentity(agent_id="agent_personnel")
_POLICY = PermissionPolicy(allowed_capabilities=frozenset({"script_python"}))


def _settings(tmp_path: Path, confiance: str = "eleve") -> SimpleNamespace:
    return SimpleNamespace(
        storage_root=str(tmp_path / "storage"),
        workdirs_root=str(tmp_path / "workdirs"),
        scripts_root="",
        scripts_timeout_seconds=120,
        scripts_max_output_bytes=80_000,
        scripts_max_source_chars=60_000,
        scripts_confiance=confiance,
    )


def _executer(tmp_path: Path, source: str, *, confiance: str = "eleve"):
    settings = _settings(tmp_path, confiance)
    compte = uuid4()
    jeton = poser_contexte_utilisateur(UserContext(utilisateur_id=str(compte)))
    try:
        outil = build_run_python_script(
            identite=_IDENTITE, policy=_POLICY, settings=settings
        )
        return outil.handler({"source": source, "nom": "utilitaire"})
    finally:
        retablir_contexte_utilisateur(jeton)


def test_defauts_de_configuration_pour_un_script_long() -> None:
    """Un utilitaire tient dans les budgets par défaut du produit."""
    from app.core.config import Settings as ConfigSettings

    champs = ConfigSettings.model_fields
    assert champs["scripts_timeout_seconds"].default >= 120
    assert champs["scripts_max_source_chars"].default >= 60_000
    assert champs["scripts_confiance"].default == "eleve"


def test_script_long_avec_fonctions_et_sous_dossiers(tmp_path: Path) -> None:
    """Le cas demandé : un vrai utilitaire, pas une ligne de calcul."""
    source = (
        "from pathlib import Path\n"
        "def consolider(dossier: Path) -> int:\n"
        "    dossier.mkdir(parents=True, exist_ok=True)\n"
        "    total = 0\n"
        "    for i in range(50):\n"
        "        fichier = dossier / f'ligne_{i}.txt'\n"
        "        fichier.write_text(str(i), encoding='utf-8')\n"
        "        total += i\n"
        "    (dossier / 'total.txt').write_text(str(total), encoding='utf-8')\n"
        "    return total\n"
        "print(consolider(Path('travail') / 'sous' / 'dossier'))\n"
    )
    sortie = _executer(tmp_path, source)
    assert sortie["code_retour"] == 0, sortie["stderr"]
    assert "1225" in sortie["stdout"]
    produits = {f["chemin"] for f in sortie["fichiers"]}
    assert "travail/sous/dossier/total.txt" in produits


@pytest.mark.parametrize(
    "source",
    [
        "print('systemctl restart carso')\n",
        "commande = 'taskkill /IM python.exe /F'\n",
        "print('uvicorn app.main:app --reload')\n",
        "print('kill 1234')\n",
        "print('git push origin main')\n",
        "print(open('.env').read())\n",
    ],
)
def test_script_qui_touche_a_l_exploitation_est_refuse(source: str) -> None:
    with pytest.raises(PermissionDeniedError, match="Script refusé"):
        verifier_script_sur(source)


def test_refus_exploitation_est_rendu_avant_execution(tmp_path: Path) -> None:
    """Le refus est une décision du tool : rien n'est écrit ni lancé."""
    with pytest.raises(PermissionDeniedError):
        _executer(tmp_path, "importer = 'systemctl stop carso'\nprint(importer)\n")
    espace_racine = tmp_path / "workdirs"
    assert not espace_racine.exists() or not any(espace_racine.iterdir())


def test_script_de_donnees_reste_autorise(tmp_path: Path) -> None:
    """Les mots ordinaires du métier ne doivent pas être pris pour des ordres."""
    source = (
        "postes = {'formation': 5, 'logistique': 3}\n"
        "print(sum(postes.values()))\n"
        "print('service rendu : 8')\n"
    )
    sortie = _executer(tmp_path, source)
    assert sortie["code_retour"] == 0, sortie["stderr"]
    assert "8" in sortie["stdout"]


def test_niveau_standard_est_annonce_au_modele(tmp_path: Path) -> None:
    outil = build_run_python_script(
        identite=_IDENTITE, policy=_POLICY, settings=_settings(tmp_path, "standard")
    )
    assert "standard" in outil.description
    eleve = build_run_python_script(
        identite=_IDENTITE, policy=_POLICY, settings=_settings(tmp_path, "eleve")
    )
    assert "élevé" in eleve.description


def test_configuration_rejette_un_niveau_inconnu() -> None:
    from pydantic import ValidationError as PydanticValidationError

    with pytest.raises(PydanticValidationError):
        Settings(scripts_confiance="total")
