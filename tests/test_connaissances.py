"""Base de connaissance (RAG) — découpage, lecture, index, service, outil, API.

Aucun appel réseau : l'embedding est remplacé par un vecteur déterministe, mais
l'index Qdrant est le **vrai** (répertoire temporaire) et le stockage écrit de
vrais fichiers. Un test qui passerait avec un faux index ne prouverait pas que
la recherche retrouve un passage.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Iterator
from io import BytesIO
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.orm import Session, sessionmaker

from app.agents.generaliste_readonly import DEFINITION as GENERALISTE
from app.agents.tools.connaissances import TOOL_NAME, construire_tool_connaissance
from app.application.services.connaissance_service import ConnaissanceService
from app.connaissances.decoupage import decouper
from app.connaissances.embeddings import (
    ConfigurationEmbeddingEffective,
    SondageEmbedding,
)
from app.connaissances.lecture import Segment, lire_segments
from app.connaissances.vecteurs import IndexQdrant, reinitialiser_index_processus
from app.core.errors import CarsoError, ConflictError, ValidationError
from app.domain.audit_vocabulaire import LIBELLES_ACTION
from app.domain.connaissance import DocumentConnaissance
from app.domain.enums import ActionAudit

# --- Embedding factice (aucun réseau) ---------------------------------------


class FauxEmbedder:
    """Vecteur déterministe : les textes partageant des mots se rapprochent."""

    def __init__(self, *, modele: str = "faux-modele", dimensions: int = 16) -> None:
        self._configuration = ConfigurationEmbeddingEffective(
            provider="openai_compatible",
            model=modele,
            base_url="http://localhost:11434/v1",
            dimensions=dimensions,
            api_key="cle-de-test",
            source="test",
        )

    @property
    def configuration(self) -> ConfigurationEmbeddingEffective:
        return self._configuration

    def vectoriser(self, textes: list[str]) -> list[list[float]]:
        vecteurs: list[list[float]] = []
        for texte in textes:
            vecteur = [0.0] * self._configuration.dimensions
            for mot in texte.lower().split():
                empreinte = int(hashlib.sha256(mot.encode("utf-8")).hexdigest(), 16)
                vecteur[empreinte % len(vecteur)] += 1.0
            norme = math.sqrt(sum(valeur * valeur for valeur in vecteur)) or 1.0
            vecteurs.append([valeur / norme for valeur in vecteur])
        return vecteurs


# --- Découpage et lecture ----------------------------------------------------


def test_decoupage_borne_et_conserve_la_provenance() -> None:
    texte = "\n\n".join(
        f"Paragraphe {index} : " + ("méthode CARSO et suivi qualité " * 8)
        for index in range(12)
    )
    passages = decouper(
        [Segment(texte=texte, page=7, section="Méthodologie")],
        taille=600,
        chevauchement=80,
        minimum=120,
    )
    assert len(passages) > 1
    assert [passage.position for passage in passages] == list(range(len(passages)))
    assert all(passage.page == 7 and passage.section == "Méthodologie" for passage in passages)
    assert all(len(passage.texte) <= 600 for passage in passages)
    # Le chevauchement existe : la fin d'un passage se retrouve au début du suivant.
    fin = passages[0].texte[-60:].split(" ", 1)[1]
    assert fin in passages[1].texte


def test_lecture_texte_et_xlsx(tmp_path: Path) -> None:
    fichier = tmp_path / "note.md"
    fichier.write_text("# Guide\n\nUne méthodologie.", encoding="utf-8")
    segments = lire_segments(fichier, nom_fichier="note.md")
    assert "méthodologie" in segments[0].texte

    from openpyxl import Workbook

    classeur = Workbook()
    feuille = classeur.active
    feuille.title = "Budget"
    feuille.append(["Poste", "Montant"])
    feuille.append(["Formation", 1200])
    xlsx = tmp_path / "budget.xlsx"
    classeur.save(xlsx)
    segments = lire_segments(xlsx, nom_fichier="budget.xlsx")
    assert segments[0].section == "Budget"
    assert "Formation | 1200" in segments[0].texte


def test_lecture_refuse_un_format_non_indexable(tmp_path: Path) -> None:
    fichier = tmp_path / "photo.jpg"
    fichier.write_bytes(b"\xff\xd8\xff")
    with pytest.raises(ValidationError):
        lire_segments(fichier, nom_fichier="photo.jpg")


def test_lecture_pdf_garde_la_page(tmp_path: Path) -> None:
    from fpdf import FPDF

    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=12)
    pdf.cell(0, 10, "Premiere page de methode")
    pdf.add_page()
    pdf.cell(0, 10, "Deuxieme page de resultats")
    chemin = tmp_path / "ouvrage.pdf"
    pdf.output(str(chemin))

    segments = lire_segments(chemin, nom_fichier="ouvrage.pdf")
    assert [segment.page for segment in segments] == [1, 2]
    assert "Deuxieme page" in segments[1].texte


# --- Service (SQLite + vrai Qdrant + vrai stockage) --------------------------


@pytest.fixture
def service(
    api_session_factory: sessionmaker[Session], tmp_path: Path
) -> Iterator[tuple[ConnaissanceService, Session, IndexQdrant]]:
    """Service avec un répertoire et un index dédiés (fermés en fin de test)."""
    index = IndexQdrant(tmp_path / "vecteurs")
    session = api_session_factory()
    try:
        yield (
            ConnaissanceService(
                session,
                "acteur:test",
                racine=tmp_path / "connaissances",
                index=index,
                embedder=FauxEmbedder(),
            ),
            session,
            index,
        )
    finally:
        session.close()
        index.fermer()


def _ajouter(service: ConnaissanceService, *, titre: str, contenu: str) -> DocumentConnaissance:
    return service.ajouter(
        flux=BytesIO(contenu.encode("utf-8")),
        nom_fichier=f"{titre.lower().replace(' ', '-')}.txt",
        titre=titre,
        auteur="CARSO",
        categorie="methode",
        description="Document de test",
    )


def test_bout_en_bout_ajout_indexation_recherche(
    service: tuple[ConnaissanceService, Session, IndexQdrant],
) -> None:
    svc, session, index = service
    document = _ajouter(
        svc,
        titre="Guide de suivi qualite",
        contenu=(
            "Le suivi qualite repose sur trois visites de terrain et un rapport "
            "mensuel remis a l'organisation cliente."
        ),
    )
    assert document.statut == "en_attente"

    rapport = svc.indexer_maintenant(document.id)
    session.commit()
    assert rapport.statut == "indexe"
    assert rapport.nombre_chunks == 1
    assert index.compter() == 1

    resultat = svc.rechercher("rapport mensuel de suivi qualite")
    assert resultat["nombre"] == 1
    passage = resultat["passages"][0]
    assert passage["titre"] == "Guide de suivi qualite"
    assert passage["auteur"] == "CARSO"
    assert "trois visites" in passage["extrait"]
    assert resultat["signature"] == FauxEmbedder().configuration.signature


def test_recherche_multi_formulations_groupe_par_requete(
    service: tuple[ConnaissanceService, Session, IndexQdrant],
) -> None:
    """Deux formulations ⇒ deux groupes de passages, dans l'ordre demandé."""
    svc, session, _ = service
    qualite = _ajouter(
        svc,
        titre="Suivi qualite",
        contenu="Le suivi qualite repose sur un rapport mensuel remis au client.",
    )
    collecte = _ajouter(
        svc,
        titre="Collecte des donnees",
        contenu="La collecte des donnees passe par une enquete de terrain.",
    )
    svc.indexer_maintenant(qualite.id)
    svc.indexer_maintenant(collecte.id)
    session.commit()

    lot = svc.rechercher_plusieurs(["suivi qualite", "collecte des donnees"], limite=2)

    assert [groupe["query"] for groupe in lot["requetes"]] == [
        "suivi qualite",
        "collecte des donnees",
    ]
    assert lot["requetes"][0]["passages"][0]["titre"] == "Suivi qualite"
    assert lot["requetes"][1]["passages"][0]["titre"] == "Collecte des donnees"
    assert all(groupe["nombre"] for groupe in lot["requetes"])
    # ``limite`` borne **chaque** formulation, pas le total : deux ouvrages
    # indexés, deux passages au plus par groupe.
    assert all(groupe["nombre"] <= 2 for groupe in lot["requetes"])
    assert lot["total_passages"] == sum(
        groupe["nombre"] for groupe in lot["requetes"]
    )
    assert lot["message"] is None
    assert lot["signature"] == FauxEmbedder().configuration.signature


