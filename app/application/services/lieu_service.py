"""Service métier—lieux d'exécution.

Un **lieu** est une donnée distincte d'une mission (règle confirmée CARSO) :
il n'est jamais porté par le lot. Le service reste volontairement minimal—
nom obligatoire, localisation libre, aucune nomenclature CARSO inventée.

Les coordonnées (WGS84) sont une paire : les deux colonnes vont ensemble,
bornées aux plages valides, arrondies à 6 décimales (~11 cm). Un lieu sans
coordonnées reste utilisable comme lieu d'exécution — il n'a simplement pas de
position sur la carte.

Périmètre (décision du 22/10) : l'équipe interne gère le référentiel ; un
formateur **lit** les lieux de ses missions et **ajuste la position** du
marqueur, il ne crée pas de lieu et ne renomme rien.
"""

from uuid import UUID

from sqlalchemy.orm import Session

from app.application.dto import LieuInput, LieuUpdateInput
from app.application.perimetre import Perimetre
from app.application.trace import TraceContext
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.domain.enums import ActionAudit, ActorType
from app.domain.execution import Lieu
from app.infrastructure.repositories import LieuRepository

_ENTITY = "lieu"

_LATITUDE_MIN, _LATITUDE_MAX = -90.0, 90.0
_LONGITUDE_MIN, _LONGITUDE_MAX = -180.0, 180.0


def valider_coordonnees(
    latitude: float | None, longitude: float | None
) -> tuple[float, float] | None:
    """Paire de coordonnées valide, ou ``None`` sans position.

    Raises:
        ValidationError: une seule coordonnée fournie, ou valeur hors plage
            WGS84 — une position partielle dessinerait un point au milieu de
            l'océan.
    """
    if (latitude is None) != (longitude is None):
        raise ValidationError(
            "Coordonnées incomplètes : la latitude et la longitude vont ensemble",
            details={"latitude": latitude, "longitude": longitude},
        )
    if latitude is None or longitude is None:
        return None
    if not _LATITUDE_MIN <= latitude <= _LATITUDE_MAX:
        raise ValidationError(
            f"Latitude invalide : entre {_LATITUDE_MIN} et {_LATITUDE_MAX}",
            details={"latitude": latitude},
        )
    if not _LONGITUDE_MIN <= longitude <= _LONGITUDE_MAX:
        raise ValidationError(
            f"Longitude invalide : entre {_LONGITUDE_MIN} et {_LONGITUDE_MAX}",
            details={"longitude": longitude},
        )
    return round(latitude, 6), round(longitude, 6)


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
        coordonnees = valider_coordonnees(entree.latitude, entree.longitude)
        lieu = Lieu(
            nom=nom,
            adresse=entree.adresse,
            ville=entree.ville,
            pays=entree.pays,
            zone=entree.zone,
            latitude=coordonnees[0] if coordonnees else None,
            longitude=coordonnees[1] if coordonnees else None,
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
        coordonnees = valider_coordonnees(entree.latitude, entree.longitude)
        if coordonnees is not None and (coordonnees[0], coordonnees[1]) != (
            lieu.latitude,
            lieu.longitude,
        ):
            lieu.latitude, lieu.longitude = coordonnees
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

    # --- périmètre (formateur : ses missions) ---------------------------------

    def lister_pour_perimetre(self, perimetre: Perimetre) -> list[Lieu]:
        """Lieux visibles : tous pour l'interne, ceux de ses missions sinon."""
        if perimetre.est_complet:
            return self.lister()
        return self.lieux.list_pour_missions(set(perimetre.missions_autorisees))

    def obtenir_pour_perimetre(self, lieu_id: UUID, perimetre: Perimetre) -> Lieu:
        """Lieu si le périmètre l'atteint — sinon 404 (l'existence ne se devine pas)."""
        lieu = self._get(lieu_id)
        if not self.est_dans_perimetre(lieu, perimetre):
            raise NotFoundError(f"Lieu {lieu_id} introuvable")
        return lieu

    def est_dans_perimetre(self, lieu: Lieu, perimetre: Perimetre) -> bool:
        """Vrai si ce lieu sert une mission du périmètre (interne ⇒ toujours)."""
        if perimetre.est_complet:
            return True
        return any(
            mission.id in perimetre.missions_autorisees for mission in lieu.missions
        )

    def positionner(
        self,
        lieu_id: UUID,
        latitude: float,
        longitude: float,
        *,
        acteur_id: str | None = None,
        acteur_type: ActorType = ActorType.HUMAIN,
    ) -> Lieu:
        """Ajuste la position d'un marqueur (drag sur la carte, lecture GPS).

        Les deux coordonnées sont obligatoires : on ne déplace jamais "la moitié
        d'un point". Une écriture d'agent porte son ``actor_type`` pour que la
        trace lise « qui a déplacé le marqueur ».
        """
        lieu = self._get(lieu_id)
        coordonnees = valider_coordonnees(latitude, longitude)
        if coordonnees is None:
            raise ValidationError("La position exige une latitude et une longitude")
        avant = {"latitude": lieu.latitude, "longitude": lieu.longitude}
        lieu.latitude, lieu.longitude = coordonnees
        self._session.flush()
        self._trace.record_event(
            actor_type=acteur_type.value,
            action=ActionAudit.LIEU_POSITION_MODIFIEE.value,
            entity_type=_ENTITY,
            entity_id=lieu.id,
            before=avant,
            after={"latitude": lieu.latitude, "longitude": lieu.longitude},
        )
        return lieu

    # --- internes ---------------------------------------------------------

    def _get(self, lieu_id: UUID) -> Lieu:
        lieu = self.lieux.get(lieu_id)
        if lieu is None:
            raise NotFoundError(f"Lieu {lieu_id} introuvable")
        return lieu


def verifier_lieu_existant(lieu_id: UUID | None, session: Session) -> None:
    """Un rattachement vise un lieu **existant** — jamais un UUID au hasard.

    La clé étrangère refuse aussi, mais un ``IntegrityError`` rend un 500 sans
    message d'interface : la validation explicite rend un 404 actionnable.
    """
    if lieu_id is not None and session.get(Lieu, lieu_id) is None:
        raise NotFoundError(
            f"Lieu {lieu_id} introuvable", details={"lieu_id": str(lieu_id)}
        )


__all__ = ["LieuService", "verifier_lieu_existant"]
