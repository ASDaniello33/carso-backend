"""Tests API — routes /api/v1 sur les services (SQLite mémoire, session override).

La dépendance de session est redirigée vers SQLite : les requêtes suivent le
même contrat transactionnel (commit en fin de requête). On vérifie à la fois
la sémantique HTTP (202/404/409/201) et la persistance réelle des traces.
"""

from collections.abc import Iterator
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.domain.document import Approbation, AuditEvent
from app.domain.enums import (
    ActionAudit,
    StatutAffectation,
    StatutAppelAProposition,
    StatutMission,
)
from app.domain.execution import Equipe, Mission
from app.domain.organization import AppelAProposition, Budget, Lot, Organisation
from tests.conftest import seed


@pytest.fixture
def session_factory(
    api_session_factory: sessionmaker[Session],
) -> Iterator[sessionmaker[Session]]:
    yield api_session_factory


def _seed_org(factory: sessionmaker[Session]) -> Organisation:
    org = Organisation(nom="Client API")
    seed(factory, org)
    return org


def _seed_appel(factory: sessionmaker[Session]) -> AppelAProposition:
    org = _seed_org(factory)
    appel = AppelAProposition(
        organisation_id=org.id, reference=f"AO-API-{uuid4().hex[:8]}", titre="Formation API"
    )
    seed(factory, appel)
    return appel


def _seed_lot(factory: sessionmaker[Session], appel: AppelAProposition) -> Lot:
    lot = Lot(appel_a_proposition_id=appel.id, numero="1", titre="Lot 1")
    seed(factory, lot)
    return lot


def _post_offre(
    api_client: TestClient,
    lot: Lot,
    reference: str,
    titre: str,
    type_offre: str = "offre_technique",
) -> Response:
    """Crée un brouillon d'offre par l'API (un type explicite est obligatoire)."""
    return api_client.post(
        "/api/v1/offres/",
        json={
            "lot_id": str(lot.id),
            "reference": reference,
            "titre": titre,
            "type": type_offre,
        },
    )


def _seed_mission(factory: sessionmaker[Session]) -> Mission:
    org = _seed_org(factory)
    mission = Mission(
        organisation_id=org.id,
        reference="M-API-01",
        titre="Mission API",
        statut=StatutMission.PLANIFIEE.value,
    )
    seed(factory, mission)
    return mission


def _seed_equipe(factory: sessionmaker[Session]) -> Equipe:
    membre = Equipe(nom="Haddad", prenom="Omar", email="ohaddad@example.com")
    seed(factory, membre)
    return membre


# --- Santé ----------------------------------------------------------------------


