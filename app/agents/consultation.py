"""Lectures métier de l'agent généraliste (clinrules 06, Lot A1).

``consultation_service`` est le **seul point d'accès données** de
``agent_generaliste_readonly`` : des requêtes **nommées et bornées**, jamais
de SQL libre (clinrules 08 : « Pas de SQL libre non contrôlé pour les
mutations » — ici ni pour les lectures exposées au modèle : le périmètre des
colonnes est fixé par fonction, jamais construit depuis une entrée du LLM).

Chaque fonction reçoit une session SQLAlchemy et un payload validé, et
renvoie une valeur sérialisable par le kit (``serialiser_entite``).

Fonctions :

- ``rechercher_organisations`` — organisations par nom (ilike borné).
- ``rechercher_appels_a_proposition`` — appels (filtre organisation et statut).
- ``rechercher_missions`` — missions par statut / organisation.
- ``rechercher_equipes`` — vivier par nom/prénom/profil (CV exclus : seuls
  les documents de type ``cv`` sont listés par ``documents_d_equipe``).
- ``rechercher_documents`` — documents par ancre métier et type.
- ``compter_missions_par_statut`` — agrégation déterministe (le LLM explique,
  le service calcule — instruction/10 §9).
- ``compter_beneficiaires_par_session`` — présence par session.
- ``compter_affectations_par_role`` — rôles réellement utilisés.
- ``compter_missions_par_organisation`` — missions par organisation cliente.
- ``compter_sessions_par_mois`` — sessions lancées par mois (fenêtre glissante).
- ``compter_beneficiaires_par_mois`` — bénéficiaires distincts formés par mois.
"""

from __future__ import annotations

from datetime import date as DateType
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agents.toolkit import (
    LIMITE_MAX_RECHERCHE,
    PageSearchInput,
    serialiser_entite,
)
from app.core.errors import NotFoundError
from app.core.errors import ValidationError as BusinessValidationError
from app.domain.document import Document
from app.domain.enums import StatutAffectation
from app.domain.execution import (
    AffectationEquipe,
    Equipe,
    Mission,
    Participation,
    Presence,
    SessionFormation,
)
from app.domain.organization import AppelAProposition, Organisation
from app.infrastructure.repositories import (
    AppelAPropositionRepository,
    MissionRepository,
)

_CHAMPS_ORGANISATION = ("id", "nom", "type", "email", "telephone", "statut")
_CHAMPS_APPEL = (
    "id",
    "organisation_id",
    "reference",
    "titre",
    "statut",
    "date_reception",
    "date_limite",
)
_CHAMPS_LOT = ("id", "appel_a_proposition_id", "numero", "titre", "zone")
_CHAMPS_OFFRE = (
    "id",
    "organisation_id",
    "lot_id",
    "reference",
    "titre",
    "statut",
    "version",
)
_CHAMPS_MISSION = (
    "id",
    "organisation_id",
    "offre_id",
    "reference",
    "titre",
    "date_debut",
    "date_fin",
    "lieu",
    "statut",
)
_CHAMPS_EQUIPE = ("id", "nom", "prenom", "email", "telephone", "profil", "statut")
_CHAMPS_DOCUMENT = (
    "id",
    "nom",
    "type_document",
    "mime_type",
    "version",
    "statut",
    "organisation_id",
    "appel_a_proposition_id",
    "offre_id",
    "mission_id",
    "equipe_id",
)


def _limite(payload: dict[str, Any]) -> int:
    limite = payload.get("limite", 20)
    return max(1, min(int(limite), LIMITE_MAX_RECHERCHE))


def rechercher_organisations(session: Session, payload: dict[str, Any]) -> dict[str, Any]:
    """Organisations dont le nom contient la recherche (insensible à la casse)."""
    terme = (payload.get("recherche") or "").strip()
    stmt = select(Organisation).order_by(Organisation.nom)
    if terme:
        stmt = stmt.where(Organisation.nom.ilike(f"%{terme}%"))
    organisations = list(session.scalars(stmt.limit(_limite(payload))))
    return {
        "organisations": [serialiser_entite(org, *_CHAMPS_ORGANISATION) for org in organisations]
    }


class RechercheAppelsAPropositionInput(PageSearchInput):
    """Filtres contrôlés des appels à proposition (aucun SQL libre)."""

    organisation_id: UUID | None = None
    statut: str | None = Field(default=None, max_length=50)


