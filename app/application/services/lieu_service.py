"""Service métier — lieux d'exécution.

Un **lieu** est une donnée distincte d'une mission (règle confirmée CARSO) :
il n'est jamais porté par le lot. Le service reste volontairement minimal —
nom obligatoire, localisation libre, aucune nomenclature CARSO inventée.
"""

from uuid import UUID

from sqlalchemy.orm import Session

from app.application.dto import LieuInput, LieuUpdateInput
from app.application.trace import TraceContext
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.domain.enums import ActionAudit, ActorType
from app.domain.execution import Lieu
from app.infrastructure.repositories import LieuRepository

_ENTITY = "lieu"


class LieuService:
    """Use cases : creer, obtenir_ou_creer, modifier, obtenir, lister."""

    def __init__(self, session: Session, actor_id: str | None = None) -> None:
        self._session = session
        self.lieux = LieuRepository(session)
        self._trace = TraceContext(session, actor_id=actor_id)

    def creer(self, entree: LieuInput) -> Lieu:
        """Crée un lieu. Un nom déjà utilisé est un conflit explicite — le
        service ne fusionne jamais deux lieux silencieusement."""
        nom = entree.nom.strip()
        if not nom:
            raise ValidationError("Le nom du lieu est obligatoire")
        if self.lieux.find_by_nom(nom) is not None:
            raise ConflictError(
                f"Un lieu nommé {nom!r} existe déjà",
                details={"nom": nom},
            )
        lieu = Lieu(
            nom=nom,
            adresse=entree.adresse,
            ville=entree.ville,
            pays=entree.pays,
            zone=entree.zone,
        )
        self.lieux.add(lieu)
        self._session.flush()
        self._trace.record_event(
            actor_type=ActorType.HUMAIN.value,
            action=ActionAudit.LIEU_ENREGISTRE.value,
            entity_type=_ENTITY,
            entity_id=lieu.id,
            after={"nom": lieu.nom},
        )
        return lieu

    def obtenir_ou_creer(self, nom: str) -> Lieu:
        """Retourne le lieu existant pour ce nom, ou le crée.

        Usage contrôlé (saisie rapide / préparation de mission) : aucune
        localisation n'est inventée, seul le nom est utilisé.
        """
        existant = self.lieux.find_by_nom(nom)
        if existant is not None:
            return existant
        return self.creer(LieuInput(nom=nom))

    def modifier(self, lieu_id: UUID, entree: LieuUpdateInput) -> Lieu:
        lieu = self._get(lieu_id)
        modifie = False
        if entree.nom is not None:
            nom = entree.nom.strip()
            if not nom:
                raise ValidationError("Le nom du lieu ne peut pas être vide")
            if nom != lieu.nom:
                if self.lieux.find_by_nom(nom) is not None:
                    raise ConflictError(f"Un lieu nommé {nom!r} existe déjà")
                lieu.nom = nom
                modifie = True
        for champ in ("adresse", "ville", "pays", "zone"):
            valeur = getattr(entree, champ)
            if valeur is not None and valeur != getattr(lieu, champ):
                setattr(lieu, champ, valeur)
                modifie = True
        if not modifie:
            raise ValidationError(
                "Aucune modification fournie pour le lieu",
                details={"lieu_id": str(lieu_id)},
            )
        self._trace.record_event(
            actor_type=ActorType.HUMAIN.value,
            action=ActionAudit.LIEU_MIS_A_JOUR.value,
            entity_type=_ENTITY,
            entity_id=lieu.id,
            after={"nom": lieu.nom, "ville": lieu.ville},
        )
        return lieu

    def obtenir(self, lieu_id: UUID) -> Lieu:
        return self._get(lieu_id)

    def lister(self) -> list[Lieu]:
        return self.lieux.list_all()

    # --- internes ---------------------------------------------------------

    def _get(self, lieu_id: UUID) -> Lieu:
        lieu = self.lieux.get(lieu_id)
        if lieu is None:
            raise NotFoundError(f"Lieu {lieu_id} introuvable")
        return lieu


__all__ = ["LieuService"]
