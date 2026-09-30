"""Service comptes — inscription pending, activation admin, login, profil.

Le titulaire d'un compte gère lui-même son identité et son mot de passe
(``modifier_profil``, ``changer_mot_de_passe``) : l'ancien mot de passe est exigé
comme preuve de maîtrise du compte, et aucune valeur de mot de passe n'entre dans
un ``AuditEvent``, un log ou une réponse d'API (AGENTS.md §9).
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.application.security import (
    decoder_jeton,
    emettre_jeton,
    hasher_mot_de_passe,
    verifier_mot_de_passe,
)
from app.application.services import notifications_evenements as notifications
from app.application.trace import TraceContext
from app.core.config import Settings, get_settings
from app.core.errors import (
    AuthenticationError,
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
    ValidationError,
)
from app.domain.enums import ActionAudit, ActorType, RoleUtilisateur, StatutUtilisateur
from app.domain.execution import Equipe
from app.domain.identity import Utilisateur
from app.domain.state_machines import validate_transition
from app.infrastructure.repositories import UtilisateurRepository

_ENTITY = "utilisateur"

#: Rôles attribuables par un administrateur sur un compte existant. Le rôle
#: ``en_attente`` est le marqueur des inscriptions non validées et
#: ``administrateur`` ne s'attribue pas depuis la liste des comptes : une
#: élévation en admin est une décision de déploiement (bootstrap) ou de
#: création directe, pas un geste ordinaire de la page Utilisateurs.
ROLES_GERABLES: tuple[str, ...] = (
    RoleUtilisateur.COLLABORATEUR.value,
    RoleUtilisateur.FORMATEUR.value,
)


class UtilisateurService:
    def __init__(self, session: Session, actor_id: str | None = None) -> None:
        self._session = session
        self.utilisateurs = UtilisateurRepository(session)
        self._trace = TraceContext(session, actor_id=actor_id)

    def inscrire(self, *, email: str, nom: str, prenom: str, mot_de_passe: str) -> Utilisateur:
        """Inscription : réservée aux personnes du vivier (décision validée).

        L'email doit correspondre à une personne **active** de la page Équipes
        (hors admin, créé au bootstrap). Nom, prénom et rôle du compte sont
        repris de l'équipe — jamais de la saisie : aucun doublon possible, la
        fiche vivier reste la source de vérité.
        """
        email_n = email.lower().strip()
        if self.utilisateurs.get_by_email(email_n) is not None:
            raise ConflictError("Un compte existe déjà pour cet email")
        equipe = self._session.scalar(
            select(Equipe).where(func.lower(Equipe.email) == email_n)
        )
        if equipe is None:
            raise ValidationError(
                "Aucun membre de l'équipe ne porte cet email : "
                "faites-vous enregistrer dans la page Équipes avant de créer un compte"
            )
        from app.domain.enums import StatutEquipe

        if (equipe.statut or StatutEquipe.ACTIF.value) != StatutEquipe.ACTIF.value:
            raise ValidationError("Cette personne de l'équipe n'est plus active")
        # Rôle de compte porté par l'équipe (défaut : collaborateur).
        roles_valides = (RoleUtilisateur.COLLABORATEUR.value, RoleUtilisateur.FORMATEUR.value)
        role_compte = equipe.role_compte or RoleUtilisateur.COLLABORATEUR.value
        if role_compte not in roles_valides:
            role_compte = RoleUtilisateur.COLLABORATEUR.value
        user = Utilisateur(
            email=email_n,
            nom=equipe.nom,
            prenom=equipe.prenom,
            password_hash=hasher_mot_de_passe(mot_de_passe),
            role=RoleUtilisateur.EN_ATTENTE.value,
            statut=StatutUtilisateur.PENDING.value,
            equipe_id=equipe.id,
        )
        self._role_souhaite = role_compte
        self.utilisateurs.add(user)
        self._session.flush()
        self._trace.record_event(
            actor_type=ActorType.HUMAIN.value,
            action=ActionAudit.UTILISATEUR_INSCRIT.value,
            entity_type=_ENTITY,
            entity_id=user.id,
            after={"email": email_n, "statut": user.statut},
        )
        notifications.notifier_inscription_recue(self._session, user)
        return user

    def authentifier(
        self, *, email: str, mot_de_passe: str, settings: Settings | None = None
    ) -> str:
        user = self.utilisateurs.get_by_email(email.lower().strip())
        if user is None or not verifier_mot_de_passe(mot_de_passe, user.password_hash):
            raise PermissionDeniedError("Identifiants invalides")
        if user.statut == StatutUtilisateur.PENDING.value:
            raise PermissionDeniedError("Compte en attente de validation administrateur")
        if user.statut != StatutUtilisateur.ACTIF.value:
            raise PermissionDeniedError("Compte suspendu")
        return emettre_jeton(user.id, user.role, user.email, settings or get_settings())

    def activer(
        self,
        user_id: UUID,
        *,
        admin_id: UUID,
        role: str | None = None,
    ) -> Utilisateur:
        """Valide une demande d'inscription (ou réactive un compte suspendu).

        Args:
            user_id: compte cible.
            admin_id: administrateur décideur (traçabilité ``approved_by``).
            role: rôle de compte attribué à la validation. Sans rôle explicite,
                un compte en attente devient ``collaborateur`` (équipe interne) ;
                le choix validé est ``collaborateur`` ou ``formateur`` — jamais
                ``administrateur`` ni ``en_attente``.
        """
        user = self._get(user_id)
        validate_transition(_ENTITY, user.statut, StatutUtilisateur.ACTIF.value)
        etait_suspendu = user.statut == StatutUtilisateur.SUSPENDU.value
        user.statut = StatutUtilisateur.ACTIF.value
        if user.role == RoleUtilisateur.EN_ATTENTE.value:
            # Rôle porté par l'équipe liée (décision « rôle équipe = rôle
            # utilisateur ») ; défaut : collaborateur.
            if user.equipe_id is not None:
                equipe = self._session.get(Equipe, user.equipe_id)
                roles_valides = (
                    RoleUtilisateur.COLLABORATEUR.value,
                    RoleUtilisateur.FORMATEUR.value,
                )
                role_equipe = (
                    (equipe.role_compte if equipe else None) or RoleUtilisateur.COLLABORATEUR.value
                )
                user.role = (
                    role_equipe
                    if role_equipe in roles_valides
                    else RoleUtilisateur.COLLABORATEUR.value
                )
            else:
                user.role = RoleUtilisateur.COLLABORATEUR.value
        if role is not None:
            if user.statut != StatutUtilisateur.ACTIF.value:
                raise ValidationError("Le rôle se règle sur un compte actif")
            if role not in (RoleUtilisateur.COLLABORATEUR.value, RoleUtilisateur.FORMATEUR.value):
                raise ValidationError(
                    "Rôle de compte invalide à la validation (choix : collaborateur, formateur)"
                )
            user.role = role
        user.approved_at = datetime.now(UTC)
        user.approved_by = admin_id
        self._trace.record_event(
            actor_type=ActorType.HUMAIN.value,
            action=(
                ActionAudit.UTILISATEUR_REACTIVE.value
                if etait_suspendu
                else ActionAudit.UTILISATEUR_ACTIVE.value
            ),
            entity_type=_ENTITY,
            entity_id=user.id,
            after={"statut": user.statut, "role": user.role},
        )
        notifications.notifier_compte_active(self._session, user)
        return user

    def refuser(self, user_id: UUID) -> Utilisateur:
        """Refuse une demande d'inscription : le compte pending est retiré.

        La demande n'a produit aucune donnée métier (aucune FK pointe vers un
        compte en attente) : la suppression est propre et la demande disparaît
        de la file. L'email reste libre pour une nouvelle inscription corrigée.
        Traçabilité : l'événement d'audit porte la demande refusée (email,
        nom, prénom) — la fiche disparaissant, c'est la seule trace.
        """
        user = self._get(user_id)
        if user.role != RoleUtilisateur.EN_ATTENTE.value:
            raise ValidationError(
                "Seule une demande d'inscription en attente peut être refusée"
            )
        if user.statut != StatutUtilisateur.PENDING.value:
            raise ValidationError(
                "Seule une demande d'inscription en attente peut être refusée"
            )
        self._trace.record_event(
            actor_type=ActorType.HUMAIN.value,
            action=ActionAudit.UTILISATEUR_REFUSE.value,
            entity_type=_ENTITY,
            entity_id=user.id,
            after={
                "email": user.email,
                "nom": user.nom,
                "prenom": user.prenom,
                "decision": "refusee",
            },
        )
        self.utilisateurs.delete(user)
        self._session.flush()
        return user

    def lister_demandes(self) -> list[Utilisateur]:
        """File d'attente : toutes les demandes d'inscription en attente."""
        return list(
            self._session.scalars(
                select(Utilisateur)
                .where(
                    Utilisateur.role == RoleUtilisateur.EN_ATTENTE.value,
                    Utilisateur.statut == StatutUtilisateur.PENDING.value,
                )
                .order_by(Utilisateur.created_at.asc())
            )
        )

    def suspendre(self, user_id: UUID) -> Utilisateur:
        user = self._get(user_id)
        validate_transition(_ENTITY, user.statut, StatutUtilisateur.SUSPENDU.value)
        user.statut = StatutUtilisateur.SUSPENDU.value
        self._trace.record_event(
            actor_type=ActorType.HUMAIN.value,
            action=ActionAudit.UTILISATEUR_SUSPENDU.value,
            entity_type=_ENTITY,
            entity_id=user.id,
            after={"statut": user.statut},
        )
        notifications.notifier_compte_suspendu(self._session, user)
        return user

    # --- Gestion des comptes existants (page Utilisateurs, administrateur) ---

    def lister_comptes(self, *, recherche: str | None = None) -> list[Utilisateur]:
        """Tous les comptes gérables (hors demandes en attente), recherche optionnelle.

        La file d'attente des inscriptions a sa propre liste
        (``lister_demandes``) : un compte ``en_attente``/``pending`` n'apparaît
        pas ici, il se valide ou se refuse depuis la file.

        Args:
            recherche: texte libre insensible à la casse cherché dans l'email,
                le nom ou le prénom. ``None`` ou blanc ⇒ tous les comptes.
        """
        stmt = (
            select(Utilisateur)
            .where(
                Utilisateur.role != RoleUtilisateur.EN_ATTENTE.value,
                Utilisateur.statut != StatutUtilisateur.PENDING.value,
            )
            .order_by(Utilisateur.created_at.desc())
        )
        terme = (recherche or "").strip().lower()
        if terme:
            motif = f"%{terme}%"
            stmt = stmt.where(
                (Utilisateur.email.ilike(motif))
                | (Utilisateur.nom.ilike(motif))
                | (Utilisateur.prenom.ilike(motif))
            )
        return list(self._session.scalars(stmt))

    def changer_role(self, user_id: UUID, *, role: str, admin_id: UUID) -> Utilisateur:
        """Change le rôle d'un compte existant (collaborateur ou formateur).

        Un administrateur ne change pas son propre rôle : même garde-fou que la
        suppression (il ne s'affaiblit pas lui-même depuis cette page).

        Raises:
            NotFoundError: compte inexistant.
            ValidationError: rôle hors choix validé, compte pas encore actif,
                ou auto-modification.
        """
        user = self._get(user_id)
        if user.id == admin_id:
            raise ValidationError(
                "Vous ne pouvez pas modifier le rôle de votre propre compte"
            )
        if role not in ROLES_GERABLES:
            raise ValidationError(
                "Rôle de compte invalide (choix : collaborateur, formateur)"
            )
        if user.role == RoleUtilisateur.EN_ATTENTE.value:
            raise ValidationError(
                "Ce compte est une demande en attente : validez-la d'abord depuis la file"
            )
        avant = user.role
        user.role = role
        self._trace.record_event(
            actor_type=ActorType.HUMAIN.value,
            action=ActionAudit.UTILISATEUR_MODIFIE.value,
            entity_type=_ENTITY,
            entity_id=user.id,
            after={"role": user.role},
            event_metadata={"avant": {"role": avant}},
        )
        return user

    def supprimer(self, user_id: UUID, *, admin_id: UUID) -> None:
        """Supprime définitivement un compte, avec garde-fous explicites.

        Garde-fous (hypothèses validées par défaut, à confirmer avec CARSO) :

        - un administrateur ne supprime pas son propre compte depuis cette
          page (risque de verrouillage immédiat) ;
        - le compte ne peut pas être le dernier administrateur actif.

        Aucune FK métier ne pointe vers ``utilisateurs`` : la suppression
        n'oriente aucune donnée métier. L'audit conserve l'identité du compte
        supprimé (la fiche disparaissant, c'est la seule trace).

        Raises:
            NotFoundError: compte inexistant.
            ValidationError: auto-suppression, ou dernier administrateur.
        """
        user = self._get(user_id)
        if user.id == admin_id:
            raise ValidationError(
                "Vous ne pouvez pas supprimer votre propre compte"
            )
        if user.role == RoleUtilisateur.ADMINISTRATEUR.value:
            actifs = self._session.scalars(
                select(Utilisateur).where(
                    Utilisateur.role == RoleUtilisateur.ADMINISTRATEUR.value,
                    Utilisateur.statut == StatutUtilisateur.ACTIF.value,
                )
            )
            if len([a for a in actifs if a.id != user_id]) < 1:
                raise ValidationError(
                    "Impossible de supprimer le dernier compte administrateur"
                )
        identite = {
            "email": user.email,
            "nom": user.nom,
            "prenom": user.prenom,
            "role": user.role,
            "statut": user.statut,
        }
        self._trace.record_event(
            actor_type=ActorType.HUMAIN.value,
            action=ActionAudit.UTILISATEUR_SUPPRIME.value,
            entity_type=_ENTITY,
            entity_id=user.id,
            after={**identite, "decision": "supprime"},
        )
        self.utilisateurs.delete(user)
        self._session.flush()

    def reinitialiser_mot_de_passe(
        self, user_id: UUID, *, admin_id: UUID, nouveau_mot_de_passe: str
    ) -> Utilisateur:
        """Réinitialise le mot de passe d'un compte à la demande de l'admin.

        Le nouveau mot de passe est choisi par l'administrateur et communiqué
        au titulaire par un canal extérieur au système ; il n'est jamais
        renvoyé par l'API, journalisé ni tracé dans l'audit (AGENTS.md §9).

        Raises:
            NotFoundError: compte inexistant.
            ValidationError: mot de passe trop court, ou identique à l'actuel.
        """
        user = self._get(user_id)
        if len(nouveau_mot_de_passe) < 8:
            raise ValidationError(
                "Le nouveau mot de passe doit compter au moins 8 caractères"
            )
        if verifier_mot_de_passe(nouveau_mot_de_passe, user.password_hash):
            raise ValidationError(
                "Le nouveau mot de passe doit être différent du mot de passe actuel"
            )
        user.password_hash = hasher_mot_de_passe(nouveau_mot_de_passe)
        self._trace.record_event(
            actor_type=ActorType.HUMAIN.value,
            action=ActionAudit.UTILISATEUR_MOT_DE_PASSE_REINITIALISE.value,
            entity_type=_ENTITY,
            entity_id=user.id,
            after={"champ": "password_hash", "initiateur": "administrateur"},
        )
        return user

    def definir_photo_profil(self, user_id: UUID, *, chemin: str) -> Utilisateur:
        """Pose (ou remplace) la photo de profil du compte (incrément 24)."""
        user = self._get(user_id)
        ancienne = user.photo_profil_chemin
        user.photo_profil_chemin = chemin
        self._trace.record_event(
            actor_type=ActorType.HUMAIN.value,
            action=ActionAudit.UTILISATEUR_MODIFIE.value,
            entity_type=_ENTITY,
            entity_id=user.id,
            after={"champ": "photo_profil_chemin"},
        )
        if ancienne and ancienne != chemin:
            self._supprimer_fichier_photo(ancienne)
        return user

    def supprimer_photo_profil(self, user_id: UUID) -> Utilisateur:
        """Retire la photo de profil : fichier supprimé, référence vidée."""
        user = self._get(user_id)
        ancienne = user.photo_profil_chemin
        user.photo_profil_chemin = None
        if ancienne:
            self._supprimer_fichier_photo(ancienne)
        self._trace.record_event(
            actor_type=ActorType.HUMAIN.value,
            action=ActionAudit.UTILISATEUR_MODIFIE.value,
            entity_type=_ENTITY,
            entity_id=user.id,
            after={"champ": "photo_profil_chemin", "valeur": None},
        )
        return user

    def _supprimer_fichier_photo(self, chemin_logique: str) -> None:
        """Suppression disque best-effort : l'échec ne bloque pas la mutation."""
        import logging

        from app.core.config import get_settings
        from app.documents.storage import LocalDocumentStorage

        try:
            LocalDocumentStorage(
                get_settings().storage_root, max_bytes=get_settings().max_upload_bytes
            ).remove(chemin_logique)
        except (FileNotFoundError, OSError) as exc:
            logging.getLogger(__name__).warning(
                "Photo de profil %s déjà absente ou non supprimable : %s", chemin_logique, exc
            )

    def modifier_profil(self, user_id: UUID, *, nom: str, prenom: str) -> Utilisateur:
        """Modifie l'identité du titulaire du compte (nom, prénom).

        L'email ne se change pas ici : c'est l'identifiant de connexion, une
        décision d'administration (un doublon d'email doit être refusé, pas
        contourné depuis une fiche personnelle).

        Raises:
            NotFoundError: compte inexistant.
            ValidationError: nom ou prénom vide après nettoyage.
        """
        user = self._get(user_id)
        avant = {"nom": user.nom, "prenom": user.prenom}
        nom_propre = nom.strip()
        prenom_propre = prenom.strip()
        if not nom_propre or not prenom_propre:
            raise ValidationError("Le nom et le prénom sont obligatoires")

        user.nom = nom_propre
        user.prenom = prenom_propre
        self._trace.record_event(
            actor_type=ActorType.HUMAIN.value,
            action=ActionAudit.UTILISATEUR_MODIFIE.value,
            entity_type=_ENTITY,
            entity_id=user.id,
            after={"nom": user.nom, "prenom": user.prenom},
            event_metadata={"avant": avant},
        )
        return user

    def changer_mot_de_passe(
        self, user_id: UUID, *, ancien_mot_de_passe: str, nouveau_mot_de_passe: str
    ) -> Utilisateur:
        """Remplace le mot de passe, l'ancien faisant office de preuve.

        Aucune valeur ni aucun hash n'est écrit dans l'audit : seule la
        modification est consignée. Le JWT déjà émis reste valide jusqu'à son
        expiration — limitation assumée (pas de révocation côté serveur).

        Raises:
            NotFoundError: compte inexistant.
            PermissionDeniedError: ancien mot de passe incorrect.
            ValidationError: nouveau mot de passe identique à l'ancien.
        """
        user = self._get(user_id)
        if not verifier_mot_de_passe(ancien_mot_de_passe, user.password_hash):
            raise PermissionDeniedError("Mot de passe actuel incorrect")
        if verifier_mot_de_passe(nouveau_mot_de_passe, user.password_hash):
            raise ValidationError(
                "Le nouveau mot de passe doit être différent du mot de passe actuel"
            )

        user.password_hash = hasher_mot_de_passe(nouveau_mot_de_passe)
        self._trace.record_event(
            actor_type=ActorType.HUMAIN.value,
            action=ActionAudit.UTILISATEUR_MOT_DE_PASSE_CHANGE.value,
            entity_type=_ENTITY,
            entity_id=user.id,
            after={"champ": "password_hash"},
        )
        return user

    def obtenir(self, user_id: UUID) -> Utilisateur:
        return self._get(user_id)

    def depuis_jeton(self, jeton: str, settings: Settings | None = None) -> Utilisateur:
        payload = decoder_jeton(jeton, settings or get_settings())
        user = self._get(UUID(str(payload["sub"])))
        if user.statut != StatutUtilisateur.ACTIF.value:
            raise AuthenticationError("Compte inactif")
        return user

    def assurer_bootstrap_admin(self, settings: Settings | None = None) -> Utilisateur | None:
        """Crée le premier admin si BOOTSTRAP_ADMIN_* est défini et aucun admin n'existe."""
        cfg = settings or get_settings()
        if not cfg.bootstrap_admin_email or not cfg.bootstrap_admin_password:
            return None
        email = cfg.bootstrap_admin_email.lower().strip()
        existant = self.utilisateurs.get_by_email(email)
        if existant is not None:
            return existant
        user = Utilisateur(
            email=email,
            nom="Admin",
            prenom="Bootstrap",
            password_hash=hasher_mot_de_passe(
                cfg.bootstrap_admin_password.get_secret_value()
            ),
            role=RoleUtilisateur.ADMINISTRATEUR.value,
            statut=StatutUtilisateur.ACTIF.value,
            approved_at=datetime.now(UTC),
        )
        self.utilisateurs.add(user)
        self._session.flush()
        return user

    def _get(self, user_id: UUID) -> Utilisateur:
        user = self.utilisateurs.get(user_id)
        if user is None:
            raise NotFoundError(f"Utilisateur {user_id} introuvable")
        return user
