"""Périmètre documentaire des agents (demande 04/10).

Deux demandes distinctes, un même point de contrôle : **les tools déclarés au
contrat doivent réellement être construits**. Un nom présent dans ``tools`` mais
absent du graphe ne sert à rien (c'est exactement ce qui manquait au statistique :
``read_document`` était déclaré dans son module de tools mais filtré par l'allow-list
de son contrat — donc jamais câblé).

- **agent_assistant_formateur** : tous les outils de manipulation de documents,
  générateur professionnel ET document libre compris (**priorité de génération
  docx/pdf : ``generer_document_professionnel``**) ;
- **agent_statistique** : lecture documentaire seule (PDF/DOCX/XLSX) pour
  extraire des données à représenter en dashboard — aucune capacité d'écriture.
"""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.agents.agent_statistique import DEFINITION as STAT_DEF
from app.agents.agent_statistique import build_statistique
from app.agents.assistant_formateur import DEFINITION as ASSISTANT_DEF
from app.agents.assistant_formateur import build_assistant
from app.agents.generaliste_readonly import READ_DOCUMENT_TOOL
from app.agents.outillage import tools_documents_pour
from app.domain.base import Base

#: Outils de **lecture** documentaire (aucun dépôt, aucune modification).
OUTILS_LECTURE = (
    "lire_document",
    "read_document",
    "get_document_structure",
    "read_document_range",
    "chercher_dans_document",
    "lire_element_document",
    "importer_tableau_xlsx",
)

#: Outils de **production** documentaire, attendus chez l'assistant formateur.
OUTILS_PRODUCTION = (
    "generer_document",
    "generer_document_professionnel",
    "generer_document_html",
    "clone_document_structure",
    "fill_template",
    "valider_document",
    "render_document",
    "corriger_document",
    "analyze_document_reference",
)


def _session_factory() -> tuple[sessionmaker, object]:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    return factory, engine


def test_assistant_formateur_expose_tous_les_outils_documents() -> None:
    factory, engine = _session_factory()
    session = factory()
    try:
        outils = build_assistant(session, session_factory=factory).deep_tools()
        noms = {tool.name for tool in outils}
    finally:
        session.close()
        engine.dispose()

    manquants = [nom for nom in (*OUTILS_LECTURE, *OUTILS_PRODUCTION) if nom not in noms]
    assert not manquants, f"tools documentaires absents du graphe : {manquants}"
    # Les deux voies de génération demandées : le professionnel (prioritaire
    # docx/pdf) ET le document libre.
    assert "generer_document_professionnel" in noms
    assert "generer_document_html" in noms


def test_statistique_lit_les_documents_sans_capacite_d_ecriture() -> None:
    factory, engine = _session_factory()
    session = factory()
    try:
        # Même câblage que le manager : le tool de lecture est construit depuis
        # le contrat de l'agent, puis injecté.
        lecture = next(
            (
                tool
                for tool in tools_documents_pour(STAT_DEF, factory)
                if tool.name == READ_DOCUMENT_TOOL
            ),
            None,
        )
        assert lecture is not None, "read_document n'est pas construit pour le statistique"
        noms = {
            tool.name
            for tool in build_statistique(
                session, session_factory=factory, read_document_tool=lecture
            ).deep_tools()
        }
    finally:
        session.close()
        engine.dispose()

    assert set(OUTILS_LECTURE) <= noms
    # Aucun outil de production documentaire, aucune capacité d'écriture.
    assert not (set(OUTILS_PRODUCTION) & noms)
    assert "document_read" in STAT_DEF.capabilities
    assert "document_propose" not in STAT_DEF.capabilities


def test_contrats_declarent_les_outils_documents() -> None:
    """Le contrat (allow-list) et le module de tools restent d'accord."""
    from app.agents.tools.agent_assistant_formateur_tools import (
        NOMS_TOOLS as NOMS_ASSISTANT,
    )

    assert set(OUTILS_LECTURE) <= set(ASSISTANT_DEF.tools)
    assert set(OUTILS_PRODUCTION) <= set(ASSISTANT_DEF.tools)
    assert set(ASSISTANT_DEF.tools) <= set(NOMS_ASSISTANT)
    assert set(OUTILS_LECTURE) <= set(STAT_DEF.tools)
