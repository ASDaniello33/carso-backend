"""Repli de modèle : une panne du fournisseur ne termine plus le run en erreur.

Constat du 25/11 : trois tours de modèle (49 s, 120 s, 269 s) puis ``APIError`` —
sept minutes d'attente pour un ``RUN_ERROR``. Les mêmes pannes reviennent depuis
le 10/11 (503, 429, ``Service temporarily unavailable``).

Ce qui est verrouillé ici :

- une panne **du fournisseur** bascule sur le secours, dans le même run, en flux
  comme hors flux, et le fait est journalisé ;
- une erreur **de requête** (400) n'est jamais rejouée : elle échouerait à
  l'identique sur le second modèle ;
- la bascule est **par appel** (le principal redevient prioritaire ensuite) ;
- le réglage est réellement branché par ``build_chat_model``, **désactivé**
  par le test de configuration administrateur (un secours qui répondrait
  masquerait un modèle candidat inutilisable) et **refusé** quand le nom
  n'existe pas sur la passerelle configurée ;
- seuls les chemins asynchrones sont décorés (le synchrone n'a pas de copie).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx
import openai
import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult

from app.agents import providers
from app.agents.repli_modele import STATUTS_REPLIABLES, RepliModele, erreur_repliable
from app.core.config import Settings

_MESSAGES = [HumanMessage(content="Bonjour")]


def _requete() -> httpx.Request:
    return httpx.Request("POST", "http://localhost:9/v1/chat/completions")


def _statut(statut: int) -> openai.APIStatusError:
    """Erreur HTTP du SDK OpenAI, sans réseau."""
    reponse = httpx.Response(statut, request=_requete())
    classe = (
        openai.RateLimitError if statut == 429 else openai.InternalServerError
        if statut == 503
        else openai.APIStatusError
    )
    return classe(f"HTTP {statut}", response=reponse, body=None)


# ---------------------------------------------------------------------------
# Ce qui justifie un repli — et ce qui ne le justifie pas
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("statut", sorted(STATUTS_REPLIABLES))
def test_panne_fournisseur_repliable(statut: int) -> None:
    assert erreur_repliable(_statut(statut))


def test_erreur_de_requete_non_repliable() -> None:
    """400 : rejouer sur un autre modèle coûterait un appel sans rien changer."""
    assert not erreur_repliable(
        openai.BadRequestError(
            "Requête invalide",
            response=httpx.Response(400, request=_requete()),
            body=None,
        )
    )


def test_connexion_coupee_et_flux_muet_repliables() -> None:
    assert erreur_repliable(openai.APIConnectionError(request=_requete()))
    assert erreur_repliable(openai.APITimeoutError(request=_requete()))
    # ``StreamChunkTimeoutError`` de langchain-openai dérive de TimeoutError.
    assert erreur_repliable(TimeoutError("aucun chunk"))


def test_api_error_sans_statut_repliable() -> None:
    """Forme observée le 19/11 et le 25/11 : la passerelle ne renvoie aucun code."""
    assert erreur_repliable(
        openai.APIError("Service temporarily unavailable", _requete(), body=None)
    )


def test_erreur_locale_non_repliable() -> None:
    assert not erreur_repliable(ValueError("contenu inattendu"))


# ---------------------------------------------------------------------------
# Bascule réelle, sans réseau
# ---------------------------------------------------------------------------


class _SocleFactice(BaseChatModel):
    """Cible de ``super()`` du mixin : réponse immédiate ou panne programmée."""

    etiquette: str = "modele"
    reponse: str = "reponse"
    panne: Any = None
    morceaux_avant_panne: int = 0

    @property
    def _llm_type(self) -> str:
        return "factice"

    def _resultat(self) -> ChatResult:
        return ChatResult(
            generations=[ChatGeneration(message=AIMessage(content=self.reponse))]
        )

    def _fragments(self) -> list[ChatGenerationChunk]:
        return [
            ChatGenerationChunk(message=AIMessageChunk(content="fragment"))
            for _ in range(self.morceaux_avant_panne)
        ]

    # Une panne est levée **après** les éventuels fragments : c'est la coupure
    # de flux en cours de génération.
    def _generate(
        self,
        messages: Any,
        stop: Any = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        if self.panne is not None:
            raise self.panne
        return self._resultat()

    async def _agenerate(
        self,
        messages: Any,
        stop: Any = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        if self.panne is not None:
            raise self.panne
        return self._resultat()

    def _stream(
        self,
        messages: Any,
        stop: Any = None,
        run_manager: Any = None,
        **kwargs: Any,
    ):
        yield from self._fragments()
        if self.panne is not None:
            raise self.panne
        yield ChatGenerationChunk(message=AIMessageChunk(content=self.reponse))

    async def _astream(
        self,
        messages: Any,
        stop: Any = None,
        run_manager: Any = None,
        **kwargs: Any,
    ):
        for fragment in self._fragments():
            yield fragment
        if self.panne is not None:
            raise self.panne
        yield ChatGenerationChunk(message=AIMessageChunk(content=self.reponse))


class ModeleFactice(RepliModele, _SocleFactice):
    """Le mixin passe devant : ``super()`` atteint ``_SocleFactice``."""


def _paire(panne: Any = None, *, morceaux_avant_panne: int = 0) -> ModeleFactice:
    principal = ModeleFactice(etiquette="principal", reponse="principal", panne=panne)
    principal.morceaux_avant_panne = morceaux_avant_panne
    principal.modele_de_secours = ModeleFactice(
        etiquette="secours", reponse="secours"
    )
    return principal


def test_repli_hors_flux(caplog: pytest.LogCaptureFixture) -> None:
    modele = _paire(_statut(503))

    async def _appel() -> ChatResult:
        return await modele._agenerate(_MESSAGES)

    with caplog.at_level(logging.WARNING, logger="app.agents.repli_modele"):
        resultat = asyncio.run(_appel())

    assert resultat.generations[0].message.content == "secours"
    assert "repli de modèle" in caplog.text


def test_erreur_de_requete_ne_bascule_pas() -> None:
    modele = _paire(
        openai.BadRequestError(
            "Requête invalide",
            response=httpx.Response(400, request=_requete()),
            body=None,
        )
    )

    async def _appel() -> ChatResult:
        return await modele._agenerate(_MESSAGES)

    with pytest.raises(openai.BadRequestError):
        asyncio.run(_appel())


def test_repli_en_flux() -> None:
    modele = _paire(_statut(503))

    async def _collecter() -> str:
        return "".join(
            [
                fragment.message.content
                async for fragment in modele._astream(_MESSAGES)
            ]
        )

    assert asyncio.run(_collecter()) == "secours"


def test_repli_en_flux_apres_fragments() -> None:
    """Fragment puis panne : la réponse du secours suit, et le journal le dit."""
    modele = _paire(_statut(503), morceaux_avant_panne=1)

    async def _collecter() -> str:
        return "".join(
            [
                fragment.message.content
                async for fragment in modele._astream(_MESSAGES)
            ]
        )

    assert asyncio.run(_collecter()) == "fragmentsecours"


def test_secours_absent_relance_l_erreur() -> None:
    modele = _paire(_statut(503))
    modele.modele_de_secours = None

    async def _appel() -> ChatResult:
        return await modele._agenerate(_MESSAGES)

    with pytest.raises(openai.APIStatusError):
        asyncio.run(_appel())


# ---------------------------------------------------------------------------
# Branchement dans la construction des modèles
# ---------------------------------------------------------------------------


def _reglages(**surcharges: Any) -> Settings:
    base = {
        "agent_provider": "openai_compatible",
        "agent_model": "principal",
        # Explicite : la suite ne doit pas dépendre du ``.env`` du poste (qui
        # peut définir un secours réel et faire échouer « sans réglage »).
        "agent_model_secours": None,
        "agent_base_url": "http://localhost:9/v1",
        "agent_api_key": "cle-de-test",
        "agent_ssl_verify": False,
    }
    base.update(surcharges)
    return Settings(**base)  # type: ignore[arg-type]


@pytest.fixture(autouse=True)
def _sans_reseau(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pas de réseau : ce test porte sur le repli, pas sur la lecture ``/models``.

    La passerelle est réputée publier le modèle demandé ; les cas « absent » et
    « non vérifié » surchargent cette doublure dans leur propre test.
    """
    monkeypatch.setattr(providers, "_profil_modele", lambda *_a, **_k: {})
    monkeypatch.setattr(
        providers,
        "_fenetre_publiee",
        lambda *_a, **_k: providers._LectureFenetre(100_000, True, present=True),
    )


