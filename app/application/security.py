"""Hash mot de passe + JWT (secrets jamais loggés)."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from app.core.config import Settings
from app.core.errors import AuthenticationError, ValidationError

_SEP = "$"


def hasher_mot_de_passe(clair: str) -> str:
    """PBKDF2-HMAC-SHA256 (stdlib, pas de dépendance extra)."""
    if len(clair) < 8:
        raise ValidationError("Le mot de passe doit contenir au moins 8 caractères")
    sel = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", clair.encode("utf-8"), sel.encode("ascii"), 200_000)
    return f"pbkdf2_sha256{_SEP}200000{_SEP}{sel}{_SEP}{digest.hex()}"


def verifier_mot_de_passe(clair: str, enregistre: str) -> bool:
    try:
        algo, rounds, sel, attendu = enregistre.split(_SEP)
    except ValueError:
        return False
    if algo != "pbkdf2_sha256":
        return False
    digest = hashlib.pbkdf2_hmac(
        "sha256", clair.encode("utf-8"), sel.encode("ascii"), int(rounds)
    )
    return hmac.compare_digest(digest.hex(), attendu)


def emettre_jeton(user_id: UUID, role: str, email: str, settings: Settings) -> str:
    """JWT HS256 minimal (header.payload.signature) — pas de lib tierce obligatoire."""
    import base64
    import json

    secret = _secret(settings)
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "role": role,
        "email": email,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=settings.auth_token_minutes)).timestamp()),
    }
    header = base64.urlsafe_b64encode(b'{"alg":"HS256","typ":"JWT"}').rstrip(b"=")
    body = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).rstrip(
        b"="
    )
    msg = header + b"." + body
    sig = hmac.new(secret.encode("utf-8"), msg, hashlib.sha256).digest()
    return (msg + b"." + base64.urlsafe_b64encode(sig).rstrip(b"=")).decode("ascii")


def decoder_jeton(jeton: str, settings: Settings) -> dict[str, Any]:
    import base64
    import json

    secret = _secret(settings)
    parties = jeton.split(".")
    if len(parties) != 3:
        raise AuthenticationError("Jeton invalide")
    header_b, body_b, sig_b = parties
    msg = f"{header_b}.{body_b}".encode()

    def pad(s: str) -> str:
        return s + "=" * (-len(s) % 4)
    attendu = hmac.new(secret.encode("utf-8"), msg, hashlib.sha256).digest()
    recu = base64.urlsafe_b64decode(pad(sig_b))
    if not hmac.compare_digest(attendu, recu):
        raise AuthenticationError("Jeton invalide")
    payload = json.loads(base64.urlsafe_b64decode(pad(body_b)))
    if int(payload.get("exp", 0)) < int(datetime.now(UTC).timestamp()):
        raise AuthenticationError("Jeton expiré")
    return payload


def _secret(settings: Settings) -> str:
    if settings.auth_secret is None or not settings.auth_secret.get_secret_value().strip():
        raise ValidationError(
            "AUTH_SECRET n'est pas configuré — l'authentification est une décision explicite"
        )
    return settings.auth_secret.get_secret_value()
