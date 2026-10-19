"""Tests AgentGenerateurOffre — brouillon HITL depuis un lot officiel."""

from __future__ import annotations

import re

import pytest
from pydantic import BaseModel
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.sql.ddl import CreateColumn

from app.agents.generateur_offre import DEFINITION, build_generateur
from app.core.errors import ValidationError
from app.domain.base import Base
from app.domain.enums import StatutAppelAProposition
from app.domain.organization import AppelAProposition, Lot, Organisation


@compiles(JSONB, "sqlite")
def _jsonb_as_json(type_: JSONB, compiler: object, **kw: object) -> str:
    return "JSON"


@compiles(CreateColumn, "sqlite")
def _strip_pg(element: CreateColumn, compiler: object, **kw: object) -> str:
    ddl = compiler.visit_create_column(element)  # type: ignore[attr-defined]
    return re.sub(r"DEFAULT gen_random_uuid\(\)", "", ddl)


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as s:
        yield s
    engine.dispose()


def _lot(session: Session, *, statut: str) -> Lot:
    org = Organisation(nom="Client Offre")
    session.add(org)
    session.flush()
    appel = AppelAProposition(
        organisation_id=org.id,
        reference="AO-GEN-01",
        titre="AO",
        statut=statut,
    )
    session.add(appel)
    session.flush()
    lot = Lot(appel_a_proposition_id=appel.id, numero="1", titre="Lot 1")
    session.add(lot)
    session.flush()
    return lot


def test_contrat_skills_et_hitl() -> None:
    """Agent unique : HITL à chaque écriture, RH seule délégation."""
    assert "docx-officiels" in DEFINITION.skills
    assert {
        "enregistrer_appel",
        "rattacher_document_appel",
        "submit_rfp_extraction",
        "create_offer_draft",
    } <= set(DEFINITION.approval_required)
    assert DEFINITION.collaborates_with() == ("agent_rh",)
    assert DEFINITION.granted_task_types("agent_rh") == frozenset(
        {"propose_team_assignment"}
    )
    assert not any("analyseur" in cible for cible in DEFINITION.collaborates_with())
    # Responsabilités reprises de l'analyseur : capacité et outils de lecture.
    assert DEFINITION.policy.allows("analyze_rfp")
    assert {
        "read_appel_a_proposition",
        "read_document_text",
        "list_lots",
        "submit_rfp_extraction",
    } <= set(DEFINITION.tools)
    # Chaîne documentaire complète : le prompt les prescrit, donc le contrat doit
    # les autoriser, sinon l'agent ne peut pas les appeler.
    assert {
        "generer_document",
        "valider_document",
        "render_document",
        "corriger_document",
        "analyze_document_reference",
        "importer_tableau_xlsx",
    } <= set(DEFINITION.tools)
    # Corriger un document écrit une nouvelle version : même HITL que générer.
    assert "corriger_document" in DEFINITION.approval_required


def test_preparer_lot_manuel_sur_appel_recu(session: Session) -> None:
    """Un lot saisi à la main sur un appel ``recu`` ouvre le brouillon."""
    lot = _lot(session, statut=StatutAppelAProposition.RECU.value)
    agent = build_generateur(session)
    reponse = agent.preparer(lot.id, reference="OF-1", titre="Offre")
    assert reponse["requires_approval"] is True
    assert reponse["offre"]["statut"] == "brouillon"


def test_preparer_appel_rejete_refuse(session: Session) -> None:
    lot = _lot(session, statut=StatutAppelAProposition.REJETE.value)
    agent = build_generateur(session)
    with pytest.raises(ValidationError, match="appel rejeté"):
        agent.preparer(lot.id, reference="OF-1", titre="Offre")


def test_preparer_lot_valide_brouillon(session: Session) -> None:
    lot = _lot(session, statut=StatutAppelAProposition.VALIDE.value)
    agent = build_generateur(session)
    reponse = agent.preparer(lot.id, reference="OF-1", titre="Offre lot 1")
    assert reponse["requires_approval"] is True
    assert reponse["offre"]["statut"] == "brouillon"
    noms = {t.name for t in agent.deep_tools()}
    assert {"get_lot", "create_offer_draft", "get_document_template"} <= noms
    assert "fill_template" in noms
    assert "clone_document_structure" in noms
    assert "get_document_structure" in noms
    # Surface de recherche externe identique à celle de tous les agents
    # (12/10) : recherche fédérée, lecture de pages, recherche média.
    assert {
        "langsearch_web_search",
        "fetch_url",
        "read_urls",
        "rechercher_video",
    } <= noms
    assert "request_agent_task" in noms
    assert "get_agent_task_result" in noms
    assert "read_document" in noms
    assert "search_organisations" in noms
    assert "search_missions" in noms
    assert "web_search" in DEFINITION.capabilities


def test_tous_les_tools_ont_un_schema_langchain_valide(session: Session) -> None:
    """Le middleware Deep Agents (summarization) lit tool_call_schema :
    un schéma = pydantic.BaseModel brut plante le run AG-UI."""
    from app.tools.base import to_langchain_tool

    agent = build_generateur(session)
    for tool in agent.deep_tools():
        lc = to_langchain_tool(tool)
        schema = lc.tool_call_schema
        assert schema is not BaseModel, tool.name
        assert hasattr(schema, "__pydantic_generic_metadata__"), tool.name
