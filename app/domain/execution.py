"""Modèles d'exécution : Mission, Equipe, AffectationEquipe, Session, Beneficiaire,
Participation, Presence, ModeleDocument — entités [C] (Phase 2 §1 / instruction/04 §2).

Absents volontairement ([P]) : modèle de disponibilité des équipes ([?] Q6),
colonnes bénéficiaires non confirmées ([?] Q9). Le vocabulaire des rôles de
mission est désormais confirmé (``RoleMission``, app/domain/enums.py).

Les relations vers les entités d'autres modules utilisent des cibles en chaîne
simple ("Document", "Offre", ...) : SQLAlchemy les résout via le
registre déclaratif une fois tous les modules importés (voir app/domain/__init__.py).

Règles métier confirmées avec CARSO :
- le **lieu d'exécution** est une donnée distincte (entité ``Lieu``) ; un lot
  n'est pas forcément un lieu ;
- une mission peut référencer **plusieurs offres** du même lot (une offre
  technique et une offre financière) — relation N-N ``mission_offres`` ;
- un **support de formation** relie Formateur ↔ Mission ↔ Document ;
- la **présence** se pointe par jour : l'inscription (``Participation``) ne
  porte pas le pointage, qui vit dans ``Presence`` — une ligne par
  ``(participation, date)`` (règle confirmée CARSO, 24/09).
"""

from __future__ import annotations

from datetime import date, time
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import Date, ForeignKey, Index, String, Text, Time, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.domain.base import Base, TimestampMixin, UuidPkMixin
from app.domain.enums import (
    SourceAffectation,
    StatutBeneficiaire,
    StatutMission,
    StatutSessionFormation,
)

if TYPE_CHECKING:
    from app.domain.document import Document
    from app.domain.organization import Budget, Offre, Organisation


class Lieu(Base, UuidPkMixin, TimestampMixin):
    """Lieu d'exécution d'une mission — donnée **distincte** (règle confirmée CARSO).

    Un lot n'est pas forcément un lieu (il peut être un domaine, une zone, une
    activité) : le lieu d'exécution est saisi sur la **mission**. On retient le
    minimum confirmé (un nom obligatoire + une localisation libre) — aucune
    nomenclature CARSO n'est inventée.
    """

    __tablename__ = "lieux"
    __table_args__ = (
        Index("ix_lieux_nom", "nom"),
        Index("ix_lieux_ville", "ville"),
    )

    nom: Mapped[str] = mapped_column(String(255), nullable=False)
    adresse: Mapped[str | None] = mapped_column(Text)
    ville: Mapped[str | None] = mapped_column(String(255))
    pays: Mapped[str | None] = mapped_column(String(100))
    zone: Mapped[str | None] = mapped_column(String(255))

    missions: Mapped[list[Mission]] = relationship(back_populates="lieu")


class MissionOffre(Base, UuidPkMixin, TimestampMixin):
    """Association Mission ↔ Offre (N-N).

    Une mission peut référencer plusieurs offres du même lot — typiquement une
    offre technique **et** une offre financière répondant au même appel. La
    table porte le lien, jamais une logique de fusion : les offres restent
    distinctes (un lot n'est jamais fusionné).
    """

    __tablename__ = "mission_offres"
    __table_args__ = (
        UniqueConstraint(
            "mission_id", "offre_id", name="uq_mission_offres_mission_id_offre_id"
        ),
        Index("ix_mission_offres_mission_id", "mission_id"),
        Index("ix_mission_offres_offre_id", "offre_id"),
    )

    mission_id: Mapped[UUID] = mapped_column(
        ForeignKey("missions.id", ondelete="CASCADE"), nullable=False
    )
    offre_id: Mapped[UUID] = mapped_column(
        ForeignKey("offres.id", ondelete="CASCADE"), nullable=False
    )


