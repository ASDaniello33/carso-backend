"""Routes partagées — impact et suppression cascadée.

Une seule paire de contrôleurs pour neuf ressources. Dupliquer neuf fois le même
code multiplierait les endroits où l'autorisation, la validation du motif et le
format de réponse peuvent diverger ; ici, la règle de suppression vit dans
``ServiceSuppression`` et la couche API ne fait que la brancher.

``GET  /<ressource>/{identifiant}/impact`` : ce qui disparaîtrait (lecture seule)
``DELETE /<ressource>/{identifiant}``      : exécute la cascade, renvoie l'inventaire

Depuis la règle validée le 23/09, la cascade **conserve les documents** : leur
fiche reste (statut « Supprimé ») et seul leur fichier quitte le stockage. Les
documents ne figurent donc plus dans ``dependances`` mais dans ``conservees``.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter

from app.api.deps import CurrentUser, DbSession
from app.api.schemas import ImpactSuppressionRead, RapportSuppressionRead, SuppressionRequete
from app.application.services.suppression_service import (
    ImpactSuppression,
    RapportSuppression,
    ServiceSuppression,
)
from app.application.trace import Decision


def _impact_reponse(impact: ImpactSuppression) -> ImpactSuppressionRead:
    """Projection d'un impact métier en schéma d'API."""
    return ImpactSuppressionRead(
        type_entite=impact.type_entite,
        libelle=impact.libelle,
        identifiant=impact.identifiant,
        reference=impact.reference,
        dependances=impact.dependances,
        conservees=impact.conservees,
        documents_officiels=list(impact.documents_officiels),
        fichiers=impact.fichiers,
        bloquant=impact.bloquant,
        message=impact.message,
    )


def _rapport_reponse(rapport: RapportSuppression) -> RapportSuppressionRead:
    """Projection d'un rapport métier en schéma d'API."""
    return RapportSuppressionRead(
        type_entite=rapport.type_entite,
        libelle=rapport.libelle,
        identifiant=rapport.identifiant,
        reference=rapport.reference,
        dependances=rapport.dependances,
        conservees=rapport.conservees,
        fichiers_supprimes=rapport.fichiers_supprimes,
    )


def enregistrer_suppression(router: APIRouter, type_entite: str) -> None:
    """Ajoute les deux routes de suppression d'une ressource au routeur donné.

    Args:
        router: routeur de la ressource (``/offres``, ``/missions``, …).
        type_entite: clé de l'inventaire (``"offres"``, ``"missions"``, …),
            telle que connue de ``ServiceSuppression``.
    """

    @router.get("/{identifiant}/impact", response_model=ImpactSuppressionRead)
    def impact_suppression(identifiant: UUID, session: DbSession) -> ImpactSuppressionRead:
        """Dépendances d'une suppression, sans rien modifier.

        L'interface s'en sert pour annoncer ce qui disparaîtra avant de demander
        confirmation — jamais pour deviner.
        """
        return _impact_reponse(ServiceSuppression(session).impact(type_entite, identifiant))

    @router.delete("/{identifiant}", response_model=RapportSuppressionRead)
    def supprimer(
        identifiant: UUID,
        payload: SuppressionRequete,
        user: CurrentUser,
        session: DbSession,
    ) -> RapportSuppressionRead:
        """Supprime l'élément et son sous-arbre, en conservant l'inventaire.

        L'auteur vient du JWT (jamais du corps de la requête). Le motif est
        facultatif ici mais part dans l'``AuditEvent`` : c'est la seule trace
        conservée de ce qui a été détruit.
        """
        rapport = ServiceSuppression(session, actor_id=str(user.id)).supprimer(
            type_entite,
            identifiant,
            Decision(decided_by=str(user.id), reason=payload.reason),
        )
        return _rapport_reponse(rapport)


__all__ = ["enregistrer_suppression"]
