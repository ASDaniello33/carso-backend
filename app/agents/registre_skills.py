"""Registre des skills Deep Agents (Lot T1, clinrules 08 / deep-agents-core).

Un skill CARSO est un répertoire ``backend/app/agents/skills/<nom>/SKILL.md``
avec frontmatter YAML (``name``, ``description``). Ce n'est **pas** du code
exécuté : Deep Agents le charge à la demande via ``SkillsMiddleware``.

Deux rôles :

1. **Résolution** (``resoudre_skills``) : le harnais ne transmet jamais un
   chemin libre au runtime — seuls les noms déclarés dans
   ``AgentDefinition.skills`` sont résolus. Un nom inconnu ou un skill mal
   formé lève une erreur explicite (jamais un chargement silencieux).
2. **Contexte** (``bloc_skills``) : le prompt système de chaque agent liste
   les skills qui lui sont déclarés (nom + description du frontmatter) —
   l'agent SAIT ce qu'il peut charger et quand, sans deviner l'arborescence.
"""

from __future__ import annotations

import re
from pathlib import Path

from app.agents.base import AgentDefinition
from app.core.config import Settings, get_settings
from app.core.errors import ValidationError as BusinessValidationError

_NOM_FICHIER = "SKILL.md"
_FRONTMATTER = "---"
#: ``description:`` simple, ``>-``/``|-`` plié (le texte suit, indenté).
_RE_DESCRIPTION = re.compile(r"^description:\s*(>?-?)\s*$|^description:\s+(.+)$", re.M)
_RE_NOM = re.compile(r"^name:\s*(.+)$", re.M)


def racine_skills(settings: Settings | None = None) -> Path:
    """Racine des skills : ``Settings.skills_root`` ou ``app/agents/skills``."""
    cfg = settings or get_settings()
    if cfg.skills_root.strip():
        return Path(cfg.skills_root).expanduser().resolve()
    #backend/app/agents/registre_skills.py → dossier ``skills`` voisin du module :
    # les skills voyagent avec le paquet (image Docker, installation).
    return Path(__file__).resolve().parent / "skills"


def lister_skills(settings: Settings | None = None) -> list[str]:
    """Noms des skills valides (répertoire contenant ``SKILL.md``)."""
    racine = racine_skills(settings)
    if not racine.is_dir():
        return []
    noms: list[str] = []
    for enfant in sorted(racine.iterdir()):
        if enfant.is_dir() and (enfant / _NOM_FICHIER).is_file():
            noms.append(enfant.name)
    return noms


def resoudre_skills(
    noms: tuple[str, ...] | list[str],
    *,
    settings: Settings | None = None,
) -> list[str]:
    """Résout les noms de skills en chemins absolus pour ``create_deep_agent``.

    Raises:
        BusinessValidationError: nom inconnu, chemin hors racine, ou
            ``SKILL.md`` absent / sans frontmatter.
    """
    if not noms:
        return []
    racine = racine_skills(settings)
    resolus: list[str] = []
    for nom in noms:
        if not nom or "/" in nom or "\\" in nom or ".." in nom:
            raise BusinessValidationError(
                f"Nom de skill invalide : {nom!r}",
                details={"skill": nom},
            )
        dossier = (racine / nom).resolve()
        try:
            dossier.relative_to(racine)
        except ValueError as exc:
            raise BusinessValidationError(
                f"Skill hors racine : {nom!r}",
                details={"skill": nom},
            ) from exc
        manifeste = dossier / _NOM_FICHIER
        if not manifeste.is_file():
            disponibles = lister_skills(settings)
            raise BusinessValidationError(
                f"Skill inconnu : {nom!r}",
                details={"skill": nom, "disponibles": disponibles},
            )
        if frontmatter_skill(manifeste) is None:
            raise BusinessValidationError(
                f"SKILL.md de {nom!r} sans frontmatter YAML",
                details={"skill": nom, "fichier": str(manifeste)},
            )
        resolus.append(str(dossier))
    return resolus


def bloc_skills(
    definition: AgentDefinition, *, settings: Settings | None = None
) -> str:
    """Bloc de prompt listant les skills déclarés de l'agent (nom + description).

    Vide si l'agent ne déclare aucun skill. Une description absente ou
    illisible n'est jamais devinée : le skill est listé sans description et
    l'agent le lit au besoin — le ``SKILL.md`` reste la source de vérité.
    """
    if not definition.skills:
        return ""
    racine = racine_skills(settings)
    lignes: list[str] = []
    for nom in definition.skills:
        # Un skill cassé est levé au montage par ``resoudre_skills`` : ici, un
        # nom sans description est simplement listé nu, jamais deviné.
        description = description_skill(racine / nom / _NOM_FICHIER)
        lignes.append(f"- {nom}" + (f" : {description}" if description else ""))
    return "\n".join(
        [
            "## Skills disponibles",
            "",
            "Des guides spécialisés te sont déclarés : quand ta tâche relève du",
            "domaine d'un skill, LIS son SKILL.md (les skills sont exposés par le",
            "système de fichiers de Deep Agents) et applique ses recettes.",
            "",
            *lignes,
        ]
    )


def description_skill(chemin: Path) -> str:
    """Description du frontmatter d'un ``SKILL.md`` (scalaire plié YAML inclus)."""
    try:
        bloc = frontmatter_skill(chemin)
    except OSError:
        return ""
    if bloc is None:
        return ""
    return _description_pliee(bloc)


def frontmatter_skill(chemin: Path) -> str | None:
    """Contenu entre les deux ``---`` d'un ``SKILL.md``, ou ``None``.

    Raises:
        OSError: fichier illisible (le cas ``SKILL.md`` obligatoirement absent
            est déjà traité par ``resoudre_skills``).
    """
    texte = chemin.read_text(encoding="utf-8")
    lignes = texte.lstrip().splitlines()
    if not lignes or lignes[0].strip() != _FRONTMATTER:
        return None
    for index, ligne in enumerate(lignes[1:], start=1):
        if ligne.strip() == _FRONTMATTER:
            return "\n".join(lignes[1:index])
    return None


def _description_pliee(bloc: str) -> str:
    """Valeur de ``description:`` — simple ligne ou bloc plié (``>``, ``|-``)."""
    correspondance = _RE_DESCRIPTION.search(bloc)
    if correspondance is None:
        return ""
    if correspondance.group(2):
        return correspondance.group(2).strip()
    # Bloc plié : les lignes suivantes, plus indentées que la clé, sont le texte.
    morceaux: list[str] = []
    for ligne in bloc.splitlines():
        retiree = ligne.lstrip()
        if not retiree or retiree.startswith(("name:", "description:")):
            continue
        if len(ligne) - len(retiree) == 0:
            break  # une autre clé de premier niveau : le bloc est terminé
        morceaux.append(retiree)
    return " ".join(" ".join(morceaux).split())


__all__ = [
    "bloc_skills",
    "description_skill",
    "lister_skills",
    "racine_skills",
    "resoudre_skills",
]
