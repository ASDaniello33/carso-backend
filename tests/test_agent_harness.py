"""Harnais Deep Agents — tests **hors ligne** (modèle scripté, aucun réseau).

Ce fichier vérifie ce que le harnais garantit indépendamment du provider :

- la **boucle d'agent** réelle (le modèle scripté appelle des tools, les tools
  passent par les services applicatifs, chacun dans sa propre transaction) ;
- l'**allow-list** de tools du contrat d'agent ;
- le **backend virtuel** : les tools fichiers du harness n'écrivent pas sur l'hôte ;
- l'**approbation humaine** : ``interrupt_on`` bloque l'action, ``resume`` la libère ;
- la **configuration multi-provider** : erreurs explicites, clé jamais divulguée.

Les appels réels à un modèle sont dans ``test_agent_live.py`` (opt-in).
"""

from __future__ import annotations

import io
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from uuid import UUID

import pytest
from langgraph.checkpoint.memory import MemorySaver
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.agents import (
    GENERATEUR_DEFINITION,
    AgentConfigurationError,
    AgentGenerateurOffre,
    AgentRegistry,
    AgentRuntimeConfig,
    build_chat_model,
    build_generateur,
    create_carso_deep_agent,
    is_agent_configured,
)
from app.agents.base import AgentDefinition
from app.agents.harness import allowed_tools_only
from app.application.dto import DocumentUploadInput
from app.application.services import DocumentService
from app.core.config import Settings, get_settings
from app.core.errors import ValidationError
from app.domain.document import AgentTask
from app.domain.enums import StatutAgentTask, StatutAppelAProposition, TypeDocument
from app.domain.organization import AppelAProposition, Lot, Organisation
from app.tools.base import TypedTool, to_langchain_tool
from app.tools.permissions import PermissionPolicy
from tests.agent_fixtures import ScriptedChatModel, final_message, tool_call
from tests.conftest import seed
from tests.document_fixtures import build_pdf

MISSION_TEXTE = "MISSION CARSO 2026"

EXTRACTION = {
    "organisation": {"nom": "Ministère de la Formation"},
    "resume": "Formation en gestion pour 200 bénéficiaires",
    "lots": [{"numero": "1", "titre": "Gestion financière", "zone": "Rabat"}],
}


class _SchemaEntree(BaseModel):
    """Schéma minimal pour les tools de test."""

    parametre: str = "valeur"


# --- Fixtures -------------------------------------------------------------------


