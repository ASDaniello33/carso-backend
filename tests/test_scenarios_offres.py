"""Scénarios réels rejouables — le prompt compacté produit-il une offre acceptable ?

Pourquoi ce fichier : la réduction du prompt du générateur (110 602 → 82 316
caractères, blocs génériques non dupliqués) a été vérifiée par la présence de
règles et par une borne de taille. Ce n'est pas suffisant : il faut rejouer des
situations réelles et regarder **le livrable**.

Chaque scénario est un dossier complet et déterministe (aucun modèle, aucun
réseau) :

1. **Règles du prompt compacté** : les phrases de règle qui portent le scénario
   sont cherchées dans ``build_system_prompt(DEFINITION)``. Une condensation
   qui les supprime fait échouer le scénario, pas un test de forme.
2. **Chaîne réelle** : appel → extraction → lots → offres, via les tools de
   l'agent (mêmes services qu'en production), puis génération du document.
3. **Livrable relu** : le texte réellement extrait du fichier produit est
   vérifié — pas de montant inventé, statut ``proposed``, avertissements
   traités.

Scénarios : AMI, appel à proposition avec lots, modèle client imposé, données
manquantes (dont le tiret cadratin signalé puis corrigé).

Limite assumée : le modèle de langage n'est pas appelé (le projet n'a pas de
provider dans les tests). Ce qui est rejoué est la chaîne outils/services et le
prompt réellement assemblé ; la qualité rédactionnelle d'une offre générée par
un LLM reste vérifiée par revue humaine.
"""

from __future__ import annotations

import io
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.agents.generateur_offre import DEFINITION, AgentGenerateurOffre, build_generateur
from app.agents.harness import build_system_prompt
from app.application.dto import AppelInput, DocumentUploadInput
from app.application.perimetre import Perimetre
from app.application.services import AppelService, DocumentService
from app.application.services.modele_document_service import ModeleDocumentService
from app.application.trace import Decision
from app.core.config import get_settings
from app.domain.document import Document
from app.domain.enums import StatutDocument, StatutOffre, TypeAppel, TypeDocument, TypeOffre
from app.domain.organization import Appel, Lot, Offre, Organisation
from app.tools.base import TypedTool
from tests.test_moteur_documentaire import modele_docx

# --- Règles du prompt compacté, par scénario ------------------------------------

REGLES_PROMPT: dict[str, tuple[str, ...]] = {
    "ami": (
        "Ne pas transformer un AMI en offre technique et financière complète",
        "respecter exactement les pièces demandées",
    ),
    "lots": (
        "une par lot et par type",
        "Budget par lot",
    ),
    "modele": (
        "copier puis modifier",
        "ne se « réinterprète » ni ne se redessine",
    ),
    "donnees_manquantes": (
        "Tu n'inventes AUCUN montant, AUCUN taux, AUCUN prix unitaire",
        "Tu ne le remplis pas d'office.",
    ),
}

# --- Outils de mesure du livrable ----------------------------------------------


def montants_inventes(texte: str, autorises: set[str]) -> list[str]:
    """Montants cités dans le document et absents des données fournies.

    Seuls les montants portant une devise sont examinés : un nombre nu peut
    être une date, une référence ou un numéro de lot. ``autorises`` contient
    les montants réellement fournis (normalisés sans espaces).
    """
    motif = re.compile(
        r"\d[\d\s\u00a0\u202f.,]*\s?(?:MGA|Ar\b|FCFA|EUR\b|USD\b|€)"
    )
    trouves: list[str] = []
    for brut in motif.findall(texte):
        normalise = re.sub(r"[\s\u00a0\u202f]", "", brut)
        if normalise not in autorises:
            trouves.append(brut.strip())
    return trouves


def _avertissements(sortie: dict[str, Any]) -> list[str]:
    """Avertissements d'un résultat d'outil, quel que soit le nom du champ."""
    brut = sortie.get("avertissements") or sortie.get("warnings") or []
    return [str(item) for item in brut]


# --- Fixtures -------------------------------------------------------------------


@pytest.fixture
def storage_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    racine = tmp_path / "carso"
    monkeypatch.setenv("STORAGE_ROOT", str(racine))
    get_settings.cache_clear()
    yield racine
    get_settings.cache_clear()


@pytest.fixture
def factory(api_session_factory: sessionmaker[Session]) -> sessionmaker[Session]:
    return api_session_factory


@pytest.fixture
def session(factory: sessionmaker[Session]) -> Iterator[Session]:
    with factory() as active:
        yield active


@pytest.fixture
def agent(session: Session, factory: sessionmaker[Session]) -> AgentGenerateurOffre:
    return build_generateur(session, session_factory=factory)


