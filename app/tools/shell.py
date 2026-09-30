"""Shell contrôlé pour agents (Lot 1, décision D1 / ADR 0003).

Un agent n'obtient **jamais** un shell brut : ``carso_shell`` applique un
refus par défaut (deny by default) sur trois axes, dans cet ordre :

1. **racines autorisées** — ``shell_allowed_roots`` vide désactive le shell
   (activer le shell est une décision explicite de déploiement) ;
2. **allowlist de commandes** — seul le premier mot de la commande est comparé
   (insensible à la casse) ; tout le reste est refusé, sans heuristique ;
3. **budgets** — timeout et plafond de sortie sont bornés par ``Settings``.

Chaque invocation (autorisée ou refusée) est journalisée : un refus
silencieux serait une faille de traçabilité (instruction/08). Le tool ne
connaît ni la base ni les services — il délègue l'audit au composant appelant.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from app.core.errors import PermissionDeniedError, ValidationError
from app.tools.permissions import AgentIdentity, PermissionPolicy

__all__ = ["CarsoShellInput", "CarsoShellResult", "build_carso_shell"]

_MAX_COMMAND_CHARS = 2000
_TIMEOUT_EXIT_CODE = 124
# Séparateurs et redirecteurs interdits : un allowlist sur le premier mot ne
# protège pas contre ``Get-ChildItem; Remove-Item x``. Deny par défaut : une
# commande d'inspection n'a pas besoin de chaînage ni de redirection.
_CARACTERES_INTERDITS = (";", "|", "&", "`", ">", "<", "\n", "\r")


class CarsoShellInput(BaseModel):
    """Entrée typée du shell contrôlé : une commande, un répertoire de travail."""

    commande: str = Field(
        min_length=1,
        max_length=_MAX_COMMAND_CHARS,
        description="Commande à exécuter ; le premier mot doit être dans l'allowlist.",
    )
    repertoire: str | None = Field(
        default=None,
        max_length=1024,
        description="Répertoire de travail relatif à une racine autorisée (optionnel).",
    )


class CarsoShellResult(BaseModel):
    """Sortie normalisée : jamais d'exception brute renvoyée à l'agent."""

    code_retour: int
    stdout: str
    stderr: str
    timeout: bool = False


def _split_csv(valeur: str) -> list[str]:
    """Éclate un champ CSV de Settings en jetons normalisés (sans vides)."""
    return [part.strip().lower() for part in valeur.split(",") if part.strip()]


def _racines_autorisees(roots_csv: str) -> list[Path]:
    """Résout les racines autorisées (chemins absolus, canoniques)."""
    return [
        Path(brut.strip()).resolve()
        for brut in roots_csv.split(",")
        if brut.strip()
    ]


def _est_sous_chemin(candidat: Path, racines: list[Path]) -> bool:
    """Vrai si ``candidat`` est égal à ou sous une des racines autorisées."""
    for racine in racines:
        try:
            candidat.relative_to(racine)
        except ValueError:
            continue
        return True
    return False


def _resoudre_repertoire(
    repertoire: str | None, racines: list[Path]
) -> Path | None:
    """Résout le répertoire de travail DANS une racine autorisée.

    ``None`` => la première racine. Tout dépassement (``..``, racine système,
    lien symbolique sortant) est refusé : le path traversal n'est jamais
    silencieusement corrigé (AGENTS.md §7).
    """
    if not racines:
        return None
    if repertoire is None or not repertoire.strip():
        return racines[0]
    candidat = Path(repertoire)
    if not candidat.is_absolute():
        candidat = racines[0] / candidat
    candidat = candidat.resolve()
    if not _est_sous_chemin(candidat, racines):
        msg = f"Répertoire hors des racines autorisées : {repertoire!r}"
        raise PermissionDeniedError(msg, details={"repertoire": repertoire})
    return candidat
def build_carso_shell(
    *,
    identite: AgentIdentity,
    policy: PermissionPolicy,
    settings: Any,
    audit: Any | None = None,
) -> Any:
    """Construit le ``TypedTool`` ``carso_shell`` pour UN agent.

    Args:
        identite: identité de l'agent appelant (traçabilité).
        policy: permissions de l'agent — la capacité ``shell`` est exigée.
        settings: ``Settings`` (allowlist, racines, budgets). Passé explicitement
            pour la testabilité ; ``get_settings()`` reste au composant appelant.
        audit: callback optionnel ``f(action: str, details: dict)`` pour la
            traçabilité hors base (les agents n'ont pas de session ORM).

    Returns:
        Un ``TypedTool`` nommé ``carso_shell`` (schéma ``CarsoShellInput``).

    Raises:
        ValidationError: pas de racine autorisée => shell désactivé pour cet
            agent (refus à la construction : configuration invalide explicite).
    """
    racines = _racines_autorisees(settings.shell_allowed_roots)
    if not racines:
        msg = (
            "carso_shell désactivé : aucune racine autorisée "
            "(SHELL_ALLOWED_ROOTS vide) — activer le shell est une décision explicite"
        )
        raise ValidationError(msg)

    allowlist = frozenset(_split_csv(settings.shell_allowed_commands))
    timeout_s = settings.shell_timeout_seconds
    max_octets = settings.shell_max_output_bytes

    def _tracer(action: str, details: dict[str, Any]) -> None:
        if audit is not None:
            audit(action, {"agent_id": identite.agent_id, **details})

    def _handler(payload: dict[str, Any]) -> dict[str, Any]:
        # Défense en profondeur : la capacité est revérifiée à CHAQUE invocation,
        # pas seulement à l'assemblage de l'agent.
        policy.require(identite.agent_id, "shell")

        commande = payload["commande"].strip()
        if not commande:
            raise ValidationError("Commande vide")

        premier_mot = commande.split(None, 1)[0].lower()
        if premier_mot not in allowlist:
            _tracer("shell.refuse", {"commande": premier_mot})
            msg = (
                f"Commande {premier_mot!r} hors allowlist "
                f"(autorisées : {sorted(allowlist)})"
            )
            raise PermissionDeniedError(
                msg, details={"commande": premier_mot, "allowlist": sorted(allowlist)}
            )

        if any(caractere in commande for caractere in _CARACTERES_INTERDITS):
            _tracer("shell.refuse", {"commande": "caracteres_interdits"})
            msg = (
                "Commande refusée : séparateurs/redirecteurs interdits "
                f"({_CARACTERES_INTERDITS!r}) — une commande d'inspection n'a "
                "pas besoin de chaînage"
            )
            raise PermissionDeniedError(msg)

        repertoire = _resoudre_repertoire(payload.get("repertoire"), racines)

        _tracer("shell.exec", {"commande": premier_mot, "repertoire": str(repertoire)})
        try:
            if sys.platform == "win32":
                # PowerShell au premier plan : cohérent avec l'environnement hôte
                # (les cmdlets allowlistés comme Get-ChildItem n'existent qu'ici).
                completed = subprocess.run(  # noqa: S603
                    [
                        "powershell",
                        "-NoProfile",
                        "-NonInteractive",
                        "-Command",
                        commande,
                    ],
                    cwd=repertoire,
                    timeout=timeout_s,
                    capture_output=True,
                    check=False,
                )
            else:
                completed = subprocess.run(  # noqa: S603, S604
                    commande,
                    shell=True,  # noqa: S604 — mêmes garde-fous, hôte non Windows
                    cwd=repertoire,
                    timeout=timeout_s,
                    capture_output=True,
                    check=False,
                )
        except subprocess.TimeoutExpired:
            _tracer("shell.timeout", {"commande": premier_mot})
            return CarsoShellResult(
                code_retour=_TIMEOUT_EXIT_CODE,
                stdout="",
                stderr=f"Commande interrompue après {timeout_s}s (budget shell)",
                timeout=True,
            ).model_dump()

        return CarsoShellResult(
            code_retour=completed.returncode,
            stdout=completed.stdout[:max_octets].decode("utf-8", errors="replace"),
            stderr=completed.stderr[:max_octets].decode("utf-8", errors="replace"),
        ).model_dump()

    from app.tools.base import TypedTool

    return TypedTool(
        name="carso_shell",
        description=(
            "Exécute une commande d'inspection dans un environnement contrôlé "
            "(allowlist + répertoires autorisés). Les commandes de mutation "
            "sont refusées."
        ),
        handler=_handler,
        input_schema=CarsoShellInput,
        tags=("shell", "inspection"),
    )
