"""Pure domain tests — metadata integrity and deterministic budget rule (no DB)."""

from decimal import Decimal

import pytest
from sqlalchemy.orm import configure_mappers

from app.domain import Base
from app.domain.enums import (
    RoleMission,
    StatutMission,
    StatutOffre,
    StatutSessionFormation,
    est_formateur,
)
from app.domain.organization import LigneBudget
from app.domain.rules import compute_budget_total, compute_cout_total

EXPECTED_TABLES = {
    "organisations",
    "appels",
    "lots",
    "offres",
    "missions",
    "lieux",
    "mission_offres",
    "supports_formation",
    "equipes",
    "affectations_equipe",
    "documents",
    "beneficiaires",
    "sessions",
    "participations",
    "presences",
    "budgets",
    "lignes_budget",
    "modeles_documents",
    "approbations",
    "agent_tasks",
    "audit_events",
    "utilisateurs",
    "codes_reinitialisation_mot_de_passe",
    "configurations_runtime_agent",
    "surcharges_agent",
    "configurations_agent_personnel",
    "skills",
    "serveurs_mcp",
    "parametres_voyage",
    "itineraires_cache",
    "osm_voies",
    "notifications",
    "conversations",
    "membres_conversation",
    "messages",
    "annonces",
    "annonces_pieces",
    "annonces_lectures",
    "commentaires",
    "reactions",
}


def test_metadata_contains_all_confirmed_tables() -> None:
    assert EXPECTED_TABLES == set(Base.metadata.tables)


def test_mappers_configure_without_error() -> None:
    """Configure le graphe complet des relations : back_populates cohérents, cibles résolues."""
    configure_mappers()


def test_affectation_carries_role_not_equipe() -> None:
    """Règle [C]: le rôle vit dans affectations_equipe, jamais sur equipes."""
    affectation_cols = set(Base.metadata.tables["affectations_equipe"].columns.keys())
    equipe_cols = set(Base.metadata.tables["equipes"].columns.keys())

    assert "role_dans_mission" in affectation_cols
    assert "role_dans_mission" not in equipe_cols


def test_participation_unique_per_session_and_beneficiaire() -> None:
    """Règle [C]: unicité (session, bénéficiaire)."""
    table = Base.metadata.tables["participations"]
    uniques = {tuple(sorted(c.name for c in u.columns)) for u in table.constraints
               if u.__class__.__name__ == "UniqueConstraint"}

    assert ("beneficiaire_id", "session_id") in uniques


def test_presence_unique_per_participation_and_date() -> None:
    """Règle du 24/09 : un seul pointage par (inscription, date).

    C'est cette unicité qui fait qu'une correction corrige au lieu de dupliquer
    — la garantie est en base, pas seulement dans le service.
    """
    table = Base.metadata.tables["presences"]
    uniques = {tuple(sorted(c.name for c in u.columns)) for u in table.constraints
               if u.__class__.__name__ == "UniqueConstraint"}

    assert ("date", "participation_id") in uniques


def test_l_inscription_ne_porte_plus_la_presence() -> None:
    """Une seule vérité pour la présence : la table ``presences``."""
    colonnes = set(Base.metadata.tables["participations"].columns.keys())

    assert "presence" not in colonnes
    assert "heure_arrivee" not in colonnes
    assert "heure_depart" not in colonnes


def _uniques(table_name: str) -> set[tuple[str, ...]]:
    table = Base.metadata.tables[table_name]
    return {
        tuple(sorted(c.name for c in u.columns))
        for u in table.constraints
        if u.__class__.__name__ == "UniqueConstraint"
    }


def test_lot_to_offre_is_unique_per_type() -> None:
    """Un lot porte au plus une offre de chaque type (technique + financière)."""
    assert ("lot_id", "type") in _uniques("offres")


def test_offre_repond_a_appel_et_lot() -> None:
    """Une offre répond toujours à ``Appel + Lot`` : les deux FK existent."""
    colonnes = set(Base.metadata.tables["offres"].columns.keys())
    assert "appel_id" in colonnes
    assert "lot_id" in colonnes
    assert "type" in colonnes


def test_mission_offres_est_n_n() -> None:
    """Une mission référence plusieurs offres (technique + financière)."""
    assert ("mission_id", "offre_id") in _uniques("mission_offres")
    # Le lien 1-1 historique a disparu de ``missions``.
    assert "offre_id" not in set(Base.metadata.tables["missions"].columns.keys())
    assert "offre_formation_id" not in set(Base.metadata.tables["missions"].columns.keys())


def test_mission_porte_son_lieu() -> None:
    """Le lieu est une donnée distincte, référencée par la mission."""
    colonnes = set(Base.metadata.tables["missions"].columns.keys())
    assert "lieu_id" in colonnes
    assert "lieu" not in colonnes  # plus de texte libre


