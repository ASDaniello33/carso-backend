"""Chaîne de l'agent générateur d'offre — réception → offres → documents.

L'agent analyseur d'appel à proposition a été supprimé : ses responsabilités
sont désormais portées par ``agent_generateur_offre``. Ce fichier fige la
chaîne complète confirmée avec CARSO :

```text
Appel reçu (proposition OU manifestation d'intérêt)
  ↓ enregistrer_appel + rattachement du document source
  ↓ submit_rfp_extraction (zone *proposal*)
  ↓ validation humaine → lots officiels
  ↓ offres par type (technique / financière / autre)
  ↓ documents rattachés à l'offre, téléchargeables
  ↓ mission possible, créée depuis les offres approuvées d'un lot
```

Tout passe par les **tools** de l'agent (Agent → Tool → Service), dans leur
propre transaction : ce que le modèle peut faire est exactement ce que ces
tests exercent. Aucun modèle, aucun réseau.
"""

from __future__ import annotations

import io
import json
from collections.abc import Iterator
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.agents.generateur_offre import DEFINITION, AgentGenerateurOffre, build_generateur
from app.application.dto import (
    AppelAPropositionInput,
    DocumentUploadInput,
    LieuInput,
    MissionDepuisOffresInput,
)
from app.application.services import (
    AppelAPropositionService,
    DocumentService,
    LieuService,
    MissionService,
    OffreService,
)
from app.application.trace import Decision
from app.core.config import get_settings
from app.core.errors import ConflictError
from app.domain.document import Document
from app.domain.enums import (
    StatutAppelAProposition,
    StatutDocument,
    StatutOffre,
    TypeAppel,
    TypeDocument,
    TypeOffre,
)
from app.domain.organization import AppelAProposition, Lot, Offre, Organisation
from app.tools.base import TypedTool

# --- Fixtures -------------------------------------------------------------------


@pytest.fixture
def storage_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Racine documentaire temporaire (les tools écrivent de vrais fichiers)."""
    racine = tmp_path / "carso"
    monkeypatch.setenv("STORAGE_ROOT", str(racine))
    get_settings.cache_clear()
    yield racine
    get_settings.cache_clear()


@pytest.fixture
def factory(api_session_factory: sessionmaker[Session]) -> sessionmaker[Session]:
    """Fabrique partagée (StaticPool) : les tools commitent leur transaction."""
    return api_session_factory


@pytest.fixture
def session(factory: sessionmaker[Session]) -> Iterator[Session]:
    with factory() as active:
        yield active


@pytest.fixture
def agent(session: Session, factory: sessionmaker[Session]) -> AgentGenerateurOffre:
    """Agent relié à des transactions par tool (comme en production)."""
    return build_generateur(session, session_factory=factory)


@pytest.fixture
def outils(agent: AgentGenerateurOffre) -> dict[str, TypedTool]:
    return {tool.name: tool for tool in agent.deep_tools()}


@pytest.fixture
def organisation(factory: sessionmaker[Session]) -> Organisation:
    org = Organisation(nom="Organisation émettrice")
    with factory() as session:
        session.add(org)
        session.commit()
        session.refresh(org)
    return org


@pytest.fixture
def appel_recu(factory: sessionmaker[Session], organisation: Organisation) -> AppelAProposition:
    with factory() as session:
        appel = AppelAPropositionService(session).enregistrer(_entree_appel(organisation))
        session.commit()
        session.refresh(appel)
    return appel


@pytest.fixture
def appel_valide(
    factory: sessionmaker[Session], appel_recu: AppelAProposition
) -> AppelAProposition:
    """Appel validé : la proposition extraite devient la donnée officielle."""
    with factory() as session:
        service = AppelAPropositionService(session)
        service.enregistrer_extraction(
            appel_recu.id,
            dict(PROPOSITION),
            proposed_by_agent=DEFINITION.agent_id,
        )
        service.approuver_extraction(appel_recu.id, Decision(decided_by="carso"))
        session.commit()
        appel = session.get(AppelAProposition, appel_recu.id)
        assert appel is not None
        session.refresh(appel)
        return appel


@pytest.fixture
def lot_officiel(factory: sessionmaker[Session], appel_valide: AppelAProposition) -> Lot:
    """Le lot officiel créé par la validation de l'extraction."""
    with factory() as session:
        lot = session.scalars(
            select(Lot).where(Lot.appel_a_proposition_id == appel_valide.id)
        ).one()
        session.refresh(lot)
        return lot


