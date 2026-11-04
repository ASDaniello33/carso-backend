"""Correction d'un livrable PAR SECTION + versionnement automatique.

Deux comportements sont vérifiés ici, de bout en bout (service + stockage local
temporaire + SQLite, aucun mock du rendu ni du dépôt) :

1. ``modifier_section_document`` corrige **une** section d'un document rendu par
   ``generer_document_professionnel`` (source conservée au dépôt) sans réécrire
   le reste, et dépose une **nouvelle version** ;
2. redéposer un document de même nom, même type et même rattachement crée une
   version suivante (v2, v3…) au lieu d'échouer en « version 1 existe déjà » —
   c'est ce qui faisait afficher une file de documents tous en v1.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.application.services import DocumentService
from app.documents.storage import LocalDocumentStorage
from app.domain.base import Base
from app.domain.organization import Organisation
from app.tools.documents import build_document_tools
from app.tools.permissions import AgentIdentity, PermissionPolicy

IDENTITE = AgentIdentity(agent_id="agent_generateur_offre")
POLICY = PermissionPolicy(
    allowed_capabilities=frozenset({"document_read", "document_propose"})
)


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as active:
        yield active
    engine.dispose()


@pytest.fixture
def storage(tmp_path: Path) -> LocalDocumentStorage:
    return LocalDocumentStorage(tmp_path / "carso", max_bytes=4 * 1024 * 1024)


@pytest.fixture
def service(session: Session, storage: LocalDocumentStorage) -> DocumentService:
    return DocumentService(session, storage=storage, actor_id="test")


@pytest.fixture
def organisation(session: Session) -> Organisation:
    entite = Organisation(nom="Client Formation")
    session.add(entite)
    session.flush()
    return entite


@pytest.fixture
def outils(service: DocumentService, tmp_path: Path) -> dict[str, Any]:
    construits = build_document_tools(
        identite=IDENTITE,
        policy=POLICY,
        service_factory=lambda: service,
        dossier_brouillons=tmp_path / "_brouillons",
        dossier_sources=tmp_path / "_sources",
    )
    return {outil.name: outil for outil in construits}


_CORPS = (
    '<section class="cover"><h1 class="cover-title">Offre technique</h1></section>'
    "<h1>1. Contexte</h1><p>L'appel demande une formation en incubation.</p>"
    "<h1>2. Démarche</h1><p>Méthodologie en trois phases.</p>"
    "<h1>3. Budget</h1><p>Enveloppe à confirmer.</p>"
)


def _generer(
    outils: dict[str, Any],
    *,
    organisation: Organisation | None = None,
    titre: str = "Offre technique lot 3",
    corps: str = _CORPS,
    format: str = "docx",
) -> dict[str, Any]:
    demande: dict[str, Any] = {
        "action": "generer",
        "titre": titre,
        "format": format,
        "type_document": "offre",
        "contenu_html": corps,
        "propose_par": IDENTITE.agent_id,
    }
    if organisation is not None:
        demande["ancre"] = "organisation"
        demande["entity_id"] = str(organisation.id)
    return outils["generer_document_professionnel"].invoke(demande)


def _section(outils: dict[str, Any], document_id: str, **demande: Any) -> dict[str, Any]:
    return outils["modifier_section_document"].invoke(
        {"document_id": document_id, **demande}
    )


def test_lister_decrit_les_sections(outils: dict[str, Any]) -> None:
    depot = _generer(outils)
    assert depot["ok"] is True, depot

    reponse = _section(outils, depot["document_id"])
    assert reponse["ok"] is True, reponse
    sections = reponse["sections"]
    assert [section["index"] for section in sections] == [1, 2, 3, 4]
    assert sections[0]["classes"] == ["cover"]
    assert sections[1]["titre"] == "1. Contexte"
    assert "caracteres" in sections[1]


def test_remplacer_une_section_cree_une_version(outils: dict[str, Any]) -> None:
    depot = _generer(outils)
    assert depot["version"] == 1

    correction = _section(
        outils,
        depot["document_id"],
        action="remplacer",
        section_index=3,
        contenu_html="<h1>2. Démarche</h1><p>Méthodologie en quatre phases.</p>",
    )
    assert correction["ok"] is True, correction
    assert correction["version"] == 2
    assert correction["statut"] == "proposed"
    assert correction["document_id"] != depot["document_id"]
    assert correction["sections"] == 4

    relu = _section(outils, correction["document_id"])
    assert relu["sections"][2]["titre"] == "2. Démarche"
    # Les sections voisines sont intactes : rien n'a été réécrit.
    assert relu["sections"][1]["titre"] == "1. Contexte"
    assert relu["sections"][3]["titre"] == "3. Budget"


def test_ajouter_une_section_apres_une_autre(outils: dict[str, Any]) -> None:
    depot = _generer(outils)
    ajout = _section(
        outils,
        depot["document_id"],
        action="ajouter",
        section_index=3,
        contenu_html="<h1>2 bis. Calendrier</h1><p>Six semaines.</p>",
    )
    assert ajout["ok"] is True, ajout
    assert ajout["sections"] == 5
    relu = _section(outils, ajout["document_id"])
    assert [section["titre"] for section in relu["sections"]][:4] == [
        "Offre technique",
        "1. Contexte",
        "2. Démarche",
        "2 bis. Calendrier",
    ]


def test_ajouter_sans_index_met_a_la_fin(outils: dict[str, Any]) -> None:
    depot = _generer(outils)
    ajout = _section(
        outils,
        depot["document_id"],
        action="ajouter",
        contenu_html="<h1>4. Annexes</h1><p>Liste des pièces.</p>",
    )
    assert ajout["ok"] is True, ajout
    relu = _section(outils, ajout["document_id"])
    assert relu["sections"][-1]["titre"] == "4. Annexes"


def test_supprimer_une_section(outils: dict[str, Any]) -> None:
    depot = _generer(outils)
    retrait = _section(outils, depot["document_id"], action="supprimer", section_index=4)
    assert retrait["ok"] is True, retrait
    assert retrait["sections"] == 3
    relu = _section(outils, retrait["document_id"])
    assert "3. Budget" not in [section["titre"] for section in relu["sections"]]


def test_redeposer_le_meme_document_cree_une_version(outils: dict[str, Any]) -> None:
    premier = _generer(outils)
    assert premier["version"] == 1

    second = _generer(outils)
    assert second["ok"] is True, second
    assert second["version"] == 2
    assert second["nouvelle_version_de"] == premier["document_id"]
    assert second["document_id"] != premier["document_id"]


def test_meme_nom_sur_deux_ancres_ne_fusionne_pas_les_versions(
    outils: dict[str, Any], organisation: Organisation, session: Session
) -> None:
    """Deux dossiers différents ne partagent jamais un historique de versions.

    Le nom logique reste unique par type (règle existante du dépôt) : un second
    dépôt sur **une autre** ancre est refusé, jamais versionné depuis le premier
    document — mêler deux offres serait pire que le refus.
    """
    from app.domain.organization import Organisation as _Organisation

    autre = _Organisation(nom="Autre client")
    session.add(autre)
    session.flush()

    premier = _generer(outils, organisation=organisation)
    assert premier["version"] == 1

    second = _generer(outils, organisation=autre)
    assert second["ok"] is False
    assert "existe déjà" in second["error"]["message"]

    # Même ancre : là, c'est bien une révision.
    troisieme = _generer(outils, organisation=organisation)
    assert troisieme["ok"] is True, troisieme
    assert troisieme["version"] == 2


def test_document_sans_source_refuse(outils: dict[str, Any]) -> None:
    """Un document structuré n'a pas de corps HTML : la correction le dit."""
    depot = outils["generer_document"].invoke(
        {
            "format": "docx",
            "nom_fichier": "note-simple",
            "type_document": "rapport",
            "propose_par": IDENTITE.agent_id,
            "titre": "Note simple",
            "paragraphes": ["Un paragraphe."],
        }
    )
    assert depot["ok"] is True, depot

    reponse = _section(outils, depot["document_id"])
    assert reponse["ok"] is False
    assert "pas de source éditable" in reponse["error"]["message"]


