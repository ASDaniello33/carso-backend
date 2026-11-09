"""Tests des tools **budget de mission** de l'agent RH (Phase 2 §5).

L'agent RH doit pouvoir créer le budget d'une mission, ajuster ses lignes
(ajouter, modifier, supprimer) et le soumettre — sans jamais fournir un
montant officiel : ``cout_total`` et le total sont recalculés par le service
(règle 7 [C]), les écritures passent sous HITL et un budget approuvé est
verrouillé (nouvelle version).
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy.sql.ddl import CreateColumn

from app.agents.agent_rh import DEFINITION
from app.agents.tools.agent_rh_tools import NOMS_TOOLS
from app.application.services import BudgetService
from app.application.trace import Decision
from app.domain.base import Base
from app.domain.enums import StatutMission
from app.domain.execution import Mission
from app.domain.organization import Organisation
from app.tools.base import TypedTool


@compiles(JSONB, "sqlite")
def _jsonb_as_json(type_: JSONB, compiler: object, **kw: object) -> str:
    return "JSON"


@compiles(CreateColumn, "sqlite")
def _strip_pg(element: CreateColumn, compiler: object, **kw: object) -> str:
    ddl = compiler.visit_create_column(element)  # type: ignore[attr-defined]
    return re.sub(r"DEFAULT gen_random_uuid\(\)", "", ddl)


@pytest.fixture
def factory() -> Iterator[sessionmaker[Session]]:
    """Fabrique partagée (StaticPool) : les tools ouvrent leur propre transaction."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    yield sessionmaker(bind=engine)
    engine.dispose()


def _mission(factory: sessionmaker[Session], reference: str = "M-BUD-1") -> Mission:
    """Une mission planifiée portée par une organisation (client du dossier)."""
    with factory() as session:
        org = Organisation(nom="Client budget")
        session.add(org)
        session.flush()
        mission = Mission(
            organisation_id=org.id,
            reference=reference,
            titre="Mission budget",
            statut=StatutMission.PLANIFIE.value,
        )
        session.add(mission)
        session.commit()
        session.refresh(mission)
        return mission


def _outils(factory: sessionmaker[Session]) -> dict[str, TypedTool]:
    from app.agents.agent_rh import build_rh

    agent = build_rh(factory(), session_factory=factory)
    return {tool.name: tool for tool in agent.deep_tools()}


def _creer_avec_deux_lignes(factory: sessionmaker[Session]) -> tuple[str, str, str]:
    """Budget brouillon + deux lignes renvoyées par les tools eux-mêmes."""
    outils = _outils(factory)
    mission = _mission(factory)
    sortie = outils["creer_budget_mission"].invoke({"mission_id": str(mission.id)})
    assert sortie["ok"] is True
    budget_id = sortie["budget_id"]
    l1 = outils["ajouter_ligne_budget"].invoke(
        {
            "budget_id": budget_id,
            "categorie": "Transport",
            "quantite": "3",
            "cout_unitaire": "150000",
            "unite": "Forfait",
        }
    )
    l2 = outils["ajouter_ligne_budget"].invoke(
        {
            "budget_id": budget_id,
            "categorie": "Hébergement",
            "quantite": "12",
            "cout_unitaire": "80000",
            "unite": "Nuitée",
        }
    )
    assert l1["ok"] and l2["ok"]
    return budget_id, l1["ligne_id"], l2["ligne_id"]


# --- contrat -----------------------------------------------------------------


def test_contrat_budgets() -> None:
    """Les 7 tools budget sont déclarés ; les écritures sont sous HITL."""
    attends = {
        "lister_budgets_mission",
        "lire_budget",
        "creer_budget_mission",
        "ajouter_ligne_budget",
        "modifier_ligne_budget",
        "retirer_ligne_budget",
        "soumettre_budget",
    }
    assert attends <= set(NOMS_TOOLS)
    for nom in (
        "creer_budget_mission",
        "ajouter_ligne_budget",
        "modifier_ligne_budget",
        "retirer_ligne_budget",
        "soumettre_budget",
    ):
        assert nom in DEFINITION.approval_required, nom


def test_cout_total_n_est_pas_au_contrat() -> None:
    """Le champ ``cout_total`` n'existe pas dans l'entrée du tool (règle 7 [C])."""
    from app.agents.tools.agent_rh_tools import LigneBudgetToolInput

    assert "cout_total" not in LigneBudgetToolInput.model_fields