def test_recherche_multi_formulations_un_seul_appel_d_embedding(
    service: tuple[ConnaissanceService, Session, IndexQdrant],
) -> None:
    """Regrouper ne coûte pas N appels au fournisseur d'embedding."""

    class CompteurEmbedder(FauxEmbedder):
        appels = 0

        def vectoriser(self, textes: list[str]) -> list[list[float]]:
            type(self).appels += 1
            return super().vectoriser(textes)

    svc, session, index = service
    document = _ajouter(
        svc, titre="Methode", contenu="Une methode de suivi qualite sur trois visites."
    )
    svc.indexer_maintenant(document.id)
    session.commit()

    compteur = ConnaissanceService(
        session,
        racine=svc._racine,  # noqa: SLF001 — même répertoire que le fixture
        index=index,
        embedder=CompteurEmbedder(),
    )
    lot = compteur.rechercher_plusieurs(["suivi qualite", "visites de terrain"])

    assert len(lot["requetes"]) == 2
    assert CompteurEmbedder.appels == 1


def test_recherche_multi_formulations_dedoublonne_et_borne(
    service: tuple[ConnaissanceService, Session, IndexQdrant],
) -> None:
    svc, session, index = service
    document = _ajouter(svc, titre="Ouvrage", contenu="Une methode de suivi qualite.")
    svc.indexer_maintenant(document.id)
    session.commit()

    # Même formulation à un espace près : une seule requête réellement exécutée.
    lot = svc.rechercher_plusieurs(["suivi  qualite", " suivi qualite "])
    assert [groupe["query"] for groupe in lot["requetes"]] == ["suivi qualite"]

    with pytest.raises(ValidationError):
        svc.rechercher_plusieurs([f"question {indice}" for indice in range(6)])
    with pytest.raises(ValidationError):
        svc.rechercher_plusieurs(["   "])


