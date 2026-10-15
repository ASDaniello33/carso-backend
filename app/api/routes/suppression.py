"""Routes partagées : impact et suppression cascadée.

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

from fastapi import APIRouter, Depends

from app.api.deps import CurrentUser, DbSession
from app.api.permissions import PerimetreDep, exiger_interne
from app.api.schemas import ImpactSuppressionRead, RapportSuppressionRead, SuppressionRequete
from app.application.perimetre import Perimetre
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


def _impact(
    type_entite: str,
    identifiant: UUID,
    session: DbSession,
    perimetre: Perimetre | None,
) -> ImpactSuppressionRead:
    """Dépendances d'une suppression, sans rien modifier (lecture seule)."""
    return _impact_reponse(
        ServiceSuppression(session, perimetre=perimetre).impact(type_entite, identifiant)
    )


def _supprimer(
    type_entite: str,
    identifiant: UUID,
    payload: SuppressionRequete,
    user: CurrentUser,
    session: DbSession,
    perimetre: Perimetre | None,
) -> RapportSuppressionRead:
    """Exécute la suppression et rend son inventaire.

    L'auteur vient du JWT (jamais du corps de la requête). Le motif est
    facultatif ici mais part dans l'``AuditEvent`` : c'est la seule trace
    conservée de ce qui a été détruit.
    """
    rapport = ServiceSuppression(
        session, actor_id=str(user.id), perimetre=perimetre
    ).supprimer(
        type_entite,
        identifiant,
        Decision(decided_by=str(user.id), reason=payload.reason),
    )
    return _rapport_reponse(rapport)


def enregistrer_suppression(
    router: APIRouter,
    type_entite: str,
    *,
    perimetre: bool = False,
) -> None:
    """Ajoute les deux routes de suppression d'une ressource au routeur donné.

    Args:
        router: routeur de la ressource (``/offres``, ``/missions``, …).
        type_entite: clé de l'inventaire (``"offres"``, ``"missions"``, …),
            telle que connue de ``ServiceSuppression``.
        perimetre: ``True`` pour les **documents** : leur suppression est
            *logique* (la fiche reste, le fichier part), un formateur peut donc
            la déclencher **dans son périmètre** — le service répond 404
            au-delà. Toute autre ressource garde la garde d'équipe interne.
    """
    if perimetre:

        @router.get("/{identifiant}/impact", response_model=ImpactSuppressionRead)
        def impact_suppression(
            identifiant: UUID, session: DbSession, filtre: PerimetreDep
        ) -> ImpactSuppressionRead:
            """Ce qui sera retiré, borné au périmètre du compte (404 sinon)."""
            return _impact(type_entite, identifiant, session, filtre)

        @router.delete("/{identifiant}", response_model=RapportSuppressionRead)
        def supprimer(
            identifiant: UUID,
            payload: SuppressionRequete,
            user: CurrentUser,
            session: DbSession,
            filtre: PerimetreDep,
        ) -> RapportSuppressionRead:
            """Suppression logique d'un document de son périmètre."""
            return _supprimer(type_entite, identifiant, payload, user, session, filtre)

        return

    @router.get(
        "/{identifiant}/impact",
        response_model=ImpactSuppressionRead,
        # La suppression est un geste de pilotage : équipe interne uniquement.
        dependencies=[Depends(exiger_interne)],
    )
    def impact_suppression(identifiant: UUID, session: DbSession) -> ImpactSuppressionRead:  # noqa: F811
        """Dépendances d'une suppression, sans rien modifier.

        L'interface s'en sert pour annoncer ce qui disparaîtra avant de demander
        confirmation — jamais pour deviner.
        """
        return _impact(type_entite, identifiant, session, None)

    @router.delete(
        "/{identifiant}",
        response_model=RapportSuppressionRead,
        dependencies=[Depends(exiger_interne)],
    )
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
        return _supprimer(type_entite, identifiant, payload, user, session, None)


__all__ = ["enregistrer_suppression"]
