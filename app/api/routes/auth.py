"""Routes auth — register (pending), login, me, profil, activation admin.

``GET/PATCH /auth/me`` et ``POST /auth/me/mot-de-passe`` ne concernent que le
compte porté par le JWT : aucun identifiant utilisateur n'est accepté dans le
corps, sinon un utilisateur pourrait modifier le compte d'un autre.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, File, UploadFile, status
from fastapi.responses import FileResponse

from app.api.deps import AdminUser, CurrentUser, DbSession
from app.api.schemas.auth import (
    ActivationCreate,
    LoginCreate,
    MotDePasseUpdate,
    ProfilUpdate,
    RegisterCreate,
    ReinitialisationMotDePasseCreate,
    RoleUpdate,
    TokenRead,
    UtilisateurRead,
)
from app.application.services import UtilisateurService
from app.core.config import get_settings
from app.domain.identity import Utilisateur

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=UtilisateurRead, status_code=status.HTTP_201_CREATED)
def register(payload: RegisterCreate, session: DbSession) -> Utilisateur:
    """Crée un compte *pending* — aucun accès tant que l'admin n'active pas."""
    return UtilisateurService(session).inscrire(
        email=str(payload.email),
        nom=payload.nom,
        prenom=payload.prenom,
        mot_de_passe=payload.mot_de_passe,
    )


@router.post("/login", response_model=TokenRead)
def login(payload: LoginCreate, session: DbSession) -> TokenRead:
    jeton = UtilisateurService(session).authentifier(
        email=str(payload.email),
        mot_de_passe=payload.mot_de_passe,
        settings=get_settings(),
    )
    return TokenRead(access_token=jeton)


@router.get("/me", response_model=UtilisateurRead)
def me(user: CurrentUser) -> Utilisateur:
    return user


@router.patch("/me", response_model=UtilisateurRead)
def update_me(payload: ProfilUpdate, user: CurrentUser, session: DbSession) -> Utilisateur:
    """Modifie l'identité du compte connecté (nom, prénom) — mutation tracée."""
    return UtilisateurService(session, actor_id=str(user.id)).modifier_profil(
        user.id, nom=payload.nom, prenom=payload.prenom
    )


@router.post("/me/photo-profil", response_model=UtilisateurRead)
def definir_photo_profil(
    session: DbSession,
    user: CurrentUser,
    file: Annotated[UploadFile, File(description="Photo de profil (image)")],
) -> Utilisateur:
    """Définit (ou remplace) la photo de profil du compte connecté.

    L'image est stockée sous ``chat/profil-{user_id}/`` (scope ``chat`` du
    stockage documentaire : confinement de chemin, plafond de taille, type
    vérifié). Une image seule est acceptée — le service refuse le reste.
    """
    import io

    from fastapi import HTTPException


    if not (file.content_type or "").startswith("image/"):
        raise HTTPException(status_code=422, detail="Seule une image est acceptée")
    from app.documents.paths import build_logical_path
    from app.documents.storage import LocalDocumentStorage

    reglages = get_settings()
    stockage = LocalDocumentStorage(reglages.storage_root, max_bytes=reglages.max_upload_bytes)
    chemin = build_logical_path("chat", user.id, f"profil-{file.filename or 'photo'}")
    stocke = stockage.save(chemin, io.BytesIO(file.file.read()))
    return UtilisateurService(session, actor_id=str(user.id)).definir_photo_profil(
        user.id, chemin=stocke.logical_path
    )


@router.delete("/me/photo-profil", response_model=UtilisateurRead)
def supprimer_photo_profil(user: CurrentUser, session: DbSession) -> Utilisateur:
    """Retire la photo de profil (fichier + référence) du compte connecté."""
    return UtilisateurService(session, actor_id=str(user.id)).supprimer_photo_profil(user.id)


@router.get("/me/photo-profil", response_class=FileResponse)
def lire_photo_profil(user: CurrentUser, session: DbSession):
    """Sert l'image de profil du compte (Bearer requis — jamais d'URL publique)."""
    from fastapi import HTTPException
    from fastapi.responses import FileResponse

    from app.documents.paths import resolve_within_root

    if not user.photo_profil_chemin:
        raise HTTPException(status_code=404, detail="Aucune photo de profil")
    chemin = resolve_within_root(get_settings().storage_root, user.photo_profil_chemin)
    return FileResponse(path=chemin)


