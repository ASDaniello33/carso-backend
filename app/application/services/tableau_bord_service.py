"""Service métier — tableau de bord (lecture seule, déterministe).

Tous les chiffres du dashboard sont calculés en SQL par ce service, à partir
des données validées. Aucune estimation, aucune donnée de démonstration :
lorsqu'il n'y a pas de données, les compteurs valent 0 et les listes sont
vides — c'est la vérité du système (AGENTS.md §2.2 : ne jamais inventer).

Le service ne fait ni commit ni rollback (instruction/04 §5) et n'expose que
de la lecture : le tableau de bord ne mute jamais les données métier.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.domain.document import Document
from app.domain.enums import (
    StatutAppelAProposition,
    StatutBeneficiaire,
    StatutDocument,
    StatutEquipe,
    StatutMission,
    StatutOffre,
    StatutSessionFormation,
    StatutUtilisateur,
)
from app.domain.execution import Beneficiaire, Equipe, Mission, SessionFormation
from app.domain.identity import Utilisateur
from app.domain.organization import AppelAProposition, Offre, Organisation

#: Fenêtre des « sessions à venir » (alertes + KPI).
FENETRE_SESSIONS_JOURS = 7
#: Nombre maximum d'objets dans l'activité récente.
PLAFOND_ACTIVITE = 6
#: Nombre maximum de sessions listées dans les alertes.
PLAFOND_SESSIONS_A_VENIR = 5


def _aujourdhui() -> date:
    """Jour de référence (une seule date pour tout le calcul)."""
    return datetime.now(UTC).date()


def _iso(valeur: date | datetime | None) -> str | None:
    if valeur is None:
        return None
    if isinstance(valeur, datetime):
        return valeur.isoformat()
    return valeur.isoformat()


class TableauBordService:
    """Agrégations du dashboard : compteurs, alertes, activité récente."""

    def __init__(self, session: Session) -> None:
        self._session = session

    # ------------------------------------------------------------------
    # Lecture consolidée
    # ------------------------------------------------------------------

    def apercu(self) -> dict[str, Any]:
        """Le payload complet du dashboard (compteurs, alertes, activité)."""
        return {
            "compteurs": self.compteurs(),
            "alertes": self.alertes(),
            "activite_recente": self.activite_recente(),
            "genere_at": datetime.now(UTC).isoformat(),
        }

    # ------------------------------------------------------------------
    # Compteurs par statut
    # ------------------------------------------------------------------

    def _compter_par_statut(self, colonne: Any, valeurs: list[str]) -> dict[str, int]:
        """Nombre de lignes par valeur de statut (0 pour les valeurs absentes)."""
        compteurs = dict(
            self._session.execute(
                select(colonne, func.count()).where(colonne.in_(valeurs)).group_by(colonne)
            ).all()
        )
        return {valeur: int(compteurs.get(valeur, 0)) for valeur in valeurs}

    def _compter_total(self, modele: type, *filtres: Any) -> int:
        requete = select(func.count()).select_from(modele)
        if filtres:
            requete = requete.where(*filtres)
        return int(self._session.scalar(requete) or 0)

    def compteurs(self) -> dict[str, Any]:
        """Compteurs par statut des objets métier du cycle CARSO."""
        statuts_sessions = [s.value for s in StatutSessionFormation]
        sessions_par_statut = self._compter_par_statut(
            SessionFormation.statut, statuts_sessions
        )
        a_venir = self._compter_sessions_a_venir(nb=0)
        return {
            "appels": self._compter_par_statut(
                AppelAProposition.statut, [s.value for s in StatutAppelAProposition]
            ),
            "offres": self._compter_par_statut(
                Offre.statut, [s.value for s in StatutOffre]
            ),
            "missions": self._compter_par_statut(
                Mission.statut, [s.value for s in StatutMission]
            ),
            "sessions": {
                **sessions_par_statut,
                "a_venir_7j": a_venir,
            },
            "organisations": {"total": self._compter_total(Organisation)},
            "equipes": {
                "actifs": self._compter_total(
                    Equipe, Equipe.statut == StatutEquipe.ACTIF.value
                )
            },
            "beneficiaires": {
                "actifs": self._compter_total(
                    Beneficiaire, Beneficiaire.statut == StatutBeneficiaire.ACTIF.value
                )
            },
            "utilisateurs": {
                "en_attente": self._compter_total(
                    Utilisateur, Utilisateur.statut == StatutUtilisateur.PENDING.value
                )
            },
        }

    # ------------------------------------------------------------------
    # Alertes (cliquables, calculées — jamais inventées)
    # ------------------------------------------------------------------

    def alertes(self) -> list[dict[str, Any]]:
        """Points d'attention du jour, triés par priorité de traitement."""
        alertes: list[dict[str, Any]] = []

        appels_attente = (
            self._compter_total(
                AppelAProposition,
                AppelAProposition.statut == StatutAppelAProposition.RECU.value,
            )
            + self._compter_total(
                AppelAProposition,
                AppelAProposition.statut == StatutAppelAProposition.EN_ANALYSE.value,
            )
        )
        if appels_attente > 0:
            alertes.append(
                {
                    "type": "appels_a_traiter",
                    "nb": appels_attente,
                    "texte": (
                        f"{appels_attente} appel à proposition attend une analyse"
                        if appels_attente == 1
                        else f"{appels_attente} appels à proposition attendent une analyse"
                    ),
                    "href": "/appels-propositions",
                }
            )

        offres_revue = self._compter_total(
            Offre, Offre.statut == StatutOffre.EN_REVUE.value
        )
        if offres_revue > 0:
            alertes.append(
                {
                    "type": "offres_a_valider",
                    "nb": offres_revue,
                    "texte": (
                        f"{offres_revue} offre attend une validation"
                        if offres_revue == 1
                        else f"{offres_revue} offres attendent une validation"
                    ),
                    "href": "/offres",
                }
            )

        documents_proposes = self._compter_total(
            Document, Document.statut == StatutDocument.PROPOSED.value
        )
        if documents_proposes > 0:
            alertes.append(
                {
                    "type": "documents_a_approuver",
                    "nb": documents_proposes,
                    "texte": (
                        f"{documents_proposes} document attend une approbation"
                        if documents_proposes == 1
                        else f"{documents_proposes} documents attendent une approbation"
                    ),
                    "href": "/documents",
                }
            )

        inscriptions = self._compter_total(
            Utilisateur, Utilisateur.statut == StatutUtilisateur.PENDING.value
        )
        if inscriptions > 0:
            alertes.append(
                {
                    "type": "inscriptions_en_attente",
                    "nb": inscriptions,
                    "texte": (
                        f"{inscriptions} demande d'inscription attend une décision"
                        if inscriptions == 1
                        else f"{inscriptions} demandes d'inscription attendent une décision"
                    ),
                    "href": "/utilisateurs",
                }
            )

        limite = _aujourdhui() + timedelta(days=FENETRE_SESSIONS_JOURS)
        sessions_prochaines = (
            self._session.execute(
                select(SessionFormation, Mission.titre)
                .join(Mission, SessionFormation.mission_id == Mission.id)
                .where(
                    SessionFormation.statut.in_(
                        [
                            StatutSessionFormation.PLANIFIEE.value,
                            StatutSessionFormation.CONFIRMEE.value,
                        ]
                    ),
                    SessionFormation.date_debut >= _aujourdhui(),
                    SessionFormation.date_debut <= limite,
                )
                .order_by(SessionFormation.date_debut.asc())
                .limit(PLAFOND_SESSIONS_A_VENIR)
            )
            .all()
        )
        for session_formation, mission_titre in sessions_prochaines:
            confirmee = session_formation.statut == StatutSessionFormation.CONFIRMEE.value
            statut = "confirmée" if confirmee else "planifiée"
            alertes.append(
                {
                    "type": "session_a_venir",
                    "nb": 1,
                    "texte": (
                        f"Session « {session_formation.theme or 'sans thème'} » "
                        f"({statut}) le {_iso(session_formation.date_debut)} — {mission_titre}"
                    ),
                    "href": "/sessions",
                }
            )

        return alertes

    def _compter_sessions_a_venir(self, *, nb: int) -> int:
        """Sessions planifiées/confirmées démarrant sous 7 jours (KPI)."""
        limite = _aujourdhui() + timedelta(days=FENETRE_SESSIONS_JOURS)
        return int(
            self._session.scalar(
                select(func.count())
                .select_from(SessionFormation)
                .where(
                    SessionFormation.statut.in_(
                        [
                            StatutSessionFormation.PLANIFIEE.value,
                            StatutSessionFormation.CONFIRMEE.value,
                        ]
                    ),
                    SessionFormation.date_debut >= _aujourdhui(),
                    SessionFormation.date_debut <= limite,
                )
            )
            or 0
        )

    # ------------------------------------------------------------------
    # Activité récente
    # ------------------------------------------------------------------

    def activite_recente(self) -> list[dict[str, Any]]:
        """Derniers objets créés sur le cycle métier (fusionnés, plafonnés)."""
        elements: list[dict[str, Any]] = []

        for appel in self._session.scalars(
            select(AppelAProposition)
            .order_by(AppelAProposition.created_at.desc())
            .limit(PLAFOND_ACTIVITE)
        ):
            elements.append(
                {
                    "type": "appel",
                    "id": str(appel.id),
                    "titre": appel.titre,
                    "reference": appel.reference,
                    "date": _iso(appel.created_at),
                    "statut": appel.statut,
                }
            )
        for offre in self._session.scalars(
            select(Offre).order_by(Offre.created_at.desc()).limit(PLAFOND_ACTIVITE)
        ):
            elements.append(
                {
                    "type": "offre",
                    "id": str(offre.id),
                    "titre": offre.titre,
                    "reference": offre.reference,
                    "date": _iso(offre.created_at),
                    "statut": offre.statut,
                }
            )
        for mission in self._session.scalars(
            select(Mission).order_by(Mission.created_at.desc()).limit(PLAFOND_ACTIVITE)
        ):
            elements.append(
                {
                    "type": "mission",
                    "id": str(mission.id),
                    "titre": mission.titre,
                    "reference": mission.reference,
                    "date": _iso(mission.created_at),
                    "statut": mission.statut,
                }
            )
        for session_formation in self._session.scalars(
            select(SessionFormation)
            .order_by(SessionFormation.created_at.desc())
            .limit(PLAFOND_ACTIVITE)
        ):
            elements.append(
                {
                    "type": "session",
                    "id": str(session_formation.id),
                    "titre": session_formation.theme or "Session de formation",
                    "reference": None,
                    "date": _iso(session_formation.created_at),
                    "statut": session_formation.statut,
                }
            )

        elements = [e for e in elements if e["date"] is not None]
        elements.sort(key=lambda e: e["date"], reverse=True)
        return elements[:PLAFOND_ACTIVITE]


__all__ = ["TableauBordService"]