def _entree_appel(organisation: Organisation) -> AppelAPropositionInput:
    return AppelAPropositionInput(
        organisation_id=organisation.id,
        reference="AO-2026-42",
        titre="Appel à proposition — éducation",
    )


PROPOSITION = {
    "organisation": {"nom": "Organisation émettrice"},
    "resume": "Appui à la formation professionnelle",
    "lots": [
        {
            "numero": "1",
            "titre": "Gestion financière",
            "zone": "Rabat",
            "objectifs": "Renforcer les compétences des formateurs",
        }
    ],
}


def _deposer_non_classe(
    factory: sessionmaker[Session], nom: str = "Appel recu.pdf"
) -> UUID:
    """Pièce jointe déposée hors fiche métier : ``generated/{id}/``."""
    from tests.document_fixtures import build_pdf

    with factory() as session:
        document = DocumentService(session).enregistrer_document(
            DocumentUploadInput(
                type_document=TypeDocument.APPEL_OFFRE.value,
                nom=nom,
                stream=io.BytesIO(build_pdf("APPEL CARSO 2026")),
                created_by="carso",
            )
        )
        session.commit()
        return document.id


def _document(factory: sessionmaker[Session], document_id: UUID) -> Document:
    with factory() as session:
        document = session.get(Document, document_id)
        assert document is not None
        return document


# --- Périmètre outils de l'agent unique -----------------------------------------


class TestPerimetreOutils:
    def test_responsabilites_de_l_analyseur_reprises(self, outils: dict[str, TypedTool]) -> None:
        """Lecture, extraction et lots sont les tools du générateur."""
        attends = {
            "read_appel_a_proposition",
            "list_appel_documents",
            "read_document_text",
            "submit_rfp_extraction",
            "list_lots",
            "enregistrer_appel",
            "rattacher_document_appel",
        }
        assert attends <= set(outils)

    def test_aucun_doublon_de_l_analyseur(self, outils: dict[str, TypedTool]) -> None:
        """``list_appel_a_proposition_documents`` est fusionné : une implémentation."""
        assert "list_appel_a_proposition_documents" not in outils
        assert not any("analyze_rfp_" == nom for nom in outils)

    def test_ecritures_sous_approbation_humaine(self) -> None:
        """Toute écriture de la chaîne est déclarée HITL au contrat."""
        assert {
            "enregistrer_appel",
            "rattacher_document_appel",
            "submit_rfp_extraction",
            "create_offer_draft",
            "generer_document",
        } <= set(DEFINITION.approval_required)

    def test_permission_analyse_portee_par_le_generateur(self) -> None:
        assert DEFINITION.policy.allows("analyze_rfp")


# --- Étape 1 : réception de l'appel ---------------------------------------------


