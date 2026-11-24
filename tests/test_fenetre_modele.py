"""Fenêtre d'entrée du modèle : lue chez le fournisseur, jamais devinée.

`/models` d'une passerelle compatible OpenAI peut publier ``context_length``
(constaté le 15/11 sur tokenharbor : ``deepseek-v4.1-flash:free`` → 1 048 576,
``qwen3.8-27b`` → 1 000 000, ``deepseek-v3.2`` → 128 000). Ce chiffre a deux
emplois : il donne un sens aux seuils **en fraction** du résumé d'historique, et
il borne la coupe de queue au-delà du budget d'entrée.

Ce qui est verrouillé ici :

- la valeur publiée est utilisée, et ``AGENT_MAX_INPUT_TOKENS`` n'est **pas** un
  choix de modèle imposé à tous ;
- un fournisseur qui ne publie rien, ou une lecture impossible, ne casse pas la
  construction du modèle : il retombe sur le repli **en le disant** ;
- **seul le définitif est mémorisé** : un échec réseau n'est pas figé pour la vie
  du processus, et le journal ne dit jamais « non publiée » pour une lecture
  qui n'a pas abouti.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
import pytest

from app.agents.providers import (
    PROVENANCE_MANUELLE,
    PROVENANCE_NON_LUE,
    PROVENANCE_NON_PUBLIEE,
    PROVENANCE_PUBLIEE,
    _FENETRES_PUBLIEES,
    _fenetre_publiee,
    _profil_modele,
    build_chat_model,
    fenetre_effective,
)
from app.core.config import Settings

REPLI = 200_000


def _reglages(modele: str = "modele-x") -> Settings:
    return Settings(
        agent_model=modele,
        agent_base_url="http://localhost:9/v1",
        agent_api_key="cle-de-test",  # type: ignore[arg-type]
        agent_ssl_verify=False,
        agent_max_input_tokens=REPLI,
    )


class _Reponse:
    """Réponse ``/models`` minimale, sans réseau."""

    def __init__(self, modeles: list[dict[str, Any]]) -> None:
        self._modeles = modeles

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return {"data": self._modeles}


class _ErreurHTTP:
    """Réponse en erreur, pour ``raise_for_status``."""

    def __init__(self, statut: int) -> None:
        self.status_code = statut


def _http(statut: int) -> Any:
    def _lever(*_a: Any, **_k: Any) -> Any:
        raise httpx.HTTPStatusError(
            f"HTTP {statut}",
            request=httpx.Request("GET", "http://localhost:9/v1/models"),
            response=_ErreurHTTP(statut),  # type: ignore[arg-type]
        )

    return _lever


@pytest.fixture(autouse=True)
def _cache_vide():
    _FENETRES_PUBLIEES.clear()
    yield
    _FENETRES_PUBLIEES.clear()


def test_la_fenetre_publiee_est_utilisee(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "httpx.get",
        lambda *a, **k: _Reponse(
            [
                {"id": "autre", "context_length": 999},
                {"id": "modele-x", "context_length": 1_048_576},
            ]
        ),
    )

    assert _fenetre_publiee(_reglages(), "modele-x").valeur == 1_048_576
    assert _profil_modele(_reglages(), "modele-x") == {"max_input_tokens": 1_048_576}


def test_une_fenetre_null_est_definitive_et_retombe_sur_le_repli(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """``context_length: null`` = « non publiée », et c'est une réponse ferme."""
    monkeypatch.setattr(
        "httpx.get", lambda *a, **k: _Reponse([{"id": "modele-x", "context_length": None}])
    )
    reglages = _reglages()

    assert _fenetre_publiee(reglages, "modele-x").definitive is True
    assert _FENETRES_PUBLIEES == {("http://localhost:9/v1", "modele-x"): None}

    with caplog.at_level(logging.WARNING):
        profil = _profil_modele(reglages, "modele-x")

    assert profil == {"max_input_tokens": REPLI}
    assert any("non publiée" in r.message for r in caplog.records)
    assert not any("NON LUE" in r.message for r in caplog.records)