def test_health_ok(client: TestClient) -> None:
    resp = client.get("/api/v1/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


# --- Appels à proposition --------------------------------------------------------------


def test_get_appel_inexistant_404(api_client: TestClient) -> None:
    resp = api_client.get(f"/api/v1/appels-a-proposition/{uuid4()}")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"


def test_get_appel_existant(api_client: TestClient, session_factory) -> None:
    appel = _seed_appel(session_factory)

    resp = api_client.get(f"/api/v1/appels-a-proposition/{appel.id}")

    assert resp.status_code == 200
    body = resp.json()
    assert body["reference"] == appel.reference
    assert body["statut"] == StatutAppelAProposition.RECU.value


def test_flux_extraction_et_validation(api_client: TestClient, session_factory) -> None:
    """P1 complet via HTTP : proposition (202) → décision humaine → traces."""
    appel = _seed_appel(session_factory)

    propose = api_client.post(
        f"/api/v1/appels-a-proposition/{appel.id}/extraction",
        json={
            # Un lot exploitable demande numéro **et** titre : sans titre, la
            # validation n'officialiserait rien (elle est refusée, 409).
            "donnees_extraites": {"lots": [{"numero": "1", "titre": "Lot 1"}]},
            "proposed_by_agent": "AgentAnalyseurAppelAProposition",
        },
    )
    assert propose.status_code == 202
    assert propose.json()["statut"] == StatutAppelAProposition.PROPOSE.value

    validation = api_client.post(
        f"/api/v1/appels-a-proposition/{appel.id}/validation",
        json={"decision": "approuve", "reason": "ok"},
    )
    assert validation.status_code == 200
    assert validation.json()["statut"] == StatutAppelAProposition.VALIDE.value

    with session_factory() as s:
        approbations = s.scalars(select(Approbation)).all()
        events = s.scalars(select(AuditEvent)).all()
    assert len(approbations) == 1
    assert approbations[0].decided_by  # identifiant utilisateur (lab ou JWT)
    assert approbations[0].proposed_by_agent == "AgentAnalyseurAppelAProposition"
    assert len(events) >= 2  # proposition (agent) + décision (humain)


def test_extraction_sur_appel_inexistant_404(api_client: TestClient) -> None:
    resp = api_client.post(
        f"/api/v1/appels-a-proposition/{uuid4()}/extraction",
        json={"donnees_extraites": {}, "proposed_by_agent": "agent"},
    )
    assert resp.status_code == 404


def test_extraction_payload_invalide_422(api_client: TestClient, session_factory) -> None:
    appel = _seed_appel(session_factory)
    resp = api_client.post(
        f"/api/v1/appels-a-proposition/{appel.id}/extraction",
        json={"donnees_extraites": {}},  # proposed_by_agent manquant
    )
    assert resp.status_code == 422


def test_creer_appel_a_proposition(api_client: TestClient, session_factory) -> None:
    org = _seed_org(session_factory)
    cree = api_client.post(
        "/api/v1/appels-a-proposition/",
        json={
            "organisation_id": str(org.id),
            "reference": "ULA-097",
            "titre": "Incubation TEFIALA",
        },
    )
    assert cree.status_code == 201, cree.text
    assert cree.json()["statut"] == StatutAppelAProposition.RECU.value
    assert cree.json()["reference"] == "ULA-097"

    liste = api_client.get("/api/v1/appels-a-proposition/")
    assert liste.status_code == 200
    assert any(item["reference"] == "ULA-097" for item in liste.json())


def test_creer_appel_organisation_absente_404(api_client: TestClient) -> None:
    resp = api_client.post(
        "/api/v1/appels-a-proposition/",
        json={
            "organisation_id": str(uuid4()),
            "reference": "AO-X",
            "titre": "Sans org",
        },
    )
    assert resp.status_code == 404


def test_validation_refusee_sans_lot_exploitable(api_client: TestClient, session_factory) -> None:
    """Le défaut d'origine ne se reproduit plus : 409, appel intact, zéro lot."""
    appel = _seed_appel(session_factory)
    api_client.post(
        f"/api/v1/appels-a-proposition/{appel.id}/extraction",
        json={
            "donnees_extraites": {"lots": []},
            "proposed_by_agent": "agent_generateur_offre",
        },
    )

    validation = api_client.post(
        f"/api/v1/appels-a-proposition/{appel.id}/validation",
        json={"decision": "approuve"},
    )

    assert validation.status_code == 409, validation.text
    assert validation.json()["error"]["code"] == "conflict"
    assert api_client.get(f"/api/v1/appels-a-proposition/{appel.id}").json()["statut"] == (
        StatutAppelAProposition.PROPOSE.value
    )
    assert api_client.get(f"/api/v1/appels-a-proposition/{appel.id}/lots/").json() == []


def test_remise_en_proposition_repare_appel_valide_sans_lot(
    api_client: TestClient, session_factory
) -> None:
    """Réparation HTTP : une nouvelle proposition débloque l'appel figé."""
    appel = _seed_appel(session_factory)
    api_client.post(
        f"/api/v1/appels-a-proposition/{appel.id}/extraction",
        json={
            "donnees_extraites": {"lots": []},
            "proposed_by_agent": "agent_generateur_offre",
        },
    )
    # État figé hérité de l'ancien comportement (validation forcée en base).
    with session_factory() as s:
        entite = s.get(AppelAProposition, appel.id)
        entite.statut = StatutAppelAProposition.VALIDE
        s.commit()

    reprise = api_client.post(
        f"/api/v1/appels-a-proposition/{appel.id}/proposition",
        json={
            "donnees_extraites": {"lots": [{"numero": "1", "titre": "Lot 1"}]},
            "proposed_by_agent": "agent_generateur_offre",
        },
    )

    assert reprise.status_code == 200, reprise.text
    assert reprise.json()["statut"] == StatutAppelAProposition.PROPOSE.value

    validation = api_client.post(
        f"/api/v1/appels-a-proposition/{appel.id}/validation",
        json={"decision": "approuve"},
    )
    assert validation.status_code == 200, validation.text
    lots = api_client.get(f"/api/v1/appels-a-proposition/{appel.id}/lots/").json()
    assert [lot["numero"] for lot in lots] == ["1"]


def test_creation_offre_sans_reference_attribuee_et_unique(
    api_client: TestClient, session_factory
) -> None:
    """Régression : deux lots numérotés « 1 » dans deux appels ne se battent plus.

    Le client ne fabrique plus la référence : le service attribue la première
    libre, et un doublon explicite ressort en 409 (jamais 500 de contrainte).
    """
    org = _seed_org(session_factory)

    def _appel_et_lot(reference: str):
        appel = api_client.post(
            "/api/v1/appels-a-proposition/",
            json={"organisation_id": str(org.id), "reference": reference, "titre": "Appel"},
        ).json()
        lot = api_client.post(
            f"/api/v1/appels-a-proposition/{appel['id']}/lots/",
            json={"numero": "1", "titre": "Lot 1"},
        ).json()
        return lot

    premier = api_client.post(
        "/api/v1/offres/",
        json={"lot_id": _appel_et_lot("OFF-REF-A")["id"], "type": "autre", "titre": "Offre A"},
    )
    assert premier.status_code == 201, premier.text
    assert premier.json()["reference"] == "OFFRE-AUTRE-L1"

    second = api_client.post(
        "/api/v1/offres/",
        json={"lot_id": _appel_et_lot("OFF-REF-B")["id"], "type": "autre", "titre": "Offre B"},
    )
    assert second.status_code == 201, second.text
    assert second.json()["reference"] == "OFFRE-AUTRE-L1-2"

    # Une référence explicite déjà prise : 409 actionnable, pas 500.
    doublon = api_client.post(
        "/api/v1/offres/",
        json={
            "lot_id": _appel_et_lot("OFF-REF-C")["id"],
            "type": "autre",
            "reference": "OFFRE-AUTRE-L1",
            "titre": "Doublon",
        },
    )
    assert doublon.status_code == 409, doublon.text
    assert doublon.json()["error"]["code"] == "conflict"


def test_validation_materialise_lots(api_client: TestClient, session_factory) -> None:
    """P1 bout en bout : créer → extraire 2 lots → valider → lots officiels."""
    org = _seed_org(session_factory)
    cree = api_client.post(
        "/api/v1/appels-a-proposition/",
        json={
            "organisation_id": str(org.id),
            "reference": "ULA-097-B",
            "titre": "RFP lots indépendants",
        },
    )
    appel_id = cree.json()["id"]

    api_client.post(
        f"/api/v1/appels-a-proposition/{appel_id}/extraction",
        json={
            "donnees_extraites": {
                "lots": [
                    {"numero": "1", "titre": "Lot 1 PBM"},
                    {"numero": "2", "titre": "Lot 2 PBM"},
                    {"numero": "3", "titre": "Lot 3 PVM"},
                    {"numero": "4", "titre": "Lot 4 PR"},
                ]
            },
            "proposed_by_agent": "agent_generateur_offre",
        },
    )
    validation = api_client.post(
        f"/api/v1/appels-a-proposition/{appel_id}/validation",
        json={"decision": "approuve", "reason": "lots distincts"},
    )
    assert validation.status_code == 200
    assert validation.json()["statut"] == StatutAppelAProposition.VALIDE.value

    lots = api_client.get(f"/api/v1/appels-a-proposition/{appel_id}/lots/")
    assert lots.status_code == 200
    numeros = sorted(item["numero"] for item in lots.json())
    assert numeros == ["1", "2", "3", "4"]


# --- Offres de formation ---------------------------------------------------------


def test_cycle_offre_complet(api_client: TestClient, session_factory) -> None:
    appel = _seed_appel(session_factory)
    lot = _seed_lot(session_factory, appel)

    created = _post_offre(api_client, lot, "OF-API-01", "Offre API")
    assert created.status_code == 201
    offre_id = created.json()["id"]
    assert created.json()["statut"] == "brouillon"

    revue = api_client.post(f"/api/v1/offres/{offre_id}/revue")
    assert revue.status_code == 200
    assert revue.json()["statut"] == "en_revue"

    published = api_client.post(
        f"/api/v1/offres/{offre_id}/publication",
        json={"decision": "approuve"},
    )
    assert published.status_code == 200
    assert published.json()["statut"] == "approuve"
    assert published.json()["approved_at"] is not None


def test_create_seconde_offre_meme_lot_409(
    api_client: TestClient, session_factory
) -> None:
    """Un lot porte au plus une offre de chaque type (409 sinon)."""
    appel = _seed_appel(session_factory)
    lot = _seed_lot(session_factory, appel)
    premier = _post_offre(api_client, lot, "OF-API-1A", "Offre A")
    assert premier.status_code == 201
    second = _post_offre(api_client, lot, "OF-API-1B", "Offre B")
    assert second.status_code == 409


def test_create_offre_autre_type_meme_lot_201(
    api_client: TestClient, session_factory
) -> None:
    """Un appel à proposition se répond par une technique **et** une financière."""
    appel = _seed_appel(session_factory)
    lot = _seed_lot(session_factory, appel)
    technique = _post_offre(api_client, lot, "OF-API-2T", "Offre technique")
    assert technique.status_code == 201
    financiere = _post_offre(
        api_client, lot, "OF-API-2F", "Offre financière", "offre_financiere"
    )
    assert financiere.status_code == 201
    assert financiere.json()["type"] == "offre_financiere"


def test_create_offre_lot_inexistant_404(api_client: TestClient) -> None:
    resp = api_client.post(
        "/api/v1/offres/",
        json={
            "lot_id": str(uuid4()),
            "reference": "OF-X",
            "titre": "Offre",
            "type": "offre_technique",
        },
    )
    assert resp.status_code == 404


def test_publication_sans_revue_409(api_client: TestClient, session_factory) -> None:
    appel = _seed_appel(session_factory)
    lot = _seed_lot(session_factory, appel)
    created = _post_offre(api_client, lot, "OF-API-02", "Offre")
    offre_id = created.json()["id"]

    resp = api_client.post(
        f"/api/v1/offres/{offre_id}/publication",
        json={"decision": "approuve"},
    )
    assert resp.status_code == 409


# --- Budgets : règle 7 à la frontière HTTP ----------------------------------------


def test_budget_total_recalcule(api_client: TestClient, session_factory) -> None:
    appel = _seed_appel(session_factory)
    lot = _seed_lot(session_factory, appel)
    created = _post_offre(api_client, lot, "OF-API-03", "Offre")
    offre_id = UUID(created.json()["id"])
    budget = Budget(offre_id=offre_id, devise="MAD")
    seed(session_factory, budget)

    l1 = api_client.post(
        f"/api/v1/offres/{offre_id}/budgets/{budget.id}/lignes",
        json={"categorie": "formation", "quantite": "3", "cout_unitaire": "1000"},
    )
    assert l1.status_code == 201
    assert Decimal(l1.json()["cout_total"]) == Decimal("3000.00")

    api_client.post(
        f"/api/v1/offres/{offre_id}/budgets/{budget.id}/lignes",
        json={"categorie": "logistique", "quantite": "2.5", "cout_unitaire": "200"},
    )

    total = api_client.get(f"/api/v1/offres/budgets/{budget.id}/total")
    assert total.status_code == 200
    assert Decimal(total.json()["total"]) == Decimal("3500.00")
    assert total.json()["nb_lignes"] == 2


def test_ligne_ignore_cout_total_fourni(api_client: TestClient, session_factory) -> None:
    """Règle 7 à la frontière : un cout_total envoyé par le client est ignoré."""
    appel = _seed_appel(session_factory)
    lot = _seed_lot(session_factory, appel)
    created = _post_offre(api_client, lot, "OF-API-04", "Offre")
    offre_id = UUID(created.json()["id"])
    budget = Budget(offre_id=offre_id)
    seed(session_factory, budget)

    resp = api_client.post(
        f"/api/v1/offres/{offre_id}/budgets/{budget.id}/lignes",
        json={
            "categorie": "x",
            "quantite": "2",
            "cout_unitaire": "10",
            "cout_total": "999999",
        },
    )
    assert resp.status_code == 201
    assert Decimal(resp.json()["cout_total"]) == Decimal("20.00")


def test_budget_brouillon_approbation_directe_409(
    api_client: TestClient, session_factory
) -> None:
    """Machine à états : brouillon → approuve interdit (passe par propose)."""
    appel = _seed_appel(session_factory)
    lot = _seed_lot(session_factory, appel)
    created = _post_offre(api_client, lot, "OF-API-05", "Offre")
    offre_id = UUID(created.json()["id"])
    budget = Budget(offre_id=offre_id)
    seed(session_factory, budget)

    resp = api_client.post(
        f"/api/v1/offres/budgets/{budget.id}/approbation",
        json={"decision": "approuve"},
    )
    assert resp.status_code == 409


# --- Affectations -----------------------------------------------------------------


def test_flux_affectation_complet(api_client: TestClient, session_factory) -> None:
    mission = _seed_mission(session_factory)
    membre = _seed_equipe(session_factory)

    propose = api_client.post(
        f"/api/v1/missions/{mission.id}/affectations",
        json={
            "mission_id": str(mission.id),
            "equipe_id": str(membre.id),
            "role_dans_mission": "formateur",
            "proposed_by": "AgentRH",
            "source": "ai_proposal",
        },
    )
    assert propose.status_code == 202
    assert propose.json()["statut"] == StatutAffectation.PROPOSEE.value
    affectation_id = propose.json()["id"]

    listing = api_client.get(f"/api/v1/missions/{mission.id}/affectations")
    assert listing.status_code == 200
    assert listing.json()["mission_id"] == str(mission.id)
    assert len(listing.json()["affectations"]) == 1

    approved = api_client.post(
        f"/api/v1/affectations/{affectation_id}/approbation",
        json={"decision": "approuve"},
    )
    assert approved.status_code == 200
    assert approved.json()["statut"] == StatutAffectation.APPROUVEE.value
    assert approved.json()["approved_by"]  # id utilisateur (lab JWT)


def test_affectation_sans_proposed_by_auteur_du_jeton(
    api_client: TestClient, session_factory
) -> None:
    """L'auteur vient du jeton, pas du corps — et son absence n'est plus un 422.

    Régression : ``proposed_by`` était un champ **requis** du schéma alors que la
    route l'ignorait ; tout appelant qui ne l'envoyait pas recevait un 422 que la
    UI affichait en « [object Object] » (incrément 9). Une valeur forgée par le
    client ne doit avoir aucun effet.
    """
    mission = _seed_mission(session_factory)
    membre = _seed_equipe(session_factory)

    propose = api_client.post(
        f"/api/v1/missions/{mission.id}/affectations",
        json={
            "mission_id": str(mission.id),
            "equipe_id": str(membre.id),
            "role_dans_mission": "formateur",
            "source": "manual",
            "proposed_by": "client-forge",
        },
    )
    assert propose.status_code == 202, propose.text

    with session_factory() as session:
        evenement = session.scalars(
            select(AuditEvent).where(
                AuditEvent.entity_id == propose.json()["id"],
                AuditEvent.action == ActionAudit.AFFECTATION_PROPOSEE.value,
            )
        ).one()
    auteur = evenement.event_metadata["proposed_by"]
    assert auteur != "client-forge"
    # uuid4() du mode lab (aucun AUTH_SECRET en test) : un identifiant réel.
    assert UUID(auteur)


def test_modifier_affectation_role_et_dates(api_client: TestClient, session_factory) -> None:
    """L'utilisateur ajuste une affectation : rôle et période, jamais le membre."""
    mission = _seed_mission(session_factory)
    membre = _seed_equipe(session_factory)
    propose = api_client.post(
        f"/api/v1/missions/{mission.id}/affectations",
        json={
            "mission_id": str(mission.id),
            "equipe_id": str(membre.id),
            "role_dans_mission": "formateur",
        },
    )
    affectation_id = propose.json()["id"]

    modifie = api_client.patch(
        f"/api/v1/affectations/{affectation_id}",
        json={"role_dans_mission": "chef_de_mission", "date_debut": "2026-10-01"},
    )
    assert modifie.status_code == 200, modifie.text
    assert modifie.json()["role_dans_mission"] == "chef_de_mission"
    assert modifie.json()["date_debut"] == "2026-10-01"

    # La modification est tracée (audit) avec l'état avant/après.
    with session_factory() as session:
        evenements = session.scalars(
            select(AuditEvent).where(
                AuditEvent.entity_id == affectation_id,
                AuditEvent.action == ActionAudit.AFFECTATION_MODIFIEE.value,
            )
        ).all()
    assert len(evenements) == 1
    assert evenements[0].before["role"] == "formateur"
    assert evenements[0].after["role"] == "chef_de_mission"


def test_modifier_affectation_membre_impossible(
    api_client: TestClient, session_factory
) -> None:
    """Changer de membre n'est pas une modification : le schéma refuse le champ."""
    mission = _seed_mission(session_factory)
    membre = _seed_equipe(session_factory)
    propose = api_client.post(
        f"/api/v1/missions/{mission.id}/affectations",
        json={
            "mission_id": str(mission.id),
            "equipe_id": str(membre.id),
            "role_dans_mission": "formateur",
        },
    )
    affectation_id = propose.json()["id"]
    autre = _seed_equipe(session_factory)

    modifie = api_client.patch(
        f"/api/v1/affectations/{affectation_id}",
        json={"equipe_id": str(autre.id), "role_dans_mission": "chef_de_mission"},
    )
    assert modifie.status_code == 422  # ``equipe_id`` hors schéma : extra interdit
    # Rien n'a bougé.
    listing = api_client.get(f"/api/v1/missions/{mission.id}/affectations")
    assert listing.json()["affectations"][0]["equipe_id"] == str(membre.id)
    assert listing.json()["affectations"][0]["role_dans_mission"] == "formateur"


def test_modifier_affectation_refusee_409(api_client: TestClient, session_factory) -> None:
    """Une affectation refusée est figée : la modifier la réécrirait après coup."""
    mission = _seed_mission(session_factory)
    membre = _seed_equipe(session_factory)
    propose = api_client.post(
        f"/api/v1/missions/{mission.id}/affectations",
        json={
            "mission_id": str(mission.id),
            "equipe_id": str(membre.id),
            "role_dans_mission": "formateur",
        },
    )
    affectation_id = propose.json()["id"]
    assert (
        api_client.post(
            f"/api/v1/affectations/{affectation_id}/refus",
            json={"decision": "rejete"},
        ).status_code
        == 200
    )

    modifie = api_client.patch(
        f"/api/v1/affectations/{affectation_id}",
        json={"role_dans_mission": "chef_de_mission"},
    )
    assert modifie.status_code == 409


def test_modifier_affectation_dates_incoherentes_422(
    api_client: TestClient, session_factory
) -> None:
    mission = _seed_mission(session_factory)
    membre = _seed_equipe(session_factory)
    propose = api_client.post(
        f"/api/v1/missions/{mission.id}/affectations",
        json={
            "mission_id": str(mission.id),
            "equipe_id": str(membre.id),
            "role_dans_mission": "formateur",
        },
    )
    affectation_id = propose.json()["id"]

    modifie = api_client.patch(
        f"/api/v1/affectations/{affectation_id}",
        json={"date_debut": "2026-11-01", "date_fin": "2026-10-01"},
    )
    assert modifie.status_code == 422
    assert "fin" in modifie.json()["error"]["message"].lower()


def test_mission_depuis_appel_201(api_client: TestClient, session_factory) -> None:
    """Création de mission depuis l'appel : organisation déduite, offres du lot."""
    appel = _seed_appel(session_factory)
    lot = _seed_lot(session_factory, appel)
    # Une offre technique créée puis soumise en revue et publiée (flux réel).
    created = _post_offre(api_client, lot, "OF-API-DT", "Offre technique du lot")
    assert created.status_code == 201, created.text
    offre_id = UUID(created.json()["id"])
    budget = Budget(offre_id=offre_id)
    seed(session_factory, budget)
    soumise = api_client.post(f"/api/v1/offres/{offre_id}/revue")
    assert soumise.status_code == 200, soumise.text
    publie = api_client.post(
        f"/api/v1/offres/{offre_id}/publication", json={"decision": "approuve"}
    )
    assert publie.status_code == 200, publie.text

    creee = api_client.post(
        "/api/v1/missions/depuis-appel",
        json={
            "appel_a_proposition_id": str(appel.id),
            "lot_id": str(lot.id),
            "reference": "M-API-DT-01",
            "titre": "Formation depuis l'appel",
        },
    )
    assert creee.status_code == 201, creee.text
    corps = creee.json()
    assert corps["organisation_id"] == str(appel.organisation_id)
    assert [o["reference"] for o in corps["offres"]] == ["OF-API-DT"]


def test_mission_depuis_appel_lot_hors_appel_422(
    api_client: TestClient, session_factory
) -> None:
    """Le lot doit appartenir à l'appel fourni — sinon 422 explicite."""
    appel = _seed_appel(session_factory)
    autre_appel = _seed_appel(session_factory)
    lot = _seed_lot(session_factory, autre_appel)

    creee = api_client.post(
        "/api/v1/missions/depuis-appel",
        json={
            "appel_a_proposition_id": str(appel.id),
            "lot_id": str(lot.id),
            "reference": "M-API-DT-02",
            "titre": "Mission",
        },
    )
    assert creee.status_code == 422
    assert "n'appartient pas" in creee.json()["error"]["message"]


def test_affectation_mission_incoherente_409(
    api_client: TestClient, session_factory
) -> None:
    mission = _seed_mission(session_factory)
    membre = _seed_equipe(session_factory)

    resp = api_client.post(
        f"/api/v1/missions/{uuid4()}/affectations",
        json={
            "mission_id": str(mission.id),
            "equipe_id": str(membre.id),
            "role_dans_mission": "formateur",
            "proposed_by": "x",
        },
    )
    assert resp.status_code == 409


def test_affectation_mission_cloturee_409(api_client: TestClient, session_factory) -> None:
    mission = _seed_mission(session_factory)
    mission.statut = StatutMission.CLOTUREE.value
    seed(session_factory, mission)
    membre = _seed_equipe(session_factory)

    resp = api_client.post(
        f"/api/v1/missions/{mission.id}/affectations",
        json={
            "mission_id": str(mission.id),
            "equipe_id": str(membre.id),
            "role_dans_mission": "formateur",
            "proposed_by": "x",
        },
    )
    assert resp.status_code == 409