@pytest.fixture
def outils(agent: AgentGenerateurOffre) -> dict[str, TypedTool]:
    return {outil.name: outil for outil in agent.deep_tools()}


@pytest.fixture
def organisation(factory: sessionmaker[Session]) -> Organisation:
    org = Organisation(nom="Organisation émettrice")
    with factory() as session:
        session.add(org)
        session.commit()
        session.refresh(org)
    return org


# --- Helpers de scénario --------------------------------------------------------


def _regles_du_prompt(scenario: str) -> str:
    prompt = build_system_prompt(DEFINITION)
    for regle in REGLES_PROMPT[scenario]:
        assert regle in prompt, (
            f"règle perdue par la compaction ({scenario}) : {regle!r}"
        )
    return prompt


def _appel_avec_lots(
    factory: sessionmaker[Session],
    organisation: Organisation,
    *,
    reference: str,
    titre: str,
    type_appel: str,
    lots: list[dict[str, Any]],
) -> tuple[UUID, dict[str, UUID]]:
    """Appel enregistré, extraction approuvée, lots officiels (numéro → id)."""
    with factory() as session:
        appel = AppelService(session).enregistrer(
            AppelInput(
                organisation_id=organisation.id,
                reference=reference,
                titre=titre,
                type=type_appel,
            )
        )
        appel_id = appel.id
        service = AppelService(session)
        service.enregistrer_extraction(
            appel_id,
            {
                "organisation": {"nom": organisation.nom},
                "resume": titre,
                "lots": lots,
            },
            proposed_by_agent=DEFINITION.agent_id,
        )
        service.approuver_extraction(appel_id, Decision(decided_by="carso"))
        session.commit()
        officiels = session.scalars(select(Lot).where(Lot.appel_id == appel_id)).all()
        return appel_id, {str(lot.numero): lot.id for lot in officiels}


def _offre(outils: dict[str, TypedTool], lot_id: UUID, type_offre: str, reference: str):
    sortie = outils["create_offer_draft"].invoke(
        {
            "lot_id": str(lot_id),
            "type": type_offre,
            "reference": reference,
            "titre": f"Offre {type_offre} — {reference}",
        }
    )
    assert sortie["ok"] is True, sortie
    return sortie


def _document_offre(
    outils: dict[str, TypedTool],
    offre_id: str,
    *,
    nom_fichier: str,
    html: str,
) -> tuple[dict[str, Any], str]:
    """Génère le document de l'offre et renvoie (sortie, texte réel du fichier)."""
    sortie = outils["generer_document"].invoke(
        {
            "format": "docx",
            "nom_fichier": nom_fichier,
            "type_document": TypeDocument.OFFRE.value,
            "ancre": "offre",
            "entity_id": offre_id,
            "propose_par": DEFINITION.agent_id,
            "titre": nom_fichier,
            "html": html,
        }
    )
    assert sortie["ok"] is True, sortie
    texte = outils["read_document_text"].invoke(
        {"document_id": sortie["document_id"]}
    )["texte"]
    return sortie, texte


def _verifier_livrable_acceptable(
    sortie: dict[str, Any],
    texte: str,
    *,
    autorises: set[str],
) -> None:
    """Les trois garanties demandées : proposed, aucun montant inventé, relu."""
    assert sortie["statut"] == StatutDocument.PROPOSED.value, sortie
    assert sortie.get("requires_approval") is not False
    inventes = montants_inventes(texte, autorises)
    assert not inventes, f"montants non fournis dans le livrable : {inventes}"
    assert "\u2014" not in texte, "le livrable contient un tiret cadratin"


# --- Scénarios ------------------------------------------------------------------


class TestAmi:
    """AMI : une manifestation d'intérêt, pas une offre technique + financière."""

    def test_ami_produit_une_offre_autre_et_un_livrable_acceptable(
        self,
        factory: sessionmaker[Session],
        organisation: Organisation,
        outils: dict[str, TypedTool],
    ) -> None:
        _regles_du_prompt("ami")
        appel_id, lots = _appel_avec_lots(
            factory,
            organisation,
            reference="AMI-2026-07",
            titre="Manifestation d'intérêt — agriculture",
            type_appel=TypeAppel.APPEL_A_MANIFESTATION_INTERET.value,
            lots=[
                {
                    "numero": "1",
                    "titre": "Appui aux coopératives",
                    "zone": "Toliara",
                    "objectifs": "Renforcer les capacités de gestion",
                }
            ],
        )
        assert list(lots) == ["1"]

        offre = _offre(outils, lots["1"], TypeOffre.AUTRE.value, "OF-AMI-1")
        assert offre["type"] == TypeOffre.AUTRE.value
        assert offre["statut"] == StatutOffre.BROUILLON.value

        sortie, texte = _document_offre(
            outils,
            offre["offre_id"],
            nom_fichier="Note d'interet - AMI 2026-07",
            html=(
                "<div class='h1'>Manifestation d'intérêt</div>"
                "<p class='p'>Domaines d'expertise : formation professionnelle, "
                "gestion de projets.</p>"
                "<p class='p'>Références : (à compléter)</p>"
            ),
        )
        _verifier_livrable_acceptable(sortie, texte, autorises=set())
        assert "Manifestation d'intérêt" in texte
        # Aucune pièce financière n'a été demandée par l'AMI.
        assert "Budget" not in texte and "Offre financière" not in texte

        with factory() as active:
            assert active.get(Appel, appel_id) is not None
            offre_db = active.get(Offre, UUID(offre["offre_id"]))
            assert offre_db is not None and offre_db.type == TypeOffre.AUTRE.value


