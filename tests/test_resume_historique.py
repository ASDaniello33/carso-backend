"""Résumé d'historique : seuil en fraction, comptage réel, durée journalisée.

Le constat du 15/11 : un run de 974 s contenait deux trous (331 s et 20 s) qui
étaient le **résumé d'historique** de Deep Agents, invisible dans le journal
parce qu'appelé hors des étapes « model ». Le seuil est désormais **explicite**
(75 % de la fenêtre déclarée, 10 % conservés) et le comptage porte sur l'entrée
réelle. Ces tests verrouillent :

- le seuil de déclenchement, en fraction de ``AGENT_MAX_INPUT_TOKENS`` ;
- le fait que le `total_tokens` déclaré par le fournisseur (sortie et
  raisonnement compris) ne déclenche plus un résumé ;
- la fenêtre posée sur un modèle qui n'en annonce aucune (sans quoi la fraction
  est refusée à la construction) ;
- la journalisation de la durée, lente ou en échec.
"""

from __future__ import annotations

import logging

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from app.agents.resume_historique import (
    NOM_MIDDLEWARE,
    SEUIL_RESUME_LENT,
    construire_resume_historique,
)
from app.core.config import Settings
from tests.agent_fixtures import ScriptedChatModel, final_message

FENETRE = 200_000
SEUIL = int(FENETRE * 0.75)


def _modele(**extra: object) -> ScriptedChatModel:
    """Modèle scripté, sans profil déclaré (comme une passerelle compatible)."""
    return ScriptedChatModel(responses=[final_message("prêt")], **extra)  # type: ignore[arg-type]


def _resume(model=None, fraction: float = 0.75, gardee: float = 0.10):
    from deepagents.backends import StateBackend

    return construire_resume_historique(
        model if model is not None else _modele(),
        StateBackend(),
        agent_id="agent_generateur_offre",
        fraction_seuil=fraction,
        fraction_gardee=gardee,
        fenetre_jetons=FENETRE,
    )


def test_le_resume_remplace_le_defaut_par_son_nom() -> None:
    """Le nom est le contrat : ``create_deep_agent`` remplace à sa place."""
    assert _resume().name == NOM_MIDDLEWARE == "SummarizationMiddleware"


def test_le_seuil_est_la_fraction_de_la_fenetre() -> None:
    mw = _resume()

    assert mw._lc_helper.trigger == ("fraction", 0.75)
    assert mw._lc_helper.keep == ("fraction", 0.10)
    assert mw._should_summarize([HumanMessage(content="x")], SEUIL - 1) is False
    assert mw._should_summarize([HumanMessage(content="x")], SEUIL) is True


def test_une_fraction_differente_change_le_seuil() -> None:
    mw = _resume(fraction=0.5)

    assert mw._should_summarize([HumanMessage(content="x")], int(FENETRE * 0.5)) is True
    assert mw._should_summarize([HumanMessage(content="x")], int(FENETRE * 0.4)) is False


def test_la_fenetre_est_posee_si_le_modele_n_en_annonce_aucune() -> None:
    """Sans ``max_input_tokens``, LangChain refuse un seuil en fraction."""
    modele = _modele()
    assert modele.profile is None

    mw = _resume(modele)

    assert modele.profile == {"max_input_tokens": FENETRE}
    assert mw._get_profile_limits() == FENETRE


def test_la_fenetre_du_modele_est_respectee() -> None:
    """Un profil déclaré par le provider prime sur la valeur de repli."""
    modele = _modele(profile={"max_input_tokens": 100_000})

    mw = _resume(modele)

    assert mw._lc_helper.trigger == ("fraction", 0.75)
    assert mw._get_profile_limits() == 100_000


def test_le_total_declare_par_le_fournisseur_ne_declenche_pas() -> None:
    """Le total déclaré inclut sortie et raisonnement : il surestime l'entrée.

    Le 15/11, un résumé de 331 s a démarré sur ~157 000 caractères d'entrée :
    c'est ce chemin (``_should_summarize_based_on_reported_tokens``) qui a
    parlé, pas la taille réelle de la requête.
    """
    mw = _resume()
    gonfle = AIMessage(
        content="ok",
        usage_metadata={
            "input_tokens": 1_000,
            "output_tokens": 1_000,
            "total_tokens": 300_000,
        },
        response_metadata={"model_provider": "openai"},
    )

    # L'entrée réelle (comptée) est très en dessous du seuil : pas de résumé,
    # même si le fournisseur annonce 300 000 jetons.
    assert mw._should_summarize([HumanMessage(content="x"), gonfle], 1_000) is False


def test_le_resume_est_chronometre(monkeypatch: pytest.MonkeyPatch) -> None:
    from deepagents.middleware.summarization import SummarizationMiddleware

    monkeypatch.setattr(
        SummarizationMiddleware, "_create_summary", lambda self, messages: "résumé"
    )
    mw = _resume()

    assert mw._create_summary([HumanMessage(content="x")]) == "résumé"

    assert any("résumé historique : " in r.message for r in _lignes())
    assert any("seuil=" in r.message and "0.75" in r.message for r in _lignes())


def _lignes() -> list[logging.LogRecord]:
    """Lignes du journal de mesure capturées par le dernier test."""
    return _CAPTEUR.records


class _Capteur(logging.Handler):
    records: list[logging.LogRecord]

    def __init__(self) -> None:
        super().__init__()
        self.records = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


_CAPTEUR = _Capteur()


