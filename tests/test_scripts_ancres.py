"""Scripts **ancrés** (INC-124) : produire près du dossier métier, déposer sans copie.

Ce que ces tests verrouillent, dans l'ordre du plan approuvé :

1. **mode personnel inchangé** — sans ``ancre``, le script s'exécute dans
   ``workdirs/user-<compte>`` et le retour ne parle ni de temporaire ni de
   document ;
2. **exécution ancrée** — le script tourne dans ``.tmp-run-*`` créé **sous** le
   dossier métier, y écrit, lit ce dossier (son parent) et les modèles de
   l'organisation émettrice ;
3. **dépôt** — chaque livrable déclaré devient un document ``proposed`` rattaché
   à l'ancre ; relancer avec le même nom et le même type produit une **version
   suivante**, pas un doublon ;
4. **dossier de travail supprimé** après l'appel, y compris en cas d'échec ;
5. **refus explicites** — une écriture hors du dossier de travail échoue, un
   script en erreur ne dépose rien, un livrable invalide est listé sans
   empêcher les autres, ``livrables`` sans ancre est refusé à la validation ;
6. **ancre hors base** — refusée avant de toucher au disque (aucun dossier).

SQLite mémoire + stockage local temporaire (mêmes fixtures que
``test_tools_documents_html.py``).
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.agents.contexte import (
    UserContext,
    poser_contexte_utilisateur,
    retablir_contexte_utilisateur,
)
from app.application.services import DocumentService
from app.core.errors import NotFoundError
from app.documents.storage import LocalDocumentStorage
from app.domain.base import Base
from app.domain.organization import Appel, Organisation
from app.tools.permissions import AgentIdentity, PermissionPolicy
from app.tools.scripts import build_run_python_script
from app.tools.workspace import espace_utilisateur

IDENTITE = AgentIdentity(agent_id="agent_assistant_formateur")
POLICY = PermissionPolicy(
    allowed_capabilities=frozenset(
        {"script_python", "document_read", "document_propose"}
    )
)

#: Script minimal qui produit un classeur valide avec l'aide fournie.
SOURCE_CLASSEUR = (
    "import classeur\n"
    "classeur.ecrire_classeur('sorties/presences.xlsx', 'Presences',"
    " [['Nom', 'Statut'], ['Rakoto', 'present'], ['Bao', 'present']],"
    " titre='Liste de présence')\n"
    "print('ok')\n"
)


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
    return LocalDocumentStorage(tmp_path / "storage", max_bytes=4 * 1024 * 1024)


@pytest.fixture
def service(session: Session, storage: LocalDocumentStorage) -> DocumentService:
    return DocumentService(session, storage=storage, actor_id="test")


@pytest.fixture
def organisation(session: Session) -> Organisation:
    organisation = Organisation(nom="Client Ancre")
    session.add(organisation)
    session.flush()
    return organisation


@pytest.fixture
def appel(session: Session, organisation: Organisation) -> Appel:
    appel = Appel(
        organisation_id=organisation.id,
        type="appel_a_proposition",
        reference="AP-124",
        titre="Appel ancré",
        statut="recu",
    )
    session.add(appel)
    session.flush()
    return appel


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


def _outil(settings: SimpleNamespace, service: DocumentService):
    return build_run_python_script(
        identite=IDENTITE,
        policy=POLICY,
        settings=settings,
        service_factory=lambda: service,
    )


def _executer(
    settings: SimpleNamespace,
    service: DocumentService,
    *,
    source: str = SOURCE_CLASSEUR,
    ancre: str | None = None,
    entity_id=None,
    livrables=None,
) -> dict:
    compte = uuid4()
    jeton = poser_contexte_utilisateur(UserContext(utilisateur_id=str(compte)))
    try:
        payload = {"source": source, "nom": "utilitaire"}
        if ancre is not None:
            payload["ancre"] = ancre
            payload["entity_id"] = str(entity_id)
        if livrables is not None:
            payload["livrables"] = livrables
        return _outil(settings, service).handler(payload)
    finally:
        retablir_contexte_utilisateur(jeton)


# --- 1. Mode personnel inchangé ---------------------------------------------


def test_sans_ancre_l_espace_personnel_est_inchange(
    tmp_path: Path, service: DocumentService
) -> None:
    """Le comportement historique ne bouge pas : rien n'est déposé, rien n'est purgé."""
    settings = _settings(tmp_path)
    source = "from pathlib import Path\nPath('note.txt').write_text('ok', encoding='utf-8')\n"
    sortie = _executer(settings, service, source=source)

    assert sortie["code_retour"] == 0, sortie["stderr"]
    assert {f["chemin"] for f in sortie["fichiers"]} == {"note.txt"}
    assert "documents" not in sortie
    assert "temporaire" not in sortie
    assert service.lister_tous() == []


# --- 2. Exécution ancrée -----------------------------------------------------


def test_script_ancre_ecrit_dans_le_dossier_metier_et_depose(
    tmp_path: Path, service: DocumentService, organisation: Organisation
) -> None:
    """Le fichier produit sous le dossier métier devient un document rattaché."""
    settings = _settings(tmp_path)
    sortie = _executer(
        settings,
        service,
        ancre="organisation",
        entity_id=organisation.id,
        livrables=[
            {
                "fichier": "sorties/presences.xlsx",
                "type_document": "liste_beneficiaires",
                "nom": "presences.xlsx",
            }
        ],
    )

    assert sortie["code_retour"] == 0, sortie["stderr"]
    assert sortie["temporaire"] == "supprimé"
    assert len(sortie["documents"]) == 1, sortie["refuses"]
    document = sortie["documents"][0]
    assert document["statut"] == "proposed"
    assert document["type_document"] == "liste_beneficiaires"
    assert document["nom"] == "presences.xlsx"
    assert document["version"] == 1

    # Le fichier est dans le dossier de l'organisation, pas dans un temporaire.
    dossier = tmp_path / "storage" / "organisations" / str(organisation.id)
    assert (dossier / "presences.xlsx").is_file()
    assert [p.name for p in dossier.iterdir() if p.name.startswith(".tmp-run-")] == []


def test_le_script_lit_le_dossier_metier_et_les_modeles(
    tmp_path: Path, service: DocumentService, organisation: Organisation
) -> None:
    """Lecture : le dossier de l'ancre (parent du temporaire) et ses modèles."""
    settings = _settings(tmp_path)
    dossier_org = tmp_path / "storage" / "organisations" / str(organisation.id)
    dossier_org.mkdir(parents=True, exist_ok=True)
    (dossier_org / "modele.txt").write_text("modele du client", encoding="utf-8")

    source = (
        "from pathlib import Path\n"
        "modele = Path('..') / 'modele.txt'\n"
        "print('lu:', modele.read_text(encoding='utf-8'))\n"
        "Path('resultat.txt').write_text(modele.read_text(encoding='utf-8'), encoding='utf-8')\n"
    )
    sortie = _executer(
        settings,
        service,
        source=source,
        ancre="organisation",
        entity_id=organisation.id,
    )
    assert sortie["code_retour"] == 0, sortie["stderr"]
    assert "lu: modele du client" in sortie["stdout"]


