"""La génération libre est fermée quand un modèle doit être copié (INC-129).

L'agent produisait des offres avec `generer_document_professionnel` alors que le
client avait déposé son modèle : le fichier sortait conforme au thème CARSO, pas
au modèle. Le prompt l'interdisait déjà ; la règle est désormais portée par le
service, donc un appel mal orienté échoue avec un message qui nomme la chaîne à
suivre (copier, remplacer, remplir).
"""

from __future__ import annotations

import io
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.application.dto import DocumentUploadInput
from app.application.services.document_service import DocumentService
from app.application.services.modele_document_service import ModeleDocumentService
from app.application.services.moteur_documentaire_service import MoteurDocumentaireService
from app.core.errors import ValidationError
from app.documents.storage import LocalDocumentStorage
from app.domain.base import Base
from app.domain.enums import StatutMission
from app.domain.execution import Mission
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


def _outils(documents):
    from app.tools.documents import build_document_tools
    from app.tools.permissions import AgentIdentity, PermissionPolicy

    return {
        outil.name: outil
        for outil in build_document_tools(
            identite=AgentIdentity(agent_id="agent_generateur_offre"),
            policy=PermissionPolicy(
                allowed_capabilities=frozenset({"document_propose"})
            ),
            service_factory=lambda: documents,
        )
    }


def _contexte(
    tmp_path: Path,
    db_session,
    *,
    avec_modele: bool,
    avec_copie: bool = False,
    type_offre: str = "offre_technique",
    type_modele: str = "modele_offre_technique",
):
    """Organisation, offre, et — au choix — modèle au registre et copie de travail.

    ``type_offre`` / ``type_modele`` permettent de vérifier la correspondance :
    un modèle déposé pour un type d'offre ne couvre pas l'autre (constat
    ``OFFRE-FIN-L01``, où le modèle technique du FID fermait l'offre
    financière).
    """
    stockage = LocalDocumentStorage(tmp_path / "stockage", max_bytes=8 * 1024 * 1024)
    organisation = Organisation(nom="Client Modèle")
    db_session.add(organisation)
    db_session.flush()
    documents = DocumentService(db_session, storage=stockage, actor_id="test")
    modele_id = None
    if avec_modele:
        document = documents.enregistrer_document(
            DocumentUploadInput(
                type_document="template",
                nom="modele_client.docx",
                stream=io.BytesIO(modele_docx()),
                created_by="test",
                organisation_id=organisation.id,
            )
        )
        modele_id = ModeleDocumentService(db_session).enregistrer_modele(
            organisation.id,
            nom="modele_client.docx",
            type_document=type_modele,
            document_template_id=document.id,
            registered_by="test",
        ).id

    appel = Appel(
        organisation_id=organisation.id,
        reference="AMI-COPIER",
        titre="Appel à copier",
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
        type=type_offre,
        reference="OFFRE-COPIER",
        titre="Offre à copier",
        version=1,
    )
    db_session.add(offre)
    db_session.flush()

    service = MoteurDocumentaireService(
        db_session, document_service=documents, actor_id="agent_generateur_offre"
    )
    if avec_copie:
        service.copier_modele_dans_objet("offre", offre.id, modele_id=modele_id)
    return {
        "documents": documents,
        "service": service,
        "organisation": organisation,
        "offre": offre,
        "modele_id": modele_id,
        "db": db_session,
    }


def _payload(context, **extra):
    corps = {
        "titre": "Offre technique",
        "contenu_html": "<h1>Offre</h1><p>Contenu.</p>",
        "format": "docx",
        "nom_fichier": "offre_technique.docx",
        "type_document": "offre",
        "propose_par": "agent_generateur_offre",
        "ancre": "offre",
        "entity_id": contexte_id(context),
    }
    corps.update(extra)
    return corps


def contexte_id(context):
    """Identifiant de l'offre du contexte (le payload accepte un UUID)."""
    return context["offre"].id


def test_une_offre_avec_modele_ne_se_genere_pas(tmp_path, db_session) -> None:
    contexte = _contexte(tmp_path, db_session, avec_modele=True)

    with pytest.raises(ValidationError, match="ne se génère à neuf"):
        contexte["service"].exiger_copie_modele("offre", contexte["offre"].id)

    # Le message nomme le modèle déposé et la chaîne à suivre.
    try:
        contexte["service"].exiger_copie_modele("offre", contexte["offre"].id)
    except ValidationError as erreur:
        assert erreur.details["modeles"][0]["modele_id"] == str(contexte["modele_id"])
        assert "copier_modele_document" in erreur.details["outils"]


