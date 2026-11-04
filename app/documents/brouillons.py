"""Brouillons de rédaction progressive des documents professionnels.

Un document long (une offre technique de vingt pages, un rapport) demanderait à
un modèle d'« écrire » 40 000 à 150 000 caractères d'un seul tenant dans les
arguments d'un tool call. Une génération aussi longue fait taire le fournisseur
en cours de route : plus aucun chunk n'arrive, le run meurt — et l'outil
n'est **jamais exécuté** (aucun document déposé, aucune trace de son appel).
La parade est structurelle : le modèle écrit **section par section**, chaque
tour de modèle restant petit ; le serveur assemble.

Le brouillon est un état **technique** (un griffonnage serveur), pas une donnée
métier : ni entité ``Document``, ni validation humaine, ni ancre. Il vit sous
``<storage_root>/_brouillons/{agent}/{uuid}.json`` — même convention que
``_scripts`` — avec l'état et les sections du corps. Il est supprimé dès la
finalisation réussie ; les brouillons abandonnés sont purgés après ``TTL_JOURS``
au prochain ouverture de l'agent.

L'accès est nominatif : un agent ne peut ni lire, ni continuer, ni finaliser le
brouillon d'un autre agent — l'identifiant seul ne suffit jamais (le chemin est
résolu dans le dossier de l'agent appelant).
"""

from __future__ import annotations

import json
import re
import uuid as _uuid_module
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

from app.core.errors import ValidationError

__all__ = [
    "TTL_JOURS",
    "charger",
    "enregistrer",
    "ouvrir",
    "racine_brouillons",
    "supprimer",
]

#: Durée de vie d'un brouillon abandonné (en jours). Purge au prochain
#: ``ouvrir`` de l'agent : un griffonnage n'a pas à vivre éternellement.
TTL_JOURS = 7

#: Sous-dossier technique de ``storage_root`` (même convention que ``_scripts``).
_REPERTOIRE = "_brouillons"

#: Un identifiant d'agent devient un nom de dossier : tout caractère hors
#: ``[A-Za-z0-9_-]`` est remplacé (aucun path traversal possible par ce canal).
_TOKEN_DOSSIER = re.compile(r"[^A-Za-z0-9_-]")


def racine_brouillons(storage_root: str | Path) -> Path:
    """Racine technique des brouillons, sous l'arbre de stockage géré."""
    return Path(storage_root) / _REPERTOIRE


def _dossier(racine: Path, agent_id: str) -> Path:
    return racine / _TOKEN_DOSSIER.sub("_", agent_id or "agent")


def _chemin(racine: Path, agent_id: str, identifiant: Any) -> Path:
    """Chemin d'un brouillon, **dans le dossier de l'agent appelant**.

    Un ``brouillon_id`` invalide produit une erreur explicite : jamais un
    UUID fabriqué depuis un texte arbitraire (path traversal coupé ici).
    """
    try:
        identifiant_uuid = UUID(str(identifiant))
    except (ValueError, TypeError, AttributeError) as erreur:
        raise ValidationError(
            f"brouillon_id n'est pas un UUID valide : {identifiant!r}"
        ) from erreur
    return _dossier(racine, agent_id) / f"{identifiant_uuid}.json"


def _ecrire(chemin: Path, etat: dict[str, Any]) -> None:
    chemin.parent.mkdir(parents=True, exist_ok=True)
    temporaire = chemin.with_suffix(".tmp")
    temporaire.write_text(
        json.dumps(etat, ensure_ascii=False, default=str), encoding="utf-8"
    )
    temporaire.replace(chemin)


def _purger_perimes(dossier: Path, maintenant: datetime) -> None:
    """Supprime les brouillons de CET agent plus vieux que ``TTL_JOURS``."""
    limite = (maintenant - timedelta(days=TTL_JOURS)).timestamp()
    for fichier in dossier.glob("*.json"):
        try:
            if fichier.stat().st_mtime < limite:
                fichier.unlink()
        except OSError:
            continue


def ouvrir(racine: Path, agent_id: str, meta: dict[str, Any]) -> dict[str, Any]:
    """Ouvre un brouillon vierge pour l'agent et renvoie son état.

    Les paramètres d'habillage (titre, format, thème, ancre…) sont figés dans
    l'état : les appels suivants (``section``, ``finaliser``) n'ont plus à les
    répéter, et la finalisation les relit tels quels.
    """
    dossier = _dossier(racine, agent_id)
    dossier.mkdir(parents=True, exist_ok=True)
    maintenant = datetime.now(UTC)
    _purger_perimes(dossier, maintenant)
    identifiant = _uuid_module.uuid4()
    etat: dict[str, Any] = {
        "brouillon_id": str(identifiant),
        "agent_id": agent_id,
        "meta": dict(meta),
        "sections": [],
        "cree": maintenant.isoformat(),
        "maj": maintenant.isoformat(),
    }
    _ecrire(dossier / f"{identifiant}.json", etat)
    return etat


def charger(racine: Path, agent_id: str, identifiant: Any) -> dict[str, Any]:
    """Charge un brouillon **appartenant à cet agent**.

    Erreurs explicites (l'agent sait quoi faire) : identifiant invalide,
    brouillon inconnu ou déjà finalisé, brouillon d'un autre agent.
    """
    chemin = _chemin(racine, agent_id, identifiant)
    if not chemin.exists():
        raise ValidationError(
            f"Brouillon {identifiant} inconnu (ou déjà finalisé) : "
            "commencer par action='demarrer'."
        )
    etat = json.loads(chemin.read_text(encoding="utf-8"))
    if etat.get("agent_id") != agent_id:
        raise ValidationError(
            "Ce brouillon appartient à un autre agent : un brouillon "
            "n'est pas partageable."
        )
    return etat


def enregistrer(racine: Path, agent_id: str, etat: dict[str, Any]) -> None:
    """Réécrit l'état du brouillon (écriture atomique : ``.tmp`` puis rename)."""
    etat["maj"] = datetime.now(UTC).isoformat()
    chemin = _chemin(racine, agent_id, etat["brouillon_id"])
    _ecrire(chemin, etat)


def supprimer(racine: Path, agent_id: str, identifiant: Any) -> None:
    """Supprime le brouillon (finalisation réussie) — silencieux si absent."""
    _chemin(racine, agent_id, identifiant).unlink(missing_ok=True)
