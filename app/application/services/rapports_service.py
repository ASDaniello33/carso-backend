"""Service—rapports et indicateurs—tableau de bord par défaut (lecture seule).

Assemble le **tableau de bord par défaut** de la page Rapports à partir des
agrégations déterministes nommées (mêmes fonctions que ``query_analytics``) :
le service calcule, la page affiche—aucun chiffre ne naît d'un modèle.

Le format de sortie est **le même** que celui du dashboard généré par l'agent
(outil frontend ``generer_dashboard``)—une définition
``{titre, sous_titre, source, genere_le, widgets[], sources[], note_methode}``
rendue par le même composant frontend. Une seule voie de rendu, deux
producteurs (service / agent)—et dans les deux cas, des valeurs calculées.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.agents.consultation import (
    compter_affectations_par_role,
    compter_beneficiaires_par_mois,
    compter_missions_par_organisation,
    compter_missions_par_statut,
    compter_sessions_par_mois,
)
from app.agents.tools.agent_generaliste_tools import (
    compter_beneficiaires_total,
    compter_offres_par_statut,
)
from app.application.perimetre import (
    Perimetre,
    perimetre_effectif,
    poser_perimetre,
    retablir_perimetre,
)

#: Fenêtre mensuelle du dashboard par défaut (12 mois glissants).
FENETRE_MOIS = 12


def _en_lignes(repartition: dict[str, Any], *, tri: str = "valeur") -> list[dict[str, Any]]:
    """Convertit une répartition ``{libellé: valeur}`` en lignes de widget.

    ``tri="valeur"`` classe par valeur décroissante (répartitions de statuts,
    rôles, organisations) ; ``tri="cle"`` garde l'ordre chronologique (mois).
    """
    lignes = [
        {"libelle": str(cle), "valeur": int(valeur or 0)}
        for cle, valeur in repartition.items()
    ]
    if tri == "valeur":
        lignes.sort(key=lambda ligne: (-ligne["valeur"], ligne["libelle"]))
    return lignes


class RapportsService:
    """Lecture seule : agrégations nommées assemblées en définition de dashboard."""

    def __init__(self, session: Session, *, perimetre: Perimetre | None = None) -> None:
        self._session = session
        self._perimetre = perimetre if perimetre is not None else perimetre_effectif()

    def tableau_bord_defaut(self) -> dict[str, Any]:
        """Définition du tableau de bord par défaut (8 agrégations autorisées).

        Le périmètre du compte est posé pour la durée du calcul : les
        agrégations lisent le même ``ContextVar`` que les tools des agents, donc
        une seule règle de filtrage. Sous périmètre restreint, les chiffres sont
        ceux des missions affectées au compte — jamais ceux de l'entreprise.
        """
        jeton = poser_perimetre(self._perimetre)
        try:
            return self._assembler()
        finally:
            retablir_perimetre(jeton)

    def _assembler(self) -> dict[str, Any]:
        """Assemble les widgets (appelé périmètre déjà posé)."""
        session = self._session
        restreint = self._perimetre.est_restreint

        missions_statut = compter_missions_par_statut(session, {})
        missions_organisation = compter_missions_par_organisation(session, {})
        sessions_mois = compter_sessions_par_mois(session, {"mois": FENETRE_MOIS})
        beneficiaires_mois = compter_beneficiaires_par_mois(session, {"mois": FENETRE_MOIS})
        affectations_role = compter_affectations_par_role(session, {})
        offres_statut = compter_offres_par_statut(session, {})
        beneficiaires_total = compter_beneficiaires_total(session, {})

        widgets: list[dict[str, Any]] = [
            {
                "type": "tuile",
                "libelle": "Missions enregistrées",
                "valeur": missions_statut["total"],
                "detail": "toutes périodes",
            },
            {
                "type": "tuile",
                "libelle": "Sessions lancées",
                "valeur": sessions_mois["total_sessions_datees"],
                "detail": (
                    f"dont {sum(sessions_mois['sessions_par_mois'].values())} "
                    f"sur {FENETRE_MOIS} mois"
                ),
            },
            {
                "type": "tuile",
                "libelle": "Bénéficiaires formés",
                "valeur": beneficiaires_mois["total_beneficiaires_formes"],
                "detail": "personnes distinctes",
            },
            {
                "type": "tuile",
                "libelle": "Bénéficiaires enregistrés",
                "valeur": beneficiaires_total["beneficiaires_total"],
                "detail": "registre actif",
            },
            {
                "type": "graphique",
                "forme": "barres",
                "libelle": "Missions par statut",
                "donnees": _en_lignes(missions_statut["missions_par_statut"]),
            },
            {
                "type": "graphique",
                "forme": "lignes",
                "libelle": "Sessions lancées par mois",
                "donnees": _en_lignes(sessions_mois["sessions_par_mois"], tri="cle"),
            },
            {
                "type": "graphique",
                "forme": "lignes",
                "libelle": "Bénéficiaires formés par mois",
                "donnees": _en_lignes(beneficiaires_mois["beneficiaires_par_mois"], tri="cle"),
            },
            {
                "type": "graphique",
                "forme": "secteurs",
                "libelle": "Affectations approuvées par rôle",
                "donnees": _en_lignes(affectations_role["affectations_approuvees_par_role"]),
            },
            {
                "type": "graphique",
                "forme": "barres",
                "libelle": "Offres par statut",
                "donnees": _en_lignes(offres_statut["offres_par_statut"]),
            },
            {
                "type": "tableau",
                "libelle": "Missions par organisation",
                "colonnes": [
                    {"cle": "organisation", "titre": "Organisation"},
                    {"cle": "missions", "titre": "Missions"},
                ],
                "lignes": [
                    {
                        "id": ligne["libelle"],
                        "valeurs": {
                            "organisation": ligne["libelle"],
                            "missions": str(ligne["valeur"]),
                        },
                    }
                    for ligne in _en_lignes(missions_organisation["missions_par_organisation"])
                ],
            },
        ]

        return {
            "titre": "Vue d'ensemble de l'activité",
            "sous_titre": (
                (
                    "Vos missions, vos sessions et leurs bénéficiaires : "
                    "agrégations déterministes du service, bornées à votre "
                    "périmètre."
                )
                if restreint
                else (
                    "Missions, sessions, bénéficiaires, offres et affectations : "
                    "agrégations déterministes du service."
                )
            ),
            "source": "base",
            "genere_le": datetime.now().isoformat(timespec="seconds"),
            "widgets": widgets,
            "sources": [
                {"type": "base", "reference": f"query_analytics:{metrique}"}
                for metrique in (
                    "missions_par_statut",
                    "missions_par_organisation",
                    "sessions_par_mois",
                    "beneficiaires_par_mois",
                    "affectations_par_role",
                    "offres_par_statut",
                    "beneficiaires_total",
                )
            ],
            "note_methode": (
                (
                    "Chiffres limités aux missions où vous êtes affecté et "
                    "approuvé. Le service calcule, l'interface affiche : aucune "
                    "valeur produite ni estimée par un modèle."
                )
                if restreint
                else (
                    "Le service calcule, l'interface affiche : aucune valeur "
                    "produite ni estimée par un modèle."
                )
            ),
        }


__all__ = ["FENETRE_MOIS", "RapportsService"]
