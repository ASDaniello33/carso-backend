"""Analyse de données et modèle de prédiction par script (agent statistique).

Ce que ces tests verrouillent :

1. ``pandas``, ``numpy`` et ``scikit-learn`` s'importent et s'exécutent sous la
   garde d'audit réelle (leurs bibliothèques natives viennent de l'installation
   Python, pas de l'espace de travail) ;
2. un script d'étude produit bien un résumé statistique, un score mesuré sur
   le jeu de test et une prédiction, les trois lus dans ``stdout`` ;
3. les refus restent en place : ``ctypes``, ``os``, ``subprocess`` et les
   modules réseau ne s'importent pas dans le source.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

import app.tools.scripts as scripts
from app.agents.contexte import (
    UserContext,
    poser_contexte_utilisateur,
    retablir_contexte_utilisateur,
)
from app.core.errors import PermissionDeniedError
from app.tools.permissions import AgentIdentity, PermissionPolicy
from app.tools.scripts import build_run_python_script

IDENTITE = AgentIdentity(agent_id="agent_statistique")
POLICY = PermissionPolicy(allowed_capabilities=frozenset({"script_python"}))

SOURCE_ETUDE = """
import numpy as np
import pandas as pd
from sklearn.linear_model import LinearRegression
from sklearn.metrics import r2_score
from sklearn.model_selection import train_test_split

donnees = pd.DataFrame(
    {
        "mois": [1, 2, 3, 4, 5, 6, 7, 8],
        "beneficiaires": [10, 14, 15, 19, 24, 25, 29, 33],
    }
)
donnees = donnees.drop_duplicates().dropna()
print("lignes", len(donnees))
print("moyenne", float(donnees["beneficiaires"].mean()))

x = donnees[["mois"]].to_numpy()
y = donnees["beneficiaires"].to_numpy()
x_train, x_test, y_train, y_test = train_test_split(
    x, y, test_size=0.25, random_state=0
)
modele = LinearRegression().fit(x_train, y_train)
score = r2_score(y_test, modele.predict(x_test))
print("score", round(float(score) * 100, 1))
print("prediction", round(float(modele.predict(np.array([[9]]))[0]), 2))
"""


def _settings(tmp_path: Path) -> SimpleNamespace:
    return SimpleNamespace(
        storage_root=str(tmp_path / "storage"),
        workdirs_root=str(tmp_path / "workdirs"),
        scripts_root="",
        scripts_timeout_seconds=120,
        scripts_max_output_bytes=80_000,
        scripts_max_source_chars=60_000,
        scripts_confiance="eleve",
    )


def _executer(tmp_path: Path, source: str) -> dict:
    outil = build_run_python_script(
        identite=IDENTITE,
        policy=POLICY,
        settings=_settings(tmp_path),
    )
    jeton = poser_contexte_utilisateur(UserContext(utilisateur_id=str(uuid4())))
    try:
        return outil.handler({"source": source, "nom": "etude"})
    finally:
        retablir_contexte_utilisateur(jeton)


def test_etude_pandas_sklearn_sous_la_garde(tmp_path: Path) -> None:
    pytest.importorskip("pandas")
    pytest.importorskip("sklearn")

    sortie = _executer(tmp_path, SOURCE_ETUDE)

    assert sortie["code_retour"] == 0, sortie["stderr"]
    lignes = sortie["stdout"].splitlines()
    assert "lignes 8" in lignes
    assert any(ligne.startswith("moyenne 21.125") for ligne in lignes)
    assert any(ligne.startswith("score ") for ligne in lignes)
    assert any(ligne.startswith("prediction ") for ligne in lignes)


def test_les_imports_dangereux_restent_refuses() -> None:
    for source in (
        "import ctypes",
        "import _ctypes",
        "import os",
        "import subprocess",
        "import socket",
        "import requests",
    ):
        with pytest.raises(PermissionDeniedError):
            scripts.verifier_imports(source)


def test_les_paquets_analytiques_sont_admis_par_la_verification() -> None:
    scripts.verifier_imports(
        "import numpy as np\nimport pandas as pd\nfrom sklearn.linear_model import LinearRegression"
    )
