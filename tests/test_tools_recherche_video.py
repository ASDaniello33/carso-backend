"""Tool ``rechercher_video`` — bornes, permission, réponse et échec dit.

Aucun appel réseau : ``moteurs_web.videos`` est remplacé par un substitut, ce qui
laisse le test porter sur ce que le tool **décide** (borne de configuration,
capacité exigée, forme de la réponse) et non sur le HTML de YouTube, éprouvé
dans ``test_moteurs_web.py``.
"""

from __future__ import annotations

from typing import Any

import pydantic
import pytest

from app.core.config import Settings
from app.core.errors import ValidationError
from app.tools import moteurs_web
from app.tools.permissions import AgentIdentity, PermissionPolicy
from app.tools.recherche_video import (
    RechercheVideoInput,
    attacher_recherche_video,
    build_recherche_video_tool,
)

IDENTITE = AgentIdentity(agent_id="agent_test")
POLICY_WEB = PermissionPolicy(allowed_capabilities=frozenset({"web_search"}))
POLICY_VIDE = PermissionPolicy(allowed_capabilities=frozenset())

VIDEO = {
    "titre": "Tuto FastAPI",
    "url": "https://www.youtube.com/watch?v=abc123DEF45",
    "vignette": "https://i.ytimg.com/1.jpg",
    "duree": "12:34",
    "auteur": "Chaîne CARSO",
    "date": "il y a 3 jours",
}


def _settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {"web_search_max_results": 5}
    base.update(overrides)
    return Settings(_env_file=None, **base)


def _tool(
    *, policy: PermissionPolicy = POLICY_WEB, settings: Settings | None = None
) -> Any:
    return build_recherche_video_tool(
        identite=IDENTITE, policy=policy, settings=settings or _settings()
    )


def test_permission_absente_refusee(monkeypatch: pytest.MonkeyPatch) -> None:
    appels: list[tuple[str, int]] = []
    monkeypatch.setattr(moteurs_web, "videos", lambda r, n: appels.append((r, n)) or [])

    sortie = _tool(policy=POLICY_VIDE).invoke({"requete": "formation"})

    assert sortie["ok"] is False
    assert sortie["error"]["code"] == "permission_denied"
    assert appels == [], "un refus de permission ne doit rien envoyer au réseau"


def test_la_requete_est_bornee() -> None:
    """Une requête trop courte, trop longue ou un plafond hors borne sont refusés."""
    with pytest.raises(pydantic.ValidationError):
        RechercheVideoInput(requete="x")
    with pytest.raises(pydantic.ValidationError):
        RechercheVideoInput(requete="a" * 400)
    with pytest.raises(pydantic.ValidationError):
        RechercheVideoInput(requete="tuto", max_resultats=99)


def test_le_plafond_de_configuration_serre_la_demande(monkeypatch: pytest.MonkeyPatch) -> None:
    demandes: list[int] = []

    def _videos(_requete: str, nb: int) -> list[dict[str, Any]]:
        demandes.append(nb)
        return [VIDEO]

    monkeypatch.setattr(moteurs_web, "videos", _videos)
    sortie = _tool(settings=_settings(web_search_max_results=5)).invoke(
        {"requete": "tuto fastapi", "max_resultats": 9}
    )

    assert demandes == [5], "la configuration serre la borne, jamais l'inverse"
    assert sortie["ok"] is True


def test_la_reponse_porte_la_source_et_les_champs_rendus(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(moteurs_web, "videos", lambda _requete, _nb: [dict(VIDEO)])
    sortie = _tool().invoke({"requete": "tuto fastapi"})

    assert sortie["requete"] == "tuto fastapi"
    assert sortie["nb_resultats"] == 1
    assert sortie["resultats"] == [VIDEO]
    assert sortie["sources_utilisees"] == ["youtube"]
    assert sortie["avertissements"] == []


def test_un_echec_du_fournisseur_est_dit(monkeypatch: pytest.MonkeyPatch) -> None:
    """Une erreur métier du fournisseur arrive à l'agent, jamais en traceback."""

    def _videos(_requete: str, _nb: int) -> list[dict[str, Any]]:
        raise ValidationError("YouTube n'a pas publié ses résultats")

    monkeypatch.setattr(moteurs_web, "videos", _videos)
    sortie = _tool().invoke({"requete": "tuto"})

    assert sortie["ok"] is False
    assert "YouTube" in sortie["error"]["message"]


def test_attache_ce_que_le_contrat_declare() -> None:
    class _Contrat:
        agent_id = "agent_test"
        policy = POLICY_WEB

    class _Avec(_Contrat):
        tools = ("langsearch_web_search", "rechercher_video")

    class _Sans(_Contrat):
        tools = ("langsearch_web_search",)

    class _Outil:
        name = "existante"

    deja = _Outil()
    attaches = attacher_recherche_video(_Avec(), (deja,), settings=_settings())
    assert [outil.name for outil in attaches] == ["existante", "rechercher_video"]
    assert attacher_recherche_video(_Sans(), (deja,), settings=_settings()) == (deja,)
