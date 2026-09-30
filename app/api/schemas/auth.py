"""Schémas API — authentification.

``ProfilUpdate`` et ``MotDePasseUpdate`` ne concernent que **son propre** compte :
l'identifiant vient toujours du JWT, jamais du corps de la requête (même règle que
les autres décisions humaines). Aucun de ces schémas ne renvoie de mot de passe ni
de hash, et rien n'en est journalisé (AGENTS.md §9).
"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class RegisterCreate(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    nom: str = Field(min_length=1, max_length=100)
    prenom: str = Field(min_length=1, max_length=100)
    mot_de_passe: str = Field(min_length=8, max_length=200)


class ProfilUpdate(BaseModel):
    """Identité modifiable par le titulaire du compte (l'email ne l'est pas ici)."""

    nom: str = Field(min_length=1, max_length=100)
    prenom: str = Field(min_length=1, max_length=100)


class MotDePasseUpdate(BaseModel):
    """Changement de mot de passe : l'ancien prouve la maîtrise du compte."""

    ancien_mot_de_passe: str = Field(min_length=1, max_length=200)
    nouveau_mot_de_passe: str = Field(min_length=8, max_length=200)


class ActivationCreate(BaseModel):
    """Décision de validation d'une inscription : le rôle de compte attribué.

    Choix validé : ``collaborateur`` (équipe interne) ou ``formateur`` (accès
    restreint aux missions/sessions où il est affecté). Un rôle absent laisse le
    défaut du service (collaborateur) ; le compte administrateur ne s'attribue
    jamais via cette décision.
    """

    model_config = ConfigDict(extra="forbid")

    role: Literal["collaborateur", "formateur"] | None = None


class RoleUpdate(BaseModel):
    """Changement de rôle d'un compte existant (page Utilisateurs, admin).

    Même choix validé qu'à la validation d'une inscription : ``collaborateur``
    (équipe interne) ou ``formateur`` (accès restreint). Le rôle
    ``administrateur`` ne s'attribue pas depuis la liste des comptes.
    """

    model_config = ConfigDict(extra="forbid")

    role: Literal["collaborateur", "formateur"]


class ReinitialisationMotDePasseCreate(BaseModel):
    """Réinitialisation admin : le nouveau mot de passe est choisi par l'admin.

    Il est communiqué au titulaire hors système (AGENTS.md §9) : jamais
    renvoyé par l'API, jamais journalisé, jamais tracé dans l'audit.
    """

    nouveau_mot_de_passe: str = Field(min_length=8, max_length=200)


class LoginCreate(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    mot_de_passe: str = Field(min_length=1, max_length=200)


class TokenRead(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UtilisateurRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: str
    nom: str
    prenom: str
    role: str
    statut: str
    #: Date d'inscription — affichée sur la page Profil, jamais un identifiant
    #: technique supplémentaire.
    created_at: datetime
    #: Avatar personnel (chemin logique, scope ``chat``) — incrément 24.
    photo_profil_chemin: str | None = None
