"""Où passe le temps d'un tour d'agent : fournisseur, charge d'entrée, ou nous ?

Mesure trois choses, dans l'ordre du soupçon :

1. **l'appel brut** au modèle effectivement configuré (prompt trivial, sans
   outil) — c'est le plancher du fournisseur ;
2. **le même appel avec le catalogue d'outils de l'agent** attaché — coût de
   traitement de l'entrée par le fournisseur ;
3. **le prompt système assemblé de l'agent** — poids fixe ajouté à chaque tour.

Usage ::

    ./.venv/Scripts/python.exe -m scripts.diagnostic_latence_modele
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from app.core.config import get_settings


def _reglages_effectifs() -> Any:
    """Configuration réellement utilisée par les agents (surcharge admin incluse)."""
    from app.agents.manager import get_runtime_manager

    manager = get_runtime_manager()
    return manager.effective_settings()


async def _chrono(modele: Any, messages: Any, *, tours: int = 2) -> list[tuple[float, float]]:
    """(premier token, total) par appel, en secondes."""
    mesures: list[tuple[float, float]] = []
    for _ in range(tours):
        debut = time.monotonic()
        premier: float | None = None
        async for morceau in modele.astream(messages):
            if premier is None:
                premier = time.monotonic() - debut
        total = time.monotonic() - debut
        mesures.append((premier if premier is not None else total, total))
    return mesures


def _formater(mesures: list[tuple[float, float]]) -> str:
    return " | ".join(f"1er token {p:.1f}s, total {t:.1f}s" for p, t in mesures)


async def main() -> int:
    from app.agents.providers import build_chat_model, is_agent_configured

    reglages = _reglages_effectifs()
    print("--- configuration effective ---")
    print(f"provider   : {reglages.agent_provider}")
    print(f"modèle     : {reglages.agent_model}")
    print(f"base_url   : {reglages.agent_base_url}")
    print(f"température: {reglages.agent_temperature}")
    print(f"clé présente: {bool(reglages.agent_api_key)}")
    if not is_agent_configured(reglages):
        print("configuration incomplète : aucun appel possible")
        return 2

    modele = build_chat_model(reglages)

    # 1. Appel brut, prompt trivial.
    simple = [("user", "Réponds uniquement par le mot : CARSO")]
    brut = await _chrono(modele, simple)
    print("\n1. appel brut (sans outil) :", _formater(brut))

    # 2. Le même, avec le catalogue d'outils de l'agent personnel.
    from app.agents.catalogue import AGENT_DEFINITIONS_BY_ID

    definition = AGENT_DEFINITIONS_BY_ID["agent_personnel"]
    outils: list[Any] = []
    try:
        fabrique = getattr(definition, "deep_tools", None)
        outils = list(fabrique()) if callable(fabrique) else []
    except Exception as exc:  # noqa: BLE001 - diagnostic : on continue sans outils
        print(f"   (catalogue d'outils indisponible : {type(exc).__name__} — {exc})")

    if outils:
        modele_outils = modele.bind_tools(outils)
        avec_outils = await _chrono(modele_outils, simple)
        print(f"2. appel avec {len(outils)} outils :", _formater(avec_outils))

    # 3. Prompt système de l'agent.
    from app.agents.prompt import build_system_prompt

    prompt = build_system_prompt(definition)
    print(f"\n3. prompt système assemblé : {len(prompt):,} caractères".replace(",", " "))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
