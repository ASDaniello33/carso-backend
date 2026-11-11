"""Remplissage HTML par lot (positions) et retour arrière (INC-129).

`remplir_modele` accepte déjà plusieurs éléments en un seul appel, chacun avec sa
`position`. Ce qui manquait : pouvoir **annuler** un lot qui a abîmé la copie.
Le retour arrière ne détruit rien — le contenu de la version précédente est
déposé comme nouvelle version — et il se vérifie ici, sur le fichier relu.
"""

from __future__ import annotations

import io
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from docx import Document as LireDocx
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.application.dto import DocumentUploadInput
from app.application.perimetre import Perimetre
from app.application.services.document_service import DocumentService
from app.application.services.modele_document_service import ModeleDocumentService
from app.application.services.moteur_documentaire_service import MoteurDocumentaireService
from app.core.errors import ValidationError
from app.documents.storage import LocalDocumentStorage
from app.domain.base import Base
from app.domain.organization import Appel, Lot, Offre, Organisation
from tests.test_moteur_documentaire import modele_docx


@pytest.fixture
def db_session():
    moteur = create_engine("sqlite://")
    Base.metadata.create_all(moteur)
    fabrique = sessionmaker(bind=moteur)
    with fabrique() as active:
        yield active
    moteur.dispose()


@pytest.fixture
def contexte(tmp_path: Path, db_session):
    """Organisation, modèle au registre, offre, copie de travail prête."""
    stockage = LocalDocumentStorage(tmp_path / "stockage", max_bytes=8 * 1024 * 1024)
    organisation = Organisation(nom="Client Retour")
    db_session.add(organisation)
    db_session.flush()
    documents = DocumentService(
        db_session,
        storage=stockage,
        actor_id="test",
        perimetre=Perimetre(role="administrateur", utilisateur_id=uuid4(), est_complet=True),
    )
    document = documents.enregistrer_document(
        DocumentUploadInput(
            type_document="template",
            nom="modele_client.docx",
            stream=io.BytesIO(modele_docx()),
            created_by="test",
            organisation_id=organisation.id,
        )
    )
    modele = ModeleDocumentService(db_session).enregistrer_modele(
        organisation.id,
        nom="modele_client.docx",
        type_document="modele_offre_technique",
        document_template_id=document.id,
        registered_by="test",
    )
    appel = Appel(
        organisation_id=organisation.id,
        reference="AMI-RETOUR",
        titre="Appel retour",
        type="appel_a_proposition",
    )
    db_session.add(appel)
    db_session.flush()
    lot = Lot(appel_id=appel.id, numero="1", titre="Lot 1")
    db_session.add(lot)
    db_session.flush()
    offre = Offre(
        organisation_id=organisation.id,
        appel_id=appel.id,
        lot_id=lot.id,
        type="offre_technique",
        reference="OFFRE-RETOUR",
        titre="Offre retour",
        version=1,
    )
    db_session.add(offre)
    db_session.flush()
    service = MoteurDocumentaireService(
        db_session, document_service=documents, actor_id="agent_generateur_offre"
    )
    copie, _modele, _elements = service.copier_modele_dans_objet(
        "offre", offre.id, modele_id=modele.id
    )
    return {
        "documents": documents,
        "service": service,
        "offre": offre,
        "copie": copie,
    }


def _textes(contexte, document_id: str) -> list[str]:
    _nom, chemin = contexte["documents"].telecharger(UUID(document_id))
    return [paragraphe.text for paragraphe in LireDocx(str(chemin)).paragraphs]


def _identifiants(contexte, service) -> dict[str, str]:
    structure = service.structure_document("offre", contexte["offre"].id)
    return {element["texte"]: element["element_id"] for element in structure["elements"]}


def test_le_lot_html_applique_plusieurs_positions_en_une_version(contexte) -> None:
    service = contexte["service"]
    ids = _identifiants(contexte, service)
    contexte_titre = ids["1. Contexte"]
    methode = next(texte for texte in ids if texte.startswith("Méthode du client"))

    resultat = service.remplir(
        "offre",
        contexte["offre"].id,
        [
            {
                "element_id": contexte_titre,
                "contenu_html": "<p>Bandeau du client</p>",
                "position": "avant",
            },
            {
                "element_id": ids[methode],
                "contenu_html": "<h2>3. Méthodologie</h2><p>Ateliers.</p>",
            },
            {
                "element_id": ids["2. Activités"],
                "contenu_html": "<p>Annexe du lot</p>",
                "position": "apres",
            },
        ],
    )

    assert resultat["version"] == contexte["copie"].version + 1  # un seul lot, une version
    textes = _textes(contexte, resultat["document_id"])
    assert "Bandeau du client" in textes
    assert "3. Méthodologie" in textes
    assert "Annexe du lot" in textes
    # Position respectée : bandeau avant le titre visé, annexe juste après l'autre.
    assert textes.index("Bandeau du client") < textes.index("1. Contexte")
    assert textes.index("Annexe du lot") == textes.index("2. Activités") + 1
    # Le texte remplacé a disparu, le reste du modèle est intact.
    assert methode not in textes
    assert "Client : Région Sud-Ouest" in textes


