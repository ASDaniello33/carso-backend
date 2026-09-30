"""Sécurité des chemins du système documentaire (instruction/06 §3, instruction/08 §4).

Trois garanties, dans cet ordre :

1. ``sanitize_filename`` — un nom d'entrée utilisateur devient un nom **sûr** ou
   une erreur explicite. On ne « corrige » jamais silencieusement un nom douteux :
   les séparateurs de chemin, les noms réservés Windows et les caractères de
   contrôle sont **refusés**, pas nettoyés.
2. ``build_logical_path`` — le chemin logique est **construit** à partir de
   segments contrôlés (scope fermé + identifiant d'entité), jamais concaténé
   à partir d'entrée utilisateur (instruction/06 §3).
3. ``resolve_within_root`` — point de passage **obligatoire** avant tout accès
   disque : chemin relatif, premier segment appartenant à la liste fermée des
   scopes, aucun ``.``/``..``, et vérification finale ``is_relative_to(root)``
   après résolution (ce qui neutralise aussi les liens symboliques sortants).

Le ``storage_path`` persité en base est un chemin **logique** (POSIX, relatif à
la racine) ; il n'est jamais utilisé pour lire sans repasser par
``resolve_within_root``.
"""

from __future__ import annotations

import unicodedata
from pathlib import Path, PurePosixPath
from uuid import UUID

from app.core.errors import ValidationError

#: Arborescence confirmée (instruction/06 §3). Liste FERMÉE : un chemin dont le
#: premier segment n'y figure pas n'est jamais résolu par le storage.
SCOPES: frozenset[str] = frozenset(
    {
        "appel_proposition",
        # Conservé pour lire les fichiers historiques déjà classés sous
        # ``appel_offre/{id}/`` (instruction/06). Les nouveaux dépôts
        # d'appels à proposition utilisent ``appel_proposition``.
        "appel_offre",
        # Scope officiel des offres (``Offre`` générique). Les dépôts nouveaux
        # écrivent sous ``offres/{id}/``.
        "offres",
        # Scope HISTORIQUE conservé en LECTURE : les fichiers déjà classés sous
        # ``offres_formation/{id}/`` restent résolvables (aucun déplacement de
        # fichier n'est fait par la migration — décision documentée, ADR 0004).
        "offres_formation",
        "equipes",
        # Chat social (incrément 20) : messages et annonces avec pièce jointe.
        # Un dossier par conversation (``chat/{conversation_id}/``) et un
        # dossier par annonce (``chat/annonce-{annonce_id}/``) — le préfixe
        # ``annonce-`` distingue les deux espaces, le validateur refusant
        # tout segment libre.
        "chat",
        "missions",
        # Ajout 20/09 (ADR 0003) : les documents propres à une session (fiche de
        # présence, checklist, rapport) sont ancrés sur ``documents.session_id``
        # et non sur la mission. Un document doit retrouver son dossier après un
        # remplacement (une ancre = un scope). Déviation assumée par rapport à la
        # cible instruction/06 §3 — à confirmer avec CARSO.
        "sessions",
        # Ajout Phase 4 : les documents rattachés à une organisation cliente
        # (modèles/templates) n'ont pas de dossier dans instruction/06 §3 alors
        # que ``documents.organisation_id`` existe. Scope PROVISOIRE [?] — les
        # règles définitives de classement restent à confirmer avec CARSO.
        "organisations",
        # Document sans ancre métier (génération ou dépôt hors fiche).
        # Dossier : ``generated/{id_du_document}/`` — l'identifiant est celui
        # de la ligne ``documents``, jamais un UUID inventé.
        "generated",
    }
)

#: Longueur maximale d'un nom de fichier (la colonne ``documents.nom`` est
#: ``String(255)`` ; on garde une marge pour le suffixe de version ``_v{n}``).
MAX_FILENAME_LENGTH = 200

#: Caractères spéciaux acceptés en plus des alphanumériques Unicode (accents
#: français inclus, ``str.isalnum()`` les couvre). Liste positive : tout le
#: reste est refusé.
_ALLOWED_SPECIALS: frozenset[str] = frozenset(" ._-'’()[]&+,°#@")

#: Noms de périphériques réservés Windows : un fichier ainsi nommé est
#: inaccessible (ou détourne une E/S) sur un poste Windows.
_RESERVED_WINDOWS_NAMES: frozenset[str] = frozenset(
    {
        "con",
        "prn",
        "aux",
        "nul",
        *(f"com{i}" for i in range(1, 10)),
        *(f"lpt{i}" for i in range(1, 10)),
    }
)


def extension_of(nom: str) -> str:
    """Extension normalisée (minuscules, sans point) ; chaîne vide si absente."""
    return Path(nom).suffix.lstrip(".").lower()


