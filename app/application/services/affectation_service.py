"""Service métier — affectations d'équipe (P3 : proposition → décision humaine).

Règles appliquées :
- règle 3 [C] : le rôle est défini DANS la mission (AffectationEquipe), pas
  sur la personne ;
- règle 6 [C] : toute affectation d'origine IA (ou humaine) n'entre en
  vigueur qu'après approbation humaine explicite (Approbation + AuditEvent) ;
- Q6 [P] : AUCUNE règle de disponibilité inventée — le service vérifie
  uniquement les invariants confirmés (existence, mission ouverte au
  staffing), jamais une disponibilité supposée ;
- Q5 [P] : le vocabulaire des rôles n'est pas figé — le service n'impose
  aucune liste, la table de référence viendra de l'atelier CARSO.
"""

from uuid import UUID

from sqlalchemy.orm import Session

from app.application.dto import AffectationProposee
from app.application.services import notifications_evenements as notifications
from app.application.trace import Decision, TraceContext
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.domain.enums import (
    ActionAudit,
    ActorType,
    DecisionApprobation,
    RoleMission,
    SourceAffectation,
    StatutAffectation,
    StatutMission,
    TypeProposition,
)
from app.domain.execution import AffectationEquipe, Mission
from app.domain.state_machines import validate_transition
from app.infrastructure.repositories import (
    AffectationEquipeRepository,
    EquipeRepository,
    MissionRepository,
)

_ENTITY = "affectation_equipe"
_MISSIONS_STAFFABLES = {StatutMission.PLANIFIEE, StatutMission.EN_PREPARATION}


