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


__all__ = [
    "ASSISTANT_FORMATEUR_SYSTEM_PROMPT",
    "BLOC_BASE_CONNAISSANCE",
    "BLOC_RECHERCHE_WEB",
    "DOSSIER_PROMPTS",
    "GENERALISTE_SYSTEM_PROMPT",
    "GENERATEUR_OFFRE_SYSTEM_PROMPT",
    "PERSONNEL_SYSTEM_PROMPT",
    "RH_SYSTEM_PROMPT",
    "STATISTIQUE_SYSTEM_PROMPT",
    "charger_prompt",
    "chemin_prompt",
    "prompts_agents",
]
