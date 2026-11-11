"""Lecture du journal d'audit — une seule logique pour l'API et les agents.

Le journal est **insert-only** : ce service ne fait que lire. Il vit ici plutôt
que dans la route parce que deux chemins doivent répondre exactement pareil :

- ``GET /audit`` (la page « Journal d'audit ») ;
- l'outil ``consulter_journal_audit`` d'un agent, ouvert au seul
  **administrateur**.

Si chacun recalculait les libellés, la résolution des acteurs ou le motif, une
même ligne d'audit s'afficherait différemment selon le canal — et l'agent
lirait une trace que l'écran ne montre pas. Une seule implémentation, deux
entrées.

Ce que le service ne fait pas :

- il ne modifie, ne masque et ne purge rien (la rétention CARSO reste une
  décision ouverte : on ne détruit pas de trace) ;
- il ne décide pas **qui a le droit** de lire : la garde HTTP (``exiger_audit``)
  et la garde de l'outil d'agent (rôle administrateur) le font en amont. Un
  service ne connaît pas de rôle, il sert une lecture déjà autorisée.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import delete, distinct, func, select
from sqlalchemy.orm import Session

from app.application.trace import TraceContext
from app.core.errors import ValidationError
from app.domain.audit_vocabulaire import (
    ENTITES_SUPPRIMEES,
    libelle_action,
)
from app.domain.document import AuditEvent
from app.domain.enums import ActionAudit, ActorType
from app.infrastructure.repositories import AuditRepository, UtilisateurRepository

__all__ = [
    "RapportPurgeAudit",
    "TAILLE_PAGE_DEFAUT",
    "TAILLE_PAGE_MAX",
    "TAILLE_OUTIL_MAX",
    "TYPES_ENTITE_MAX",
    "JournalAuditService",
]

#: Taille de page par défaut et plafond (le journal peut être volumineux).
TAILLE_PAGE_DEFAUT = 50
TAILLE_PAGE_MAX = 200

#: Plafond d'un appel **d'agent** : un modèle n'a pas besoin de 200 lignes dans
#: son contexte, et une trace complète y prendrait la place de la réponse. La
#: valeur demandée est resserrée, jamais rejetée (le modèle ne doit pas échouer
#: pour avoir demandé trop large).
TAILLE_OUTIL_MAX = 50

#: Un type d'entité se répète (``?entity_type=offre&entity_type=offre_formation``)
#: parce qu'une même ressource porte plusieurs noms dans le journal. Le plafond
#: borne la longueur du ``IN (...)`` : au-delà, c'est une erreur d'appel, pas un
#: filtre.
TYPES_ENTITE_MAX = 10


def _demandeur(evenement: AuditEvent) -> str | None:
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


def _motif(evenement: AuditEvent) -> str | None:
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


def types_entite(demandes: list[str] | None) -> list[str] | None:
    """Types d'entité demandés : vides retirés, plafond vérifié.

    Un type vide (``?entity_type=``) ne doit pas filtrer sur la chaîne vide —
    il est ignoré, comme les autres filtres facultatifs. Le plafond protège la
    requête d'un appel qui empilerait les valeurs.
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


class _ResolveurActeurs:
    """Résout le libellé d'un acteur — une requête par acteur distinct, au plus."""

    def __init__(self, session: Session) -> None:
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


@dataclass(frozen=True, slots=True)
class RapportPurgeAudit:
    """Ce qu'une purge a réellement retiré — et ce qu'elle a refusé de retirer.

    ``purges``/``conserves`` sont les deux moitiés du compte : un rapport qui ne
    dirait que les suppressions laisserait croire qu'une plage a été vidée alors
    que la trace du geste, elle, est restée.
    """

    purges: int
    conserves: int
    periode: tuple[datetime | None, datetime | None]
    motif: str


