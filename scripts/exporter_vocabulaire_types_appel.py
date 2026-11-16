"""Génère les libellés d'interface des types d'appel pour le frontend.

**Source unique** : ``app.domain.enums.LIBELLES_TYPE_APPEL``. Le fichier
``frontend/src/lib/vocabulaires/types-appel.ts`` est un **artefact** : il ne se
modifie jamais à la main.

Usage (depuis ``backend/``) :

    ./.venv/Scripts/python.exe scripts/exporter_vocabulaire_types_appel.py
    ./.venv/Scripts/python.exe scripts/exporter_vocabulaire_types_appel.py --verifier

``--verifier`` ne réécrit rien et sort en erreur si le fichier a divergé : c'est
ce que lance ``tests/test_vocabulaire_types_appel.py``, donc une modification
d'un seul côté ne peut pas passer silencieusement.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

RACINE_BACKEND = Path(__file__).resolve().parents[1]
if str(RACINE_BACKEND) not in sys.path:
    sys.path.insert(0, str(RACINE_BACKEND))

from app.domain.enums import LIBELLES_TYPE_APPEL  # noqa: E402

#: Fichier consommé par ``frontend/src/lib/metier.ts``.
CIBLE = (
    RACINE_BACKEND.parent
    / "frontend"
    / "src"
    / "lib"
    / "vocabulaires"
    / "types-appel.ts"
)

COMMANDE = (
    "cd backend && ./.venv/Scripts/python.exe "
    "scripts/exporter_vocabulaire_types_appel.py"
)


def rendu() -> str:
    """Contenu TS attendu pour la table Python, à l'octet près."""
    lignes = [
        "/**",
        " * Libellés des types d'appel : FICHIER GÉNÉRÉ, ne pas modifier à la main.",
        " *",
        " * Source unique : `backend/app/domain/enums.py` (`LIBELLES_TYPE_APPEL`).",
        " * Régénérer :",
        f" *   {COMMANDE}",
        " *",
        " * `backend/tests/test_vocabulaire_types_appel.py` échoue dès que ce fichier",
        " * diverge de la source : la dérive est signalée, jamais silencieuse.",
        " */",
        "",
        'import type { TypeAppel } from "@/types";',
        "",
        "export const LIBELLES_TYPE_APPEL: Readonly<Record<TypeAppel, string>> = {",
    ]
    for type_appel, libelle in LIBELLES_TYPE_APPEL.items():
        lignes.append(f"  {type_appel.value}: {json.dumps(libelle, ensure_ascii=False)},")
    lignes.append("};")
    lignes.append("")
    return "\n".join(lignes)


def verifier() -> int:
    """0 si le fichier est à jour, 1 sinon (aucune écriture)."""
    actuel = CIBLE.read_text(encoding="utf-8") if CIBLE.exists() else ""
    if actuel == rendu():
        print(f"À jour : {CIBLE}")
        return 0
    print(
        f"Divergence : {CIBLE} n'est plus la source unique.\n"
        f"Régénérez-le avec : {COMMANDE}"
    )
    return 1


def main() -> int:
    if "--verifier" in sys.argv[1:]:
        return verifier()
    CIBLE.parent.mkdir(parents=True, exist_ok=True)
    # Fin de ligne fixée : le fichier généré doit être identique sur toute machine.
    CIBLE.write_text(rendu(), encoding="utf-8", newline="\n")
    print(f"Écrit : {CIBLE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
