"""Service métier—appels à proposition (P1—réception → extraction → validation).

Règles appliquées :
- règle 6 [C]—la proposition IA vit dans ``donnees_extraites`` (zone
  *proposal*), jamais écrite dans les tables officielles avant validation ;
- règle 8 [C]—validation = Approbation + AuditEvent dans la même transaction ;
- instruction/02 §B—un collaborateur peut enregistrer un appel reçu.

La matérialisation des lots officiels n'utilise que le schéma
``ExtractionAppelAProposition`` déjà produit par l'analyseur (numero + titre
obligatoires). Les lots déjà saisis manuellement (même ``numero``) ne sont
jamais écrasés. Les lots mal formés sont refusés—la validation n'invente
aucun lot.
"""

from datetime import date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.application.dossier import dossier_autorise
from app.application.dto import AppelAPropositionInput, AppelUpdateInput
from app.application.perimetre import Perimetre, perimetre_effectif
from app.application.trace import Decision, TraceContext
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.domain.enums import (
    ActionAudit,
    ActorType,
    DecisionApprobation,
    StatutAppelAProposition,
    TypeAppel,
    TypeProposition,
)
from app.domain.organization import AppelAProposition, Lot, Offre
from app.domain.state_machines import validate_transition
from app.infrastructure.repositories import (
    AppelAPropositionRepository,
    LotRepository,
    OrganisationRepository,
)

_ENTITY = "appel_a_proposition"
_META_KEY = "_meta"


def _date_ou_rien(valeur: Any) -> date | None:
    """Accepte une ``date`` ou une chaîne ISO ; ignore le reste (pas d'invention)."""
    if isinstance(valeur, date) and not isinstance(valeur, datetime):
        return valeur
    if isinstance(valeur, datetime):
        return valeur.date()
    if isinstance(valeur, str) and valeur.strip():
        try:
            return date.fromisoformat(valeur.strip()[:10])
        except ValueError:
            return None
    return None


