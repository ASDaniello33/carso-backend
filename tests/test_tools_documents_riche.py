"""Tools documentaires enrichis — spec, validation, référence, aperçu (ADR 0005).

Le contrat attendu par un agent est vérifié de bout en bout :

```text
document_spec → generer_document → validation → render_document → pages lisibles
document de référence → analyze_document_reference → style réutilisable
```

Tout passe par les vraies couches (service + stockage local temporaire + SQLite) :
aucun mock de la génération, du rendu ou de la validation.
"""

from __future__ import annotations

import io
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.application.dto import DocumentUploadInput
from app.application.services import DocumentService
from app.documents.storage import LocalDocumentStorage
from app.domain.base import Base
from app.domain.organization import Organisation
from app.tools.documents import build_document_tools
from app.tools.permissions import AgentIdentity, PermissionPolicy
from tests.document_fixtures import build_docx_riche, build_reference_stylisee

IDENTITE = AgentIdentity(agent_id="agent_generateur_offre")
POLICY = PermissionPolicy(allowed_capabilities=frozenset({"document_read", "document_propose"}))

_MOTS = (
    "formation",
    "bénéficiaires",
    "méthodologie",
    "séquence",
    "formateurs",
    "logistique",
    "sessions",
    "évaluation",
    "compétences",
    "quartiers",
    "partenaires",
    "calendrier",
)


def _texte(nb_mots: int, decalage: int) -> str:
    return " ".join(_MOTS[(index + decalage) % len(_MOTS)] for index in range(nb_mots))


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


def _tools(service: DocumentService) -> dict[str, Any]:
    outils = build_document_tools(
        identite=IDENTITE, policy=POLICY, service_factory=lambda: service
    )
    return {outil.name: outil for outil in outils}


def _offre_spec(*, complet: bool = True, **metadata: Any) -> dict[str, Any]:
    """Spec d'offre technique conforme (ou amputée pour tester la validation)."""
    sections = (
        ("Contexte", 0),
        ("Objectifs", 1),
        ("Démarche", 2),
        ("Moyens", 3),
        ("Planning", 4),
        ("Résultats attendus", 5),
        ("Conclusion", 6),
    )
    blocs: list[dict[str, Any]] = []
    for index, (titre, decalage) in enumerate(sections):
        if not complet and index > 1:
            break
        blocs.append({"type": "titre", "texte": titre})
        blocs.append({"type": "paragraphe", "texte": _texte(60, decalage * 7)})
        if index == 4:
            blocs.append(
                {
                    "type": "tableau",
                    "entete": ["Phase", "Durée", "Montant"],
                    "lignes": [
                        ["Préparation", "2 semaines", "4 000,00"],
                        ["Exécution", "6 semaines", "9 500,00"],
                        ["Total", "8 semaines", "13 500,00"],
                    ],
                    "total_ligne": 2,
                }
            )
    base_meta: dict[str, Any] = {
        "titre": "Offre technique — lot 2",
        "reference": "CARSO-2026-100",
        "organisation": "CARSO",
        "date_document": date(2026, 9, 21).isoformat(),
        "type_document": "offre_technique",
    }
    base_meta.update(metadata)
    return {"metadata": base_meta, "profil": "offre_technique", "blocs": blocs}


def _deposer_reference(service: DocumentService, organisation: Organisation) -> str:
    """Dépose un document de référence stylisé (DOCX) et renvoie son identifiant."""
    document = service.enregistrer_document(
        DocumentUploadInput(
            type_document="template",
            nom="reference_client.docx",
            stream=io.BytesIO(build_reference_stylisee()),
            created_by="test",
            organisation_id=organisation.id,
        )
    )
    return str(document.id)


# --- generer_document : chemin structuré --------------------------------------


def test_generation_structurer_produit_docx_et_pdf(
    service: DocumentService, organisation: Organisation
) -> None:
    outil = _tools(service)["generer_document"]
    sortie = outil.invoke(
        {
            "format": "docx",
            "nom_fichier": "offre_lot2",
            "type_document": "offre",
            "ancre": "organisation",
            "entity_id": str(organisation.id),
            "propose_par": IDENTITE.agent_id,
            "document_spec": _offre_spec(),
        }
    )

    assert sortie["statut"] == "proposed"
    assert sortie["documents"]["format"] == "docx"
    assert sortie["documents"]["pdf"]  # livrable lisible (donc prévisualisable)
    assert sortie["validation"]["structure"] == "passed"
    assert sortie["validation"]["contenu"] == "passed"
    assert sortie["warnings"] == []

    principal = service.obtenir(_uuid(sortie["documents"]["principal"]))
    jumeau = service.obtenir(_uuid(sortie["documents"]["pdf"]))
    assert principal.nom.endswith(".docx")
    assert jumeau.nom.endswith(".pdf")
    assert principal.offre_id is None and principal.organisation_id == organisation.id
    assert principal.storage_path == jumeau.storage_path.replace(".pdf", ".docx")


