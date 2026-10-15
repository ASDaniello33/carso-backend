"""Annuaire des agents CARSO (refactor collaboration, point 2).

Chaque agent reçoit dans son system prompt **l'identifiant et la description**
de tous les autres agents (connaissance). L'appel lui-même reste porté par
``request_agent_task`` et n'est autorisé que si le contrat porte un
``CollaborationGrant`` (refus par défaut, AGENTS.md §1.10).

Aucune information inventée : les descriptions viennent des contrats
(``AgentDefinition``), jamais de texte libre.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.agents.base import AgentDefinition


@dataclass(frozen=True)
class EntreeAnnuaire:
    """Un agent du runtime CARSO."""

    agent_id: str
    display_name: str
    description: str
    taches: tuple[str, ...]
    appelable: bool


def _catalogue() -> dict[str, AgentDefinition]:
    """Import paresseux : évite le cycle ``catalogue`` ↔ ``annuaire``."""
    from app.agents.catalogue import AGENT_RUNTIME_BY_ID

    return AGENT_RUNTIME_BY_ID


def annuaire_complet(
    definition: AgentDefinition,
    definitions_connues: dict[str, AgentDefinition] | None = None,
) -> tuple[EntreeAnnuaire, ...]:
    """Tous les agents du runtime **sauf** l'appelant.

    ``appelable`` est vrai uniquement si un ``CollaborationGrant`` existe
    pour cet ``agent_id`` : la connaissance ne contourne jamais l'autorisation.
    """
    connus = definitions_connues if definitions_connues is not None else _catalogue()
    grants = {grant.agent_id: grant for grant in definition.collaboration}
    entrees: list[EntreeAnnuaire] = []
    for agent_id, connu in sorted(connus.items()):
        if agent_id == definition.agent_id:
            continue
        grant = grants.get(agent_id)
        entrees.append(
            EntreeAnnuaire(
                agent_id=agent_id,
                display_name=connu.display_name,
                description=(connu.description or "").strip(),
                taches=tuple(sorted(grant.task_types)) if grant else (),
                appelable=grant is not None,
            )
        )
    return tuple(entrees)


def annuaire_pour(
    definition: AgentDefinition,
    definitions_connues: dict[str, AgentDefinition] | None = None,
) -> tuple[EntreeAnnuaire, ...]:
    """Agents **appelables** par ``definition`` (grants explicites)."""
    return tuple(e for e in annuaire_complet(definition, definitions_connues) if e.appelable)


def bloc_annuaire(
    definition: AgentDefinition,
    definitions_connues: dict[str, AgentDefinition] | None = None,
) -> str:
    """Bloc markdown injecté au system prompt de **tous** les agents.

    Deux sous-sections :

    - liste complète (id + description) — connaissance ;
    - agents appelables via ``request_agent_task`` — uniquement les grants.
    """
    entrees = annuaire_complet(definition, definitions_connues)
    if not entrees:
        return ""
    lignes = [
        "## Agents CARSO",
        "",
        "Identifiants exacts. Pour déléguer une tâche, utilise le tool",
        "`request_agent_task` avec l'`agent_id` et un `task_type` supporté —",
        "uniquement si l'agent figure dans la section « appelables » ci-dessous.",
        "L'agent appelé revérifie ses propres permissions ; le résultat t'est",
        "renvoyé. Tu n'hérites jamais de ses droits.",
        "",
    ]
    for entree in entrees:
        lignes.append(f"- `{entree.agent_id}` — {entree.display_name}")
        if entree.description:
            lignes.append(f"  {entree.description}")
    appelables = [e for e in entrees if e.appelable]
    if appelables:
        lignes.extend(["", "## Agents CARSO que tu peux appeler", ""])
        for entree in appelables:
            lignes.append(f"- `{entree.agent_id}` — {entree.display_name}")
            lignes.append(
                "  Tâches supportées : " + ", ".join(f"`{t}`" for t in entree.taches)
            )
    return "\n".join(lignes)


__all__ = [
    "EntreeAnnuaire",
    "annuaire_complet",
    "annuaire_pour",
    "bloc_annuaire",
]
