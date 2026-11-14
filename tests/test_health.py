"""Tests for the health endpoint."""

from fastapi.testclient import TestClient


def test_health_returns_ok(client: TestClient) -> None:
    response = client.get("/api/v1/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["app"] == "CARSO AI"


def test_health_annonce_le_plafond_de_depot(client: TestClient) -> None:
    """Le plafond de dépôt est exposé : **source unique** du plafond frontend.

    Sans cette valeur, l'interface retombait sur un nombre codé en dur (20 Mo)
    et bloquait un fichier que le serveur acceptait. Le champ doit valoir
    exactement le réglage appliqué au stockage — jamais une seconde valeur.
    """
    from app.core.config import get_settings

    body = client.get("/api/v1/health").json()

    assert body["max_upload_bytes"] == get_settings().max_upload_bytes
    assert body["max_upload_bytes"] > 0


def test_openapi_is_served_under_v1(client: TestClient) -> None:
    response = client.get("/api/v1/openapi.json")

    assert response.status_code == 200
    assert "/api/v1/health" in response.json()["paths"]