@pytest.fixture(autouse=True)
def _capture():
    journal = logging.getLogger("app.agents.mesure_modele")
    journal.addHandler(_CAPTEUR)
    journal.setLevel(logging.DEBUG)
    _CAPTEUR.records.clear()
    yield
    journal.removeHandler(_CAPTEUR)


def test_un_resume_lent_est_signale(monkeypatch: pytest.MonkeyPatch) -> None:
    from deepagents.middleware.summarization import SummarizationMiddleware

    monkeypatch.setattr(
        SummarizationMiddleware, "_create_summary", lambda self, messages: "résumé"
    )
    monkeypatch.setattr("app.agents.resume_historique.SEUIL_RESUME_LENT", -1.0)
    mw = _resume()

    assert SEUIL_RESUME_LENT > 0  # la constante d'origine reste un vrai seuil
    mw._create_summary([HumanMessage(content="x")])

    assert any(r.levelno == logging.WARNING for r in _lignes())
    assert any("LENT" in r.message for r in _lignes())


def test_un_echec_de_resume_est_journalise_et_relance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from deepagents.middleware.summarization import SummarizationMiddleware

    def _echoue(self, messages):
        raise RuntimeError("fournisseur muet")

    monkeypatch.setattr(SummarizationMiddleware, "_create_summary", _echoue)
    mw = _resume()

    with pytest.raises(RuntimeError, match="fournisseur muet"):
        mw._create_summary([HumanMessage(content="x")])

    assert any("en échec" in r.message for r in _lignes())
    assert any(r.levelno == logging.WARNING for r in _lignes())


def test_le_harness_monte_le_resume_configure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Câblage : le harnais passe bien les fractions et la fenêtre."""
    from app.agents.base import AgentDefinition
    from app.agents.harness import AgentRuntimeConfig, create_carso_deep_agent
    from app.tools.permissions import PermissionPolicy

    appels: list[dict[str, object]] = []

    def _recorder(model, backend, **kwargs):
        appels.append(kwargs)
        from deepagents.middleware.summarization import SummarizationMiddleware

        return SummarizationMiddleware(
            model=model, backend=backend, trigger=("tokens", 1), keep=("messages", 1)
        )

    monkeypatch.setattr(
        "app.agents.resume_historique.construire_resume_historique", _recorder
    )
    definition = AgentDefinition(
        agent_id="AgentTestResume",
        display_name="Agent de test",
        description="d",
        policy=PermissionPolicy(allowed_capabilities=frozenset({"c"})),
        tools=(),
    )
    settings = Settings(
        agent_model="modele-de-test",
        agent_base_url="http://localhost:1/v1",
        agent_resume_historique_fraction=0.75,
        agent_resume_historique_fraction_gardee=0.1,
        agent_max_input_tokens=200_000,
    )

    create_carso_deep_agent(
        definition=definition,
        tools=[],
        runtime=AgentRuntimeConfig(
            model=ScriptedChatModel(responses=[final_message("x")])
        ),
        settings=settings,
    )

    assert appels
    assert appels[0]["fraction_seuil"] == 0.75
    assert appels[0]["fraction_gardee"] == 0.1
    assert appels[0]["fenetre_jetons"] == 200_000
    assert appels[0]["agent_id"] == "AgentTestResume"


def test_le_seuil_suit_la_fenetre_et_n_est_pas_une_valeur_fixe() -> None:
    """Question posée (15/11) : le seuil est-il 75 % **de la fenêtre du modèle**,
    ou une valeur fixe déguisée ?

    Deux modèles de fenêtres très différentes sont construits : si le seuil était
    fixe (170 000 du défaut de la bibliothèque, ou 200 000 du repli), les deux
    déclencheraient au même nombre de jetons. C'est le contraire qui doit se voir.
    """
    grand = _modele(profile={"max_input_tokens": 1_048_576})
    petit = _modele(profile={"max_input_tokens": 200_000})
    mw_grand = _resume(grand)
    mw_petit = _resume(petit)

    seuil_grand = int(1_048_576 * 0.75)  # 786 432
    seuil_petit = int(200_000 * 0.75)  # 150 000
    assert seuil_grand != seuil_petit

    # Chacun déclenche à **son** seuil, et pas à celui de l'autre.
    assert mw_grand._should_summarize([HumanMessage(content="x")], seuil_grand) is True
    assert mw_petit._should_summarize([HumanMessage(content="x")], seuil_petit) is True
    assert mw_petit._should_summarize([HumanMessage(content="x")], seuil_petit - 1) is False

    # Le seuil du grand modèle ne serait pas atteint par le petit : la valeur
    # fixe 170 000/200 000 n'est le seuil d'aucun des deux.
    assert mw_grand._should_summarize([HumanMessage(content="x")], 170_000) is False
    assert mw_petit._should_summarize([HumanMessage(content="x")], 170_000) is True
    # 786 432 est bien 75 % de la grande fenêtre, pas 75 % de la petite.
    assert int(1_048_576 * 0.75) == 786_432
    assert int(200_000 * 0.75) == 150_000


def test_le_compteur_ne_se_recale_pas_sur_le_fournisseur() -> None:
    """Le compteur annoncé est déterministe : 4 caractères par jeton."""
    mw = _resume()
    messages = [HumanMessage(content="a" * 4_000)]

    # 4 000 caractères ≈ 1 000 jetons + jetons de structure, sans recours à un
    # `total_tokens` de fournisseur.
    compte = mw._count_tokens(messages, None, None)
    assert 1_000 <= compte <= 1_100
