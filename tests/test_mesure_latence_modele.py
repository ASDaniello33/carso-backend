"""Durée de l'étape « model » journalisée — un incident de latence attribuable.

Constat du 15/11 : la latence d'un tour ne pouvait être attribuée qu'en rejouant
une campagne de mesures hors production, faute de trace du temps passé dans le
tour de modèle. Ces tests vérifient le contrat du middleware qui l'écrit :

1. une ligne par appel de modèle, avec durée, modèle, adresse et taille d'entrée ;
2. la réponse du modèle est rendue **inchangée** (le middleware ne décore rien) ;
3. un échec est journalisé avec sa durée, puis l'exception est relancée telle
   quelle — un tour refusé lentement ne ressemble pas à un refus immédiat ;
4. rien de sensible n'est écrit : ni texte de message, ni clé.
"""

from __future__ import annotations

import logging
import time
from types import SimpleNamespace

import pytest
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import MemorySaver

from app.agents.catalogue import AGENT_DEFINITIONS
from app.agents.harness import AgentRuntimeConfig, build_system_prompt, create_carso_deep_agent
from app.agents.mesure_latence_modele import (
    MesureLatenceModeleMiddleware,
    adresse_modele,
    nom_modele,
    nom_outil,
    taille_entree,
)
from tests.agent_fixtures import ScriptedChatModel, final_message


def _modele(nom: str = "qwen-3.8-27b", base: str = "https://logfare.ai/v1") -> SimpleNamespace:
    return SimpleNamespace(model_name=nom, openai_api_base=base)


def _requete(
    messages: int = 3,
    contenu: str = "Bonjour",
    *,
    thread_id: str = "fil-1",
    run_id: str = "run-1",
) -> SimpleNamespace:
    return SimpleNamespace(
        model=_modele(),
        messages=[SimpleNamespace(content=contenu) for _ in range(messages)],
        system_message=SimpleNamespace(content="x" * 100),
        runtime=SimpleNamespace(
            config={"configurable": {"thread_id": thread_id, "run_id": run_id}}
        ),
    )


# --- Lecture du modèle et de l'entrée ----------------------------------------


def test_nom_et_adresse_du_modele() -> None:
    assert nom_modele(_modele()) == "qwen-3.8-27b"
    assert adresse_modele(_modele()) == "https://logfare.ai/v1"
    # Un provider natif sans URL ne produit pas de champ vide dans la ligne.
    assert adresse_modele(SimpleNamespace()) is None
    assert nom_modele(SimpleNamespace()) == "SimpleNamespace"


def test_taille_entree_compte_messages_et_prompt_systeme() -> None:
    assert taille_entree(_requete(messages=3, contenu="Bonjour")) == 3 * 7 + 100


# --- Journalisation -----------------------------------------------------------


def test_journalise_duree_modele_et_taille_d_entree(caplog: pytest.LogCaptureFixture) -> None:
    middleware = MesureLatenceModeleMiddleware("agent_generaliste_readonly")

    def handler(_request: object) -> str:
        time.sleep(0.05)
        return "réponse"

    with caplog.at_level(logging.INFO, logger="app.agents.mesure_modele"):
        assert middleware.wrap_model_call(_requete(), handler) == "réponse"

    ligne = caplog.text
    assert "étape model" in ligne
    assert "agent=agent_generaliste_readonly" in ligne
    assert "model=qwen-3.8-27b" in ligne
    assert "base_url=https://logfare.ai/v1" in ligne
    assert "entree=121 car." in ligne
    assert "messages=3" in ligne
    assert "thread=fil-1" in ligne
    assert "run=run-1" in ligne
    # La durée est bien mesurée (0,05 s de sommeil, marge large).
    duree = float(ligne.split("duree=")[1].split(" s")[0])
    assert 0.04 <= duree <= 2.0


def test_ne_journalise_aucun_texte_de_message(caplog: pytest.LogCaptureFixture) -> None:
    middleware = MesureLatenceModeleMiddleware("agent_rh")
    secret = "Contenu métier confidentiel"

    with caplog.at_level(logging.INFO, logger="app.agents.mesure_modele"):
        middleware.wrap_model_call(_requete(contenu=secret), lambda _r: "ok")

    assert secret not in caplog.text


def test_un_echec_est_journalise_avec_sa_duree_puis_relance(
    caplog: pytest.LogCaptureFixture,
) -> None:
    middleware = MesureLatenceModeleMiddleware("agent_rh")

    def handler(_request: object) -> str:
        time.sleep(0.02)
        raise RuntimeError("fournisseur indisponible")

    with caplog.at_level(logging.INFO, logger="app.agents.mesure_modele"):
        with pytest.raises(RuntimeError, match="fournisseur indisponible"):
            middleware.wrap_model_call(_requete(), handler)

    assert "étape model en échec" in caplog.text
    assert "erreur=RuntimeError" in caplog.text
    assert "duree=" in caplog.text


