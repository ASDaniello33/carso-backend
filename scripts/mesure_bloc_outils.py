"""Mesure la taille du bloc d'outils remis au modèle, pour un agent.

Pourquoi ce script : la réduction du nombre d'outils n'a de valeur que si le
contexte envoyé au modèle diminue réellement. On mesure donc, pour chaque
outil autorisé par le contrat de l'agent :

- son **nom** ;
- sa **description** (le texte que le modèle lit pour choisir) ;
- son **schéma d'entrée** sérialisé (JSON Schema).

La somme est le « bloc d'outils » (hors instructions du prompt). Aucun appel de
modèle, aucune base : l'agent est construit sur une base SQLite en mémoire et
les outils sont assemblés par les mêmes fabriques que le harnais.

Usage :
    python scripts/mesure_bloc_outils.py [agent_id]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RACINE))

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.agents.catalogue import AGENT_DEFINITIONS_BY_ID  # noqa: E402
from app.agents.harness import allowed_tools_only  # noqa: E402


def _outils(agent_id: str):
    """Instancie l'agent et rend les outils retenus par son contrat.

    Aucune table n'est créée : la construction des outils ne lit pas la base
    (les handlers ouvrent leur session à l'invocation).
    """
    from app.agents.generateur_offre import build_generateur

    engine = create_engine("sqlite://")
    session = sessionmaker(bind=engine)()
    try:
        agent = build_generateur(session, session_factory=lambda: session)
        definition = AGENT_DEFINITIONS_BY_ID[agent_id]
        return definition, allowed_tools_only(definition, agent.deep_tools())
    finally:
        session.close()
        engine.dispose()


def main() -> int:
    agent_id = sys.argv[1] if len(sys.argv) > 1 else "agent_generateur_offre"
    definition, outils = _outils(agent_id)

    lignes: list[tuple[str, int, int, int]] = []
    for outil in outils:
        schema = outil.input_schema.model_json_schema() if outil.input_schema else {}
        taille_schema = len(json.dumps(schema, ensure_ascii=False))
        lignes.append(
            (outil.name, len(outil.name), len(outil.description or ""), taille_schema)
        )
    lignes.sort(key=lambda ligne: -(ligne[2] + ligne[3]))

    total = sum(nom + description + schema for _, nom, description, schema in lignes)
    print(f"agent : {agent_id}")
    print(f"outils autorisés : {len(outils)} (contrat : {len(definition.allowed_tools())})")
    print(f"bloc d'outils : {total} caractères")
    print(f"  noms : {sum(ligne[1] for ligne in lignes)}")
    print(f"  descriptions : {sum(ligne[2] for ligne in lignes)}")
    print(f"  schémas : {sum(ligne[3] for ligne in lignes)}")
    print()
    print(f"{'outil':44} {'nom':>5} {'desc':>7} {'schéma':>7}")
    for nom, taille_nom, description, schema in lignes:
        print(f"{nom:44} {taille_nom:>5} {description:>7} {schema:>7}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
