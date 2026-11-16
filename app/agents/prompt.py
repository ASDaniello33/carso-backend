"""System prompts des agents CARSO — chargés depuis ``app/agents/prompts/*.md``.

Un prompt est du **contenu éditorial**, pas du code. Il vit donc en markdown,
dans ``backend/app/agents/prompts/`` :

```text
prompts/
├── agent_generateur_offre.md      (un fichier par agent du catalogue)
├── agent_rh.md
├── agent_assistant_formateur.md
├── agent_generaliste_readonly.md
├── agent_statistique.md
├── agent_personnel.md
└── blocs/
    ├── recherche_web.md           (blocs communs, injectés par le harnais)
    └── base_connaissance.md
```

Ce module ne contient **plus aucune instruction** : il ne fait que lire ces
fichiers. Corriger une consigne — aligner deux agents, préciser un contrat
d'outil — se fait désormais dans le markdown, sans toucher à du Python ni
risquer une chaîne mal échappée.

Les noms de constantes sont conservés (``GENERATEUR_OFFRE_SYSTEM_PROMPT``…) :
les modules d'agents et les tests s'y réfèrent, la valeur vient du fichier. Le
chargement a lieu à l'import : un markdown absent ou vide fait échouer le
démarrage avec le chemin fautif, jamais un prompt silencieusement vide.
"""

from __future__ import annotations

from pathlib import Path

#: Répertoire des prompts, voisin du module : ils voyagent avec le paquet
#: (``[tool.setuptools.package-data]`` déclare ``prompts/**/*.md``).
DOSSIER_PROMPTS = Path(__file__).resolve().parent / "prompts"


def chemin_prompt(relatif: str) -> Path:
    """Chemin absolu d'un prompt, **jamais** hors du dossier des prompts.

    Raises:
        RuntimeError: nom qui sortirait du répertoire (``..``, chemin absolu).
    """
    chemin = (DOSSIER_PROMPTS / f"{relatif}.md").resolve()
    try:
        chemin.relative_to(DOSSIER_PROMPTS)
    except ValueError as exc:
        raise RuntimeError(
            f"Prompt hors du dossier {DOSSIER_PROMPTS} : {relatif!r}"
        ) from exc
    return chemin


def charger_prompt(relatif: str) -> str:
    """Contenu d'un prompt markdown, sans espaces de bord.

    Args:
        relatif: nom sans extension, relatif au dossier des prompts
            (``"agent_rh"``, ``"blocs/recherche_web"``).

    Raises:
        RuntimeError: fichier absent, illisible ou vide — le message porte le
            chemin, pour qu'un renommage se voie au démarrage.
    """
    chemin = chemin_prompt(relatif)
    try:
        contenu = chemin.read_text(encoding="utf-8")
    except OSError as exc:
        raise RuntimeError(f"Prompt d'agent introuvable ou illisible : {chemin}") from exc
    texte = contenu.strip()
    if not texte:
        raise RuntimeError(f"Prompt d'agent vide : {chemin}")
    return texte


def prompts_agents() -> dict[str, str]:
    """Prompts des agents du catalogue, indexés par ``agent_id``.

    La correspondance nom de fichier ↔ identifiant est la seule convention à
    respecter : ``agent_rh`` lit ``prompts/agent_rh.md``.
    """
    from app.agents.catalogue import AGENT_DEFINITIONS

    return {
        definition.agent_id: charger_prompt(definition.agent_id)
        for definition in AGENT_DEFINITIONS
    }


GENERATEUR_OFFRE_SYSTEM_PROMPT = charger_prompt("agent_generateur_offre")
RH_SYSTEM_PROMPT = charger_prompt("agent_rh")
ASSISTANT_FORMATEUR_SYSTEM_PROMPT = charger_prompt("agent_assistant_formateur")
GENERALISTE_SYSTEM_PROMPT = charger_prompt("agent_generaliste_readonly")
STATISTIQUE_SYSTEM_PROMPT = charger_prompt("agent_statistique")
PERSONNEL_SYSTEM_PROMPT = charger_prompt("agent_personnel")

