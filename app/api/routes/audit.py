"""Route—journal d'audit—« qui a fait quoi ».

Décision validée—le journal est consultable par l'**administrateur** et
l'**équipe interne** (lecture seule), jamais par un formateur. Il est
**insert-only**—cette route ne modifie, ne masque et ne purge rien—la
rétention CARSO reste une décision ouverte, on ne détruit pas de trace.

Ce que le journal expose (aucun secret, AGENTS.md §9) :

- l'acteur (type ``humain`` | ``agent`` | ``systeme``, identifiant, libellé
  résolu pour l'affichage) ;
- l'action (vocabulaire ``ActionAudit``) ;
- l'entité touchée (type + identifiant) ;
- l'horodatage, l'état ``before``/``after`` et les métadonnées de l'événement.

Les filtres sont combinables et la pagination est **déclarée** (``total``) :
l'interface affiche un vrai décompte, pas une estimation.

La lecture elle-même vit dans ``JournalAuditService`` : l'outil
``consulter_journal_audit`` d'un agent ouvre la **même** vue, avec les mêmes
libellés et la même résolution d'acteurs — un canal ne raconte pas l'audit
autrement que l'autre.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from app.api.deps import AdminUser, DbSession, get_current_user
from app.api.permissions import exiger_audit
from app.application.services.journal_audit_service import (
    TAILLE_PAGE_DEFAUT,
    TAILLE_PAGE_MAX,
    TYPES_ENTITE_MAX,
    JournalAuditService,
    types_entite,
)

router = APIRouter(
    prefix="/audit",
    tags=["audit"],
    dependencies=[Depends(get_current_user), Depends(exiger_audit)],
)


class AuditEventRead(BaseModel):
    """Un événement du journal, prêt à afficher."""

    id: UUID
    created_at: datetime
    acteur_type: str
    acteur_id: str | None
    acteur_libelle: str | None
    action: str
    #: Nom affiché de l'action (« Suppression d'une offre »), calculé par le
    #: backend : une seule source de vérité, donc pas de divergence entre le
    #: filtre et la colonne « Action ».
    libelle: str
    entity_type: str
    entity_id: str | None
    before: dict[str, Any] | None
    after: dict[str, Any] | None
    event_metadata: dict[str, Any] | None
    #: Motif déclaré par l'auteur du geste (``event_metadata.reason``) : la
    #: suppression et la purge l'exigent, la décision humaine peut le porter.
    #: C'est la seule trace conservée de ce qui a été détruit — il s'affiche.
    motif: str | None = None
    #: Demandeur d'un geste **d'agent** : le compte qui a ouvert la conversation
    #: (ou écrit la mention) — le journal répond alors à « qui fait quoi ».
    demande_par: str | None = None
    demande_par_libelle: str | None = None


class AuditPageRead(BaseModel):
    """Page d'événements + décompte total (avant pagination)."""

    elements: list[AuditEventRead]
    total: int
    limit: int
    offset: int


class ActionAuditItem(BaseModel):
    """Entrée du vocabulaire d'actions (alimente le filtre de l'interface).

    ``entity_type`` n'est renseigné que pour les actions **génériques**
    (``suppression.effectuee``) : le filtre isole alors « Suppression d'une
    offre » de « Suppression d'une mission », qui partagent la même clé.
    """

    action: str
    libelle: str
    entity_type: str | None = None


@router.get("", response_model=AuditPageRead)
def lister_journal(
    session: DbSession,
    acteur_type: Annotated[
        str | None, Query(description="humain | agent | systeme")
    ] = None,
    acteur_id: Annotated[str | None, Query(description="Compte ou nom d'agent")] = None,
    action: Annotated[str | None, Query(description="Action d'audit")] = None,
    entity_type: Annotated[
        list[str] | None,
        Query(
            description=(
                "Type(s) d'entité ; répétable pour couvrir les noms hérités "
                "d'une même ressource (offre et offre_formation)"
            )
        ),
    ] = None,
    entity_id: Annotated[str | None, Query(description="Identifiant d'entité")] = None,
    date_debut: Annotated[
        datetime | None, Query(description="Borne basse (incluse)")
    ] = None,
    date_fin: Annotated[datetime | None, Query(description="Borne haute (incluse)")] = None,
    limit: Annotated[int, Query(ge=1, le=TAILLE_PAGE_MAX)] = TAILLE_PAGE_DEFAUT,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> AuditPageRead:
    """Événements filtrés, du plus récent au plus ancien (lecture seule)."""
    page = JournalAuditService(session).lister(
        acteur_type=acteur_type,
        acteur_id=acteur_id,
        action=action,
        entity_type=types_entite(entity_type),
        entity_id=entity_id,
        depuis=date_debut,
        jusqu_a=date_fin,
        limit=limit,
        offset=offset,
    )
    return AuditPageRead(**page)


class PurgeAuditCreate(BaseModel):
    """Sélection à purger : identifiants cochés **ou** une plage de dates.

    Les deux ne se mélangent pas : une purge manuelle dit « ces lignes-là », une
    purge par date dit « ce que cette période contient ». Les combiner ferait
    dépendre le résultat de l'ordre des gestes de l'utilisateur.
    """

    motif: str = Field(min_length=1, max_length=500)
    ids: list[UUID] = Field(default_factory=list, max_length=500)
    date_debut: datetime | None = None
    date_fin: datetime | None = None


class PurgeAuditRead(BaseModel):
    """Conséquence réelle de la purge (jamais une intention)."""

    purges: int
    conserves: int
    message: str


@router.post("/purge", response_model=PurgeAuditRead)
def purger_journal(
    entree: PurgeAuditCreate, session: DbSession, user: AdminUser
) -> PurgeAuditRead:
    """Purge des journaux d'audit — **administrateurs uniquement** (décision 10/11).

    Le geste est lui-même tracé : la ligne ``audit.purge`` survit à toutes les
    purges et dit combien d'événements ont disparu, sur quelle période et
    pourquoi. Sans cela, vider le journal effacerait la preuve qu'il a été vidé.

    Une purge ne touche jamais les lignes ``audit.purge`` précédentes :
    l'historique des purges reste lisible.
    """
    rapport = JournalAuditService(session).purger(
        motif=entree.motif,
        actor_id=str(user.id),
        ids=entree.ids,
        depuis=entree.date_debut,
        jusqu_a=entree.date_fin,
    )
    session.commit()
    message = f"{rapport.purges} événement(s) purgé(s)."
    if rapport.conserves:
        message += (
            f" {rapport.conserves} entrée(s) de la sélection n'ont pas été "
            "supprimées (déjà absentes ou protégées)."
        )
    if rapport.purges == 0:
        message = "Aucun événement ne correspondait à cette sélection."
    return PurgeAuditRead(
        purges=rapport.purges, conserves=rapport.conserves, message=message
    )


@router.get("/actions", response_model=list[ActionAuditItem])
def lister_actions(session: DbSession) -> list[ActionAuditItem]:
    """Vocabulaire des actions tracées (filtre de l'interface).

    Les libellés viennent de ``app.domain.audit_vocabulaire`` : le filtre affiche
    « Suppression d'une offre », jamais ``suppression.effectuee``. Les actions
    génériques produisent une entrée **par entité présente** dans le journal.
    """
    return [ActionAuditItem(**entree) for entree in JournalAuditService(session).actions()]


__all__ = [
    "TAILLE_PAGE_DEFAUT",
    "TAILLE_PAGE_MAX",
    "TYPES_ENTITE_MAX",
    "router",
]
