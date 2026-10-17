"""Service métier—participations (Lot C1, instruction/02 §G).

Session ↔ Beneficiaire, unique par couple (contrainte
``uq_participations_session_beneficiaire``). Fonctions—inscrire, compléter
évaluation/observations, retirer, lister.

**Le pointage n'est plus ici** (règle confirmée CARSO, 24/09)—la présence est
un pointage daté qui vit dans ``PresenceService`` et dans la table
``presences``—une ligne par ``(participation, date)``. Ne jamais réintroduire
une présence sur l'inscription—ce serait une deuxième vérité pour le même fait.

Mutations tracées par AuditEvent (règle 10), sans Approbation. Ni commit ni
rollback (instruction/04 §5).
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from app.application.dto import (
    BeneficiaireInput,
    ParticipationInscriptionInput,
    ParticipationUpdateInput,
)
from app.application.perimetre import Perimetre, perimetre_effectif
from app.application.trace import TraceContext
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.domain.enums import ActionAudit, ActorType, StatutBeneficiaire
from app.domain.execution import Beneficiaire, Participation
from app.infrastructure.repositories import (
    BeneficiaireRepository,
    ParticipationRepository,
    PresenceRepository,
    SessionRepository,
)

_ENTITY = "participation"


class ParticipationService:
    """Use cases : inscrire, modifier, retirer, lister_pour_session."""

    def __init__(
        self,
        session: Session,
        actor_id: str | None = None,
        *,
        perimetre: Perimetre | None = None,
    ) -> None:
        self._session = session
        self._perimetre = perimetre if perimetre is not None else perimetre_effectif()
        self.participations = ParticipationRepository(session)
        # Lu pour compter les pointages au retrait — jamais écrit ici : la règle
        # de pointage appartient à ``PresenceService``.
        self.presences = PresenceRepository(session)
        self.sessions = SessionRepository(session)
        self.beneficiaires = BeneficiaireRepository(session)
        self._trace = TraceContext(session, actor_id=actor_id)

    def inscrire(self, session_id: UUID, entree: ParticipationInscriptionInput) -> Participation:
        """Inscrit un bénéficiaire à une session.

        Raises:
            NotFoundError: session ou bénéficiaire introuvable.
            ConflictError: bénéficiaire déjà inscrit à cette session.
        """
        self._session_requise(session_id)
        if self.beneficiaires.get(entree.beneficiaire_id) is None:
            raise NotFoundError(f"Bénéficiaire {entree.beneficiaire_id} introuvable")
        if self.participations.get_for_couple(session_id, entree.beneficiaire_id) is not None:
            raise ConflictError(
                "Ce bénéficiaire est déjà inscrit à cette session",
                details={
                    "session_id": str(session_id),
                    "beneficiaire_id": str(entree.beneficiaire_id),
                },
            )
        participation = Participation(
            session_id=session_id,
            beneficiaire_id=entree.beneficiaire_id,
        )
        self.participations.add(participation)
        self._session.flush()
        self._trace.record_event(
            actor_type=ActorType.HUMAIN.value,
            action=ActionAudit.PARTICIPATION_INSCRITE.value,
            entity_type=_ENTITY,
            entity_id=participation.id,
            after={
                "session_id": str(session_id),
                "beneficiaire_id": str(entree.beneficiaire_id),
            },
        )
        return participation

    def inscrire_avec_fiche(self, session_id: UUID, fiche: BeneficiaireInput) -> Participation:
        """Crée (ou réutilise) la personne puis l'inscrit — le geste manuel
        de l'import Excel.

        Un formateur ajoute ainsi **sa** gente à **sa** session sans toucher
        au registre : la session est exigée dans son périmètre
        (``_session_requise``), et la création suit la même sémantique que
        l'import — une personne active déjà connue (nom + prénom, insensible
        à la casse) est **réutilisée**, jamais dupliquée ; une personne
        archivée est signalée plutôt que ressuscitée en silence.

        Raises:
            NotFoundError: session hors périmètre (404 côté API).
            ValidationError: nom ou prénom absent, ou personne archivée.
            ConflictError: personne déjà inscrite à cette session.
        """
        self._session_requise(session_id)
        nom = (fiche.nom or "").strip()
        prenom = (fiche.prenom or "").strip()
        if not nom or not prenom:
            raise ValidationError("Le nom et le prénom sont tous deux nécessaires")

        existant = self._trouver_par_identite(nom, prenom)
        if existant is not None and (
            existant.statut or StatutBeneficiaire.ACTIF.value
        ) == StatutBeneficiaire.ARCHIVE.value:
            raise ValidationError(
                f"{prenom} {nom} est archivé(e) dans le registre : "
                "demandez à l'équipe interne de le/la réactiver",
                details={"beneficiaire_id": str(existant.id)},
            )
        if existant is None:
            from app.application.services.beneficiaire_service import BeneficiaireService

            beneficiaire = BeneficiaireService(
                self._session, actor_id=self._trace.actor_id
            ).creer(
                BeneficiaireInput(
                    nom=nom,
                    prenom=prenom,
                    contact=fiche.contact,
                    organisation_origine=fiche.organisation_origine,
                    identifiant_externe=fiche.identifiant_externe,
                )
            )
        else:
            beneficiaire = existant
        return self.inscrire(
            session_id, ParticipationInscriptionInput(beneficiaire_id=beneficiaire.id)
        )

    def _trouver_par_identite(self, nom: str, prenom: str) -> Beneficiaire | None:
        """Personne du registre portant ce nom + prénom (insensible à la casse).

        Archivés inclus : une correspondance archivée doit être **signalée**,
        pas ignorée — sinon une réinscription silencieuse ressusciterait une
        fiche que l'équipe interne a délibérément close.
        """
        for statut in (StatutBeneficiaire.ACTIF.value, StatutBeneficiaire.ARCHIVE.value):
            for candidat in self.beneficiaires.list_by_statut(statut):
                if (
                    (candidat.nom or "").strip().lower() == nom.lower()
                    and (candidat.prenom or "").strip().lower() == prenom.lower()
                ):
                    return candidat
        return None

    def modifier(self, participation_id: UUID, entree: ParticipationUpdateInput) -> Participation:
        """Complète évaluation/observations.

        La présence n'est pas modifiable ici : elle se pointe dans
        ``PresenceService``, pour une date précise.

        Raises:
            ValidationError: aucune modification fournie.
        """
        participation = self._get(participation_id)
        modifie = False
        for nom in ("evaluation", "observations"):
            valeur = getattr(entree, nom)
            if valeur is None or getattr(participation, nom) == valeur:
                continue
            setattr(participation, nom, valeur)
            modifie = True
        if not modifie:
            raise ValidationError(
                "Aucune modification fournie pour la participation",
                details={"participation_id": str(participation_id)},
            )
        self._trace.record_event(
            actor_type=ActorType.HUMAIN.value,
            action=ActionAudit.PARTICIPATION_MODIFIEE.value,
            entity_type=_ENTITY,
            entity_id=participation.id,
        )
        return participation

    def retirer(self, participation_id: UUID) -> None:
        """Retire une personne d'une session (désinscription).

        La participation est un **lien**, pas une donnée métier : la personne et
        la session survivent. Des pointages déjà enregistrés n'empêchent pas le
        retrait, mais ils **partent avec l'inscription** (``ON DELETE CASCADE``)
        : un pointage sans inscription n'a aucun sens. Leur nombre est conservé
        dans la trace, qui porte l'historique.
        """
        participation = self._get(participation_id)
        pointages = len(self.presences.list_for_participation(participation.id))
        self._trace.record_event(
            actor_type=ActorType.HUMAIN.value,
            action=ActionAudit.PARTICIPATION_RETIREE.value,
            entity_type=_ENTITY,
            entity_id=participation.id,
            after={
                "session_id": str(participation.session_id),
                "beneficiaire_id": str(participation.beneficiaire_id),
                "pointages_retires": pointages,
            },
        )
        self.participations.delete(participation)

    def obtenir(self, participation_id: UUID) -> Participation:
        """Consultation (lecture pure)."""
        return self._get(participation_id)

    def lister_pour_session(self, session_id: UUID) -> list[Participation]:
        """Participations d'une session du périmètre (404 sinon)."""
        self._session_requise(session_id)
        return self.participations.list_for_session(session_id)

    def _session_requise(self, session_id: UUID) -> None:
        """Vérifie que la session existe **et** appartient au périmètre."""
        session_formation = self.sessions.get(session_id)
        if session_formation is None or not self._perimetre.mission_autorisee(
            session_formation.mission_id
        ):
            raise NotFoundError(f"Session {session_id} introuvable")

    def _get(self, participation_id: UUID) -> Participation:
        participation = self.participations.get(participation_id)
        if participation is None:
            raise NotFoundError(f"Participation {participation_id} introuvable")
        self._session_requise(participation.session_id)
        return participation


__all__ = ["ParticipationService"]