class AffectationService:
    """Use cases : proposer, approuver, refuser, lister pour une mission."""

    def __init__(self, session: Session, actor_id: str | None = None) -> None:
        self._session = session
        self.missions = MissionRepository(session)
        self.equipes = EquipeRepository(session)
        self.affectations = AffectationEquipeRepository(session)
        self._trace = TraceContext(session, actor_id=actor_id)

    def proposer(self, proposal: AffectationProposee) -> AffectationEquipe:
        """Crée une affectation en statut ``proposee``.

        Acceptable d'un humain (``manual``) comme d'un agent (``ai_proposal``) :
        dans les deux cas la mise en vigueur exige la décision humaine
        (règle 6 — même une proposition manuelle est revue avant approbation).
        """
        mission = self._get_mission(proposal.mission_id)
        if mission.statut not in {s.value for s in _MISSIONS_STAFFABLES}:
            msg = f"Mission {mission.reference} non ouverte au staffing ({mission.statut})"
            raise ConflictError(msg)

        if self.equipes.get(proposal.equipe_id) is None:
            raise NotFoundError(f"Membre d'équipe {proposal.equipe_id} introuvable")

        source = proposal.source
        if source not in {SourceAffectation.MANUAL.value, SourceAffectation.AI_PROPOSAL.value}:
            msg = f"source d'affectation inconnue: {source!r}"
            raise ConflictError(msg)

        affectation = AffectationEquipe(
            mission_id=proposal.mission_id,
            equipe_id=proposal.equipe_id,
            role_dans_mission=proposal.role_dans_mission,
            date_debut=proposal.date_debut,
            date_fin=proposal.date_fin,
            statut=StatutAffectation.PROPOSEE.value,
            source_affectation=source,
        )
        self.affectations.add(affectation)
        self._session.flush()  # id attribué avant la trace d'audit
        self._trace.record_event(
            actor_type=(
                ActorType.AGENT.value
                if source == SourceAffectation.AI_PROPOSAL.value
                else ActorType.HUMAIN.value
            ),
            action=ActionAudit.AFFECTATION_PROPOSEE.value,
            entity_type=_ENTITY,
            entity_id=affectation.id,
            after={
                "mission_id": str(proposal.mission_id),
                "equipe_id": str(proposal.equipe_id),
                "role": proposal.role_dans_mission,
                "source": source,
            },
            event_metadata={"proposed_by": proposal.proposed_by},
        )
        notifications.notifier_affectation_proposee(
            self._session,
            affectation,
            mission.titre,
            affectation.role_dans_mission,
        )
        return affectation

    def approuver(self, affectation_id: UUID, decision: Decision) -> AffectationEquipe:
        """Décision humaine favorable : proposee → approuvee, ``approved_by``
        renseigné, Approbation + AuditEvent."""
        affectation = self._get(affectation_id)
        current = self._current_statut(affectation)
        validate_transition(_ENTITY, current, StatutAffectation.APPROUVEE.value)

        affectation.statut = StatutAffectation.APPROUVEE
        affectation.approved_by = decision.decided_by
        self._trace.record_decision(
            entity_type=_ENTITY,
            entity_id=affectation.id,
            proposal_type=TypeProposition.AFFECTATION_EQUIPE.value,
            decision=DecisionApprobation.APPROUVE.value,
            proposed_by_agent=(
                self._agent_proposeur(affectation)
                if affectation.source_affectation == SourceAffectation.AI_PROPOSAL.value
                else None
            ),
            decided_by=decision.decided_by,
            reason=decision.reason,
            after={"statut": StatutAffectation.APPROUVEE.value},
        )
        notifications.notifier_affectation_confirmee(
            self._session, affectation, affectation.mission.titre, affectation.role_dans_mission
        )
        return affectation

    def refuser(self, affectation_id: UUID, decision: Decision) -> AffectationEquipe:
        """Décision humaine défavorable : proposee → refusee (tracée, conservée)."""
        affectation = self._get(affectation_id)
        current = self._current_statut(affectation)
        validate_transition(_ENTITY, current, StatutAffectation.REFUSEE.value)

        affectation.statut = StatutAffectation.REFUSEE
        self._trace.record_decision(
            entity_type=_ENTITY,
            entity_id=affectation.id,
            proposal_type=TypeProposition.AFFECTATION_EQUIPE.value,
            decision=DecisionApprobation.REJETE.value,
            proposed_by_agent=(
                self._agent_proposeur(affectation)
                if affectation.source_affectation == SourceAffectation.AI_PROPOSAL.value
                else None
            ),
            decided_by=decision.decided_by,
            reason=decision.reason,
            after={"statut": StatutAffectation.REFUSEE.value},
        )
        return affectation

    def supprimer(self, affectation_id: UUID, reason: str | None = None) -> None:
        """Retire une affectation **refusée** de la liste (demande 30/09).

        Une affectation refusée est déjà sans effet (elle n'entre jamais en
        vigueur) : la garder dans la liste n'apporte que du bruit. La
        suppression est donc réservée à ce seul état — une proposition en
        attente doit recevoir sa décision (approuver/refuser), une affectation
        approuvée fait partie de l'historique de la mission et ne peut pas
        disparaître sans trace. Le retrait est tracé dans l'audit.
        """
        affectation = self._get(affectation_id)
        statut = self._current_statut(affectation)
        if statut != StatutAffectation.REFUSEE.value:
            raise ConflictError(
                "Seule une affectation refusée peut être retirée de la liste",
                details={"affectation_id": str(affectation_id), "statut": statut},
            )
        avant = {
            "mission_id": str(affectation.mission_id),
            "equipe_id": str(affectation.equipe_id),
            "role": affectation.role_dans_mission,
        }
        self._session.delete(affectation)
        self._session.flush()
        self._trace.record_event(
            actor_type=ActorType.HUMAIN.value,
            action=ActionAudit.AFFECTATION_REFUSEE.value,
            entity_type=_ENTITY,
            entity_id=affectation.id,
            before=avant,
            event_metadata={
                "retrait": True,
                "motif": reason or "Retrait d'une affectation refusée",
            },
        )

    def lister_pour_mission(self, mission_id: UUID) -> list[AffectationEquipe]:
        """Affectations d'une mission, pour la UI de validation."""
        self._get_mission(mission_id)  # NotFoundError si mission absente
        return self.affectations.list_for_mission(mission_id)

    def modifier(
        self,
        affectation_id: UUID,
        *,
        role_dans_mission: str | None = None,
        date_debut=None,
        date_fin=None,
    ) -> AffectationEquipe:
        """Corrige une affectation : rôle et/ou période, jamais la personne.

        Changer le **membre** n'est pas une modification : une autre personne
        passe par une nouvelle proposition (refus de l'ancienne, proposition de
        la nouvelle) — l'historique des décisions reste lisible.

        Règles :
        - une affectation **refusée** est figée : la modifier la réécrirait
          après coup ; il faut re-proposer ;
        - une affectation **approuvée** reste modifiable (l'utilisateur ajuste
          un rôle ou une période en cours de préparation) : la modification est
          tracée dans l'audit, avec l'état avant/après ;
        - les dates bornent la participation dans la mission : une période
          incohérente (fin avant début) est refusée — sans règle inventée sur la
          disponibilité de la personne (Q6 [P]).
        """
        affectation = self._get(affectation_id)
        statut = affectation.statut or StatutAffectation.PROPOSEE.value
        if statut == StatutAffectation.REFUSEE.value:
            raise ConflictError(
                "Une affectation refusée n'est pas modifiable",
                details={"affectation_id": str(affectation_id), "statut": statut},
            )

        modifications: dict[str, object] = {}
        if role_dans_mission is not None:
            role = role_dans_mission.strip()
            if not role:
                raise ValidationError("Le rôle dans la mission ne peut pas être vide")
            if role not in {r.value for r in RoleMission}:
                raise ValidationError(
                    f"Rôle inconnu : {role!r}",
                    details={"roles_attendus": sorted(r.value for r in RoleMission)},
                )
            if role != affectation.role_dans_mission:
                modifications["role_dans_mission"] = role
        if date_debut is not None and date_debut != affectation.date_debut:
            modifications["date_debut"] = str(date_debut)
        if date_fin is not None and date_fin != affectation.date_fin:
            modifications["date_fin"] = str(date_fin)

        debut = date_debut if date_debut is not None else affectation.date_debut
        fin = date_fin if date_fin is not None else affectation.date_fin
        if debut and fin and fin < debut:
            raise ValidationError(
                "La date de fin ne peut pas précéder la date de début",
                details={"date_debut": str(debut), "date_fin": str(fin)},
            )

        if not modifications:
            return affectation  # rien à changer : ni écriture, ni trace

        before = {
            "role": affectation.role_dans_mission,
            "date_debut": str(affectation.date_debut) if affectation.date_debut else None,
            "date_fin": str(affectation.date_fin) if affectation.date_fin else None,
        }
        affectation.role_dans_mission = modifications.get(
            "role_dans_mission", affectation.role_dans_mission
        )
        if "date_debut" in modifications:
            affectation.date_debut = date_debut
        if "date_fin" in modifications:
            affectation.date_fin = date_fin
        self._session.flush()
        self._trace.record_event(
            actor_type=ActorType.HUMAIN.value,
            action=ActionAudit.AFFECTATION_MODIFIEE.value,
            entity_type=_ENTITY,
            entity_id=affectation.id,
            before=before,
            after={
                "role": affectation.role_dans_mission,
                "date_debut": str(affectation.date_debut) if affectation.date_debut else None,
                "date_fin": str(affectation.date_fin) if affectation.date_fin else None,
            },
        )
        return affectation

    # --- internes ---------------------------------------------------------

    def _get_mission(self, mission_id: UUID) -> Mission:
        mission = self.missions.get(mission_id)
        if mission is None:
            raise NotFoundError(f"Mission {mission_id} introuvable")
        return mission

    def _get(self, affectation_id: UUID) -> AffectationEquipe:
        affectation = self.affectations.get(affectation_id)
        if affectation is None:
            raise NotFoundError(f"Affectation {affectation_id} introuvable")
        return affectation

    @staticmethod
    def _current_statut(affectation: AffectationEquipe) -> str:
        """Statut courant, avec erreur explicite si absent (invariant applicatif)."""
        if affectation.statut is None:
            msg = f"Affectation {affectation.id} sans statut"
            raise ConflictError(msg)
        return affectation.statut

    @staticmethod
    def _agent_proposeur(affectation: AffectationEquipe) -> str | None:
        """Nom de l'agent proposeur si la source est une proposition IA.

        Phase 4 : le payload d'audit (event_metadata.proposed_by) porte le nom ;
        la colonne dédiée viendra avec le runtime agents (instruction/05).
        """
        return None
