"""Budgets : chaîne complète (INC-111).

Les tables ``budgets`` / ``lignes_budget`` existaient depuis la Phase 2, mais
**aucun code ne créait de budget** : les routes de ligne, d'approbation et de
total étaient inatteignables (aucun ``budget_id`` n'existait), et aucune
interface ne les mentionnait.

Ces tests exercent le parcours réel par HTTP :

```text
créer (offre financière) → lignes → soumettre → approuver
                                    ↓ reprise
                          budget de la mission (brouillon)
```

Règle 7 vérifiée à la frontière : aucun montant calculé n'est accepté d'un
client, ``cout_total`` et le total sont recalculés côté serveur.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.domain.execution import Mission
from app.domain.organization import Appel, Lot, Organisation
from tests.conftest import seed

PRÉFIXE = "/api/v1"


def _organisation(factory: sessionmaker[Session]) -> Organisation:
    org = Organisation(nom=f"Client Budget {uuid4().hex[:6]}")
    seed(factory, org)
    return org


def _appel(factory: sessionmaker[Session], org: Organisation) -> Appel:
    appel = Appel(
        organisation_id=org.id,
        reference=f"AO-BUD-{uuid4().hex[:8]}",
        titre="Formation budget",
    )
    seed(factory, appel)
    return appel


def _lot(factory: sessionmaker[Session], appel: Appel) -> Lot:
    lot = Lot(appel_id=appel.id, numero="1", titre="Lot budget")
    seed(factory, lot)
    return lot


def _offre_financiere(api_client: TestClient, lot: Lot) -> UUID:
    """Offre financière créée par l'API (seule porteuse d'un budget)."""
    reponse = api_client.post(
        f"{PRÉFIXE}/offres",
        json={
            "lot_id": str(lot.id),
            "reference": f"OF-FIN-{uuid4().hex[:6]}",
            "titre": "Offre financière",
            "type": "offre_financiere",
        },
    )
    assert reponse.status_code == 201, reponse.text
    return UUID(reponse.json()["id"])


def _budget(api_client: TestClient, offre_id: UUID) -> dict[str, object]:
    reponse = api_client.post(
        f"{PRÉFIXE}/budgets", json={"offre_id": str(offre_id), "devise": "MAD"}
    )
    assert reponse.status_code == 201, reponse.text
    return reponse.json()


def _ligne(api_client: TestClient, budget_id: str, quantite: str, cout: str) -> dict[str, object]:
    reponse = api_client.post(
        f"{PRÉFIXE}/budgets/{budget_id}/lignes",
        json={
            "categorie": "formation",
            "quantite": quantite,
            "cout_unitaire": cout,
            "description": "Session de formation",
            "unite": "jour",
        },
    )
    assert reponse.status_code == 201, reponse.text
    return reponse.json()


def _approuve(api_client: TestClient, budget_id: str) -> None:
    soumission = api_client.post(f"{PRÉFIXE}/budgets/{budget_id}/soumission")
    assert soumission.status_code == 200, soumission.text
    decision = api_client.post(
        f"{PRÉFIXE}/budgets/{budget_id}/approbation",
        json={"decision": "approuve", "reason": "Validé direction"},
    )
    assert decision.status_code == 200, decision.text
    assert decision.json()["statut"] == "approuve"


def _mission(factory: sessionmaker[Session], org: Organisation) -> Mission:
    mission = Mission(
        organisation_id=org.id,
        reference=f"MIS-BUD-{uuid4().hex[:8]}",
        titre="Mission budget",
    )
    seed(factory, mission)
    return mission


@pytest.fixture
def contexte(api_client: TestClient, api_session_factory: sessionmaker[Session]):
    """Organisation, appel, lot et offre financière prêts à budgéter."""
    org = _organisation(api_session_factory)
    appel = _appel(api_session_factory, org)
    lot = _lot(api_session_factory, appel)
    offre_id = _offre_financiere(api_client, lot)
    return {"org": org, "offre_id": offre_id, "factory": api_session_factory}


def test_creer_budget_et_calculer_le_total(api_client: TestClient, contexte) -> None:
    """Création → lignes → total recalculé (aucun montant fourni)."""
    budget = _budget(api_client, contexte["offre_id"])
    assert budget["statut"] == "brouillon"
    assert budget["version"] == 1
    assert budget["offre_id"] == str(contexte["offre_id"])
    assert Decimal(budget["total"]) == Decimal("0.00")

    premiere = _ligne(api_client, budget["budget_id"], "3", "1000")
    assert Decimal(premiere["cout_total"]) == Decimal("3000.00")
    _ligne(api_client, budget["budget_id"], "2.5", "200")

    detail = api_client.get(f"{PRÉFIXE}/budgets/{budget['budget_id']}")
    assert detail.status_code == 200
    assert Decimal(detail.json()["total"]) == Decimal("3500.00")
    assert len(detail.json()["lignes"]) == 2

    liste = api_client.get(f"{PRÉFIXE}/budgets", params={"offre_id": str(contexte["offre_id"])})
    assert liste.status_code == 200
    assert liste.json()[0]["nb_lignes"] == 2


def test_cout_total_envoye_par_le_client_est_ignore(api_client: TestClient, contexte) -> None:
    """Règle 7 à la frontière : le montant calculé vient du serveur, jamais du client."""
    budget = _budget(api_client, contexte["offre_id"])
    reponse = api_client.post(
        f"{PRÉFIXE}/budgets/{budget['budget_id']}/lignes",
        json={
            "categorie": "logistique",
            "quantite": "2",
            "cout_unitaire": "150",
            "cout_total": "999999",
        },
    )
    assert reponse.status_code == 201
    assert Decimal(reponse.json()["cout_total"]) == Decimal("300.00")


def test_budget_vide_ne_se_soumet_pas(api_client: TestClient, contexte) -> None:
    budget = _budget(api_client, contexte["offre_id"])
    refus = api_client.post(f"{PRÉFIXE}/budgets/{budget['budget_id']}/soumission")
    assert refus.status_code == 422
    assert "ligne" in refus.json()["error"]["message"]


def test_soumettre_puis_approuver(api_client: TestClient, contexte) -> None:
    budget = _budget(api_client, contexte["offre_id"])
    _ligne(api_client, budget["budget_id"], "1", "500")

    soumission = api_client.post(f"{PRÉFIXE}/budgets/{budget['budget_id']}/soumission")
    assert soumission.status_code == 200
    assert soumission.json()["statut"] == "propose"

    decision = api_client.post(
        f"{PRÉFIXE}/budgets/{budget['budget_id']}/approbation",
        json={"decision": "approuve", "reason": "Validé direction"},
    )
    assert decision.status_code == 200
    assert decision.json()["statut"] == "approuve"


def test_approbation_directe_depuis_brouillon_refusee(api_client: TestClient, contexte) -> None:
    budget = _budget(api_client, contexte["offre_id"])
    _ligne(api_client, budget["budget_id"], "1", "500")
    refus = api_client.post(
        f"{PRÉFIXE}/budgets/{budget['budget_id']}/approbation",
        json={"decision": "approuve", "reason": "trop tôt"},
    )
    assert refus.status_code == 409


def test_budget_approuve_verrouille_les_lignes(api_client: TestClient, contexte) -> None:
    budget = _budget(api_client, contexte["offre_id"])
    ligne = _ligne(api_client, budget["budget_id"], "1", "500")
    _approuve(api_client, budget["budget_id"])

    ajout = api_client.post(
        f"{PRÉFIXE}/budgets/{budget['budget_id']}/lignes",
        json={"categorie": "divers", "quantite": "1", "cout_unitaire": "10"},
    )
    assert ajout.status_code == 409
    modification = api_client.patch(
        f"{PRÉFIXE}/budgets/{budget['budget_id']}/lignes/{ligne['id']}",
        json={"categorie": "formation", "quantite": "2", "cout_unitaire": "500"},
    )
    assert modification.status_code == 409


def test_nouvelle_version_recopie_les_lignes(api_client: TestClient, contexte) -> None:
    budget = _budget(api_client, contexte["offre_id"])
    _ligne(api_client, budget["budget_id"], "1", "500")
    _approuve(api_client, budget["budget_id"])

    version = api_client.post(f"{PRÉFIXE}/budgets/{budget['budget_id']}/version")
    assert version.status_code == 201, version.text
    assert version.json()["version"] == 2
    assert version.json()["statut"] == "brouillon"
    assert Decimal(version.json()["total"]) == Decimal("500.00")
    assert len(version.json()["lignes"]) == 1

    # L'ancienne version reste lisible et approuvée.
    ancienne = api_client.get(f"{PRÉFIXE}/budgets/{budget['budget_id']}")
    assert ancienne.json()["statut"] == "approuve"
    assert ancienne.json()["version"] == 1


def test_version_refusee_si_non_approuve(api_client: TestClient, contexte) -> None:
    budget = _budget(api_client, contexte["offre_id"])
    _ligne(api_client, budget["budget_id"], "1", "500")
    refus = api_client.post(f"{PRÉFIXE}/budgets/{budget['budget_id']}/version")
    assert refus.status_code == 409


def test_reprise_sur_mission(api_client: TestClient, contexte) -> None:
    """Le budget d'offre approuvé alimente le budget d'exécution de la mission."""
    budget = _budget(api_client, contexte["offre_id"])
    _ligne(api_client, budget["budget_id"], "3", "1000")
    _approuve(api_client, budget["budget_id"])
    mission = _mission(contexte["factory"], contexte["org"])

    reprise = api_client.post(
        f"{PRÉFIXE}/budgets/{budget['budget_id']}/reprise",
        json={"mission_id": str(mission.id)},
    )
    assert reprise.status_code == 201, reprise.text
    assert reprise.json()["mission_id"] == str(mission.id)
    assert reprise.json()["offre_id"] is None
    assert reprise.json()["statut"] == "brouillon"
    assert reprise.json()["version"] == 1
    assert Decimal(reprise.json()["total"]) == Decimal("3000.00")

    # Une seule reprise par mission.
    encore = api_client.post(
        f"{PRÉFIXE}/budgets/{budget['budget_id']}/reprise",
        json={"mission_id": str(mission.id)},
    )
    assert encore.status_code == 409

    liste = api_client.get(
        f"{PRÉFIXE}/budgets", params={"mission_id": str(mission.id)}
    )
    assert liste.status_code == 200
    assert len(liste.json()) == 1


def test_reprise_refusee_si_budget_non_approuve(api_client: TestClient, contexte) -> None:
    budget = _budget(api_client, contexte["offre_id"])
    _ligne(api_client, budget["budget_id"], "1", "100")
    mission = _mission(contexte["factory"], contexte["org"])
    refus = api_client.post(
        f"{PRÉFIXE}/budgets/{budget['budget_id']}/reprise",
        json={"mission_id": str(mission.id)},
    )
    assert refus.status_code == 409


def test_budget_uniquement_sur_offre_financiere(
    api_client: TestClient, api_session_factory: sessionmaker[Session]
) -> None:
    """Une offre technique ne porte pas de budget (règle validée 04/11)."""
    org = _organisation(api_session_factory)
    appel = _appel(api_session_factory, org)
    lot = _lot(api_session_factory, appel)
    technique = api_client.post(
        f"{PRÉFIXE}/offres",
        json={
            "lot_id": str(lot.id),
            "reference": f"OF-TECH-{uuid4().hex[:6]}",
            "titre": "Offre technique",
            "type": "offre_technique",
        },
    )
    assert technique.status_code == 201, technique.text
    refus = api_client.post(
        f"{PRÉFIXE}/budgets", json={"offre_id": technique.json()["id"]}
    )
    assert refus.status_code == 422
    assert "financière" in refus.json()["error"]["message"]


def test_budget_sans_porteur_refuse(api_client: TestClient) -> None:
    refus = api_client.post(f"{PRÉFIXE}/budgets", json={})
    assert refus.status_code == 422


def test_un_seul_budget_par_offre(api_client: TestClient, contexte) -> None:
    _budget(api_client, contexte["offre_id"])
    # Deux budgets pour la même offre créeraient deux chiffrages concurrents.
    from app.application.services.budget_service import BudgetService

    session = contexte["factory"]()
    try:
        with pytest.raises(Exception) as erreur:
            BudgetService(session, actor_id="test").creer_budget(
                offre_id=contexte["offre_id"]
            )
        assert "existe déjà" in str(erreur.value)
    finally:
        session.close()


def test_export_xlsx_et_docx(api_client: TestClient, contexte) -> None:
    budget = _budget(api_client, contexte["offre_id"])
    _ligne(api_client, budget["budget_id"], "2", "250")

    xlsx = api_client.get(
        f"{PRÉFIXE}/budgets/{budget['budget_id']}/export", params={"format": "xlsx"}
    )
    assert xlsx.status_code == 200
    assert xlsx.content[:2] == b"PK"  # conteneur OOXML
    assert "spreadsheetml" in xlsx.headers["content-type"]

    docx = api_client.get(
        f"{PRÉFIXE}/budgets/{budget['budget_id']}/export", params={"format": "docx"}
    )
    assert docx.status_code == 200
    assert docx.content[:2] == b"PK"
    assert "wordprocessingml" in docx.headers["content-type"]

    inconnu = api_client.get(
        f"{PRÉFIXE}/budgets/{budget['budget_id']}/export", params={"format": "pdf"}
    )
    assert inconnu.status_code == 422


def test_liste_exige_un_porteur(api_client: TestClient) -> None:
    refus = api_client.get(f"{PRÉFIXE}/budgets")
    assert refus.status_code == 422


def test_budget_inexistant_404(api_client: TestClient) -> None:
    assert api_client.get(f"{PRÉFIXE}/budgets/{uuid4()}").status_code == 404