class Mission(Base, UuidPkMixin, TimestampMixin):
    """Unité opérationnelle de prestation/formation, issue d'offres approuvées [C].

    Règle confirmée CARSO : un appel à proposition se répond par une offre
    technique **et** une offre financière d'un même lot ; la mission référence
    ces offres (N-N ``mission_offres``). Une prestation directe sans offre reste
    possible (aucune offre rattachée) — ``offres`` peut être vide.

    ``lieu_id`` : le lieu d'exécution est une donnée distincte, référencée par
    la mission et jamais par le lot (règle confirmée CARSO).
    """

    __tablename__ = "missions"
    __table_args__ = (
        UniqueConstraint("reference", name="uq_missions_reference"),
        Index("ix_missions_statut", "statut"),
        Index("ix_missions_organisation_id", "organisation_id"),
        Index("ix_missions_lieu_id", "lieu_id"),
        Index("ix_missions_dates", "date_debut", "date_fin"),
    )

    organisation_id: Mapped[UUID] = mapped_column(
        ForeignKey("organisations.id", ondelete="RESTRICT"), nullable=False
    )
    lieu_id: Mapped[UUID | None] = mapped_column(ForeignKey("lieux.id", ondelete="SET NULL"))
    reference: Mapped[str] = mapped_column(String(100), nullable=False)
    titre: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    date_debut: Mapped[date | None] = mapped_column(Date)
    date_fin: Mapped[date | None] = mapped_column(Date)
    statut: Mapped[str] = mapped_column(
        String(50), nullable=False, default=StatutMission.PLANIFIEE.value,
        server_default=StatutMission.PLANIFIEE.value,
    )

    #: Offres alimentant la mission (N-N : technique + financière). Vide pour
    #: une prestation directe.
    offres: Mapped[list[Offre]] = relationship(
        "Offre", secondary="mission_offres", back_populates="missions"
    )
    lieu: Mapped[Lieu | None] = relationship(back_populates="missions")
    affectations: Mapped[list[AffectationEquipe]] = relationship(
        back_populates="mission", cascade="all, delete-orphan"
    )
    sessions: Mapped[list[SessionFormation]] = relationship(
        back_populates="mission", cascade="all, delete-orphan"
    )
    documents: Mapped[list[Document]] = relationship("Document", back_populates="mission")
    budgets: Mapped[list[Budget]] = relationship("Budget", back_populates="mission")
    supports_formation: Mapped[list[SupportFormation]] = relationship(
        back_populates="mission", cascade="all, delete-orphan"
    )


class SupportFormation(Base, UuidPkMixin, TimestampMixin):
    """Support de formation : relation Formateur ↔ Mission ↔ Document (règle CARSO).

    Un support est un **document** utilisé pendant une mission par un formateur
    (ex. ``Module_Entrepreneuriat.pdf``). Le document reste l'entité
    documentaire unique (``Document``) : le support ne crée jamais un second
    système documentaire — il porte seulement la triple relation.
    """

    __tablename__ = "supports_formation"
    __table_args__ = (
        UniqueConstraint(
            "mission_id",
            "equipe_id",
            "document_id",
            name="uq_supports_formation_mission_equipe_document",
        ),
        Index("ix_supports_formation_mission_id", "mission_id"),
        Index("ix_supports_formation_equipe_id", "equipe_id"),
        Index("ix_supports_formation_document_id", "document_id"),
    )

    mission_id: Mapped[UUID] = mapped_column(
        ForeignKey("missions.id", ondelete="CASCADE"), nullable=False
    )
    #: Le formateur — une personne du vivier affectée à la mission.
    equipe_id: Mapped[UUID] = mapped_column(
        ForeignKey("equipes.id", ondelete="RESTRICT"), nullable=False
    )
    document_id: Mapped[UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="RESTRICT"), nullable=False
    )
    libelle: Mapped[str | None] = mapped_column(String(255))
    statut: Mapped[str | None] = mapped_column(String(50))

    mission: Mapped[Mission] = relationship(back_populates="supports_formation")
    equipe: Mapped[Equipe] = relationship(back_populates="supports_formation")
    document: Mapped[Document] = relationship("Document")


