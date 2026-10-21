"""CORS configurable par l'environnement (CORS_ORIGINS).

Le préflight CORS est la porte d'entrée de **toute** requête navigateur : une
origine absente de l'allowlist fait échouer les formulaires en silence (le
navigateur annule le POST). Ces tests verrouillent le contrat :

- la valeur d'environnement ``CORS_ORIGINS`` (chaîne à virgules) est scindée ;
- le préflight d'une origine configurée passe et porte ``access-control-allow-origin`` ;
- le préflight d'une origine inconnue est refusé (400) — jamais élargi en silence ;
- les défauts couvrent le développement local (3000/3950).
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

import app.main as module_main
from app.core.config import Settings


def _app_avec(origins: list[str]) -> TestClient:
    """Construit l'application réelle avec une allowlist imposée."""
    sous = Settings(cors_origins=origins)
    monkeypatched = lambda: sous  # noqa: E731
    original = module_main.get_settings
    module_main.get_settings = monkeypatched
    try:
        return TestClient(module_main.create_app())
    finally:
        module_main.get_settings = original


def test_valeur_env_a_virgules_est_scindee() -> None:
    lues = Settings(cors_origins=" https://demo.trycloudflare.com , http://localhost:3000 ,")
    assert lues.cors_origins == ["https://demo.trycloudflare.com", "http://localhost:3000"]


def test_liste_directe_acceptee() -> None:
    lues = Settings(cors_origins=["https://carso.mg"])
    assert lues.cors_origins == ["https://carso.mg"]


def test_variable_environnement_reelle(monkeypatch: pytest.MonkeyPatch) -> None:
    """Le parcours ENV (le seul qui compte au démarrage) sans NoDecode levait
    SettingsError : pydantic-settings tente un ``json.loads`` sur les champs
    complexes AVANT tout validateur."""
    monkeypatch.setenv("CORS_ORIGINS", "https://mon-front.trycloudflare.com,http://localhost:3000")
    lues = Settings(_env_file=None)
    assert lues.cors_origins == [
        "https://mon-front.trycloudflare.com",
        "http://localhost:3000",
    ]


def test_defauts_developpement_local() -> None:
    lues = Settings()
    assert "http://localhost:3000" in lues.cors_origins
    assert "http://127.0.0.1:3950" in lues.cors_origins


def test_preflight_origine_configuree_passe() -> None:
    client = _app_avec(["https://demo.trycloudflare.com"])
    reponse = client.options(
        "/api/v1/auth/login",
        headers={
            "Origin": "https://demo.trycloudflare.com",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert reponse.status_code == 200
    assert reponse.headers["access-control-allow-origin"] == "https://demo.trycloudflare.com"
    assert reponse.headers["access-control-allow-credentials"] == "true"


@pytest.mark.parametrize(
    "origine",
    [
        "https://inconnue.trycloudflare.com",
        "http://evil.example.com",
    ],
)
def test_preflight_origine_inconnue_refusee(origine: str) -> None:
    client = _app_avec(["https://demo.trycloudflare.com"])
    reponse = client.options(
        "/api/v1/auth/login",
        headers={
            "Origin": origine,
            "Access-Control-Request-Method": "POST",
        },
    )
    assert reponse.status_code == 400
    assert "access-control-allow-origin" not in reponse.headers


def test_preflight_social_non_lus_repond_avant_toute_logique() -> None:
    """Le préflight du badge social ne traverse ni session ni initialisation.

    Incident de production : ``OPTIONS /api/v1/social/non-lus`` répondait 500
    (« application initialization timed out ») parce que l'application montait
    les agents—et ouvrait PostgreSQL—avant de servir la moindre requête.
    """
    origine = "https://carso-ai.onrender.com"
    client = _app_avec([origine])

    reponse = client.options(
        "/api/v1/social/non-lus",
        headers={
            "Origin": origine,
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization",
        },
    )

    assert reponse.status_code == 200
    assert reponse.headers["access-control-allow-origin"] == origine
    assert "authorization" in reponse.headers["access-control-allow-headers"].lower()


def test_avertit_quand_l_origine_du_frontend_est_absente(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Le piège n°1 (front changé, allowlist oubliée) est signalé au démarrage.

    Sans ce signal, le backend n'écrit rien : le préflight reçoit 400 et le
    navigateur annule le POST — le symptôme se cherche dans le tunnel.
    """
    from app.main import _signaler_origine_frontend_absente

    with caplog.at_level("WARNING"):
        _signaler_origine_frontend_absente(
            Settings(
                _env_file=None,
                cors_origins=["http://localhost:3000"],
                frontend_public_url="https://carso-ai.onrender.com",
            )
        )

    assert "CORS_ORIGINS" in caplog.text
    assert "https://carso-ai.onrender.com" in caplog.text


def test_app_par_defaut_autorise_localhost(monkeypatch: pytest.MonkeyPatch) -> None:
    """create_app() lit bien l'allowlist de la configuration (et pas une liste figée)."""
    monkeypatch.setattr(
        module_main, "get_settings", lambda: Settings(cors_origins=["http://localhost:3000"])
    )
    app = module_main.create_app()
    middleware: Any = next(
        m for m in app.user_middleware if m.cls.__name__ == "CORSMiddleware"
    )
    assert middleware.kwargs["allow_origins"] == ["http://localhost:3000"]
