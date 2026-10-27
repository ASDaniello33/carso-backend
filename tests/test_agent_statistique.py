"""Tests AgentStatistique — lecture seule, métrique inconnue refusée."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.agents.agent_statistique import DEFINITION, build_statistique
from app.core.errors import ValidationError
from app.domain.base import Base


def test_contrat_readonly() -> None:
    assert DEFINITION.agent_id == "agent_statistique"
    assert DEFINITION.approval_required == ()
    assert "query_analytics" in DEFINITION.tools
    assert "langsearch_web_search" in DEFINITION.tools


def test_metrique_inconnue() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        agent = build_statistique(session)
        with pytest.raises(ValidationError):
            agent.kpi("chiffre_invente")
        noms = {t.name for t in agent.deep_tools()}
        assert "query_analytics" in noms
        assert "langsearch_web_search" in noms
        assert {
            "search_organisations",
            "search_appels",
            "search_lots",
            "search_offres",
            "search_missions",
            "search_sessions",
            "search_beneficiaires",
            "search_equipes",
            "search_affectations",
            "search_documents",
        } <= noms
    finally:
        session.close()
        engine.dispose()
