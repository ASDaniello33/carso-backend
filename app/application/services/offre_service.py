"""Service métier — offres génériques (brouillon → revue → publication).

Règles appliquées :

- une **offre répond toujours à ``Appel + Lot``** : le lot est obligatoire et
  porte l'appel ; le service vérifie la cohérence de l'appel fourni ;
- **type explicite** (``OFFRE_TECHNIQUE`` / ``OFFRE_FINANCIERE`` / ``AUTRE``) :
  un même lot peut porter une offre technique **et** une offre financière,
  d'où l'unicité ``(lot_id, type)`` ;
- règle 6 [C] : la publication est une décision humaine (Approbation) ;
- règle 7 [C] : les montants de budget sont recalculés côté serveur.

Aucune logique « une offre est forcément liée à une formation » : une offre est
générique.
"""

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy.orm import Session

from app.application.dto import BudgetTotal, LigneBudgetInput, OffreUpdateInput
from app.application.services import notifications_evenements as notifications
from app.application.trace import Decision, TraceContext
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.domain.enums import (
    ActionAudit,
    ActorType,
    DecisionApprobation,
    StatutBudget,
    StatutOffre,
    TypeOffre,
    TypeProposition,
)
from app.domain.organization import Budget, LigneBudget, Offre
from app.domain.rules import compute_budget_total, compute_cout_total
from app.domain.state_machines import validate_transition
from app.infrastructure.repositories import (
    BudgetRepository,
    LigneBudgetRepository,
    LotRepository,
    ModeleDocumentRepository,
    OffreRepository,
)

_ENTITY = "offre"


def _iso(valeur: date | None) -> str | None:
    """Date → chaîne ISO : l'audit ne stocke que des valeurs sérialisables."""
    return valeur.isoformat() if valeur is not None else None


