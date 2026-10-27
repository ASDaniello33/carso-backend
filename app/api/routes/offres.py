"""Routes—offres génériques (P2) et budgets rattachés.

Contrôleurs minces sur ``OffreService`` ; les décisions humaines (publication,
archivage, budget) écrivent Approbation + AuditEvent dans la transaction de la
requête. Une offre répond toujours à ``Appel + Lot`` et porte un type
(technique, financière, autre).
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from app.api.deps import CurrentUser, DbSession, get_current_user
from app.api.permissions import InterneUser, PerimetreDep, exiger_interne
from app.api.routes.suppression import enregistrer_suppression
from app.api.schemas import (
    BudgetRead,
    BudgetTotalRead,
    DecisionCreate,
    DocumentRead,
    LigneBudgetCreate,
    LigneBudgetRead,
    OffreCreate,
    OffreRead,
    OffreUpdate,
)
from app.application.dto import LigneBudgetInput, OffreUpdateInput
from app.application.services import DocumentService, OffreService
from app.application.trace import Decision
from app.domain.document import Document
from app.domain.organization import Budget, LigneBudget, Offre

router = APIRouter(
    prefix="/offres",
    tags=["offres"],
    dependencies=[Depends(get_current_user)],
)


@router.get(
    "/", response_model=list[OffreRead], dependencies=[Depends(exiger_interne)]
)
def list_offres(
    session: DbSession,
    statut: Annotated[
        str | None, Query(description="Filtre facultatif sur le statut de l'offre")
    ] = None,
    type: Annotated[
        str | None,
        Query(description="Filtre facultatif sur le type (technique, financière, autre)"),
    ] = None,
) -> list[Offre]:
    """Liste des offres, éventuellement filtrée par statut et/ou type."""
    return OffreService(session).lister(statut=statut, type=type)


@router.get(
    "/lot/{lot_id}",
    response_model=list[OffreRead],
    dependencies=[Depends(exiger_interne)],
)
def list_offres_du_lot(lot_id: UUID, session: DbSession) -> list[Offre]:
    """Offres d'un lot : au plus une par type (technique + financière)."""
    return OffreService(session).lister_pour_lot(lot_id)


@router.post(
    "/{offre_id}/document",
    response_model=DocumentRead,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(exiger_interne)],
)
def generer_document_offre(
    offre_id: UUID,
    session: DbSession,
    utilisateur: InterneUser,
) -> Document:
    """Génère le DOCX de l'offre (données réelles) comme **proposition**.

    Le document naît en ``proposed`` rattaché à l'offre : l'humain le télécharge,
    le révise, puis l'approuve ou demande une régénération (version suivante).
    Chemin déterministe, sans dépendance LLM.
    """
    return DocumentService(session, actor_id=str(utilisateur.id)).generer_document_offre(
        offre_id
    )


@router.get("/{offre_id}", response_model=OffreRead)
def get_offre(
    offre_id: UUID,
    session: DbSession,
    perimetre: PerimetreDep,
) -> Offre:
    """Consultation d'une offre (404 si absente ou hors du dossier de ses missions)."""
    return OffreService(session, perimetre=perimetre).obtenir(offre_id)


@router.post(
    "/",
    response_model=OffreRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(exiger_interne)],
)
def create_offre(
    payload: OffreCreate,
    session: DbSession,
    utilisateur: InterneUser,
) -> Offre:
    """Crée l'offre en brouillon pour un lot et un type donnés."""
    return OffreService(session, actor_id=str(utilisateur.id)).creer_brouillon(
        payload.lot_id,
        payload.reference,
        payload.titre,
        type=payload.type,
        appel_id=payload.appel_id,
        modele_document_id=payload.modele_document_id,
    )


@router.patch(
    "/{offre_id}", response_model=OffreRead, dependencies=[Depends(exiger_interne)]
)
def update_offre(
    offre_id: UUID,
    payload: OffreUpdate,
    session: DbSession,
    utilisateur: InterneUser,
) -> Offre:
    """Modification d'identité : titre, type, échéances prévues.

    Le lot et l'appel ne bougent jamais (une offre répond à ``Appel + Lot``).
    Une offre archivée est figée, et le type reste unique par lot.
    """
    return OffreService(session, actor_id=str(utilisateur.id)).modifier(
        offre_id,
        OffreUpdateInput(
            titre=payload.titre,
            type=payload.type,
            date_debut_prevue=payload.date_debut_prevue,
            date_fin_prevue=payload.date_fin_prevue,
        ),
    )


