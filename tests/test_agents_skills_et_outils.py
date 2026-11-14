"""Chaque outil d'agent a le guide (skill) qui l'accompagne.

Un agent qui peut générer un livrable, lire un classeur DOCX/PDF ou exécuter un
script doit pouvoir charger le guide correspondant : sans lui, il improvise la
méthode alors que le skill décrit les recettes validées. Les skills déclarés
doivent aussi porter une description lisible (c'est elle qui entre dans le
prompt système).
"""

from __future__ import annotations

from app.agents.catalogue import AGENT_DEFINITIONS
from app.agents.registre_skills import description_skill, racine_skills

RACINE = racine_skills()

#: Outil → skill qui décrit sa méthode.
SKILLS_ATTENDUS: dict[str, str] = {
    "run_python_script": "scripts-python",
    "generer_document": "generation-document",
    "generer_document_professionnel": "generation-document",
    "generer_document_html": "generation-document",
    "get_document_structure": "pdf-officiels",
    "importer_tableau_xlsx": "xlsx-officiels",
    "remplir_modele": "docx-officiels",
}


def test_les_agents_ont_les_skills_de_leurs_outils() -> None:
    for definition in AGENT_DEFINITIONS:
        outils = set(definition.tools)
        attendus = {
            skill for outil, skill in SKILLS_ATTENDUS.items() if outil in outils
        }
        manquants = attendus - set(definition.skills)
        assert not manquants, f"{definition.agent_id} : skills manquants {manquants}"


def test_aucun_outil_n_est_declare_deux_fois() -> None:
    """Un doublon dans un contrat est une confusion, pas une redondance."""
    for definition in AGENT_DEFINITIONS:
        doublons = {nom for nom in definition.tools if definition.tools.count(nom) > 1}
        assert not doublons, f"{definition.agent_id} : doublons {sorted(doublons)}"


def test_chaque_skill_declare_a_une_description() -> None:
    for definition in AGENT_DEFINITIONS:
        for nom in definition.skills:
            assert (RACINE / nom / "SKILL.md").is_file(), (definition.agent_id, nom)
            assert description_skill(RACINE / nom / "SKILL.md").strip(), (
                definition.agent_id,
                nom,
            )
