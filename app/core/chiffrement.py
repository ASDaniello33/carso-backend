"""Chiffrement au repos des secrets administrables (paramètres).

Un secret qui doit être stocké en base (la clé du provider LLM, saisie depuis la
page Paramètres) n'y est jamais écrit en clair. Ce module est le **seul** endroit
qui sait le chiffrer et le déchiffrer : les services ne manipulent que des formes
chiffrées, et l'API ne renvoie jamais la valeur.

Clé de chiffrement, dans l'ordre :

1. ``PARAMETRES_CHIFFREMENT_KEY`` — clé Fernet dédiée (recommandé en production) ;
2. dérivation unidirectionnelle depuis ``AUTH_SECRET`` (HKDF-SHA256) : aucun
   secret supplémentaire à gérer, mais faire tourner ``AUTH_SECRET`` rend les
   secrets stockés illisibles — ils doivent alors être ressaisis.

Sans l'une des deux, le chiffrement est **indisponible** : enregistrer un secret
échoue avec un message explicite plutôt que de le stocker en clair.
"""

from __future__ import annotations

import base64
import hashlib
from functools import lru_cache

from app.core.config import Settings, get_settings
from app.core.errors import CarsoError

#: Sel fixe : la dérivation doit être stable entre deux processus.
_SEL_HKDF = b"carso-parametres-secrets-v1"


class ChiffrementIndisponible(CarsoError):
    """Aucune clé de chiffrement exploitable dans la configuration."""

    status_code = 409


def _materiel_brut(settings: Settings) -> str | None:
    """Clé Fernet explicite, ou secret à dériver (jamais les deux vides)."""
    explicite = settings.parametres_chiffrement_key
    if explicite is not None and explicite.get_secret_value().strip():
        return explicite.get_secret_value().strip()
    secret = settings.auth_secret
    if secret is not None and secret.get_secret_value().strip():
        return secret.get_secret_value().strip()
    return None


@lru_cache(maxsize=1)
def _fernet() -> object:
    """Instance Fernet construite une fois par processus (clé stable)."""
    from cryptography.fernet import Fernet

    brut = _materiel_brut(get_settings())
    if brut is None:
        raise ChiffrementIndisponible(
            "Aucune clé de chiffrement : renseigner PARAMETRES_CHIFFREMENT_KEY "
            "(ou AUTH_SECRET) dans backend/.env avant d'enregistrer un secret."
        )
    try:
        # Clé Fernet fournie telle quelle.
        return Fernet(brut.encode("ascii"))
    except Exception:
        # Sinon : secret arbitraire → clé de 32 octets par HKDF, encodée base64.
        derivee = hashlib.pbkdf2_hmac("sha256", brut.encode("utf-8"), _SEL_HKDF, 200_000)
        cle = base64.urlsafe_b64encode(derivee)
        return Fernet(cle)


def chiffrement_disponible(settings: Settings | None = None) -> bool:
    """Vrai si un secret peut être chiffré avec la configuration courante.

    Ne construit pas de clé durable : sert à l'interface pour expliquer pourquoi
    le champ est refusé **avant** la saisie.
    """
    reglages = settings or get_settings()
    if _materiel_brut(reglages) is None:
        return False
    try:
        from cryptography.fernet import Fernet  # noqa: F401
    except ImportError:  # pragma: no cover - dépend de l'installation
        return False
    return True


def chiffrer(valeur: str) -> str:
    """Chiffre une valeur en clair (jamais stockée telle quelle).

    Raises:
        ChiffrementIndisponible: aucune clé de chiffrement configurée.
    """
    return _fernet().encrypt(valeur.encode("utf-8")).decode("ascii")  # type: ignore[attr-defined]


def dechiffrer(jeton: str | None) -> str | None:
    """Déchiffre une valeur stockée, ou ``None`` si absente.

    Un jeton illisible (clé de chiffrement changée) renvoie ``None`` : le runtime
    retombe alors sur la clé du ``.env`` au lieu de démarrer avec une valeur
    corrompue. L'appelant décide comment le signaler.
    """
    if not jeton:
        return None
    try:
        return _fernet().decrypt(jeton.encode("ascii")).decode("utf-8")  # type: ignore[attr-defined]
    except ChiffrementIndisponible:
        raise
    except Exception:
        return None


def effacer_cache() -> None:
    """Vide la clé mémorisée (tests, changement de configuration)."""
    _fernet.cache_clear()


__all__ = [
    "ChiffrementIndisponible",
    "chiffrement_disponible",
    "chiffrer",
    "dechiffrer",
    "effacer_cache",
]