@pytest.fixture
def storage_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Racine documentaire temporaire (les tools lisent les vrais fichiers)."""
    racine = tmp_path / "carso"
    monkeypatch.setenv("STORAGE_ROOT", str(racine))
    get_settings.cache_clear()
    yield racine
    get_settings.cache_clear()


@pytest.fixture
def factory(api_session_factory: sessionmaker[Session]) -> sessionmaker[Session]:
    """Fabrique de sessions (SQLite mémoire partagée, utilisable entre threads)."""
    return api_session_factory


@pytest.fixture
def caller_session(api_session_factory: sessionmaker[Session]) -> Iterator[Session]:
    """Session de l'appelant (chemin déterministe / appels directs).

    Le chemin deep agent n'y écrit pas : la trace et les tools ont leurs propres
    transactions, pour ne pas garder ouverte la transaction de l'appelant
    pendant toute la durée d'un run (modèle + tools).
    """
    session = api_session_factory()
    yield session
    session.rollback()
    session.close()


@pytest.fixture
def appel(api_session_factory: sessionmaker[Session]) -> AppelAProposition:
    organisation = Organisation(nom="Ministère de la Formation")
    seed(api_session_factory, organisation)
    appel = AppelAProposition(
        organisation_id=organisation.id,
        reference="AO-HARNESS-01",
        titre="Formation en gestion",
        statut=StatutAppelAProposition.RECU.value,
    )
    seed(api_session_factory, appel)
    return appel


@pytest.fixture
def appel_avec_document(
    appel: AppelAProposition,
    api_session_factory: sessionmaker[Session],
    storage_root: Path,
) -> tuple[AppelAProposition, UUID]:
    """Appel à proposition + document PDF réel déposé par le service documentaire."""
    del storage_root
    with api_session_factory() as session:
        document = DocumentService(session).enregistrer_document(
            DocumentUploadInput(
                type_document=TypeDocument.APPEL_PROPOSITION.value,
                nom="Appel à proposition.pdf",
                stream=io.BytesIO(build_pdf(MISSION_TEXTE)),
                created_by="carso",
                appel_a_proposition_id=appel.id,
            )
        )
        session.commit()
        document_id = document.id
    return appel, document_id


@pytest.fixture
def generateur(
    caller_session: Session, api_session_factory: sessionmaker[Session]
) -> AgentGenerateurOffre:
    """Agent relié à des transactions par tool (comme en production)."""
    return build_generateur(caller_session, session_factory=api_session_factory)


def _modele(*messages: object) -> ScriptedChatModel:
    return ScriptedChatModel(responses=list(messages))  # type: ignore[arg-type]


def _appel_relu(factory: sessionmaker[Session], appel_id: UUID) -> AppelAProposition:
    """Relit l'appel dans une **nouvelle** session (comme le ferait l'API)."""
    with factory() as session:
        appel = session.get(AppelAProposition, appel_id)
        assert appel is not None
        return appel


# --- AgentFactory et garde-fous -------------------------------------------------


class TestAgentFactory:
    def test_agent_se_construit_avec_un_modele(
        self, generateur: AgentGenerateurOffre
    ) -> None:
        graphe = generateur.build_agent(
            runtime=AgentRuntimeConfig(model=_modele(final_message("prêt")))
        )

        assert hasattr(graphe, "invoke")
        # Le graphe expose au moins le noeud de tools et une porte de sortie.
        assert {"tools", "model"} <= set(graphe.nodes)

    def test_seuls_les_tools_du_contrat_sont_exposes(
        self, generateur: AgentGenerateurOffre
    ) -> None:
        """L'allow-list du contrat est la seule source des tools exposés."""
        exposes = allowed_tools_only(GENERATEUR_DEFINITION, generateur.deep_tools())

        assert sorted(tool.name for tool in exposes) == sorted(GENERATEUR_DEFINITION.tools)
        assert "approve_offer" not in {tool.name for tool in exposes}
        assert "delete" not in {tool.name for tool in exposes}

    def test_tool_non_declare_dans_le_contrat_refuse(self) -> None:
        definition = AgentDefinition(
            agent_id="AgentTest",
            display_name="Agent de test",
            description="d",
            policy=PermissionPolicy(allowed_capabilities=frozenset({"c"})),
            tools=("autorise",),
        )
        interdit = TypedTool(
            name="interdit",
            description="d",
            input_schema=_SchemaEntree,
            handler=lambda _payload: {"ok": True},
        )

        with pytest.raises(ValidationError) as erreur:
            create_carso_deep_agent(
                definition=definition,
                tools=[interdit],
                runtime=AgentRuntimeConfig(model=_modele(final_message("x"))),
            )

        assert "non déclaré" in str(erreur.value)

    def test_outil_desactive_par_surcharge_exclu_sans_bloquer_le_montage(
        self,
    ) -> None:
        """Désactiver un outil dans Paramètres ne doit pas empêcher l'agent de
        monter : le tool déclaré mais retiré par la surcharge est **écarté** du
        graphe (silencieusement), tandis qu'un tool jamais déclaré reste une
        erreur (garde anti-erreur de code, test ci-dessus)."""
        definition = AgentDefinition(
            agent_id="AgentTest",
            display_name="Agent de test",
            description="d",
            policy=PermissionPolicy(allowed_capabilities=frozenset({"c"})),
            tools=("outil_a", "outil_b"),
        )
        effectif = replace(definition, tools=("outil_b",))  # outil_a désactivé
        outil_a = TypedTool(
            name="outil_a",
            description="d",
            input_schema=_SchemaEntree,
            handler=lambda _payload: {"ok": True},
        )
        outil_b = TypedTool(
            name="outil_b",
            description="d",
            input_schema=_SchemaEntree,
            handler=lambda _payload: {"ok": True},
        )

        # Ne lève PAS : l'agent monte sans l'outil désactivé.
        create_carso_deep_agent(
            definition=definition,
            tools=[outil_a, outil_b],
            runtime=AgentRuntimeConfig(
                model=_modele(final_message("x")), definition=effectif
            ),
        )

        # La garde stricte, elle, refuse toujours l'outil hors contrat.
        with pytest.raises(ValidationError) as erreur:
            allowed_tools_only(effectif, [outil_a])
        assert "non déclaré" in str(erreur.value)

    def test_tool_duplique_refuse(self) -> None:
        definition = AgentDefinition(
            agent_id="AgentTest",
            display_name="Agent de test",
            description="d",
            policy=PermissionPolicy(allowed_capabilities=frozenset({"c"})),
            tools=("autorise",),
        )
        tool = TypedTool(
            name="autorise",
            description="d",
            input_schema=_SchemaEntree,
            handler=lambda _payload: {"ok": True},
        )

        with pytest.raises(ValidationError):
            create_carso_deep_agent(
                definition=definition,
                tools=[tool, tool],
                runtime=AgentRuntimeConfig(model=_modele(final_message("x"))),
            )

    def test_tool_sans_schema_refuse(self) -> None:
        """Un tool exposé à un modèle doit être typé (instruction/05 §11)."""
        tool = TypedTool(name="libre", description="d", handler=lambda _payload: {"ok": True})

        with pytest.raises(ValidationError) as erreur:
            to_langchain_tool(tool)

        assert "schéma" in str(erreur.value)

    def test_tools_fichiers_n_ecrivent_pas_sur_l_hote(
        self, generateur: AgentGenerateurOffre, appel: AppelAProposition, tmp_path: Path
    ) -> None:
        """Le backend par défaut est virtuel : aucun accès au disque hôte."""
        cible = tmp_path / "fuite.txt"
        modele = _modele(
            tool_call("write_file", {"file_path": str(cible), "content": "données"}),
            final_message("écrit"),
        )

        generateur.run(appel.id, runtime=AgentRuntimeConfig(model=modele))

        assert not cible.exists(), "les tools fichiers ne doivent pas toucher l'hôte"

    def test_checkpointer_toujours_fourni(
        self, generateur: AgentGenerateurOffre
    ) -> None:
        """Le harnais fournit toujours un checkpointer (MemorySaver).

        Deux raisons : les interruptions HITL ne peuvent se reprendre sans
        état persisté, et l'adaptateur AG-UI appelle ``aget_state`` au
        premier tour d'un fil (échec « No checkpointer set » sinon).
        """
        interactif = generateur.build_agent(
            runtime=AgentRuntimeConfig(model=_modele(final_message("ok")), interactive=True)
        )
        simple = generateur.build_agent(
            runtime=AgentRuntimeConfig(model=_modele(final_message("ok")))
        )

        assert interactif.checkpointer is not None
        assert simple.checkpointer is not None

    def test_checkpointer_explicit_respecte(
        self, generateur: AgentGenerateurOffre
    ) -> None:
        """Un checkpointer explicite (persistant) prime sur le défaut."""
        from langgraph.checkpoint.memory import MemorySaver

        personnalise = MemorySaver()
        agent = generateur.build_agent(
            runtime=AgentRuntimeConfig(
                model=_modele(final_message("ok")), checkpointer=personnalise
            )
        )

        assert agent.checkpointer is personnalise


