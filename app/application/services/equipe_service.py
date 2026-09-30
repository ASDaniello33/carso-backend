"""Service métier — personnes du vivier (Lot C1, instruction/02 §D).

Fonctions : créer/consulter/modifier, historique des affectations via
``AffectationService.lister_pour_mission`` côté mission. Le CV est un
Document rattaché via ``DocumentService`` (ancre ``equipe_id``), jamais une
colonne ici (instruction/04 §equipes). Compétences/expériences/disponibilité :
non saisi tant que le modèle n'est pas confirmé [P Q6].

Mutations tracées par AuditEvent (règle 10), sans Approbation (pas une
proposition d'agent). Ni commit ni rollback (instruction/04 §5).
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.application.dto import EquipeInput, EquipeUpdateInput
from app.application.trace import TraceContext
from app.core.errors import NotFoundError, ValidationError
from app.domain.enums import (
    ActionAudit,
    ActorType,
    RoleUtilisateur,
    StatutEquipe,
    StatutUtilisateur,
)
from app.domain.execution import Equipe
from app.domain.identity import Utilisateur
from app.infrastructure.repositories import EquipeRepository

_ENTITY = "equipe"

_CHAMPS = ("nom", "prenom", "email", "telephone", "profil", "statut", "role_compte")


class EquipeService:
    """Use cases : creer, modifier, obtenir, lister."""

    def __init__(self, session: Session, actor_id: str | None = None) -> None:
        self._session = session
        self.equipes = EquipeRepository(session)
        self._trace = TraceContext(session, actor_id=actor_id)

    def creer(self, entree: EquipeInput) -> Equipe:
        """Enregistre une personne du vivier (active par défaut).

        ``StatutEquipe`` porte le vocabulaire : sans statut fourni, la personne
        naît ``actif`` — une personne créée ne peut pas être invisible des
        listes actives par surprise.
        """
        equipe = Equipe(
            nom=entree.nom,
            prenom=entree.prenom,
            email=entree.email,
            telephone=entree.telephone,
            profil=entree.profil,
            statut=entree.statut or StatutEquipe.ACTIF.value,
        )
        self.equipes.add(equipe)
        self._session.flush()
        self._trace.record_event(
            actor_type=ActorType.HUMAIN.value,
            action=ActionAudit.EQUIPE_ENREGISTREE.value,
            entity_type=_ENTITY,
            entity_id=equipe.id,
            after={"nom": equipe.nom, "prenom": equipe.prenom},
        )
        return equipe

    def modifier(self, equipe_id: UUID, entree: EquipeUpdateInput) -> Equipe:
        """Modification partielle : seuls les champs fournis sont appliqués.

        Raises:
            ValidationError: aucune modification fournie.
        """
        equipe = self._get(equipe_id)
        modifie = False
        for nom in _CHAMPS:
            valeur = getattr(entree, nom)
            if valeur is not None and getattr(equipe, nom) != valeur:
                setattr(equipe, nom, valeur)
                modifie = True
        if not modifie:
            raise ValidationError(
                "Aucune modification fournie pour la personne du vivier",
                details={"equipe_id": str(equipe_id)},
            )
        # Synchronisation équipe → compte (incrément 24) : modifier la fiche
        # vivier met à jour l'identité et le rôle du compte lié.
        self._synchroniser_compte(equipe)
        self._trace.record_event(
            actor_type=ActorType.HUMAIN.value,
            action=ActionAudit.EQUIPE_MODIFIEE.value,
            entity_type=_ENTITY,
            entity_id=equipe.id,
            after={"nom": equipe.nom, "prenom": equipe.prenom},
        )
        return equipe

    def obtenir(self, equipe_id: UUID) -> Equipe:
        """Consultation (lecture pure)."""
        return self._get(equipe_id)

    def archiver(self, equipe_id: UUID) -> Equipe:
        """Retire une personne des listes actives sans rien détruire (20/09).

        L'historique (affectations, CV, participations) reste intact et
        consultable : il n'existe volontairement aucune suppression définitive,
        car ``affectations_equipe.equipe_id`` est en ``RESTRICT`` et un CV est
        un document officiel conservé.
        """
        return self._changer_statut(
            equipe_id,
            StatutEquipe.ARCHIVE.value,
            ActionAudit.EQUIPE_ARCHIVEE.value,
        )

    def reactiver(self, equipe_id: UUID) -> Equipe:
        """Remet une personne archivée dans les listes actives."""
        return self._changer_statut(
            equipe_id,
            StatutEquipe.ACTIF.value,
            ActionAudit.EQUIPE_REACTIVEE.value,
        )

    def _changer_statut(self, equipe_id: UUID, cible: str, action: str) -> Equipe:
        equipe = self._get(equipe_id)
        avant = equipe.statut
        if avant == cible:
            raise ValidationError(
                f"Cette personne est déjà « {cible} »",
                details={"equipe_id": str(equipe_id), "statut": cible},
            )
        equipe.statut = cible
        # Synchronisation équipe → compte (incrément 24, décision validée) :
        # archiver une personne du vivier suspend son compte ; le réactiver
        # réactive le compte (l'admin garde la main pour suspendre à nouveau).
        self._synchroniser_compte(equipe)
        self._trace.record_event(
            actor_type=ActorType.HUMAIN.value,
            action=action,
            entity_type=_ENTITY,
            entity_id=equipe.id,
            after={"statut": cible},
            event_metadata={"avant": avant},
        )
        return equipe

    def _synchroniser_compte(self, equipe: Equipe) -> None:
        """Propage la fiche vivier sur le compte lié (identité, rôle, statut).

        Aucune création ici : le compte naît d'une inscription (avec mot de
        passe choisi par le titulaire) — voir ``UtilisateurService.inscrire``.
        """
        compte = self._session.scalar(
            select(Utilisateur).where(Utilisateur.equipe_id == equipe.id)
        )
        if compte is None:
            return
        if equipe.nom:
            compte.nom = equipe.nom
        if equipe.prenom:
            compte.prenom = equipe.prenom
        if equipe.email:
            compte.email = equipe.email.lower().strip()
        roles_valides = (RoleUtilisateur.COLLABORATEUR.value, RoleUtilisateur.FORMATEUR.value)
        if equipe.role_compte in roles_valides:
            compte.role = equipe.role_compte
        if equipe.statut == StatutEquipe.ARCHIVE.value:
            if compte.statut == StatutUtilisateur.ACTIF.value:
                compte.statut = StatutUtilisateur.SUSPENDU.value
        elif equipe.statut == StatutEquipe.ACTIF.value:
            if compte.statut == StatutUtilisateur.SUSPENDU.value:
                compte.statut = StatutUtilisateur.ACTIF.value

    def lister(self, statut: str | None = None) -> list[Equipe]:
        """Vivier complet, ou seulement les personnes d'un statut donné.

        Sans filtre, les agents continuent de voir la liste entière (y compris
        les archivés) : l'archivage ne masque rien à l'analyse, il ne fait que
        retirer la personne des listes de travail.
        """
        if statut is None:
            return self.equipes.list_all()
        return self.equipes.list_by_statut(statut)

    def _get(self, equipe_id: UUID) -> Equipe:
        equipe = self.equipes.get(equipe_id)
        if equipe is None:
            raise NotFoundError(f"Personne du vivier {equipe_id} introuvable")
        return equipe


__all__ = ["EquipeService"]