def test_generation_structurer_signale_les_erreurs_de_validation(
    service: DocumentService, organisation: Organisation
) -> None:
    """Un livrable incomplet est produit **et** signalé : jamais présenté comme bon."""
    outil = _tools(service)["generer_document"]
    sortie = outil.invoke(
        {
            "format": "pdf",
            "nom_fichier": "offre_incomplete",
            "type_document": "offre",
            "ancre": "organisation",
            "entity_id": str(organisation.id),
            "propose_par": IDENTITE.agent_id,
            "document_spec": _offre_spec(complet=False),
        }
    )
    assert sortie["validation"]["ok"] is False
    assert sortie["validation"]["structure"] == "failed"
    assert any("S001" in message for message in sortie["validation"]["erreurs"])
    assert "PAS conforme" in sortie["message"]


def test_generation_structurer_refuse_un_spec_invalide(
    service: DocumentService, organisation: Organisation
) -> None:
    outil = _tools(service)["generer_document"]
    invalide = _offre_spec()
    invalide["blocs"].append({"type": "bloc_inconnu", "texte": "x"})
    sortie = outil.invoke(
        {
            "format": "pdf",
            "nom_fichier": "offre_ko",
            "type_document": "offre",
            "ancre": "organisation",
            "entity_id": str(organisation.id),
            "propose_par": IDENTITE.agent_id,
            "document_spec": invalide,
        }
    )
    assert sortie["ok"] is False
    assert sortie["error"]["code"] == "business_validation_error"
    champs = {
        erreur["champ"] for erreur in sortie["error"]["details"]["erreurs"]
    }
    assert any("blocs" in champ for champ in champs)


def test_generation_simple_reste_disponible(
    service: DocumentService, organisation: Organisation
) -> None:
    """Le contrat historique (titre + paragraphes + tableau) n'est pas cassé."""
    outil = _tools(service)["generer_document"]
    sortie = outil.invoke(
        {
            "format": "docx",
            "nom_fichier": "note_simple",
            "type_document": "autre",
            "ancre": "organisation",
            "entity_id": str(organisation.id),
            "propose_par": IDENTITE.agent_id,
            "titre": "Note interne",
            "paragraphes": ["Contenu court."],
            "tableau": [["A", "B"], ["1", "2"]],
        }
    )
    assert sortie["statut"] == "proposed"
    assert sortie["document_id"]


def test_generation_refusee_sans_spec_ni_titre(service: DocumentService) -> None:
    outil = _tools(service)["generer_document"]
    sortie = outil.invoke(
        {
            "format": "pdf",
            "nom_fichier": "vide",
            "type_document": "autre",
            "propose_par": IDENTITE.agent_id,
        }
    )
    assert sortie["ok"] is False
    assert sortie["error"]["code"] == "business_validation_error"


# --- valider_document ---------------------------------------------------------


def test_valider_document_ok(service: DocumentService) -> None:
    outil = _tools(service)["valider_document"]
    sortie = outil.invoke({"document_spec": _offre_spec()})
    assert sortie["ok"] is True
    assert sortie["validation"]["profil"] == "offre_technique"
    assert sortie["validation"]["structure"] == "passed"
    assert sortie["findings"] == []


def test_valider_document_liste_les_constats(service: DocumentService) -> None:
    outil = _tools(service)["valider_document"]
    sortie = outil.invoke({"document_spec": _offre_spec(complet=False)})
    codes = {constat["code"] for constat in sortie["findings"]}
    assert "S001" in codes  # sections attendues absentes
    assert sortie["findings"][0]["gravite"] == "erreur"
    assert sortie["findings"][0]["conseil"]


def test_valider_document_portees_selectives(service: DocumentService) -> None:
    outil = _tools(service)["valider_document"]
    sortie = outil.invoke({"document_spec": _offre_spec(), "portees": ["style"]})
    assert set(sortie["validation"]) >= {"structure", "contenu", "style"}
    assert sortie["validation"]["structure"] == "skipped"
    assert sortie["resume"]["portees"] == ["style"]


# --- analyze_document_reference ----------------------------------------------


def test_analyse_reference_rend_plan_et_style(
    service: DocumentService, organisation: Organisation
) -> None:
    reference_id = _deposer_reference(service, organisation)
    outil = _tools(service)["analyze_document_reference"]
    sortie = outil.invoke({"document_id": reference_id, "niveau": "complet"})

    assert sortie["format"] == "docx"
    structure = sortie["content_structure"]
    assert structure["sections"]
    style = sortie["style_spec"]
    # Le format est **mesuré** sur la référence (python-docx produit du Letter :
    # le style extrait le dit, il ne suppose pas A4).
    assert style["page"]["format"] in ("A4", "Letter")
    assert style["typographie"] or style["typographie"].get("titres")
    # Le contenu de la référence n'est jamais recopié dans l'analyse de style.
    assert "texte" not in str(style)


def test_analyse_reference_refuse_un_niveau_inconnu(
    service: DocumentService, organisation: Organisation
) -> None:
    reference_id = _deposer_reference(service, organisation)
    outil = _tools(service)["analyze_document_reference"]
    sortie = outil.invoke({"document_id": reference_id, "niveau": "integral"})
    assert sortie["ok"] is False
    assert sortie["error"]["code"] == "business_validation_error"


