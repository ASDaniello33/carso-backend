"""Définition effective d'un agent = son contrat **plus** sa surcharge administrée.

Le contrat (``AgentDefinition``) reste la source de vérité : il est écrit dans le
code, revu et testé. Une surcharge administrée depuis la page Paramètres ne peut
que :

- **retirer** un outil que le contrat déclare (jamais en ajouter un qui n'y est
  pas : le harnais refuse tout outil hors allow-list, et une surcharge ne doit pas
  contourner cette garde) ;
- **ajouter** des consignes au system prompt ;
- **ajouter** des skills à charger.

``definition_effective`` est le seul point de dérivation : catalogue, harnais et
agent manager l'appellent, il n'existe donc qu'une seule réponse à la question
« quels sont les outils de cet agent, aujourd'hui ».
"""

from __future__ import annotations

from dataclasses import replace

from app.agents.base import AgentDefinition
from app.domain.agent_runtime import SurchargeAgent


def definition_effective(
    definition: AgentDefinition, surcharge: SurchargeAgent | None
) -> AgentDefinition:
    """Applique une surcharge à un contrat d'agent.

    Args:
        definition: contrat déclaré dans le code.
        surcharge: ligne administrée, ou ``None`` si l'agent suit son contrat.

    Returns:
        Un contrat de même identité, avec outils réduits, prompt complété et
        skills ajoutés. La politique de permissions n'est jamais touchée : une
        surcharge règle la conduite, pas les droits.
    """
    if surcharge is None or not surcharge.actif:
        return definition

    outils_retires = {nom for nom in (surcharge.outils_desactives or [])}
    outils = tuple(
        nom for nom in definition.tools if nom not in outils_retires
    )
    skills = tuple(
        dict.fromkeys([*definition.skills, *(surcharge.skills_ajoutes or [])])
    )
    prompt = definition.prompt
    supplement = (surcharge.instructions_supplementaires or "").strip()
    if supplement:
        prompt = f"{prompt.rstrip()}\n\n{supplement}\n"

    return replace(definition, tools=outils, skills=skills, prompt=prompt)


def outils_hors_contrat(definition: AgentDefinition, noms: list[str]) -> list[str]:
    """Noms d'outils demandés qui ne figurent pas au contrat de l'agent.

    Sert à refuser explicitement une désactivation fantaisiste : un outil que
    l'agent n'a pas n'a pas besoin d'être « désactivé », et l'accepter en silence
    masquerait une incompréhension du contrat.
    """
    autorises = definition.allowed_tools()
    return sorted(nom for nom in noms if nom not in autorises)


__all__ = ["definition_effective", "outils_hors_contrat"]