class Equipe(Base, UuidPkMixin, TimestampMixin):
    """Personne du vivier CARSO. Le CV est un Document rattaché, pas une colonne [C].

    Rôle de **mission** jamais porté ici : il vit dans AffectationEquipe, par
    mission [C]. ``role_compte`` (incrément 24) porte en revanche le rôle de
    **compte** (collaborateur | formateur) attribué automatiquement au futur
    utilisateur : « rôle équipe = rôle utilisateur » (décision validée).
    """

    __tablename__ = "equipes"

    nom: Mapped[str] = mapped_column(String(100), nullable=False)
    prenom: Mapped[str] = mapped_column(String(100), nullable=False)
    email: Mapped[str | None] = mapped_column(String(255))
    telephone: Mapped[str | None] = mapped_column(String(50))
    profil: Mapped[str | None] = mapped_column(Text)
    statut: Mapped[str | None] = mapped_column(String(50))
    #: Rôle de compte attribué à l'utilisateur lié (voir docstring de classe).
    role_compte: Mapped[str | None] = mapped_column(String(50))

    affectations: Mapped[list[AffectationEquipe]] = relationship(back_populates="equipe")
    documents: Mapped[list[Document]] = relationship("Document", back_populates="equipe")
    #: Supports de formation portés par cette personne (formateur).
    supports_formation: Mapped[list[SupportFormation]] = relationship(
        back_populates="equipe"
    )


class AffectationEquipe(Base, UuidPkMixin, TimestampMixin):
    """Mission ↔ Equipe. Le rôle est porté ici, défini dans le contexte de la mission [C].

    Vocabulaire confirmé CARSO : ``RoleMission`` (formateur, chef de mission,
    assistance logistique, accompagnateur, coach formateur). La colonne reste un
    ``String`` (pas d'ENUM PostgreSQL) : c'est le **service** qui refuse une
    valeur hors référentiel, et ``RoleMission`` est la source de vérité Python.
    Règle métier : un Chef de Mission est aussi un Formateur — voir
    ``est_formateur`` (app/domain/enums.py), jamais un rôle dupliqué.
    """

    __tablename__ = "affectations_equipe"
    __table_args__ = (
        Index("ix_affectations_equipe_mission_id", "mission_id"),
        Index("ix_affectations_equipe_equipe_id", "equipe_id"),
    )

    mission_id: Mapped[UUID] = mapped_column(
        ForeignKey("missions.id", ondelete="CASCADE"), nullable=False
    )
    equipe_id: Mapped[UUID] = mapped_column(
        ForeignKey("equipes.id", ondelete="RESTRICT"), nullable=False
    )
    role_dans_mission: Mapped[str] = mapped_column(String(100), nullable=False)
    date_debut: Mapped[date | None] = mapped_column(Date)
    date_fin: Mapped[date | None] = mapped_column(Date)
    statut: Mapped[str | None] = mapped_column(String(50))
    source_affectation: Mapped[str] = mapped_column(
        String(50), nullable=False, default=SourceAffectation.MANUAL.value,
        server_default=SourceAffectation.MANUAL.value,
    )
    approved_by: Mapped[str | None] = mapped_column(String(255))

    mission: Mapped[Mission] = relationship(back_populates="affectations")
    equipe: Mapped[Equipe] = relationship(back_populates="affectations")


class SessionFormation(Base, UuidPkMixin, TimestampMixin):
    """Unité d'exécution d'une mission [C]."""

    __tablename__ = "sessions"
    __table_args__ = (
        Index("ix_sessions_mission_id", "mission_id"),
        Index("ix_sessions_dates", "date_debut", "date_fin"),
    )

    mission_id: Mapped[UUID] = mapped_column(
        ForeignKey("missions.id", ondelete="CASCADE"), nullable=False
    )
    date_debut: Mapped[date | None] = mapped_column(Date)
    date_fin: Mapped[date | None] = mapped_column(Date)
    lieu: Mapped[str | None] = mapped_column(String(255))
    theme: Mapped[str | None] = mapped_column(String(255))
    statut: Mapped[str] = mapped_column(
        String(50), nullable=False, default=StatutSessionFormation.PLANIFIEE.value,
        server_default=StatutSessionFormation.PLANIFIEE.value,
    )

    mission: Mapped[Mission] = relationship(back_populates="sessions")
    participations: Mapped[list[Participation]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )
    documents: Mapped[list[Document]] = relationship(
        "Document", back_populates="session"
    )