class TestReceptionAppel:
    def test_appel_enregistre_avec_son_document_source(
        self,
        factory: sessionmaker[Session],
        organisation: Organisation,
        outils: dict[str, TypedTool],
    ) -> None:
        document_id = _deposer_non_classe(factory)
        avant = _document(factory, document_id)
        assert avant.appel_a_proposition_id is None
        assert avant.storage_path.startswith("generated/")

        sortie = outils["enregistrer_appel"].invoke(
            {
                "organisation_id": str(organisation.id),
                "reference": "AMI-2026-07",
                "titre": "Manifestation d'intérêt — agriculture",
                "type": TypeAppel.APPEL_A_MANIFESTATION_INTERET.value,
                "document_source_id": str(document_id),
            }
        )

        assert sortie["ok"] is True, sortie
        assert sortie["statut"] == StatutAppelAProposition.RECU.value
        assert sortie["type"] == TypeAppel.APPEL_A_MANIFESTATION_INTERET.value
        assert sortie["requires_approval"] is True
        appel_id = UUID(sortie["appel_a_proposition_id"])

        # L'appel existe, avec sa nature explicite (jamais déduite).
        with factory() as session:
            appel = session.get(AppelAProposition, appel_id)
            assert appel is not None
            assert appel.type == TypeAppel.APPEL_A_MANIFESTATION_INTERET.value
            assert appel.statut == StatutAppelAProposition.RECU.value

        # Le document source a rejoint le dossier de l'appel, octets identiques.
        apres = _document(factory, document_id)
        assert apres.appel_a_proposition_id == appel_id
        assert apres.storage_path == f"appel_proposition/{appel_id}/{apres.nom}"
        assert apres.checksum_sha256 == avant.checksum_sha256

        storage = DocumentService(factory()).storage
        assert storage.exists(apres.storage_path)
        assert not storage.exists(avant.storage_path), "l'ancienne copie est retirée"

    def test_type_obligatoire(
        self, organisation: Organisation, outils: dict[str, TypedTool]
    ) -> None:
        """Sans type explicite, la réception est refusée (aucune supposition)."""
        sortie = outils["enregistrer_appel"].invoke(
            {
                "organisation_id": str(organisation.id),
                "reference": "AO-1",
                "titre": "Appel sans nature",
            }
        )
        assert sortie["ok"] is False
        assert sortie["error"]["code"] == "business_validation_error"

    def test_type_inconnu_refuse(
        self, factory: sessionmaker[Session], organisation: Organisation, outils
    ) -> None:
        sortie = outils["enregistrer_appel"].invoke(
            {
                "organisation_id": str(organisation.id),
                "reference": "AO-1",
                "titre": "Appel",
                "type": "appel_invente",
            }
        )
        assert sortie["ok"] is False
        assert "Type d'appel inconnu" in sortie["error"]["message"]
        with factory() as session:
            assert session.scalars(select(AppelAProposition)).all() == []

    def test_organisation_inconnue_refusee(
        self, factory: sessionmaker[Session], outils: dict[str, TypedTool]
    ) -> None:
        from uuid import uuid4

        sortie = outils["enregistrer_appel"].invoke(
            {
                "organisation_id": str(uuid4()),
                "reference": "AO-1",
                "titre": "Appel",
                "type": TypeAppel.APPEL_A_PROPOSITION.value,
            }
        )
        assert sortie["ok"] is False
        assert sortie["error"]["code"] == "not_found"
        with factory() as session:
            assert session.scalars(select(AppelAProposition)).all() == []

    def test_echec_du_rattachement_annule_l_appel(
        self,
        factory: sessionmaker[Session],
        organisation: Organisation,
        appel_recu: AppelAProposition,
        outils: dict[str, TypedTool],
    ) -> None:
        """Une seule transaction : un rattachement impossible ne laisse pas d'appel."""
        from tests.document_fixtures import build_pdf

        with factory() as session:
            document = DocumentService(session).enregistrer_document(
                DocumentUploadInput(
                    type_document=TypeDocument.APPEL_OFFRE.value,
                    nom="Deja rattache.pdf",
                    stream=io.BytesIO(build_pdf("DEJA RATTACHE")),
                    created_by="carso",
                    appel_a_proposition_id=appel_recu.id,
                )
            )
            session.commit()
            document_id = document.id

        sortie = outils["enregistrer_appel"].invoke(
            {
                "organisation_id": str(organisation.id),
                "reference": "AO-ROLLBACK",
                "titre": "Appel qui ne doit pas exister",
                "type": TypeAppel.APPEL_A_PROPOSITION.value,
                "document_source_id": str(document_id),
            }
        )

        assert sortie["ok"] is False
        assert sortie["error"]["code"] == "conflict"
        with factory() as session:
            assert (
                session.scalars(
                    select(AppelAProposition).where(
                        AppelAProposition.reference == "AO-ROLLBACK"
                    )
                ).all()
                == []
            )