class TestAppelAPropositionAvecLots:
    """Appel à proposition, deux lots : technique + financière par lot."""

    def test_deux_lots_deux_offres_par_lot_et_budget_sans_invention(
        self,
        factory: sessionmaker[Session],
        organisation: Organisation,
        outils: dict[str, TypedTool],
    ) -> None:
        _regles_du_prompt("lots")
        _, lots = _appel_avec_lots(
            factory,
            organisation,
            reference="AO-2026-42",
            titre="Appel à proposition — éducation",
            type_appel=TypeAppel.APPEL_A_PROPOSITION.value,
            lots=[
                {"numero": "1", "titre": "Gestion financière", "zone": "Rabat"},
                {"numero": "2", "titre": "Ingénierie pédagogique", "zone": "Rabat"},
            ],
        )
        assert set(lots) == {"1", "2"}

        offres: dict[str, dict[str, Any]] = {}
        for numero in ("1", "2"):
            offres[f"tech-{numero}"] = _offre(
                outils, lots[numero], TypeOffre.OFFRE_TECHNIQUE.value, f"OF-TECH-{numero}"
            )
            offres[f"fin-{numero}"] = _offre(
                outils, lots[numero], TypeOffre.OFFRE_FINANCIERE.value, f"OF-FIN-{numero}"
            )

        # Un lot porte au plus une offre par type.
        doublon = outils["create_offer_draft"].invoke(
            {
                "lot_id": str(lots["1"]),
                "type": TypeOffre.OFFRE_TECHNIQUE.value,
                "reference": "OF-TECH-1BIS",
                "titre": "Doublon interdit",
            }
        )
        assert doublon["ok"] is False
        assert doublon["error"]["code"] == "conflict"

        # Budget : seul le montant réellement fourni apparaît, le reste est dit.
        fourni = "12 000 MGA"
        autorises = {re.sub(r"\s", "", fourni)}
        sortie, texte = _document_offre(
            outils,
            offres["fin-1"]["offre_id"],
            nom_fichier="Offre financière lot 1",
            html=(
                "<div class='h1'>Offre financière — lot 1</div>"
                "<table class='tableau'><tr><th>Poste</th><th>Montant</th></tr>"
                f"<tr><td>Expert formateur</td><td>{fourni}</td></tr>"
                "<tr><td>Logistique</td><td>(à compléter)</td></tr></table>"
            ),
        )
        _verifier_livrable_acceptable(sortie, texte, autorises=autorises)
        assert "compléter" in texte.lower()

        with factory() as active:
            for offre in offres.values():
                assert active.get(Offre, UUID(offre["offre_id"])) is not None


