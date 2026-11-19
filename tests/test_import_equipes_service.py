"""Import du vivier depuis un classeur Excel (service, 18/11).

Règles vérifiées : colonnes nom/prénom obligatoires, lignes incomplètes
signalées, doublons **signalés jamais fusionnés**, aperçu sans écriture, et CV
jamais importés par le classeur.
"""

from __future__ import annotations

import io
import re
from collections.abc import Iterator

import pytest
from openpyxl import Workbook
from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.sql.ddl import CreateColumn

from app.application.dto import DocumentUploadInput
from app.application.perimetre import Perimetre
from app.application.services.document_service import DocumentService
from app.application.services.import_equipes_service import (
    ImportEquipesService,
    StatutLigneEquipe,
)
from app.core.config import get_settings
from app.core.errors import ValidationError
from app.domain.base import Base
from app.domain.enums import RoleUtilisateur, StatutUtilisateur, TypeDocument
from app.domain.execution import Equipe
from app.domain.identity import Utilisateur


@compiles(JSONB, "sqlite")
def _jsonb_as_json(type_: JSONB, compiler: object, **kw: object) -> str:
    return "JSON"


@compiles(CreateColumn, "sqlite")
def _strip_pg_defaults(element: CreateColumn, compiler: object, **kw: object) -> str:
    ddl = compiler.visit_create_column(element)  # type: ignore[arg-type]
    return re.sub(r"DEFAULT gen_random_uuid\(\)", "", ddl)


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as s:
        yield s
    engine.dispose()


@pytest.fixture(autouse=True)
def stockage_tmp(monkeypatch, tmp_path) -> Iterator[None]:
    monkeypatch.setattr(get_settings(), "storage_root", str(tmp_path))
    yield


def _classeur(lignes: list[list[object]], *, entete: list[str] | None = None) -> bytes:
    """Classeur XLSX minimal : une feuille, l'en-tête en première ligne."""
    classeur = Workbook()
    feuille = classeur.active
    feuille.title = "Vivier"
    feuille.append(entete or ["Nom", "Prenom", "Email", "Telephone", "Profil", "Fonction"])
    for ligne in lignes:
        feuille.append(ligne)
    tampon = io.BytesIO()
    classeur.save(tampon)
    return tampon.getvalue()


def _compte(session: Session) -> Utilisateur:
    """Un compte réel : un document sans ancre appartient à quelqu'un (ADR 0013)."""
    utilisateur = Utilisateur(
        email="rh.import@carso.test",
        nom="Import",
        prenom="RH",
        password_hash="x",
        role=RoleUtilisateur.COLLABORATEUR.value,
        statut=StatutUtilisateur.ACTIF.value,
    )
    session.add(utilisateur)
    session.flush()
    return utilisateur


def _deposer(session: Session, contenu: bytes, nom: str = "vivier.xlsx"):
    proprietaire = _compte(session)
    return DocumentService(
        session,
        perimetre=Perimetre(
            role=proprietaire.role,
            utilisateur_id=proprietaire.id,
            est_complet=True,
        ),
    ).enregistrer_document(
        DocumentUploadInput(
            type_document=TypeDocument.AUTRE.value,
            nom=nom,
            stream=io.BytesIO(contenu),
        )
    )


def test_apercu_classe_les_lignes_sans_ecrire(session: Session) -> None:
    """L'aperçu dit ce qui serait créé — et ne crée rien."""
    with session.begin_nested():
        session.add(Equipe(nom="Rakoto", prenom="Voahangy", email="voahangy@carso.test"))
    document = _deposer(
        session,
        _classeur(
            [
                ["Rakoto", "Voahangy", "voahangy@carso.test", "", "Formation", "Formatrice"],
                ["Rasoa", "Sara", "sara@carso.test", "034", "Gestion", "Formatrice"],
                ["SansPrenom", "", "", "", "", ""],
            ]
        ),
    )

    apercu = ImportEquipesService(session).preparer(document.id)

    assert apercu.feuille == "Vivier"
    assert apercu.nb_lignes == 3
    assert apercu.nb_valides == 1
    statuts = [ligne.statut for ligne in apercu.lignes]
    assert statuts == [
        StatutLigneEquipe.DOUBLON.value,
        StatutLigneEquipe.VALIDE.value,
        StatutLigneEquipe.INCOMPLETE.value,
    ]
    # Aucune écriture : le vivier compte toujours une seule personne.
    assert len(list(session.scalars(select(Equipe)))) == 1


def test_import_cree_les_lignes_valides_et_signale_les_doublons(session: Session) -> None:
    """Création des seules lignes valides ; doublon signalé, jamais recréé."""
    session.add(Equipe(nom="Rakoto", prenom="Voahangy"))
    session.flush()
    document = _deposer(
        session,
        _classeur(
            [
                ["Rakoto", "Voahangy", "", "", "", ""],
                ["Rasoa", "Sara", "sara@carso.test", "034", "Gestion", "Formatrice"],
                ["Rasoa", "Sara", "sara@carso.test", "034", "Gestion", "Formatrice"],
            ]
        ),
    )

    resultat = ImportEquipesService(session).importer(document.id)

    assert resultat.crees == 1
    assert resultat.personnes[0]["nom_complet"] == "Sara Rasoa"
    crees = list(session.scalars(select(Equipe).where(Equipe.nom == "Rasoa")))
    assert len(crees) == 1
    assert crees[0].email == "sara@carso.test"
    assert crees[0].profil == "Gestion"
    assert crees[0].fonction == "Formatrice"
    # Un doublon du classeur n'est pas fusionné en silence : il est écarté.
    doublons = [
        ligne
        for ligne in ImportEquipesService(session).preparer(document.id).lignes
        if ligne.statut == StatutLigneEquipe.DOUBLON.value
    ]
    assert len(doublons) == 2


def test_colonnes_obligatoires_absentes_refusees(session: Session) -> None:
    """Sans colonne nom/prenom, le refus est explicite et nomme les colonnes."""
    document = _deposer(session, _classeur([["Rasoa"]], entete=["Famille"]))

    with pytest.raises(ValidationError) as refus:
        ImportEquipesService(session).preparer(document.id)

    assert "nom" in str(refus.value)
    assert "prenom" in str(refus.value)


def test_import_sans_ligne_valide_refuse(session: Session) -> None:
    """Un classeur sans ligne exploitable ne produit aucune écriture muette."""
    document = _deposer(session, _classeur([["", "", "", "", "", ""]]))

    with pytest.raises(ValidationError):
        ImportEquipesService(session).importer(document.id)

    assert list(session.scalars(select(Equipe))) == []