def test_doublon_refuse(
    service: tuple[ConnaissanceService, Session, IndexQdrant],
) -> None:
    svc, session, _ = service
    _ajouter(svc, titre="Premier", contenu="Contenu identique.")
    with pytest.raises(ConflictError):
        _ajouter(svc, titre="Second", contenu="Contenu identique.")
    session.rollback()


def test_signature_differente_refusee(
    service: tuple[ConnaissanceService, Session, IndexQdrant],
    tmp_path: Path,
) -> None:
    svc, session, index = service
    document = _ajouter(
        svc, titre="Ouvrage", contenu="Une methode de collecte des donnees."
    )
    svc.indexer_maintenant(document.id)
    session.commit()

    autre = ConnaissanceService(
        session,
        racine=tmp_path / "connaissances",
        index=index,
        embedder=FauxEmbedder(modele="autre-modele"),
    )
    with pytest.raises(ConflictError):
        autre.rechercher("collecte des donnees")


def test_suppression_retire_fiche_passages_et_fichier(
    service: tuple[ConnaissanceService, Session, IndexQdrant],
) -> None:
    svc, session, index = service
    document = _ajouter(svc, titre="A retirer", contenu="Texte a supprimer.")
    svc.indexer_maintenant(document.id)
    session.commit()
    chemin = svc._racine / document.chemin_stockage  # noqa: SLF001 — vérification du fichier
    assert chemin.is_file()

    passages = svc.supprimer(document.id)
    session.commit()
    assert passages == 1
    assert not chemin.exists()
    assert index.compter() == 0
    assert svc.lister() == []


def test_configuration_requires_un_chiffrement_pour_la_cle(
    service: tuple[ConnaissanceService, Session, IndexQdrant],
    monkeypatch: pytest.MonkeyPatch,
    sans_chiffrement: None,
) -> None:
    """Sans clé de chiffrement, enregistrer une clé est refusé (jamais en clair)."""
    svc, session, _ = service
    monkeypatch.setattr(
        "app.application.services.connaissance_service.sonder",
        lambda configuration: SondageEmbedding(
            dimensions=16,
            signature=f"{configuration.provider}:{configuration.model}:16",
            latence_ms=1,
            modele=configuration.model,
        ),
    )
    with pytest.raises(CarsoError):
        svc.configurer(
            provider="openai_compatible",
            model="faux-modele",
            base_url="http://localhost:11434/v1",
            api_key="cle",
        )
    session.rollback()


