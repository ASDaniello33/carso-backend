"""Service métier — budgets et lignes de budget (instruction/04, Phase 2 §5).

Le modèle et les tables existaient depuis la Phase 2, mais **aucun code ne
créait de budget** : les routes de ligne, d'approbation et de total ne pouvaient
donc jamais être atteintes (aucun ``budget_id`` n'existait). Ce service ferme la
chaîne :

```text
creer_budget(offre_financière) → lignes → soumettre → approuver
        ↓ reprise (budget approuvé)
creer le budget de la MISSION (copie des lignes, brouillon)
```

Règles appliquées :

- **Règle 7 [C]** — ``cout_total`` et le total sont **recalculés** côté serveur
  (``quantite × cout_unitaire``, puis somme) ; aucun montant fourni par un client
  ou par un agent n'est accepté comme officiel.
- **Human-in-the-loop** — ``brouillon → propose → approuve`` via
  ``validate_transition`` (machine à états existante) ; un budget approuvé est
  **verrouillé** : toute correction passe par une nouvelle version.
- **Un budget par porteur** — une offre (ou une mission) a un budget ; les
  révisions créent une **ligne de budget** de version suivante (``version``),
  jamais une réécriture silencieuse.
- **Reprise offre → mission** — seule une version **approuvée** est reprise :
  recopier un chiffrage non validé dans l'exécution répandrait des montants
  officieux. La reprise est tracée (``budget.repris``) et le budget de mission
  repart en ``brouillon`` pour être ajusté à l'exécution.
- **Traçabilité** — chaque mutation écrit un ``AuditEvent`` dans la transaction
  courante ; le service ne fait ni commit ni rollback (l'appelant décide).
"""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from sqlalchemy.orm import Session

from app.application.dto import (
    BudgetComplet,
    BudgetResume,
    BudgetTotal,
    LigneBudgetInput,
)
from app.application.perimetre import Perimetre, perimetre_effectif
from app.application.trace import Decision, TraceContext
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.domain.enums import (
    ActionAudit,
    ActorType,
    StatutBudget,
    TypeOffre,
    TypeProposition,
)
from app.domain.execution import Mission
from app.domain.organization import Budget, LigneBudget, Offre
from app.domain.rules import compute_budget_total, compute_cout_total
from app.domain.state_machines import validate_transition
from app.infrastructure.repositories import (
    BudgetRepository,
    LigneBudgetRepository,
    MissionRepository,
    OffreRepository,
)

_ENTITY = "budget"

#: Statuts dans lesquels un budget reste modifiable.
_MODIFIABLES = {StatutBudget.BROUILLON.value, StatutBudget.PROPOSE.value}

#: Formats d'export acceptés (mêmes moteurs que les documents générés).
EXPORTS = ("xlsx", "docx")