def test_modele_lu_avec_bom_n_a_pas_l_air_d_avoir_perdu_sa_source(
    outils: dict[str, Any], tmp_path: Path
) -> None:
    """Un JSON valide lu avec un BOM (Windows) n'est pas une source absente.

    Constaté en recette : ``json.loads`` sur un texte portant ``\\ufeff`` échoue
    avec « Unexpected UTF-8 BOM », et l'agent recevait un message brut à la
    place du diagnostic de source. La lecture doit tolérer ce cas.
    """
    import json

    depot = _generer(outils)
    assert depot["ok"] is True, depot
    chemin = tmp_path / "_sources" / f"{depot['document_id']}.json"
    etat = json.loads(chemin.read_text(encoding="utf-8"))
    chemin.write_text(json.dumps(etat), encoding="utf-8-sig")

    reponse = _section(outils, depot["document_id"])
    assert reponse["ok"] is True, reponse
    assert reponse["sections"][1]["titre"] == "1. Contexte"


def test_correction_retrouve_la_source_d_une_autre_version(
    outils: dict[str, Any], service: DocumentService
) -> None:
    """Corriger la version la plus récente quand seule une autre porte la source.

    Cas réel : le premier correctif n'a pas pu partir de la v2 (source absente à
    son identifiant) et a créé un document distinct − il a laissé l'ancien
    livrable sans source. Corriger le livrable doit retrouver la source de sa
    famille (même nom, même type), pas renvoyer « pas de source éditable ».
    """
    from app.documents.sources import racine_sources, supprimer

    depot = _generer(outils)
    assert depot["ok"] is True, depot
    ancien = _section(
        outils,
        depot["document_id"],
        action="remplacer",
        section_index=2,
        contenu_html="<h1>1. Contexte</h1><p>Contexte revu.</p>",
    )
    assert ancien["ok"] is True, ancien

    # La v2 perd sa source (incident du correctif précédent) : seule la v1 l'a.
    supprimer(racine_sources(tmp_path_root(service)), depot["document_id"])

    reponse = _section(outils, ancien["document_id"])
    assert reponse["ok"] is True, reponse
    assert reponse["sections"][1]["titre"] == "1. Contexte"