def test_une_offre_sans_modele_garde_la_generation_libre(tmp_path, db_session) -> None:
    contexte = _contexte(tmp_path, db_session, avec_modele=False)

    # Aucun refus : le cas sans modèle reste couvert par la génération pro.
    contexte["service"].exiger_copie_modele("offre", contexte["offre"].id)


def test_la_copie_existante_interdit_un_second_document(tmp_path, db_session) -> None:
    contexte = _contexte(tmp_path, db_session, avec_modele=True, avec_copie=True)

    with pytest.raises(ValidationError, match="a déjà sa copie du modèle"):
        contexte["service"].exiger_copie_modele("offre", contexte["offre"].id)


def test_un_autre_type_de_document_reste_possible(tmp_path, db_session) -> None:
    contexte = _contexte(tmp_path, db_session, avec_modele=True, avec_copie=True)

    # Une annexe (rapport, lettre) n'est pas la copie : elle peut être générée.
    contexte["service"].exiger_copie_modele(
        "offre", contexte["offre"].id, type_document="rapport"
    )


def test_la_copie_d_une_mission_interdit_le_meme_type(tmp_path, db_session) -> None:
    contexte = _contexte(tmp_path, db_session, avec_modele=True)
    mission = Mission(
        organisation_id=contexte["organisation"].id,
        reference="MISSION-COPIER",
        titre="Formation à copier",
        statut=StatutMission.EN_PLANIFICATION.value,
    )
    contexte["db"].add(mission)
    contexte["db"].flush()
    contexte["service"].copier_modele_dans_objet("mission", mission.id)

    with pytest.raises(ValidationError, match="a déjà sa copie du modèle"):
        contexte["service"].exiger_copie_modele(
            "mission", mission.id, type_document="autre"
        )
    # La fiche de présence n'est pas la copie : la génération reste ouverte.
    contexte["service"].exiger_copie_modele(
        "mission", mission.id, type_document="fiche_presence"
    )


def test_l_outil_professionnel_refuse_une_offre_a_modele(tmp_path, db_session) -> None:
    contexte = _contexte(tmp_path, db_session, avec_modele=True)
    outils = _outils(contexte["documents"])

    sortie = outils["generer_document_professionnel"].invoke(_payload(contexte))

    assert sortie["ok"] is False
    assert "ne se génère à neuf" in sortie["error"]["message"]
    assert sortie["error"]["details"]["modeles"][0]["nom"] == "modele_client.docx"


def test_l_outil_html_refuse_aussi_une_offre_a_modele(tmp_path, db_session) -> None:
    contexte = _contexte(tmp_path, db_session, avec_modele=True)
    outils = _outils(contexte["documents"])
    corps = _payload(contexte, html="<html><body><p>Offre</p></body></html>")
    # Le schéma du tool HTML est strict : ``contenu_html`` est le nom du champ
    # (``html``), et ``titre`` n'existe pas de ce côté. Le payload doit être
    # valide, sinon l'erreur observée serait celle de la validation d'entrée et
    # non le refus de génération qu'on veut vérifier.
    corps.pop("contenu_html")
    corps.pop("titre", None)

    sortie = outils["generer_document_html"].invoke(corps)

    assert sortie["ok"] is False
    assert "copie du modèle" in sortie["error"]["message"] or (
        "ne se génère à neuf" in sortie["error"]["message"]
    )


def test_l_outil_professionnel_reste_ouvert_sans_modele(tmp_path, db_session) -> None:
    contexte = _contexte(tmp_path, db_session, avec_modele=False)
    outils = _outils(contexte["documents"])

    sortie = outils["generer_document_professionnel"].invoke(_payload(contexte))

    # Sans modèle, la génération professionnelle suit son cours (dépôt proposé).
    assert "ne se génère à neuf" not in str(sortie.get("error") or "")


def test_un_document_sans_ancre_n_est_pas_concerne(tmp_path, db_session) -> None:
    contexte = _contexte(tmp_path, db_session, avec_modele=True)
    outils = _outils(contexte["documents"])
    payload = _payload(contexte)
    for cle in ("ancre", "entity_id"):
        payload.pop(cle)

    sortie = outils["generer_document_professionnel"].invoke(payload)

    assert "ne se génère à neuf" not in str(sortie.get("error") or "")


