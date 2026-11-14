"""Alignement prompts ↔ outils réels des agents (INC-124/125).

Un prompt qui annonce un outil absent fait appeler un outil inexistant ; un
prompt qui omet un outil disponible prive l'agent d'une capacité qu'il a. Les
deux produisent les mêmes symptômes côté utilisateur : un agent qui promet puis
échoue, ou qui dit « je ne peux pas » alors qu'il peut.

Ces tests verrouillent :

1. **contrat ↔ prompt** — pour chaque agent, tout outil de **génération** qu'il
   possède est nommé dans son prompt système (blocs inclus), et son prompt
   d'agent ne nomme aucun outil de génération qu'il n'a pas (recherche par mot
   entier : ``generer_document`` ne matche pas ``generer_document_professionnel``) ;
2. **injection ciblée** — un agent purement lecteur (généraliste, statisticien)
   ne reçoit **pas** le bloc de génération ; les agents producteurs le reçoivent ;
3. **contenu des blocs** — le bloc de génération décrit le modèle d'organisation
   (deux vocabulaires, titres exacts, sections conservées) et le bloc espace de
   travail décrit le mode ancré (ancre, livrables, code de retour, temporaire) ;
4. **skills** — les guides restent cohérents avec les contraintes réelles
   (openpyxl bloqué, modèle PDF non remplissable, remplissage par titres).
"""

from __future__ import annotations

import re

import pytest

from app.agents.catalogue import AGENT_DEFINITIONS
from app.agents.harness import build_system_prompt
from app.agents.prompt import (
    BLOC_DOCUMENTS_ET_FICHIERS,
    BLOC_ESPACE_TRAVAIL,
    BLOC_GENERATION_DOCUMENTS,
    BLOC_MODELE_ORGANISATION,
    TOOLS_DOCUMENTS,
    TOOLS_GENERATION,
    TOOLS_MODELE_ORGANISATION,
)

#: Outils de production documentaire suivis par ces tests.
OUTILS_GENERATION = (
    "generer_document",
    "generer_document_professionnel",
    "generer_document_html",
    "remplir_document_modele",
    "generer_document_modele",
    "clone_document_structure",
    "fill_template",
    "corriger_document",
    "modifier_section_document",
    "run_python_script",
    "analyze_document_reference",
)

PAR_AGENT = {definition.agent_id: definition for definition in AGENT_DEFINITIONS}


def _nomme(nom: str, texte: str) -> bool:
    """Vrai si ``nom`` apparaît comme un mot (``generer_document`` ≠ ``…_html``)."""
    motif = r"(?<![a-z0-9_])" + re.escape(nom) + r"(?![a-z0-9_])"
    return re.search(motif, texte) is not None


# --- 1. Contrat ↔ prompt ------------------------------------------------------


@pytest.mark.parametrize("agent_id", sorted(PAR_AGENT))
def test_tout_outil_de_generation_possede_est_nomme_dans_le_prompt(agent_id: str) -> None:
    definition = PAR_AGENT[agent_id]
    prompt = build_system_prompt(definition)
    manquants = [
        nom for nom in OUTILS_GENERATION if nom in definition.tools and not _nomme(nom, prompt)
    ]
    assert not manquants, (
        f"{agent_id} possède {manquants} mais son prompt n'en dit rien : "
        "l'agent ne saura pas s'en servir"
    )


@pytest.mark.parametrize("agent_id", sorted(PAR_AGENT))
def test_le_prompt_d_agent_ne_promet_aucun_outil_absent(agent_id: str) -> None:
    definition = PAR_AGENT[agent_id]
    fantomes = [
        nom
        for nom in OUTILS_GENERATION
        if nom not in definition.tools and _nomme(nom, definition.prompt)
    ]
    assert not fantomes, (
        f"{agent_id} ne possède pas {fantomes} mais son prompt les annonce : "
        "c'est un appel d'outil inexistant garanti"
    )


