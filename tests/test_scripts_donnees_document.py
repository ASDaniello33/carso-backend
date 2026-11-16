"""Jeu de données d'un document lu **par script** (agent statistique).

Le parcours qui bouclait (16/11/2026) : un CSV de populations joint au chat,
« estime la population 2023 par continent ». L'agent lisait le fichier avec
``read_document_range`` — 200 lignes d'un coup, résultat trop gros pour la
fenêtre — puis cherchait indéfiniment un moyen de passer ces lignes à
``run_python_script``, qui n'y avait pas accès : aucun chemin n'existait entre
le stockage documentaire et la sandbox.

``donnees`` est ce chemin : le document est copié **dans le dossier
d'exécution**, le script le lit avec pandas, et seul le résultat revient.

Ce que ces tests verrouillent :

1. le CSV est déposé sous son nom et **lu** par le script (agrégat réel) ;
2. un nom de fichier demandé ne peut pas sortir du dossier (``../../x.csv``) ;
3. un PDF/DOCX est refusé **avant** de lancer le script (la lecture passe par
   ``lire_document``) ;
4. sans ``document_read``, l'appel est refusé.
"""

from __future__ import annotations

import io
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
from app.application.dto import DocumentUploadInput
from app.application.services import DocumentService
from app.core.errors import PermissionDeniedError, ValidationError
from app.documents.storage import LocalDocumentStorage
from app.domain.base import Base
from app.domain.organization import Organisation
from app.tools.permissions import AgentIdentity, PermissionPolicy
from app.tools.scripts import build_run_python_script
from app.tools.workspace import espace_utilisateur

IDENTITE = AgentIdentity(agent_id="agent_statistique")
POLICY = PermissionPolicy(
    allowed_capabilities=frozenset({"script_python", "document_read"})
)

#: Trois pays, deux continents : la somme par continent est vérifiable à la main.
CSV_POPULATIONS = (
    "Country,Continent,2022 Population,Growth Rate\n"
    "Madagascar,Africa,30000000,1.0257\n"
    "Nigeria,Africa,218000000,1.0241\n"
    "France,Europe,68000000,1.0012\n"
)

SOURCE_AGREGAT = (
    "import pandas as pd\n"
    "df = pd.read_csv('donnees.csv')\n"
    "for continent, valeur in df.groupby('Continent')['2022 Population'].sum().items():\n"
    "    print(continent, int(valeur))\n"
)


@pytest.fixture
def session() -> Iterator[Session]:
    moteur = create_engine("sqlite://")
    Base.metadata.create_all(moteur)
    fabrique = sessionmaker(bind=moteur)
    with fabrique() as active:
        yield active
    moteur.dispose()


@pytest.fixture
def storage(tmp_path: Path) -> LocalDocumentStorage:
    return LocalDocumentStorage(tmp_path / "storage", max_bytes=4 * 1024 * 1024)


@pytest.fixture
def organisation(session: Session) -> Organisation:
    organisation = Organisation(nom="Client Données")
    session.add(organisation)
    session.flush()
    return organisation


@pytest.fixture
def service(session: Session, storage: LocalDocumentStorage) -> DocumentService:
    return DocumentService(session, storage=storage, actor_id="test")


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


def _document(
    service: DocumentService, organisation: Organisation, nom: str, contenu: bytes
) -> str:
    """Document rattaché à une organisation : il suit le périmètre de cet objet."""
    document = service.enregistrer_document(
        DocumentUploadInput(
            type_document="autre",
            nom=nom,
            stream=io.BytesIO(contenu),
            created_by="test",
            organisation_id=organisation.id,
        )
    )
    service.session.flush()
    return document.id


def _executer(
    settings: SimpleNamespace,
    service: DocumentService,
    payload: dict,
    *,
    policy: PermissionPolicy = POLICY,
    compte: object | None = None,
) -> dict:
    outil = build_run_python_script(
        identite=IDENTITE,
        policy=policy,
        settings=settings,
        service_factory=lambda: service,
    )
    jeton = poser_contexte_utilisateur(
        UserContext(utilisateur_id=str(compte or uuid4()))
    )
    try:
        return outil.handler(payload)
    finally:
        retablir_contexte_utilisateur(jeton)