def test_appel_porte_son_type() -> None:
    """Un appel est soit un appel à proposition, soit une manifestation d'intérêt."""
    colonnes = set(Base.metadata.tables["appels"].columns.keys())
    assert "type" in colonnes


def test_chef_de_mission_est_formateur() -> None:
    """Règle confirmée : un Chef de Mission est également un Formateur."""
    assert est_formateur(RoleMission.CHEF_DE_MISSION.value) is True
    assert est_formateur(RoleMission.FORMATEUR.value) is True
    assert est_formateur(RoleMission.ACCOMPAGNATEUR.value) is False
    # Rôle non confirmé comme formateur : aucune présomption.
    assert est_formateur(RoleMission.COACH_FORMATEUR.value) is False


def test_support_formation_lie_formateur_mission_document() -> None:
    """Un support relie Formateur ↔ Mission ↔ Document (unique par triplet)."""
    colonnes = set(Base.metadata.tables["supports_formation"].columns.keys())
    assert {"mission_id", "equipe_id", "document_id"} <= colonnes
    assert ("document_id", "equipe_id", "mission_id") in _uniques("supports_formation")


def test_status_defaults_use_confirmed_vocabulary() -> None:
    """Vérifie les défauts sur les Column objects (les défauts Python s'appliquent au flush).

    ADR 0012 : une mission naît « en planification » ; une session née de la
    planification d'une mission démarre « planifiée ».
    """
    mission_statut = Base.metadata.tables["missions"].columns["statut"].default.arg
    session_statut = (
        Base.metadata.tables["sessions"].columns["statut"].default.arg
    )
    offre_statut = (
        Base.metadata.tables["offres"].columns["statut"].default.arg
    )

    assert mission_statut == StatutMission.EN_PLANIFICATION.value
    assert session_statut == StatutSessionFormation.PLANIFIE.value
    assert offre_statut == StatutOffre.BROUILLON.value


def test_cycle_de_vie_un_lot_une_mission_une_session() -> None:
    """ADR 0012 : les cardinalités confirmées sont posées dans le schéma."""
    # 1 lot → 1 mission (les prestations directes ne consomment aucun lot).
    assert ("lot_id",) in _uniques("missions")
    # 1 mission → 1 session.
    assert ("mission_id",) in _uniques("sessions")
    colonnes_mission = set(Base.metadata.tables["missions"].columns.keys())
    assert {"lot_id", "organisation_id", "lieu_id"} <= colonnes_mission


def test_propriete_documentaire_dans_le_schema() -> None:
    """ADR 0013 : propriétaire, visibilité, références de partage."""
    documents = set(Base.metadata.tables["documents"].columns.keys())
    assert {"proprietaire_id", "visibilite"} <= documents
    visibilite = Base.metadata.tables["documents"].columns["visibilite"]
    assert visibilite.default.arg == "prive"
    messages = set(Base.metadata.tables["messages"].columns.keys())
    annonces = set(Base.metadata.tables["annonces_pieces"].columns.keys())
    assert "document_id" in messages
    assert "document_id" in annonces


def test_fonction_libre_sur_la_personne() -> None:
    """ADR 0012 : la fonction de travail est libre et portée par la personne."""
    equipes = set(Base.metadata.tables["equipes"].columns.keys())
    assert "fonction" in equipes


def test_ligne_budget_columns_are_numeric_not_float() -> None:
    """instruction/04 §4: valeurs monétaires en types numériques, jamais float."""
    cout_total = Base.metadata.tables["lignes_budget"].columns["cout_total"]
    assert cout_total.type.__class__.__name__ == "Numeric"


class TestDeterministicBudgetRule:
    """Règle 7 [C]: calcul déterministe côté serveur, le LLM n'invente aucun montant."""

    def test_simple_line(self) -> None:
        assert compute_cout_total(Decimal("2.5"), Decimal("10.00")) == Decimal("25.00")

    def test_rounding_half_up(self) -> None:
        assert compute_cout_total(Decimal("3"), Decimal("0.335")) == Decimal("1.01")

    def test_zero_quantity(self) -> None:
        assert compute_cout_total(Decimal("0"), Decimal("199.99")) == Decimal("0.00")

    def test_negative_quantity_rejected(self) -> None:
        with pytest.raises(ValueError, match="positifs"):
            compute_cout_total(Decimal("-1"), Decimal("10"))

    def test_budget_total_sums_lines(self) -> None:
        total = compute_budget_total([Decimal("25.00"), Decimal("10.505"), Decimal("0.004")])
        assert total == Decimal("35.51")

    def test_orm_line_uses_the_rule(self) -> None:
        line = LigneBudget(
            budget_id=__import__("uuid").uuid4(),
            categorie="formateur",
            quantite=Decimal("4"),
            cout_unitaire=Decimal("1500.00"),
            cout_total=compute_cout_total(Decimal("4"), Decimal("1500.00")),
        )
        assert line.cout_total == Decimal("6000.00")
