"""Compacité du prompt du générateur, sans perte de règle.

Le prompt dépassait 27 k tokens. Deux leviers, mesurés par
``scripts/audit_outils_agents.py`` :

- la « leçon » de génération d'offre est condensée (mêmes règles) ;
- les blocs génériques « génération » et « modèle » ne sont plus ajoutés :
  les instructions du générateur les décrivent déjà (``blocs_inline``).
"""

from __future__ import annotations

import pytest

from app.agents.base import AgentDefinition
from app.agents.generateur_offre import DEFINITION
from app.agents.harness import build_system_prompt
from app.agents.prompt import (
    BLOC_DOCUMENTS_ET_FICHIERS,
    BLOC_GENERATION_DOCUMENTS,
    BLOC_MODELE_ORGANISATION,
)
from app.core.errors import ValidationError


def test_le_prompt_du_generateur_ne_repete_pas_les_blocs_inline() -> None:
    prompt = build_system_prompt(DEFINITION)

    assert BLOC_GENERATION_DOCUMENTS not in prompt
    assert BLOC_MODELE_ORGANISATION not in prompt
    # Le bloc documents (lecture des pièces) reste dû : il n'est pas inline.
    assert BLOC_DOCUMENTS_ET_FICHIERS in prompt


def test_les_regles_de_generation_restent_presentes() -> None:
    prompt = build_system_prompt(DEFINITION)

    for extrait in (
        "copier_modele_document",
        "remplir_modele",
        "generer_document_professionnel",
        "generer_document_html",
        "document_spec",
        "proposer_telechargement_document",
        "graphiques",
        "base64",
        "(à compléter",
        "OFFRE_TECHNIQUE",
        "OFFRE_FINANCIERE",
        "Matrice de conformité",
    ):
        assert extrait in prompt, extrait


def test_le_prompt_du_generateur_est_borne() -> None:
    """Garde-fou de taille : prompt d'agent resserré + bloc documents ajouté."""
    assert len(build_system_prompt(DEFINITION)) < 90_000


def test_un_bloc_inline_inconnu_est_refuse() -> None:
    definition = AgentDefinition(
        agent_id="agent_test",
        display_name="Test",
        description="Test",
        policy=DEFINITION.policy,
        blocs_inline=("inexistant",),
    )
    with pytest.raises(ValidationError):
        build_system_prompt(definition)