def test_le_csv_est_depose_et_lu_par_le_script(
    tmp_path: Path, service: DocumentService, organisation: Organisation
) -> None:
    pytest.importorskip("pandas")
    document_id = _document(
        service, organisation, "donnees.csv", CSV_POPULATIONS.encode()
    )

    sortie = _executer(
        _settings(tmp_path),
        service,
        {
            "source": SOURCE_AGREGAT,
            "nom": "population",
            "donnees": [{"document_id": document_id, "fichier": "donnees.csv"}],
        },
    )

    assert sortie["code_retour"] == 0, sortie["stderr"]
    # La donnée a bien été lue, pas devinée : la somme du fichier sort du script.
    assert "Africa 248000000" in sortie["stdout"]
    assert "Europe 68000000" in sortie["stdout"]
    # Le retour dit ce qui a réellement été déposé.
    assert [(d["fichier"], d["document_id"]) for d in sortie["donnees"]] == [
        ("donnees.csv", str(document_id))
    ]
    assert sortie["donnees"][0]["octets"] == len(CSV_POPULATIONS.encode())


def test_la_copie_ne_reste_pas_dans_l_espace_de_travail(
    tmp_path: Path, service: DocumentService, organisation: Organisation
) -> None:
    """Une copie de document métier n'est pas un fichier de travail.

    Elle n'apparaît ni dans ``fichiers`` (ce que le compte verrait) ni sur le
    disque après l'exécution ; seul ce que le script a réellement produit reste.
    """
    pytest.importorskip("pandas")
    document_id = _document(
        service, organisation, "donnees.csv", CSV_POPULATIONS.encode()
    )
    settings = _settings(tmp_path)
    compte = uuid4()

    sortie = _executer(
        settings,
        service,
        {
            "source": (
                "import pandas as pd\n"
                "df = pd.read_csv('donnees.csv')\n"
                "print('lignes', len(df))\n"
                "with open('resultat.txt', 'w', encoding='utf-8') as sortie:\n"
                "    sortie.write('ok')\n"
            ),
            "nom": "population",
            "donnees": [{"document_id": document_id, "fichier": "donnees.csv"}],
        },
        compte=compte,
    )

    assert sortie["code_retour"] == 0, sortie["stderr"]
    assert "lignes 3" in sortie["stdout"]
    # La copie a bien été retirée, et elle n'a jamais été listée comme fichier.
    assert sortie["donnees_retirees"] == 1
    assert [f["chemin"] for f in sortie["fichiers"]] == ["resultat.txt"]
    espace = espace_utilisateur(settings, compte)
    assert not (espace / "donnees.csv").exists()
    assert (espace / "resultat.txt").exists()


def test_un_nom_de_fichier_ne_sort_pas_du_dossier(
    tmp_path: Path, service: DocumentService, organisation: Organisation
) -> None:
    pytest.importorskip("pandas")
    document_id = _document(
        service, organisation, "donnees.csv", CSV_POPULATIONS.encode()
    )

    sortie = _executer(
        _settings(tmp_path),
        service,
        {
            "source": "import pandas as pd\nprint(len(pd.read_csv('evil.csv')))",
            "nom": "evasion",
            "donnees": [{"document_id": document_id, "fichier": "../../evil.csv"}],
        },
    )

    assert sortie["code_retour"] == 0, sortie["stderr"]
    assert sortie["donnees"][0]["fichier"] == "evil.csv"
    assert "3" in sortie["stdout"]
    # Aucun fichier n'a été écrit hors du dossier d'exécution.
    assert not (tmp_path / "evil.csv").exists()


def test_un_pdf_est_refuse_avant_le_script(
    tmp_path: Path, service: DocumentService, organisation: Organisation
) -> None:
    document_id = _document(
        service, organisation, "rapport.pdf", b"%PDF-1.4\n%%EOF\n"
    )

    with pytest.raises(ValidationError, match="n'est pas un jeu de données"):
        _executer(
            _settings(tmp_path),
            service,
            {
                "source": "print('jamais lancé')",
                "nom": "refus",
                "donnees": [{"document_id": document_id}],
            },
        )


def test_sans_document_read_les_donnees_sont_refusees(
    tmp_path: Path, service: DocumentService, organisation: Organisation
) -> None:
    document_id = _document(
        service, organisation, "donnees.csv", CSV_POPULATIONS.encode()
    )
    sans_lecture = PermissionPolicy(allowed_capabilities=frozenset({"script_python"}))

    with pytest.raises(PermissionDeniedError):
        _executer(
            _settings(tmp_path),
            service,
            {
                "source": "print('jamais lancé')",
                "nom": "refus",
                "donnees": [{"document_id": document_id}],
            },
            policy=sans_lecture,
        )