def test_la_chaine_modele_est_declaree_chez_le_generateur_d_offre() -> None:
    """La chaîne copier/éditer s'ancre sur une offre : un seul agent la porte."""
    definition = PAR_AGENT["agent_generateur_offre"]
    prompt = build_system_prompt(definition)
    for nom in (
        "lister_modeles_documents",
        "obtenir_structure_modele",
        "copier_modele_document",
        "obtenir_structure_document",
        "remplacer_element_model",
        "remplir_modele",
    ):
        assert nom in definition.tools, nom
        assert _nomme(nom, prompt), nom


def test_la_chaine_modele_est_aussi_chez_l_assistant_formateur() -> None:
    """Missions et sessions : c'est lui qui applique les modèles de documents."""
    definition = PAR_AGENT["agent_assistant_formateur"]
    prompt = build_system_prompt(definition)
    for nom in ("copier_modele_document", "remplacer_element_model", "remplir_modele"):
        assert nom in definition.tools, nom
        assert _nomme(nom, prompt), nom
    assert "mission" in prompt and "session" in prompt


def test_l_agent_rh_ne_promet_pas_la_chaine_modele() -> None:
    """L'agent RH n'a pas d'objet où copier un modèle : il ne doit pas le décrire."""
    definition = PAR_AGENT["agent_rh"]
    assert not {"copier_modele_document", "remplir_modele"} & set(definition.tools)
    assert not _nomme("copier_modele_document", build_system_prompt(definition))


def test_rh_peut_lire_un_modele_avant_de_le_reprendre() -> None:
    """Sans l'inventaire du modèle, reprendre sa structure revient à l'inventer."""
    definition = PAR_AGENT["agent_rh"]
    assert "analyze_document_reference" in definition.tools


# --- 2. Injection ciblée ------------------------------------------------------


def test_le_modele_d_organisation_n_est_decrit_qu_a_qui_peut_le_remplir() -> None:
    """Généraliste et statisticien produisent un PDF, mais ne remplissent pas un modèle."""
    for agent_id in ("agent_generaliste_readonly", "agent_statistique"):
        definition = PAR_AGENT[agent_id]
        assert not TOOLS_MODELE_ORGANISATION.intersection(definition.tools), agent_id
        prompt = build_system_prompt(definition)
        assert BLOC_MODELE_ORGANISATION.strip() not in prompt, agent_id
        assert "modele_documentaire.titres" not in prompt, agent_id


def test_les_agents_producteurs_recoivent_le_bloc_de_generation() -> None:
    for agent_id in (
        "agent_generateur_offre",
        "agent_assistant_formateur",
        "agent_rh",
        "agent_personnel",
    ):
        definition = PAR_AGENT[agent_id]
        assert TOOLS_GENERATION.intersection(definition.tools), agent_id
        # Le bloc doit être présent, sauf si le prompt de l'agent le documente
        # déjà lui-même (``blocs_inline``) : mêmes règles, une seule fois.
        if "generation" in definition.blocs_inline:
            assert not BLOC_GENERATION_DOCUMENTS.strip() in build_system_prompt(
                definition
            ), agent_id
        else:
            assert BLOC_GENERATION_DOCUMENTS.strip() in build_system_prompt(
                definition
            ), agent_id


def test_le_bloc_du_modele_va_aux_agents_qui_editent_une_copie() -> None:
    """Le bloc décrit la copie et l'édition : générateur d'offre et formateur."""
    for agent_id in ("agent_generateur_offre", "agent_assistant_formateur"):
        definition = PAR_AGENT[agent_id]
        assert TOOLS_MODELE_ORGANISATION.intersection(definition.tools), agent_id
        if "modele" in definition.blocs_inline:
            assert BLOC_MODELE_ORGANISATION.strip() not in build_system_prompt(
                definition
            ), agent_id
        else:
            assert BLOC_MODELE_ORGANISATION.strip() in build_system_prompt(
                definition
            ), agent_id
    autre = PAR_AGENT["agent_rh"]
    assert not TOOLS_MODELE_ORGANISATION.intersection(autre.tools)
    assert BLOC_MODELE_ORGANISATION.strip() not in build_system_prompt(autre)


