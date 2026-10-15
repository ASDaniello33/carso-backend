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
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import distinct, select

from app.api.deps import DbSession, get_current_user
from app.api.permissions import exiger_audit
from app.core.errors import ValidationError
from app.domain.audit_vocabulaire import (
    ACTIONS_PAR_ENTITE,
    ENTITES_SUPPRIMEES,
    libelle_action,
)
from app.domain.document import AuditEvent
from app.domain.enums import ActionAudit, ActorType
from app.infrastructure.repositories import AuditRepository, UtilisateurRepository

#: Taille de page par défaut et plafond (le journal peut être volumineux).
TAILLE_PAGE_DEFAUT = 50
TAILLE_PAGE_MAX = 200

#: Un type d'entité se répète (``?entity_type=offre&entity_type=offre_formation``)
#: parce qu'une même ressource porte plusieurs noms dans le journal. Le plafond
#: borne la longueur du ``IN (...)`` : au-delà, c'est une erreur d'appel, pas un
#: filtre.
TYPES_ENTITE_MAX = 10

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


def _demandeur(evenement: Any) -> str | None:
    """Identifiant du demandeur inscrit par ``TraceContext`` (métadonnées).

    L'événement n'a pas de colonne dédiée (le schéma validé des ``audit_events``
    n'en prévoit pas et aucune migration n'est justifiée par ce seul ajout) : la
    valeur vit dans ``event_metadata``, avec le reste du contexte du geste.
    """
    metadonnees = evenement.event_metadata
    if not isinstance(metadonnees, dict):
        return None
    valeur = metadonnees.get("demande_par")
    return str(valeur) if valeur else None


def _motif(evenement: Any) -> str | None:
    """Motif déclaré du geste, tel qu'inscrit dans les métadonnées de l'événement.

    Une décision humaine (``record_decision``) et une suppression
    (``ServiceSuppression``) rangent le motif sous ``event_metadata.reason``.
    Le journal ne dispose pas de colonne dédiée et aucune migration n'est
    justifiée par ce seul affichage (même choix que ``demande_par``).
    """
    metadonnees = evenement.event_metadata
    if not isinstance(metadonnees, dict):
        return None
    valeur = metadonnees.get("reason") or metadonnees.get("motif")
    return str(valeur).strip() or None if valeur else None


def _types_entite(demandes: list[str] | None) -> list[str] | None:
    """Types d'entité demandés : vides retirés, plafond vérifié.

    Un type vide (``?entity_type=``) ne doit pas filtrer sur la chaîne vide —
    il est ignoré, comme les autres filtres facultatifs de cette route. Le
    plafond protège la requête d'un appel qui empilerait les valeurs.
    """
    if not demandes:
        return None
    propres = [valeur.strip() for valeur in demandes if valeur and valeur.strip()]
    if not propres:
        return None
    if len(propres) > TYPES_ENTITE_MAX:
        msg = f"Au plus {TYPES_ENTITE_MAX} types d'entité par requête"
        raise ValidationError(msg, details={"entity_type": propres})
    return propres


def _entites_tracees(session: DbSession, action: str) -> list[str]:
    """Entités réellement présentes dans le journal pour une action générique.

    Le filtre ne propose que ce qui existe : une entrée « Suppression d'un lot »
    n'apparaît qu'après la première suppression de lot. L'ordre suit celui de
    ``ENTITES_SUPPRIMEES`` (stable d'un chargement à l'autre), les entités
    inconnues fermant la marche.
    """
    presentes = set(
        session.execute(
            select(distinct(AuditEvent.entity_type)).where(AuditEvent.action == action)
        ).scalars().all()
    )
    connues = [entite for entite in ENTITES_SUPPRIMEES if entite in presentes]
    return connues + sorted(presentes - set(connues))


class _ResolveurActeurs:
    """Résout le libellé d'un acteur — une requête par acteur distinct, au plus."""

    def __init__(self, session: DbSession) -> None:
        self._utilisateurs = UtilisateurRepository(session)
        self._agents: dict[str, str] | None = None
        self._cache: dict[tuple[str, str], str | None] = {}

    def libelle(self, acteur_type: str, acteur_id: str | None) -> str | None:
        if not acteur_id:
            return None
        cle = (acteur_type, acteur_id)
        if cle in self._cache:
            return self._cache[cle]
        libelle = self._resoudre(acteur_type, acteur_id)
        self._cache[cle] = libelle
        return libelle

    def _resoudre(self, acteur_type: str, acteur_id: str) -> str | None:
        if acteur_type == ActorType.HUMAIN.value:
            try:
                utilisateur = self._utilisateurs.get(UUID(acteur_id))
            except ValueError:
                return acteur_id
            if utilisateur is None:
                return acteur_id
            return f"{utilisateur.prenom} {utilisateur.nom}".strip() or utilisateur.email
        if acteur_type == ActorType.AGENT.value:
            return self._noms_agents().get(acteur_id, acteur_id)
        return acteur_id

    def _noms_agents(self) -> dict[str, str]:
        """Noms d'affichage des agents (chargés paresseusement, jamais en dur)."""
        if self._agents is None:
            from app.agents.catalogue import AGENT_DEFINITIONS

            self._agents = {
                definition.agent_id: definition.display_name
                for definition in AGENT_DEFINITIONS
            }
        return self._agents


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
    types_entite = _types_entite(entity_type)
    evenements, total = AuditRepository(session).lister(
        acteur_type=acteur_type,
        acteur_id=acteur_id,
        action=action,
        entity_type=types_entite,
        entity_id=entity_id,
        depuis=date_debut,
        jusqu_a=date_fin,
        limit=limit,
        offset=offset,
    )
    resolveur = _ResolveurActeurs(session)
    elements: list[AuditEventRead] = []
    for evenement in evenements:
        demandeur = _demandeur(evenement)
        elements.append(
            AuditEventRead(
                id=evenement.id,
                created_at=evenement.created_at,
                acteur_type=evenement.actor_type,
                acteur_id=evenement.actor_id,
                acteur_libelle=resolveur.libelle(
                    evenement.actor_type, evenement.actor_id
                ),
                action=evenement.action,
                libelle=libelle_action(evenement.action, evenement.entity_type),
                entity_type=evenement.entity_type,
                entity_id=evenement.entity_id,
                before=evenement.before,
                after=evenement.after,
                event_metadata=evenement.event_metadata,
                motif=_motif(evenement),
                demande_par=demandeur,
                demande_par_libelle=resolveur.libelle(
                    ActorType.HUMAIN.value, demandeur
                ),
            )
        )
    return AuditPageRead(
        elements=elements,
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/actions", response_model=list[ActionAuditItem])
def lister_actions(session: DbSession) -> list[ActionAuditItem]:
    """Vocabulaire des actions tracées (filtre de l'interface).

    Les libellés viennent de ``app.domain.audit_vocabulaire`` : le filtre affiche
    « Suppression d'une offre », jamais ``suppression.effectuee``. Les actions
    génériques produisent une entrée **par entité présente** dans le journal.
    """
    entrees: list[ActionAuditItem] = []
    for action in ActionAudit:
        valeur = action.value
        if valeur in ACTIONS_PAR_ENTITE:
            entrees.extend(
                ActionAuditItem(
                    action=valeur,
                    entity_type=entite,
                    libelle=libelle_action(valeur, entite),
                )
                for entite in _entites_tracees(session, valeur)
            )
            continue
        entrees.append(ActionAuditItem(action=valeur, libelle=libelle_action(valeur)))
    return entrees


__all__ = ["router"]