# --- création et lecture -----------------------------------------------------


def test_creer_budget_mission_et_lignes(factory: sessionmaker[Session]) -> None:
    """Création en brouillon, lignes calculées par le serveur, total correct."""
    outils = _outils(factory)
    mission = _mission(factory)
    # Sans devise explicite : le tool doit produire la devise de travail
    # (ariary), pas l'héritage ``MAD`` du prototype.
    sortie = outils["creer_budget_mission"].invoke({"mission_id": str(mission.id)})
    assert sortie["statut"] == "brouillon"
    assert sortie["requires_approval"] is True
    assert sortie["devise"] == "MGA"

    ligne = outils["ajouter_ligne_budget"].invoke(
        {
            "budget_id": sortie["budget_id"],
            "categorie": "Carburant",
            "quantite": "4",
            "cout_unitaire": "12000",
            "cout_total": "999999",  # fourni par erreur : ignoré, recalculé
        }
    )
    assert ligne["cout_total"] == 48000.0  # 4 × 12 000, pas la valeur fournie

    detail = outils["lire_budget"].invoke({"budget_id": sortie["budget_id"]})
    assert detail["total"] == 48000.0
    assert detail["lignes"][0]["categorie"] == "Carburant"
    assert detail["lignes"][0]["ordre"] == 1


def test_un_seul_budget_par_mission(factory: sessionmaker[Session]) -> None:
    """Un second budget pour la même mission est refusé (révision = version)."""
    outils = _outils(factory)
    mission = _mission(factory)
    assert outils["creer_budget_mission"].invoke({"mission_id": str(mission.id)})["ok"]
    second = outils["creer_budget_mission"].invoke({"mission_id": str(mission.id)})
    assert second["ok"] is False
    assert "nouvelle version" in second["error"]["message"]


def test_mission_inexistante_refusee(factory: sessionmaker[Session]) -> None:
    """Une mission hors périmètre ou inconnue est 404, jamais créée à l'aveugle."""
    outils = _outils(factory)
    sortie = outils["creer_budget_mission"].invoke(
        {"mission_id": "00000000-0000-0000-0000-000000000001"}
    )
    assert sortie["ok"] is False
    assert "introuvable" in sortie["error"]["message"]


def test_montants_negatifs_refuses(factory: sessionmaker[Session]) -> None:
    """Une quantité ou un coût unitaire négatif ne passe pas le contrat d'entrée."""
    outils = _outils(factory)
    mission = _mission(factory, "M-BUD-NEG")
    budget_id = outils["creer_budget_mission"].invoke({"mission_id": str(mission.id)})[
        "budget_id"
    ]
    sortie = outils["ajouter_ligne_budget"].invoke(
        {
            "budget_id": budget_id,
            "categorie": "Test",
            "quantite": "-1",
            "cout_unitaire": "100",
        }
    )
    assert sortie["ok"] is False


# --- lignes : modifier, supprimer -------------------------------------------


def test_modifier_et_supprimer_ligne(factory: sessionmaker[Session]) -> None:
    """La correction recalcule ; la suppression retire la ligne tracée."""
    outils = _outils(factory)
    budget_id, ligne_id, _ = _creer_avec_deux_lignes(factory)

    corrige = outils["modifier_ligne_budget"].invoke(
        {
            "budget_id": budget_id,
            "ligne_id": ligne_id,
            "categorie": "Transport",
            "quantite": "5",
            "cout_unitaire": "150000",
        }
    )
    assert corrige["cout_total"] == 750000.0
    detail = outils["lire_budget"].invoke({"budget_id": budget_id})
    assert detail["total"] == 750000.0 + 12 * 80000

    supprime =    outils["retirer_ligne_budget"].invoke(
        {"budget_id": budget_id, "ligne_id": ligne_id}
    )
    assert supprime["ok"] is True
    detail = outils["lire_budget"].invoke({"budget_id": budget_id})
    assert [ligne["ligne_id"] for ligne in detail["lignes"]] != [ligne_id]


