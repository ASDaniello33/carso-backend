"""Résolution du répertoire géré de la base de connaissance.

Un seul point de vérité : ``KNOWLEDGE_STORAGE_PATH`` s'il est renseigné (absolu,
ou relatif à ``STORAGE_ROOT``), sinon ``STORAGE_ROOT/connaissances``. Le chemin
physique n'est **jamais** renvoyé par l'API (AGENTS.md §9) ; il n'est utilisé que
par la couche de service pour lire et écrire.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath

from app.core.config import Settings, get_settings
from app.core.errors import ValidationError


def racine_connaissances(settings: Settings | None = None) -> Path:
    """Racine des fichiers d'ouvrages (créée à la demande par l'appelant)."""
    cfg = settings or get_settings()
    racine_storage = Path(cfg.storage_root).expanduser()
    if cfg.knowledge_storage_path:
        chemin = Path(cfg.knowledge_storage_path).expanduser()
        return chemin if chemin.is_absolute() else racine_storage / chemin
    return racine_storage / cfg.knowledge_directory


def repertoire_vecteurs(settings: Settings | None = None) -> Path:
    """Répertoire de l'index vectoriel local (sous la racine des ouvrages)."""
    cfg = settings or get_settings()
    return racine_connaissances(cfg) / cfg.knowledge_vectors_subdir


def resoudre_fichier(racine: Path, chemin_logique: str) -> Path:
    """Chemin physique d'un ouvrage, garanti **sous** ``racine``.

    L'arborescence de la base de connaissance est ``{id}/{nom}`` : deux
    segments contrôlés (un UUID et un nom déjà validé par
    ``sanitize_filename``). Tout autre forme est refusée — un chemin libre
    n'entre jamais ici (anti path-traversal).

    Raises:
        ValidationError: chemin absolu, segments ``.``/``..``, profondeur
            inattendue, ou résolution hors racine (lien symbolique sortant).
    """
    pur = PurePosixPath(chemin_logique or "")
    if not chemin_logique or pur.is_absolute() or "\\" in chemin_logique:
        raise ValidationError(
            "Chemin de connaissance invalide", details={"chemin": chemin_logique}
        )
    if any(segment in {".", ".."} for segment in pur.parts) or len(pur.parts) != 2:
        raise ValidationError(
            "Chemin de connaissance invalide (attendu {identifiant}/{fichier})",
            details={"chemin": chemin_logique},
        )
    base = racine.resolve()
    candidat = base.joinpath(*pur.parts).resolve()
    if not candidat.is_relative_to(base):
        raise ValidationError(
            "Chemin hors de la base de connaissance", details={"chemin": chemin_logique}
        )
    return candidat


__all__ = ["racine_connaissances", "repertoire_vecteurs", "resoudre_fichier"]
