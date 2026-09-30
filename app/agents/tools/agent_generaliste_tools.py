"""Tools de ``agent_generaliste_readonly`` (clinrules 06, refactor outils).

Lieu unique du périmètre outils de l'agent généraliste : **lecture seule sur
toutes les tables métier** (matrice validée), via des requêtes nommées et
bornées — jamais de SQL libre (clinrules 08). Le kit est construit en
``readonly=True`` : une opération de mutation lèverait
``PermissionDeniedError`` à la construction.

Tools :

- ``search_organisations`` — organisations par nom ;
- ``search_appels_a_proposition`` — appels + lots ;
- ``search_lots`` — lots par appel / numéro ;
- ``search_offres`` — offres de formation (+ budget total) ;
- ``search_missions`` — missions + effectifs ;
- ``search_sessions`` — sessions d'une mission ;
- ``search_beneficiaires`` — bénéficiaires par nom (données minimisées) ;
- ``search_equipes`` — vivier (CV exclus) ;
- ``search_affectations`` — affectations d'une mission ;
- ``search_documents`` — documents par ancre métier (métadonnées seules) ;
- ``read_document`` (fourni par ``app.tools.documents``) ;
- ``query_analytics`` — agrégations déterministes nommées.
"""

from __future__ import annotations

from datetime import date as DateType
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agents.consultation import (
    compter_affectations_par_role,
    compter_beneficiaires_par_mois,
    compter_beneficiaires_par_session,
    compter_missions_par_organisation,
    compter_missions_par_statut,
    compter_sessions_par_mois,
    rechercher_appels_a_proposition,
    rechercher_documents,
    rechercher_equipes,
    rechercher_missions,
    rechercher_organisations,
)
from app.agents.hitl import HitlReponse, HitlScenario
from app.agents.toolkit import (
    AgentTools,
    OperationSpec,
    PageSearchInput,
    serialiser_entite,
)
from app.core.errors import ValidationError as BusinessValidationError
from app.domain.execution import (
    AffectationEquipe,
    Beneficiaire,
    SessionFormation,
)
from app.domain.organization import Lot, Offre

AGENT_ID = "agent_generaliste_readonly"
TASK_TYPE = "consultation_generale"
READ_DOCUMENT_TOOL = "read_document"

#: Agrégations autorisées à ``query_analytics`` (périmètre fermé, pas de SQL).
ANALYTIQUES_AUTORISEES = (
    "missions_par_statut",
    "missions_par_organisation",
    "sessions_par_mois",
    "beneficiaires_par_mois",
    "beneficiaires_par_session",
    "affectations_par_role",
    "offres_par_statut",
    "beneficiaires_total",
)

NOMS_TOOLS: tuple[str, ...] = (
    "search_organisations",
    "search_appels_a_proposition",
    "search_lots",
    "search_offres",
    "search_missions",
    "search_sessions",
    "search_beneficiaires",
    "search_equipes",
    "search_affectations",
    "search_documents",
    READ_DOCUMENT_TOOL,
    "query_analytics",
    "langsearch_web_search",
)


class LotSearchInput(PageSearchInput):
    """Filtres contrôlés des lots."""

    appel_a_proposition_id: UUID | None = None


class OffreSearchInput(PageSearchInput):
    """Filtres contrôlés des offres de formation."""

    statut: str | None = Field(default=None, max_length=50)
    organisation_id: UUID | None = None


class SessionSearchInput(PageSearchInput):
    """Sessions : par mission, ou toutes."""

    mission_id: UUID | None = None


class BeneficiaireSearchInput(PageSearchInput):
    """Bénéficiaires : recherche par nom/prénom (données minimisées)."""


class AffectationSearchInput(PageSearchInput):
    """Affectations : une mission à la fois."""

    mission_id: UUID


