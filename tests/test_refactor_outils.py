"""Tests du refactor outils agents (docs/agents/REFACTOR-OUTILS-AGENTS.md).

Couvre :

1. **Un lieu par agent** : chaque module ``agent_*_tools.py`` expose les tools
   du contrat — et le harnais refuse tout tool non déclaré (déjà testé) ;
2. **Annuaire** : le bloc markdown liste les agents appelables (grant = source
   de vérité) et est injecté dans le system prompt ;
3. **Matrice d'accès** : généraliste/statistique strictement readonly (une
   mutation lève à la construction) ; écritures HITL déclarées chez le
   générateur, RH et l'assistant ;
4. **Backend routé** : le storage CARSO est monté sous ``/storage/`` (lecture
   des vrais fichiers), l'état du graphe reste le défaut.
"""

from __future__ import annotations

import pytest

from app.agents.annuaire import bloc_annuaire
from app.agents.harness import build_system_prompt

# --- 2. Annuaire ----------------------------------------------------------------


def test_annuaire_sans_collaboration_connait_les_autres() -> None:
    """Un agent sans grant connaît les autres (id + description) mais n'a
    aucune section « que tu peux appeler » : la connaissance ≠ autorisation."""
    from app.agents.generaliste_readonly import DEFINITION

    assert DEFINITION.collaboration == ()
    prompt = build_system_prompt(DEFINITION)
    assert "## Agents CARSO" in prompt
    assert "`agent_rh`" in prompt
    assert "`agent_statistique`" in prompt
    assert "Agents CARSO que tu peux appeler" not in prompt


def test_annuaire_liste_les_agents_appelables_du_generateur() -> None:
    from app.agents.agent_rh import DEFINITION as RH_DEF
    from app.agents.generateur_offre import DEFINITION

    connus = {RH_DEF.agent_id: RH_DEF}
    bloc = bloc_annuaire(DEFINITION, connus)
    # Le grant du générateur est décrit avec son identifiant et son type de tâche.
    assert "`agent_rh`" in bloc
    assert "propose_team_assignment" in bloc
    # La description vient du contrat, rien d'inventé.
    assert RH_DEF.description in bloc
    # L'agent analyseur a été supprimé : aucune trace dans l'annuaire.
    assert "analyseur" not in bloc


def test_annuaire_injecte_dans_le_prompt_du_generateur() -> None:
    from app.agents.generateur_offre import DEFINITION

    prompt = build_system_prompt(DEFINITION)
    assert "Agents CARSO que tu peux appeler" in prompt
    assert "`agent_rh`" in prompt
    assert "analyseur" not in prompt.split("Agents CARSO que tu peux appeler")[1]


def test_annuaire_hors_grant_connu_mais_non_appelable() -> None:
    """Un agent hors grant figure dans la liste (connaissance) mais pas
    dans « que tu peux appeler » : la découverte ne contourne jamais
    l'autorisation (refus par défaut)."""
    from app.agents.agent_statistique import DEFINITION as STAT_DEF
    from app.agents.generaliste_readonly import DEFINITION as GEN_DEF

    bloc = bloc_annuaire(STAT_DEF, {GEN_DEF.agent_id: GEN_DEF})
    assert "`agent_generaliste_readonly`" in bloc
    assert "Agents CARSO que tu peux appeler" not in bloc


# --- 1. Un lieu par agent : périmètres complets ----------------------------------


def test_modules_exposent_le_perimetre_complet() -> None:
    from app.agents.agent_rh import DEFINITION as RH_DEF
    from app.agents.agent_statistique import DEFINITION as STAT_DEF
    from app.agents.assistant_formateur import DEFINITION as ASS_DEF
    from app.agents.generaliste_readonly import DEFINITION as GEN_DEF
    from app.agents.generateur_offre import DEFINITION as GENO_DEF
    from app.agents.tools import (
        NOMS_TOOLS_ASSISTANT,
        NOMS_TOOLS_GENERALISTE,
        NOMS_TOOLS_GENERATEUR,
        NOMS_TOOLS_RH,
        NOMS_TOOLS_STATISTIQUE,
    )

    # Chaque tool du contrat est couvert par le module du même périmètre
    # (le harnais, lui, refuse tout tool fourni hors contrat).
    assert set(RH_DEF.tools) <= set(NOMS_TOOLS_RH)
    assert set(STAT_DEF.tools) <= set(NOMS_TOOLS_STATISTIQUE)
    assert set(ASS_DEF.tools) <= set(NOMS_TOOLS_ASSISTANT)
    assert set(GEN_DEF.tools) <= set(NOMS_TOOLS_GENERALISTE)
    assert set(GENO_DEF.tools) <= set(NOMS_TOOLS_GENERATEUR)


