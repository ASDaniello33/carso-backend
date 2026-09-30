"""Exécution contrôlée de scripts Python (Lot T1, clinrules 08 §Shell).

Ce n'est **pas** un shell libre. L'agent fournit le *source* ; le tool
vérifie les imports (AST, deny by default), écrit dans une sandbox, exécute
via ``sys.executable -I``, borne timeout/sortie, journalise.
"""

from __future__ import annotations

import ast
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from app.core.errors import PermissionDeniedError, ValidationError
from app.tools.permissions import AgentIdentity, PermissionPolicy

__all__ = ["RunPythonScriptInput", "build_run_python_script", "racine_scripts"]

_IMPORTS_AUTORISES = frozenset(
    {
        "collections",
        "csv",
        "datetime",
        "decimal",
        "io",
        "json",
        "math",
        "pathlib",
        "re",
        "statistics",
        "string",
        "textwrap",
        "typing",
        "unicodedata",
        "docx",
        "openpyxl",
        "pypdf",
        "fpdf",
        "fpdf2",
    }
)
_IMPORTS_INTERDITS = frozenset(
    {
        "os",
        "sys",
        "subprocess",
        "shutil",
        "socket",
        "http",
        "urllib",
        "ctypes",
        "multiprocessing",
        "importlib",
        "builtins",
    }
)


class RunPythonScriptInput(BaseModel):
    """Source Python à exécuter dans la sandbox."""

    source: str = Field(min_length=1, description="Code Python (UTF-8).")
    nom: str | None = Field(default=None, max_length=80)


def _modules_importes(source: str) -> set[str]:
    """Noms de modules de premier niveau importés (AST, pas d'exécution)."""
    try:
        arbre = ast.parse(source)
    except SyntaxError as exc:
        raise ValidationError(
            f"Script Python syntaxiquement invalide : {exc.msg}",
            details={"lineno": exc.lineno},
        ) from exc
    modules: set[str] = set()
    for noeud in ast.walk(arbre):
        if isinstance(noeud, ast.Import):
            for alias in noeud.names:
                modules.add(alias.name.split(".", 1)[0])
        elif isinstance(noeud, ast.ImportFrom):
            if noeud.level and noeud.level > 0:
                raise PermissionDeniedError(
                    "Import relatif interdit dans un script d'agent"
                )
            if noeud.module:
                modules.add(noeud.module.split(".", 1)[0])
    return modules


def verifier_imports(source: str) -> None:
    """Refuse tout import hors allowlist (deny by default)."""
    for module in sorted(_modules_importes(source)):
        if module in _IMPORTS_INTERDITS or module not in _IMPORTS_AUTORISES:
            raise PermissionDeniedError(
                f"Import {module!r} interdit dans un script d'agent",
                details={"module": module, "autorises": sorted(_IMPORTS_AUTORISES)},
            )


def racine_scripts(settings: Any) -> Path:
    """Sandbox : ``scripts_root`` ou ``<storage_root>/_scripts``."""
    brut = getattr(settings, "scripts_root", "") or ""
    if str(brut).strip():
        return Path(str(brut)).expanduser().resolve()
    storage = Path(getattr(settings, "storage_root", "/storage/carso"))
    return (storage / "_scripts").resolve()


def build_run_python_script(
    *,
    identite: AgentIdentity,
    policy: PermissionPolicy,
    settings: Any,
    audit: Any | None = None,
) -> Any:
    """Construit le tool ``run_python_script`` pour UN agent."""
    racine = racine_scripts(settings)
    racine.mkdir(parents=True, exist_ok=True)
    timeout_s = int(settings.scripts_timeout_seconds)
    max_octets = int(settings.scripts_max_output_bytes)
    max_source = int(settings.scripts_max_source_chars)

    def _tracer(action: str, details: dict[str, Any]) -> None:
        if audit is not None:
            audit(action, {"agent_id": identite.agent_id, **details})

    def _handler(payload: dict[str, Any]) -> dict[str, Any]:
        policy.require(identite.agent_id, "script_python")
        source = payload["source"]
        if len(source) > max_source:
            raise ValidationError(
                f"Script trop long ({len(source)} > {max_source} caractères)"
            )
        verifier_imports(source)
        nom = (payload.get("nom") or "script").strip() or "script"
        _tracer("script.exec", {"nom": nom, "taille": len(source)})

        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            suffix=".py",
            prefix="carso_",
            dir=racine,
            delete=False,
        ) as fichier:
            fichier.write(source)
            chemin = Path(fichier.name)

        try:
            completed = subprocess.run(  # noqa: S603
                [sys.executable, "-I", str(chemin)],
                cwd=racine,
                timeout=timeout_s,
                capture_output=True,
                check=False,
            )
        except subprocess.TimeoutExpired:
            _tracer("script.timeout", {"nom": nom})
            return {
                "code_retour": 124,
                "stdout": "",
                "stderr": f"Script interrompu après {timeout_s}s",
                "timeout": True,
            }
        finally:
            chemin.unlink(missing_ok=True)

        return {
            "code_retour": completed.returncode,
            "stdout": completed.stdout[:max_octets].decode("utf-8", errors="replace"),
            "stderr": completed.stderr[:max_octets].decode("utf-8", errors="replace"),
            "timeout": False,
        }

    from app.tools.base import TypedTool

    return TypedTool(
        name="run_python_script",
        description=(
            "Exécute un script Python court dans une sandbox (imports "
            "allowlistés : json/csv/docx/openpyxl/pypdf/fpdf). Pas de réseau, "
            "pas d'os/sys/subprocess."
        ),
        handler=_handler,
        input_schema=RunPythonScriptInput,
        tags=("scripts", "documents"),
    )
