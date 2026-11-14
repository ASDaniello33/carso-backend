"""Le classeur d'un import se retrouve par ancre, sans identifiant deviné.

Constat d'usage réel (13/11/2026) : le prompt d'import remis par la page Session
ne portait que le ``session_id``, et l'assistant formateur ne disposait que de
``list_mission_documents(mission_id)``. Il n'avait donc **aucun** moyen de
retrouver le classeur joint ; il a tourné en boucle en réessayant l'identifiant
de session comme ``document_id``, puis en cherchant une mission qu'on ne lui
avait pas donnée.

La lecture est désormais **une seule** opération à ancre
(``list_documents(ancre, entity_id)``) : la mission et sa session sont les deux
faces du même dossier, la règle ne vit qu'une fois.

Ces tests verrouillent la correction :

- l'outil rend le classeur déposé depuis la session, comme celui de la mission ;
- l'aperçu s'enchaîne avec l'identifiant réellement lu, jamais deviné ;
- une ancre hors matrice est refusée (l'assistant ne lit pas tout).

Test au niveau du **tool** : c'est la surface que l'agent appelle réellement.
"""

from __future__ import annotations

import io
from collections.abc import Iterator
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.agents.assistant_formateur import DEFINITION
from app.agents.tools.agent_assistant_formateur_tools import (
    NOMS_TOOLS,
    _list_documents,
    _preview_import,
    construire_tools_assistant_formateur,
)
from app.application.dto import DocumentUploadInput
from app.application.perimetre import Perimetre
from app.application.services.document_service import DocumentService
from app.core.config import get_settings
from app.domain.base import Base
from app.domain.enums import (
    RoleUtilisateur,
    StatutMission,
    StatutUtilisateur,
    TypeDocument,
)
from app.domain.execution import Beneficiaire, Mission, SessionFormation
from app.domain.identity import Utilisateur
from app.domain.organization import Organisation
from tests.document_fixtures import build_xlsx


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as s:
        yield s
    engine.dispose()


@pytest.fixture(autouse=True)
def stockage_tmp(monkeypatch, tmp_path: Path) -> Iterator[None]:
    monkeypatch.setattr(get_settings(), "storage_root", str(tmp_path))
    yield


def _perimetre(session: Session) -> Perimetre:
    """Compte interne : l'agent n'est pas restreint ici, la lecture seule suffit."""
    utilisateur = Utilisateur(
        email="admin@carso.mg",
        nom="Test",
        prenom="Import",
        password_hash="x",
        role=RoleUtilisateur.ADMINISTRATEUR.value,
        statut=StatutUtilisateur.ACTIF.value,
    )
    session.add(utilisateur)
    session.flush()
    return Perimetre(
        role=utilisateur.role,
        utilisateur_id=utilisateur.id,
        equipe_id=None,
        est_complet=True,
    )


@pytest.fixture
def mission_session_classeur(session: Session) -> tuple[Mission, SessionFormation, str]:
    """Une mission, sa session, et le classeur déposé depuis la session."""
    organisation = Organisation(nom="Client Import")
    session.add(organisation)
    session.flush()
    mission = Mission(
        organisation_id=organisation.id,
        reference="M-IMPORT-01",
        titre="Mission import",
        statut=StatutMission.PLANIFIE.value,
    )
    session.add(mission)
    session.flush()
    session_formation = SessionFormation(mission_id=mission.id, theme="Jour 1")
    session.add(session_formation)
    session.flush()

    contenu = build_xlsx(
        feuille="Liste",
        lignes=[["nom", "prenom"], ["Sow", "Fatou"]],
    )
    document = DocumentService(session, perimetre=_perimetre(session)).enregistrer_document(
        DocumentUploadInput(
            type_document=TypeDocument.LISTE_BENEFICIAIRES.value,
            nom="liste.xlsx",
            stream=io.BytesIO(contenu),
            session_id=session_formation.id,
        )
    )
    session.flush()
    return mission, session_formation, str(document.id)


def test_le_contrat_declare_la_lecture_par_ancre() -> None:
    """Le nom doit être **déclaré** au contrat : sinon le harnais ne le monte pas."""
    assert "list_documents" in NOMS_TOOLS
    assert "list_documents" in DEFINITION.tools
    # Deux outils jumeaux auraient porté la même règle en double.
    assert "list_mission_documents" not in NOMS_TOOLS
    assert "list_session_documents" not in NOMS_TOOLS


def test_l_outil_est_reellement_monte() -> None:
    """Un nom déclaré mais absent du graphe ne sert à rien."""
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    try:
        outils = construire_tools_assistant_formateur(DEFINITION, sessionmaker(bind=engine))
        assert "list_documents" in {tool.name for tool in outils}
    finally:
        engine.dispose()


def test_le_classeur_se_retrouve_par_la_session(
    session: Session, mission_session_classeur: tuple[Mission, SessionFormation, str]
) -> None:
    """Le scénario du bug : l'agent n'a que le ``session_id``."""
    _mission, session_formation, document_id = mission_session_classeur

    resultat = _list_documents(
        session, {"ancre": "session", "entity_id": str(session_formation.id)}
    )

    documents = resultat["documents"]
    assert [d["document_id"] for d in documents] == [document_id]
    # Les métadonnées suffisent à choisir le classeur, sans lire son contenu.
    assert documents[0]["type_document"] == TypeDocument.LISTE_BENEFICIAIRES.value
    assert documents[0]["nom"] == "liste.xlsx"


def test_la_mission_rend_aussi_le_classeur_de_sa_session(
    session: Session, mission_session_classeur: tuple[Mission, SessionFormation, str]
) -> None:
    """Appartenance partagée : les deux ancres mènent au même document."""
    mission, _session_formation, document_id = mission_session_classeur

    resultat = _list_documents(session, {"ancre": "mission", "entity_id": str(mission.id)})

    assert [d["document_id"] for d in resultat["documents"]] == [document_id]


def test_une_ancre_hors_matrice_est_refusee() -> None:
    """L'assistant ne lit pas les pièces d'une organisation ni d'un appel."""
    from app.agents.tools.agent_assistant_formateur_tools import DocumentsAncreInput

    with pytest.raises(ValidationError):
        DocumentsAncreInput.model_validate(
            {"ancre": "organisation", "entity_id": "00000000-0000-0000-0000-000000000001"}
        )


def test_l_apercu_s_enchaines_avec_l_identifiant_lu(
    session: Session, mission_session_classeur: tuple[Mission, SessionFormation, str]
) -> None:
    """Lecture puis aperçu : l'identifiant vient de la liste, il n'est pas deviné.

    L'aperçu est vérifié ici pour la règle qui compte : aucune écriture. Le
    service fait foi (testé ailleurs) ; on vérifie qu'il accepte l'identifiant
    réellement lu et ne crée aucun bénéficiaire.
    """
    _mission, session_formation, _document_id = mission_session_classeur
    lu = _list_documents(session, {"ancre": "session", "entity_id": str(session_formation.id)})
    document_id = lu["documents"][0]["document_id"]

    apercu = _preview_import(
        session,
        {"document_id": document_id, "session_id": str(session_formation.id)},
    )

    assert apercu["requires_approval"] is True
    assert apercu["nb_valides"] == 1  # une ligne : « Sow Fatou »
    assert session.query(Beneficiaire).count() == 0