# --- Branchement dans le graphe de production --------------------------------


def test_une_etape_lente_est_signalee_en_warning(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Au-delà du seuil, l'étape saute aux yeux dans le journal (15/11 : 209 s)."""
    from app.agents import mesure_latence_modele as module

    monkeypatch.setattr(module, "SEUIL_ETAPE_LENTE", 0.0)
    middleware = MesureLatenceModeleMiddleware("agent_generateur_offre")

    with caplog.at_level(logging.INFO, logger="app.agents.mesure_modele"):
        middleware.wrap_model_call(_requete(), lambda _r: "ok")

    assert "étape model lente" in caplog.text
    assert caplog.records[-1].levelno == logging.WARNING


def test_une_reponse_vide_est_signalee(caplog: pytest.LogCaptureFixture) -> None:
    """Reproduction du 15/11 : run terminé sans un mot, rien dans le journal."""
    from langchain_core.messages import AIMessage

    middleware = MesureLatenceModeleMiddleware("agent_generateur_offre")

    with caplog.at_level(logging.INFO, logger="app.agents.mesure_modele"):
        middleware.wrap_model_call(_requete(), lambda _r: AIMessage(content=""))

    assert "réponse vide" in caplog.text
    assert caplog.records[-1].levelno == logging.WARNING


def test_un_appel_d_outil_sans_texte_n_est_pas_une_reponse_vide(
    caplog: pytest.LogCaptureFixture,
) -> None:
    from langchain_core.messages import AIMessage

    middleware = MesureLatenceModeleMiddleware("agent_generateur_offre")
    appel = AIMessage(
        content="", tool_calls=[{"name": "search_appels", "args": {}, "id": "c1"}]
    )

    with caplog.at_level(logging.INFO, logger="app.agents.mesure_modele"):
        middleware.wrap_model_call(_requete(), lambda _r: appel)

    assert "réponse vide" not in caplog.text


def test_bilan_par_run(caplog: pytest.LogCaptureFixture) -> None:
    """Une ligne par run : durée totale et nombre d'étapes modèle."""
    runtime = SimpleNamespace(
        execution_info=SimpleNamespace(thread_id="fil-bilan", run_id="run-bilan")
    )
    middleware = MesureLatenceModeleMiddleware("agent_generateur_offre")

    with caplog.at_level(logging.INFO, logger="app.agents.mesure_modele"):
        assert middleware.before_agent({}, runtime) is None
        middleware.wrap_model_call(
            _requete(thread_id="fil-bilan", run_id="run-bilan"), lambda _r: "ok"
        )
        assert middleware.after_agent({}, runtime) is None

    ligne = [r for r in caplog.records if "run terminé" in r.getMessage()][-1]
    message = ligne.getMessage()
    assert "agent=agent_generateur_offre" in message
    assert "etapes=1" in message
    assert "thread=fil-bilan" in message
    assert "run=run-bilan" in message
    # Le suivi est libéré : aucune fuite d'état entre deux runs.
    assert middleware._runs == {}


# --- Temps passé dans les outils (15/11 : « avant et après les outils ») -----


def _appel_outil(
    nom: str = "generer_document_offre", *, thread_id: str = "fil-o", run_id: str = "run-o"
) -> SimpleNamespace:
    return SimpleNamespace(
        tool_call={"name": nom, "args": {}, "id": "c1"},
        tool=SimpleNamespace(name=nom),
        runtime=SimpleNamespace(
            config={"configurable": {"thread_id": thread_id, "run_id": run_id}}
        ),
    )


def test_nom_outil_lu_dans_les_deux_formes() -> None:
    """Dict et objet coexistent selon la version : les deux sont lus."""
    assert nom_outil(_appel_outil("generer_document_offre")) == "generer_document_offre"
    assert nom_outil(SimpleNamespace(tool=SimpleNamespace(name="autre"))) == "autre"
    assert nom_outil(SimpleNamespace()) == "inconnu"


def test_la_duree_d_un_outil_est_journalisee(caplog: pytest.LogCaptureFixture) -> None:
    """Le temps passé dans l'outil — invisible dans les étapes model — est tracé."""
    middleware = MesureLatenceModeleMiddleware("agent_generateur_offre")

    def handler(_request: object) -> str:
        time.sleep(0.05)
        return "document"

    with caplog.at_level(logging.INFO, logger="app.agents.mesure_modele"):
        assert middleware.wrap_tool_call(_appel_outil(), handler) == "document"

    ligne = caplog.text
    assert "outil=generer_document_offre" in ligne
    assert "agent=agent_generateur_offre" in ligne
    assert "thread=fil-o" in ligne
    duree = float(ligne.split("duree=")[1].split(" s")[0])
    assert 0.04 <= duree <= 2.0


def test_un_outil_lent_est_signale_en_warning(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Une génération de 5-6 min doit apparaître comme lente, pas comme un écart."""
    from app.agents import mesure_latence_modele as module

    monkeypatch.setattr(module, "SEUIL_OUTIL_LENT", 0.0)
    middleware = MesureLatenceModeleMiddleware("agent_generateur_offre")

    with caplog.at_level(logging.INFO, logger="app.agents.mesure_modele"):
        middleware.wrap_tool_call(_appel_outil(), lambda _r: "ok")

    assert "outil lent" in caplog.text
    assert caplog.records[-1].levelno == logging.WARNING


def test_un_outil_en_echec_est_journalise_puis_relance(
    caplog: pytest.LogCaptureFixture,
) -> None:
    middleware = MesureLatenceModeleMiddleware("agent_rh")

    def handler(_request: object) -> str:
        raise RuntimeError("outil cassé")

    with caplog.at_level(logging.INFO, logger="app.agents.mesure_modele"):
        with pytest.raises(RuntimeError, match="outil cassé"):
            middleware.wrap_tool_call(_appel_outil("lister_cv"), handler)

    assert "outil en échec" in caplog.text
    assert "outil=lister_cv" in caplog.text
    assert "erreur=RuntimeError" in caplog.text


def test_le_bilan_du_run_compte_le_temps_des_outils(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Le bilan sépare enfin les trois postes : modèle, outils, mise en forme."""
    runtime = SimpleNamespace(
        execution_info=SimpleNamespace(thread_id="fil-total", run_id="run-total")
    )
    middleware = MesureLatenceModeleMiddleware("agent_generateur_offre")

    with caplog.at_level(logging.INFO, logger="app.agents.mesure_modele"):
        assert middleware.before_agent({}, runtime) is None
        middleware.wrap_model_call(
            _requete(thread_id="fil-total", run_id="run-total"), lambda _r: "ok"
        )
        middleware.wrap_tool_call(
            _appel_outil(thread_id="fil-total", run_id="run-total"), lambda _r: "doc"
        )
        middleware.wrap_tool_call(
            _appel_outil(thread_id="fil-total", run_id="run-total"), lambda _r: "doc"
        )
        assert middleware.after_agent({}, runtime) is None

    ligne = [r for r in caplog.records if "run terminé" in r.getMessage()][-1]
    message = ligne.getMessage()
    assert "etapes=1" in message
    assert "outils=2" in message
    assert "temps_outils=" in message


@pytest.mark.anyio
async def test_l_outil_est_mesure_aussi_en_async(caplog: pytest.LogCaptureFixture) -> None:
    """Le graphe exécute les outils en asynchrone : ce chemin doit compter aussi."""
    middleware = MesureLatenceModeleMiddleware("agent_assistant_formateur")

    async def handler(_request: object) -> str:
        return "ok"

    with caplog.at_level(logging.INFO, logger="app.agents.mesure_modele"):
        assert await middleware.awrap_tool_call(_appel_outil("importer_excel"), handler) == "ok"

    assert "outil=importer_excel" in caplog.text


def test_le_graphe_de_production_ecrit_la_ligne(caplog: pytest.LogCaptureFixture) -> None:
    """Le harnais monte la mesure : un run réel du graphe laisse une trace."""
    definition = next(d for d in AGENT_DEFINITIONS if d.agent_id == "agent_generaliste_readonly")
    graphe = create_carso_deep_agent(
        definition=definition,
        tools=[],
        runtime=AgentRuntimeConfig(
            model=ScriptedChatModel(responses=[final_message("ok")]),
            checkpointer=MemorySaver(),
        ),
    )

    with caplog.at_level(logging.INFO, logger="app.agents.mesure_modele"):
        graphe.invoke(
            {"messages": [HumanMessage(content="Bonjour")]},
            config={"configurable": {"thread_id": "fil-journal"}},
        )

    assert "agent=agent_generaliste_readonly" in caplog.text
    assert "model=ScriptedChatModel" in caplog.text
    assert "thread=fil-journal" in caplog.text

    # Le prompt système de l'agent reste servi normalement.
    assert "Page ouverte" in build_system_prompt(definition)