def test_ecriture_hors_du_dossier_de_travail_refusee(
    tmp_path: Path, service: DocumentService, organisation: Organisation
) -> None:
    """La garde d'audit tient : le script n'écrit pas dans le dossier métier."""
    settings = _settings(tmp_path)
    source = (
        "from pathlib import Path\n"
        "Path('..') / 'intrus.txt'\n"
        "(Path('..') / 'intrus.txt').write_text('non', encoding='utf-8')\n"
    )
    sortie = _executer(
        settings,
        service,
        source=source,
        ancre="organisation",
        entity_id=organisation.id,
    )
    assert sortie["code_retour"] != 0
    assert "Ecriture refusee hors de l'espace de travail" in sortie["stderr"]
    assert sortie["documents"] == []
    assert not (tmp_path / "storage" / "organisations" / str(organisation.id) / "intrus.txt").exists()


# --- 3. Dépôt, versions, refus partiels -------------------------------------


def test_relancer_produit_une_nouvelle_version(
    tmp_path: Path, service: DocumentService, organisation: Organisation
) -> None:
    settings = _settings(tmp_path)
    livrables = [
        {
            "fichier": "sorties/presences.xlsx",
            "type_document": "liste_beneficiaires",
            "nom": "presences.xlsx",
        }
    ]
    premier = _executer(
        settings,
        service,
        ancre="organisation",
        entity_id=organisation.id,
        livrables=livrables,
    )
    second = _executer(
        settings,
        service,
        ancre="organisation",
        entity_id=organisation.id,
        livrables=livrables,
    )
    assert premier["documents"][0]["version"] == 1
    assert second["documents"][0]["version"] == 2
    # Chaque version est une fiche distincte (l'historique se lit par nom + type) :
    # le second dépôt n'écrase pas le premier, il le révise.
    derniere = service.derniere_version("presences.xlsx", "liste_beneficiaires")
    assert derniere is not None
    assert str(derniere.id) == second["documents"][0]["document_id"]
    assert {doc.version for doc in service.lister_tous()} == {1, 2}


