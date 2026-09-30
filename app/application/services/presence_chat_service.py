"""Service — présence des utilisateurs du chat social (incrément 21).

Le statut n'est **pas stocké** : il est calculé à la lecture depuis
``Utilisateur.derniere_activite_at`` — aucune donnée à expirer, aucun état à
maintenir, plusieurs processus serveur donnent le même résultat.

Seuils (décision produit) : ``en_ligne`` < 2 minutes, ``absent`` < 15 minutes
(affiché « En ligne il y a N min »), ``hors_ligne`` au-delà.

Distinct de ``presence_service`` (pointage des bénéficiaires aux sessions) :
deux notions de « présence » coexistent dans le domaine CARSO.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.identity import Utilisateur

SEUIL_EN_LIGNE = timedelta(minutes=2)
SEUIL_ABSENT = timedelta(minutes=15)


def statut_de(derniere_activite_at: datetime | None, *, maintenant: datetime | None = None) -> str:
    """Statut de présence calculé : en_ligne | absent | hors_ligne."""
    if derniere_activite_at is None:
        return "hors_ligne"
    instant = maintenant or datetime.now(UTC)
    ecart = instant - derniere_activite_at
    if ecart <= SEUIL_EN_LIGNE:
        return "en_ligne"
    if ecart <= SEUIL_ABSENT:
        return "absent"
    return "hors_ligne"


class PresenceChatService:
    """Heartbeat et lecture des statuts de présence du chat social."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def battement(self, utilisateur_id: UUID) -> datetime | None:
        """Enregistre le dernier signal de vie de l'utilisateur (appelé par le frontend).

        Tolérant : un compte introuvable (utilisateur technique du mode lab,
        compte supprimé entre deux battements) n'est **pas** une erreur — un
        heartbeat qui échouerait en 404 polluerait le polling du frontend.
        Retourne l'instant enregistré, ou ``None`` si rien n'a été écrit.
        """
        utilisateur = self._session.get(Utilisateur, utilisateur_id)
        if utilisateur is None:
            return None
        maintenant = datetime.now(UTC)
        utilisateur.derniere_activite_at = maintenant
        self._session.flush()
        return maintenant

    def statut(self, utilisateur_id: UUID) -> dict[str, object]:
        """Statut d'un utilisateur : état calculé + libellé relatif.

        Raises:
            NotFoundError: compte introuvable.
        """
        utilisateur = self._session.get(Utilisateur, utilisateur_id)
        if utilisateur is None:
            from app.core.errors import NotFoundError

            raise NotFoundError(f"Utilisateur {utilisateur_id} introuvable")
        return _resume(utilisateur)

    def statuts_pour(self, ids: list[UUID]) -> dict[str, dict[str, object]]:
        """Statuts de plusieurs utilisateurs (en-tête de conversation, social)."""
        if not ids:
            return {}
        utilisateurs = list(
            self._session.scalars(select(Utilisateur).where(Utilisateur.id.in_(ids)))
        )
        return {str(u.id): _resume(u) for u in utilisateurs}


def _resume(utilisateur: Utilisateur) -> dict[str, object]:
    maintenant = datetime.now(UTC)
    etat = statut_de(utilisateur.derniere_activite_at, maintenant=maintenant)
    libelle = "Hors ligne"
    if utilisateur.derniere_activite_at is not None and etat == "absent":
        minutes = max(
            1, int((maintenant - utilisateur.derniere_activite_at).total_seconds() // 60)
        )
        libelle = f"En ligne il y a {minutes} min"
    elif etat == "en_ligne":
        libelle = "En ligne"
    return {
        "statut": etat,
        "libelle": libelle,
        "derniere_activite_at": (
            utilisateur.derniere_activite_at.isoformat()
            if utilisateur.derniere_activite_at is not None
            else None
        ),
    }
