"""Lot 1 — tool ``carso_shell`` (D1 : shell contrôlé, deny par défaut).

Aucune vraie commande externe n'est nécessaire pour la majorité des cas :
seul le chemin PowerShell est exécuté réellement (commande ``python``), afin
de vérifier le câblage subprocess. Tout le reste teste les refus.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.core.config import Settings
from app.core.errors import ValidationError
from app.tools.permissions import AgentIdentity, PermissionPolicy
from app.tools.shell import build_carso_shell

IDENTITE = AgentIdentity(agent_id="agent_test")
POLICY_SHELL = PermissionPolicy(allowed_capabilities=frozenset({"shell"}))
POLICY_VIDE = PermissionPolicy(allowed_capabilities=frozenset())


def _settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "shell_allowed_commands": "python,get-childitem",
        "shell_allowed_roots": "",
        "shell_timeout_seconds": 10,
        "shell_max_output_bytes": 10_000,
    }
    base.update(overrides)
    return Settings(_env_file=None, **base)


def test_construction_refusee_sans_racine() -> None:
    """Racines vides => shell désactivé : refus explicite à la construction."""
    with pytest.raises(ValidationError, match="désactivé"):
        build_carso_shell(
            identite=IDENTITE, policy=POLICY_SHELL, settings=_settings()
        )


def test_construction_ok_avec_racine(tmp_path: Path) -> None:
    tool = build_carso_shell(
        identite=IDENTITE,
        policy=POLICY_SHELL,
        settings=_settings(shell_allowed_roots=str(tmp_path)),
    )
    assert tool.name == "carso_shell"
    assert tool.input_schema is not None


def test_permission_absente_refusee(tmp_path: Path) -> None:
    tool = build_carso_shell(
        identite=IDENTITE,
        policy=POLICY_VIDE,
        settings=_settings(shell_allowed_roots=str(tmp_path)),
    )
    sortie = tool.invoke({"commande": "python --version"})
    assert sortie["ok"] is False
    assert sortie["error"]["code"] == "permission_denied"


def test_commande_hors_allowlist_refusee(tmp_path: Path) -> None:
    journal: list[tuple[str, dict[str, Any]]] = []
    tool = build_carso_shell(
        identite=IDENTITE,
        policy=POLICY_SHELL,
        settings=_settings(shell_allowed_roots=str(tmp_path)),
        audit=lambda action, details: journal.append((action, details)),
    )
    sortie = tool.invoke({"commande": "Remove-Item forbidden"})
    assert sortie["ok"] is False
    assert "allowlist" in sortie["error"]["message"]
    assert journal[0][0] == "shell.refuse"


@pytest.mark.parametrize(
    "commande",
    [
        "Get-ChildItem; Remove-Item x",
        "Get-ChildItem | Out-File secret.txt",
        "Get-ChildItem & whoami",
        "python -c \"print('..')\" > dump.txt",
    ],
)
def test_separateurs_et_redirecteurs_interdits(
    tmp_path: Path, commande: str
) -> None:
    tool = build_carso_shell(
        identite=IDENTITE,
        policy=POLICY_SHELL,
        settings=_settings(shell_allowed_roots=str(tmp_path)),
    )
    sortie = tool.invoke({"commande": commande})
    assert sortie["ok"] is False
    assert sortie["error"]["code"] == "permission_denied"


def test_repertoire_hors_racines_refuse(tmp_path: Path) -> None:
    tool = build_carso_shell(
        identite=IDENTITE,
        policy=POLICY_SHELL,
        settings=_settings(shell_allowed_roots=str(tmp_path)),
    )
    sortie = tool.invoke({"commande": "Get-ChildItem", "repertoire": "C:/Windows"})
    assert sortie["ok"] is False
    assert "racines" in sortie["error"]["message"]


def test_path_traversal_refuse(tmp_path: Path) -> None:
    tool = build_carso_shell(
        identite=IDENTITE,
        policy=POLICY_SHELL,
        settings=_settings(shell_allowed_roots=str(tmp_path)),
    )
    sortie = tool.invoke(
        {"commande": "Get-ChildItem", "repertoire": str(tmp_path / ".." / "..")}
    )
    assert sortie["ok"] is False
    assert "racines" in sortie["error"]["message"]


def test_execution_reelle_python(tmp_path: Path) -> None:
    """Chemin subprocess complet : une commande allowlistée s'exécute réellement."""
    tool = build_carso_shell(
        identite=IDENTITE,
        policy=POLICY_SHELL,
        settings=_settings(shell_allowed_roots=str(tmp_path)),
    )
    resultat = tool.invoke({"commande": "python -c \"print('carso_ok')\""})
    assert resultat["code_retour"] == 0
    assert "carso_ok" in resultat["stdout"]
    assert resultat["timeout"] is False


def test_tracabilite_execution(tmp_path: Path) -> None:
    journal: list[tuple[str, dict[str, Any]]] = []
    tool = build_carso_shell(
        identite=IDENTITE,
        policy=POLICY_SHELL,
        settings=_settings(shell_allowed_roots=str(tmp_path)),
        audit=lambda action, details: journal.append((action, details)),
    )
    tool.invoke({"commande": "python -c \"print(1)\""})
    assert journal[0][0] == "shell.exec"
    assert journal[0][1]["agent_id"] == "agent_test"


def test_output_cap(tmp_path: Path) -> None:
    """Le plafond de sortie est appliqué (borne en octets, avant décodage)."""
    tool = build_carso_shell(
        identite=IDENTITE,
        policy=POLICY_SHELL,
        settings=_settings(shell_allowed_roots=str(tmp_path), shell_max_output_bytes=64),
    )
    resultat = tool.invoke({"commande": "python -c \"print('x' * 500)\""})
    assert len(resultat["stdout"].encode("utf-8")) <= 72  # marge replace multioctets