class AppelAPropositionService:
    """Use cases : enregistrer, enregistrer_extraction, approuver_extraction,
    refuser_extraction, rejeter_appel. Aucun commit : le commit appartient à
    l'appelant (route FastAPI via get_session, ou script)."""

    def __init__(
        self,
        session: Session,
        actor_id: str | None = None,
        *,
        perimetre: Perimetre | None = None,
    ) -> None:
        self._session = session
        self._perimetre = perimetre if perimetre is not None else perimetre_effectif()
        self.appels = AppelAPropositionRepository(session)
        self.lots = LotRepository(session)
        self.organisations = OrganisationRepository(session)
        self._trace = TraceContext(session, actor_id=actor_id)

    def enregistrer(self, entree: AppelAPropositionInput) -> AppelAProposition:
        """Enregistre un appel reçu (statut ``recu``). Aucune extraction.

        Raises:
            NotFoundError: organisation cliente introuvable.
            ConflictError: ``reference`` déjà utilisée.
        """
        if self.organisations.get(entree.organisation_id) is None:
            raise NotFoundError(
                f"Organisation {entree.organisation_id} introuvable"
            )
        reference = entree.reference.strip()
        titre = entree.titre.strip()
        if not reference or not titre:
            raise ValidationError("reference et titre sont obligatoires")
        if self.appels.get_by_reference(reference) is not None:
            raise ConflictError(
                f"La référence {reference!r} existe déjà",
                details={"reference": reference},
            )

        # Nature de l'appel : jamais devinée — un type inconnu est refusé, et
        # l'absence retombe sur l'appel à proposition (cas historique dominant).
        type_appel = entree.type or TypeAppel.APPEL_A_PROPOSITION.value
        if type_appel not in {t.value for t in TypeAppel}:
            raise ValidationError(
                f"Type d'appel inconnu : {type_appel!r}",
                details={"types_valides": sorted(t.value for t in TypeAppel)},
            )

        appel = AppelAProposition(
            organisation_id=entree.organisation_id,
            type=type_appel,
            reference=reference,
            titre=titre,
            description=entree.description,
            # Un appel reçu aujourd'hui : la date de réception est le jour
            # même quand elle n'est pas fournie (usage CARSO, affichage UI).
            date_reception=entree.date_reception or date.today(),
            date_limite=entree.date_limite,
            statut=StatutAppelAProposition.RECU.value,
        )
        self.appels.add(appel)
        self._session.flush()
        self._trace.record_event(
            actor_type=ActorType.HUMAIN.value,
            action=ActionAudit.APPEL_A_PROPOSITION_ENREGISTRE.value,
            entity_type=_ENTITY,
            entity_id=appel.id,
            after={
                "reference": appel.reference,
                "titre": appel.titre,
                "type": appel.type,
            },
        )
        return appel

    def enregistrer_extraction(
        self,
        appel_a_proposition_id: UUID,
        donnees_extraites: dict[str, Any],
        *,
        proposed_by_agent: str,
    ) -> AppelAProposition:
        """Mémorise la proposition d'extraction d'un agent (zone *proposal*)
        et la SOUMET à décision humaine : l'appel traverse ``en_analyse`` puis
        arrive en ``propose`` (depuis ``recu`` ou ``corrige`` — boucle de
        re-analyse après correction).

        Ne touche à aucune donnée officielle.
        """
        appel = self._get(appel_a_proposition_id)
        # Phase 2 §3 : l'enregistrement de l'extraction EST la soumission de la
        # proposition → l'appel traverse en_analyse puis arrive en 'propose'.
        # Il ne reste plus qu'au décideur humain à valider ou refuser.
        validate_transition(_ENTITY, appel.statut, StatutAppelAProposition.EN_ANALYSE.value)
        appel.statut = StatutAppelAProposition.EN_ANALYSE
        validate_transition(_ENTITY, appel.statut, StatutAppelAProposition.PROPOSE.value)
        appel.statut = StatutAppelAProposition.PROPOSE

        appel.donnees_extraites = {
            **donnees_extraites,
            _META_KEY: {"proposed_by_agent": proposed_by_agent},
        }

        self._trace.record_event(
            actor_type=ActorType.AGENT.value,
            action=ActionAudit.EXTRACTION_PROPOSEE.value,
            entity_type=_ENTITY,
            entity_id=appel.id,
            after={"statut": StatutAppelAProposition.PROPOSE.value},
            event_metadata={"proposed_by_agent": proposed_by_agent},
        )
        return appel

    def approuver_extraction(
        self, appel_a_proposition_id: UUID, decision: Decision
    ) -> AppelAProposition:
        """Décision humaine favorable : la proposition devient la donnée de
        référence de l'appel (zone officielle), statut ``valide``.
        Approbation + AuditEvent dans la même transaction.

        La validation n'est acceptée que si elle officialise quelque chose :
        une proposition **sans aucun lot exploitable** est refusée. Sans cette
        garde, approuver une proposition vide passait l'appel en ``valide`` —
        un état qui ne mène qu'à ``archive`` — sans créer le moindre lot
        officiel : le dossier était figé, et l'utilisateur ne pouvait plus ni
        refaire la proposition ni créer de lot.

        Raises:
            ConflictError: proposition absente, ou rien à officialiser.
            ValidationError: proposition illisible (``lots`` n'est pas une liste).
        """
        appel = self._get_validable(appel_a_proposition_id)
        validate_transition(_ENTITY, appel.statut, StatutAppelAProposition.VALIDE.value)
        # Tout est calculé et vérifié **avant** la moindre écriture : un refus
        # laisse l'appel exactement dans son état d'origine.
        champs_lots = self._champs_lots_a_materialiser(appel)
        if not champs_lots and not self.lots.list_for_appel_a_proposition(appel.id):
            raise ConflictError(
                "Aucun lot à officialiser : la proposition ne contient aucun lot "
                "exploitable (numéro et titre requis). Complétez la proposition "
                "avant de la valider.",
                details={"appel_a_proposition_id": str(appel.id), "nb_lots": 0},
            )

        appel.statut = StatutAppelAProposition.VALIDE
        lots_crees = self._materialiser_lots_officiels(appel, champs_lots)
        self._trace.record_decision(
            entity_type=_ENTITY,
            entity_id=appel.id,
            proposal_type=TypeProposition.EXTRACTION_APPEL_A_PROPOSITION.value,
            decision=DecisionApprobation.APPROUVE.value,
            proposed_by_agent=self._proposed_by_agent(appel),
            decided_by=decision.decided_by,
            reason=decision.reason,
            after={
                "statut": StatutAppelAProposition.VALIDE.value,
                "lots_materialises": lots_crees,
            },
        )
        return appel

    def refuser_extraction(
        self, appel_a_proposition_id: UUID, decision: Decision
    ) -> AppelAProposition:
        """Décision humaine défavorable sur la proposition : l'appel passe à
        ``corrige`` (reprise humaine / re-analyse). La proposition reste en
        zone *proposal* pour audit — rien n'est effacé."""
        appel = self._get_validable(appel_a_proposition_id)
        validate_transition(_ENTITY, appel.statut, StatutAppelAProposition.CORRIGE.value)

        appel.statut = StatutAppelAProposition.CORRIGE
        self._trace.record_decision(
            entity_type=_ENTITY,
            entity_id=appel.id,
            proposal_type=TypeProposition.EXTRACTION_APPEL_A_PROPOSITION.value,
            decision=DecisionApprobation.REJETE.value,
            proposed_by_agent=self._proposed_by_agent(appel),
            decided_by=decision.decided_by,
            reason=decision.reason,
            after={"statut": StatutAppelAProposition.CORRIGE.value},
        )
        return appel

    def enregistrer_proposition_corrigee(
        self,
        appel_a_proposition_id: UUID,
        donnees_extraites: dict[str, Any],
        *,
        proposed_by_agent: str,
    ) -> AppelAProposition:
        """Enregistre une proposition corrigée sur un appel **déjà validé**.

        Une validation a beau avoir été prononcée, l'appel n'est réparable que
        si son contenu n'a pas encore produit d'objet officiel : dès qu'un lot
        porte des offres ou des missions, réécrire la proposition ferait mentir
        le dossier. Dans ce cas, l'appel reste ``valide`` et une nouvelle
        proposition ne peut naître que d'un nouvel appel.

        Enregistrer n'officialise rien : l'appel revient en ``propose``, avec
        sa nouvelle proposition, et attend une décision humaine.

        Raises:
            ConflictError: l'appel a déjà des offres ou des missions.
            ValidationError: proposition illisible.
        """
        appel = self._get(appel_a_proposition_id)
        dependances = self._dependances_officielles(appel.id)
        if dependances["nb_offres"]:
            raise ConflictError(
                "Cet appel a déjà des offres ou des missions : sa proposition "
                "n'est plus modifiable.",
                details={"appel_a_proposition_id": str(appel.id), **dependances},
            )

        # Revenir en proposition : la transition part du statut courant
        # (``valide`` ou ``corrige``) et n'est donc possible que telle quelle.
        validate_transition(_ENTITY, appel.statut, StatutAppelAProposition.PROPOSE.value)
        # Contrôle de forme AVANT écriture : une proposition illisible est
        # refusée sans laisser l'appel dans un statut intermédiaire.
        precedent = appel.donnees_extraites
        appel.donnees_extraites = {
            **donnees_extraites,
            _META_KEY: {"proposed_by_agent": proposed_by_agent},
        }
        try:
            champs_lots = self._champs_lots_a_materialiser(appel)
        except (ValidationError, ConflictError):
            appel.donnees_extraites = precedent
            raise
        if not champs_lots:
            appel.donnees_extraites = precedent
            raise ValidationError(
                "Proposition inexploitable : aucun lot avec numéro et titre. "
                "Les lots officiels naissent de ces deux champs.",
                details={"appel_a_proposition_id": str(appel.id), "nb_lots": 0},
            )

        appel.statut = StatutAppelAProposition.PROPOSE
        self._trace.record_event(
            actor_type=ActorType.AGENT.value,
            action=ActionAudit.EXTRACTION_PROPOSEE.value,
            entity_type=_ENTITY,
            entity_id=appel.id,
            after={
                "statut": StatutAppelAProposition.PROPOSE.value,
                "nb_lots": len(champs_lots),
            },
            event_metadata={"proposed_by_agent": proposed_by_agent},
        )
        return appel

    def _dependances_officielles(self, appel_a_proposition_id: UUID) -> dict[str, Any]:
        """Offres déjà nées de cet appel — le seul lien officiel direct.

        Une mission ne porte pas d'ancre d'appel : elle référence des offres
        (règle 5), donc aucune mission ne peut exister pour cet appel sans une
        offre. Compter les offres suffit à savoir si la proposition a produit
        du concret. Lecture seule.
        """
        offres = self._session.scalar(
            select(func.count())
            .select_from(Offre)
            .where(Offre.appel_a_proposition_id == appel_a_proposition_id)
        )
        return {"nb_offres": int(offres or 0)}

    def rejeter_appel(self, appel_a_proposition_id: UUID, decision: Decision) -> AppelAProposition:
        """Rejet de l'appel lui-même (pas de la proposition) — statut ``rejete``."""
        appel = self._get(appel_a_proposition_id)
        validate_transition(_ENTITY, appel.statut, StatutAppelAProposition.REJETE.value)

        appel.statut = StatutAppelAProposition.REJETE
        self._trace.record_decision(
            entity_type=_ENTITY,
            entity_id=appel.id,
            proposal_type=TypeProposition.EXTRACTION_APPEL_A_PROPOSITION.value,
            decision=DecisionApprobation.REJETE.value,
            proposed_by_agent=self._proposed_by_agent(appel),
            decided_by=decision.decided_by,
            reason=decision.reason,
            after={"statut": StatutAppelAProposition.REJETE.value},
        )
        return appel

    # --- lecture (pour la couche API) ---------------------------------------

    def obtenir(self, appel_a_proposition_id: UUID) -> AppelAProposition:
        """Charge un appel à proposition (lecture pure, sans mutation).

        Sous périmètre restreint, l'appel se lit à travers une mission affectée
        (le dossier de la mission) : un appel hors dossier reste introuvable.
        """
        if self._perimetre.est_restreint and not dossier_autorise(
            self._session, self._perimetre
        ).contient_appel(appel_a_proposition_id):
            raise NotFoundError(f"Appel à proposition {appel_a_proposition_id} introuvable")
        return self._get(appel_a_proposition_id)

    def lister(self) -> list[AppelAProposition]:
        """Tous les appels (référentiel interne, volume limité)."""
        return self.appels.list_all()

    # --- internes ---------------------------------------------------------

    def _get(self, appel_a_proposition_id: UUID) -> AppelAProposition:
        appel = self.appels.get(appel_a_proposition_id)
        if appel is None:
            raise NotFoundError(f"Appel à proposition {appel_a_proposition_id} introuvable")
        return appel

    def _get_validable(self, appel_a_proposition_id: UUID) -> AppelAProposition:
        appel = self._get(appel_a_proposition_id)
        if appel.donnees_extraites is None:
            msg = "Aucune proposition d'extraction à valider"
            raise ConflictError(msg)
        return appel

    def _proposed_by_agent(self, appel: AppelAProposition) -> str | None:
        """Nom de l'agent proposeur, mémorisé dans la zone ``_meta`` de la
        proposition (convention interne, documentée)."""
        if not appel.donnees_extraites:
            return None
        meta = appel.donnees_extraites.get(_META_KEY)
        if isinstance(meta, dict):
            agent = meta.get("proposed_by_agent")
            return str(agent) if agent else None
        return None

    def modifier(
        self, appel_a_proposition_id: UUID, entree: AppelUpdateInput
    ) -> AppelAProposition:
        """Modification d'un appel reçu par un humain (formulaire d'interface).

        Toujours modifiables : titre, description, nature, dates.
        Modifiables **tant que l'appel n'est pas validé** : référence et
        organisation émettrice — les lots officiels en héritent, les changer
        après validation réécrirait l'histoire du dossier.

        Raises:
            ConflictError: référence déjà utilisée, ou identité figée par la validation.
            NotFoundError: appel ou organisation introuvable.
            ValidationError: type inconnu, titre/référence vide, aucune modification.
        """
        appel = self._get(appel_a_proposition_id)
        valide = appel.statut == StatutAppelAProposition.VALIDE.value
        avant = {
            "reference": appel.reference,
            "titre": appel.titre,
            "type": appel.type,
        }
        modifie = False

        if entree.reference is not None and entree.reference.strip() != appel.reference:
            reference = entree.reference.strip()
            if not reference:
                raise ValidationError("La référence d'un appel ne peut pas être vide")
            if valide:
                raise ConflictError(
                    "La référence d'un appel validé n'est plus modifiable "
                    "(les lots officiels en dépendent)",
                    details={"appel_a_proposition_id": str(appel.id)},
                )
            existant = self.appels.get_by_reference(reference)
            if existant is not None and existant.id != appel.id:
                raise ConflictError(
                    f"La référence {reference!r} existe déjà",
                    details={"reference": reference},
                )
            appel.reference = reference
            modifie = True

        if entree.organisation_id is not None and entree.organisation_id != appel.organisation_id:
            if self.organisations.get(entree.organisation_id) is None:
                raise NotFoundError(
                    f"Organisation {entree.organisation_id} introuvable"
                )
            if valide:
                raise ConflictError(
                    "L'organisation émettrice d'un appel validé n'est plus modifiable",
                    details={"appel_a_proposition_id": str(appel.id)},
                )
            appel.organisation_id = entree.organisation_id
            modifie = True

        if entree.titre is not None and entree.titre.strip() != appel.titre:
            titre = entree.titre.strip()
            if not titre:
                raise ValidationError("Le titre d'un appel ne peut pas être vide")
            appel.titre = titre
            modifie = True

        if entree.type is not None and entree.type != appel.type:
            if entree.type not in {t.value for t in TypeAppel}:
                raise ValidationError(
                    f"Type d'appel inconnu : {entree.type!r}",
                    details={"types_valides": sorted(t.value for t in TypeAppel)},
                )
            appel.type = entree.type
            modifie = True

        for nom in ("description", "date_reception", "date_limite"):
            valeur = getattr(entree, nom)
            if valeur is not None and getattr(appel, nom) != valeur:
                setattr(appel, nom, valeur)
                modifie = True

        if not modifie:
            raise ValidationError(
                "Aucune modification fournie pour l'appel à proposition",
                details={"appel_a_proposition_id": str(appel.id)},
            )

        self._trace.record_event(
            actor_type=ActorType.HUMAIN.value,
            action=ActionAudit.APPEL_A_PROPOSITION_MODIFIE.value,
            entity_type=_ENTITY,
            entity_id=appel.id,
            after={
                "reference": appel.reference,
                "titre": appel.titre,
                "type": appel.type,
                "date_limite": (
                    appel.date_limite.isoformat() if appel.date_limite else None
                ),
            },
            event_metadata={"avant": avant},
        )
        return appel

    def modifier_titre(
        self, appel_a_proposition_id: UUID, *,
        titre: str | None,
    ) -> AppelAProposition:
        """Modification partielle du titre (HITL agent). Aucun autre champ."""
        appel = self._get(appel_a_proposition_id)
        if titre is None or titre.strip() == "" or titre == appel.titre:
            raise ValidationError(
                "Aucune modification fournie pour l'appel à proposition",
                details={"appel_a_proposition_id": str(appel_a_proposition_id)},
            )
        appel.titre = titre.strip()
        self._trace.record_event(
            actor_type=ActorType.AGENT.value,
            action=ActionAudit.APPEL_A_PROPOSITION_MODIFIE.value,
            entity_type=_ENTITY,
            entity_id=appel.id,
            after={"titre": appel.titre},
        )
        return appel

    def _materialiser_lots_officiels(
        self, appel: AppelAProposition, champs_lots: list[dict[str, Any]]
    ) -> list[str]:
        """Crée les lots officiels à partir de la proposition validée.

        N'écrit que les lots dont ``numero`` n'existe pas encore pour cet
        appel : les lots déjà saisis à la main (même ``numero``) survivent à
        l'approbation et ne sont comptés que s'ils sont réellement nouveaux.
        """
        crees: list[str] = []
        for champs in champs_lots:
            if self.lots.get_by_numero(appel.id, champs["numero"]) is not None:
                continue
            self.lots.add(Lot(appel_a_proposition_id=appel.id, **champs))
            crees.append(champs["numero"])
        if crees:
            self._session.flush()
            self._trace.record_event(
                actor_type=ActorType.SYSTEME.value,
                action=ActionAudit.LOTS_MATERIALISES.value,
                entity_type=_ENTITY,
                entity_id=appel.id,
                after={"numeros": crees},
            )
        return crees

    def _champs_lots_a_materialiser(self, appel: AppelAProposition) -> list[dict[str, Any]]:
        """Champs officiels des lots proposés, ou liste vide s'il n'y en a aucun.

        Refuse une proposition illisible plutôt que d'inventer un lot. Les lots
        sans ``numero`` ou ``titre`` sont ignorés : c'est la lecture commune de
        la validation et de l'enregistrement d'une proposition corrigée, si
        bien qu'un appel ne peut pas devenir « validé » sans les mêmes lots que
        ceux qu'un humain a vus.
        """
        proposition = self._proposition_sans_meta(appel.donnees_extraites)
        lots_bruts = proposition.get("lots")
        if lots_bruts is None:
            return []
        if not isinstance(lots_bruts, list):
            raise ValidationError(
                "Proposition d'extraction illisible : 'lots' doit être une liste",
                details={"type": type(lots_bruts).__name__},
            )
        champs: list[dict[str, Any]] = []
        for index, brut in enumerate(lots_bruts):
            champs_lot = self._champs_lot_officiels(brut, index)
            if champs_lot is not None:
                champs.append(champs_lot)
        return champs

    @staticmethod
    def _proposition_sans_meta(donnees: dict[str, Any] | None) -> dict[str, Any]:
        if not donnees:
            return {}
        return {cle: valeur for cle, valeur in donnees.items() if cle != _META_KEY}

    @staticmethod
    def _champs_lot_officiels(brut: Any, index: int) -> dict[str, Any] | None:
        """Extrait les champs officiels d'un lot proposé.

        Un lot sans ``numero`` ou ``titre`` est ignoré — on n'invente rien.
        Un élément qui n'est pas un objet est une proposition illisible.
        """
        if not isinstance(brut, dict):
            raise ValidationError(
                f"Lot extrait #{index} illisible (objet attendu)",
                details={"index": index},
            )
        numero = brut.get("numero")
        titre = brut.get("titre")
        if not isinstance(numero, str) or not numero.strip():
            return None
        if not isinstance(titre, str) or not titre.strip():
            return None
        return {
            "numero": numero.strip(),
            "titre": titre.strip(),
            "zone": brut.get("zone") if isinstance(brut.get("zone"), str) else None,
            "objectifs": brut.get("objectifs")
            if isinstance(brut.get("objectifs"), str)
            else None,
            "resultats_attendus": brut.get("resultats_attendus")
            if isinstance(brut.get("resultats_attendus"), str)
            else None,
            "mission_description": brut.get("mission_description")
            if isinstance(brut.get("mission_description"), str)
            else None,
            "partenariat": brut.get("partenariat")
            if isinstance(brut.get("partenariat"), str)
            else None,
            "date_fin": _date_ou_rien(brut.get("date_fin")),
        }
