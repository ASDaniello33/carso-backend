"""Outil d'audit des agents — lecture du journal, réservée à l'administrateur.

Pourquoi un outil plutôt qu'une route : un agent doit pouvoir **lire** le journal
(« qui a fait quoi sur cette offre, et quand ? ») sans quitter la conversation,
mais seulement si le compte qui lui parle est administrateur. La page
``/audit`` suit la règle inverse et ouvre la lecture à l'équipe interne ; ce
n'est pas la même surface, et c'est délibéré :

- l'**écran** est un outil de supervision : l'équipe interne y vérifie ses
  propres gestes ;
- l'**outil d'agent** ouvre le journal au modèle, donc dans un contexte où
  l'agent pourrait le résumer, le citer ou le transmettre. Le limiter à
  l'administrateur est la décision de moindre privilège (AGENTS.md §9).

La garde est appliquée **à l'appel**, pas au montage : un graphe d'agent est
partagé entre les comptes (seul l'agent personnel est par compte), donc un
outil monté pour un administrateur resterait monté quand un collaborateur
ouvre le même agent. Le rôle est lu dans le contexte d'exécution posé par la
garde HTTP (``preparer_acces_agent``) — jamais dans les arguments du modèle.

Lecture seule : aucune opération ici ne modifie, ne masque ni ne purge une
trace. La suppression d'un journal est un lot distinct, décidé séparément.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, Field

from app.agents.toolkit import AgentTools, OperationSpec
from app.application.services.journal_audit_service import (
    TAILLE_OUTIL_MAX,
    JournalAuditService,
    types_entite,
)
from app.core.errors import PermissionDeniedError, ValidationError
from app.domain.enums import RoleUtilisateur

__all__ = ["NOM_OUTIL", "ConsulterJournalAuditInput", "construire_tools_audit"]

#: Nom du tool, déclaré au contrat des agents qui y ont droit.
NOM_OUTIL = "consulter_journal_audit"


class ConsulterJournalAuditInput(BaseModel):
    """Filtres de lecture du journal (tous facultatifs).

    Les bornes de date sont des **dates** (jour), pas des instants : un
    administrateur demande « les gestes du 3 novembre », pas « depuis
    2026-11-03T00:00:00.000Z ». La borne haute couvre donc la journée entière.
    """

    action: str | None = Field(
        default=None,
        max_length=100,
        description="Clé d'action (ex. « offre.publiee »). Omettre pour toutes.",
    )
    entity_type: str | None = Field(
        default=None,
        max_length=50,
        description="Type d'entité touchée (ex. « offre », « mission »).",
    )
    entity_id: str | None = Field(
        default=None,
        max_length=64,
        description=(
            "Identifiant de l'entité. Pour suivre **un** dossier, préférer "
            "entity_type + entity_id plutôt qu'une recherche par texte."
        ),
    )
    acteur_type: str | None = Field(
        default=None,
        max_length=20,
        description="« humain », « agent » ou « systeme ». Omettre pour tous.",
    )
    date_debut: date | None = Field(
        default=None, description="Premier jour inclus (AAAA-MM-JJ)."
    )
    date_fin: date | None = Field(
        default=None, description="Dernier jour inclus (AAAA-MM-JJ)."
    )
    limite: int = Field(
        default=20,
        ge=1,
        description=f"Nombre d'événements (plafonné à {TAILLE_OUTIL_MAX}).",
    )


def _horodatage(jour: date, *, fin: bool) -> datetime:
    """Borne de requête pour un jour, dans le fuseau local du serveur.

    ``AuditEvent.created_at`` est stocké avec fuseau. Comparer une date nue à
    une colonne consciente ferait dépendre le filtre du fuseau du serveur de
    base : on construit donc les bornes avec le fuseau local, et la borne
    haute va jusqu'à la dernière seconde du jour demandé.
    """
    debut = datetime(jour.year, jour.month, jour.day).astimezone()
    if not fin:
        return debut
    return debut.replace(hour=23, minute=59, second=59, microsecond=999999)


def _role_du_run() -> str | None:
    """Rôle du compte qui parle, lu dans le contexte d'exécution du serveur.

    ``None`` hors requête utilisateur (tests déterministes, scripts) : l'appel
    est alors refusé, comme pour tout outil qui dépend d'un compte.
    """
    from app.agents.contexte import contexte_utilisateur_effectif

    contexte = contexte_utilisateur_effectif()
    return contexte.role if contexte is not None else None


def _consulter(session: Any, payload: dict[str, Any]) -> dict[str, Any]:
    """Lit le journal pour un administrateur, refuse les autres rôles.

    La vérification est **ici** (et pas seulement au contrat) : le modèle ne
    peut pas contourner un rôle en changeant les arguments, et un graphe
    partagé ne peut pas servir le journal à un compte non administrateur.
    """
    role = _role_du_run()
    if role != RoleUtilisateur.ADMINISTRATEUR.value:
        raise PermissionDeniedError(
            "Le journal d'audit n'est lisible par un agent que pour un compte "
            "administrateur.",
            details={"role": role},
        )
    entree = ConsulterJournalAuditInput.model_validate(payload)
    if (
        entree.date_debut is not None
        and entree.date_fin is not None
        and entree.date_debut > entree.date_fin
    ):
        raise ValidationError(
            "La date de début doit précéder la date de fin.",
            details={
                "date_debut": entree.date_debut.isoformat(),
                "date_fin": entree.date_fin.isoformat(),
            },
        )

    page = JournalAuditService(session).lister(
        acteur_type=entree.acteur_type,
        action=entree.action,
        entity_type=types_entite([entree.entity_type]) if entree.entity_type else None,
        entity_id=entree.entity_id,
        depuis=_horodatage(entree.date_debut, fin=False) if entree.date_debut else None,
        jusqu_a=_horodatage(entree.date_fin, fin=True) if entree.date_fin else None,
        limit=min(entree.limite, TAILLE_OUTIL_MAX),
        offset=0,
    )
    # Un événement complet porte ``before``/``after`` : volumineux et parfois
    # porteur de données personnelles. L'outil rend la trace lisible (qui, quoi,
    # quand, sur quoi) et laisse le détail à l'écran d'audit, où l'humain décide
    # de l'ouvrir.
    elements = [
        {
            "id": str(ligne["id"]),
            "quand": ligne["created_at"].isoformat()
            if isinstance(ligne["created_at"], datetime)
            else str(ligne["created_at"]),
            "acteur": ligne["acteur_libelle"] or ligne["acteur_id"],
            "acteur_type": ligne["acteur_type"],
            "action": ligne["action"],
            "libelle": ligne["libelle"],
            "entite": ligne["entity_type"],
            "entite_id": ligne["entity_id"],
            "motif": ligne["motif"],
            "demande_par": ligne["demande_par_libelle"] or ligne["demande_par"],
        }
        for ligne in page["elements"]
    ]
    return {
        "total": page["total"],
        "elements": elements,
        "limite": page["limit"],
        "tronque": page["total"] > len(elements),
    }


def construire_tools_audit(definition: Any, session_factory: Any) -> tuple[Any, ...]:
    """Outil d'audit du contrat de l'agent (rien si le contrat ne le déclare pas)."""
    if NOM_OUTIL not in definition.allowed_tools():
        return ()
    kit = AgentTools(definition, readonly=True, session_factory=session_factory)
    kit.enregistrer(
        OperationSpec(
            name=NOM_OUTIL,
            description=(
                "Journal d'audit CARSO : qui a fait quoi, quand, sur quelle entité "
                "(lecture seule, réservée à un compte administrateur). Filtrer par "
                "entité (type + identifiant) pour l'histoire d'un dossier, ou par "
                "dates pour une période. Ne remplace pas les données métier : la "
                "donnée vient des outils de recherche, la trace dit qui l'a modifiée."
            ),
            input_schema=ConsulterJournalAuditInput,
            run=_consulter,
        )
    )
    return kit.construire()
