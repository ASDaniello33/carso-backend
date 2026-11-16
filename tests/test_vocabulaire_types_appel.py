"""Tests — le vocabulaire d'affichage des types d'appel n'a qu'une source.

Les libellés vivent dans ``app.domain.enums.LIBELLES_TYPE_APPEL``. Le fichier
``frontend/src/lib/vocabulaires/types-appel.ts`` est généré à partir d'elle :

- une modification d'un seul côté **échoue** ici, avec la commande de
  régénération ;
- la source elle-même est vérifiée : chaque ``TypeAppel`` a un libellé non vide,
  et deux types ne partagent pas le même libellé.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from app.domain.enums import LIBELLES_TYPE_APPEL, TypeAppel

RACINE_BACKEND = Path(__file__).resolve().parents[1]
SCRIPT = RACINE_BACKEND / "scripts" / "exporter_vocabulaire_types_appel.py"
CIBLE = (
    RACINE_BACKEND.parent
    / "frontend"
    / "src"
    / "lib"
    / "vocabulaires"
    / "types-appel.ts"
)


def test_source_couvre_tous_les_types_avec_des_libelles_distincts() -> None:
    """Chaque type canonique a un libellé, et deux types ne se confondent pas."""
    assert set(LIBELLES_TYPE_APPEL) == set(TypeAppel)
    for libelle in LIBELLES_TYPE_APPEL.values():
        assert libelle.strip(), "un type d'appel sans libellé s'afficherait comme un code"
    assert len(set(LIBELLES_TYPE_APPEL.values())) == len(TypeAppel)


def test_fichier_frontend_est_a_jour() -> None:
    """Le fichier généré correspond à la source, sinon le test le dit."""
    assert CIBLE.exists(), (
        f"Fichier généré absent : {CIBLE}. Régénérez-le avec "
        f"`{sys.executable} scripts/exporter_vocabulaire_types_appel.py`."
    )
    resultat = subprocess.run(
        [sys.executable, str(SCRIPT), "--verifier"],
        cwd=str(RACINE_BACKEND),
        capture_output=True,
        text=True,
        check=False,
    )
    assert resultat.returncode == 0, resultat.stdout + resultat.stderr
