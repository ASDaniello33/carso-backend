"""Espace de travail persistant d'un compte (INC-116).

Chaque compte dispose de **son** dossier, sous ``workdirs/user-<uuid>`` : les
scripts et les fichiers qu'un agent y produit survivent à l'appel et ne sont
jamais partagés avec un autre compte.

```text
<storage_root>/../workdirs/       (ou WORKDIRS_ROOT) — à côté du stockage
officiel
└── user-<uuid>/
    ├── .scripts/                 scripts Python créés par l'agent
    ├── .sandbox/                 lanceur confiné (généré)
    └── …                         fichiers de travail du compte
```

Le confinement est double : ici, le chemin est résolu puis vérifié
(``relative_to``), ce qui refuse ``..``, un lien sortant ou un identifiant
forgé ; à l'exécution, la garde d'audit du lanceur interdit toute écriture hors
de ce dossier (``app/tools/scripts.py``).

Aucun repli silencieux : sans identifiant de compte valide, aucune fonction ne
crée ni ne renvoie de dossier.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from app.core.errors import PermissionDeniedError, ValidationError

__all__ = [
    "NOM_PREFIXE",
    "NOM_PREFIXE_TEMPORAIRE",
    "dossier_temporaire",
    "espace_utilisateur",
    "lister_fichiers",
    "nom_espace",
    "purger_temporaires",
    "racine_espaces",
    "supprimer_temporaire",
]

#: Préfixe des dossiers de compte — le chemin demandé est ``workdirs/user-uuid``.
NOM_PREFIXE = "user-"

#: Préfixe d'un dossier de travail **ancré** (INC-124), créé sous le dossier
#: métier et supprimé à la fin de l'appel. Le point initial le rend invisible
#: dans un listing de l'espace de travail ; il ne devient jamais un document.
NOM_PREFIXE_TEMPORAIRE = ".tmp-run-"

#: Bornes du listing renvoyé à l'agent (jamais un dossier entier).
_LIMITE_FICHIERS = 60


def racine_espaces(settings: Any) -> Path:
    """Racine des espaces de travail : ``workdirs_root`` ou le voisin du stockage.

    Par défaut ``<storage_root>/../workdirs`` : avec ``storage_root`` =
    ``backend/var/storage/carso``, les dossiers sont donc
    ``backend/var/storage/workdirs/user-<uuid>``. Le travail d'un agent reste
    ainsi **hors** de l'arborescence documentaire officielle.
    """
    brut = str(getattr(settings, "workdirs_root", "") or "").strip()
    if brut:
        base = Path(brut).expanduser()
    else:
        storage = Path(str(getattr(settings, "storage_root", "/storage/carso")))
        base = storage.parent / "workdirs"
    return base.resolve()


def nom_espace(utilisateur_id: str | UUID) -> str:
    """Nom logique du dossier d'un compte — l'UUID est validé, jamais recopié brut.

    Raises:
        ValidationError: identifiant absent ou qui n'est pas un UUID.
    """
    if utilisateur_id is None or not str(utilisateur_id).strip():
        raise ValidationError(
            "Espace de travail indisponible : aucun compte identifié pour cet appel."
        )
    try:
        identifiant = str(UUID(str(utilisateur_id)))
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValidationError(
            f"Identifiant de compte invalide pour l'espace de travail : {utilisateur_id!r}"
        ) from exc
    return f"{NOM_PREFIXE}{identifiant}"


def espace_utilisateur(
    settings: Any, utilisateur_id: str | UUID, *, creer: bool = True
) -> Path:
    """Dossier de travail du compte, créé à la demande et confiné à la racine.

    Raises:
        ValidationError: identifiant invalide.
        PermissionDeniedError: chemin résolu hors de la racine (lien, ``..``).
    """
    racine = racine_espaces(settings)
    espace = (racine / nom_espace(utilisateur_id)).resolve()
    try:
        espace.relative_to(racine)
    except ValueError as exc:  # pragma: no cover - dépend du système de fichiers
        raise PermissionDeniedError(
            "Espace de travail hors de la racine autorisée",
            details={"racine": str(racine)},
        ) from exc
    if creer:
        (espace / ".scripts").mkdir(parents=True, exist_ok=True)
        (espace / ".sandbox").mkdir(parents=True, exist_ok=True)
    return espace


def dossier_temporaire(dossier_metier: Path) -> Path:
    """Crée le dossier de travail d'une exécution ancrée, sous le dossier métier.

    Le script y écrit librement (c'est la seule zone d'écriture autorisée), et
    lit le dossier métier comme son **parent** — aucun chemin absolu n'a besoin
    d'être exposé. Le nom porte un identifiant d'exécution pour que deux appels
    simultanés ne partagent jamais le même dossier.

    Args:
        dossier_metier: dossier résolu d'une ancre métier (créé au besoin).

    Returns:
        Le chemin du dossier temporaire, vide.
    """
    dossier_metier.mkdir(parents=True, exist_ok=True)
    temporaire = dossier_metier / f"{NOM_PREFIXE_TEMPORAIRE}{uuid4().hex[:8]}"
    temporaire.mkdir(parents=True, exist_ok=False)
    return temporaire


def supprimer_temporaire(temporaire: Path) -> bool:
    """Supprime un dossier de travail ancré, **si** c'en est bien un.

    Le préfixe est la garde : une erreur de programmation ne doit jamais
    pouvoir effacer le dossier métier lui-même (ou un document officiel).

    Returns:
        ``True`` si un dossier a été supprimé.
    """
    chemin = Path(temporaire)
    if not chemin.name.startswith(NOM_PREFIXE_TEMPORAIRE) or not chemin.is_dir():
        return False
    shutil.rmtree(chemin, ignore_errors=True)
    return not chemin.exists()


def purger_temporaires(dossier_metier: Path, *, garder: Path | None = None) -> int:
    """Supprime les dossiers de travail laissés par un appel **interrompu**.

    Un run tué en cours de route (délai, redémarrage) laisse son
    ``.tmp-run-*`` derrière lui. Le prochain appel ancré du même dossier métier
    le nettoie, sans toucher au dossier de l'appel en cours.

    Returns:
        Le nombre de dossiers supprimés.
    """
    base = Path(dossier_metier)
    if not base.is_dir():
        return 0
    supprimes = 0
    for enfant in base.iterdir():
        if not enfant.is_dir() or not enfant.name.startswith(NOM_PREFIXE_TEMPORAIRE):
            continue
        if garder is not None and enfant.resolve() == Path(garder).resolve():
            continue
        if supprimer_temporaire(enfant):
            supprimes += 1
    return supprimes


def lister_fichiers(espace: Path, *, limite: int = _LIMITE_FICHIERS) -> list[dict[str, Any]]:
    """Fichiers visibles de l'espace (hors dossiers techniques), bornés.

    Les dossiers commençant par ``.`` (``.scripts``, ``.sandbox``) sont exclus :
    ce sont des détails d'implémentation, pas le travail du compte.
    Le dossier des aides (``carso_outils``) l'est aussi : il est recopié à
    chaque exécution, il ne fait pas partie du travail du compte.
    """
    if not espace.is_dir():
        return []
    fichiers: list[dict[str, Any]] = []
    for chemin in sorted(espace.rglob("*")):
        if not chemin.is_file():
            continue
        relatif = chemin.relative_to(espace)
        if any(partie.startswith(".") for partie in relatif.parts):
            continue
        if relatif.parts[0] == "carso_outils":
            continue
        try:
            taille = chemin.stat().st_size
        except OSError:  # pragma: no cover - fichier supprimé entre-temps
            continue
        fichiers.append(
            {
                "chemin": str(relatif).replace("\\", "/"),
                "taille_octets": taille,
            }
        )
        if len(fichiers) >= limite:
            break
    return fichiers