class TestModeleClientImpose:
    """Un modèle déposé par le client interdit la génération libre."""

    def test_modele_client_impose_la_copie_et_non_la_generation_libre(
        self,
        factory: sessionmaker[Session],
        organisation: Organisation,
        outils: dict[str, TypedTool],
    ) -> None:
        _regles_du_prompt("modele")
        _, lots = _appel_avec_lots(
            factory,
            organisation,
            reference="AO-MODELE-1",
            titre="Appel avec modèle client",
            type_appel=TypeAppel.APPEL_A_PROPOSITION.value,
            lots=[{"numero": "1", "titre": "Lot unique"}],
        )
        offre = _offre(
            outils, lots["1"], TypeOffre.OFFRE_TECHNIQUE.value, "OF-MODELE-1"
        )

        with factory() as session:
            documents = DocumentService(
                session,
                perimetre=Perimetre(
                    role="administrateur", utilisateur_id=organisation.id, est_complet=True
                ),
            )
            modele_document = documents.enregistrer_document(
                DocumentUploadInput(
                    type_document="template",
                    nom="modele_client.docx",
                    stream=io.BytesIO(modele_docx()),
                    created_by="carso",
                    organisation_id=organisation.id,
                )
            )
            ModeleDocumentService(session).enregistrer_modele(
                organisation.id,
                nom="modele_client.docx",
                type_document="modele_offre_technique",
                document_template_id=modele_document.id,
                registered_by="carso",
            )
            session.commit()

        # La génération libre est refusée, avec la chaîne à suivre.
        refus = outils["generer_document_professionnel"].invoke(
            {
                "titre": "Offre technique",
                "contenu_html": "<h1>Offre</h1><p>Contenu.</p>",
                "format": "docx",
                "nom_fichier": "offre_technique.docx",
                "type_document": "offre",
                "propose_par": DEFINITION.agent_id,
                "ancre": "offre",
                "entity_id": offre["offre_id"],
            }
        )
        assert refus["ok"] is False
        assert "ne se génère à neuf" in refus["error"]["message"]
        assert "copier_modele_document" in refus["error"]["details"]["outils"]

        # La copie du modèle est le document à éditer, en proposition.
        copie = outils["copier_modele_document"].invoke(
            {"ancre": "offre", "entity_id": offre["offre_id"]}
        )
        assert copie["ok"] is True, copie
        assert copie["statut"] == StatutDocument.PROPOSED.value

        with factory() as session:
            document = session.get(Document, UUID(copie["document_id"]))
            assert document is not None
            assert document.statut == StatutDocument.PROPOSED.value
            assert document.offre_id == UUID(offre["offre_id"])


class TestDonneesManquantes:
    """Ce qui n'est pas fourni est dit, et les avertissements sont traités."""

    def test_donnees_manquantes_ne_produisent_ni_montant_ni_silence(
        self,
        factory: sessionmaker[Session],
        organisation: Organisation,
        outils: dict[str, TypedTool],
    ) -> None:
        _regles_du_prompt("donnees_manquantes")
        _, lots = _appel_avec_lots(
            factory,
            organisation,
            reference="AO-INCOMPLET-1",
            titre="Appel incomplet",
            type_appel=TypeAppel.APPEL_A_PROPOSITION.value,
            lots=[{"numero": "1", "titre": "Lot incomplet"}],
        )
        offre = _offre(
            outils, lots["1"], TypeOffre.OFFRE_FINANCIERE.value, "OF-INCOMPLET-1"
        )

        # 1. Le premier jet contient un tiret cadratin : le serveur le signale.
        premier, texte_premier = _document_offre(
            outils,
            offre["offre_id"],
            nom_fichier="Budget prévisionnel",
            html=(
                "<div class='h1'>Budget prévisionnel</div>"
                "<p class='p'>Montant global — (à compléter)</p>"
                "<table class='tableau'><tr><th>Poste</th><th>Montant</th></tr>"
                "<tr><td>Honoraires</td><td>(à compléter)</td></tr></table>"
            ),
        )
        avertissements = _avertissements(premier)
        assert avertissements, "le tiret cadratin doit être signalé, pas corrigé en silence"

        # 2. Corrigé : même contenu sans le caractère fautif, plus d'avertissement.
        corrige, texte = _document_offre(
            outils,
            offre["offre_id"],
            nom_fichier="Budget prévisionnel corrigé",
            html=(
                "<div class='h1'>Budget prévisionnel</div>"
                "<p class='p'>Montant global : (à compléter)</p>"
                "<table class='tableau'><tr><th>Poste</th><th>Montant</th></tr>"
                "<tr><td>Honoraires</td><td>(à compléter)</td></tr></table>"
            ),
        )
        assert not _avertissements(corrige), _avertissements(corrige)
        _verifier_livrable_acceptable(corrige, texte, autorises=set())
        assert "compléter" in texte.lower()
        assert "0 MGA" not in texte and "0 Ar" not in texte

        # 3. Le premier jet reste une proposition datée, pas une officialisation.
        assert premier["statut"] == StatutDocument.PROPOSED.value
        assert "—" not in texte_premier


class TestGardeFousDuJeuDeScenarios:
    """Le jeu de scénarios doit lui-même détecter une régression."""

    def test_un_montant_non_fourni_est_detecte(self) -> None:
        texte = "Honoraires : 350 000 MGA\nLogistique : 12 000 MGA"
        assert montants_inventes(texte, {"12000MGA"}) == ["350 000 MGA"]

    def test_les_scenarios_couvrent_les_quatre_cas_demandes(self) -> None:
        assert set(REGLES_PROMPT) == {"ami", "lots", "modele", "donnees_manquantes"}
        assert all(REGLES_PROMPT.values())

    def test_chaque_regle_du_prompt_existe_encore(self) -> None:
        for scenario in REGLES_PROMPT:
            _regles_du_prompt(scenario)