# --- Boucle d'agent -------------------------------------------------------------


class TestBoucleAgent:
    def test_l_agent_soumet_la_proposition_via_le_service(
        self,
        generateur: AgentGenerateurOffre,
        factory: sessionmaker[Session],
        appel: AppelAProposition,
    ) -> None:
        modele = _modele(
            tool_call(
                "submit_rfp_extraction",
                {"appel_a_proposition_id": str(appel.id), "extraction": EXTRACTION},
            ),
            final_message("Proposition transmise pour validation."),
        )

        reponse = generateur.run(appel.id, runtime=AgentRuntimeConfig(model=modele))

        assert reponse["status"] == "completed"
        assert reponse["requires_approval"] is True
        assert "submit_rfp_extraction" in reponse["result"]["outils_utilises"]

        # Le tool a commité sa propre transaction : visible depuis une autre session.
        relu = _appel_relu(factory, appel.id)
        assert relu.statut == StatutAppelAProposition.PROPOSE.value
        assert relu.donnees_extraites["_meta"]["proposed_by_agent"] == (
            "agent_generateur_offre"
        )
        assert relu.donnees_extraites["organisation"]["nom"] == ("Ministère de la Formation")

        # Jamais de donnée officielle : aucun lot créé.
        with factory() as session:
            assert session.scalars(select(Lot)).all() == []

        # La trace d'exécution est conservée, dans sa propre transaction.
        with factory() as session:
            tache = session.scalars(select(AgentTask)).one()
            assert tache.status == StatutAgentTask.COMPLETED.value
            assert tache.task_type == "conduire_flux_appel"
            assert tache.from_agent == "agent_generateur_offre"
            assert reponse["task_id"] == str(tache.id)
            assert tache.correlation_id

    def test_l_agent_lit_le_document_source(
        self,
        generateur: AgentGenerateurOffre,
        appel_avec_document: tuple[AppelAProposition, UUID],
    ) -> None:
        """Le texte extrait (Phase 4) parvient réellement au modèle."""
        appel, document_id = appel_avec_document
        modele = _modele(
            tool_call(
                "list_appel_documents",
                {"appel_a_proposition_id": str(appel.id)},
            ),
            tool_call("read_document_text", {"document_id": str(document_id)}),
            final_message("Document lu, données extraites."),
        )

        reponse = generateur.run(appel.id, runtime=AgentRuntimeConfig(model=modele))

        assert MISSION_TEXTE in modele.tool_messages_recus()
        assert {"list_appel_documents", "read_document_text"} <= set(
            reponse["result"]["outils_utilises"]
        )

    def test_la_trace_agent_task_est_ouverte_avant_le_run(
        self,
        generateur: AgentGenerateurOffre,
        factory: sessionmaker[Session],
        appel: AppelAProposition,
    ) -> None:
        modele = _modele(final_message("Aucun document : je ne peux rien extraire."))

        generateur.run(
            appel.id,
            instruction="signale l'absence de document",
            correlation_id="corr-harness",
            runtime=AgentRuntimeConfig(model=modele),
        )

        with factory() as session:
            tache = session.scalars(select(AgentTask)).one()
            assert tache.correlation_id == "corr-harness"
            assert tache.payload["mode"] == "deep_agent"
            assert tache.payload["appel_a_proposition_id"] == str(appel.id)

    def test_outil_de_lecture_expose_les_metadonnees(
        self, generateur: AgentGenerateurOffre, appel: AppelAProposition
    ) -> None:
        outils = {tool.name: tool for tool in generateur.deep_tools()}

        resultat = outils["read_appel_a_proposition"].invoke(
            {"appel_a_proposition_id": str(appel.id)}
        )

        assert resultat["reference"] == "AO-HARNESS-01"
        assert resultat["statut"] == StatutAppelAProposition.RECU.value
        assert resultat["a_une_proposition"] is False