def rechercher_appels_a_proposition(session: Session, payload: dict[str, Any]) -> dict[str, Any]:
    """Appels à proposition filtrés (organisation, statut) + lots rattachés."""
    filtres = RechercheAppelsAPropositionInput.model_validate(payload)
    appels = AppelAPropositionRepository(session)
    if filtres.organisation_id is not None:
        stmt = select(AppelAProposition).where(
            AppelAProposition.organisation_id == filtres.organisation_id
        )
        trouves = list(session.scalars(stmt.limit(filtres.limite)))
    elif filtres.statut is not None:
        trouves = appels.list_by_statut(filtres.statut)[: filtres.limite]
    else:
        stmt = select(AppelAProposition).order_by(AppelAProposition.date_reception.desc())
        trouves = list(session.scalars(stmt.limit(filtres.limite)))
    if filtres.recherche:
        terme = filtres.recherche.strip().lower()
        trouves = [
            appel
            for appel in trouves
            if terme in (appel.titre or "").lower() or terme in (appel.reference or "").lower()
        ]
    return {
        "appels_a_proposition": [
            {
                **serialiser_entite(appel, *_CHAMPS_APPEL),
                "lots": [serialiser_entite(lot, *_CHAMPS_LOT) for lot in appel.lots],
            }
            for appel in trouves
        ]
    }


class RechercheMissionsInput(PageSearchInput):
    """Filtres contrôlés des missions."""

    organisation_id: UUID | None = None
    statut: str | None = Field(default=None, max_length=50)


def rechercher_missions(session: Session, payload: dict[str, Any]) -> dict[str, Any]:
    """Missions filtrées (statut, organisation) + sessions et effectifs."""
    filtres = RechercheMissionsInput.model_validate(payload)
    if filtres.statut is not None:
        trouvees = MissionRepository(session).list_by_statut(filtres.statut)
    elif filtres.organisation_id is not None:
        stmt = select(Mission).where(Mission.organisation_id == filtres.organisation_id)
        trouvees = list(session.scalars(stmt))
    else:
        stmt = select(Mission).order_by(Mission.date_debut.desc())
        trouvees = list(session.scalars(stmt))
    if filtres.recherche:
        terme = filtres.recherche.strip().lower()
        trouvees = [
            mission
            for mission in trouvees
            if terme in (mission.titre or "").lower() or terme in (mission.reference or "").lower()
        ]
    trouvees = trouvees[: filtres.limite]
    return {
        "missions": [
            {
                **serialiser_entite(mission, *_CHAMPS_MISSION),
                "nb_sessions": len(mission.sessions),
                "nb_affectations": len(mission.affectations),
            }
            for mission in trouvees
        ]
    }


def rechercher_equipes(session: Session, payload: dict[str, Any]) -> dict[str, Any]:
    """Vivier : nom/prénom/profil (insensible à la casse). Le contenu des CV
    n'est jamais inclus — seuls les métadonnées des documents ``cv`` le sont
    via ``documents_d_equipe`` (principe de minimisation)."""
    terme = (payload.get("recherche") or "").strip()
    stmt = select(Equipe).order_by(Equipe.nom, Equipe.prenom)
    if terme:
        motif = f"%{terme}%"
        stmt = stmt.where(
            Equipe.nom.ilike(motif) | Equipe.prenom.ilike(motif) | Equipe.profil.ilike(motif)
        )
    equipes = list(session.scalars(stmt.limit(_limite(payload))))
    return {
        "equipes": [
            {
                **serialiser_entite(equipe, *_CHAMPS_EQUIPE),
                "nb_affectations": len(equipe.affectations),
            }
            for equipe in equipes
        ]
    }


class RechercheDocumentsInput(PageSearchInput):
    """Filtres contrôlés des documents : une seule ancre métier à la fois
    (même règle que l'API : ``lister_pour`` exige un filtre unique)."""

    type_document: str | None = Field(default=None, max_length=50)
    organisation_id: UUID | None = None
    appel_a_proposition_id: UUID | None = None
    offre_id: UUID | None = None
    mission_id: UUID | None = None
    equipe_id: UUID | None = None


def rechercher_documents(session: Session, payload: dict[str, Any]) -> dict[str, Any]:
    """Documents par ancre métier (une seule) et type. Seules les métadonnées
    sont renvoyées — jamais le contenu ni le chemin physique (le contenu passe
    par ``read_document``, soumis à permission)."""
    filtres = RechercheDocumentsInput.model_validate(payload)
    ancres = {
        "organisation_id": filtres.organisation_id,
        "appel_a_proposition_id": filtres.appel_a_proposition_id,
        "offre_id": filtres.offre_id,
        "mission_id": filtres.mission_id,
        "equipe_id": filtres.equipe_id,
    }
    actives = {nom: valeur for nom, valeur in ancres.items() if valeur is not None}
    if len(actives) > 1:
        raise BusinessValidationError(
            "Une seule ancre métier à la fois pour la recherche de documents",
            details={"ancres_fournies": sorted(actives)},
        )
    stmt = select(Document).order_by(Document.version.desc())
    if filtres.type_document is not None:
        stmt = stmt.where(Document.type_document == filtres.type_document)
    for nom, valeur in actives.items():
        stmt = stmt.where(getattr(Document, nom) == valeur)
    if filtres.recherche:
        stmt = stmt.where(Document.nom.ilike(f"%{filtres.recherche.strip()}%"))
    documents = list(session.scalars(stmt.limit(filtres.limite)))
    return {"documents": [serialiser_entite(document, *_CHAMPS_DOCUMENT) for document in documents]}


