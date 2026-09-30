"""Service — pointage des bénéficiaires (table ``presences``, règle 24/09).

Une session de formation se pointe **jour par jour** : une ligne ``Presence``
par ``(participation, date)``. Le service valide le vocabulaire
(``StatutPresence``), la plage de dates (session, à défaut sa mission) et
l'appartenance des participations, puis enregistre en upsert : re-pointer une
date corrige sans jamais dupliquer (``uq_presences_participation_id_date``).

Deux règles de trace : chaque pointage **modifiant** écrit
``presence.enregistree`` avec sa date ; re-pointer la **même** valeur n'est pas
une décision et n'écrit rien.

Le service ne committe jamais : l'atomicité d'un envoi en lot est portée par la
transaction de l'appelant (instruction/04 §5) — d'où une validation **complète**
avant la moindre écriture.

Distinct de ``presence_chat_service`` (présence des comptes dans le chat) :
deux notions de « présence » coexistent dans le domaine CARSO.
"""

from __future__ import annotations

from datetime import date
from uuid import UUID

from sqlalchemy.orm import Session

from app.application.dto import PresenceLotEntree, PresencePointageInput
from app.application.trace import TraceContext
from app.core.errors import NotFoundError, ValidationError
from app.domain.enums import ActionAudit, ActorType, StatutPresence
from app.domain.execution import Presence, SessionFormation
from app.infrastructure.repositories import (
    ParticipationRepository,
    PresenceRepository,
    SessionRepository,
)

_ENTITY = "presence"
_VOCABULAIRE = sorted(statut.value for statut in StatutPresence)