# --- 3. Matrice d'accès -----------------------------------------------------------


def _agent_tools(deep_tools) -> dict[str, str]:
    return {tool.name: tool.name for tool in deep_tools}


def test_generaliste_kit_strictement_readonly() -> None:
    """Toute opération de mutation serait refusée à la construction (kit)."""
    from app.agents.generaliste_readonly import DEFINITION
    from app.agents.toolkit import AgentTools
    from app.core.errors import PermissionDeniedError

    kit = AgentTools(DEFINITION, readonly=True)
    with pytest.raises(PermissionDeniedError):
        from pydantic import BaseModel

        class _X(BaseModel):
            v: int

        from app.agents.toolkit import OperationSpec

        kit.enregistrer(OperationSpec(
            name="operation_interdite",
            description="serait refusée",
            input_schema=_X,
            mutation=True,
            run=lambda session, payload: {},
        ))


def test_statistique_kit_strictement_readonly() -> None:
    from pydantic import BaseModel

    from app.agents.agent_statistique import DEFINITION
    from app.agents.toolkit import AgentTools, OperationSpec
    from app.core.errors import PermissionDeniedError

    kit = AgentTools(DEFINITION, readonly=True)
    with pytest.raises(PermissionDeniedError):
        class _X(BaseModel):
            v: int

        kit.enregistrer(OperationSpec(
            name="operation_interdite",
            description="serait refusée",
            input_schema=_X,
            mutation=True,
            run=lambda session, payload: {},
        ))


#: Préfixes des tools qui **écrivent** une donnée métier existante. Les agents
#: de consultation n'en ont aucun ; seul un **nouveau** livrable peut être
#: déposé (statut ``proposed``), ce qui ne modifie rien d'existant.
PREFIXES_ECRITURE = (
    "create_",
    "update_",
    "delete_",
    "import_",
    "propose_",
    "enregistrer_",
    "supprimer_",
    "envoyer_",
    "submit_",
)


def test_statistique_aucune_ecriture_de_donnee_au_contrat() -> None:
    from app.agents.agent_statistique import DEFINITION

    assert DEFINITION.approval_required == ()
    assert all(not nom.startswith(PREFIXES_ECRITURE) for nom in DEFINITION.tools)
    # La seule écriture admise : un livrable, qui ne vaut jamais approbation.
    assert "generer_document_professionnel" in DEFINITION.tools
    assert "document_propose" in DEFINITION.capabilities


def test_generaliste_aucune_ecriture_de_donnee_au_contrat() -> None:
    from app.agents.generaliste_readonly import DEFINITION

    assert all(not nom.startswith(PREFIXES_ECRITURE) for nom in DEFINITION.tools)
    assert "generer_document_professionnel" in DEFINITION.tools
    assert "document_propose" in DEFINITION.capabilities


def test_ecritures_hitl_declarees() -> None:
    """Toute écriture déclarée est listée dans ``approval_required`` (HITL)."""
    from app.agents.agent_rh import DEFINITION as RH_DEF
    from app.agents.assistant_formateur import DEFINITION as ASS_DEF
    from app.agents.generateur_offre import DEFINITION as GENO_DEF

    assert "create_assignment_proposal" in RH_DEF.approval_required
    assert "propose_equipe_update" in RH_DEF.approval_required
    assert "propose_mission_update" in RH_DEF.approval_required
    assert "import_beneficiaires" in ASS_DEF.approval_required
    assert "generer_document" in ASS_DEF.approval_required
    assert "create_offer_draft" in GENO_DEF.approval_required
    assert "propose_lot_update" in GENO_DEF.approval_required
    assert "propose_appel_update" in GENO_DEF.approval_required
    # Généraliste / statistique : aucune écriture du tout.
    from app.agents.agent_statistique import DEFINITION as STAT_DEF
    from app.agents.generaliste_readonly import DEFINITION as GEN_DEF

    assert GEN_DEF.approval_required == ()
    assert STAT_DEF.approval_required == ()