@router.post(
    "/{offre_id}/revue", response_model=OffreRead, dependencies=[Depends(exiger_interne)]
)
def submit_revue(
    offre_id: UUID,
    session: DbSession,
    utilisateur: InterneUser,
) -> Offre:
    """brouillon → en_revue : mise à disposition pour décision humaine."""
    return OffreService(session, actor_id=str(utilisateur.id)).soumettre_revue(offre_id)


@router.post(
    "/{offre_id}/publication",
    response_model=OffreRead,
    dependencies=[Depends(exiger_interne)],
)
def publish_offre(
    offre_id: UUID,
    payload: DecisionCreate,
    user: CurrentUser,
    session: DbSession,
) -> Offre:
    """Décision humaine de publication : en_revue → approuve, ``approved_at``
    horodaté, Approbation + AuditEvent dans la transaction."""
    return OffreService(session, actor_id=str(user.id)).publier(
        offre_id, Decision(decided_by=str(user.id), reason=payload.reason)
    )


@router.post(
    "/{offre_id}/archivage", response_model=OffreRead, dependencies=[Depends(exiger_interne)]
)
def archive_offre(
    offre_id: UUID,
    payload: DecisionCreate,
    user: CurrentUser,
    session: DbSession,
) -> Offre:
    """Archivage tracé (décision humaine)."""
    return OffreService(session, actor_id=str(user.id)).archiver(
        offre_id, Decision(decided_by=str(user.id), reason=payload.reason)
    )


@router.post(
    "/{offre_id}/budgets/{budget_id}/lignes",
    response_model=LigneBudgetRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(exiger_interne)],
)
def add_ligne_budget(
    budget_id: UUID,
    payload: LigneBudgetCreate,
    session: DbSession,
    utilisateur: InterneUser,
) -> LigneBudget:
    """Ajoute une ligne à un budget non approuvé. ``cout_total`` est recalculé
    par la règle déterministe — le champ n'existe pas dans la requête (règle 7)."""
    service = OffreService(session, actor_id=str(utilisateur.id))
    return service.ajouter_ligne_budget(
        budget_id,
        LigneBudgetInput(
            categorie=payload.categorie,
            quantite=payload.quantite,
            cout_unitaire=payload.cout_unitaire,
            description=payload.description,
            unite=payload.unite,
        ),
    )


@router.post(
    "/budgets/{budget_id}/approbation",
    response_model=BudgetRead,
    dependencies=[Depends(exiger_interne)],
)
def approve_budget(
    budget_id: UUID,
    payload: DecisionCreate,
    user: CurrentUser,
    session: DbSession,
) -> Budget:
    """Décision humaine d'approbation d'un budget proposé."""
    return OffreService(session, actor_id=str(user.id)).approuver_budget(
        budget_id, Decision(decided_by=str(user.id), reason=payload.reason)
    )


@router.get("/budgets/{budget_id}/total", response_model=BudgetTotalRead)
def get_budget_total(
    budget_id: UUID,
    session: DbSession,
    perimetre: PerimetreDep,
) -> BudgetTotalRead:
    """Total déterministe du budget, recalculé depuis les lignes (règle 7).

    Un formateur ne lit que le budget d'une mission affectée (dossier lié).
    """
    total = OffreService(session, perimetre=perimetre).total_budget(budget_id)
    return BudgetTotalRead(
        budget_id=total.budget_id,
        devise=total.devise,
        total=total.total,
        nb_lignes=total.nb_lignes,
    )


# --- Suppression cascadée (règle confirmée CARSO, 22/09) ----------------------
# Une offre emporte ses budgets, ses lignes, ses documents (fichiers compris) et
# les missions qu'elle alimente, avec leurs sessions et affectations.
enregistrer_suppression(router, "offres")