class PresenceService:
    """Use cases : pointer (lot d'une journée), pointer_participation, lister."""

    def __init__(self, session: Session, actor_id: str | None = None) -> None:
        self._session = session
        self.presences = PresenceRepository(session)
        self.participations = ParticipationRepository(session)
        self.sessions = SessionRepository(session)
        self._trace = TraceContext(session, actor_id=actor_id)

    # --- La plage de pointage ---------------------------------------------------------

    def plage(self, session_id: UUID) -> tuple[date | None, date | None]:
        """Bornes de pointage ``(début, fin)`` : dates de la session, à défaut
        celles de sa mission. Une borne manquante reste ``None`` (le contrôle
        de la date portera sur les bornes connues seulement).

        Raises:
            NotFoundError: session introuvable.
        """
        session_formation = self._session_requise(session_id)
        mission = session_formation.mission
        debut = session_formation.date_debut or (mission.date_debut if mission else None)
        fin = session_formation.date_fin or (mission.date_fin if mission else None)
        return debut, fin

    # --- Consultation -----------------------------------------------------------------

    def lister_pour_session(
        self, session_id: UUID, jour: date | None = None
    ) -> list[Presence]:
        """Pointages d'une session, toute la plage ou une seule journée.

        Raises:
            NotFoundError: session introuvable.
        """
        self._session_requise(session_id)
        return self.presences.list_for_session(session_id, jour)

    def lister_pour_participation(self, participation_id: UUID) -> list[Presence]:
        """Pointages d'une participation, du plus ancien au plus récent."""
        return self.presences.list_for_participation(participation_id)

    def compter_pour_participation(self, participation_id: UUID) -> int:
        """Nombre de journées pointées d'une participation."""
        return len(self.presences.list_for_participation(participation_id))

    # --- Pointage en lot (fiche de présence d'une journée) ----------------------------

    def pointer(
        self,
        session_id: UUID,
        jour: date,
        lignes: list[PresenceLotEntree],
    ) -> list[Presence]:
        """Pointe la fiche **d'une date**, en une transaction.

        Re-pointer la même date corrige les valeurs existantes : aucun doublon.

        Raises:
            NotFoundError: session introuvable.
            ValidationError: date hors plage (ou session sans dates), vocabulaire
                inconnu, participation étrangère à la session.
        """
        self._session_requise(session_id)  # 404 si la session n'existe pas
        self._verifier_date(jour, self.plage(session_id))

        # Validation complète AVANT toute écriture : un lot invalide ne laisse
        # rien derrière lui après le rollback de l'appelant.
        for ligne in lignes:
            participation = self.participations.get(ligne.participation_id)
            if participation is None or participation.session_id != session_id:
                raise ValidationError(
                    f"La participation {ligne.participation_id} n'appartient pas "
                    f"à la session {session_id}"
                )
            self._verifier_vocabulaire(ligne.presence)

        for ligne in lignes:
            self._upsert(
                participation_id=ligne.participation_id,
                jour=jour,
                presence=ligne.presence,
                heure_arrivee=ligne.heure_arrivee,
                heure_depart=ligne.heure_depart,
                after={
                    "session_id": str(session_id),
                    "participation_id": str(ligne.participation_id),
                    "date": jour.isoformat(),
                    "presence": ligne.presence,
                },
            )
        self._session.flush()
        return self.presences.list_for_session(session_id, jour)

    # --- Pointage individuel ----------------------------------------------------------

    def pointer_participation(
        self, participation_id: UUID, entree: PresencePointageInput
    ) -> Presence:
        """Pointe **une personne pour une date** (heures facultatives).

        Raises:
            NotFoundError: participation ou session introuvable.
            ValidationError: vocabulaire inconnu ou date hors de la plage.
        """
        participation = self.participations.get(participation_id)
        if participation is None:
            raise NotFoundError(f"Participation {participation_id} introuvable")
        session_formation = self.sessions.get(participation.session_id)
        if session_formation is None:
            raise NotFoundError(f"Session {participation.session_id} introuvable")
        self._verifier_date(entree.date, self.plage(participation.session_id))
        self._verifier_vocabulaire(entree.presence)

        presence = self._upsert(
            participation_id=participation_id,
            jour=entree.date,
            presence=entree.presence,
            heure_arrivee=entree.heure_arrivee,
            heure_depart=entree.heure_depart,
            after={
                "participation_id": str(participation_id),
                "date": entree.date.isoformat(),
                "presence": entree.presence,
            },
        )
        self._session.flush()
        return presence

    # --- Helpers -----------------------------------------------------------------------

    def _upsert(
        self,
        *,
        participation_id: UUID,
        jour: date,
        presence: str,
        heure_arrivee: object,
        heure_depart: object,
        after: dict[str, object],
    ) -> Presence:
        """Crée ou corrige le pointage ; ne trace qu'une **modification** réelle."""
        existant = self.presences.get_for_couple(participation_id, jour)
        if existant is None:
            pointage = Presence(
                participation_id=participation_id,
                date=jour,
                presence=presence,
                heure_arrivee=heure_arrivee,
                heure_depart=heure_depart,
            )
            self.presences.add(pointage)
            self._trace.record_event(
                actor_type=ActorType.HUMAIN.value,
                action=ActionAudit.PRESENCE_ENREGISTREE.value,
                entity_type=_ENTITY,
                entity_id=str(participation_id),
                after=after,
            )
            return pointage

        inchangé = (
            existant.presence == presence
            and existant.heure_arrivee == heure_arrivee
            and existant.heure_depart == heure_depart
        )
        if not inchangé:
            existant.presence = presence
            existant.heure_arrivee = heure_arrivee  # type: ignore[assignment]
            existant.heure_depart = heure_depart  # type: ignore[assignment]
            self._trace.record_event(
                actor_type=ActorType.HUMAIN.value,
                action=ActionAudit.PRESENCE_ENREGISTREE.value,
                entity_type=_ENTITY,
                entity_id=str(participation_id),
                after=after,
            )
        return existant

    def _session_requise(self, session_id: UUID) -> SessionFormation:
        session_formation = self.sessions.get(session_id)
        if session_formation is None:
            raise NotFoundError(f"Session {session_id} introuvable")
        return session_formation

    @staticmethod
    def _verifier_date(jour: date, plage: tuple[date | None, date | None]) -> None:
        debut, fin = plage
        if debut is None and fin is None:
            raise ValidationError(
                "La session n'a pas de dates planifiées : le pointage exige une "
                "plage de dates (session ou mission)"
            )
        if debut is not None and jour < debut:
            raise ValidationError(
                f"Le {jour.isoformat()} est hors de la plage de pointage "
                f"(à partir du {debut.isoformat()})"
            )
        if fin is not None and jour > fin:
            raise ValidationError(
                f"Le {jour.isoformat()} est hors de la plage de pointage "
                f"(jusqu'au {fin.isoformat()})"
            )

    @staticmethod
    def _verifier_vocabulaire(valeur: str) -> None:
        if valeur not in _VOCABULAIRE:
            raise ValidationError(
                f"Présence inconnue : {valeur!r}",
                details={"valeurs_valides": list(_VOCABULAIRE)},
            )