class Beneficiaire(Base, UuidPkMixin, TimestampMixin):
    """Personne bénéficiant des formations. Données sensibles minimisées [C].

    Champs au-delà du minimum confirmé (sexe, date de naissance...) : [?] Q9,
    ajoutés seulement après analyse des listes Excel réelles.
    """

    __tablename__ = "beneficiaires"

    nom: Mapped[str] = mapped_column(String(100), nullable=False)
    prenom: Mapped[str] = mapped_column(String(100), nullable=False)
    contact: Mapped[str | None] = mapped_column(String(255))
    organisation_origine: Mapped[str | None] = mapped_column(String(255))
    identifiant_externe: Mapped[str | None] = mapped_column(String(100))
    # Archivage logique (20/09) : un bénéficiaire archivé n'est plus proposé aux
    # inscriptions mais ses participations passées restent lisibles.
    statut: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default=StatutBeneficiaire.ACTIF.value,
        server_default=StatutBeneficiaire.ACTIF.value,
    )

    participations: Mapped[list[Participation]] = relationship(back_populates="beneficiaire")


class Participation(Base, UuidPkMixin, TimestampMixin):
    """Session ↔ Beneficiaire, unique par (session, bénéficiaire) [C].

    C'est l'**inscription**, rien de plus : depuis le 24/09 elle ne porte plus
    la présence (le pointage vit dans ``Presence``, une ligne par jour).
    """

    __tablename__ = "participations"
    __table_args__ = (
        UniqueConstraint(
            "session_id", "beneficiaire_id", name="uq_participations_session_beneficiaire"
        ),
        Index("ix_participations_session_id", "session_id"),
        Index("ix_participations_beneficiaire_id", "beneficiaire_id"),
    )

    session_id: Mapped[UUID] = mapped_column(
        ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False
    )
    beneficiaire_id: Mapped[UUID] = mapped_column(
        ForeignKey("beneficiaires.id", ondelete="RESTRICT"), nullable=False
    )
    evaluation: Mapped[str | None] = mapped_column(Text)
    observations: Mapped[str | None] = mapped_column(Text)

    session: Mapped[SessionFormation] = relationship(back_populates="participations")
    beneficiaire: Mapped[Beneficiaire] = relationship(back_populates="participations")
    presences: Mapped[list[Presence]] = relationship(
        back_populates="participation", cascade="all, delete-orphan"
    )


class Presence(Base, UuidPkMixin, TimestampMixin):
    """Pointage d'un bénéficiaire pour **une date** de la session (règle 24/09).

    Une session de cinq jours se pointe cinq fois : une ligne par
    ``(participation, date)``. ``presence`` reprend ``StatutPresence`` (présent,
    absent, en retard, excusé), les heures restent facultatives.

    Le pointage appartient à l'inscription : retirer un bénéficiaire de la
    session emporte ses pointages (``CASCADE``). L'inverse n'est jamais vrai —
    aucune session ne disparaît parce qu'un pointage a été corrigé.
    """

    __tablename__ = "presences"
    __table_args__ = (
        UniqueConstraint(
            "participation_id", "date", name="uq_presences_participation_id_date"
        ),
        Index("ix_presences_participation_id", "participation_id"),
        Index("ix_presences_date", "date"),
    )

    participation_id: Mapped[UUID] = mapped_column(
        ForeignKey("participations.id", ondelete="CASCADE"), nullable=False
    )
    date: Mapped[date] = mapped_column(Date, nullable=False)
    presence: Mapped[str | None] = mapped_column(String(50))
    heure_arrivee: Mapped[time | None] = mapped_column(Time)
    heure_depart: Mapped[time | None] = mapped_column(Time)

    participation: Mapped[Participation] = relationship(back_populates="presences")


class ModeleDocument(Base, UuidPkMixin, TimestampMixin):
    """Registre de templates par organisation cliente [C].

    ``document_template_id`` pointe vers un Document de type 'template' —
    une nouvelle version du template est un nouveau Document, jamais un
    écrasement du fichier [C].
    """

    __tablename__ = "modeles_documents"
    __table_args__ = (Index("ix_modeles_documents_organisation_id", "organisation_id"),)

    organisation_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("organisations.id", ondelete="SET NULL")
    )
    nom: Mapped[str] = mapped_column(String(255), nullable=False)
    type_document: Mapped[str] = mapped_column(String(50), nullable=False)
    document_template_id: Mapped[UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="RESTRICT"), nullable=False
    )
    version: Mapped[int] = mapped_column(default=1, nullable=False, server_default="1")
    statut: Mapped[str | None] = mapped_column(String(50))

    organisation: Mapped[Organisation | None] = relationship(
        "Organisation", back_populates="modeles_documents"
    )
    document_template: Mapped[Document] = relationship("Document")