class TestRattachementDocument:
    def test_rattachement_direct_et_refus_du_doublon(
        self,
        factory: sessionmaker[Session],
        appel_recu: AppelAProposition,
        outils: dict[str, TypedTool],
    ) -> None:
        document_id = _deposer_non_classe(factory, nom="Source.pdf")

        sortie = outils["rattacher_document_appel"].invoke(
            {
                "document_id": str(document_id),
                "appel_a_proposition_id": str(appel_recu.id),
            }
        )
        assert sortie["ok"] is True, sortie
        assert sortie["appel_a_proposition_id"] == str(appel_recu.id)

        # Un document déjà ancré n'est jamais reclassé silencieusement.
        encore = outils["rattacher_document_appel"].invoke(
            {
                "document_id": str(document_id),
                "appel_a_proposition_id": str(appel_recu.id),
            }
        )
        assert encore["ok"] is False
        assert encore["error"]["code"] == "conflict"

    def test_document_inconnu_refuse(
        self, appel_recu: AppelAProposition, outils: dict[str, TypedTool]
    ) -> None:
        from uuid import uuid4

        sortie = outils["rattacher_document_appel"].invoke(
            {
                "document_id": str(uuid4()),
                "appel_a_proposition_id": str(appel_recu.id),
            }
        )
        assert sortie["ok"] is False
        assert sortie["error"]["code"] == "not_found"


# --- Étapes 2 à 5 : extraction, validation, lots --------------------------------


