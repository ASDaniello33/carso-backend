"""Tests AgentAssistantFormateur — aperçu import, écriture sous HITL.

Règle validée (matrice d'accès, refactor outils) : ``Beneficiaire`` est en
Read/Write — l'import définitif est un tool **séparé** soumis à approbation
humaine (``approval_required``), jamais une écriture silencieuse de l'aperçu.
"""

from __future__ import annotations

from app.agents.assistant_formateur import DEFINITION, build_assistant


def test_contrat_skills_et_hitl() -> None:
    assert "xlsx-officiels" in DEFINITION.skills
    assert "scripts-python" in DEFINITION.skills
    assert DEFINITION.collaboration == ()
    assert "preview_import_beneficiaires" in DEFINITION.tools
    # L'import définitif existe mais reste sous approbation humaine :
    assert "import_beneficiaires" in DEFINITION.tools
    assert "import_beneficiaires" in DEFINITION.approval_required


def test_deep_tools_apercu_seulement() -> None:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.domain.base import Base

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        agent = build_assistant(session)
        noms = {t.name for t in agent.deep_tools()}
    finally:
        session.close()
        engine.dispose()
    assert "preview_import_beneficiaires" in noms
    assert "inspect_beneficiaires_xlsx" in noms
    assert "generer_document" in noms
    assert "fill_template" in noms
    # Surface de recherche externe identique à celle de tous les agents
    # (12/10) : recherche fédérée, lecture de pages, recherche média.
    assert {
        "langsearch_web_search",
        "fetch_url",
        "read_urls",
        "rechercher_video",
    } <= noms
    # L'aperçu n'écrit jamais : seul le tool HITL séparé importe.
    assert "import_beneficiaires" in noms
    assert "get_mission" in noms
    assert "get_mission_offre" in noms
    assert "get_mission_lots" in noms
    assert "list_mission_equipe" in noms
