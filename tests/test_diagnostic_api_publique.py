"""Diagnostic de l'API publiée : classification sans réseau.

Le script sert à trancher entre trois causes qui produisent le même « CORS
error » dans la console du navigateur. Ces tests vérifient la classification
(page d'avertissement, allowlist, préflight, backend muet) et le verdict rendu
par ``diagnostiquer``, avec un transport factice.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

CHEMIN = Path(__file__).resolve().parents[1] / "scripts" / "diagnostic_api_publique.py"

HTML_AVERTISSEMENT = (
    "<!DOCTYPE html><html><head><title>Localtonet Warning</title></head>"
    "<body>You are about to navigate to: demo.localto.net</body></html>"
)


def _charger() -> ModuleType:
    specification = importlib.util.spec_from_file_location("diagnostic_api_publique", CHEMIN)
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    # ``dataclass`` (annotations différées) résout son module via sys.modules.
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


diagnostic = _charger()


def _page() -> object:
    return diagnostic.Reponse(
        statut=200, type_contenu="text/html; charset=utf-8", corps=HTML_AVERTISSEMENT
    )


def _json() -> object:
    return diagnostic.Reponse(statut=200, type_contenu="application/json", corps='{"status":"ok"}')


def test_page_avertissement_reconnue() -> None:
    assert diagnostic.est_interstitiel(_page()) is True


def test_json_n_est_pas_une_page_avertissement() -> None:
    assert diagnostic.est_interstitiel(_json()) is False


@pytest.mark.parametrize(
    ("reponse", "attendu"),
    [
        (diagnostic.Reponse(statut=0), "injoignable"),
        (diagnostic.Reponse(statut=503, corps="init"), "erreur_serveur"),
        (diagnostic.Reponse(statut=404, corps=""), "inattendu"),
        (_page(), "interstitiel"),
        (diagnostic.Reponse(statut=200, type_contenu="text/plain", corps="OK"), "non_json"),
        (_json(), "ok"),
    ],
)
def test_classification_de_la_sante(reponse: object, attendu: str) -> None:
    assert diagnostic.classer_sante(reponse) == attendu


@pytest.mark.parametrize(
    ("reponse", "attendu"),
    [
        (diagnostic.Reponse(statut=0), "injoignable"),
        (_page(), "interstitiel"),
        (diagnostic.Reponse(statut=400, corps="Disallowed CORS origin"), "origine_refusee"),
        (diagnostic.Reponse(statut=200, corps="OK"), "sans_cors"),
        (
            diagnostic.Reponse(
                statut=200,
                corps="OK",
                entetes={"access-control-allow-origin": "https://carso-ai.onrender.com"},
            ),
            "ok",
        ),
    ],
)
def test_classification_du_preflight(reponse: object, attendu: str) -> None:
    assert diagnostic.classer_preflight(reponse, "https://carso-ai.onrender.com") == attendu


@pytest.mark.parametrize(
    "saisie",
    [
        "https://demo.localto.net",
        "https://demo.localto.net/",
        "https://demo.localto.net/api/v1",
        "https://demo.localto.net/api/v1/",
    ],
)
def test_normalisation_de_l_adresse(saisie: str) -> None:
    assert diagnostic.normaliser_base(saisie) == "https://demo.localto.net"


def test_verdict_tunnel_signale_la_page_et_le_backend_sain(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """La page d'avertissement est la cause ; le backend, lui, répond."""

    def transport(methode: str, url: str, entetes: object = None) -> object:
        return _json() if entetes and "localtonet-skip-warning" in entetes else _page()

    monkeypatch.setattr(diagnostic, "executer", transport)

    code = diagnostic.diagnostiquer(
        "https://demo.localto.net/api/v1", "https://carso-ai.onrender.com"
    )

    sortie = capsys.readouterr().out
    assert code == 2
    assert "page d'avertissement" in sortie
    assert "SEULE cause" in sortie


def test_verdict_origine_absente_de_l_allowlist(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def transport(methode: str, url: str, entetes: object = None) -> object:
        if methode == "GET":
            return _json()
        return diagnostic.Reponse(statut=400, corps="Disallowed CORS origin")

    monkeypatch.setattr(diagnostic, "executer", transport)

    code = diagnostic.diagnostiquer(
        "https://demo.europe.example/api/v1", "https://carso-ai.onrender.com"
    )

    assert code == 3
    assert "CORS_ORIGINS" in capsys.readouterr().out


def test_verdict_sain_quand_preflight_et_allowlist_sont_corrects(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def transport(methode: str, url: str, entetes: object = None) -> object:
        if methode == "GET":
            return _json()
        origine = (entetes or {}).get("Origin")
        if origine == "https://carso-ai.onrender.com":
            return diagnostic.Reponse(
                statut=200,
                corps="OK",
                entetes={"access-control-allow-origin": origine},
            )
        return diagnostic.Reponse(statut=400, corps="Disallowed CORS origin")

    monkeypatch.setattr(diagnostic, "executer", transport)

    code = diagnostic.diagnostiquer(
        "https://demo.europe.example/api/v1", "https://carso-ai.onrender.com"
    )

    assert code == 0
    assert "allowlist active" in capsys.readouterr().out