class TestExtractionEtLots:
    def test_proposition_puis_lots_officiels_apres_validation(
        self,
        factory: sessionmaker[Session],
        appel_recu: AppelAProposition,
        outils: dict[str, TypedTool],
    ) -> None:
        sortie = outils["submit_rfp_extraction"].invoke(
            {
                "appel_a_proposition_id": str(appel_recu.id),
                "extraction": dict(PROPOSITION),
            }
        )
        assert sortie["ok"] is True, sortie
        assert sortie["statut"] == StatutAppelAProposition.PROPOSE.value
        assert sortie["nb_lots"] == 1
        assert sortie["requires_approval"] is True

        # Proposition : aucune donnée officielle, aucun lot créé.
        with factory() as session:
            appel = session.get(AppelAProposition, appel_recu.id)
            assert appel is not None
            assert appel.statut == StatutAppelAProposition.PROPOSE.value
            assert appel.donnees_extraites["_meta"]["proposed_by_agent"] == (
                DEFINITION.agent_id
            )
            assert session.scalars(select(Lot)).all() == []

        # La validation humaine (hors agent) matérialise les lots officiels.
        with factory() as session:
            AppelAPropositionService(session).approuver_extraction(
                appel_recu.id, Decision(decided_by="carso")
            )
            session.commit()

        lots = outils["list_lots"].invoke({"appel_a_proposition_id": str(appel_recu.id)})
        assert [lot["numero"] for lot in lots["lots"]] == ["1"]
        assert lots["lots"][0]["titre"] == "Gestion financière"

    def test_extraction_invalide_refusee(
        self, appel_recu: AppelAProposition, outils: dict[str, TypedTool]
    ) -> None:
        sortie = outils["submit_rfp_extraction"].invoke(
            {
                "appel_a_proposition_id": str(appel_recu.id),
                "extraction": {"resume": "sans organisation ni lots"},
            }
        )
        assert sortie["ok"] is False

    def test_appel_valide_sans_lot_reparable_par_l_agent(
        self,
        factory: sessionmaker[Session],
        appel_recu: AppelAProposition,
        outils: dict[str, TypedTool],
    ) -> None:
        """Régression : un appel figé « validé » sans lot redevient réparable.

        Reproduit l'état observé en base (validation sans aucun lot) : l'appel
        était un cul-de-sac — la re-soumission butait sur la transition
        interdite ``valide → propose`` et aucun lot ne pouvait naître.
        """
        with factory() as session:
            service = AppelAPropositionService(session)
            service.enregistrer_extraction(
                appel_recu.id, {"lots": []}, proposed_by_agent=DEFINITION.agent_id
            )
            # État forgé : la garde de validation n'existait pas encore.
            appel = session.get(AppelAProposition, appel_recu.id)
            assert appel is not None
            appel.statut = StatutAppelAProposition.VALIDE
            session.commit()

        # L'agent voit ce qui manque au lieu d'affirmer qu'un lot existe.
        lu = outils["read_appel_a_proposition"].invoke(
            {"appel_a_proposition_id": str(appel_recu.id)}
        )
        assert lu["proposition_sans_lot"] is True
        assert lu["nb_lots_officiels"] == 0

        # La re-soumission remet l'appel en proposition au lieu d'échouer.
        sortie = outils["submit_rfp_extraction"].invoke(
            {
                "appel_a_proposition_id": str(appel_recu.id),
                "extraction": dict(PROPOSITION),
            }
        )
        assert sortie["ok"] is True, sortie
        assert sortie["statut"] == StatutAppelAProposition.PROPOSE.value
        assert sortie["nb_lots"] == 1

        # L'approbation humaine matérialise enfin les lots officiels.
        with factory() as session:
            AppelAPropositionService(session).approuver_extraction(
                appel_recu.id, Decision(decided_by="carso")
            )
            session.commit()

        lots = outils["list_lots"].invoke(
            {"appel_a_proposition_id": str(appel_recu.id)}
        )
        assert [lot["numero"] for lot in lots["lots"]] == ["1"]

    def test_validation_sans_lot_refusee_par_le_flux(
        self,
        factory: sessionmaker[Session],
        appel_recu: AppelAProposition,
        outils: dict[str, TypedTool],
    ) -> None:
        """Le défaut d'origine ne peut plus se produire : refus explicite."""
        outils["submit_rfp_extraction"].invoke(
            {
                "appel_a_proposition_id": str(appel_recu.id),
                "extraction": {
                    "organisation": {"nom": "Organisation émettrice"},
                    "lots": [],
                },
            }
        )
        with factory() as session:
            with pytest.raises(ConflictError):
                AppelAPropositionService(session).approuver_extraction(
                    appel_recu.id, Decision(decided_by="carso")
                )
            session.rollback()
            appel = session.get(AppelAProposition, appel_recu.id)
            assert appel is not None
            assert appel.statut == StatutAppelAProposition.PROPOSE.value
            assert session.scalars(select(Lot)).all() == []

    def test_extraction_json_serialisee_acceptee(
        self,
        factory: sessionmaker[Session],
        appel_recu: AppelAProposition,
        outils: dict[str, TypedTool],
    ) -> None:
        """Le LLM sérialise parfois la proposition : la chaîne JSON est parsée."""
        sortie = outils["submit_rfp_extraction"].invoke(
            {
                "appel_a_proposition_id": str(appel_recu.id),
                "extraction": json.dumps(dict(PROPOSITION), ensure_ascii=False),
            }
        )
        assert sortie["ok"] is True, sortie
        assert sortie["nb_lots"] == 1
        with factory() as session:
            appel = session.get(AppelAProposition, appel_recu.id)
            assert appel is not None
            assert appel.statut == StatutAppelAProposition.PROPOSE.value

    def test_extraction_texte_non_json_refusee_avec_erreur_actionnable(
        self, appel_recu: AppelAProposition, outils: dict[str, TypedTool]
    ) -> None:
        """Un texte libre n'est pas une proposition : refus lisible, jamais (racine)."""
        sortie = outils["submit_rfp_extraction"].invoke(
            {
                "appel_a_proposition_id": str(appel_recu.id),
                "extraction": "Proposition résumée en une phrase, sans structure.",
            }
        )
        assert sortie["ok"] is False
        assert sortie["error"]["code"] == "business_validation_error"
        assert "objet" in sortie["error"]["message"]

    def test_extraction_liste_refusee_avec_type_recu(
        self, appel_recu: AppelAProposition, outils: dict[str, TypedTool]
    ) -> None:
        """Un type non objet est refusé à la frontière avec son type annoncé."""
        sortie = outils["submit_rfp_extraction"].invoke(
            {"appel_a_proposition_id": str(appel_recu.id), "extraction": [PROPOSITION]}
        )
        assert sortie["ok"] is False
        erreurs = sortie["error"]["details"]["erreurs"]
        assert any(e.get("type_recu") == "liste" for e in erreurs)

    def test_lecture_de_l_appel_et_des_documents(
        self,
        factory: sessionmaker[Session],
        appel_recu: AppelAProposition,
        outils: dict[str, TypedTool],
    ) -> None:
        document_id = _deposer_non_classe(factory, nom="Appel source.pdf")
        outils["rattacher_document_appel"].invoke(
            {
                "document_id": str(document_id),
                "appel_a_proposition_id": str(appel_recu.id),
            }
        )

        fiche = outils["read_appel_a_proposition"].invoke(
            {"appel_a_proposition_id": str(appel_recu.id)}
        )
        assert fiche["reference"] == "AO-2026-42"
        assert fiche["a_une_proposition"] is False

        documents = outils["list_appel_documents"].invoke(
            {"appel_a_proposition_id": str(appel_recu.id)}
        )
        assert [d["document_id"] for d in documents["documents"]] == [str(document_id)]

        texte = outils["read_document_text"].invoke({"document_id": str(document_id)})
        assert "APPEL CARSO 2026" in texte["texte"]


