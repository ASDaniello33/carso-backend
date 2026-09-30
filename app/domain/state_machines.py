"""Machines à états provisoires (Phase 2 §3) — transitions autorisées par entité.

Les intitulés restent à confirmer par CARSO ([?]) ; cette table est la seule
source de vérité du code et vit dans le domaine. Toute mutation de statut
d'une entité passe par ``validate_transition`` — pas de saut d'état silencieux.
"""

from app.core.errors import ConflictError
from app.domain.enums import (
    StatutAffectation,
    StatutAgentTask,
    StatutAppelAProposition,
    StatutBudget,
    StatutDocument,
    StatutMission,
    StatutOffre,
    StatutSessionFormation,
    StatutUtilisateur,
)

TRANSITIONS: dict[str, dict[str, set[str]]] = {
    "appel_a_proposition": {
        StatutAppelAProposition.RECU: {
            StatutAppelAProposition.EN_ANALYSE,
            StatutAppelAProposition.REJETE,
        },
        StatutAppelAProposition.EN_ANALYSE: {
            StatutAppelAProposition.PROPOSE,
            StatutAppelAProposition.REJETE,
        },
        StatutAppelAProposition.PROPOSE: {
            StatutAppelAProposition.CORRIGE,
            StatutAppelAProposition.VALIDE,
        },
        # CORRIGE → EN_ANALYSE : boucle de re-analyse après correction humaine
        # (P1 : l'utilisateur corrige puis l'agent ré-analyse).
        StatutAppelAProposition.CORRIGE: {
            StatutAppelAProposition.EN_ANALYSE,
            StatutAppelAProposition.VALIDE,
        },
        StatutAppelAProposition.VALIDE: set(),
        StatutAppelAProposition.REJETE: set(),
    },
    "offre": {
        StatutOffre.BROUILLON: {
            StatutOffre.EN_REVUE,
            StatutOffre.ARCHIVE,
        },
        StatutOffre.EN_REVUE: {
            StatutOffre.APPROUVE,
            StatutOffre.BROUILLON,
            StatutOffre.ARCHIVE,
        },
        StatutOffre.APPROUVE: {StatutOffre.ARCHIVE},
        StatutOffre.ARCHIVE: set(),
    },
    "mission": {
        StatutMission.PLANIFIEE: {StatutMission.EN_PREPARATION},
        StatutMission.EN_PREPARATION: {StatutMission.EN_COURS},
        StatutMission.EN_COURS: {StatutMission.CLOTUREE},
        StatutMission.CLOTUREE: set(),
    },
    "session": {
        StatutSessionFormation.PLANIFIEE: {
            StatutSessionFormation.CONFIRMEE,
            StatutSessionFormation.ANNULEE,
        },
        StatutSessionFormation.CONFIRMEE: {
            StatutSessionFormation.REALISEE,
            StatutSessionFormation.ANNULEE,
        },
        StatutSessionFormation.REALISEE: set(),
        StatutSessionFormation.ANNULEE: set(),
    },
    "document": {
        # ``SUPPRIME`` est atteignable depuis tout état de conservation : la fiche
        # survit à la suppression, elle ne peut donc pas être un cul-de-sac
        # (règle validée 23/09).
        #
        # La **restauration** (ADR 0006) est la seule sortie. La machine autorise
        # l'arête parce que la cible dépend de l'historique de la fiche, pas d'un
        # choix : ``DocumentService.restaurer_document`` exige que la cible soit
        # *exactement* le statut écrit dans le tombstone au moment de la
        # suppression. On ne choisit jamais l'état dans lequel on restaure.
        StatutDocument.DRAFT: {
            StatutDocument.PROPOSED,
            StatutDocument.ARCHIVED,
            StatutDocument.SUPPRIME,
        },
        StatutDocument.PROPOSED: {
            StatutDocument.APPROVED,
            StatutDocument.ARCHIVED,
            StatutDocument.SUPPRIME,
        },
        # Documents de **session** (30/09) : « généré » (produit par l'assistant
        # formateur / l'outil de génération) ou « importé » (déposé manuellement)
        # — pas un cycle d'offre. Les deux peuvent être approuvés, archivés ou
        # supprimés (restauration vers leur état d'origine, comme les autres).
        StatutDocument.GENERE: {
            StatutDocument.APPROVED,
            StatutDocument.ARCHIVED,
            StatutDocument.SUPPRIME,
        },
        StatutDocument.IMPORTE: {
            StatutDocument.APPROVED,
            StatutDocument.ARCHIVED,
            StatutDocument.SUPPRIME,
        },
        StatutDocument.APPROVED: {StatutDocument.ARCHIVED, StatutDocument.SUPPRIME},
        StatutDocument.ARCHIVED: {StatutDocument.SUPPRIME},
        StatutDocument.SUPPRIME: {
            StatutDocument.DRAFT,
            StatutDocument.PROPOSED,
            StatutDocument.APPROVED,
            StatutDocument.ARCHIVED,
            StatutDocument.GENERE,
            StatutDocument.IMPORTE,
        },
    },
    "budget": {
        StatutBudget.BROUILLON: {StatutBudget.PROPOSE},
        StatutBudget.PROPOSE: {StatutBudget.APPROUVE, StatutBudget.BROUILLON},
        StatutBudget.APPROUVE: set(),
    },
    "affectation_equipe": {
        StatutAffectation.PROPOSEE: {StatutAffectation.APPROUVEE, StatutAffectation.REFUSEE},
        StatutAffectation.APPROUVEE: set(),
        StatutAffectation.REFUSEE: set(),
    },
    "agent_task": {
        StatutAgentTask.PENDING: {StatutAgentTask.RUNNING},
        StatutAgentTask.RUNNING: {
            StatutAgentTask.COMPLETED,
            StatutAgentTask.FAILED,
            StatutAgentTask.TIMEOUT,
        },
        StatutAgentTask.COMPLETED: set(),
        StatutAgentTask.FAILED: set(),
        StatutAgentTask.TIMEOUT: set(),
    },
    "utilisateur": {
        StatutUtilisateur.PENDING: {StatutUtilisateur.ACTIF},
        StatutUtilisateur.ACTIF: {StatutUtilisateur.SUSPENDU},
        StatutUtilisateur.SUSPENDU: {StatutUtilisateur.ACTIF},
    },
}


def validate_transition(entity_type: str, current: str, target: str) -> None:
    """Valide une transition de statut ; lève ConflictError si interdite.

    Args:
        entity_type: clé de ``TRANSITIONS`` (ex: ``offre``).
        current: statut actuel.
        target: statut visé.

    Raises:
        ConflictError: transition inconnue ou interdite par la machine à états.
    """
    machine = TRANSITIONS.get(entity_type)
    if machine is None:
        msg = f"Pas de machine à états pour {entity_type!r}"
        raise ConflictError(msg)

    allowed = machine.get(current)
    if allowed is None:
        msg = f"Statut inconnu {current!r} pour {entity_type!r}"
        raise ConflictError(msg)

    if target not in allowed:
        msg = f"Transition interdite {entity_type}: {current!r} → {target!r}"
        raise ConflictError(msg, details={"current": current, "target": target})