class OffreService:
    """Use cases : creer_brouillon, soumettre_revue, publier, archiver,
    ajouter_ligne_budget, approuver_budget, total_budget."""

    def __init__(self, session: Session, actor_id: str | None = None) -> None:
        self._session = session
        self.offres = OffreRepository(session)
        self.lots = LotRepository(session)
        self.modeles = ModeleDocumentRepository(session)
        self.budgets = BudgetRepository(session)
        self.lignes = LigneBudgetRepository(session)
        self._trace = TraceContext(session, actor_id=actor_id)

    def creer_brouillon(
        self,
        lot_id: UUID,
        reference: str,
        titre: str,
        *,
        type: str,
        appel_a_proposition_id: UUID | None = None,
        modele_document_id: UUID | None = None,
    ) -> Offre:
        """Crée l'offre en brouillon pour **un lot et un type donnés**.

        L'appel est déduit du lot ; s'il est fourni, il doit correspondre
        exactement (une offre répond à ``Appel + Lot``). Un second brouillon du
        même type pour le même lot est refusé (409).
        """
        if type not in {t.value for t in TypeOffre}:
            raise ValidationError(
                f"Type d'offre inconnu : {type!r}",
                details={"types_valides": sorted(t.value for t in TypeOffre)},
            )

        lot = self.lots.get(lot_id)
        if lot is None:
            raise NotFoundError(f"Lot {lot_id} introuvable")
        appel = lot.appel_a_proposition

        if appel_a_proposition_id is not None and appel_a_proposition_id != appel.id:
            raise ValidationError(
                "L'appel fourni ne correspond pas à celui du lot",
                details={
                    "lot_id": str(lot_id),
                    "appel_du_lot": str(appel.id),
                    "appel_fourni": str(appel_a_proposition_id),
                },
            )

        if self.offres.get_for_lot_and_type(lot_id, type) is not None:
            raise ConflictError(
                f"Une offre de type {type!r} existe déjà pour le lot {lot.numero}",
                details={"lot_id": str(lot_id), "numero": lot.numero, "type": type},
            )

        if modele_document_id is not None and self.modeles.get(modele_document_id) is None:
            raise NotFoundError(f"Modèle de document {modele_document_id} introuvable")

        offre = Offre(
            organisation_id=appel.organisation_id,
            appel_a_proposition_id=appel.id,
            lot_id=lot_id,
            type=type,
            reference=reference,
            titre=titre,
            statut=StatutOffre.BROUILLON.value,
            modele_document_id=modele_document_id,
        )
        self.offres.add(offre)
        self._session.flush()  # id attribué avant la trace d'audit
        self._trace.record_event(
            actor_type=ActorType.HUMAIN.value,
            action=ActionAudit.OFFRE_CREEE.value,
            entity_type=_ENTITY,
            entity_id=offre.id,
            after={"lot_id": str(lot_id), "reference": reference, "type": type},
        )
        return offre

    def soumettre_revue(self, offre_id: UUID) -> Offre:
        """brouillon → en_revue : mise à disposition pour décision humaine."""
        offre = self._get(offre_id)
        validate_transition(_ENTITY, offre.statut, StatutOffre.EN_REVUE.value)
        offre.statut = StatutOffre.EN_REVUE
        self._trace.record_event(
            actor_type=ActorType.HUMAIN.value,
            action=ActionAudit.OFFRE_SOUMISE.value,
            entity_type=_ENTITY,
            entity_id=offre.id,
            after={"statut": StatutOffre.EN_REVUE.value},
        )
        notifications.notifier_offre_a_valider(self._session, offre)
        return offre

    def publier(self, offre_id: UUID, decision: Decision) -> Offre:
        """Décision humaine de publication : en_revue → approuve, ``approved_at``
        horodaté, Approbation + AuditEvent dans la même transaction."""
        offre = self._get(offre_id)
        validate_transition(_ENTITY, offre.statut, StatutOffre.APPROUVE.value)

        offre.statut = StatutOffre.APPROUVE
        offre.approved_at = datetime.now().astimezone()
        self._trace.record_decision(
            entity_type=_ENTITY,
            entity_id=offre.id,
            proposal_type=TypeProposition.PUBLICATION_OFFRE.value,
            decision=DecisionApprobation.APPROUVE.value,
            proposed_by_agent=None,
            decided_by=decision.decided_by,
            reason=decision.reason,
            after={"statut": StatutOffre.APPROUVE.value},
        )
        notifications.notifier_offre_decision(self._session, offre, "approuvée")
        return offre

    def archiver(self, offre_id: UUID, decision: Decision) -> Offre:
        """brouillon|en_revue|approuve → archive (décision humaine tracée)."""
        offre = self._get(offre_id)
        validate_transition(_ENTITY, offre.statut, StatutOffre.ARCHIVE.value)
        offre.statut = StatutOffre.ARCHIVE
        self._trace.record_event(
            actor_type=ActorType.HUMAIN.value,
            action=ActionAudit.OFFRE_ARCHIVEE.value,
            entity_type=_ENTITY,
            entity_id=offre.id,
            after={"statut": StatutOffre.ARCHIVE.value},
            event_metadata={"decided_by": decision.decided_by},
        )
        return offre

    def ajouter_ligne_budget(
        self,
        budget_id: UUID,
        ligne: LigneBudgetInput,
    ) -> LigneBudget:
        """Ajoute une ligne à un budget NON approuvé. ``cout_total`` recalculé
        par la règle déterministe — jamais accepté de l'extérieur (règle 7)."""
        budget = self._get_budget(budget_id)
        if budget.statut == StatutBudget.APPROUVE.value:
            msg = "Impossible de modifier un budget approuvé — créer une nouvelle version"
            raise ConflictError(msg)
        if budget.statut not in {StatutBudget.BROUILLON.value, StatutBudget.PROPOSE.value}:
            msg = f"Statut budget incompatible: {budget.statut!r}"
            raise ConflictError(msg)

        entity = LigneBudget(
            budget_id=budget_id,
            categorie=ligne.categorie,
            description=ligne.description,
            unite=ligne.unite,
            quantite=ligne.quantite,
            cout_unitaire=ligne.cout_unitaire,
            cout_total=compute_cout_total(ligne.quantite, ligne.cout_unitaire),
        )
        self.lignes.add(entity)
        self._session.flush()  # id de la ligne avant la trace
        self._trace.record_event(
            actor_type=ActorType.HUMAIN.value,
            action=ActionAudit.BUDGET_LIGNE_AJOUTEE.value,
            entity_type="budget",
            entity_id=budget.id,
            after={"ligne_id": str(entity.id), "cout_total": str(entity.cout_total)},
        )
        return entity

    def approuver_budget(self, budget_id: UUID, decision: Decision) -> Budget:
        """Décision humaine d'approbation d'un budget proposé."""
        budget = self._get_budget(budget_id)
        validate_transition("budget", budget.statut, StatutBudget.APPROUVE.value)
        budget.statut = StatutBudget.APPROUVE
        self._trace.record_decision(
            entity_type="budget",
            entity_id=budget.id,
            proposal_type=TypeProposition.BUDGET.value,
            decision=DecisionApprobation.APPROUVE.value,
            proposed_by_agent=None,
            decided_by=decision.decided_by,
            reason=decision.reason,
        )
        return budget

    def total_budget(self, budget_id: UUID) -> BudgetTotal:
        """Total déterministe d'un budget, recalculé depuis les lignes."""
        budget = self._get_budget(budget_id)
        lignes = self.lignes.list_for_budget(budget_id)
        total = compute_budget_total(
            Decimal(str(ligne.cout_total)) for ligne in lignes
        )
        return BudgetTotal(
            budget_id=budget_id, devise=budget.devise, total=total, nb_lignes=len(lignes)
        )

    def modifier(self, offre_id: UUID, entree: OffreUpdateInput) -> Offre:
        """Modification d'identité : titre, type, échéances prévues.

        Ce qui ne bouge jamais : l'appel et le lot (une offre répond à
        ``Appel + Lot`` — changer de lot, c'est une autre offre). Le **type** est
        modifiable, mais l'unicité ``(lot_id, type)`` reste vérifiée : le
        changement échoue en 409 si le lot porte déjà une offre de ce type.

        Une offre archivée est figée : la rouvrir n'est pas une modification.

        Raises:
            ConflictError: offre archivée, ou type déjà porté par le lot.
            ValidationError: type inconnu, titre vide, ou aucune modification.
        """
        offre = self._get(offre_id)
        if offre.statut == StatutOffre.ARCHIVE.value:
            raise ConflictError(
                "Une offre archivée n'est pas modifiable",
                details={"offre_id": str(offre_id), "statut": offre.statut},
            )

        avant = {
            "titre": offre.titre,
            "type": offre.type,
            "date_debut_prevue": _iso(offre.date_debut_prevue),
            "date_fin_prevue": _iso(offre.date_fin_prevue),
        }
        modifie = False

        if entree.titre is not None and entree.titre.strip():
            titre = entree.titre.strip()
            if titre != offre.titre:
                offre.titre = titre
                modifie = True

        if entree.type is not None and entree.type != offre.type:
            if entree.type not in {t.value for t in TypeOffre}:
                raise ValidationError(
                    f"Type d'offre inconnu : {entree.type!r}",
                    details={"types_valides": sorted(t.value for t in TypeOffre)},
                )
            if self.offres.get_for_lot_and_type(offre.lot_id, entree.type) is not None:
                raise ConflictError(
                    f"Ce lot porte déjà une offre de type {entree.type!r}",
                    details={"lot_id": str(offre.lot_id), "type": entree.type},
                )
            offre.type = entree.type
            modifie = True

        for nom in ("date_debut_prevue", "date_fin_prevue"):
            valeur = getattr(entree, nom)
            if valeur is not None and getattr(offre, nom) != valeur:
                setattr(offre, nom, valeur)
                modifie = True

        if not modifie:
            raise ValidationError(
                "Aucune modification fournie pour l'offre",
                details={"offre_id": str(offre_id)},
            )

        self._trace.record_event(
            actor_type=ActorType.HUMAIN.value,
            action=ActionAudit.OFFRE_MODIFIEE.value,
            entity_type=_ENTITY,
            entity_id=offre.id,
            after={
                "titre": offre.titre,
                "type": offre.type,
                "date_debut_prevue": _iso(offre.date_debut_prevue),
                "date_fin_prevue": _iso(offre.date_fin_prevue),
            },
            event_metadata={"avant": avant},
        )
        return offre

    # --- lecture (pour la couche API) ---------------------------------------

    def obtenir(self, offre_id: UUID) -> Offre:
        """Charge une offre (lecture pure, sans mutation)."""
        return self._get(offre_id)

    def lister(
        self, statut: str | None = None, type: str | None = None
    ) -> list[Offre]:
        """Liste des offres, filtrée par statut et/ou par type (lecture pure)."""
        if statut is not None:
            offres = self.offres.list_by_statut(statut)
            if type is not None:
                return [o for o in offres if o.type == type]
            return offres
        if type is not None:
            return self.offres.list_by_type(type)
        return self.offres.list_all()

    def lister_pour_lot(self, lot_id: UUID) -> list[Offre]:
        """Offres d'un lot (au plus une par type : technique + financière)."""
        return self.offres.list_for_lot(lot_id)

    # --- internes ---------------------------------------------------------

    def _get(self, offre_id: UUID) -> Offre:
        offre = self.offres.get(offre_id)
        if offre is None:
            raise NotFoundError(f"Offre {offre_id} introuvable")
        return offre

    def _get_budget(self, budget_id: UUID) -> Budget:
        budget = self.budgets.get(budget_id)
        if budget is None:
            raise NotFoundError(f"Budget {budget_id} introuvable")
        return budget


__all__ = ["OffreService"]