def compter_missions_par_statut(session: Session, payload: dict[str, Any]) -> dict[str, Any]:
    """Agrégation déterministe : nombre de missions par statut.

    Le service calcule, le LLM explique (instruction/10 §9) : aucun chiffre
    de KPI ne naît d'une sortie de modèle.
    """
    _ = payload  # aucun paramètre : périmètre fixé, pas de SQL libre
    lignes = session.execute(
        select(Mission.statut, func.count(Mission.id)).group_by(Mission.statut)
    ).all()
    par_statut = {str(statut): int(nombre) for statut, nombre in lignes}
    return {"missions_par_statut": par_statut, "total": sum(par_statut.values())}


class StatistiquesSessionInput(BaseModel):
    """Session cible des statistiques de présence, et jour facultatif.

    Sans ``date``, tous les jours pointés sont comptés ; avec ``date``, la
    question devient « combien de présents le 03/09 ? ».
    """

    session_id: UUID
    date: DateType | None = None  # alias : le champ « date » masque le type


def compter_beneficiaires_par_session(session: Session, payload: dict[str, Any]) -> dict[str, Any]:
    """Inscrits d'une session et **pointages** par valeur de présence (règle 24/09).

    La présence est datée : le comptage porte donc sur les pointages, pas sur
    les personnes — une session de cinq jours produit cinq pointages par
    personne présente. ``inscrits`` reste le nombre d'inscriptions, et
    ``non_pointe`` le nombre d'inscrits sans aucun pointage (ou sans pointage
    du jour demandé) : c'est la seule lecture honnête d'un « non pointé ».

    Répond à « combien de présents ? » sans exposer les données personnelles
    des bénéficiaires au-delà du comptage.
    """
    cible = StatistiquesSessionInput.model_validate(payload)
    if session.get(SessionFormation, cible.session_id) is None:
        raise NotFoundError(f"Session {cible.session_id} introuvable")

    inscrits = int(
        session.scalar(
            select(func.count(Participation.id)).where(
                Participation.session_id == cible.session_id
            )
        )
        or 0
    )
    par_jour = [] if cible.date is None else [Presence.date == cible.date]
    lignes = session.execute(
        select(Presence.presence, func.count(Presence.id))
        .join(Participation, Presence.participation_id == Participation.id)
        .where(Participation.session_id == cible.session_id, *par_jour)
        .group_by(Presence.presence)
    ).all()
    par_presence = {str(valeur or "sans_statut"): int(n) for valeur, n in lignes}
    pointees = int(
        session.scalar(
            select(func.count(func.distinct(Presence.participation_id)))
            .join(Participation, Presence.participation_id == Participation.id)
            .where(Participation.session_id == cible.session_id, *par_jour)
        )
        or 0
    )
    return {
        "session_id": str(cible.session_id),
        "date": cible.date.isoformat() if cible.date is not None else None,
        "inscrits": inscrits,
        "pointages": sum(par_presence.values()),
        "par_presence": par_presence,
        "non_pointe": max(inscrits - pointees, 0),
    }


class FenetreMoisInput(BaseModel):
    """Fenêtre glissante (en mois) d'une agrégation mensuelle — paramètre borné."""

    mois: int = Field(default=12, ge=1, le=24)


def compter_missions_par_organisation(session: Session, payload: dict[str, Any]) -> dict[str, Any]:
    """Agrégation déterministe : missions par organisation cliente.

    Le décompte est groupé par nom d'organisation (jointure explicite) : il
    répond à « quelles organisations génèrent le plus de missions ? ». Les
    organisations sans mission n'apparaissent pas — un zéro implicite n'est
    pas une donnée, c'est une absence.
    """
    _ = payload  # aucun paramètre : périmètre fixé, pas de SQL libre
    lignes = session.execute(
        select(Organisation.nom, func.count(Mission.id))
        .join(Mission, Mission.organisation_id == Organisation.id)
        .group_by(Organisation.nom)
        .order_by(func.count(Mission.id).desc(), Organisation.nom)
    ).all()
    par_organisation = {str(nom): int(n) for nom, n in lignes}
    return {
        "missions_par_organisation": par_organisation,
        "total": sum(par_organisation.values()),
    }


