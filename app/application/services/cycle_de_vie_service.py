"""Service métier—balayage du cycle de vie (ADR 0012).

Les statuts « En cours » et « Clôturé » d'une mission et de sa session sont
**commandés par le calendrier** : aucun humain ne les saisit. Ce service
applique les transitions dues, de façon **idempotente** et **bornée** :

- session ``planifie`` → ``en_cours`` quand ``date_debut`` est atteinte ;
- session ``en_cours`` → ``cloture`` quand ``date_fin`` est dépassée ;
- mission ``planifie`` → ``en_cours`` quand sa session l'est (ou, pour une
  mission héritée sans session, quand ``date_debut`` est atteinte) ;
- mission ``en_cours`` → ``cloture`` quand sa session l'est (ou quand
  ``date_fin`` est dépassée).

Chaque transition passe par ``validate_transition`` — un statut ne change
jamais hors de la machine à états — et écrit un ``AuditEvent`` d'acteur
``SYSTEME`` avec la règle qui l'a déclenchée. Les transitions automatiques
n'émettent **pas** de notification : elles sont nombreuses et sans décision
humaine à attendre.

Le service ne fait ni commit ni rollback : la transaction appartient à
l'appelant (le balayage, une route de diagnostic, un test).
"""

from __future__ import annotations

import logging
from datetime import date

from sqlalchemy.orm import Session

from app.application.trace import TraceContext
from app.domain.enums import (
    ActionAudit,
    ActorType,
    StatutMission,
    StatutSessionFormation,
)
from app.domain.execution import Mission, SessionFormation
from app.domain.state_machines import validate_transition
from app.infrastructure.repositories import MissionRepository, SessionRepository

logger = logging.getLogger(__name__)

_MISSION = "mission"
_SESSION = "session"

_REGLE_SESSION_DEMARRAGE = "session.date_debut_atteinte"
_REGLE_SESSION_CLOTURE = "session.date_fin_depassee"
_REGLE_MISSION_DEMARRAGE = "session_en_cours"
_REGLE_MISSION_DEMARRAGE_SANS_SESSION = "mission.date_debut_atteinte"
_REGLE_MISSION_CLOTURE = "session_cloturee"
_REGLE_MISSION_CLOTURE_SANS_SESSION = "mission.date_fin_depassee"


