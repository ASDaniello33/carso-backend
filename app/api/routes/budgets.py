"""Routes des budgets — création, lignes, soumission, approbation, reprise, export.

Pendant de ``BudgetService`` (``app/application/services/budget_service.py``).
Les montants ne sont **jamais** reçus du client : ``cout_total`` et le total sont
recalculés par le service (règle 7). Les écritures sont réservées à l'équipe
interne ; les lectures passent par le périmètre du compte (un formateur ne voit
que le budget des missions où il est affecté).
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, DbSession, get_current_user
from app.api.permissions import InterneUser, PerimetreDep, exiger_interne
from app.api.schemas import (
    BudgetCreate,
    BudgetDetailRead,
    BudgetRepriseCreate,
    BudgetResumeRead,
    BudgetTotalRead,
    DecisionCreate,
    LigneBudgetDetailRead,
    LigneBudgetUpdate,
)
from app.api.schemas.offre import LigneBudgetCreate
from app.application.dto import LigneBudgetInput
from app.application.perimetre import Perimetre
from app.application.services.budget_service import BudgetService
from app.application.trace import Decision
from app.core.errors import ValidationError
from app.domain.organization import LigneBudget

router = APIRouter(
    tags=["budgets"],
    dependencies=[Depends(get_current_user)],
)


def _service(session: Session, perimetre: Perimetre, user: CurrentUser) -> BudgetService:
    """Service budget du périmètre de la requête (lecture et écriture)."""
    return BudgetService(session, actor_id=str(user.id), perimetre=perimetre)


def _entree(payload: LigneBudgetCreate | LigneBudgetUpdate) -> LigneBudgetInput:
    """Traduit la saisie API vers le contrat applicatif (jamais ``cout_total``)."""
    return LigneBudgetInput(
        categorie=payload.categorie,
        quantite=payload.quantite,
        cout_unitaire=payload.cout_unitaire,
        description=payload.description,
        unite=payload.unite,
    )


def _detail(service: BudgetService, budget_id: UUID) -> BudgetDetailRead:
    """Vue détaillée d'un budget (lignes ordonnées + total recalculé)."""
    detail = service.lire_budget(budget_id)
    return BudgetDetailRead(
        budget_id=detail.budget_id,
        offre_id=detail.offre_id,
        mission_id=detail.mission_id,
        devise=detail.devise,
        statut=detail.statut,
        version=detail.version,
        lignes=[LigneBudgetDetailRead.model_validate(ligne) for ligne in detail.lignes],
        total=detail.total,
    )


@router.post(
    "/budgets",
    response_model=BudgetDetailRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(exiger_interne)],
)
def create_budget(
    payload: BudgetCreate,
    session: DbSession,
    perimetre: PerimetreDep,
    user: InterneUser,
) -> BudgetDetailRead:
    """Crée le budget d'une offre (chiffrage) ou d'une mission (exécution)."""
    service = _service(session, perimetre, user)
    budget = service.creer_budget(
        offre_id=payload.offre_id,
        mission_id=payload.mission_id,
        devise=payload.devise,
    )
    return _detail(service, budget.id)


@router.get("/budgets", response_model=list[BudgetResumeRead])
def list_budgets(
    session: DbSession,
    perimetre: PerimetreDep,
    user: CurrentUser,
    offre_id: Annotated[UUID | None, Query()] = None,
    mission_id: Annotated[UUID | None, Query()] = None,
) -> list[BudgetResumeRead]:
    """Budgets d'une offre ou d'une mission, version la plus récente d'abord."""
    if (offre_id is None) == (mission_id is None):
        raise ValidationError(
            "Fournir 'offre_id' OU 'mission_id' (un seul)",
            details={"offre_id": offre_id, "mission_id": mission_id},
        )
    service = _service(session, perimetre, user)
    resumes = (
        service.lister_budgets_offre(offre_id)
        if offre_id is not None
        else service.lister_budgets_mission(mission_id)
    )
    return [
        BudgetResumeRead(
            budget_id=resume.budget_id,
            offre_id=resume.offre_id,
            mission_id=resume.mission_id,
            devise=resume.devise,
            statut=resume.statut,
            version=resume.version,
            total=resume.total,
            nb_lignes=resume.nb_lignes,
        )
        for resume in resumes
    ]


@router.get("/budgets/{budget_id}", response_model=BudgetDetailRead)
def get_budget(
    budget_id: UUID,
    session: DbSession,
    perimetre: PerimetreDep,
    user: CurrentUser,
) -> BudgetDetailRead:
    """Budget complet : entête, lignes ordonnées, total recalculé."""
    return _detail(_service(session, perimetre, user), budget_id)