def _fenetre_mois(mois: int) -> list[str]:
    """Clés ``AAAA-MM`` des ``mois`` derniers mois glissants (mois courant inclus)."""
    aujourd_hui = DateType.today()
    cles: list[str] = []
    annee, mois_courant = aujourd_hui.year, aujourd_hui.month
    for _ in range(mois):
        cles.append(f"{annee:04d}-{mois_courant:02d}")
        mois_courant -= 1
        if mois_courant == 0:
            mois_courant = 12
            annee -= 1
    cles.reverse()
    return cles


def compter_sessions_par_mois(session: Session, payload: dict[str, Any]) -> dict[str, Any]:
    """Agrégation déterministe : sessions lancées par mois (fenêtre glissante).

    Le mois est celui de ``date_debut`` de la session. Le regroupement mensuel
    est fait **en Python** depuis des comptages par date : aucune fonction SQL
    spécifique à un dialecte (SQLite en test, PostgreSQL en production), et le
    périmètre reste une lecture bornée.
    """
    entree = FenetreMoisInput.model_validate(payload)
    lignes = session.execute(
        select(SessionFormation.date_debut, func.count(SessionFormation.id))
        .where(SessionFormation.date_debut.is_not(None))
        .group_by(SessionFormation.date_debut)
    ).all()
    fenetre = _fenetre_mois(entree.mois)
    par_mois = {cle: 0 for cle in fenetre}
    total = 0
    for date_debut, nombre in lignes:
        cle = date_debut.strftime("%Y-%m")
        total += int(nombre)
        if cle in par_mois:
            par_mois[cle] += int(nombre)
    return {
        "sessions_par_mois": par_mois,
        "total_sessions_datees": total,
        "hors_fenetre": total - sum(par_mois.values()),
    }


def compter_beneficiaires_par_mois(session: Session, payload: dict[str, Any]) -> dict[str, Any]:
    """Agrégation déterministe : bénéficiaires **distincts** formés par mois.

    Un bénéficiaire inscrit à plusieurs sessions du même mois n'est compté
    qu'une fois pour ce mois ; s'il est formé sur deux mois, il compte dans
    chacun. Le dédoublonnage par mois est fait en Python depuis les paires
    (date de session, bénéficiaire) — exact et portable, contrairement à une
    somme de comptages par jour qui sur-compterait.
    """
    entree = FenetreMoisInput.model_validate(payload)
    paires = session.execute(
        select(SessionFormation.date_debut, Participation.beneficiaire_id)
        .join(Participation, Participation.session_id == SessionFormation.id)
        .where(SessionFormation.date_debut.is_not(None))
        .distinct()
    ).all()
    fenetre = _fenetre_mois(entree.mois)
    par_mois: dict[str, set[UUID]] = {cle: set() for cle in fenetre}
    total_ids: set[UUID] = set()
    for date_debut, beneficiaire_id in paires:
        cle = date_debut.strftime("%Y-%m")
        total_ids.add(beneficiaire_id)
        if cle in par_mois:
            par_mois[cle].add(beneficiaire_id)
    return {
        "beneficiaires_par_mois": {
            cle: len(ids) for cle, ids in par_mois.items()
        },
        "total_beneficiaires_formes": len(total_ids),
    }


def compter_affectations_par_role(session: Session, payload: dict[str, Any]) -> dict[str, Any]:
    """Affectations **approuvées** par rôle (vocabulaire réel, pas inventé).

    Répond à « combien de missions ont eu un assistant logistique ? » en
    comptant les rôles effectivement approuvés — jamais de vocabulaire figé.
    """
    _ = payload  # aucun paramètre : périmètre fixé, pas de SQL libre
    lignes = session.execute(
        select(AffectationEquipe.role_dans_mission, func.count(AffectationEquipe.id))
        .where(AffectationEquipe.statut == StatutAffectation.APPROUVEE.value)
        .group_by(AffectationEquipe.role_dans_mission)
    ).all()
    par_role = {str(role): int(n) for role, n in lignes}
    return {"affectations_approuvees_par_role": par_role, "total": sum(par_role.values())}


__all__ = [
    "compter_affectations_par_role",
    "compter_beneficiaires_par_mois",
    "compter_beneficiaires_par_session",
    "compter_missions_par_organisation",
    "compter_missions_par_statut",
    "compter_sessions_par_mois",
    "rechercher_appels_a_proposition",
    "rechercher_documents",
    "rechercher_equipes",
    "rechercher_missions",
    "rechercher_organisations",
]