def test_configuration_mesure_puis_reindexe(
    service: tuple[ConnaissanceService, Session, IndexQdrant],
    monkeypatch: pytest.MonkeyPatch,
    cle_chiffrement: str,
) -> None:
    """Le modèle est retenu avec la dimension **mesurée**, et réindexe la base."""
    svc, session, index = service
    document = _ajouter(svc, titre="Ancien index", contenu="Un passage deja indexe.")
    svc.indexer_maintenant(document.id)
    session.commit()
    assert index.compter() == 1

    monkeypatch.setattr(
        "app.application.services.connaissance_service.sonder",
        lambda configuration: SondageEmbedding(
            dimensions=32,
            signature=f"{configuration.provider}:{configuration.model}:32",
            latence_ms=1,
            modele=configuration.model,
        ),
    )
    mesure = svc.tester_configuration(
        provider="openai_compatible",
        model="nouveau-modele",
        base_url="http://localhost:11434/v1",
        api_key="cle",
    )
    assert mesure["dimensions"] == 32

    ligne, reindexation = svc.configurer(
        provider="openai_compatible",
        model="nouveau-modele",
        base_url="http://localhost:11434/v1",
        api_key="cle",
    )
    session.commit()
    assert ligne.signature == "openai_compatible:nouveau-modele:32"
    assert reindexation is True
    # Le nouveau modèle n'a aucune dimension en commun avec l'ancien : l'index
    # est reparti de zéro et chaque ouvrage attend sa réindexation.
    assert index.compter() == 0
    session.refresh(document)
    assert document.statut == "en_attente"
    assert document.nombre_chunks == 0


# --- Contrat des agents et outil --------------------------------------------


def test_tous_les_agents_du_catalogue_ont_le_tool() -> None:
    from app.agents.catalogue import AGENT_DEFINITIONS

    assert all(TOOL_NAME in definition.tools for definition in AGENT_DEFINITIONS)


def test_outil_refuse_un_agent_hors_contrat(
    api_session_factory: sessionmaker[Session],
) -> None:
    from dataclasses import replace

    sans = replace(GENERALISTE, tools=("search_missions",))
    assert construire_tool_connaissance(sans, api_session_factory) is None


def test_outil_renvoie_des_passages_sources(
    service: tuple[ConnaissanceService, Session, IndexQdrant],
    monkeypatch: pytest.MonkeyPatch,
    api_session_factory: sessionmaker[Session],
    tmp_path: Path,
) -> None:
    svc, session, index = service
    document = _ajouter(
        svc, titre="Manuel de facilitation", contenu="Animer un atelier participatif."
    )
    svc.indexer_maintenant(document.id)
    session.commit()

    # L'outil construit son propre service : mêmes répertoire, index et modèle
    # (un second client Qdrant sur le même répertoire serait verrouillé).
    monkeypatch.setattr(
        "app.agents.tools.connaissances.ConnaissanceService",
        lambda session, actor_id=None: ConnaissanceService(
            session,
            racine=tmp_path / "connaissances",
            index=index,
            embedder=FauxEmbedder(),
        ),
    )
    tool = construire_tool_connaissance(GENERALISTE, api_session_factory)
    assert tool is not None
    resultat = tool.invoke({"query": ["animer un atelier"], "top_k": 3})
    assert resultat["ok"] is True
    assert resultat["requetes"][0]["passages"][0]["titre"] == "Manuel de facilitation"
    assert resultat["total_passages"] >= 1
    assert "consigne" in resultat


def test_outil_valide_les_formulations() -> None:
    """Les bornes sont dites au modèle (schéma), pas découvertes par une erreur."""
    from pydantic import ValidationError as PydanticValidationError

    from app.agents.tools.connaissances import RechercheConnaissanceInput

    assert RechercheConnaissanceInput.model_validate({"query": ["un sujet"]}).top_k == 5
    with pytest.raises(PydanticValidationError):
        RechercheConnaissanceInput.model_validate({"query": []})
    with pytest.raises(PydanticValidationError):
        RechercheConnaissanceInput.model_validate(
            {"query": [f"question {indice}" for indice in range(6)]}
        )


