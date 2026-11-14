"""Mesure la surface d'outils et le prompt de chaque agent (audit INC-170).

Pourquoi ce script : la lenteur d'un tour d'agent vient d'abord de ce qui est
envoyé au modèle à **chaque** itération — schémas d'outils, descriptions et
prompt système — puis du nombre d'itérations. Ces volumes ne se voient ni dans
le code ni dans les tests : il faut les compter.

Il ne touche ni base, ni réseau, ni modèle : il construit les tools avec une
fabrique de session inerte (les handlers ne sont jamais appelés) et compte.

Usage :

    backend/.venv/Scripts/python.exe scripts/audit_outils_agents.py
    backend/.venv/Scripts/python.exe scripts/audit_outils_agents.py --json

Les volumes de schémas sont convertis en tokens avec ``// 4`` (approximation
usuelle pour du français) : ce sont des ordres de grandeur, pas une facture.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path
from typing import Any

RACINE = Path(__file__).resolve().parents[1]
if str(RACINE) not in sys.path:
    sys.path.insert(0, str(RACINE))

from app.agents.catalogue import AGENT_DEFINITIONS  # noqa: E402
from app.agents.harness import build_system_prompt  # noqa: E402
from app.agents.outillage import tools_documents_pour  # noqa: E402
from app.agents.tools import (  # noqa: E402
    construire_tools_assistant_formateur,
    construire_tools_generaliste,
    construire_tools_generateur_offre,
    construire_tools_rh,
    construire_tools_statistique,
)
from app.core.config import Settings  # noqa: E402
from app.tools.base import schema_entree_outil  # noqa: E402

#: Fabrique inerte : aucun handler n'est exécuté, aucune session n'est ouverte.
FABRIQUE_INERTE = lambda: None  # noqa: E731


def _surface(definition: Any) -> tuple[Any, ...]:
    """Tools réellement montés pour un agent.

    Reproduit l'assemblage des classes d'agents : les tools documentaires sont
    ajoutés soit par la fabrique ``construire_tools_*``, soit par l'appelant
    (RH, générateur). Les compter deux fois gonflerait la mesure.
    """
    agent_id = definition.agent_id
    if agent_id == "agent_generaliste_readonly":
        return construire_tools_generaliste(definition, FABRIQUE_INERTE)
    if agent_id == "agent_statistique":
        return construire_tools_statistique(definition, FABRIQUE_INERTE)
    if agent_id == "agent_rh":
        return construire_tools_rh(
            definition,
            FABRIQUE_INERTE,
            extra_tools=tools_documents_pour(definition, FABRIQUE_INERTE),
        )
    if agent_id == "agent_assistant_formateur":
        return construire_tools_assistant_formateur(definition, FABRIQUE_INERTE)
    if agent_id == "agent_generateur_offre":
        return construire_tools_generateur_offre(
            definition,
            FABRIQUE_INERTE,
            extra_tools=tools_documents_pour(definition, FABRIQUE_INERTE),
        )
    return ()


def _schema(outil: Any) -> int:
    """Taille JSON du schéma **réellement envoyé** au modèle (strict inclus)."""
    if outil.input_schema is None:
        return 0
    return len(
        json.dumps(
            schema_entree_outil(outil.input_schema).model_json_schema(),
            ensure_ascii=False,
        )
    )


def mesurer() -> dict[str, Any]:
    """Rapport complet par agent (aucune donnée métier)."""
    iteration_max = int(Settings.model_fields["agent_max_iterations"].default or 0)
    rapport: dict[str, Any] = {}
    for definition in AGENT_DEFINITIONS:
        outils = _surface(definition)
        if not outils:
            continue
        noms = [outil.name for outil in outils]
        descriptions = [len(outil.description) for outil in outils]
        tailles = {outil.name: _schema(outil) for outil in outils}
        prompt = build_system_prompt(definition)
        rapport[definition.agent_id] = {
            "tools": len(outils),
            "tools_uniques": len(set(noms)),
            "declares": len(definition.tools),
            "descriptions_car": sum(descriptions),
            "description_mediane_car": int(statistics.median(descriptions)),
            "description_max_car": max(descriptions),
            "schemas_car": sum(tailles.values()),
            "schemas_tokens_estimes": sum(tailles.values()) // 4,
            "prompt_car": len(prompt),
            "prompt_tokens_estimes": len(prompt) // 4,
            "entree_estimee_tokens": sum(tailles.values()) // 4 + len(prompt) // 4,
            # Pire cas d'un run : la borne d'itérations multiplie l'entrée.
            "iteration_max": iteration_max,
            "pire_cas_tokens": iteration_max
            * (sum(tailles.values()) // 4 + len(prompt) // 4),
            "top_schemas": sorted(tailles.items(), key=lambda item: -item[1])[:5],
            "sans_champ_requis": sum(
                1
                for outil in outils
                if outil.input_schema is not None
                and not outil.input_schema.model_json_schema().get("required")
            ),
        }
    return rapport


def _tableau(rapport: dict[str, Any]) -> str:
    lignes = [
        "| agent | tools | desc. car. | schémas car. | ~tokens schémas "
        "| prompt car. | ~tokens entrée/tour |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for agent_id, valeurs in rapport.items():
        lignes.append(
            f"| `{agent_id}` | {valeurs['tools']} | {valeurs['descriptions_car']} | "
            f"{valeurs['schemas_car']} | {valeurs['schemas_tokens_estimes']} | "
            f"{valeurs['prompt_car']} | {valeurs['entree_estimee_tokens']} |"
        )
    return "\n".join(lignes)


def main() -> int:
    parseur = argparse.ArgumentParser(description=__doc__)
    parseur.add_argument("--json", action="store_true", help="sortie JSON brute")
    arguments = parseur.parse_args()

    rapport = mesurer()
    if arguments.json:
        print(json.dumps(rapport, ensure_ascii=False, indent=1))
        return 0

    print(_tableau(rapport))
    print()
    for agent_id, valeurs in rapport.items():
        top = ", ".join(f"{nom} ({taille})" for nom, taille in valeurs["top_schemas"][:3])
        print(
            f"- {agent_id} : sans champ requis {valeurs['sans_champ_requis']} ; "
            f"top schémas {top}"
        )
    doublons = {
        agent_id: valeurs["tools"] - valeurs["tools_uniques"]
        for agent_id, valeurs in rapport.items()
        if valeurs["tools"] != valeurs["tools_uniques"]
    }
    if doublons:
        print(f"\nATTENTION — outils en double : {doublons}")
    reference = rapport.get("agent_generateur_offre") or next(iter(rapport.values()), None)
    if reference is not None:
        print(
            f"\nBorne d'itérations : {reference['iteration_max']} — pire cas d'un run "
            f"(générateur) : ~{reference['pire_cas_tokens']} tokens d'entrée."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