def test_script_en_erreur_ne_depose_rien(
    tmp_path: Path, service: DocumentService, organisation: Organisation
) -> None:
    settings = _settings(tmp_path)
    source = SOURCE_CLASSEUR + "raise SystemExit(3)\n"
    sortie = _executer(
        settings,
        service,
        source=source,
        ancre="organisation",
        entity_id=organisation.id,
        livrables=[
            {
                "fichier": "sorties/presences.xlsx",
                "type_document": "liste_beneficiaires",
            }
        ],
    )
    assert sortie["code_retour"] == 3
    assert sortie["documents"] == []
    assert "script terminé en erreur" in sortie["refuses"][0]["cause"]
    assert service.lister_tous() == []
    assert sortie["temporaire"] == "supprimé"


def test_un_livrable_invalide_n_empeche_pas_les_autres(
    tmp_path: Path, service: DocumentService, organisation: Organisation
) -> None:
    settings = _settings(tmp_path)
    sortie = _executer(
        settings,
        service,
        ancre="organisation",
        entity_id=organisation.id,
        livrables=[
            {"fichier": "absent.xlsx", "type_document": "liste_beneficiaires"},
            {"fichier": "sorties/presences.xlsx", "type_document": "type_inexistant"},
            {"fichier": "sorties/presences.xlsx", "type_document": "rapport"},
        ],
    )
    assert sortie["code_retour"] == 0
    assert [refus["fichier"] for refus in sortie["refuses"]] == [
        "absent.xlsx",
        "sorties/presences.xlsx",
    ]
    assert "absent du dossier de travail" in sortie["refuses"][0]["cause"]
    assert "Type de document inconnu" in sortie["refuses"][1]["cause"]
    assert len(sortie["documents"]) == 1
    assert sortie["documents"][0]["type_document"] == "rapport"


def test_livrables_sans_ancre_refuses_avant_execution(
    tmp_path: Path, service: DocumentService
) -> None:
    """Contrat du schéma : un livrable doit avoir un objet auquel se rattacher."""
    from pydantic import ValidationError as PydanticValidationError

    from app.tools.scripts import RunPythonScriptInput

    with pytest.raises(PydanticValidationError, match="exige 'ancre'"):
        RunPythonScriptInput(
            source="print(1)",
            livrables=[{"fichier": "a.xlsx", "type_document": "rapport"}],
        )


def test_ancre_inconnue_refusee_sans_laisser_de_dossier(
    tmp_path: Path, service: DocumentService
) -> None:
    settings = _settings(tmp_path)
    inconnue = uuid4()
    with pytest.raises(NotFoundError):
        _executer(
            settings,
            service,
            ancre="mission",
            entity_id=inconnue,
        )
    racine = tmp_path / "storage"
    laisses = list(racine.rglob(".tmp-run-*")) if racine.exists() else []
    assert laisses == []


# --- 4. Nettoyage ------------------------------------------------------------


def test_les_temporaires_orphelins_sont_purges(
    tmp_path: Path, service: DocumentService, organisation: Organisation
) -> None:
    """Un dossier laissé par un appel interrompu ne s'accumule pas."""
    settings = _settings(tmp_path)
    dossier_org = tmp_path / "storage" / "organisations" / str(organisation.id)
    orphelin = dossier_org / ".tmp-run-orphan"
    orphelin.mkdir(parents=True)
    (orphelin / "reste.txt").write_text("x", encoding="utf-8")

    sortie = _executer(
        settings,
        service,
        ancre="organisation",
        entity_id=organisation.id,
    )
    assert sortie["temporaires_purges"] == 1
    assert not orphelin.exists()


def test_espace_personnel_toujours_cree(
    tmp_path: Path, service: DocumentService, organisation: Organisation
) -> None:
    """Le script est conservé dans l'espace du compte, même en mode ancré."""
    settings = _settings(tmp_path)
    compte_source = "print('ok')\n"
    compte = uuid4()
    jeton = poser_contexte_utilisateur(UserContext(utilisateur_id=str(compte)))
    try:
        _outil(settings, service).handler(
            {
                "source": compte_source,
                "nom": "ancré",
                "ancre": "organisation",
                "entity_id": str(organisation.id),
            }
        )
    finally:
        retablir_contexte_utilisateur(jeton)

    espace = espace_utilisateur(settings, compte)
    assert list((espace / ".scripts").glob("*.py"))