# --- Approbation humaine interactive -------------------------------------------


class TestInterruptionHumaine:
    @staticmethod
    def _runtime(modele: ScriptedChatModel, checkpointer: MemorySaver) -> AgentRuntimeConfig:
        """Le checkpointer doit être partagé entre le run et la reprise."""
        return AgentRuntimeConfig(model=modele, interactive=True, checkpointer=checkpointer)

    def test_interruption_puis_reprise_approuvee(
        self,
        generateur: AgentGenerateurOffre,
        factory: sessionmaker[Session],
        appel: AppelAProposition,
    ) -> None:
        checkpointer = MemorySaver()
        modele = _modele(
            tool_call(
                "submit_rfp_extraction",
                {"appel_a_proposition_id": str(appel.id), "extraction": EXTRACTION},
            ),
            final_message("Proposition prête."),
        )

        reponse = generateur.run(
            appel.id, thread_id="thread-hitl", runtime=self._runtime(modele, checkpointer)
        )

        assert reponse["warnings"], "une action en attente doit être signalée"
        avant = _appel_relu(factory, appel.id)
        assert avant.statut == StatutAppelAProposition.RECU.value
        assert avant.donnees_extraites is None, "rien n'est écrit avant approbation"

        resultat = generateur.resume(
            thread_id="thread-hitl",
            decision="approve",
            runtime=self._runtime(_modele(final_message("Suite après approbation")), checkpointer),
        )

        assert resultat["interrompu"] is False
        apres = _appel_relu(factory, appel.id)
        assert apres.statut == StatutAppelAProposition.PROPOSE.value

    def test_rejet_humain_n_execute_pas_l_action(
        self,
        generateur: AgentGenerateurOffre,
        factory: sessionmaker[Session],
        appel: AppelAProposition,
    ) -> None:
        checkpointer = MemorySaver()
        modele = _modele(
            tool_call(
                "submit_rfp_extraction",
                {"appel_a_proposition_id": str(appel.id), "extraction": EXTRACTION},
            ),
            final_message("Proposition prête."),
        )

        generateur.run(
            appel.id, thread_id="thread-refus", runtime=self._runtime(modele, checkpointer)
        )
        generateur.resume(
            thread_id="thread-refus",
            decision="reject",
            message="Extraction non fiable, à reprendre",
            runtime=self._runtime(_modele(final_message("Compris")), checkpointer),
        )

        relu = _appel_relu(factory, appel.id)
        assert relu.statut == StatutAppelAProposition.RECU.value
        assert relu.donnees_extraites is None