def test_style_reference_reutilise_dans_une_generation(
    service: DocumentService, organisation: Organisation
) -> None:
    """« Utilise le style de ce document » : le style extrait est réutilisable."""
    from app.documents.reference import style_depuis_reference

    reference_id = _deposer_reference(service, organisation)
    analyse = _tools(service)["analyze_document_reference"].invoke(
        {"document_id": reference_id, "niveau": "complet"}
    )
    style = style_depuis_reference(analyse["style_spec"])
    assert style.nom  # style exploitable, indépendant du contenu source

    spec = _offre_spec()
    spec["style"] = style.model_dump()
    sortie = _tools(service)["generer_document"].invoke(
        {
            "format": "pdf",
            "nom_fichier": "offre_style_reference",
            "type_document": "offre",
            "ancre": "organisation",
            "entity_id": str(organisation.id),
            "propose_par": IDENTITE.agent_id,
            "document_spec": spec,
        }
    )
    assert sortie.get("error") is None, sortie.get("error", {}).get("details")
    assert sortie["validation"]["structure"] == "passed"


# --- render_document (aperçu visuel) -----------------------------------------


def test_render_document_produit_les_pages(
    service: DocumentService, organisation: Organisation
) -> None:
    generation = _tools(service)["generer_document"].invoke(
        {
            "format": "pdf",
            "nom_fichier": "offre_apercu",
            "type_document": "offre",
            "ancre": "organisation",
            "entity_id": str(organisation.id),
            "propose_par": IDENTITE.agent_id,
            "document_spec": _offre_spec(),
        }
    )
    document_id = generation["documents"]["principal"]

    sortie = _tools(service)["render_document"].invoke({"document_id": document_id})
    assert sortie["ok"] is True
    assert sortie["nb_pages"] >= 1
    assert sortie["pages"] == list(range(1, sortie["nb_pages_rendues"] + 1))
    assert "afficher_apercu_document" in sortie["message"]

    # Chaque page est réellement servie par la couche de service (route HTTP).
    nom, chemin = service.chemin_page_apercu(_uuid(document_id), 1)
    assert chemin.is_file()
    assert chemin.read_bytes().startswith(b"\x89PNG")
    assert nom.endswith("-p01.png")
    assert "/previews/" in chemin.as_posix()


def test_render_document_est_idempotent(
    service: DocumentService, organisation: Organisation, storage: LocalDocumentStorage
) -> None:
    """Un second aperçu réutilise les images : rien n'est réécrit."""
    generation = _tools(service)["generer_document"].invoke(
        {
            "format": "pdf",
            "nom_fichier": "offre_apercu_2",
            "type_document": "offre",
            "ancre": "organisation",
            "entity_id": str(organisation.id),
            "propose_par": IDENTITE.agent_id,
            "document_spec": _offre_spec(),
        }
    )
    document_id = generation["documents"]["principal"]
    apercu = service.apercu_document(_uuid(document_id))
    assert all(page.cree for page in apercu.pages)

    second = service.apercu_document(_uuid(document_id))
    assert second.pages and not any(page.cree for page in second.pages)
    assert [page.chemin_logique for page in second.pages] == [
        page.chemin_logique for page in apercu.pages
    ]


def test_render_document_refuse_un_docx(
    service: DocumentService, organisation: Organisation
) -> None:
    """Limite explicite : sans moteur bureautique, un DOCX n'est pas rendu."""
    generation = _tools(service)["generer_document"].invoke(
        {
            "format": "docx",
            "nom_fichier": "offre_docx",
            "type_document": "offre",
            "ancre": "organisation",
            "entity_id": str(organisation.id),
            "propose_par": IDENTITE.agent_id,
            "document_spec": _offre_spec(),
        }
    )
    sortie = _tools(service)["render_document"].invoke(
        {"document_id": generation["documents"]["principal"]}
    )
    assert sortie["ok"] is False
    assert sortie["error"]["code"] == "business_validation_error"
    assert "PDF" in sortie["error"]["message"]


def test_apercu_page_absente_not_found(service: DocumentService) -> None:
    import uuid as module_uuid

    from app.core.errors import NotFoundError

    with pytest.raises(NotFoundError):
        service.chemin_page_apercu(module_uuid.uuid4(), 1)


def test_lecture_structuree_dune_reference_riche(
    service: DocumentService, organisation: Organisation
) -> None:
    """Le document déposé reste lisible en structure (outil de lecture)."""
    document = service.enregistrer_document(
        DocumentUploadInput(
            type_document="template",
            nom="modele_riche.docx",
            stream=io.BytesIO(build_docx_riche()),
            created_by="test",
            organisation_id=organisation.id,
        )
    )
    sortie = _tools(service)["get_document_structure"].invoke({"document_id": str(document.id)})
    assert sortie["format"] == "docx"
    assert sortie["hierarchie"]
    assert sortie["tableaux"]


def _uuid(valeur: Any) -> Any:
    from uuid import UUID

    return UUID(str(valeur))
