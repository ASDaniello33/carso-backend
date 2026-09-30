"""Service métier — recherche globale (lecture seule).

Une requête, sept familles d'objets : appels, offres, missions, sessions,
organisations, équipes et documents (nom + métadonnées — le contenu des
fichiers n'est pas indexé, décision validée). Les bénéficiaires, données
sensibles, ne sont recherchés que si l'appelant a la permission dédiée
(décision validée) ; la recherche n'expose que nom et prénom.

La recherche est bornée (plafond par famille, requête minimale) et part de la
donnée validée : jamais de contenu inventé ni de résultat hors permission.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.domain.document import Document
from app.domain.execution import Beneficiaire, Equipe, Mission, SessionFormation
from app.domain.organization import AppelAProposition, Offre, Organisation

#: Requête minimale (évite un balayage complet sur « a »).
LONGUEUR_MINIMALE = 2
#: Résultats maximum par famille.
PLAFOND_PAR_FAMILLE = 5

_COMMENCE_PAR = "%{}%"
_CONTIENT = "%{}%"


def _nettoyer(requete: str) -> str:
    """Trim + suppression des jokers SQL pour rester sur une recherche littérale."""
    brut = (requete or "").strip()
    for joker in ("%", "_"):
        brut = brut.replace(joker, " ")
    return " ".join(brut.split())


class RechercheService:
    """Recherche textuelle bornée sur les objets métier du cycle CARSO."""

    def __init__(self, session: Session, *, peut_voir_beneficiaires: bool = False) -> None:
        self._session = session
        self._beneficiaires_autorises = peut_voir_beneficiaires

    def rechercher(self, requete: str) -> dict[str, Any]:
        """Résultats groupés par famille, avec l'URL de navigation."""
        terme = _nettoyer(requete)
        if len(terme) < LONGUEUR_MINIMALE:
            return {"requete": requete, "familles": [], "total": 0}

        familles: list[dict[str, Any]] = []
        familles.extend(self._appels(terme))
        familles.extend(self._offres(terme))
        familles.extend(self._missions(terme))
        familles.extend(self._sessions(terme))
        familles.extend(self._organisations(terme))
        familles.extend(self._equipes(terme))
        familles.extend(self._documents(terme))
        if self._beneficiaires_autorises:
            familles.extend(self._beneficiaires(terme))

        total = sum(f["total"] for f in familles)
        return {"requete": terme, "familles": familles, "total": total}

    # ------------------------------------------------------------------
    # Familles (une méthode par famille : chaque requête reste lisible)
    # ------------------------------------------------------------------

    def _appels(self, terme: str) -> list[dict[str, Any]]:
        motif_contient = _CONTIENT.format(terme)
        lignes = self._session.execute(
            select(AppelAProposition)
            .where(
                or_(
                    AppelAProposition.titre.ilike(motif_contient),
                    AppelAProposition.reference.ilike(motif_contient),
                )
            )
            .order_by(AppelAProposition.titre.asc())
            .limit(PLAFOND_PAR_FAMILLE)
        ).scalars().all()
        return [
            {
                "famille": "appels",
                "label": "Appels à propositions",
                "total": len(lignes),
                "resultats": [
                    {
                        "id": str(appel.id),
                        "titre": appel.titre,
                        "detail": appel.reference,
                        "statut": appel.statut,
                        "href": f"/appels-propositions/{appel.id}",
                    }
                    for appel in lignes
                ],
            }
        ] if lignes else []

    def _offres(self, terme: str) -> list[dict[str, Any]]:
        motif_contient = _CONTIENT.format(terme)
        lignes = self._session.execute(
            select(Offre)
            .where(
                or_(
                    Offre.titre.ilike(motif_contient),
                    Offre.reference.ilike(motif_contient),
                )
            )
            .order_by(Offre.titre.asc())
            .limit(PLAFOND_PAR_FAMILLE)
        ).scalars().all()
        return [
            {
                "famille": "offres",
                "label": "Offres de formation",
                "total": len(lignes),
                "resultats": [
                    {
                        "id": str(offre.id),
                        "titre": offre.titre,
                        "detail": offre.reference,
                        "statut": offre.statut,
                        "href": f"/offres/{offre.id}",
                    }
                    for offre in lignes
                ],
            }
        ] if lignes else []

    def _missions(self, terme: str) -> list[dict[str, Any]]:
        motif_contient = _CONTIENT.format(terme)
        lignes = self._session.execute(
            select(Mission)
            .where(
                or_(
                    Mission.titre.ilike(motif_contient),
                    Mission.reference.ilike(motif_contient),
                )
            )
            .order_by(Mission.titre.asc())
            .limit(PLAFOND_PAR_FAMILLE)
        ).scalars().all()
        return [
            {
                "famille": "missions",
                "label": "Missions",
                "total": len(lignes),
                "resultats": [
                    {
                        "id": str(mission.id),
                        "titre": mission.titre,
                        "detail": mission.reference,
                        "statut": mission.statut,
                        "href": f"/missions/{mission.id}",
                    }
                    for mission in lignes
                ],
            }
        ] if lignes else []

    def _sessions(self, terme: str) -> list[dict[str, Any]]:
        motif_contient = _CONTIENT.format(terme)
        # Le thème de session est facultatif : il ne peut pas porter la
        # recherche à lui seul ; on cherche aussi le titre de la mission mère.
        lignes = self._session.execute(
            select(SessionFormation, Mission.titre)
            .join(Mission, SessionFormation.mission_id == Mission.id)
            .where(
                or_(
                    SessionFormation.theme.ilike(motif_contient),
                    Mission.titre.ilike(motif_contient),
                    Mission.reference.ilike(motif_contient),
                )
            )
            .order_by(SessionFormation.theme.asc())
            .limit(PLAFOND_PAR_FAMILLE)
        ).all()
        return [
            {
                "famille": "sessions",
                "label": "Sessions",
                "total": len(lignes),
                "resultats": [
                    {
                        "id": str(session_formation.id),
                        "titre": session_formation.theme or f"Session — {mission_titre}",
                        "detail": mission_titre,
                        "statut": session_formation.statut,
                        "href": f"/sessions/{session_formation.id}",
                    }
                    for session_formation, mission_titre in lignes
                ],
            }
        ] if lignes else []

    def _organisations(self, terme: str) -> list[dict[str, Any]]:
        motif_contient = _CONTIENT.format(terme)
        lignes = self._session.execute(
            select(Organisation)
            .where(Organisation.nom.ilike(motif_contient))
            .order_by(Organisation.nom.asc())
            .limit(PLAFOND_PAR_FAMILLE)
        ).scalars().all()
        return [
            {
                "famille": "organisations",
                "label": "Organisations",
                "total": len(lignes),
                "resultats": [
                    {
                        "id": str(organisation.id),
                        "titre": organisation.nom,
                        "detail": None,
                        "statut": None,
                        "href": f"/organisations/{organisation.id}",
                    }
                    for organisation in lignes
                ],
            }
        ] if lignes else []

    def _equipes(self, terme: str) -> list[dict[str, Any]]:
        motif_contient = _CONTIENT.format(terme)
        lignes = self._session.execute(
            select(Equipe)
            .where(
                or_(
                    Equipe.nom.ilike(motif_contient),
                    Equipe.prenom.ilike(motif_contient),
                )
            )
            .order_by(Equipe.nom.asc())
            .limit(PLAFOND_PAR_FAMILLE)
        ).scalars().all()
        return [
            {
                "famille": "equipes",
                "label": "Équipes",
                "total": len(lignes),
                "resultats": [
                    {
                        "id": str(equipe.id),
                        "titre": f"{equipe.prenom} {equipe.nom}".strip(),
                        "detail": equipe.email,
                        "statut": equipe.statut,
                        "href": f"/equipes/{equipe.id}",
                    }
                    for equipe in lignes
                ],
            }
        ] if lignes else []

    def _documents(self, terme: str) -> list[dict[str, Any]]:
        motif_contient = _CONTIENT.format(terme)
        lignes = self._session.execute(
            select(Document)
            .where(
                or_(
                    Document.nom.ilike(motif_contient),
                    Document.type_document.ilike(motif_contient),
                )
            )
            .order_by(Document.nom.asc())
            .limit(PLAFOND_PAR_FAMILLE)
        ).scalars().all()
        return [
            {
                "famille": "documents",
                "label": "Documents",
                "total": len(lignes),
                "resultats": [
                    {
                        "id": str(document.id),
                        "titre": document.nom,
                        "detail": document.type_document,
                        "statut": document.statut,
                        "href": f"/documents/{document.id}",
                    }
                    for document in lignes
                ],
            }
        ] if lignes else []

    def _beneficiaires(self, terme: str) -> list[dict[str, Any]]:
        """Données sensibles : permission vérifiée par la route, champs minimaux."""
        motif_contient = _CONTIENT.format(terme)
        lignes = self._session.execute(
            select(Beneficiaire)
            .where(
                Beneficiaire.statut == "actif",
                or_(
                    Beneficiaire.nom.ilike(motif_contient),
                    Beneficiaire.prenom.ilike(motif_contient),
                ),
            )
            .order_by(Beneficiaire.nom.asc())
            .limit(PLAFOND_PAR_FAMILLE)
        ).scalars().all()
        return [
            {
                "famille": "beneficiaires",
                "label": "Bénéficiaires",
                "total": len(lignes),
                "resultats": [
                    {
                        "id": str(beneficiaire.id),
                        "titre": f"{beneficiaire.prenom} {beneficiaire.nom}".strip(),
                        "detail": None,
                        "statut": None,
                        "href": f"/beneficiaires/{beneficiaire.id}",
                    }
                    for beneficiaire in lignes
                ],
            }
        ] if lignes else []


__all__ = ["RechercheService"]