_MIME = {
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


class BudgetService:
    """Use cases budget : création, lecture, lignes, soumission, version, reprise."""

    def __init__(
        self,
        session: Session,
        actor_id: str | None = None,
        *,
        perimetre: Perimetre | None = None,
    ) -> None:
        self._session = session
        self._perimetre = perimetre if perimetre is not None else perimetre_effectif()
        self.budgets = BudgetRepository(session)
        self.lignes = LigneBudgetRepository(session)
        self.offres = OffreRepository(session)
        self.missions = MissionRepository(session)
        self._trace = TraceContext(session, actor_id=actor_id)

    # -- création ---------------------------------------------------------
    def creer_budget(
        self,
        *,
        offre_id: UUID | None = None,
        mission_id: UUID | None = None,
        devise: str | None = None,
    ) -> Budget:
        """Crée le budget d'une offre **ou** d'une mission (jamais les deux).

        Un second budget pour le même porteur est refusé : une révision passe
        par :meth:`nouvelle_version` (l'historique des montants reste lisible).

        Raises:
            ValidationError: aucun porteur, ou les deux à la fois.
            NotFoundError: offre/mission inexistante ou hors périmètre.
            ConflictError: un budget existe déjà pour ce porteur.
        """
        if (offre_id is None) == (mission_id is None):
            raise ValidationError(
                "Un budget se rattache à une offre OU à une mission (un seul)",
                details={"offre_id": offre_id, "mission_id": mission_id},
            )
        if offre_id is not None:
            offre = self._offre_lisible(offre_id)
            # Le budget est le chiffrage de l'**offre financière** (règle
            # validée le 04/11) : une offre technique ou « autre » n'en porte
            # pas, ses montants n'existent pas dans ce dossier.
            if offre.type != TypeOffre.OFFRE_FINANCIERE.value:
                raise ValidationError(
                    "Un budget ne se rattache qu'à une offre financière",
                    details={"offre_id": str(offre_id), "type": offre.type},
                )
            if self.budgets.list_for_offre(offre_id):
                raise ConflictError(
                    "Un budget existe déjà pour cette offre : créer une "
                    "nouvelle version plutôt qu'un second budget",
                    details={"offre_id": str(offre_id)},
                )
        if mission_id is not None:
            self._mission_lisible(mission_id)
            if self.budgets.list_for_mission(mission_id):
                raise ConflictError(
                    "Un budget existe déjà pour cette mission : créer une "
                    "nouvelle version plutôt qu'un second budget",
                    details={"mission_id": str(mission_id)},
                )

        budget = Budget(
            offre_id=offre_id,
            mission_id=mission_id,
            devise=(devise or "").strip() or "MAD",
            statut=StatutBudget.BROUILLON.value,
            version=1,
        )
        self.budgets.add(budget)
        self._session.flush()  # identifiant disponible pour la trace
        self._trace.record_event(
            actor_type=ActorType.HUMAIN.value,
            action=ActionAudit.BUDGET_CREE.value,
            entity_type=_ENTITY,
            entity_id=budget.id,
            after={
                "offre_id": str(offre_id) if offre_id else None,
                "mission_id": str(mission_id) if mission_id else None,
                "devise": budget.devise,
                "statut": budget.statut,
                "version": budget.version,
            },
        )
        return budget

    # -- lecture ----------------------------------------------------------
    def lister_budgets_offre(self, offre_id: UUID) -> list[BudgetResume]:
        """Budgets d'une offre, version la plus récente d'abord."""
        self._offre_lisible(offre_id)
        return [
            self._resume(budget)
            for budget in sorted(
                self.budgets.list_for_offre(offre_id),
                key=lambda budget: budget.version,
                reverse=True,
            )
        ]

    def lister_budgets_mission(self, mission_id: UUID) -> list[BudgetResume]:
        """Budgets d'une mission, version la plus récente d'abord."""
        self._mission_lisible(mission_id)
        return [
            self._resume(budget)
            for budget in sorted(
                self.budgets.list_for_mission(mission_id),
                key=lambda budget: budget.version,
                reverse=True,
            )
        ]

    def lire_budget(self, budget_id: UUID) -> BudgetComplet:
        """Budget détaillé : entête, lignes ordonnées, total recalculé."""
        budget = self._get(budget_id)
        lignes = tuple(self.lignes.list_for_budget(budget.id))
        total = compute_budget_total(
            Decimal(str(ligne.cout_total)) for ligne in lignes
        )
        return BudgetComplet(
            budget_id=budget.id,
            offre_id=budget.offre_id,
            mission_id=budget.mission_id,
            devise=budget.devise,
            statut=budget.statut,
            version=budget.version,
            lignes=lignes,
            total=total,
        )

    def total_budget(self, budget_id: UUID) -> BudgetTotal:
        """Total déterministe d'un budget (même règle que l'affichage)."""
        detail = self.lire_budget(budget_id)
        return BudgetTotal(
            budget_id=detail.budget_id,
            devise=detail.devise,
            total=detail.total,
            nb_lignes=len(detail.lignes),
        )

    # -- lignes -----------------------------------------------------------
    def ajouter_ligne_budget(self, budget_id: UUID, ligne: LigneBudgetInput) -> LigneBudget:
        """Ajoute une ligne à un budget non approuvé (``cout_total`` recalculé)."""
        budget = self._get_modifiable(budget_id)
        entity = self._ligne(budget_id, ligne, ordre=self._prochain_ordre(budget_id))
        self.lignes.add(entity)
        self._session.flush()
        self._trace_ligne(ActionAudit.BUDGET_LIGNE_AJOUTEE, budget.id, entity)
        return entity

    def modifier_ligne_budget(
        self, budget_id: UUID, ligne_id: UUID, ligne: LigneBudgetInput
    ) -> LigneBudget:
        """Corrige une ligne (même calcul déterministe), budget non approuvé."""
        budget = self._get_modifiable(budget_id)
        entity = self._get_ligne(budget_id, ligne_id)
        avant = {
            "categorie": entity.categorie,
            "quantite": str(entity.quantite),
            "cout_unitaire": str(entity.cout_unitaire),
            "cout_total": str(entity.cout_total),
        }
        entity.categorie = ligne.categorie
        entity.description = ligne.description
        entity.unite = ligne.unite
        entity.quantite = ligne.quantite
        entity.cout_unitaire = ligne.cout_unitaire
        entity.cout_total = compute_cout_total(ligne.quantite, ligne.cout_unitaire)
        self._session.flush()
        self._trace.record_event(
            actor_type=ActorType.HUMAIN.value,
            action=ActionAudit.BUDGET_LIGNE_MODIFIEE.value,
            entity_type=_ENTITY,
            entity_id=budget.id,
            before=avant,
            after={
                "ligne_id": str(entity.id),
                "categorie": entity.categorie,
                "quantite": str(entity.quantite),
                "cout_unitaire": str(entity.cout_unitaire),
                "cout_total": str(entity.cout_total),
            },
        )
        return entity

    def supprimer_ligne_budget(self, budget_id: UUID, ligne_id: UUID) -> None:
        """Retire une ligne d'un budget non approuvé (geste tracé)."""
        budget = self._get_modifiable(budget_id)
        entity = self._get_ligne(budget_id, ligne_id)
        self._trace.record_event(
            actor_type=ActorType.HUMAIN.value,
            action=ActionAudit.BUDGET_LIGNE_SUPPRIMEE.value,
            entity_type=_ENTITY,
            entity_id=budget.id,
            before={
                "ligne_id": str(entity.id),
                "categorie": entity.categorie,
                "cout_total": str(entity.cout_total),
            },
        )
        self.lignes.delete(entity)
        self._session.flush()

    # -- cycle de vie -----------------------------------------------------
    def soumettre_budget(self, budget_id: UUID) -> Budget:
        """``brouillon → propose`` : le budget entre dans la file de décision.

        Un budget sans ligne n'est pas soumis : il ne représente aucun chiffrage.
        """
        budget = self._get(budget_id)
        if not self.lignes.list_for_budget(budget.id):
            raise ValidationError(
                "Budget vide : ajouter au moins une ligne avant de le soumettre",
                details={"budget_id": str(budget.id)},
            )
        validate_transition(_ENTITY, budget.statut, StatutBudget.PROPOSE.value)
        budget.statut = StatutBudget.PROPOSE.value
        self._session.flush()
        self._trace.record_event(
            actor_type=ActorType.HUMAIN.value,
            action=ActionAudit.BUDGET_SOUMIS.value,
            entity_type=_ENTITY,
            entity_id=budget.id,
            after={
                "statut": budget.statut,
                "total": str(self.total_budget(budget.id).total),
            },
        )
        return budget

    def approuver_budget(self, budget_id: UUID, decision: Decision) -> Budget:
        """Décision humaine d'approbation (``propose → approuve``)."""
        budget = self._get(budget_id)
        validate_transition(_ENTITY, budget.statut, StatutBudget.APPROUVE.value)
        budget.statut = StatutBudget.APPROUVE.value
        self._trace.record_decision(
            entity_type=_ENTITY,
            entity_id=budget.id,
            proposal_type=TypeProposition.BUDGET.value,
            decision="approuve",
            proposed_by_agent=None,
            decided_by=decision.decided_by,
            reason=decision.reason,
            after={
                "statut": budget.statut,
                "total": str(self.total_budget(budget.id).total),
                "devise": budget.devise,
            },
        )
        return budget

    def nouvelle_version(self, budget_id: UUID) -> Budget:
        """Crée la version suivante d'un budget **approuvé**, lignes recopiées.

        Seule façon de modifier un budget approuvé : l'ancienne version reste
        lisible et les montants validés ne disparaissent jamais.
        """
        source = self._get(budget_id)
        if source.statut != StatutBudget.APPROUVE.value:
            raise ConflictError(
                "Une nouvelle version ne se crée qu'à partir d'un budget "
                "approuvé : modifier le budget en cours tant qu'il est "
                "brouillon ou proposé",
                details={"budget_id": str(budget_id), "statut": source.statut},
            )
        copie = self._copier(
            source,
            offre_id=source.offre_id,
            mission_id=source.mission_id,
            version=source.version + 1,
        )
        self._trace.record_event(
            actor_type=ActorType.HUMAIN.value,
            action=ActionAudit.BUDGET_VERSION.value,
            entity_type=_ENTITY,
            entity_id=copie.id,
            after={
                "version": copie.version,
                "source_id": str(source.id),
                "source_version": source.version,
            },
        )
        return copie

    def reprendre_pour_mission(self, budget_id: UUID, mission_id: UUID) -> Budget:
        """Reprend un budget d'offre **approuvé** sur une mission.

        Le budget de mission démarre en ``brouillon`` : l'exécution ajuste les
        quantités réelles, puis le circuit de validation habituel s'applique.
        """
        source = self._get(budget_id)
        if source.offre_id is None:
            raise ValidationError(
                "Seul un budget d'offre se reprend sur une mission",
                details={"budget_id": str(budget_id)},
            )
        if source.statut != StatutBudget.APPROUVE.value:
            raise ConflictError(
                "Le budget de l'offre doit être approuvé avant d'être repris "
                "sur une mission",
                details={"budget_id": str(budget_id), "statut": source.statut},
            )
        mission = self._mission_lisible(mission_id)
        if self.budgets.list_for_mission(mission.id):
            raise ConflictError(
                "Cette mission a déjà un budget",
                details={"mission_id": str(mission.id)},
            )
        copie = self._copier(source, offre_id=None, mission_id=mission.id, version=1)
        self._trace.record_event(
            actor_type=ActorType.HUMAIN.value,
            action=ActionAudit.BUDGET_REPRIS.value,
            entity_type=_ENTITY,
            entity_id=copie.id,
            after={
                "mission_id": str(mission.id),
                "source_id": str(source.id),
                "source_version": source.version,
                "total": str(self.total_budget(copie.id).total),
            },
        )
        return copie

    # -- export -----------------------------------------------------------
    def exporter(self, budget_id: UUID, format: str = "xlsx") -> tuple[bytes, str, str]:
        """Exporte le budget calculé en XLSX ou DOCX.

        Returns:
            ``(octets, nom_fichier, mime_type)``.

        L'export est une **vue calculée** : il ne crée aucune fiche ``Document``
        (aucune donnée officielle nouvelle) et ne fige donc aucun statut.
        """
        detail = self.lire_budget(budget_id)
        budget = self._get(budget_id)
        if format not in EXPORTS:
            raise ValidationError(
                f"Format d'export inconnu : {format!r}",
                details={"formats": list(EXPORTS)},
            )
        porteur = (
            f"offre {self._reference_offre(budget.offre_id)}"
            if budget.offre_id
            else f"mission {budget.mission_id}"
        )
        titre = f"Budget — {porteur} (v{detail.version})"
        paragraphes = [
            f"Statut : {detail.statut}. Devise : {detail.devise}.",
            f"Total : {detail.total} {detail.devise} ({len(detail.lignes)} ligne(s)).",
        ]
        tableau: list[list[object]] = [
            ["Catégorie", "Description", "Unité", "Quantité", "Coût unitaire", "Coût total"],
        ]
        for ligne in detail.lignes:
            tableau.append(
                [
                    ligne.categorie,
                    ligne.description or "",
                    ligne.unite or "",
                    ligne.quantite,
                    ligne.cout_unitaire,
                    ligne.cout_total,
                ]
            )
        tableau.append(["", "", "", "", "TOTAL", detail.total])

        from app.documents.generation import build_docx, build_xlsx

        if format == "xlsx":
            octets = build_xlsx(titre_feuille="Budget", lignes=tableau)
        else:
            octets = build_docx(
                titre=titre, paragraphes=paragraphes, tableau=tableau
            )
        nom = f"budget_{budget.id}_v{detail.version}.{format}"
        return octets, nom, _MIME[format]

    # -- interne ----------------------------------------------------------
    def _resume(self, budget: Budget) -> BudgetResume:
        total = self.total_budget(budget.id)
        return BudgetResume(
            budget_id=budget.id,
            offre_id=budget.offre_id,
            mission_id=budget.mission_id,
            devise=budget.devise,
            statut=budget.statut,
            version=budget.version,
            total=total.total,
            nb_lignes=total.nb_lignes,
        )

    def _reference_offre(self, offre_id: UUID | None) -> str:
        if offre_id is None:
            return "?"
        offre = self.offres.get(offre_id)
        return offre.reference if offre is not None else "?"

    def _copier(
        self,
        source: Budget,
        *,
        offre_id: UUID | None,
        mission_id: UUID | None,
        version: int,
    ) -> Budget:
        copie = Budget(
            offre_id=offre_id,
            mission_id=mission_id,
            devise=source.devise,
            statut=StatutBudget.BROUILLON.value,
            version=version,
        )
        self.budgets.add(copie)
        self._session.flush()
        for ligne in self.lignes.list_for_budget(source.id):
            self.lignes.add(
                LigneBudget(
                    budget_id=copie.id,
                    categorie=ligne.categorie,
                    description=ligne.description,
                    unite=ligne.unite,
                    quantite=ligne.quantite,
                    cout_unitaire=ligne.cout_unitaire,
                    cout_total=ligne.cout_total,
                    ordre=ligne.ordre,
                )
            )
        self._session.flush()
        return copie

    def _ligne(self, budget_id: UUID, ligne: LigneBudgetInput, *, ordre: int) -> LigneBudget:
        if not ligne.categorie.strip():
            raise ValidationError(
                "La catégorie d'une ligne de budget est obligatoire",
                details={"budget_id": str(budget_id)},
            )
        return LigneBudget(
            budget_id=budget_id,
            categorie=ligne.categorie.strip(),
            description=ligne.description,
            unite=ligne.unite,
            quantite=ligne.quantite,
            cout_unitaire=ligne.cout_unitaire,
            cout_total=compute_cout_total(ligne.quantite, ligne.cout_unitaire),
            ordre=ordre,
        )

    def _prochain_ordre(self, budget_id: UUID) -> int:
        existantes = self.lignes.list_for_budget(budget_id)
        return len(existantes) + 1

    def _trace_ligne(
        self, action: ActionAudit, budget_id: UUID, entity: LigneBudget
    ) -> None:
        self._trace.record_event(
            actor_type=ActorType.HUMAIN.value,
            action=action.value,
            entity_type=_ENTITY,
            entity_id=budget_id,
            after={"ligne_id": str(entity.id), "cout_total": str(entity.cout_total)},
        )

    def _get(self, budget_id: UUID) -> Budget:
        """Budget visible du périmètre (404 hors périmètre, jamais 403)."""
        budget = self.budgets.get(budget_id)
        if budget is None:
            raise NotFoundError(f"Budget {budget_id} introuvable")
        if self._perimetre.est_restreint:
            du_dossier = (
                budget.offre_id is not None
                and budget.offre_id in _offres_du_perimetre(self._session, self._perimetre)
            )
            de_mission = self._perimetre.mission_autorisee(budget.mission_id)
            if not (du_dossier or de_mission):
                raise NotFoundError(f"Budget {budget_id} introuvable")
        return budget

    def _get_modifiable(self, budget_id: UUID) -> Budget:
        budget = self._get(budget_id)
        if budget.statut == StatutBudget.APPROUVE.value:
            raise ConflictError(
                "Impossible de modifier un budget approuvé : créer une "
                "nouvelle version",
                details={"budget_id": str(budget_id)},
            )
        if budget.statut not in _MODIFIABLES:
            raise ConflictError(
                f"Statut budget incompatible: {budget.statut!r}",
                details={"budget_id": str(budget_id)},
            )
        return budget

    def _get_ligne(self, budget_id: UUID, ligne_id: UUID) -> LigneBudget:
        ligne = self.lignes.get(ligne_id)
        if ligne is None or ligne.budget_id != budget_id:
            raise NotFoundError(f"Ligne de budget {ligne_id} introuvable")
        return ligne

    def _offre_lisible(self, offre_id: UUID) -> Offre:
        offre = self.offres.get(offre_id)
        if offre is None:
            raise NotFoundError(f"Offre {offre_id} introuvable")
        if self._perimetre.est_restreint and offre.id not in _offres_du_perimetre(
            self._session, self._perimetre
        ):
            raise NotFoundError(f"Offre {offre_id} introuvable")
        return offre

    def _mission_lisible(self, mission_id: UUID) -> Mission:
        mission = self.missions.get(mission_id)
        if mission is None:
            raise NotFoundError(f"Mission {mission_id} introuvable")
        if not self._perimetre.mission_autorisee(mission.id):
            raise NotFoundError(f"Mission {mission_id} introuvable")
        return mission


def _offres_du_perimetre(session: Session, perimetre: Perimetre) -> frozenset[UUID]:
    """Offres visibles d'un compte restreint (dossier des missions autorisées)."""
    from app.application.dossier import dossier_autorise

    return dossier_autorise(session, perimetre).offres


__all__ = ["EXPORTS", "BudgetService"]
