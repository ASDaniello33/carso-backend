"""Événements notifiés — liste validée par CARSO (incrément 19).

Chaque fonction ci-dessous est appelée **dans le service métier** qui porte
l'écriture (AGENTS.md §2.5), dans la transaction courante. La liste des
événements notifiés est figée :

- inscriptions : demande reçue (→ admins), validée, refusée, suspension ;
- offres : soumises en revue (→ admins), décision de publication ;
- documents : soumis à approbation (→ admins), approbation/rejet ;
- missions : créée (→ admins) ;
- affectations : proposée à une personne (→ la personne), confirmée ;
- sessions : planifiée, confirmée, annulée (→ l'équipe de la mission).

Toute notification vise des **personnes** (admins, créateur, affecté) —
jamais « tous les utilisateurs ».
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.application.services.notification_service import NotificationService
from app.domain.enums import RoleUtilisateur, StatutUtilisateur
from app.domain.identity import Utilisateur

_ENTITE_COMPTE = "utilisateurs"


def ids_administrateurs(session: Session) -> list[UUID]:
    """Comptes administrateurs actifs — destinataires des demandes de décision."""
    return list(
        session.scalars(
            select(Utilisateur.id).where(
                Utilisateur.role == RoleUtilisateur.ADMINISTRATEUR.value,
                Utilisateur.statut == StatutUtilisateur.ACTIF.value,
            )
        )
    )


def _service(session: Session) -> NotificationService:
    return NotificationService(session)


# --- Comptes ---------------------------------------------------------------------


def notifier_inscription_recue(session: Session, user: Utilisateur) -> None:
    _service(session).emettre(
        destinataires=ids_administrateurs(session),
        type_notification="inscription_recue",
        titre="Nouvelle demande d'inscription",
        corps=f"{user.prenom} {user.nom} ({user.email}) attend une décision.",
        objet_type="utilisateur",
        objet_id=user.id,
        href="/utilisateurs",
    )


def notifier_compte_active(session: Session, user: Utilisateur) -> None:
    _service(session).emettre(
        destinataires=[user.id],
        type_notification="compte_active",
        titre="Votre compte a été validé",
        corps="Vous pouvez désormais vous connecter à CARSO AI.",
        objet_type="utilisateur",
        objet_id=user.id,
        href="/dashboard",
    )


def notifier_compte_refuse(session: Session, email: str) -> None:
    """La demande est supprimée : pas de destinataire en base — trace d'audit seulement.

    (Fonction volontairement sans écriture : le refus ne peut notifier personne,
    le compte n'existe plus. Gardée pour documenter la décision.)
    """


def notifier_compte_suspendu(session: Session, user: Utilisateur) -> None:
    _service(session).emettre(
        destinataires=[user.id],
        type_notification="compte_suspendu",
        titre="Votre compte a été suspendu",
        corps="Contactez un administrateur pour rétablir l'accès.",
        objet_type="utilisateur",
        objet_id=user.id,
        href="/profil",
    )


# --- Offres ----------------------------------------------------------------------


def notifier_offre_a_valider(session: Session, offre: object) -> None:
    _service(session).emettre(
        destinataires=ids_administrateurs(session),
        type_notification="offre_a_valider",
        titre="Offre en attente de validation",
        corps=f"L'offre « {offre.reference} — {offre.titre} » attend une décision.",
        objet_type="offre",
        objet_id=offre.id,
        href=f"/offres/{offre.id}",
    )


def notifier_offre_decision(session: Session, offre: object, decision: str) -> None:
    """Décision de publication : notifie l'auteur du document source si connu.

    Le service offre ne connaît pas le créateur : on notifie les admins comme
    témoin de la décision (l'auteur utilise le dashboard pour le suivi). Cette
    fonction reste volontairement simple et ne vise que les comptes réels.
    """
    _service(session).emettre(
        destinataires=ids_administrateurs(session),
        type_notification="offre_decision",
        titre=f"Offre {decision}",
        corps=f"L'offre « {offre.reference} — {offre.titre} » a été {decision}.",
        objet_type="offre",
        objet_id=offre.id,
        href=f"/offres/{offre.id}",
    )


# --- Documents -------------------------------------------------------------------


def notifier_document_a_approuver(session: Session, document: object) -> None:
    _service(session).emettre(
        destinataires=ids_administrateurs(session),
        type_notification="document_a_approuver",
        titre="Document en attente d'approbation",
        corps=f"Le document « {document.nom} » attend une approbation.",
        objet_type="document",
        objet_id=document.id,
        href=f"/documents/{document.id}",
    )


def notifier_document_approuve(session: Session, document: object, decision: str) -> None:
    _service(session).emettre(
        destinataires=ids_administrateurs(session),
        type_notification="document_decision",
        titre=f"Document {decision}",
        corps=f"Le document « {document.nom} » a été {decision}.",
        objet_type="document",
        objet_id=document.id,
        href=f"/documents/{document.id}",
    )


# --- Missions --------------------------------------------------------------------


def notifier_mission_creee(session: Session, mission: object) -> None:
    _service(session).emettre(
        destinataires=ids_administrateurs(session),
        type_notification="mission_creee",
        titre="Nouvelle mission créée",
        corps=f"La mission « {mission.reference} — {mission.titre} » a été créée.",
        objet_type="mission",
        objet_id=mission.id,
        href=f"/missions/{mission.id}",
    )


# --- Affectations ----------------------------------------------------------------


def notifier_affectation_proposee(
    session: Session, affectation: object, mission_titre: str, role: str
) -> None:
    """Proposition d'affectation : notifie la personne concernée.

    ``affectation.equipe.email`` est facultatif (colonne nullable) et **ne
    correspond pas à un compte utilisateur** (l'équipe est un vivier) : la
    notification ne peut viser qu'un compte réel — on notifie les admins,
    qui voient la proposition à traiter.
    """
    _service(session).emettre(
        destinataires=ids_administrateurs(session),
        type_notification="affectation_proposee",
        titre="Proposition d'affectation à traiter",
        corps=(
            f"{affectation.equipe.prenom} {affectation.equipe.nom} proposé comme "
            f"{role} sur « {mission_titre} »."
        ),
        objet_type="mission",
        objet_id=affectation.mission_id,
        href=f"/missions/{affectation.mission_id}",
    )


def notifier_affectation_confirmee(
    session: Session, affectation: object, mission_titre: str, role: str
) -> None:
    _service(session).emettre(
        destinataires=ids_administrateurs(session),
        type_notification="affectation_confirmee",
        titre="Affectation confirmée",
        corps=(
            f"{affectation.equipe.prenom} {affectation.equipe.nom} est confirmé "
            f"comme {role} sur « {mission_titre} »."
        ),
        objet_type="mission",
        objet_id=affectation.mission_id,
        href=f"/missions/{affectation.mission_id}",
    )


# --- Sessions --------------------------------------------------------------------


def notifier_session_changement(
    session: Session,
    session_formation: object,
    mission_titre: str,
    evenement: str,
) -> None:
    """Planification / confirmation / annulation : notifie l'équipe de la mission.

    L'équipe est connue via les affectations (vivier, pas comptes) : on notifie
    les administrateurs, coordinateurs de l'exécution — décision documentée.
    """
    libelles = {
        "planifiee": "Session planifiée",
        "confirmee": "Session confirmée",
        "annulee": "Session annulée",
        "realisee": "Session réalisée",
    }
    _service(session).emettre(
        destinataires=ids_administrateurs(session),
        type_notification=f"session_{evenement}",
        titre=libelles.get(evenement, "Session mise à jour"),
        corps=(
            f"Session « {session_formation.theme or 'sans thème'} » ({evenement}) "
            f"sur la mission « {mission_titre} »."
        ),
        objet_type="session",
        objet_id=session_formation.id,
        href="/sessions",
    )


__all__ = [
    "ids_administrateurs",
    "notifier_affectation_confirmee",
    "notifier_affectation_proposee",
    "notifier_compte_active",
    "notifier_compte_refuse",
    "notifier_compte_suspendu",
    "notifier_document_a_approuver",
    "notifier_document_approuve",
    "notifier_inscription_recue",
    "notifier_mission_creee",
    "notifier_offre_a_valider",
    "notifier_offre_decision",
    "notifier_session_changement",
]