# --- Audit et API ------------------------------------------------------------


def test_vocabulaire_audit_couvre_les_actions_connaissance() -> None:
    for action in (
        ActionAudit.CONNAISSANCE_AJOUTEE,
        ActionAudit.CONNAISSANCE_MODIFIEE,
        ActionAudit.CONNAISSANCE_SUPPRIMEE,
        ActionAudit.CONNAISSANCE_REINDEXATION_DEMANDEE,
        ActionAudit.CONNAISSANCE_INDEXEE,
        ActionAudit.CONNAISSANCE_INDEXATION_ECHOUEE,
        ActionAudit.CONFIGURATION_EMBEDDING_MODIFIEE,
    ):
        assert action.value in LIBELLES_ACTION


@pytest.fixture
def base_temporaire(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Répertoire de connaissance isolé + worker désactivé pour la durée du test."""
    from app.core.config import get_settings

    racine = tmp_path / "base"
    monkeypatch.setenv("KNOWLEDGE_STORAGE_PATH", str(racine))
    monkeypatch.setenv("KNOWLEDGE_INDEXATION_ACTIVE", "0")
    # Modèle de repli : les routes construisent leur propre service, elles ne
    # peuvent pas recevoir l'embedder factice par injection.
    monkeypatch.setenv("EMBEDDING_PROVIDER", "openai_compatible")
    monkeypatch.setenv("EMBEDDING_MODEL", "faux-modele")
    monkeypatch.setenv("EMBEDDING_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("EMBEDDING_API_KEY", "cle-de-test")
    monkeypatch.setenv("EMBEDDING_DIMENSIONS", "16")
    get_settings.cache_clear()
    reinitialiser_index_processus()
    try:
        yield racine
    finally:
        reinitialiser_index_processus()
        get_settings.cache_clear()


def test_api_liste_etat_et_permissions(
    base_temporaire: Path, api_client: Any
) -> None:
    liste = api_client.get("/api/v1/connaissances")
    assert liste.status_code == 200, liste.text
    assert liste.json()["elements"] == []

    etat = api_client.get("/api/v1/connaissances/etat")
    assert etat.status_code == 200, etat.text
    corps = etat.json()
    assert corps["ouvrages"] == 0
    assert "formats_supportes" in corps

    # Le mode lab est collaborateur : la configuration d'embedding est réservée
    # à l'administration.
    refus = api_client.get("/api/v1/connaissances/configuration")
    assert refus.status_code == 403, refus.text


def test_api_ajout_multipart_puis_indexation(
    base_temporaire: Path,
    api_client: Any,
    api_session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.infrastructure.indexation_connaissances import indexer_prochain

    monkeypatch.setattr(
        "app.application.services.connaissance_service.construire_embedder",
        lambda configuration: FauxEmbedder(),
    )
    reponse = api_client.post(
        "/api/v1/connaissances",
        files={"file": ("guide.txt", b"Une methode de suivi qualite.", "text/plain")},
        data={"titre": "Guide qualite", "categorie": "methode"},
    )
    assert reponse.status_code == 201, reponse.text
    document_id = reponse.json()["id"]
    assert reponse.json()["statut"] == "en_attente"

    rapport = indexer_prochain(
        session_factory=api_session_factory, embedder=FauxEmbedder()
    )
    assert rapport is not None
    assert rapport.statut == "indexe", rapport.message

    fiche = api_client.get(f"/api/v1/connaissances/{document_id}")
    assert fiche.status_code == 200, fiche.text
    assert fiche.json()["statut"] == "indexe"

    recherche = api_client.post(
        "/api/v1/connaissances/recherche",
        json={"question": "suivi qualite", "limite": 3},
    )
    assert recherche.status_code == 200, recherche.text
    assert recherche.json()["nombre"] == 1
    assert recherche.json()["passages"][0]["titre"] == "Guide qualite"

    fichier = api_client.get(f"/api/v1/connaissances/{document_id}/fichier")
    assert fichier.status_code == 200
    assert fichier.content == b"Une methode de suivi qualite."