def test_ligne_d_autre_budget_refusee(factory: sessionmaker[Session]) -> None:
    """Une ligne ne se modifie que **dans son budget** : jamais un id voisin."""
    outils = _outils(factory)
    mission = _mission(factory, "M-BUD-CROSS")
    budget_a = outils["creer_budget_mission"].invoke({"mission_id": str(mission.id)})[
        "budget_id"
    ]
    ligne = outils["ajouter_ligne_budget"].invoke(
        {
            "budget_id": budget_a,
            "categorie": "Fournitures",
            "quantite": "1",
            "cout_unitaire": "5000",
        }
    )
    # Un second budget sur une **autre** mission pour créer le leurre.
    mission_b = _mission(factory, "M-BUD-CROSS-B")
    budget_b = outils["creer_budget_mission"].invoke({"mission_id": str(mission_b.id)})[
        "budget_id"
    ]
    sortie =    outils["retirer_ligne_budget"].invoke(
        {"budget_id": budget_b, "ligne_id": ligne["ligne_id"]}
    )
    assert sortie["ok"] is False
    assert "introuvable" in sortie["error"]["message"]


# --- cycle de vie ------------------------------------------------------------


def test_soumettre_budget_vide_refusee_puis_valide(
    factory: sessionmaker[Session],
) -> None:
    """Un budget sans ligne ne part jamais en décision ; avec lignes, proposé."""
    outils = _outils(factory)
    mission = _mission(factory, "M-BUD-SOUM")
    budget_id = outils["creer_budget_mission"].invoke({"mission_id": str(mission.id)})[
        "budget_id"
    ]
    vide = outils["soumettre_budget"].invoke({"budget_id": budget_id})
    assert vide["ok"] is False
    assert "au moins une ligne" in vide["error"]["message"]

    outils["ajouter_ligne_budget"].invoke(
        {
            "budget_id": budget_id,
            "categorie": "Restauration",
            "quantite": "20",
            "cout_unitaire": "15000",
        }
    )
    soumis = outils["soumettre_budget"].invoke({"budget_id": budget_id})
    assert soumis["statut"] == "propose"
    assert soumis["requires_approval"] is True


def test_budget_approuve_verrouille(factory: sessionmaker[Session]) -> None:
    """Après approbation, aucune écriture de ligne : nouvelle version seulement."""
    outils = _outils(factory)
    budget_id, _, _ = _creer_avec_deux_lignes(factory)
    with factory() as session:
        service = BudgetService(session, actor_id="testeuse-carso")
        service.soumettre_budget(__import__("uuid").UUID(budget_id))
        service.approuver_budget(
            __import__("uuid").UUID(budget_id),
            Decision(decided_by="testeuse-carso", reason="budget arrêté"),
        )
        session.commit()

    sortie = outils["ajouter_ligne_budget"].invoke(
        {
            "budget_id": budget_id,
            "categorie": "Matériel",
            "quantite": "1",
            "cout_unitaire": "1000",
        }
    )
    assert sortie["ok"] is False
    assert "nouvelle version" in sortie["error"]["message"]
    detail = outils["lire_budget"].invoke({"budget_id": budget_id})
    assert detail["statut"] == "approuve"


def test_lister_budgets_mission(factory: sessionmaker[Session]) -> None:
    """La liste renvoie versions, statuts et totaux recalculés."""
    outils = _outils(factory)
    budget_id, _, _ = _creer_avec_deux_lignes(factory)
    mission_id = outils["lire_budget"].invoke({"budget_id": budget_id})["mission_id"]
    liste = outils["lister_budgets_mission"].invoke({"mission_id": mission_id})
    assert liste["nb_budgets"] == 1
    assert liste["budgets"][0]["budget_id"] == budget_id
    assert liste["budgets"][0]["nb_lignes"] == 2


# --- payloads d'erreur (types métier, jamais un crash) -----------------------


def test_payload_invalide_renvoie_erreur_lisible(
    factory: sessionmaker[Session],
) -> None:
    """Un montant non numérique est une erreur de schéma lisible, pas un crash."""
    outils = _outils(factory)
    mission = _mission(factory, "M-BUD-TYPE")
    budget_id = outils["creer_budget_mission"].invoke({"mission_id": str(mission.id)})[
        "budget_id"
    ]
    sortie: dict[str, Any] = outils["ajouter_ligne_budget"].invoke(
        {
            "budget_id": budget_id,
            "categorie": "Test",
            "quantite": "beaucoup",
            "cout_unitaire": "100",
        }
    )
    assert sortie["ok"] is False
    assert sortie["error"]["code"] == "business_validation_error"
    assert "quantite" in sortie["error"]["message"]
