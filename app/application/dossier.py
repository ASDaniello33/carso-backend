"""Dossier lié à une mission—ce qu'un compte restreint peut rattacher.

Décision validée—un formateur affecté à une mission voit le **dossier lié**
de cette mission—l'appel source et ses documents, ses lots, l'organisation
cliente—mais jamais les listes transverses (tous les appels, toutes les
organisations…), qui restent des lectures de l'équipe interne. Les **offres**
sont réservées aux administrateurs et collaborateurs (décision 18/11) : elles
restent calculées ici pour les services internes, jamais servies à un compte
restreint.

Ce module calcule les ensembles d'identifiants accessibles à partir des
missions du périmètre. Il ne décide de rien—les services s'en servent pour
répondre **404** lorsqu'un objet hors dossier est demandé (un refus ne doit
jamais confirmer l'existence d'un objet d'une autre mission).
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.application.perimetre import Perimetre
from app.domain.execution import Mission, MissionOffre
from app.domain.organization import Lot, Offre


@dataclass(frozen=True, slots=True)
class DossierAutorise:
    """Identifiants visibles par un compte restreint via ses missions."""

    organisations: frozenset[UUID]
    appels: frozenset[UUID]
    offres: frozenset[UUID]
    lots: frozenset[UUID]

    def contient_organisation(self, identifiant: UUID) -> bool:
        return identifiant in self.organisations

    def contient_appel(self, identifiant: UUID) -> bool:
        return identifiant in self.appels

    def contient_offre(self, identifiant: UUID) -> bool:
        return identifiant in self.offres

    def contient_lot(self, identifiant: UUID) -> bool:
        return identifiant in self.lots


#: Dossier vide : un compte restreint sans mission ne rattache rien.
DOSSIER_VIDE = DossierAutorise(
    organisations=frozenset(),
    appels=frozenset(),
    offres=frozenset(),
    lots=frozenset(),
)


def dossier_autorise(session: Session, perimetre: Perimetre) -> DossierAutorise:
    """Calcule le dossier lié des missions du périmètre.

    Args:
        session: session SQLAlchemy (lecture seule).
        perimetre: périmètre de l'utilisateur (accès complet ⇒ inutile, mais
            la fonction reste correcte : elle renvoie alors les ensembles des
            missions demandées).

    Returns:
        Les identifiants d'organisations, d'appels, d'offres et de lots
        rattachés aux missions autorisées. Un périmètre restreint sans mission
        donne un dossier vide.
    """
    missions = list(perimetre.missions_autorisees)
    if not missions:
        return DOSSIER_VIDE

    organisations = frozenset(
        session.scalars(
            select(Mission.organisation_id).where(Mission.id.in_(missions))
        ).all()
    )
    offres = frozenset(
        session.scalars(
            select(MissionOffre.offre_id).where(MissionOffre.mission_id.in_(missions))
        ).all()
    )
    lots_des_offres: frozenset[UUID] = frozenset()
    if offres:
        lots_des_offres = frozenset(
            session.scalars(select(Offre.lot_id).where(Offre.id.in_(offres))).all()
        )
    # Une mission peut naître **avant** l'approbation des offres de son lot
    # (règle du 10/11) : elle porte alors ``missions.lot_id`` sans aucune offre
    # rattachée. L'appel source doit rester dans le dossier — sans cette
    # lecture, une mission issue d'un lot sans offre perdait son appel et ses
    # documents (constat 18/11 sur ``MISSION-HI-NOV-2027``).
    lots_directs: frozenset[UUID] = frozenset(
        lot_id
        for lot_id in session.scalars(
            select(Mission.lot_id).where(
                Mission.id.in_(missions), Mission.lot_id.is_not(None)
            )
        )
        if lot_id is not None
    )
    lots = lots_des_offres | lots_directs
    appels: frozenset[UUID] = frozenset()
    if lots:
        appels = frozenset(
            session.scalars(
                select(Lot.appel_id).where(Lot.id.in_(lots))
            ).all()
        )

    return DossierAutorise(
        organisations=organisations,
        appels=appels,
        offres=offres,
        lots=lots,
    )


__all__ = ["DOSSIER_VIDE", "DossierAutorise", "dossier_autorise"]