# --- Configuration multi-provider ----------------------------------------------


class TestConfigurationProvider:
    def test_configuration_incomplete_echoue_explicitement(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("AGENT_MODEL", "")

        with pytest.raises(AgentConfigurationError) as erreur:
            build_chat_model(Settings())

        assert "AGENT_MODEL" in str(erreur.value)

    def test_provider_inconnu_refuse(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AGENT_PROVIDER", "provider-inexistant")
        monkeypatch.setenv("AGENT_MODEL", "modele-x")

        with pytest.raises(AgentConfigurationError) as erreur:
            build_chat_model(Settings())

        assert "provider-inexistant" in str(erreur.value)

    def test_endpoint_compatible_exige_une_base_url(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("AGENT_PROVIDER", "openai_compatible")
        monkeypatch.setenv("AGENT_MODEL", "modele-x")
        monkeypatch.setenv("AGENT_BASE_URL", "")
        monkeypatch.setenv("AGENT_API_KEY", "cle-de-test")

        with pytest.raises(AgentConfigurationError) as erreur:
            build_chat_model(Settings())

        assert "AGENT_BASE_URL" in str(erreur.value)

    def test_cle_absente_refusee(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AGENT_PROVIDER", "openai")
        monkeypatch.setenv("AGENT_MODEL", "modele-x")
        monkeypatch.setenv("AGENT_API_KEY", "")

        with pytest.raises(AgentConfigurationError) as erreur:
            build_chat_model(Settings())

        assert "AGENT_API_KEY" in str(erreur.value)

    def test_la_cle_n_apparait_jamais_dans_l_erreur(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Sécurité : un message d'erreur ne divulgue jamais le secret configuré."""
        monkeypatch.setenv("AGENT_PROVIDER", "provider-inexistant")
        monkeypatch.setenv("AGENT_MODEL", "modele-x")
        monkeypatch.setenv("AGENT_API_KEY", "CLE-SECRETE-A-NE-PAS-DIVULGUER")

        with pytest.raises(AgentConfigurationError) as erreur:
            build_chat_model(Settings())

        assert "CLE-SECRETE-A-NE-PAS-DIVULGUER" not in str(erreur.value)
        assert "CLE-SECRETE-A-NE-PAS-DIVULGUER" not in repr(Settings())

    def test_detection_de_configuration_exploitable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("AGENT_PROVIDER", "openai_compatible")
        monkeypatch.setenv("AGENT_MODEL", "")
        monkeypatch.setenv("AGENT_BASE_URL", "")
        monkeypatch.setenv("AGENT_API_KEY", "")
        assert is_agent_configured(Settings()) is False

        monkeypatch.setenv("AGENT_MODEL", "modele-x")
        monkeypatch.setenv("AGENT_BASE_URL", "https://exemple.ai/v1")
        monkeypatch.setenv("AGENT_API_KEY", "cle-de-test")
        assert is_agent_configured(Settings()) is True

        # Casse et espaces : même normalisation que ``build_chat_model``.
        monkeypatch.setenv("AGENT_PROVIDER", " OpenAI_Compatible ")
        assert is_agent_configured(Settings()) is True
        monkeypatch.setenv("AGENT_PROVIDER", "OPENAI")
        assert is_agent_configured(Settings()) is True
        monkeypatch.setenv("AGENT_MODEL", "   ")
        assert is_agent_configured(Settings()) is False


class TestAgentRegistry:
    def test_registre_refuse_les_doublons(self) -> None:
        registre = AgentRegistry()
        registre.register(GENERATEUR_DEFINITION)

        assert registre.ids() == ["agent_generateur_offre"]
        assert registre.get("agent_generateur_offre") is GENERATEUR_DEFINITION
        with pytest.raises(ValidationError):
            registre.register(GENERATEUR_DEFINITION)

    def test_agent_inconnu_echoue_explicitement(self) -> None:
        with pytest.raises(ValidationError):
            AgentRegistry().get("AgentFantome")