class CycleDeVieService:
    """Un seul use case : ``appliquer_transitions_dues(aujourd'hui)``."""

    def __init__(self, session: Session, actor_id: str | None = None) -> None:
        self._session = session
        self.missions = MissionRepository(session)
        self.sessions = SessionRepository(session)
        self._trace = TraceContext(session, actor_id=actor_id)

    def appliquer_transitions_dues(self, aujourdhui: date) -> dict[str, int]:
        """Applique toutes les transitions dues, idempotent, borné.

        L'ordre est déterministe : les sessions d'abord (elles commandent les
        missions), puis les missions. Rejouer le balayage le même jour ne
        réécrit rien : chaque candidat est re-testé, seul un changement réel
        écrit.

        Returns:
            Les compteurs par règle — journalisés par le balayage, assertés
            par les tests. Aucun commit.
        """
        compteurs = {
            _REGLE_SESSION_DEMARRAGE: 0,
            _REGLE_SESSION_CLOTURE: 0,
            _REGLE_MISSION_DEMARRAGE: 0,
            _REGLE_MISSION_DEMARRAGE_SANS_SESSION: 0,
            _REGLE_MISSION_CLOTURE: 0,
            _REGLE_MISSION_CLOTURE_SANS_SESSION: 0,
        }

        for session_formation in self._sessions_a_demarrer(aujourdhui):
            self._transition_session(session_formation, StatutSessionFormation.EN_COURS)
            compteurs[_REGLE_SESSION_DEMARRAGE] += 1
        for session_formation in self._sessions_a_cloturer(aujourdhui):
            self._transition_session(session_formation, StatutSessionFormation.CLOTURE)
            compteurs[_REGLE_SESSION_CLOTURE] += 1
        for mission, regle in self._missions_a_demarrer(aujourdhui):
            self._transition_mission(mission, StatutMission.EN_COURS, regle)
            compteurs[regle] += 1
        for mission, regle in self._missions_a_cloturer(aujourdhui):
            self._transition_mission(mission, StatutMission.CLOTURE, regle)
            compteurs[regle] += 1
        return compteurs

    # --- sélection des candidats --------------------------------------------

    def _sessions_a_demarrer(self, aujourdhui: date) -> list[SessionFormation]:
        """Sessions planifiées dont le jour J est arrivé."""
        return [
            s
            for s in self.sessions.list_by_statut(StatutSessionFormation.PLANIFIE.value)
            if s.date_debut is not None and s.date_debut <= aujourdhui
        ]

    def _sessions_a_cloturer(self, aujourdhui: date) -> list[SessionFormation]:
        """Sessions en cours dont la date de fin est dépassée."""
        return [
            s
            for s in self.sessions.list_by_statut(StatutSessionFormation.EN_COURS.value)
            if s.date_fin is not None and s.date_fin < aujourdhui
        ]

    def _missions_a_demarrer(self, aujourdhui: date) -> list[tuple[Mission, str]]:
        """Missions planifiées dont l'exécution a commencé.

        La mission suit le **calendrier de sa session** (les deux portent les
        mêmes dates à la planification) : comparer la date, pas le statut
        courant de la session, garde le balayage idempotent même quand la
        session a démarré **et** terminé entre deux balayages. Une mission
        héritée sans session se règle sur ses propres dates.
        """
        planifiees = self.missions.list_by_statut(StatutMission.PLANIFIE.value)
        resultats: list[tuple[Mission, str]] = []
        for mission in planifiees:
            session_formation = self.sessions.get_for_mission(mission.id)
            if session_formation is not None:
                if (
                    session_formation.date_debut is not None
                    and session_formation.date_debut <= aujourdhui
                ):
                    resultats.append((mission, _REGLE_MISSION_DEMARRAGE))
                continue
            # Héritage : mission planifiée avant l'ADR 0012, sans session.
            if mission.date_debut is not None and mission.date_debut <= aujourdhui:
                resultats.append((mission, _REGLE_MISSION_DEMARRAGE_SANS_SESSION))
        return resultats

    def _missions_a_cloturer(self, aujourdhui: date) -> list[tuple[Mission, str]]:
        """Missions en cours dont l'exécution est terminée.

        Même règle que le démarrage : la date de fin de la session décide, pas
        son statut courant — sinon une session démarrée et terminée entre
        deux balayages laisserait la mission en « planifié » à jamais.
        """
        en_cours = self.missions.list_by_statut(StatutMission.EN_COURS.value)
        resultats: list[tuple[Mission, str]] = []
        for mission in en_cours:
            session_formation = self.sessions.get_for_mission(mission.id)
            if session_formation is not None:
                if (
                    session_formation.date_fin is not None
                    and session_formation.date_fin < aujourdhui
                ):
                    resultats.append((mission, _REGLE_MISSION_CLOTURE))
                continue
            # Héritage : la mission clôturait sur ses propres dates.
            if mission.date_fin is not None and mission.date_fin < aujourdhui:
                resultats.append((mission, _REGLE_MISSION_CLOTURE_SANS_SESSION))
        return resultats

    # --- écriture ------------------------------------------------------------

    def _transition_session(
        self, session_formation: SessionFormation, cible: StatutSessionFormation
    ) -> None:
        validate_transition(_SESSION, session_formation.statut, cible.value)
        session_formation.statut = cible.value
        self._session.flush()
        self._trace.record_event(
            actor_type=ActorType.SYSTEME.value,
            action=ActionAudit.SESSION_STATUT_CHANGE.value,
            entity_type=_SESSION,
            entity_id=session_formation.id,
            after={"statut": cible.value},
            event_metadata={"automatique": True, "regle": "cycle_de_vie"},
        )

    def _transition_mission(
        self, mission: Mission, cible: StatutMission, regle: str
    ) -> None:
        validate_transition(_MISSION, mission.statut, cible.value)
        mission.statut = cible.value
        self._session.flush()
        self._trace.record_event(
            actor_type=ActorType.SYSTEME.value,
            action=ActionAudit.MISSION_STATUT_CHANGE.value,
            entity_type=_MISSION,
            entity_id=mission.id,
            after={"statut": cible.value},
            event_metadata={"automatique": True, "regle": regle},
        )


__all__ = ["CycleDeVieService"]