def test_repli_branche_par_la_construction() -> None:
    modele = providers.build_chat_model(_reglages(agent_model_secours="secours"))

    assert isinstance(modele, RepliModele)
    assert modele.model_name == "principal"
    assert modele.modele_de_secours is not None
    assert modele.modele_de_secours.model_name == "secours"
    # Pas de récursion : le secours n'a pas lui-même de secours.
    assert modele.modele_de_secours.modele_de_secours is None


def test_pas_de_repli_sans_reglage() -> None:
    modele = providers.build_chat_model(_reglages())
    assert modele.modele_de_secours is None


def test_pas_de_repli_sur_le_meme_modele() -> None:
    modele = providers.build_chat_model(_reglages(agent_model_secours="principal"))
    assert modele.modele_de_secours is None


def test_secours_absent_de_la_passerelle_refuse(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Un nom pris sur un autre catalogue ne doit jamais être attaché."""
    monkeypatch.setattr(
        providers,
        "_fenetre_publiee",
        lambda *_a, **_k: providers._LectureFenetre(
            None, False, "modèle absent de la liste /models", present=False
        ),
    )
    with caplog.at_level(logging.WARNING, logger="app.agents.providers"):
        modele = providers.build_chat_model(_reglages(agent_model_secours="fantome"))

    assert modele.modele_de_secours is None
    assert "fantome" in caplog.text and "absent" in caplog.text


def test_secours_non_verifie_refuse(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """``/models`` illisible : la présence n'est pas prouvée, donc pas de repli."""
    monkeypatch.setattr(
        providers,
        "_fenetre_publiee",
        lambda *_a, **_k: providers._LectureFenetre(
            None, False, "ReadTimeout", present=None
        ),
    )
    with caplog.at_level(logging.WARNING, logger="app.agents.providers"):
        modele = providers.build_chat_model(_reglages(agent_model_secours="secours"))

    assert modele.modele_de_secours is None
    assert "non vérifiée" in caplog.text


def test_test_de_configuration_ignore_le_secours(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Un secours qui répondrait ferait croire que le modèle saisi fonctionne."""
    vus: list[Settings] = []

    class _Reponse:
        content = "CARSO"

    class _Modele:
        async def ainvoke(self, *_a: Any, **_k: Any) -> _Reponse:
            return _Reponse()

    def _faux_build(reglages: Settings, **_k: Any) -> _Modele:
        vus.append(reglages)
        return _Modele()

    monkeypatch.setattr(providers, "build_chat_model", _faux_build)

    asyncio.run(
        providers.tester_configuration(
            provider="openai_compatible",
            model="candidat",
            base_url="http://localhost:9/v1",
            api_key="cle-de-test",
            settings=_reglages(agent_model_secours="secours"),
        )
    )

    assert vus and vus[0].agent_model_secours is None