def sanitize_filename(nom: str) -> str:
    """Valide un nom de fichier fourni par un utilisateur.

    Args:
        nom: nom brut (upload, formulaire, nom d'origine d'un document).

    Returns:
        Le nom normalisé (NFC, espaces de bord retirés), prêt à être utilisé
        comme segment de chemin.

    Raises:
        ValidationError: nom vide, séparateurs de chemin, caractères de
            contrôle ou interdits, nom réservé Windows, point/espace final,
            longueur excessive, ou extension absente.
    """
    if not isinstance(nom, str) or not nom.strip():
        msg = "Nom de fichier vide"
        raise ValidationError(msg)

    if "\x00" in nom or any(ord(caractere) < 32 or ord(caractere) == 127 for caractere in nom):
        msg = "Nom de fichier contenant des caractères de contrôle"
        raise ValidationError(msg)

    normalise = unicodedata.normalize("NFC", nom).strip()

    if "/" in normalise or "\\" in normalise:
        msg = "Le nom de fichier ne doit pas contenir de séparateur de chemin"
        raise ValidationError(msg, details={"nom": nom})

    if normalise in {".", ".."}:
        msg = "Nom de fichier relatif interdit"
        raise ValidationError(msg, details={"nom": nom})

    if normalise.endswith((".", " ")):
        msg = "Le nom de fichier ne doit pas se terminer par un point ou un espace"
        raise ValidationError(msg, details={"nom": nom})

    if len(normalise) > MAX_FILENAME_LENGTH:
        msg = f"Nom de fichier trop long (max {MAX_FILENAME_LENGTH} caractères)"
        raise ValidationError(msg, details={"longueur": len(normalise)})

    for caractere in normalise:
        if not (caractere.isalnum() or caractere in _ALLOWED_SPECIALS):
            msg = f"Caractère interdit dans le nom de fichier : {caractere!r}"
            raise ValidationError(msg, details={"caractere": caractere})

    if normalise.split(".")[0].strip().lower() in _RESERVED_WINDOWS_NAMES:
        msg = "Nom de fichier réservé par le système"
        raise ValidationError(msg, details={"nom": normalise})

    if not extension_of(normalise):
        msg = "Le nom de fichier doit comporter une extension"
        raise ValidationError(msg, details={"nom": normalise})

    return normalise


def build_logical_path(scope: str, entity_id: UUID, nom: str) -> str:
    """Construit le chemin logique ``{scope}/{entity_id}/{nom}``.

    Le scope appartient à ``SCOPES`` et l'identifiant est un ``UUID`` : aucun
    segment ne peut provenir d'une chaîne utilisateur libre.

    Raises:
        ValidationError: scope inconnu ou nom de fichier invalide.
    """
    if scope not in SCOPES:
        msg = f"Scope documentaire inconnu : {scope!r}"
        raise ValidationError(msg, details={"scopes": sorted(SCOPES)})

    return f"{scope}/{entity_id}/{sanitize_filename(nom)}"


def directory_of(scope: str, entity_id: UUID) -> str:
    """Répertoire logique d'une entité (sans nom de fichier)."""
    if scope not in SCOPES:
        msg = f"Scope documentaire inconnu : {scope!r}"
        raise ValidationError(msg, details={"scopes": sorted(SCOPES)})
    return f"{scope}/{entity_id}"


def resolve_within_root(root: str | Path, logical_path: str) -> Path:
    """Résout un chemin logique sous ``root``, ou refuse.

    Args:
        root: racine du stockage documentaire (``settings.storage_root``).
        logical_path: chemin logique validé, ex. ``missions/<uuid>/fiche.pdf``.

    Returns:
        Chemin absolu résolu, garanti **à l'intérieur** de ``root``.

    Raises:
        ValidationError: chemin absolu, segment ``.``/``..``, premier segment
            hors ``SCOPES``, séparateur Windows, ou résolution hors racine
            (lien symbolique sortant inclus).
    """
    if not logical_path or not isinstance(logical_path, str):
        msg = "Chemin logique documentaire vide"
        raise ValidationError(msg)

    if "\\" in logical_path or "\x00" in logical_path:
        msg = "Chemin logique documentaire invalide"
        raise ValidationError(msg, details={"chemin": logical_path})

    pur = PurePosixPath(logical_path)
    if pur.is_absolute():
        msg = "Chemin logique documentaire absolu interdit"
        raise ValidationError(msg, details={"chemin": logical_path})

    segments = pur.parts
    if any(segment in {".", ".."} for segment in segments):
        msg = "Chemin logique documentaire relatif interdit"
        raise ValidationError(msg, details={"chemin": logical_path})

    if not segments or segments[0] not in SCOPES:
        msg = "Le chemin doit commencer par un scope documentaire connu"
        raise ValidationError(msg, details={"chemin": logical_path, "scopes": sorted(SCOPES)})

    # L'arborescence documentaire est exactement {scope}/{id}/{fichier} :
    # tout chemin plus court est malformé (et n'est jamais un fichier du système).
    if len(segments) < 3:
        msg = "Chemin logique documentaire incomplet (attendu {scope}/{id}/{fichier})"
        raise ValidationError(msg, details={"chemin": logical_path})

    racine = Path(root)
    candidat = racine.joinpath(*segments)
    racine_resolue = racine.resolve()
    resolu = candidat.resolve()

    if resolu != racine_resolue and not resolu.is_relative_to(racine_resolue):
        msg = "Chemin hors de la racine de stockage"
        raise ValidationError(msg, details={"chemin": logical_path})

    return resolu


def versioned_filename(nom: str, version: int) -> str:
    """Nom de fichier d'une version donnée : ``rapport.pdf`` → ``rapport_v2.pdf``.

    Les règles de nommage définitives de CARSO ne sont pas confirmées
    (AGENTS.md §1.5) : cette convention ``_v{n}`` est provisoire et documentée,
    mais elle garantit qu'aucune version n'écrase le fichier d'une autre.
    """
    sur = sanitize_filename(nom)
    if version <= 1:
        return sur

    chemin = Path(sur)
    suffixe = chemin.suffix
    marqueur = f"_v{version}"
    tige = chemin.stem
    max_tige = MAX_FILENAME_LENGTH - len(suffixe) - len(marqueur)
    return f"{tige[:max_tige]}{marqueur}{suffixe}"
