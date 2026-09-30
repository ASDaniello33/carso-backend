"""Registre des skills Deep Agents (Lot T1, clinrules 08 / deep-agents-core).

Un skill CARSO est un répertoire ``<skills_root>/<nom>/SKILL.md`` avec
frontmatter YAML (``name``, ``description``). Ce n'est **pas** du code
exécuté : Deep Agents le charge à la demande via ``SkillsMiddleware``.

Le harnais ne transmet jamais un chemin libre au runtime : seuls les noms
déclarés dans ``AgentDefinition.skills`` sont résolus. Un nom inconnu ou
un skill mal formé lève une erreur explicite (jamais un chargement silencieux).
"""

from __future__ import annotations

from pathlib import Path

from app.core.config import Settings, get_settings
from app.core.errors import ValidationError as BusinessValidationError

_NOM_FICHIER = "SKILL.md"
_FRONTMATTER = "---"


def racine_skills(settings: Settings | None = None) -> Path:
    """Racine des skills : ``Settings.skills_root`` ou ``<repo>/skills/agents``."""
    cfg = settings or get_settings()
    if cfg.skills_root.strip():
        return Path(cfg.skills_root).expanduser().resolve()
    # backend/app/agents/skills.py → repo = parents[3]
    return Path(__file__).resolve().parents[3] / "skills" / "agents"


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
        _verifier_frontmatter(manifeste, nom)
        resolus.append(str(dossier))
    return resolus


def _verifier_frontmatter(chemin: Path, nom: str) -> None:
    """Un ``SKILL.md`` Deep Agents commence par un frontmatter YAML."""
    texte = chemin.read_text(encoding="utf-8")
    if not texte.lstrip().startswith(_FRONTMATTER):
        raise BusinessValidationError(
            f"SKILL.md de {nom!r} sans frontmatter YAML",
            details={"skill": nom, "fichier": str(chemin)},
        )


__all__ = ["lister_skills", "racine_skills", "resoudre_skills"]