@router.get("/budgets/{budget_id}/total", response_model=BudgetTotalRead)
def get_budget_total(
    budget_id: UUID,
    session: DbSession,
    perimetre: PerimetreDep,
    user: CurrentUser,
) -> BudgetTotalRead:
    """Total déterministe du budget, recalculé depuis les lignes (règle 7)."""
    total = _service(session, perimetre, user).total_budget(budget_id)
    return BudgetTotalRead(
        budget_id=total.budget_id,
        devise=total.devise,
        total=total.total,
        nb_lignes=total.nb_lignes,
    )


@router.post(
    "/budgets/{budget_id}/lignes",
    response_model=LigneBudgetDetailRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(exiger_interne)],
)
def add_ligne(
    budget_id: UUID,
    payload: LigneBudgetCreate,
    session: DbSession,
    perimetre: PerimetreDep,
    user: InterneUser,
) -> LigneBudget:
    """Ajoute une ligne ; ``cout_total`` est recalculé (jamais fourni)."""
    return _service(session, perimetre, user).ajouter_ligne_budget(
        budget_id, _entree(payload)
    )


@router.patch(
    "/budgets/{budget_id}/lignes/{ligne_id}",
    response_model=LigneBudgetDetailRead,
    dependencies=[Depends(exiger_interne)],
)
def update_ligne(
    budget_id: UUID,
    ligne_id: UUID,
    payload: LigneBudgetUpdate,
    session: DbSession,
    perimetre: PerimetreDep,
    user: InterneUser,
) -> LigneBudget:
    """Corrige une ligne d'un budget non approuvé (geste tracé)."""
    return _service(session, perimetre, user).modifier_ligne_budget(
        budget_id, ligne_id, _entree(payload)
    )


@router.delete(
    "/budgets/{budget_id}/lignes/{ligne_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(exiger_interne)],
)
def delete_ligne(
    budget_id: UUID,
    ligne_id: UUID,
    session: DbSession,
    perimetre: PerimetreDep,
    user: InterneUser,
) -> Response:
    """Retire une ligne d'un budget non approuvé (geste tracé)."""
    _service(session, perimetre, user).supprimer_ligne_budget(budget_id, ligne_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/budgets/{budget_id}/soumission",
    response_model=BudgetDetailRead,
    dependencies=[Depends(exiger_interne)],
)
def submit_budget(
    budget_id: UUID,
    session: DbSession,
    perimetre: PerimetreDep,
    user: InterneUser,
) -> BudgetDetailRead:
    """``brouillon → propose`` : le budget entre dans la file de décision."""
    service = _service(session, perimetre, user)
    service.soumettre_budget(budget_id)
    return _detail(service, budget_id)


@router.post(
    "/budgets/{budget_id}/approbation",
    response_model=BudgetDetailRead,
    dependencies=[Depends(exiger_interne)],
)
def approve_budget(
    budget_id: UUID,
    payload: DecisionCreate,
    session: DbSession,
    perimetre: PerimetreDep,
    user: InterneUser,
) -> BudgetDetailRead:
    """Décision humaine d'approbation (``propose → approuve``, tracée)."""
    service = _service(session, perimetre, user)
    service.approuver_budget(
        budget_id, Decision(decided_by=str(user.id), reason=payload.reason)
    )
    return _detail(service, budget_id)


@router.post(
    "/budgets/{budget_id}/version",
    response_model=BudgetDetailRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(exiger_interne)],
)
def new_version(
    budget_id: UUID,
    session: DbSession,
    perimetre: PerimetreDep,
    user: InterneUser,
) -> BudgetDetailRead:
    """Version suivante d'un budget approuvé (l'ancienne reste lisible)."""
    service = _service(session, perimetre, user)
    budget = service.nouvelle_version(budget_id)
    return _detail(service, budget.id)


@router.post(
    "/budgets/{budget_id}/reprise",
    response_model=BudgetDetailRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(exiger_interne)],
)
def reprendre(
    budget_id: UUID,
    payload: BudgetRepriseCreate,
    session: DbSession,
    perimetre: PerimetreDep,
    user: InterneUser,
) -> BudgetDetailRead:
    """Reprend un budget d'offre approuvé sur une mission (copie, brouillon)."""
    service = _service(session, perimetre, user)
    budget = service.reprendre_pour_mission(budget_id, payload.mission_id)
    return _detail(service, budget.id)


@router.get("/budgets/{budget_id}/export")
def export_budget(
    budget_id: UUID,
    session: DbSession,
    perimetre: PerimetreDep,
    user: CurrentUser,
    format: Annotated[str, Query(description="'xlsx' (défaut) ou 'docx'")] = "xlsx",
) -> Response:
    """Exporte le budget calculé (XLSX ou DOCX) — vue calculée, pas un document."""
    octets, nom, mime = _service(session, perimetre, user).exporter(budget_id, format)
    return Response(
        content=octets,
        media_type=mime,
        headers={"Content-Disposition": f'attachment; filename="{nom}"'},
    )


__all__ = ["router"]