#: Blocs communs, injectés par ``build_system_prompt`` selon les outils réels
#: de l'agent (recherche web, base de connaissance).
BLOC_RECHERCHE_WEB = charger_prompt("blocs/recherche_web")
BLOC_BASE_CONNAISSANCE = charger_prompt("blocs/base_connaissance")
#: Bloc « documents et fichiers » : injecté aux agents qui disposent d'au moins
#: un outil documentaire (lecture, création ou modification).
BLOC_DOCUMENTS_ET_FICHIERS = charger_prompt("blocs/documents_et_fichiers")
#: Bloc « génération de documents » : injecté **uniquement** aux agents qui
#: peuvent produire un livrable. Séparé du bloc de lecture pour qu'un agent
#: seulement lecteur (généraliste, statisticien) ne reçoive pas de mode d'emploi
#: d'outils qu'il n'a pas — cause directe d'appels d'outils inexistants.
BLOC_GENERATION_DOCUMENTS = charger_prompt("blocs/generation_documents")
#: Bloc « éditer le modèle d'une organisation » : injecté **seulement** aux
#: agents qui portent la chaîne copier/éditer. Décrire cette chaîne à un agent
#: qui ne l'a pas, c'est lui faire promettre un geste impossible.
BLOC_MODELE_ORGANISATION = charger_prompt("blocs/modele_organisation")
#: Bloc « espace de travail » : injecté aux agents qui portent
#: ``run_python_script`` (INC-116) — où écrire, ce qui persiste, ce qui est refusé.
BLOC_ESPACE_TRAVAIL = charger_prompt("blocs/espace_travail")

#: Outils qui signalent un agent capable de lire, créer ou modifier un document
#: métier — condition d'injection du bloc ci-dessus.
TOOLS_DOCUMENTS: frozenset[str] = frozenset(
    {
        "lire_document",
        "get_document_structure",
        "read_document_range",
        "chercher_dans_document",
        "lire_element_document",
        "generer_document",
        "generer_document_professionnel",
        "modifier_section_document",
        "corriger_document",
        "copier_modele_document",
        "remplacer_element_model",
        "remplir_modele",
        "revenir_version_document",
        "importer_tableau_xlsx",
        "analyser_reference_document",
        "analyze_document_reference",
    }
)

#: Outils qui signalent un agent capable de **produire** un livrable — condition
#: d'injection du bloc de génération (routes, fidélité aux modèles, pièges).
TOOLS_GENERATION: frozenset[str] = frozenset(
    {
        "generer_document",
        "generer_document_professionnel",
        "generer_document_html",
        "copier_modele_document",
        "remplacer_element_model",
        "remplir_modele",
        "revenir_version_document",
        "corriger_document",
        "modifier_section_document",
        "run_python_script",
    }
)

#: Outils qui ouvrent la route « modèle du client » — découverte, structure,
#: copie du fichier dans l'offre puis édition de la copie.
TOOLS_MODELE_ORGANISATION: frozenset[str] = frozenset(
    {
        "lister_modeles_documents",
        "obtenir_structure",
        "copier_modele_document",
        "remplacer_element_model",
        "remplir_modele",
        "revenir_version_document",
    }
)


__all__ = [
    "ASSISTANT_FORMATEUR_SYSTEM_PROMPT",
    "BLOC_BASE_CONNAISSANCE",
    "BLOC_DOCUMENTS_ET_FICHIERS",
    "BLOC_ESPACE_TRAVAIL",
    "BLOC_GENERATION_DOCUMENTS",
    "BLOC_MODELE_ORGANISATION",
    "BLOC_RECHERCHE_WEB",
    "DOSSIER_PROMPTS",
    "GENERALISTE_SYSTEM_PROMPT",
    "GENERATEUR_OFFRE_SYSTEM_PROMPT",
    "PERSONNEL_SYSTEM_PROMPT",
    "RH_SYSTEM_PROMPT",
    "STATISTIQUE_SYSTEM_PROMPT",
    "TOOLS_DOCUMENTS",
    "TOOLS_GENERATION",
    "TOOLS_MODELE_ORGANISATION",
    "charger_prompt",
    "chemin_prompt",
    "prompts_agents",
]
