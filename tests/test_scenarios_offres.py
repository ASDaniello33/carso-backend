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
manquantes (dont le tiret cadratin signalé puis corrigé), garde anti-répétition
en run réel (un modèle scripté qui boucle reçoit le refus et conclut).

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
        "Pour une manifestation d'intérêt, fournis exactement les pièces demandées",
        "La technique et la financière sont possibles sur tout appel",
    ),
    "lots": (
        "Un lot porte au plus une offre par type",
        "Un document ne traite qu'un lot",
    ),
    "modele": (
        "**copie le modèle** et modifie ses éléments",
        "Le fichier du modèle n'est jamais modifié",
    ),
    "donnees_manquantes": (
        "montant, taux, prix unitaire, quantité",
        "Elle n'entre dans le document qu'après accord de l'utilisateur",
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


def _sorties_outils(resultat: dict[str, Any]) -> list[str]:
    """Contenus des messages d'outil de l'état final (un par appel exécuté)."""
    return [
        str(message.content)
        for message in resultat.get("messages") or []
        if message.__class__.__name__ == "ToolMessage"
    ]


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


def _verifier_message_final(texte: str) -> None:
    """Aucun document n'est déclaré approuvé, signé ou envoyé par l'agent."""
    interdits = (
        "approuvé",
        "approuvee",
        "approuvée",
        "signé",
        "signe",
        "envoyé",
        "envoye",
        "officiel",
    )
    minuscule = texte.lower()
    trouves = [mot for mot in interdits if re.search(rf"\b{mot}\b", minuscule)]
    assert not trouves, f"le message déclare un état officiel : {trouves}"


class TestBoucleCompleteScriptee:
    """Boucle complète de l'agent générateur, modèle scripté, sans réseau.

    Le modèle ne décide rien ici : il rejoue un plan (lire l'appel, produire le
    document, conclure). Ce qui est vérifié est le **harnais réel** : quels
    outils sont appelés, dans quel ordre, avec quels résultats rendus au modèle,
    et ce que devient le document.
    """

    def test_outils_dans_l_ordre_et_document_jamais_approuve(
        self,
        factory: sessionmaker[Session],
        organisation: Organisation,
        agent: AgentGenerateurOffre,
        outils: dict[str, TypedTool],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from app.agents.harness import AgentRuntimeConfig, create_carso_deep_agent
        from tests.agent_fixtures import ScriptedChatModel, final_message, tool_call

        # Aucun appel réseau : construire un vrai modèle échouerait ici. Le
        # harnais doit se contenter du modèle scripté fourni par le runtime.
        def _modele_interdit(*args: Any, **kwargs: Any) -> Any:
            raise AssertionError(
                "un scénario hors ligne ne doit construire aucun modèle réseau"
            )

        monkeypatch.setattr("app.agents.harness.build_chat_model", _modele_interdit)
        monkeypatch.setattr("app.agents.providers.build_chat_model", _modele_interdit)

        appel_id, lots = _appel_avec_lots(
            factory,
            organisation,
            reference="AO-2026-42",
            titre="Appel à proposition — éducation",
            type_appel=TypeAppel.APPEL_A_PROPOSITION.value,
            lots=[{"numero": "1", "titre": "Gestion financière"}],
        )
        offre = _offre(
            outils, lots["1"], TypeOffre.OFFRE_TECHNIQUE.value, "OF-TECH-1"
        )

        modele = ScriptedChatModel(
            responses=[
                tool_call("read_appel", {"appel_id": str(appel_id)}),
                tool_call(
                    "generer_document",
                    {
                        "format": "docx",
                        "nom_fichier": "Offre technique lot 1",
                        "type_document": TypeDocument.OFFRE.value,
                        "ancre": "offre",
                        "entity_id": offre["offre_id"],
                        "propose_par": DEFINITION.agent_id,
                        "titre": "Offre technique lot 1",
                        "html": (
                            "<div class='h1'>Offre technique</div>"
                            "<p class='p'>Méthodologie : (à compléter)</p>"
                        ),
                    },
                ),
                final_message(
                    "L'offre technique du lot 1 est préparée en proposition. "
                    "Le document reste à valider par vous."
                ),
            ]
        )
        graphe = create_carso_deep_agent(
            definition=DEFINITION,
            tools=agent.deep_tools(),
            runtime=AgentRuntimeConfig(model=modele),
        )
        resultat = graphe.invoke(
            {"messages": [{"role": "user", "content": "Prépare l'offre du lot 1"}]},
            # Fil de démonstration : le checkpointer du harnais l'exige.
            config={"configurable": {"thread_id": "scenario-boucle-complete"}},
        )

        # Le modèle a reçu le vrai prompt et les vrais outils de l'agent.
        def _texte(message: Any) -> str:
            """Contenu texte d'un message (str ou blocs structurés du provider)."""
            contenu = message.content
            if isinstance(contenu, str):
                return contenu
            if isinstance(contenu, list):
                return "".join(
                    bloc.get("text", "")
                    for bloc in contenu
                    if isinstance(bloc, dict)
                )
            return str(contenu)

        prompt_recu = _texte(modele.received[0][0])
        assert "sans approbation implicite" in prompt_recu
        assert "read_appel" in modele.bound_tools
        assert "generer_document" in modele.bound_tools

        # Ordre des outils : lire l'appel, puis générer, puis conclure.
        appels = [
            appel["name"]
            for message in resultat["messages"]
            for appel in (getattr(message, "tool_calls", None) or [])
        ]
        assert appels == ["read_appel", "generer_document"]
        assert modele.invocations == 3

        # Les résultats rendus au modèle disent l'état réel : proposition.
        resultats = modele.tool_messages_recus()
        assert "AO-2026-42" in resultats
        assert "proposed" in resultats

        # Le message final ne revendique aucune officialisation.
        contenu_final = str(resultat["messages"][-1].content)
        _verifier_message_final(contenu_final)
        assert "proposition" in contenu_final.lower()
        assert "à valider" in contenu_final.lower()

        # Le modèle scripté a bien fait tout le tour : aucune boucle en plus.
        assert modele.invocations == 1 + len(appels)

        # En base, le document produit reste une proposition.
        with factory() as active:
            document = active.scalars(
                select(Document).where(Document.offre_id == UUID(offre["offre_id"]))
            ).one()
            assert document.statut == StatutDocument.PROPOSED.value

    def test_un_message_qui_declare_un_document_approuve_est_refuse(self) -> None:
        _verifier_message_final("Le document est prêt, en attente de validation.")
        with pytest.raises(AssertionError):
            _verifier_message_final("Le document est approuvé et signé par CARSO.")


class TestGardeAntiRepetitionEnRunReel:
    """Un modèle scripté qui boucle sur le même appel reçoit le refus et conclut.

    La garde est vérifiée ici **dans le harnais**, pas sur ``TypedTool.invoke``
    seul : ce qui compte est que le refus arrive au modèle sous forme de message
    d'outil (c'est ce message qui lui permet de changer de stratégie) et que la
    suite du parcours reste libre. Un refus invisible côté modèle ne ferait que
    déplacer la boucle.

    Le contexte de garde est ouvert autour de l'invocation, comme le fait la
    garde d'entrée AG-UI pour un tour utilisateur.
    """

    def _graphe(self, agent: AgentGenerateurOffre, modele: Any) -> Any:
        from app.agents.harness import AgentRuntimeConfig, create_carso_deep_agent

        return create_carso_deep_agent(
            definition=DEFINITION,
            tools=agent.deep_tools(),
            runtime=AgentRuntimeConfig(model=modele),
        )

    def _sans_reseau(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Aucun provider : un run scripté ne doit construire aucun modèle réel."""

        def _modele_interdit(*args: Any, **kwargs: Any) -> Any:
            raise AssertionError(
                "un scénario hors ligne ne doit construire aucun modèle réseau"
            )

        monkeypatch.setattr("app.agents.harness.build_chat_model", _modele_interdit)
        monkeypatch.setattr("app.agents.providers.build_chat_model", _modele_interdit)

    def test_troisieme_appel_identique_refuse_puis_conclusion(
        self,
        factory: sessionmaker[Session],
        organisation: Organisation,
        agent: AgentGenerateurOffre,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from app.tools.garde_repetition import fermer_garde, ouvrir_garde, resume
        from tests.agent_fixtures import ScriptedChatModel, final_message, tool_call

        self._sans_reseau(monkeypatch)
        appel_id, _ = _appel_avec_lots(
            factory,
            organisation,
            reference="AO-BOUCLE-1",
            titre="Appel à proposition — boucle d'outil",
            type_appel=TypeAppel.APPEL_A_PROPOSITION.value,
            lots=[{"numero": "1", "titre": "Gestion financière"}],
        )

        lire = {"appel_id": str(appel_id)}
        modele = ScriptedChatModel(
            responses=[
                tool_call("read_appel", lire),
                tool_call("read_appel", lire),
                tool_call("read_appel", lire),
                # Le refus doit laisser le parcours libre : autre outil, autre but.
                tool_call("list_lots", lire),
                final_message(
                    "Le même appel était déjà lu : je m'arrête là et je vous "
                    "signale la répétition. La suite reste à votre main."
                ),
            ]
        )

        ouvrir_garde()
        try:
            resultat = self._graphe(agent, modele).invoke(
                {"messages": [{"role": "user", "content": "Traite l'appel"}]},
                config={"configurable": {"thread_id": "scenario-garde-repetition"}},
            )
            compteurs = resume()
        finally:
            fermer_garde()

        # 1. Le modèle a bien demandé quatre appels : trois identiques, un autre.
        appels = [
            appel["name"]
            for message in resultat["messages"]
            for appel in (getattr(message, "tool_calls", None) or [])
        ]
        assert appels == ["read_appel", "read_appel", "read_appel", "list_lots"]
        assert modele.invocations == 5

        # 2. Le troisième appel identique a été refusé, et le refus est **dans le
        #    fil** : c'est ce que le modèle lit pour conclure. On lit l'état
        #    final (un message d'outil par appel), pas l'historique cumulé.
        sorties = _sorties_outils(resultat)
        assert len(sorties) == 4
        assert "AO-BOUCLE-1" in sorties[0] and "AO-BOUCLE-1" in sorties[1]
        assert (
            "a déjà été appelé 2 fois avec exactement les mêmes arguments"
            in sorties[2]
        )
        assert "conclus" in sorties[2]
        # Le refus arrive comme message d'outil **en erreur**, pas comme succès.
        erreurs = [
            message
            for message in resultat["messages"]
            if getattr(message, "status", None) == "error"
        ]
        assert len(erreurs) == 1

        # 3. Le parcours n'est pas bridé : l'outil suivant a bien tourné et le
        #    modèle a reçu son résultat réel.
        assert "Gestion financière" in sorties[3]

        # 4. Compteurs du run exposés à la trace : quatre tentatives, un refus.
        assert compteurs == {
            "appels": 4,
            "refus_repetition": 1,
            "par_outil": {"list_lots": 1, "read_appel": 3},
        }

        # 5. Conclusion : aucune officialisation revendiquée.
        contenu_final = str(resultat["messages"][-1].content)
        _verifier_message_final(contenu_final)
        assert "répétition" in contenu_final

    def test_hors_garde_le_meme_script_n_est_pas_refuse(
        self,
        factory: sessionmaker[Session],
        organisation: Organisation,
        agent: AgentGenerateurOffre,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Sans garde ouverte (script, tâche inter-agent), rien n'est borné."""
        from tests.agent_fixtures import ScriptedChatModel, final_message, tool_call

        self._sans_reseau(monkeypatch)
        appel_id, _ = _appel_avec_lots(
            factory,
            organisation,
            reference="AO-HORS-GARDE-1",
            titre="Appel hors garde",
            type_appel=TypeAppel.APPEL_A_PROPOSITION.value,
            lots=[{"numero": "1", "titre": "Lot unique"}],
        )
        lire = {"appel_id": str(appel_id)}
        modele = ScriptedChatModel(
            responses=[
                tool_call("read_appel", lire),
                tool_call("read_appel", lire),
                tool_call("read_appel", lire),
                final_message("Les trois lectures sont faites, voici le résumé."),
            ]
        )

        resultat = self._graphe(agent, modele).invoke(
            {"messages": [{"role": "user", "content": "Traite l'appel"}]},
            config={"configurable": {"thread_id": "scenario-hors-garde"}},
        )

        sorties = _sorties_outils(resultat)
        assert len(sorties) == 3
        assert all("AO-HORS-GARDE-1" in sortie for sortie in sorties)
        assert not any("a déjà été appelé" in sortie for sortie in sorties)


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