# --- Étapes 6 à 11 : offres, documents, téléchargement, mission -----------------


class TestOffresEtDocuments:
    def test_chain_complete_appel_lot_offres_documents(
        self,
        factory: sessionmaker[Session],
        appel_valide: AppelAProposition,
        lot_officiel: Lot,
        outils: dict[str, TypedTool],
    ) -> None:
        """``Appel → Lot → Offre → Mission`` : la chaîne demandée par CARSO."""
        lot_id = str(lot_officiel.id)
        technique = outils["create_offer_draft"].invoke(
            {
                "lot_id": lot_id,
                "type": TypeOffre.OFFRE_TECHNIQUE.value,
                "reference": "OF-TECH-1",
                "titre": "Offre technique — lot 1",
            }
        )
        financiere = outils["create_offer_draft"].invoke(
            {
                "lot_id": lot_id,
                "type": TypeOffre.OFFRE_FINANCIERE.value,
                "reference": "OF-FIN-1",
                "titre": "Offre financière — lot 1",
            }
        )
        assert technique["ok"] and financiere["ok"]
        assert technique["statut"] == StatutOffre.BROUILLON.value

        # Chaque offre est liée explicitement à l'appel et au lot.
        with factory() as session:
            offre = session.get(Offre, UUID(financiere["offre_id"]))
            assert offre is not None
            assert offre.appel_a_proposition_id == appel_valide.id
            assert offre.lot_id == lot_officiel.id

        # Un document par offre, ancré à l'offre (visible dans l'espace documents).
        for offre, titre in ((technique, "Offre technique"), (financiere, "Offre financière")):
            sortie = outils["generer_document"].invoke(
                {
                    "format": "docx",
                    "nom_fichier": f"{titre} lot 1",
                    "type_document": TypeDocument.OFFRE.value,
                    "ancre": "offre",
                    "entity_id": offre["offre_id"],
                    "propose_par": DEFINITION.agent_id,
                    "titre": titre,
                    "paragraphes": [
                        "Compréhension de l'appel : non précisé dans le document",
                        "Méthodologie : (à compléter)",
                    ],
                }
            )
            assert sortie["ok"] is True, sortie
            assert sortie["statut"] == StatutDocument.PROPOSED.value
            document_id = UUID(sortie["document_id"])

            # Téléchargeable dans le chat : le fichier existe sur disque.
            with factory() as session:
                nom, chemin = DocumentService(session).telecharger(document_id)
                assert nom.endswith(".docx")
                assert chemin.is_file()

        with factory() as session:
            documents = session.scalars(
                select(Document).where(Document.offre_id == UUID(technique["offre_id"]))
            ).all()
            assert documents, "le document de l'offre est rattaché à l'offre"
            assert documents[0].storage_path.startswith(
                f"offres/{technique['offre_id']}/"
            )

        # La mission est possible depuis les offres approuvées du lot.
        with factory() as session:
            lieu = LieuService(session).creer(LieuInput(nom="Rabat"))
            service = OffreService(session)
            for identifiant in (technique["offre_id"], financiere["offre_id"]):
                service.soumettre_revue(UUID(identifiant))
                service.publier(UUID(identifiant), Decision(decided_by="carso"))
            mission = MissionService(session).creer_depuis_offres(
                MissionDepuisOffresInput(
                    offre_ids=(
                        UUID(technique["offre_id"]),
                        UUID(financiere["offre_id"]),
                    ),
                    reference="M-1",
                    titre="Mission lot 1",
                    lieu_id=lieu.id,
                )
            )
            session.commit()
            assert {o.id for o in mission.offres} == {
                UUID(technique["offre_id"]),
                UUID(financiere["offre_id"]),
            }
            assert mission.lieu_id == lieu.id

    def test_second_type_identique_refuse(
        self, lot_officiel: Lot, outils: dict[str, TypedTool]
    ) -> None:
        """Un lot porte au plus une offre par type : l'agent doit réviser l'existante."""
        payload = {
            "lot_id": str(lot_officiel.id),
            "type": TypeOffre.OFFRE_TECHNIQUE.value,
            "reference": "OF-TECH-1",
            "titre": "Offre technique",
        }
        assert outils["create_offer_draft"].invoke(payload)["ok"] is True
        doublon = outils["create_offer_draft"].invoke(
            {**payload, "reference": "OF-TECH-2"}
        )
        assert doublon["ok"] is False
        assert doublon["error"]["code"] == "conflict"

    def test_manifestation_interet_repond_par_une_offre_autre(
        self,
        factory: sessionmaker[Session],
        organisation: Organisation,
        outils: dict[str, TypedTool],
    ) -> None:
        """Un appel à manifestation d'intérêt se répond par une offre ``autre``."""
        with factory() as session:
            appel = AppelAPropositionService(session).enregistrer(
                AppelAPropositionInput(
                    organisation_id=organisation.id,
                    reference="AMI-2026-1",
                    titre="Manifestation d'intérêt",
                    type=TypeAppel.APPEL_A_MANIFESTATION_INTERET.value,
                )
            )
            session.commit()
            appel_id = appel.id

        with factory() as session:
            service = AppelAPropositionService(session)
            service.enregistrer_extraction(
                appel_id, dict(PROPOSITION), proposed_by_agent=DEFINITION.agent_id
            )
            service.approuver_extraction(appel_id, Decision(decided_by="carso"))
            session.commit()
            lot = session.scalars(
                select(Lot).where(Lot.appel_a_proposition_id == appel_id)
            ).one()
            lot_id = lot.id

        sortie = outils["create_offer_draft"].invoke(
            {
                "lot_id": str(lot_id),
                "type": TypeOffre.AUTRE.value,
                "reference": "OF-AMI-1",
                "titre": "Note d'intérêt",
            }
        )
        assert sortie["ok"] is True, sortie
        assert sortie["type"] == TypeOffre.AUTRE.value

    def test_lot_manuel_sur_appel_recu_autorise_la_creation_d_offre(
        self,
        factory: sessionmaker[Session],
        appel_recu: AppelAProposition,
        outils: dict[str, TypedTool],
    ) -> None:
        """Un lot saisi à la main est officiel : l'extraction n'est pas requise."""
        with factory() as session:
            session.add(
                Lot(
                    appel_a_proposition_id=appel_recu.id,
                    numero="9",
                    titre="Lot saisi manuellement",
                )
            )
            session.commit()
            lot = session.scalars(
                select(Lot).where(Lot.appel_a_proposition_id == appel_recu.id)
            ).one()
            lot_id = lot.id

        sortie = outils["create_offer_draft"].invoke(
            {
                "lot_id": str(lot_id),
                "type": TypeOffre.OFFRE_TECHNIQUE.value,
                "reference": "OF-9",
                "titre": "Offre du lot manuel",
            }
        )
        assert sortie["ok"] is True, sortie
        assert sortie["statut"] == StatutOffre.BROUILLON.value

    def test_appel_rejete_refuse_la_creation_d_offre(
        self,
        factory: sessionmaker[Session],
        appel_recu: AppelAProposition,
        outils: dict[str, TypedTool],
    ) -> None:
        """Un appel rejeté ne reçoit plus d'offre, même si un lot existe."""
        with factory() as session:
            session.add(
                Lot(
                    appel_a_proposition_id=appel_recu.id,
                    numero="9",
                    titre="Lot sur appel rejeté",
                )
            )
            AppelAPropositionService(session).rejeter_appel(
                appel_recu.id, Decision(decided_by="carso")
            )
            session.commit()
            lot = session.scalars(
                select(Lot).where(Lot.appel_a_proposition_id == appel_recu.id)
            ).one()
            lot_id = lot.id

        sortie = outils["create_offer_draft"].invoke(
            {
                "lot_id": str(lot_id),
                "type": TypeOffre.OFFRE_TECHNIQUE.value,
                "reference": "OF-9",
                "titre": "Offre sur appel rejeté",
            }
        )
        assert sortie["ok"] is False
        assert "rejeté" in sortie["error"]["message"]