@router.post("/me/mot-de-passe", response_model=UtilisateurRead)
def change_password(
    payload: MotDePasseUpdate, user: CurrentUser, session: DbSession
) -> Utilisateur:
    """Change le mot de passe du compte connecté (l'ancien est exigé).

    ``403`` si l'ancien mot de passe est faux, ``422`` s'il est trop court ou
    identique à l'ancien — messages affichés tels quels par l'interface.
    """
    return UtilisateurService(session, actor_id=str(user.id)).changer_mot_de_passe(
        user.id,
        ancien_mot_de_passe=payload.ancien_mot_de_passe,
        nouveau_mot_de_passe=payload.nouveau_mot_de_passe,
    )


@router.get("/utilisateurs/demandes", response_model=list[UtilisateurRead])
def lister_demandes(
    admin: AdminUser,
    session: DbSession,
) -> list[Utilisateur]:
    """File d'attente des demandes d'inscription (comptes en attente)."""
    return UtilisateurService(session).lister_demandes()


@router.post("/utilisateurs/{user_id}/activation", response_model=UtilisateurRead)
def activer(
    user_id: UUID,
    admin: AdminUser,
    session: DbSession,
    payload: ActivationCreate | None = None,
) -> Utilisateur:
    """Valide une demande d'inscription (ou réactive un compte suspendu).

    Le rôle de compte se choisit à la validation : ``collaborateur`` (équipe
    interne, défaut) ou ``formateur`` (accès limité aux missions/sessions où il
    est affecté). Le corps est facultatif pour compatibilité.
    """
    return UtilisateurService(session, actor_id=str(admin.id)).activer(
        user_id,
        admin_id=admin.id,
        role=payload.role if payload is not None else None,
    )


@router.post("/utilisateurs/{user_id}/refus", status_code=status.HTTP_204_NO_CONTENT)
def refuser(
    user_id: UUID,
    admin: AdminUser,
    session: DbSession,
) -> None:
    """Refuse une demande d'inscription : la demande est retirée (audit conservé)."""
    UtilisateurService(session, actor_id=str(admin.id)).refuser(user_id)


@router.post("/utilisateurs/{user_id}/suspension", response_model=UtilisateurRead)
def suspendre(
    user_id: UUID,
    admin: AdminUser,
    session: DbSession,
) -> Utilisateur:
    return UtilisateurService(session, actor_id=str(admin.id)).suspendre(user_id)


# --- Gestion des comptes existants (page Utilisateurs, administrateur) -------


@router.get("/utilisateurs", response_model=list[UtilisateurRead])
def lister_comptes(
    admin: AdminUser,
    session: DbSession,
    recherche: str | None = None,
) -> list[Utilisateur]:
    """Liste des comptes gérables (hors demandes en attente), recherche optionnelle.

    ``?recherche=`` filtre en insensible à la casse sur email, nom et prénom —
    le filtre est appliqué côté service, la pagination suivra le besoin réel
    (aucune exigence validée aujourd'hui).
    """
    return UtilisateurService(session, actor_id=str(admin.id)).lister_comptes(
        recherche=recherche
    )


@router.patch("/utilisateurs/{user_id}/role", response_model=UtilisateurRead)
def changer_role(
    user_id: UUID,
    payload: RoleUpdate,
    admin: AdminUser,
    session: DbSession,
) -> Utilisateur:
    """Change le rôle d'un compte (``collaborateur`` ou ``formateur``).

    Garde-fou service : un administrateur ne change pas son propre rôle.
    """
    return UtilisateurService(session, actor_id=str(admin.id)).changer_role(
        user_id, role=payload.role, admin_id=admin.id
    )


@router.delete("/utilisateurs/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def supprimer_compte(
    user_id: UUID,
    admin: AdminUser,
    session: DbSession,
) -> None:
    """Supprime définitivement un compte.

    Garde-fous service : jamais son propre compte, jamais le dernier
    administrateur actif. L'audit conserve l'identité du compte supprimé.
    """
    UtilisateurService(session, actor_id=str(admin.id)).supprimer(
        user_id, admin_id=admin.id
    )


@router.post(
    "/utilisateurs/{user_id}/reinitialisation-mot-de-passe",
    response_model=UtilisateurRead,
)
def reinitialiser_mot_de_passe(
    user_id: UUID,
    payload: ReinitialisationMotDePasseCreate,
    admin: AdminUser,
    session: DbSession,
) -> Utilisateur:
    """Réinitialise le mot de passe d'un compte (mot de passe choisi par l'admin).

    La valeur n'est jamais renvoyée par l'API ni tracée (AGENTS.md §9) : la
    réponse est la fiche du compte, comme après toute autre mutation.
    """
    return UtilisateurService(session, actor_id=str(admin.id)).reinitialiser_mot_de_passe(
        user_id, admin_id=admin.id, nouveau_mot_de_passe=payload.nouveau_mot_de_passe
    )