def test_generaliste_couve_toutes_les_tables() -> None:
    """Le généraliste lit désormais les domaines élargis (matrice : tout en
    lecture) : lots, offres, sessions, bénéficiaires, affectations."""
    from app.agents.generaliste_readonly import DEFINITION

    requis = {
        "search_organisations",
        "search_appels_a_proposition",
        "search_lots",
        "search_offres",
        "search_missions",
        "search_sessions",
        "search_beneficiaires",
        "search_equipes",
        "search_affectations",
        "search_documents",
    }
    assert requis <= set(DEFINITION.tools)


def test_statistique_couvre_toutes_les_tables() -> None:
    from app.agents.agent_statistique import DEFINITION

    requis = {
        "query_analytics",
        "search_organisations",
        "search_appels_a_proposition",
        "search_lots",
        "search_offres",
        "search_missions",
        "search_sessions",
        "search_beneficiaires",
        "search_equipes",
        "search_affectations",
        "search_documents",
    }
    assert requis <= set(DEFINITION.tools)


def test_assistant_couvre_lecture_mission_complet() -> None:
    """L'assistant lit Mission / sessions / équipe / lot / offre d'une mission."""
    from app.agents.assistant_formateur import DEFINITION

    requis = {
        "get_mission",
        "list_mission_sessions",
        "list_session_beneficiaires",
        "get_mission_offre",
        "get_mission_lots",
        "list_mission_equipe",
        "list_mission_documents",
    }
    assert requis <= set(DEFINITION.tools)


def test_generateur_lit_les_autres_tables() -> None:
    """Hors Document/Appel/Lot/Offre : lecture seule des autres domaines."""
    from app.agents.generateur_offre import DEFINITION

    requis = {
        "search_organisations",
        "search_missions",
        "search_sessions",
        "search_beneficiaires",
        "search_equipes",
        "search_affectations",
        "search_documents",
    }
    assert requis <= set(DEFINITION.tools)


def test_rh_lit_les_autres_tables() -> None:
    from app.agents.agent_rh import DEFINITION

    requis = {
        "search_organisations",
        "search_appels_a_proposition",
        "search_lots",
        "search_offres",
        "search_sessions",
        "search_beneficiaires",
        "search_documents",
    }
    assert requis <= set(DEFINITION.tools)


def test_generateur_lit_les_lots() -> None:
    """Les lots se lisent depuis l'agent unique de la chaîne de l'appel."""
    from app.agents.generateur_offre import DEFINITION

    assert "list_lots" in DEFINITION.tools


# --- 4. Backend routé (sandbox fichiers) -------------------------------------------


def test_backend_route_le_storage_carso(tmp_path) -> None:
    """Le CompositeBackend expose le storage sous /storage/ : un fichier écrit
    dans le storage est lisible par les tools fichiers du harness."""
    from deepagents.backends import CompositeBackend, FilesystemBackend, StateBackend

    from app.agents.harness import _backend_fichiers

    class _Settings:
        storage_root = str(tmp_path)

    backend = _backend_fichiers(_Settings())
    assert isinstance(backend, CompositeBackend)
    assert isinstance(backend.default, StateBackend)
    assert isinstance(backend.routes["/storage/"], FilesystemBackend)

    # Un fichier réel du storage est lisible via le chemin virtuel.
    cible = tmp_path / "missions" / "test.txt"
    cible.parent.mkdir(parents=True)
    cible.write_text("contenu réel", encoding="utf-8")
    lu = backend.read("/storage/missions/test.txt")
    assert "contenu réel" in str(lu)


def test_prompt_generaliste_connait_les_agents_sans_pouvoir_les_appeler() -> None:
    from app.agents.generaliste_readonly import DEFINITION

    prompt = build_system_prompt(DEFINITION)
    assert "Cadre CARSO" in prompt
    assert "## Agents CARSO" in prompt
    assert "`agent_rh`" in prompt
    assert "Agents CARSO que tu peux appeler" not in prompt
