"""Schémas API — affectations d'équipe (proposition → décision humaine)."""

from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class AffectationProposeeCreate(BaseModel):
    """Proposition d'affectation (humaine ou agent) — naît en ``proposee``.

    L'auteur (``proposed_by``) n'est **pas** un champ du corps : la route le
    dérive de l'utilisateur authentifié (``str(user.id)``). Une donnée
    d'attribution ne vient jamais du client (AGENTS.md §9). Le champ a existé
    ici en ``requis`` alors qu'il était ignoré — tout appelant qui ne
    l'envoyait pas recevait un 422 illisible (correctif incrément 9).
    """

    mission_id: UUID
    equipe_id: UUID
    role_dans_mission: str = Field(min_length=1, max_length=100)
    source: Literal["manual", "ai_proposal"] = "manual"
    date_debut: date | None = None
    date_fin: date | None = None


class AffectationUpdate(BaseModel):
    """Modification d'une affectation : rôle et/ou période, jamais la personne.

    Un champ absent n'est pas appliqué. ``equipe_id`` est volontairement absent
    du schéma (``extra="forbid"``) : changer de membre passe par une nouvelle
    proposition (refus + nouvelle affectation) pour garder l'historique lisible.
    """

    model_config = ConfigDict(extra="forbid")

    role_dans_mission: str | None = Field(default=None, min_length=1, max_length=100)
    date_debut: date | None = None
    date_fin: date | None = None


class AffectationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    mission_id: UUID
    equipe_id: UUID
    role_dans_mission: str
    statut: str | None = None
    source_affectation: str
    approved_by: str | None = None
    date_debut: date | None = None
    date_fin: date | None = None


class MissionAffectationsRead(BaseModel):
    """Liste des affectations d'une mission pour la UI de validation."""

    mission_id: UUID
    affectations: list[AffectationRead]


class CandidatClasse(BaseModel):
    """Candidat du vivier classé par l'AgentRH — score explicable,
    construit uniquement à partir des données enregistrées (aucune invention)."""

    equipe_id: UUID
    nom: str
    prenom: str
    profil: str | None = None
    score: int
    score_max: int
    criteres: dict[str, bool]
    deja_affecte: bool = False


class PropositionAgentRhRead(BaseModel):
    """Réponse de l'AgentRH : candidats classés + affectations proposées
    en attente de décision humaine (HITL)."""

    mission_id: UUID
    role_dans_mission: str
    propositions: list[AffectationRead]
    candidats: list[CandidatClasse]
    message: str
