"""Shared FastAPI dependencies."""

from datetime import UTC, datetime
from typing import Annotated
from uuid import NAMESPACE_URL, uuid5

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.application.services import UtilisateurService
from app.core.config import get_settings
from app.core.errors import AuthenticationError, PermissionDeniedError
from app.domain.enums import RoleUtilisateur
from app.domain.identity import Utilisateur
from app.infrastructure.database import get_session

_bearer = HTTPBearer(auto_error=False)

#: Utilisateur technique du mode lab : identité **stable** dans le processus
#: (id dérivé de l'email) — les routes qui lient des données au compte courant
#: (chat social, pièces jointes) restent testables et cohérentes entre deux
#: appels. Un UUID aléatoire par requête rendait ces routes incohérentes.
_LAB_USER_ID = uuid5(NAMESPACE_URL, "carso://lab-user")


def _utilisateur_lab() -> Utilisateur:
    # ``created_at`` fixe : l'objet n'est jamais persisté mais le schéma de
    # réponse ``UtilisateurRead`` l'exige (date d'inscription affichée).
    return Utilisateur(
        id=_LAB_USER_ID,
        email="lab@localhost",
        nom="Lab",
        prenom="Mode",
        password_hash="!",
        role=RoleUtilisateur.COLLABORATEUR.value,
        statut="actif",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )

DbSession = Annotated[Session, Depends(get_session)]


def get_current_user(
    session: DbSession,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)] = None,
) -> Utilisateur:
    """Exige un Bearer JWT si AUTH_SECRET est configuré.

    Sans secret (suite de tests / lab) : utilisateur technique anonyme *actif*
    collaborateur — jamais administrateur. Activer AUTH_SECRET est une
    décision de déploiement explicite.
    """
    settings = get_settings()
    secret = settings.auth_secret
    if secret is None or not secret.get_secret_value().strip():
        return _utilisateur_lab()
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise AuthenticationError("Authentification requise")
    return UtilisateurService(session).depuis_jeton(credentials.credentials, settings)


CurrentUser = Annotated[Utilisateur, Depends(get_current_user)]


def require_admin(user: CurrentUser) -> Utilisateur:
    if user.role != RoleUtilisateur.ADMINISTRATEUR.value:
        raise PermissionDeniedError("Réservé aux administrateurs")
    return user


AdminUser = Annotated[Utilisateur, Depends(require_admin)]

__all__ = [
    "AdminUser",
    "CurrentUser",
    "DbSession",
    "get_current_user",
    "get_session",
    "require_admin",
]
