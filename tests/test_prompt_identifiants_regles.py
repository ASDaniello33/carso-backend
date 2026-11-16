"""Identifiants de règle du prompt générateur — la compaction ne peut plus perdre une règle.

Le prompt est condensé régulièrement (83 k → 84 k caractères, leçon fusionnée,
blocs non dupliqués). Jusqu'ici, la non-régression se vérifiait par quelques
chaînes : une règle pouvait disparaître dans une reformulation sans qu'aucun
test ne le voie.

Chaque règle métier validée porte désormais un identifiant `[R-…]` : ce fichier
vérifie que **tous** sont présents, une seule fois, dans le prompt réellement
assemblé, et que la phrase qui les porte est toujours là. Une compaction qui
supprime ou déplace une règle casse ici, avec le nom de la règle.
"""

from __future__ import annotations

from app.agents.generateur_offre import DEFINITION
from app.agents.harness import build_system_prompt

#: Identifiant -> fragment de la phrase qui porte la règle.
REGLES: dict[str, str] = {
    "R-APPEL-01": "sa nature est explicite et confirmée par l'utilisateur, jamais déduite",
    "R-LOT-01": "Un lot n'est pas un lieu",
    "R-LOT-02": "**Lieu d'exécution**",
    "R-OFFRE-01": "réponse à un couple Appel + Lot",
    "R-OFFRE-02": "Un lot porte au plus une offre par type",
    "R-FID-01": "ta seule source est le document de l'appel et ses annexes",
    "R-FID-02": "**Jamais inventé**",
    "R-FIAB-01": "Une offre exacte et vérifiable vaut mieux",
    "R-FIAB-07": "qu'après accord de l'utilisateur",
    "R-FIAB-09": "Pas de blanc silencieux",
    "R-STATUT-01": "sans écrasement et sans approbation implicite",
}


def test_toutes_les_regles_ont_un_identifiant_unique() -> None:
    prompt = build_system_prompt(DEFINITION)

    for identifiant, phrase in REGLES.items():
        assert prompt.count(identifiant) == 1, (
            f"{identifiant} doit apparaître exactement une fois "
            f"(vu {prompt.count(identifiant)} fois)"
        )
        assert phrase in prompt, f"la règle {identifiant} a perdu sa formulation"


def test_aucun_identifiant_orphelin() -> None:
    """Tout `[R-…]` du prompt est déclaré dans ce fichier."""
    import re

    prompt = build_system_prompt(DEFINITION)
    trouves = set(re.findall(r"\[R-[A-Z]+-\d{2}\]", prompt))
    declares = {f"[{identifiant}]" for identifiant in REGLES}
    assert trouves == declares, f"écart : {sorted(trouves ^ declares)}"