class AnalyticsInput(BaseModel):
    """Demande d'agrégation : nom fermé + paramètres validés."""

    metrique: str = Field(description="Nom de l'agrégation (voir ANALYTIQUES_AUTORISEES).")
    session_id: UUID | None = Field(
        default=None, description="Requis pour beneficiaires_par_session."
    )
    date: DateType | None = Field(  # alias : le champ « date » masque le type
        default=None,
        description=(
            "Jour précis (AAAA-MM-JJ) pour beneficiaires_par_session ; "
            "absent = tous les jours pointés."
        ),
    )
    mois: int = Field(
        default=12,
        ge=1,
        le=24,
        description=(
            "Fenêtre glissante en mois pour les agrégations mensuelles "
            "(sessions_par_mois, beneficiaires_par_mois)."
        ),
    )
    limite: int = Field(default=20, ge=1, le=100)


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
_CHAMPS_SESSION = ("id", "mission_id", "date_debut", "date_fin", "lieu", "theme", "statut")
_CHAMPS_BENEFICIAIRE = ("id", "nom", "prenom", "organisation_origine", "identifiant_externe")
_CHAMPS_AFFECTATION = (
    "id",
    "mission_id",
    "equipe_id",
    "role_dans_mission",
    "statut",
    "source_affectation",
)


def _limite(payload: dict[str, Any]) -> int:
    limite = payload.get("limite", 20)
    return max(1, min(int(limite), 100))


def rechercher_lots(session: Session, payload: dict[str, Any]) -> dict[str, Any]:
    """Lots d'un appel (ou tous), ordonnés par appel puis numéro."""
    filtres = LotSearchInput.model_validate(payload)
    stmt = select(Lot).order_by(Lot.appel_a_proposition_id, Lot.numero)
    if filtres.appel_a_proposition_id is not None:
        stmt = stmt.where(Lot.appel_a_proposition_id == filtres.appel_a_proposition_id)
    if filtres.recherche:
        motif = f"%{filtres.recherche.strip()}%"
        stmt = stmt.where(Lot.titre.ilike(motif) | Lot.numero.ilike(motif))
    lots = list(session.scalars(stmt.limit(filtres.limite)))
    return {"lots": [serialiser_entite(lot, *_CHAMPS_LOT) for lot in lots]}


def rechercher_offres(session: Session, payload: dict[str, Any]) -> dict[str, Any]:
    """Offres de formation (statut, organisation) + total budgétaire connu."""
    filtres = OffreSearchInput.model_validate(payload)
    stmt = select(Offre).order_by(Offre.reference)
    if filtres.statut is not None:
        stmt = stmt.where(Offre.statut == filtres.statut)
    if filtres.organisation_id is not None:
        stmt = stmt.where(Offre.organisation_id == filtres.organisation_id)
    if filtres.recherche:
        motif = f"%{filtres.recherche.strip()}%"
        stmt = stmt.where(Offre.titre.ilike(motif) | Offre.reference.ilike(motif))
    offres = list(session.scalars(stmt.limit(filtres.limite)))
    return {
        "offres": [
            {
                **serialiser_entite(offre, *_CHAMPS_OFFRE),
                "nb_missions": len(offre.missions),
            }
            for offre in offres
        ]
    }


def rechercher_sessions(session: Session, payload: dict[str, Any]) -> dict[str, Any]:
    """Sessions d'une mission (ou toutes), par dates."""
    filtres = SessionSearchInput.model_validate(payload)
    stmt = select(SessionFormation).order_by(SessionFormation.date_debut)
    if filtres.mission_id is not None:
        stmt = stmt.where(SessionFormation.mission_id == filtres.mission_id)
    sessions = list(session.scalars(stmt.limit(filtres.limite)))
    return {"sessions": [serialiser_entite(s, *_CHAMPS_SESSION) for s in sessions]}


def rechercher_beneficiaires(session: Session, payload: dict[str, Any]) -> dict[str, Any]:
    """Bénéficiaires par nom/prénom. Contact exposé en dernier recours : le
    champ est minimisé (matrice d'accès : lecture autorisée, minimisation
    appliquée — aucune donnée bancaire ou sensible au-delà du contact)."""
    filtres = BeneficiaireSearchInput.model_validate(payload)
    stmt = select(Beneficiaire).order_by(Beneficiaire.nom, Beneficiaire.prenom)
    if filtres.recherche:
        motif = f"%{filtres.recherche.strip()}%"
        stmt = stmt.where(Beneficiaire.nom.ilike(motif) | Beneficiaire.prenom.ilike(motif))
    beneficiaires = list(session.scalars(stmt.limit(filtres.limite)))
    return {
        "beneficiaires": [
            serialiser_entite(b, *_CHAMPS_BENEFICIAIRE) for b in beneficiaires
        ]
    }


