"""Schémas API — missions (Lot C1, instruction/02 §F).

Une mission peut référencer **plusieurs offres** d'un même lot (offre technique
+ offre financière) et possède son propre lieu d'exécution.
"""

from datetime import date
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, computed_field

from app.api.schemas.lieu import LieuRead
from app.api.schemas.offre import OffreResume


class MissionDepuisAppelCreate(BaseModel):
    """Création d'une mission depuis un appel et son lot (chemin UI unique).

    L'organisation est **déduite de l'appel** : elle n'est pas un champ du corps,
    l'appelant ne peut pas la choisir. Le lot doit appartenir à l'appel (422
    sinon). Les offres approuvées du lot sont référencées par le service.
    """

    appel_a_proposition_id: UUID
    lot_id: UUID
    reference: str = Field(min_length=1, max_length=100)
    titre: str = Field(min_length=1, max_length=255)
    description: str | None = None
    date_debut: date | None = None
    date_fin: date | None = None
    lieu_id: UUID | None = None


class MissionDepuisOffresCreate(BaseModel):
    """Création d'une mission issue d'offres approuvées (règle 5 [C]).

    Toutes les offres doivent appartenir au même lot ; un appel à proposition se
    répond par une offre technique et une offre financière.
    """

    offre_ids: list[UUID] = Field(min_length=1)
    reference: str = Field(min_length=1, max_length=100)
    titre: str = Field(min_length=1, max_length=255)
    description: str | None = None
    date_debut: date | None = None
    date_fin: date | None = None
    lieu_id: UUID | None = None


class MissionDirecteCreate(BaseModel):
    """Création d'une prestation directe sans offre."""

    organisation_id: UUID
    reference: str = Field(min_length=1, max_length=100)
    titre: str = Field(min_length=1, max_length=255)
    description: str | None = None
    date_debut: date | None = None
    date_fin: date | None = None
    lieu_id: UUID | None = None


class MissionStatutUpdate(BaseModel):
    """Changement de statut (machine à états ``mission``)."""

    statut: str = Field(min_length=1, max_length=50)


class MissionLieuUpdate(BaseModel):
    """Rattachement du lieu d'exécution (``null`` retire le lieu).

    Le lieu est une entité distincte référencée par identifiant : aucun texte
    libre n'est accepté ici, le service refuse un identifiant inconnu.
    """

    lieu_id: UUID | None = None


class MissionUpdate(BaseModel):
    """Modification partielle : titre, lieu, description (jamais les offres).

    Un champ absent n'est pas appliqué ; le lieu passe par identifiant (entité
    distincte), jamais par un texte libre. Détacher le lieu se fait par
    ``PATCH /missions/{id}/lieu`` avec ``lieu_id = null`` : un ``null`` ici
    signifie « non fourni ».
    """

    titre: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    lieu_id: UUID | None = None


class MissionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    organisation_id: UUID
    reference: str
    titre: str
    description: str | None = None
    date_debut: date | None = None
    date_fin: date | None = None
    lieu_id: UUID | None = None
    statut: str
    offres: list[OffreResume] = Field(default_factory=list)
    lieu: LieuRead | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def offre_ids(self) -> list[UUID]:
        """Identifiants des offres rattachées — pratique pour l'UI et les filtres."""
        return [offre.id for offre in self.offres]