def test_un_echec_reseau_n_est_pas_memorise(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Le cœur du correctif : un blip ne doit pas figer le repli.

    Mémoïser l'échec ferait retomber le modèle sur 200 000 jusqu'au prochain
    redémarrage, et le journal annoncerait « non publiée » alors que personne
    n'a pu demander au fournisseur.
    """
    appels: list[str] = []

    def _blip(*_a: Any, **_k: Any) -> Any:
        appels.append("appel")
        raise ConnectionError("blip transitoire")

    monkeypatch.setattr("httpx.get", _blip)
    reglages = _reglages()

    lecture = _fenetre_publiee(reglages, "modele-x")
    assert lecture.valeur is None
    assert lecture.definitive is False
    assert _FENETRES_PUBLIEES == {}  # rien de mémorisé

    # Le réseau revient : la lecture est bien retentée et aboutit.
    monkeypatch.setattr(
        "httpx.get",
        lambda *a, **k: _Reponse([{"id": "modele-x", "context_length": 1_048_576}]),
    )
    assert _fenetre_publiee(reglages, "modele-x").valeur == 1_048_576
    assert len(appels) == 1


def test_la_lecture_impossible_le_dit_sans_parler_de_publication(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Le journal distingue « non publiée » (réponse ferme) de « non lue »."""
    monkeypatch.setattr(
        "httpx.get",
        lambda *a, **k: (_ for _ in ()).throw(TimeoutError("délai dépassé")),
    )

    with caplog.at_level(logging.WARNING):
        profil = _profil_modele(_reglages(), "modele-x")

    assert profil == {"max_input_tokens": REPLI}
    messages = " ".join(r.message for r in caplog.records)
    assert "NON LUE" in messages
    assert "TimeoutError" in messages
    assert "non publiée" not in messages


def test_une_erreur_http_non_definitive_n_est_pas_memorisee(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """500 / 429 : c'est un incident du moment, on retentera."""
    monkeypatch.setattr("httpx.get", _http(503))

    lecture = _fenetre_publiee(_reglages(), "modele-x")

    assert lecture.definitive is False
    assert lecture.raison == "HTTP 503"
    assert _FENETRES_PUBLIEES == {}


def test_un_endpoint_models_absent_est_definitif(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """404/405 : le fournisseur n'expose pas ce chemin — le retenter ne sert à rien."""
    for statut in (404, 405, 501):
        _FENETRES_PUBLIEES.clear()
        monkeypatch.setattr("httpx.get", _http(statut))

        lecture = _fenetre_publiee(_reglages(), "modele-x")

        assert lecture.definitive is True, statut
        assert str(statut) in (lecture.raison or "")
        assert _FENETRES_PUBLIEES == {("http://localhost:9/v1", "modele-x"): None}


def test_un_modele_absent_de_la_liste_n_est_pas_memorise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Une liste peut être partielle : ce n'est pas une réponse définitive."""
    monkeypatch.setattr(
        "httpx.get", lambda *a, **k: _Reponse([{"id": "un-autre", "context_length": 1000}])
    )

    lecture = _fenetre_publiee(_reglages(), "modele-x")

    assert lecture.definitive is False
    assert "absent de la liste" in (lecture.raison or "")
    assert _FENETRES_PUBLIEES == {}


def test_un_fournisseur_muet_ne_casse_pas_la_construction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _echoue(*_a: Any, **_k: Any) -> Any:
        raise ConnectionError("fournisseur muet")

    monkeypatch.setattr("httpx.get", _echoue)
    modele = build_chat_model(_reglages())

    assert modele.profile == {"max_input_tokens": REPLI}


def test_une_valeur_definitive_est_memorisee(monkeypatch: pytest.MonkeyPatch) -> None:
    """Une valeur publiée, elle, ne se relit pas (une par modèle et par processus)."""
    appels: list[str] = []

    def _compte(url: str, **_k: Any) -> Any:
        appels.append(url)
        return _Reponse([{"id": "modele-x", "context_length": 500_000}])

    monkeypatch.setattr("httpx.get", _compte)
    reglages = _reglages()

    assert _fenetre_publiee(reglages, "modele-x").valeur == 500_000
    assert _fenetre_publiee(reglages, "modele-x").valeur == 500_000
    assert len(appels) == 1

    # Deux modèles distincts = deux lectures (la fenêtre est une propriété du
    # modèle, pas de la passerelle).
    assert _fenetre_publiee(reglages, "modele-y").definitive is False
    assert len(appels) == 2


def test_la_cle_n_est_jamais_journalisee(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr("httpx.get", lambda *a, **k: _Reponse([]))
    reglages = _reglages()

    with caplog.at_level(logging.DEBUG):
        _fenetre_publiee(reglages, "modele-x")

    assert "cle-de-test" not in caplog.text


# --- fenetre_effective : ce que Paramètres affiche -------------------------------


def test_fenetre_effective_publiee_et_seuils(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """La valeur publiée pilote les seuils affichés (75 % / 10 % de la fenêtre)."""
    monkeypatch.setattr(
        "httpx.get",
        lambda *a, **k: _Reponse([{"id": "modele-x", "context_length": 1_048_576}]),
    )
    reglages = _reglages().model_copy(
        update={
            "agent_resume_historique_fraction": 0.75,
            "agent_resume_historique_fraction_gardee": 0.10,
        }
    )

    fenetre = fenetre_effective(reglages, "modele-x")

    assert fenetre["jetons"] == 1_048_576
    assert fenetre["provenance"] == PROVENANCE_PUBLIEE
    assert fenetre["raison"] is None
    assert fenetre["seuil_resume"] == int(1_048_576 * 0.75)
    assert fenetre["garde_resume"] == int(1_048_576 * 0.10)


def test_fenetre_effective_non_publiee_montre_le_repli(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Le chiffre affiché est le repli, et la provenance le dit."""
    monkeypatch.setattr(
        "httpx.get", lambda *a, **k: _Reponse([{"id": "modele-x", "context_length": None}])
    )

    fenetre = fenetre_effective(_reglages(), "modele-x")

    assert fenetre["provenance"] == PROVENANCE_NON_PUBLIEE
    assert fenetre["jetons"] == REPLI
    assert fenetre["seuil_resume"] == int(REPLI * 0.75)


def test_fenetre_effective_non_lue_se_distingue_de_non_publiee(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Une lecture impossible ne doit pas s'afficher comme une absence annoncée."""
    monkeypatch.setattr(
        "httpx.get", lambda *a, **k: (_ for _ in ()).throw(ConnectionError("blip"))
    )

    fenetre = fenetre_effective(_reglages(), "modele-x")

    assert fenetre["provenance"] == PROVENANCE_NON_LUE
    assert fenetre["jetons"] == REPLI
    assert fenetre["raison"] == "ConnectionError"


def test_fenetre_manuelle_quand_le_fournisseur_ne_publie_rien(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """La valeur déclarée dans Paramètres comble l'absence — et se dit manuelle."""
    monkeypatch.setattr(
        "httpx.get", lambda *a, **k: _Reponse([{"id": "modele-x", "context_length": None}])
    )
    reglages = _reglages().model_copy(update={"agent_fenetre_contexte_manuelle": 128_000})

    fenetre = fenetre_effective(reglages, "modele-x")

    assert fenetre["provenance"] == PROVENANCE_MANUELLE
    assert fenetre["jetons"] == 128_000
    assert fenetre["seuil_resume"] == int(128_000 * 0.75)
    assert fenetre["garde_resume"] == int(128_000 * 0.10)
    assert "Paramètres" in (fenetre["raison"] or "")
    assert _profil_modele(reglages, "modele-x") == {"max_input_tokens": 128_000}


def test_la_fenetre_publiee_prime_sur_la_valeur_manuelle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Une donnée annoncée par le fournisseur ne se contredit pas à la main."""
    monkeypatch.setattr(
        "httpx.get",
        lambda *a, **k: _Reponse([{"id": "modele-x", "context_length": 1_048_576}]),
    )
    reglages = _reglages().model_copy(update={"agent_fenetre_contexte_manuelle": 128_000})

    fenetre = fenetre_effective(reglages, "modele-x")

    assert fenetre["provenance"] == PROVENANCE_PUBLIEE
    assert fenetre["jetons"] == 1_048_576
    assert _profil_modele(reglages, "modele-x") == {"max_input_tokens": 1_048_576}


def test_fenetre_manuelle_couvre_une_lecture_impossible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Lecture en échec = pas de valeur publiée : la déclaration s'applique."""
    monkeypatch.setattr(
        "httpx.get", lambda *a, **k: (_ for _ in ()).throw(ConnectionError("blip"))
    )
    reglages = _reglages().model_copy(update={"agent_fenetre_contexte_manuelle": 64_000})

    fenetre = fenetre_effective(reglages, "modele-x")

    assert fenetre["provenance"] == PROVENANCE_MANUELLE
    assert fenetre["jetons"] == 64_000
    assert "ConnectionError" in (fenetre["raison"] or "")
    assert _profil_modele(reglages, "modele-x") == {"max_input_tokens": 64_000}


def test_sans_fenetre_manuelle_le_repli_reste_200k(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Comportement d'avant préservé : rien de déclaré ⇒ 200 000 jetons."""
    monkeypatch.setattr(
        "httpx.get", lambda *a, **k: _Reponse([{"id": "modele-x", "context_length": None}])
    )

    fenetre = fenetre_effective(_reglages(), "modele-x")

    assert fenetre["provenance"] == PROVENANCE_NON_PUBLIEE
    assert fenetre["jetons"] == REPLI


def test_fenetre_effective_suit_les_fractions_configurees(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Changer la fraction change le seuil affiché — pas de valeur figée."""
    monkeypatch.setattr(
        "httpx.get",
        lambda *a, **k: _Reponse([{"id": "modele-x", "context_length": 1_000_000}]),
    )
    reglages = _reglages().model_copy(update={"agent_resume_historique_fraction": 0.5})

    assert fenetre_effective(reglages, "modele-x")["seuil_resume"] == 500_000