def rechercher_affectations(session: Session, payload: dict[str, Any]) -> dict[str, Any]:
    """Affectations (mission ↔ équipe ↔ rôle) d'une mission."""
    filtres = AffectationSearchInput.model_validate(payload)
    stmt = select(AffectationEquipe).where(
        AffectationEquipe.mission_id == filtres.mission_id
    )
    affectations = list(session.scalars(stmt.limit(filtres.limite)))
    return {
        "affectations": [
            serialiser_entite(a, *_CHAMPS_AFFECTATION) for a in affectations
        ]
    }


def compter_offres_par_statut(session: Session, payload: dict[str, Any]) -> dict[str, Any]:
    """Agrégation déterministe : offres de formation par statut."""
    _ = payload
    lignes = session.execute(
        select(Offre.statut, func.count(Offre.id)).group_by(
            Offre.statut
        )
    ).all()
    par_statut = {str(statut): int(nombre) for statut, nombre in lignes}
    return {"offres_par_statut": par_statut, "total": sum(par_statut.values())}


def compter_beneficiaires_total(session: Session, payload: dict[str, Any]) -> dict[str, Any]:
    """Agrégation déterministe : nombre total de bénéficiaires enregistrés."""
    _ = payload
    total = int(session.scalar(select(func.count(Beneficiaire.id))) or 0)
    return {"beneficiaires_total": total}


def executer_analytique(session: Session, payload: dict[str, Any]) -> dict[str, Any]:
    """Route ``query_analytics`` vers l'agrégation nommée (périmètre fermé)."""
    entree = AnalyticsInput.model_validate(payload)
    if entree.metrique not in ANALYTIQUES_AUTORISEES:
        raise BusinessValidationError(
            f"Métrique inconnue : {entree.metrique!r}",
            details={"metriques_autorisees": sorted(ANALYTIQUES_AUTORISEES)},
        )
    if entree.metrique == "missions_par_statut":
        return compter_missions_par_statut(session, {})
    if entree.metrique == "missions_par_organisation":
        return compter_missions_par_organisation(session, {})
    if entree.metrique == "affectations_par_role":
        return compter_affectations_par_role(session, {})
    if entree.metrique == "offres_par_statut":
        return compter_offres_par_statut(session, {})
    if entree.metrique == "beneficiaires_total":
        return compter_beneficiaires_total(session, {})
    if entree.metrique == "sessions_par_mois":
        return compter_sessions_par_mois(session, {"mois": entree.mois})
    if entree.metrique == "beneficiaires_par_mois":
        return compter_beneficiaires_par_mois(session, {"mois": entree.mois})
    if entree.session_id is None:
        raise BusinessValidationError("session_id requis pour beneficiaires_par_session")
    return compter_beneficiaires_par_session(
        session,
        {
            "session_id": str(entree.session_id),
            "date": entree.date.isoformat() if entree.date is not None else None,
        },
    )