class JournalAuditService:
    """Lecture et purge contrôlée du journal d'audit.

    La lecture sert l'écran et les agents ; la **purge** est un geste
    d'administration décidé par CARSO (10/11), réservé à l'administrateur par la
    route et tracé par le journal lui-même.
    """

    def __init__(self, session: Session) -> None:
        self._session = session
        self._repository = AuditRepository(session)

    # --- lecture ------------------------------------------------------------

    def lister(
        self,
        *,
        acteur_type: str | None = None,
        acteur_id: str | None = None,
        action: str | None = None,
        entity_type: str | list[str] | None = None,
        entity_id: str | None = None,
        depuis: datetime | None = None,
        jusqu_a: datetime | None = None,
        limit: int = TAILLE_PAGE_DEFAUT,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Événements filtrés, du plus récent au plus ancien.

        Returns:
            ``{"elements": [...], "total": n, "limit": l, "offset": o}`` — le
            total compte **avant** pagination, pour que l'appelant affiche un
            vrai décompte au lieu d'une estimation.
        """
        evenements, total = self._repository.lister(
            acteur_type=acteur_type,
            acteur_id=acteur_id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            depuis=depuis,
            jusqu_a=jusqu_a,
            limit=limit,
            offset=offset,
        )
        resolveur = _ResolveurActeurs(self._session)
        elements = [self._ligne(evenement, resolveur) for evenement in evenements]
        return {
            "elements": elements,
            "total": total,
            "limit": limit,
            "offset": offset,
        }

    # --- purge (administrateur) ---------------------------------------------

    def purger(
        self,
        *,
        motif: str,
        actor_id: str | None,
        ids: list[UUID] | None = None,
        depuis: datetime | None = None,
        jusqu_a: datetime | None = None,
    ) -> RapportPurgeAudit:
        """Retire des journaux d'audit — geste d'administration, lui-même tracé.

        Deux sélections, jamais mélangées : une **liste d'identifiants**
        (sélection manuelle à l'écran) ou une **plage de dates** (purge par
        période). Fournir les deux n'a pas de sens — on ne devine pas laquelle
        l'emporte — donc c'est refusé.

        Règle non négociable : les lignes ``audit.purge`` **ne sont jamais
        supprimées**. Le journal qui garde la trace des gestes ne peut pas
        servir à effacer la trace de ses propres effacements ; la dernière
        trace dit toujours quand, sur quelle période, combien et pourquoi.

        Args:
            motif: raison déclarée par l'administrateur (obligatoire).
            actor_id: compte qui exécute (inscrit dans la trace).
            ids: identifiants d'événements sélectionnés à la main.
            depuis: borne basse **incluse** de la plage à purger.
            jusqu_a: borne haute **incluse** de la plage à purger.

        Returns:
            Le rapport : événements purgés, événements conservés (la trace du
            geste et les purges antérieures), période et motif.

        Raises:
            ValidationError: motif vide, ni identifiants ni borne de date, ou
                les deux sélections à la fois.
        """
        raison = (motif or "").strip()
        if not raison:
            raise ValidationError(
                "Un motif est obligatoire pour purger des journaux d'audit.",
                details={"champ": "motif"},
            )
        identifiants = [identifiant for identifiant in (ids or []) if identifiant]
        plage = depuis is not None or jusqu_a is not None
        if identifiants and plage:
            raise ValidationError(
                "Choisir une sélection manuelle OU une plage de dates, pas les deux.",
                details={"identifiants": len(identifiants)},
            )
        if not identifiants and not plage:
            raise ValidationError(
                "Aucune sélection : fournir des identifiants ou une plage de dates.",
                details={"champ": "selection"},
            )
        if depuis is not None and jusqu_a is not None and depuis > jusqu_a:
            raise ValidationError(
                "La borne basse doit précéder la borne haute.",
                details={"depuis": depuis.isoformat(), "jusqu_a": jusqu_a.isoformat()},
            )

        conditions = [AuditEvent.action != ActionAudit.AUDIT_PURGE.value]
        if identifiants:
            conditions.append(AuditEvent.id.in_(identifiants))
        else:
            if depuis is not None:
                conditions.append(AuditEvent.created_at >= depuis)
            if jusqu_a is not None:
                conditions.append(AuditEvent.created_at <= jusqu_a)

        # Ce qui existe, avant de détruire : le rapport doit dire la vérité même
        # si une partie de la sélection était déjà purgée ou protégée.
        a_purger = int(
            self._session.scalar(
                select(func.count()).select_from(AuditEvent).where(*conditions)
            )
            or 0
        )
        # Le total demandé moins ce qui part = ce qui reste protégé ou absent.
        demandes = len(identifiants) if identifiants else None
        if a_purger:
            self._session.execute(delete(AuditEvent).where(*conditions))
            self._session.flush()
        conserves = (demandes - a_purger) if demandes is not None else 0

        bornes = self._bornes(identifiants, depuis, jusqu_a, a_purger)
        # La trace du geste : écrite après la suppression, dans la **même**
        # transaction. Si le commit échoue, rien n'a été purgé non plus. Passer
        # par ``TraceContext`` garde l'attribution au même endroit que les
        # autres gestes — même écrivain, même convention.
        TraceContext(self._session, actor_id=actor_id).record_event(
            actor_type=ActorType.HUMAIN.value,
            action=ActionAudit.AUDIT_PURGE.value,
            entity_type="audit_event",
            entity_id=None,
            before={"evenements": a_purger},
            after={"evenements_purges": a_purger},
            event_metadata={
                "reason": raison,
                "periode": bornes,
                "selection": "manuelle" if identifiants else "plage",
                "conserves": conserves,
            },
        )
        return RapportPurgeAudit(
            purges=a_purger,
            conserves=conserves,
            periode=(depuis, jusqu_a),
            motif=raison,
        )

    @staticmethod
    def _bornes(
        identifiants: list[UUID],
        depuis: datetime | None,
        jusqu_a: datetime | None,
        a_purger: int,
    ) -> list[str | None]:
        """Période inscrite dans la trace : les bornes, ou celles de la sélection.

        Une purge manuelle n'a pas de bornes déclarées : la trace dit alors
        « sélection manuelle » et le nombre, plutôt qu'une plage inventée.
        """
        if identifiants:
            return [None, None]
        _ = a_purger
        return [
            depuis.isoformat() if depuis is not None else None,
            jusqu_a.isoformat() if jusqu_a is not None else None,
        ]

    def actions(self) -> list[dict[str, Any]]:
        """Vocabulaire des actions tracées (filtre de l'interface).

        Les libellés viennent de ``app.domain.audit_vocabulaire`` : le filtre
        affiche « Suppression d'une offre », jamais ``suppression.effectuee``.
        Les actions génériques produisent une entrée **par entité présente**
        dans le journal.
        """
        entrees: list[dict[str, Any]] = []
        for action in ActionAudit:
            valeur = action.value
            if valeur in {ActionAudit.SUPPRESSION_EFFECTUEE.value}:
                entrees.extend(
                    {
                        "action": valeur,
                        "entity_type": entite,
                        "libelle": libelle_action(valeur, entite),
                    }
                    for entite in self._entites_tracees(valeur)
                )
                continue
            entrees.append(
                {
                    "action": valeur,
                    "entity_type": None,
                    "libelle": libelle_action(valeur),
                }
            )
        return entrees

    # --- internes -----------------------------------------------------------

    def _entites_tracees(self, action: str) -> list[str]:
        """Entités réellement présentes dans le journal pour une action générique.

        Le filtre ne propose que ce qui existe : une entrée « Suppression d'un
        lot » n'apparaît qu'après la première suppression de lot. L'ordre suit
        celui de ``ENTITES_SUPPRIMEES`` (stable d'un chargement à l'autre), les
        entités inconnues fermant la marche.
        """
        presentes = set(
            self._session.execute(
                select(distinct(AuditEvent.entity_type)).where(AuditEvent.action == action)
            ).scalars().all()
        )
        connues = [entite for entite in ENTITES_SUPPRIMEES if entite in presentes]
        return connues + sorted(presentes - set(connues))

    @staticmethod
    def _ligne(evenement: AuditEvent, resolveur: _ResolveurActeurs) -> dict[str, Any]:
        """Une ligne prête à afficher (aucun objet ORM ne sort du service)."""
        demandeur = _demandeur(evenement)
        return {
            "id": evenement.id,
            "created_at": evenement.created_at,
            "acteur_type": evenement.actor_type,
            "acteur_id": evenement.actor_id,
            "acteur_libelle": resolveur.libelle(evenement.actor_type, evenement.actor_id),
            "action": evenement.action,
            "libelle": libelle_action(evenement.action, evenement.entity_type),
            "entity_type": evenement.entity_type,
            "entity_id": evenement.entity_id,
            "before": evenement.before,
            "after": evenement.after,
            "event_metadata": evenement.event_metadata,
            "motif": _motif(evenement),
            "demande_par": demandeur,
            "demande_par_libelle": resolveur.libelle(ActorType.HUMAIN.value, demandeur),
        }