def test_le_bloc_de_lecture_reste_injecte_pour_un_agent_documentaire() -> None:
    definition = PAR_AGENT["agent_generaliste_readonly"]
    assert TOOLS_DOCUMENTS.intersection(definition.tools)
    assert BLOC_DOCUMENTS_ET_FICHIERS.strip() in build_system_prompt(definition)


# --- 3. Contenu des blocs -----------------------------------------------------


def test_le_bloc_du_modele_decrit_la_copie_et_l_edition() -> None:
    bloc = BLOC_MODELE_ORGANISATION
    for attendu in (
        "copier_modele_document",
        "remplacer_element_model",
        "remplir_modele",
        "data:image",
        "parties du paquet",
        "relations",
    ):
        assert attendu in bloc, attendu


def test_le_bloc_du_modele_dit_qu_il_n_y_a_pas_de_placeholders() -> None:
    """Les modèles réels n'ont pas de `{{clé}}` : le bloc ne doit pas les promettre."""
    bloc = BLOC_MODELE_ORGANISATION
    assert "n'ont pas de `{{placeholder}}`" in bloc
    assert "generer_document_professionnel" in bloc


def test_le_bloc_de_generation_rappelle_le_contrat_d_outils() -> None:
    """Un mode d'emploi ne doit jamais autoriser un outil absent du contrat."""
    assert "Tu n'utilises que les outils de ton contrat" in BLOC_GENERATION_DOCUMENTS


def test_le_bloc_espace_de_travail_decrit_le_mode_ancre() -> None:
    bloc = BLOC_ESPACE_TRAVAIL
    for attendu in ("ancre", "livrables", "entity_id", "type_document", "code_retour"):
        assert attendu in bloc, attendu
    # Les deux pièges mesurés : dépôt partiel et absence de dépôt sur échec.
    assert "aucun dépôt" in bloc
    assert "refuses" in bloc


def test_le_bloc_espace_de_travail_dit_qu_openpyxl_est_bloque() -> None:
    """La sandbox refuse openpyxl : annoncer le contraire fait boucler l'agent."""
    assert "openpyxl" in BLOC_ESPACE_TRAVAIL
    assert "classeur" in BLOC_ESPACE_TRAVAIL


# --- 4. Skills ----------------------------------------------------------------


def _skill(nom: str) -> str:
    from app.agents.prompt import DOSSIER_PROMPTS

    return (DOSSIER_PROMPTS.parent / "skills" / nom / "SKILL.md").read_text(encoding="utf-8")


def test_le_skill_scripts_python_decrit_le_mode_ancre_et_la_vraie_sandbox() -> None:
    texte = _skill("scripts-python")
    assert "livrables" in texte
    assert "ancre" in texte
    assert "classeur" in texte
    # openpyxl est refusé à l'exécution : le skill ne doit pas le proposer.
    assert "openpyxl" in texte
    assert "bloqué" in texte


def test_le_skill_docx_route_vers_la_copie_du_modele() -> None:
    texte = _skill("docx-officiels")
    for attendu in ("copier", "data:image", "element_id", "HTML stylisé"):
        assert attendu in texte, attendu
    # Le skill est partagé par plusieurs agents : il ne nomme pas les outils de
    # la chaîne modèle, que seul le générateur d'offre possède.
    assert "copier_modele_document" not in texte


def test_le_skill_pdf_ne_promet_pas_de_remplir_un_modele_pdf() -> None:
    texte = _skill("pdf-officiels")
    assert "ne se remplit pas" in texte or "refuse" in texte
    assert "identité visuelle" in texte


def test_le_skill_generation_document_couvre_modeles_et_scripts() -> None:
    texte = _skill("generation-document")
    assert "copier le modèle" in texte
    assert "livrables" in texte