def test_un_modele_d_un_autre_type_d_offre_ne_bloque_pas(tmp_path, db_session) -> None:
    """Le modèle technique du client ne ferme pas l'offre financière.

    Constat de terrain (offre ``OFFRE-FIN-L01``, organisation FID) : le refus
    citait le modèle technique déposé et invitait à le copier dans une offre
    financière — un gabarit qui ne lui correspond pas. La règle ne vaut que
    pour le type d'offre que le modèle couvre.
    """
    contexte = _contexte(
        tmp_path,
        db_session,
        avec_modele=True,
        type_offre="offre_financiere",
        type_modele="modele_offre_technique",
    )

    # Aucun refus : la génération libre reste le bon chemin pour ce type.
    contexte["service"].exiger_copie_modele("offre", contexte["offre"].id)

    outils = _outils(contexte["documents"])
    sortie = outils["generer_document_professionnel"].invoke(_payload(contexte))
    assert "ne se génère à neuf" not in str(sortie.get("error") or "")


def test_le_modele_du_meme_type_d_offre_bloque(tmp_path, db_session) -> None:
    """Le pendant : le modèle financier ferme bien l'offre financière."""
    contexte = _contexte(
        tmp_path,
        db_session,
        avec_modele=True,
        type_offre="offre_financiere",
        type_modele="modele_offre_financiere",
    )

    with pytest.raises(ValidationError, match="ne se génère à neuf") as refus:
        contexte["service"].exiger_copie_modele("offre", contexte["offre"].id)
    assert refus.value.details["modeles"][0]["type_document"] == "modele_offre_financiere"


def test_un_modele_generique_couvre_les_deux_types(tmp_path, db_session) -> None:
    """Un modèle générique (``offre``) s'applique à toute offre."""
    contexte = _contexte(
        tmp_path,
        db_session,
        avec_modele=True,
        type_offre="offre_financiere",
        type_modele="offre",
    )

    with pytest.raises(ValidationError, match="ne se génère à neuf"):
        contexte["service"].exiger_copie_modele("offre", contexte["offre"].id)


def test_une_copie_issue_d_un_autre_type_n_empeche_pas_la_generation(
    tmp_path, db_session
) -> None:
    """La copie technique déjà déposée ne ferme pas l'offre financière.

    État réel constaté sur ``OFFRE-FIN-L01`` : trois versions de
    ``modele-OFFRE-FIN-L01.docx`` copiées depuis ``Model_offre_technique_FID``.
    Cette copie n'est pas le document d'une offre financière : elle ne doit
    plus bloquer la production du bon livrable.
    """
    contexte = _contexte(
        tmp_path,
        db_session,
        avec_modele=True,
        avec_copie=True,
        type_offre="offre_financiere",
        type_modele="modele_offre_technique",
    )

    contexte["service"].exiger_copie_modele("offre", contexte["offre"].id)

    outils = _outils(contexte["documents"])
    sortie = outils["generer_document_professionnel"].invoke(_payload(contexte))
    assert "a déjà sa copie du modèle" not in str(sortie.get("error") or "")
    assert "ne se génère à neuf" not in str(sortie.get("error") or "")


def test_une_copie_du_bon_type_bloque_toujours(tmp_path, db_session) -> None:
    """La copie du modèle du même type, elle, reste le document à éditer."""
    contexte = _contexte(
        tmp_path,
        db_session,
        avec_modele=True,
        avec_copie=True,
        type_offre="offre_financiere",
        type_modele="modele_offre_financiere",
    )

    with pytest.raises(ValidationError, match="a déjà sa copie du modèle"):
        contexte["service"].exiger_copie_modele("offre", contexte["offre"].id)


def test_une_ancre_inconnue_repond_introuvable(tmp_path, db_session) -> None:
    contexte = _contexte(tmp_path, db_session, avec_modele=True)
    outils = _outils(contexte["documents"])

    sortie = outils["generer_document_professionnel"].invoke(
        _payload(contexte, entity_id=uuid4())
    )

    assert sortie["ok"] is False
    assert sortie["error"]["code"] in ("not_found", "business_validation_error")
