"""Schémas API — sessions de formation, participations et présences (Lot C1).

Note d'import : le type ``date`` est importé sous l'alias ``DateType``. Un champ
nommé ``date`` masque le type homonyme dans l'espace de noms de sa classe, et
Pydantic (annotations différées, PEP 563) ne peut alors plus résoudre
``date | None``. L'alias supprime l'ambiguïté sans renommer le champ exposé.
"""

from datetime import date as DateType
from datetime import time
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class SessionCreate(BaseModel):
    """Planification d'une session rattachée à une mission existante."""

    theme: str | None = Field(default=None, max_length=255)
    date_debut: DateType | None = None
    date_fin: DateType | None = None
    lieu: str | None = Field(default=None, max_length=255)


class SessionUpdate(BaseModel):
    """Modification d'une session (la mission de rattachement ne bouge pas)."""

    theme: str | None = Field(default=None, max_length=255)
    date_debut: DateType | None = None
    date_fin: DateType | None = None
    lieu: str | None = Field(default=None, max_length=255)


class SessionStatutUpdate(BaseModel):
    """Transition de statut (machine à états ``session``)."""

    statut: str = Field(min_length=1, max_length=50)


class SessionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    mission_id: UUID
    theme: str | None = None
    date_debut: DateType | None = None
    date_fin: DateType | None = None
    lieu: str | None = None
    statut: str


class ParticipationCreate(BaseModel):
    """Inscription d'un bénéficiaire à une session."""

    beneficiaire_id: UUID


class PresencePointageCreate(BaseModel):
    """Pointage d'**une** personne **pour une date** (règle du 24/09).

    La date est obligatoire : une présence sans jour ne dit rien. Elle doit
    tomber dans la plage de la session (sinon sa mission) — le service refuse.
    """

    date: DateType
    presence: str = Field(min_length=1, max_length=50)
    heure_arrivee: time | None = None
    heure_depart: time | None = None


class ParticipationUpdate(BaseModel):
    """Évaluation/observations — seuls les champs fournis sont appliqués.

    Pas de présence ici : elle se pointe pour une date précise, via
    ``/sessions/{id}/presences`` ou ``/participations/{id}/presence``.
    """

    evaluation: str | None = None
    observations: str | None = None


class ParticipationRead(BaseModel):
    """Inscription d'un bénéficiaire — **sans** pointage (table ``presences``)."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    session_id: UUID
    beneficiaire_id: UUID
    evaluation: str | None = None
    observations: str | None = None


class PresenceRead(BaseModel):
    """Un pointage daté."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    participation_id: UUID
    date: DateType
    presence: str | None = None
    heure_arrivee: time | None = None
    heure_depart: time | None = None


class PresenceLigne(BaseModel):
    """Une ligne de la fiche de présence d'une session, pour la date de l'envoi."""

    participation_id: UUID
    presence: str = Field(min_length=1, max_length=50)
    heure_arrivee: time | None = None
    heure_depart: time | None = None


class PresenceLotUpdate(BaseModel):
    """Pointage de toute la fiche **d'une date** (une seule transaction).

    Le 01, le 02, le 03… : chaque journée est un envoi, jamais un mélange de
    dates dans la même transaction.
    """

    date: DateType
    lignes: list[PresenceLigne] = Field(min_length=1)


class FichePresenceCreate(BaseModel):
    """Génération du classeur de présence — pour **une** date."""

    date: DateType


class ImportApercuCreate(BaseModel):
    """Aperçu d'import depuis un classeur déposé dans ``Document``."""

    document_id: UUID
    sheet: str | None = None
    max_lignes: int = Field(default=200, ge=1, le=500)


class ImportLigneCorrigee(BaseModel):
    """Ligne telle que l'utilisateur l'a corrigée avant enregistrement."""

    nom: str = Field(default="", max_length=100)
    prenom: str = Field(default="", max_length=100)
    contact: str | None = Field(default=None, max_length=255)
    organisation_origine: str | None = Field(default=None, max_length=255)
    identifiant_externe: str | None = Field(default=None, max_length=100)


class ImportConfirmationCreate(BaseModel):
    """Les lignes confirmées font foi : elles portent les corrections humaines.

    Le classeur lui-même n'est plus référencé ici : l'aperçu (``document_id``)
    a déjà établi le lien, et ce sont les lignes corrigées qui font foi.
    """

    lignes: list[ImportLigneCorrigee] = Field(min_length=1)


class ImportLigneRead(BaseModel):
    """Ligne d'aperçu classée — aucune écriture n'a eu lieu à ce stade."""

    nom: str
    prenom: str
    contact: str | None = None
    organisation_origine: str | None = None
    identifiant_externe: str | None = None
    statut: str
    message: str = ""


class ImportApercuRead(BaseModel):
    """Aperçu d'import : table corrigeable, sans aucun bénéficiaire créé."""

    feuille: str
    colonnes: list[str] = Field(default_factory=list)
    nb_lignes: int = 0
    nb_valides: int = 0
    lignes: list[ImportLigneRead] = Field(default_factory=list)


class ImportResultatRead(BaseModel):
    """Décompte d'un import confirmé."""

    crees: int = 0
    inscrits: int = 0
    ignores: int = 0
    doublons_signales: int = 0