def tmp_path_root(service: DocumentService) -> Path:
    """Racine de stockage du service de test (le stockage local est temporaire)."""
    return Path(service.storage.root)


def test_fragment_qui_ouvre_plusieurs_parties_refuse(outils: dict[str, Any]) -> None:
    """Une section = une partie : deux titres, c'est un appel par section."""
    depot = _generer(outils)
    reponse = _section(
        outils,
        depot["document_id"],
        action="remplacer",
        section_index=2,
        contenu_html="<h1>1. Contexte</h1><p>A</p><h1>2. Démarche</h1><p>B</p>",
    )
    assert reponse["ok"] is False
    assert "ouvre 2 parties" in reponse["error"]["message"]


def test_section_index_hors_bornes_refuse(outils: dict[str, Any]) -> None:
    depot = _generer(outils)
    reponse = _section(
        outils,
        depot["document_id"],
        action="remplacer",
        section_index=9,
        contenu_html="<h1>Trop loin</h1>",
    )
    assert reponse["ok"] is False


def test_deck_exige_une_section_par_diapositive(outils: dict[str, Any]) -> None:
    deck = (
        '<section class="cover"><h1>Parcours</h1></section>'
        '<section class="slide"><h1>5 bootcamps</h1><p>Intro.</p></section>'
    )
    depot = _generer(outils, titre="Parcours bootcamps", corps=deck, format="pptx")
    assert depot["ok"] is True, depot

    refus = _section(
        outils,
        depot["document_id"],
        action="remplacer",
        section_index=2,
        contenu_html="<p>Un paragraphe nu</p>",
    )
    assert refus["ok"] is False
    assert "section" in refus["error"]["message"]

    accepte = _section(
        outils,
        depot["document_id"],
        action="remplacer",
        section_index=2,
        contenu_html='<section class="slide"><h1>5 bootcamps</h1><p>Intro corrigée.</p></section>',
    )
    assert accepte["ok"] is True, accepte
    assert accepte["format"] == "pptx"
    assert accepte["version"] == 2
