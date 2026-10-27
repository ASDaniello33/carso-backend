"""Service métier—appels à proposition (P1—réception → extraction → validation).

Règles appliquées :
- règle 6 [C]—la proposition IA vit dans ``donnees_extraites`` (zone
  *proposal*), jamais écrite dans les tables officielles avant validation ;
- règle 8 [C]—validation = Approbation + AuditEvent dans la même transaction ;
- instruction/02 §B—un collaborateur peut enregistrer un appel reçu.

La matérialisation des lots officiels n'utilise que le schéma
``ExtractionAppel`` déjà produit par l'analyseur (numero + titre
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
from app.application.dto import AppelInput, AppelUpdateInput
from app.application.perimetre import Perimetre, perimetre_effectif
from app.application.trace import Decision, TraceContext
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.domain.enums import (
    ActionAudit,
    ActorType,
    DecisionApprobation,
    StatutAppel,
    TypeAppel,
    TypeProposition,
)
from app.domain.organization import Appel, Lot, Offre
from app.domain.state_machines import validate_transition
from app.infrastructure.repositories import (
    AppelRepository,
    LotRepository,
    OrganisationRepository,
)

_ENTITY = "appel"
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


class AppelService:
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
        self.appels = AppelRepository(session)
        self.lots = LotRepository(session)
        self.organisations = OrganisationRepository(session)
        self._trace = TraceContext(session, actor_id=actor_id)

    def enregistrer(self, entree: AppelInput) -> Appel:
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
        # l'absence retombe sur l'appel (cas historique dominant).
        type_appel = entree.type or TypeAppel.APPEL_A_PROPOSITION.value
        if type_appel not in {t.value for t in TypeAppel}:
            raise ValidationError(
                f"Type d'appel inconnu : {type_appel!r}",
                details={"types_valides": sorted(t.value for t in TypeAppel)},
            )

        appel = Appel(
            organisation_id=entree.organisation_id,
            type=type_appel,
            reference=reference,
            titre=titre,
            description=entree.description,
            # Un appel reçu aujourd'hui : la date de réception est le jour
            # même quand elle n'est pas fournie (usage CARSO, affichage UI).
            date_reception=entree.date_reception or date.today(),
            date_limite=entree.date_limite,
            statut=StatutAppel.RECU.value,
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
        appel_id: UUID,
        donnees_extraites: dict[str, Any],
        *,
        proposed_by_agent: str,
    ) -> Appel:
        """Mémorise la proposition d'extraction d'un agent (zone *proposal*)
        et la SOUMET à décision humaine : l'appel traverse ``en_analyse`` puis
        arrive en ``propose`` (depuis ``recu`` ou ``corrige`` — boucle de
        re-analyse après correction).

        Ne touche à aucune donnée officielle.
        """
        appel = self._get(appel_id)
        # Phase 2 §3 : l'enregistrement de l'extraction EST la soumission de la
        # proposition → l'appel traverse en_analyse puis arrive en 'propose'.
        # Il ne reste plus qu'au décideur humain à valider ou refuser.
        validate_transition(_ENTITY, appel.statut, StatutAppel.EN_ANALYSE.value)
        appel.statut = StatutAppel.EN_ANALYSE
        validate_transition(_ENTITY, appel.statut, StatutAppel.PROPOSE.value)
        appel.statut = StatutAppel.PROPOSE

        appel.donnees_extraites = {
            **donnees_extraites,
            _META_KEY: {"proposed_by_agent": proposed_by_agent},
        }

        self._trace.record_event(
            actor_type=ActorType.AGENT.value,
            action=ActionAudit.EXTRACTION_PROPOSEE.value,
            entity_type=_ENTITY,
            entity_id=appel.id,
            after={"statut": StatutAppel.PROPOSE.value},
            event_metadata={"proposed_by_agent": proposed_by_agent},
        )
        return appel

    def approuver_extraction(
        self, appel_id: UUID, decision: Decision
    ) -> Appel:
        """Décision humaine favorable : la proposition devient la donnée de
        référence de l'appel (zone officielle), statut ``valide``.
        Approbation + AuditEvent dans la même transaction.

        Un appel dont la proposition ne contient **aucun lot exploitable**
        reçoit, à sa validation, **un lot unique** numéro ``1`` portant le
        titre de l'appel (règle confirmée avec CARSO, 26/10) : les offres y
        sont rattachées, aucune n'est orpheline.

        Raises:
            ConflictError: proposition absente.
            ValidationError: proposition illisible (``lots`` n'est pas une liste).
        """
        appel = self._get_validable(appel_id)
        validate_transition(_ENTITY, appel.statut, StatutAppel.VALIDE.value)
        # Tout est calculé et vérifié **avant** la moindre écriture : un refus
        # laisse l'appel exactement dans son état d'origine.
        champs_lots = self._champs_lots_a_materialiser(appel)
        repli = not champs_lots and not self.lots.list_for_appel(appel.id)

        appel.statut = StatutAppel.VALIDE
        lots_crees = self._materialiser_lots_officiels(appel, champs_lots)
        if repli:
            lots_crees = self._materialiser_lot_de_repli(appel)
        self._trace.record_decision(
            entity_type=_ENTITY,
            entity_id=appel.id,
            proposal_type=TypeProposition.EXTRACTION_APPEL_A_PROPOSITION.value,
            decision=DecisionApprobation.APPROUVE.value,
            proposed_by_agent=self._proposed_by_agent(appel),
            decided_by=decision.decided_by,
            reason=decision.reason,
            after={
                "statut": StatutAppel.VALIDE.value,
                "lots_materialises": lots_crees,
                "lot_de_repli": repli,
            },
        )
        return appel

    def refuser_extraction(
        self, appel_id: UUID, decision: Decision
    ) -> Appel:
        """Décision humaine défavorable sur la proposition : l'appel passe à
        ``corrige`` (reprise humaine / re-analyse). La proposition reste en
        zone *proposal* pour audit — rien n'est effacé."""
        appel = self._get_validable(appel_id)
        validate_transition(_ENTITY, appel.statut, StatutAppel.CORRIGE.value)

        appel.statut = StatutAppel.CORRIGE
        self._trace.record_decision(
            entity_type=_ENTITY,
            entity_id=appel.id,
            proposal_type=TypeProposition.EXTRACTION_APPEL_A_PROPOSITION.value,
            decision=DecisionApprobation.REJETE.value,
            proposed_by_agent=self._proposed_by_agent(appel),
            decided_by=decision.decided_by,
            reason=decision.reason,
            after={"statut": StatutAppel.CORRIGE.value},
        )
        return appel

    def enregistrer_proposition_corrigee(
        self,
        appel_id: UUID,
        donnees_extraites: dict[str, Any],
        *,
        proposed_by_agent: str,
    ) -> Appel:
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
        appel = self._get(appel_id)
        dependances = self._dependances_officielles(appel.id)
        if dependances["nb_offres"]:
            raise ConflictError(
                "Cet appel a déjà des offres ou des missions : sa proposition "
                "n'est plus modifiable.",
                details={"appel_id": str(appel.id), **dependances},
            )

        # Revenir en proposition : la transition part du statut courant
        # (``valide`` ou ``corrige``) et n'est donc possible que telle quelle.
        validate_transition(_ENTITY, appel.statut, StatutAppel.PROPOSE.value)
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
        # Une proposition sans lot est **légitime** depuis le 26/10 : la
        # validation crée alors le lot unique « 1 » au titre de l'appel. Seule
        # une proposition *illisible* (``lots`` n'est pas une liste) est
        # refusée — c'est déjà le cas dans ``_champs_lots_a_materialiser``.

        appel.statut = StatutAppel.PROPOSE
        self._trace.record_event(
            actor_type=ActorType.AGENT.value,
            action=ActionAudit.EXTRACTION_PROPOSEE.value,
            entity_type=_ENTITY,
            entity_id=appel.id,
            after={
                "statut": StatutAppel.PROPOSE.value,
                "nb_lots": len(champs_lots),
            },
            event_metadata={"proposed_by_agent": proposed_by_agent},
        )
        return appel

    def _dependances_officielles(self, appel_id: UUID) -> dict[str, Any]:
        """Offres déjà nées de cet appel — le seul lien officiel direct.

        Une mission ne porte pas d'ancre d'appel : elle référence des offres
        (règle 5), donc aucune mission ne peut exister pour cet appel sans une
        offre. Compter les offres suffit à savoir si la proposition a produit
        du concret. Lecture seule.
        """
        offres = self._session.scalar(
            select(func.count())
            .select_from(Offre)
            .where(Offre.appel_id == appel_id)
        )
        return {"nb_offres": int(offres or 0)}

    def rejeter_appel(self, appel_id: UUID, decision: Decision) -> Appel:
        """Rejet de l'appel lui-même (pas de la proposition) — statut ``rejete``."""
        appel = self._get(appel_id)
        validate_transition(_ENTITY, appel.statut, StatutAppel.REJETE.value)

        appel.statut = StatutAppel.REJETE
        self._trace.record_decision(
            entity_type=_ENTITY,
            entity_id=appel.id,
            proposal_type=TypeProposition.EXTRACTION_APPEL_A_PROPOSITION.value,
            decision=DecisionApprobation.REJETE.value,
            proposed_by_agent=self._proposed_by_agent(appel),
            decided_by=decision.decided_by,
            reason=decision.reason,
            after={"statut": StatutAppel.REJETE.value},
        )
        return appel

    # --- lecture (pour la couche API) ---------------------------------------

    def obtenir(self, appel_id: UUID) -> Appel:
        """Charge un appel (lecture pure, sans mutation).

        Sous périmètre restreint, l'appel se lit à travers une mission affectée
        (le dossier de la mission) : un appel hors dossier reste introuvable.
        """
        if self._perimetre.est_restreint and not dossier_autorise(
            self._session, self._perimetre
        ).contient_appel(appel_id):
            raise NotFoundError(f"Appel {appel_id} introuvable")
        return self._get(appel_id)

    def lister(self) -> list[Appel]:
        """Tous les appels (référentiel interne, volume limité)."""
        return self.appels.list_all()

    # --- internes ---------------------------------------------------------

    def _get(self, appel_id: UUID) -> Appel:
        appel = self.appels.get(appel_id)
        if appel is None:
            raise NotFoundError(f"Appel {appel_id} introuvable")
        return appel

    def _get_validable(self, appel_id: UUID) -> Appel:
        appel = self._get(appel_id)
        if appel.donnees_extraites is None:
            msg = "Aucune proposition d'extraction à valider"
            raise ConflictError(msg)
        return appel

    def _proposed_by_agent(self, appel: Appel) -> str | None:
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
        self, appel_id: UUID, entree: AppelUpdateInput
    ) -> Appel:
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
        appel = self._get(appel_id)
        valide = appel.statut == StatutAppel.VALIDE.value
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
                    details={"appel_id": str(appel.id)},
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
                    details={"appel_id": str(appel.id)},
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
                "Aucune modification fournie pour l'appel",
                details={"appel_id": str(appel.id)},
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
        self, appel_id: UUID, *,
        titre: str | None,
    ) -> Appel:
        """Modification partielle du titre (HITL agent). Aucun autre champ."""
        appel = self._get(appel_id)
        if titre is None or titre.strip() == "" or titre == appel.titre:
            raise ValidationError(
                "Aucune modification fournie pour l'appel",
                details={"appel_id": str(appel_id)},
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
        self, appel: Appel, champs_lots: list[dict[str, Any]]
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
            self.lots.add(Lot(appel_id=appel.id, **champs))
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

    def _materialiser_lot_de_repli(self, appel: Appel) -> list[str]:
        """Crée le **lot unique** d'un appel sans lot (règle CARSO, 26/10).

        Numéro ``1``, titre de l'appel : les offres de cet appel y sont
        rattachées — aucune n'est orpheline. Le lot n'est créé que s'il
        n'existe pas déjà (un lot « 1 » saisi à la main survit), et la trace
        d'audit porte ``repli: true`` pour distinguer ce chemin d'une
        extraction qui aurait produit un lot.
        """
        if self.lots.get_by_numero(appel.id, "1") is not None:
            return []
        self.lots.add(
            Lot(appel_id=appel.id, numero="1", titre=appel.titre)
        )
        self._session.flush()
        self._trace.record_event(
            actor_type=ActorType.SYSTEME.value,
            action=ActionAudit.LOTS_MATERIALISES.value,
            entity_type=_ENTITY,
            entity_id=appel.id,
            after={"numeros": ["1"], "repli": True, "titre": appel.titre},
        )
        return ["1"]

    def _champs_lots_a_materialiser(self, appel: Appel) -> list[dict[str, Any]]:
        """Champs officiels des lots proposés, ou liste vide s'il n'y en a aucun.

        Refuse une proposition illisible plutôt que d'inventer un lot. Les lots
        sans ``numero`` ou ``titre`` sont ignorés : c'est la lecture commune de
        la validation et de l'enregistrement d'une proposition corrigée. Une
        proposition **sans aucun lot** est légitime : la validation crée alors
        le lot unique « 1 » au titre de l'appel (voir
        ``_materialiser_lot_de_repli``).
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