def test_le_retour_arriere_restaure_la_version_precedente(contexte) -> None:
    service = contexte["service"]
    ids = _identifiants(contexte, service)
    cible = next(texte for texte in ids if texte.startswith("Méthode du client"))

    rempli = service.remplir(
        "offre",
        contexte["offre"].id,
        [{"element_id": ids[cible], "contenu_html": "<h2>Section ratée</h2>"}],
    )
    assert "Section ratée" in _textes(contexte, rempli["document_id"])

    retour = service.revenir_version_precedente("offre", contexte["offre"].id)

    assert retour["version"] == rempli["version"] + 1
    assert retour["version_restauree"] == contexte["copie"].version
    assert retour["version_annulee"] == rempli["version"]
    # Le contenu est bien revenu à l'état d'avant le lot…
    textes = _textes(contexte, retour["document_id"])
    assert "Section ratée" not in textes
    assert cible in textes
    # …et la version annulée est toujours dans l'historique.
    _nom, chemin = contexte["documents"].telecharger(UUID(rempli["document_id"]))
    assert "Section ratée" in [p.text for p in LireDocx(str(chemin)).paragraphs]


def test_le_retour_arriere_est_lineaire_et_ne_perd_rien(contexte) -> None:
    """L'annulation est une version comme une autre : l'historique ne recule pas."""
    service = contexte["service"]
    ids = _identifiants(contexte, service)
    cible = next(texte for texte in ids if texte.startswith("Méthode du client"))

    premier = service.remplir(
        "offre",
        contexte["offre"].id,
        [{"element_id": ids[cible], "contenu_html": "<h2>Premier lot</h2>"}],
    )
    second = service.remplir(
        "offre",
        contexte["offre"].id,
        [{"element_id": ids[cible], "contenu_html": "<h2>Second lot</h2>"}],
    )
    assert second["version"] == premier["version"] + 1

    annule = service.revenir_version_precedente("offre", contexte["offre"].id)
    assert annule["version_restauree"] == premier["version"]
    assert annule["version"] == second["version"] + 1
    textes = _textes(contexte, annule["document_id"])
    assert "Premier lot" in textes
    assert "Second lot" not in textes

    # Toutes les versions restent dans l'historique : rien n'est écrasé.
    for document_id in (premier["document_id"], second["document_id"]):
        _nom, chemin = contexte["documents"].telecharger(UUID(document_id))
        assert chemin.read_bytes().startswith(b"PK")


def test_le_retour_arriere_sans_antecedent_est_refuse(contexte) -> None:
    service = contexte["service"]

    with pytest.raises(ValidationError, match="pas de version antérieure"):
        service.revenir_version_precedente("offre", contexte["offre"].id)


def test_apres_retour_arriere_le_lot_se_refait(contexte) -> None:
    service = contexte["service"]
    ids = _identifiants(contexte, service)
    cible = next(texte for texte in ids if texte.startswith("Méthode du client"))

    service.remplir(
        "offre",
        contexte["offre"].id,
        [{"element_id": ids[cible], "contenu_html": "<h2>Mauvais endroit</h2>"}],
    )
    service.revenir_version_precedente("offre", contexte["offre"].id)
    ids = _identifiants(contexte, service)  # relus, pas réutilisés de mémoire

    refait = service.remplir(
        "offre",
        contexte["offre"].id,
        [{"element_id": ids[cible], "contenu_html": "<h2>3. Méthodologie</h2>"}],
    )

    textes = _textes(contexte, refait["document_id"])
    assert "3. Méthodologie" in textes
    assert "Mauvais endroit" not in textes