def construire_tools_generaliste(
    definition: Any,
    session_factory: Any,
    *,
    read_document_tool: Any = None,
) -> tuple[Any, ...]:
    """Tools du généraliste : kit readonly + lecture documentaire + recherche web.

    Args:
        definition: contrat de l'agent (allow-list appliquée par le harnais).
        session_factory: fabrique de sessions des tools (1 transaction par appel).
        read_document_tool: tool ``read_document`` câblé par l'appelant
            (indépendance du câblage documentaire).
    """
    kit = AgentTools(definition, readonly=True, session_factory=session_factory)
    kit.enregistrer(OperationSpec(
        name="search_organisations",
        description="Recherche des organisations clientes par nom.",
        input_schema=PageSearchInput,
        run=rechercher_organisations,
    ))
    kit.enregistrer(OperationSpec(
        name="search_appels_a_proposition",
        description="Appels à proposition (organisation, statut, texte) + lots.",
        input_schema=PageSearchInput,
        run=rechercher_appels_a_proposition,
    ))
    kit.enregistrer(OperationSpec(
        name="search_lots",
        description="Lots d'un appel à proposition (ou tous) : numéro, titre, zone.",
        input_schema=LotSearchInput,
        run=rechercher_lots,
    ))
    kit.enregistrer(OperationSpec(
        name="search_offres",
        description="Offres de formation (statut, organisation, texte) + missions liées.",
        input_schema=OffreSearchInput,
        run=rechercher_offres,
    ))
    kit.enregistrer(OperationSpec(
        name="search_missions",
        description="Missions (statut, organisation, texte) + effectifs.",
        input_schema=PageSearchInput,
        run=rechercher_missions,
    ))
    kit.enregistrer(OperationSpec(
        name="search_sessions",
        description="Sessions de formation (par mission ou toutes).",
        input_schema=SessionSearchInput,
        run=rechercher_sessions,
    ))
    kit.enregistrer(OperationSpec(
        name="search_beneficiaires",
        description="Bénéficiaires par nom/prénom (données minimisées).",
        input_schema=BeneficiaireSearchInput,
        run=rechercher_beneficiaires,
    ))
    kit.enregistrer(OperationSpec(
        name="search_equipes",
        description="Vivier (nom, prénom, profil). Contenu des CV exclu.",
        input_schema=PageSearchInput,
        run=rechercher_equipes,
    ))
    kit.enregistrer(OperationSpec(
        name="search_affectations",
        description="Affectations mission ↔ équipe ↔ rôle d'une mission.",
        input_schema=AffectationSearchInput,
        run=rechercher_affectations,
    ))
    kit.enregistrer(OperationSpec(
        name="search_documents",
        description="Documents (une ancre métier, type). Métadonnées seules.",
        input_schema=PageSearchInput,
        run=rechercher_documents,
    ))
    kit.enregistrer(OperationSpec(
        name="query_analytics",
        description=(
            "Agrégation déterministe nommée : " + ", ".join(ANALYTIQUES_AUTORISEES) +
            ". Le service calcule."
        ),
        input_schema=AnalyticsInput,
        run=executer_analytique,
    ))

    from app.tools.web import attacher_web_search

    outils = list(kit.construire())
    if read_document_tool is not None:
        outils.append(read_document_tool)
    return attacher_web_search(definition, tuple(outils))


# Scénarios HITL de l'agent (exposés via le contrat, dans generaliste_readonly).
SCENARIOS_HITL = (
    HitlScenario(
        id="lecture_document_confidentiel",
        titre="Lire ce document et le résumer ?",
        contexte=(
            "L'agent est strictement READ-ONLY : il ne modifie rien. La "
            "lecture d'un document engage sa teneur dans la conversation."
        ),
        action=("lire le document autorisé et en produire un résumé fidèle"),
        reponses=(
            HitlReponse(
                valeur="lire_resumer",
                libelle="Lire et résumer",
                description="Le contenu autorisé est lu et synthétisé sans modification.",
            ),
            HitlReponse(
                valeur="lire_extraits",
                libelle="Extraits seulement",
                description="Seuls les passages pertinents sont cités, sans résumé global.",
            ),
            HitlReponse(
                valeur="annuler",
                libelle="Ne pas lire",
                description="Aucune lecture du document.",
            ),
        ),
    ),
)


__all__ = [
    "AGENT_ID",
    "ANALYTIQUES_AUTORISEES",
    "NOMS_TOOLS",
    "READ_DOCUMENT_TOOL",
    "TASK_TYPE",
    "SCENARIOS_HITL",
    "AffectationSearchInput",
    "AnalyticsInput",
    "BeneficiaireSearchInput",
    "LotSearchInput",
    "OffreSearchInput",
    "SessionSearchInput",
    "construire_tools_generaliste",
    "executer_analytique",
    "rechercher_affectations",
    "rechercher_lots",
]
