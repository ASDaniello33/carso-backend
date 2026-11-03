"""Prompts des agents en markdown : la convention fichier ↔ agent est tenue.

Les instructions ne vivent plus dans ``app/agents/prompt.py`` mais dans
``app/agents/prompts/<agent_id>.md``. Ce déplacement ne vaut que si le nom de
fichier suit l'``agent_id`` : ajouter un agent sans son markdown, ou renommer
un fichier, doit échouer ici plutôt que produire un prompt vide à l'exécution.
"""

from __future__ import annotations

import pytest

from app.agents.catalogue import AGENT_DEFINITIONS
from app.agents.prompt import (
    BLOC_BASE_CONNAISSANCE,
    BLOC_RECHERCHE_WEB,
    DOSSIER_PROMPTS,
    charger_prompt,
    chemin_prompt,
    prompts_agents,
)


def test_chaque_agent_a_son_markdown() -> None:
    """Un fichier par agent du catalogue, à son nom — ni plus, ni moins."""
    fichiers = {chemin.name for chemin in DOSSIER_PROMPTS.glob("*.md")}
    assert fichiers == {f"{definition.agent_id}.md" for definition in AGENT_DEFINITIONS}


def test_prompt_de_l_agent_vient_du_markdown() -> None:
    """Le prompt monté est exactement le contenu du fichier (aucun doublon)."""
    agents = prompts_agents()
    for definition in AGENT_DEFINITIONS:
        assert definition.prompt == agents[definition.agent_id]
        assert len(definition.prompt) > 200, f"prompt trop court : {definition.agent_id}"


def test_blocs_communs_charges() -> None:
    """Les blocs injectés par le harnais sont eux aussi du markdown."""
    assert BLOC_RECHERCHE_WEB.startswith("## Recherche web")
    assert BLOC_BASE_CONNAISSANCE.startswith("## Base de connaissance")


def test_prompt_absent_echoue_avec_le_chemin() -> None:
    """Un markdown manquant nomme le fichier attendu, il ne rend pas du vide."""
    with pytest.raises(RuntimeError) as erreur:
        charger_prompt("agent_inexistant")
    assert "agent_inexistant.md" in str(erreur.value)


def test_prompt_ne_sort_pas_du_dossier() -> None:
    """Un nom qui remonte l'arborescence est refusé, jamais résolu."""
    with pytest.raises(RuntimeError):
        chemin_prompt("../app.main")