def test_restaurer_une_version_precise(contexte) -> None:
    service = contexte["service"]
    ids = _identifiants(contexte, service)
    cible = next(texte for texte in ids if texte.startswith("Méthode du client"))

    premier = service.remplir(
        "offre",
        contexte["offre"].id,
        [{"element_id": ids[cible], "contenu_html": "<h2>Premier lot</h2>"}],
    )
    service.remplir(
        "offre",
        contexte["offre"].id,
        [{"element_id": ids[cible], "contenu_html": "<h2>Second lot</h2>"}],
    )

    retour = service.revenir_version(
        "offre", contexte["offre"].id, premier["version"]
    )

    assert retour["version_restauree"] == premier["version"]
    assert retour["version"] == premier["version"] + 2  # deux éditions puis le retour
    textes = _textes(contexte, retour["document_id"])
    assert "Premier lot" in textes
    assert "Second lot" not in textes
    assert retour["version_restauree"] in retour["versions_disponibles"]


def test_restaurer_la_version_d_origine(contexte) -> None:
    service = contexte["service"]
    ids = _identifiants(contexte, service)
    cible = next(texte for texte in ids if texte.startswith("Méthode du client"))

    service.remplir(
        "offre",
        contexte["offre"].id,
        [{"element_id": ids[cible], "contenu_html": "<h2>Lot unique</h2>"}],
    )
    retour = service.revenir_version("offre", contexte["offre"].id, 1)

    assert retour["version_restauree"] == contexte["copie"].version
    textes = _textes(contexte, retour["document_id"])
    assert "Lot unique" not in textes
    assert cible in textes


def test_une_version_inconnue_est_refusee_avec_la_liste(contexte) -> None:
    service = contexte["service"]
    ids = _identifiants(contexte, service)
    cible = next(texte for texte in ids if texte.startswith("Méthode du client"))
    service.remplir(
        "offre",
        contexte["offre"].id,
        [{"element_id": ids[cible], "contenu_html": "<h2>Lot</h2>"}],
    )

    with pytest.raises(ValidationError, match="versions disponibles : 1, 2"):
        service.revenir_version("offre", contexte["offre"].id, 42)


def test_restaurer_la_version_courante_est_refuse(contexte) -> None:
    with pytest.raises(ValidationError, match="déjà celle du document de travail"):
        contexte["service"].revenir_version(
            "offre", contexte["offre"].id, contexte["copie"].version
        )


def test_l_historique_est_lisible_avec_la_structure(contexte) -> None:
    service = contexte["service"]
    ids = _identifiants(contexte, service)
    cible = next(texte for texte in ids if texte.startswith("Méthode du client"))
    service.remplir(
        "offre",
        contexte["offre"].id,
        [{"element_id": ids[cible], "contenu_html": "<h2>Lot</h2>"}],
    )
    service.revenir_version_precedente("offre", contexte["offre"].id)

    historique = service.structure_document("offre", contexte["offre"].id)["historique"]

    assert [entree["version"] for entree in historique] == [3, 2, 1]
    assert historique[0]["courante"] is True
    assert [entree["courante"] for entree in historique].count(True) == 1
    assert historique[0]["nature"] == "retour_arriere"
    assert historique[1]["nature"] == "remplissage_html"


def test_l_outil_de_retour_arriere_est_expose(contexte) -> None:
    from app.tools.documents import build_document_tools
    from app.tools.permissions import AgentIdentity, PermissionPolicy

    outils = {
        outil.name: outil
        for outil in build_document_tools(
            identite=AgentIdentity(agent_id="agent_assistant_formateur"),
            policy=PermissionPolicy(allowed_capabilities=frozenset({"document_propose"})),
            service_factory=lambda: contexte["documents"],
        )
    }
    assert "revenir_version_document" in outils

    ids = _identifiants(contexte, contexte["service"])
    cible = next(texte for texte in ids if texte.startswith("Méthode du client"))
    contexte["service"].remplir(
        "offre",
        contexte["offre"].id,
        [{"element_id": ids[cible], "contenu_html": "<h2>Section ratée</h2>"}],
    )

    sortie = outils["revenir_version_document"].invoke(
        {"ancre": "offre", "entity_id": str(contexte["offre"].id)}
    )

    assert sortie["ok"] is True, sortie
    assert sortie["version_restauree"] == contexte["copie"].version
    assert "Section ratée" not in _textes(contexte, sortie["document_id"])

    # La version ciblée traverse aussi l'outil : restaurer la version d'origine
    # est refusé une seconde fois, son contenu étant déjà celui du document.
    cible_version = outils["revenir_version_document"].invoke(
        {
            "ancre": "offre",
            "entity_id": str(contexte["offre"].id),
            "version": contexte["copie"].version,
        }
    )
    assert cible_version["ok"] is False
    assert "identique" in cible_version["error"]["message"]
