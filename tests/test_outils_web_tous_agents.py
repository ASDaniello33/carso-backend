"""Surface de recherche externe : **la même pour tous les agents** (12/10).

Demande utilisateur : « assurez-vous que tous les agents ont l'outil de recherche
web complet et les outils fetch urls ». L'invariant est donc verrouillé ici, une
fois pour tout le catalogue :

1. chaque agent du **runtime** déclare les six tools de lecture externe —
   ``langsearch_web_search`` (recherche fédérée), ``fetch_url`` / ``read_urls``
   (lecture d'une adresse, seule ou par lot), ``rechercher_image``,
   ``rechercher_video``, ``rechercher_document`` (recherche média) ;
2. chaque agent porte la capacité ``web_search`` — sans elle, les tools
   lèveraient ``PermissionDeniedError`` à l'appel (garde de ``app.tools.web`` /
   ``app.tools.recherche_media``) : déclarer sans capacité serait un contrat mort ;
3. chaque **fabrique d'agent** les matérialise réellement (``deep_tools``) — un
   contrat peut déclarer un tool que le constructeur n'attache pas, et le modèle
   ne le verrait jamais.

Ce que ce test ne fait pas : appeler le réseau. Les tools restent en lecture
pure ; leur comportement (fédération, anti-SSRF, bornes) est testé dans
``test_tools_web.py`` et ``test_tools_recherche_media.py``.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from sqlalchemy.orm import Session, sessionmaker

from app.agents.base import AgentDefinition
from app.agents.catalogue import AGENT_RUNTIME

#: Les six tools de lecture externe attendus sur **chaque** agent.
TOOLS_WEB: frozenset[str] = frozenset(
    {
        "langsearch_web_search",
        "fetch_url",
        "read_urls",
        "rechercher_image",
        "rechercher_video",
        "rechercher_document",
    }
)

#: Capacité exigée par chacun de ces tools (``policy.require``).
CAPACITE_WEB = "web_search"

#: Fabriques publiques, **une par agent du runtime**, de signature identique
#: (``session`` + ``session_factory``) : c'est ce qui rend l'invariant uniforme.
FABRIQUES: dict[str, Callable[..., Any]] = {}


def _fabriques() -> dict[str, Callable[..., Any]]:
    """Résolues à la demande : importer les six agents est inutile au reste."""
    if not FABRIQUES:
        from app.agents.agent_personnel import build_agent_personnel
        from app.agents.agent_rh import build_rh
        from app.agents.agent_statistique import build_statistique
        from app.agents.assistant_formateur import build_assistant
        from app.agents.generaliste_readonly import build_generaliste
        from app.agents.generateur_offre import build_generateur

        entrees: list[tuple[str, Callable[..., Any]]] = [
            ("agent_generateur_offre", build_generateur),
            ("agent_rh", build_rh),
            ("agent_assistant_formateur", build_assistant),
            ("agent_generaliste_readonly", build_generaliste),
            ("agent_statistique", build_statistique),
            ("agent_personnel", build_agent_personnel),
        ]
        FABRIQUES.update(entrees)
    return FABRIQUES


@pytest.mark.parametrize("definition", AGENT_RUNTIME, ids=lambda d: d.agent_id)
def test_chaque_agent_declare_la_surface_web_complete(definition: AgentDefinition) -> None:
    """Six tools déclarés, capacité ``web_search`` présente : ni plus, ni moins."""
    outils = set(definition.tools)
    manquants = TOOLS_WEB - outils
    assert not manquants, f"{definition.agent_id} n'a pas {sorted(manquants)}"
    assert definition.policy.allows(CAPACITE_WEB), (
        f"{definition.agent_id} déclare la recherche web sans la capacité {CAPACITE_WEB!r}"
    )
    assert CAPACITE_WEB in definition.capabilities, (
        f"{definition.agent_id} n'annonce pas la capacité {CAPACITE_WEB!r} au catalogue"
    )


@pytest.mark.parametrize("definition", AGENT_RUNTIME, ids=lambda d: d.agent_id)
def test_chaque_agent_materialise_la_surface_web_complete(
    definition: AgentDefinition, api_session_factory: sessionmaker[Session]
) -> None:
    """Le constructeur attache vraiment les six tools — RH compris (nouveau 12/10).

    Un contrat n'est pas une promesse tenue : c'est ici que « tous les agents »
    devient vérifiable, agent par agent, plutôt qu'au seul niveau du catalogue.
    """
    fabrique = _fabriques()[definition.agent_id]
    session = api_session_factory()
    try:
        noms = {
            tool.name
            for tool in fabrique(session, session_factory=api_session_factory).deep_tools()
        }
    finally:
        session.close()
    manquants = TOOLS_WEB - noms
    assert not manquants, f"{definition.agent_id} ne construit pas {sorted(manquants)}"
