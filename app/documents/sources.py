"""Source éditable des documents professionnels (correction par section).

Un document rendu par ``generer_document_professionnel`` naît d'un **corps HTML**
et d'un habillage (titre, format, thème…). Sans conserver cette source, corriger
une phrase obligeait l'agent à réécrire **tout** le corps : une génération
géante, exactement ce que la rédaction progressive (INC-105) évite, et le
document entier était refait pour un seul paragraphe.

La source est un état **technique**, pas une donnée métier : ni entité
``Document``, ni statut, ni validation humaine. Elle vit sous
``<storage_root>/_sources/{document_id}.json`` — même convention que
``_brouillons`` et ``_scripts`` — et ne sert qu'à re-rendre le document quand
une section change. Le fichier déposé, lui, reste la seule vérité officielle :
il est **remplacé par une nouvelle version**, jamais réécrit sur place.

Contenu d'un enregistrement ::

    {
      "document_id": "...",         # le document déposé (celui de la source)
      "meta": {...},                # habillage figé (titre, format, thème…)
      "graphiques": [...],          # specs de graphiques déclarées
      "corps": "<section>…",        # le corps HTML complet
      "principal_id": "...",        # le livrable de référence (docx/pptx/pdf)
      "jumeau_id": "..." | null,    # PDF jumeau déposé avec la même source
      "cree": "...", "maj": "..."
    }

Le principal et son jumeau partagent la **même** source : corriger l'un corrige
les deux (un DOCX corrigé sans son PDF d'aperçu, ou l'inverse, serait un
document à deux vérités).

Un document déposé sans source (import, ``generer_document`` structuré,
``generer_document_html``) n'est pas éditable par section : le module le dit
explicitement au lieu de deviner un contenu.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from app.core.errors import ValidationError

__all__ = [
    "charger",
    "charger_famille",
    "enregistrer",
    "racine_sources",
    "supprimer",
]

#: Sous-dossier technique de ``storage_root`` (même convention que ``_brouillons``).
_REPERTOIRE = "_sources"

#: Un identifiant de document devient un nom de fichier : hors de ce motif, refus
#: (aucun path traversal possible par ce canal).
_UID_VALIDE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def racine_sources(storage_root: str | Path) -> Path:
    """Racine technique des sources, sous l'arbre de stockage géré."""
    return Path(storage_root) / _REPERTOIRE


def _chemin(racine: Path, document_id: Any) -> Path:
    try:
        identifiant = str(UUID(str(document_id)))
    except (ValueError, TypeError, AttributeError) as erreur:
        raise ValidationError(
            f"document_id n'est pas un UUID valide : {document_id!r}"
        ) from erreur
    if not _UID_VALIDE.match(identifiant):  # ceinture et bretelles
        raise ValidationError(f"document_id invalide : {document_id!r}")
    return racine / f"{identifiant}.json"


def enregistrer(
    racine: Path,
    document_id: Any,
    *,
    corps: str,
    meta: dict[str, Any],
    graphiques: list[dict[str, Any]] | None = None,
    principal_id: Any = None,
    jumeau_id: Any = None,
) -> None:
    """Écrit (ou réécrit) la source d'un document rendu — écriture atomique."""
    chemin = _chemin(racine, document_id)
    chemin.parent.mkdir(parents=True, exist_ok=True)
    maintenant = datetime.now(UTC).isoformat()
    etat: dict[str, Any] = {
        "document_id": str(document_id),
        "meta": dict(meta),
        "graphiques": list(graphiques or []),
        "corps": corps,
        "principal_id": str(principal_id or document_id),
        "jumeau_id": str(jumeau_id) if jumeau_id else None,
        "cree": maintenant,
        "maj": maintenant,
    }
    temporaire = chemin.with_suffix(".tmp")
    temporaire.write_text(
        json.dumps(etat, ensure_ascii=False, default=str), encoding="utf-8"
    )
    temporaire.replace(chemin)


def charger_famille(
    racine: Path, document_id: Any, versions: list[Any] | tuple[Any, ...] = ()
) -> tuple[dict[str, Any], Any] | None:
    """Source d'un document, étendue à ses versions **connues de l'appelant**.

    Une version n'est pas un fichier neuf : v2 naît d'une source v1 et peut la
    réutiliser (`modifier_section_document`). Mais une correction qui n'a pas pu
    prendre la v2 pour point de départ crée un document distinct partageant la
    même source — il faut alors retrouver le fichier de source par son contenu
    (``principal_id`` commun), pas seulement par l'identifiant demandé.

    Args:
        racine: racine technique des sources.
        document_id: document à corriger (version quelconque acceptable).
        versions: versions du même nom et type, déjà résolues sous périmètre par
            le service (l'appelant ne devine rien ; un document hors périmètre
            n'y figure pas).

    Returns:
        ``(source, identifiant_de_la_version_dont_elle_provient)`` ou ``None``
        si aucune version ne porte de source (import, document structuré…).
    """
    directe = _chemin(racine, document_id)
    if directe.exists():
        return _lire(directe), str(document_id)

    chargees: list[tuple[dict[str, Any], str]] = []
    for version in versions:
        try:
            chemin = _chemin(racine, getattr(version, "id", version))
        except ValidationError:
            continue
        if chemin.exists():
            chargees.append((_lire(chemin), chemin.stem))
    if not chargees:
        return None
    # Priorité à la version la plus récente qui porte une source : c'est l'état
    # courant du document, jamais un corps périmé.
    chargees.sort(key=lambda paire: str(paire[0].get("maj") or ""), reverse=True)
    return chargees[0]


def _lire(chemin: Path) -> dict[str, Any]:
    """Lit un état de source, BOM toléré.

    Un fichier réécrit par un éditeur Windows peut porter un BOM : ``json.loads``
    le refuse (« Unexpected UTF-8 BOM ») alors que le contenu est valide. La
    source existe — on ne la déclare pas absente à cause d'un octet.
    """
    return json.loads(chemin.read_text(encoding="utf-8-sig"))


def charger(racine: Path, document_id: Any) -> dict[str, Any]:
    """Charge la source d'un document.

    Raises:
        ValidationError: source absente — le message dit **pourquoi** ce
            document n'est pas éditable par section, plutôt que de laisser
            l'agent croire à une erreur passagère.
    """
    chemin = _chemin(racine, document_id)
    if not chemin.exists():
        raise ValidationError(
            "Ce document n'a pas de source éditable : il n'a pas été produit "
            "par generer_document_professionnel (import, document structuré ou "
            "document libre). Le corriger suppose une nouvelle génération avec "
            "generer_document_professionnel — la correction par section "
            "s'applique aux documents rendus depuis un corps HTML.",
            details={"document_id": str(document_id)},
        )
    return _lire(chemin)


def supprimer(racine: Path, document_id: Any) -> None:
    """Supprime la source d'un document — silencieux si elle n'existe pas."""
    _chemin(racine, document_id).unlink(missing_ok=True)
