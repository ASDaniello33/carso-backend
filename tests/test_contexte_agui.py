"""Non-régression — le chemin AG-UI ne casse plus le contexte d'exécution.

Panne corrigée : ``UserContext.__init__() got an unexpected keyword argument
'thread_id'``. Chaîne exacte :

1. ``ag_ui_langgraph`` écrit ``config["configurable"]["thread_id"]`` (clé
   client : ``RunAgentInput.thread_id``) ;
2. son ``get_stream_kwargs`` verse ``config['configurable']`` dans
   ``kwargs['context']`` ;
3. ``langgraph.pregel._coerce_context`` applique le schéma du graphe :
   ``context_schema(**context)`` → ``TypeError`` sur une clé d'exécution.

Plus une **faillite latente** corrigée au passage : ce contexte reconstruit
n'a aucune identité, il valait ``est_interne=True`` par défaut — un formateur
serait passé pour un compte interne. L'autorité reste le contexte posé par la
garde serveur ; un contexte porteur de clés d'exécution n'en est jamais un.
"""

from types import SimpleNamespace

import pytest
from langchain_core.messages import SystemMessage, ToolMessage
from langgraph.pregel.main import _coerce_context

from app.agents.contexte import (
    ContexteUtilisateurMiddleware,
    UserContext,
    contexte_du_runtime,
    outils_autorises_pour,
    poser_contexte_utilisateur,
    retablir_contexte_utilisateur,
)

#: Clés que l'adaptateur verse dans la configuration partagée, donc dans le
#: contexte du graphe : reprise du fil, et checkpoints sur les chemins *fork* /
#: *regenerate*.
CLES_DU_FIL = {"thread_id": "thread-démo", "checkpoint_ns": "agent", "checkpoint_id": "cp1"}


# --- 1. La construction du contexte ne doit pas lever ----------------------------


def test_user_context_absorbe_les_cles_d_execution() -> None:
    """Les clés du fil sont absorbées, jamais appliquées à l'identité."""
    contexte = UserContext(**CLES_DU_FIL)

    assert contexte.cles_execution == ("checkpoint_id", "checkpoint_ns", "thread_id")
    assert not contexte.est_construit_par_le_serveur
    # Aucun droit n'est élargi : les valeurs par défaut restent inchangées.
    assert contexte.role == ""
    assert contexte.utilisateur_id is None
    assert contexte.outils_autorises == frozenset()


def test_la_coercion_langgraph_ne_leve_plus_de_typeerror() -> None:
    """Le chemin exact de la panne : ``_coerce_context(UserContext, configurable)``."""
    contexte = _coerce_context(UserContext, dict(CLES_DU_FIL))

    assert isinstance(contexte, UserContext)
    assert "thread_id" in contexte.cles_execution


def test_un_contexte_construit_par_le_serveur_est_signe() -> None:
    """La voie explicite (``context=UserContext(...)``) reste identifiable."""
    contexte = UserContext(role="formateur", est_interne=False, utilisateur_id="u-1")

    assert contexte.cles_execution == ()
    assert contexte.est_construit_par_le_serveur


# --- 2. L'autorité reste le contexte posé par la garde ---------------------------


def test_le_contexte_reconstruit_n_ouvre_aucun_droit_hors_run() -> None:
    """Sans garde, un contexte reconstruit ne vaut pas « accès complet »."""
    runtime = SimpleNamespace(context=UserContext(**CLES_DU_FIL))

    assert contexte_du_runtime(runtime) is None
    assert outils_autorises_pour(runtime) == frozenset()


def test_la_garde_prime_sur_le_contexte_reconstruit() -> None:
    """Le contexte du compte connecté l'emporte, clés d'exécution ou non."""
    garde = UserContext(
        role="formateur",
        est_interne=False,
        utilisateur_id="u-1",
        outils_autorises=frozenset({"query_analytics"}),
    )
    runtime = SimpleNamespace(context=UserContext(**CLES_DU_FIL))
    jeton = poser_contexte_utilisateur(garde)
    try:
        effectif = contexte_du_runtime(runtime)
        assert effectif is garde
        assert effectif.role == "formateur"
        assert not effectif.est_interne
        assert effectif.peut_executer("query_analytics")
        assert not effectif.peut_executer("proposer_affectation")
    finally:
        retablir_contexte_utilisateur(jeton)


def test_le_contexte_explicite_sert_hors_run_agent() -> None:
    """Tests, scripts et tâches inter-agents : la voie explicite reste valable."""
    runtime = SimpleNamespace(context=UserContext(role="collaborateur"))

    assert contexte_du_runtime(runtime).role == "collaborateur"


# --- 3. Le refus d'outil tient malgré le contexte reconstruit --------------------


def test_le_middleware_refuse_un_tool_hors_contrat_du_role() -> None:
    """Un formateur ne voit pas « proposer une affectation » passer l'exécution."""
    garde = UserContext(
        role="formateur",
        est_interne=False,
        utilisateur_id="u-1",
        outils_autorises=frozenset({"query_analytics"}),
    )
    middleware = ContexteUtilisateurMiddleware()
    runtime = SimpleNamespace(context=UserContext(**CLES_DU_FIL))
    jeton = poser_contexte_utilisateur(garde)
    try:
        demande = SimpleNamespace(
            runtime=runtime, tool_call={"name": "proposer_affectation", "id": "call-1"}
        )

        reponse = middleware.wrap_tool_call(demande, lambda request: "exécuté")

        assert isinstance(reponse, ToolMessage)
        assert "Accès refusé" in reponse.content
    finally:
        retablir_contexte_utilisateur(jeton)


def test_le_middleware_laisse_passer_un_tool_du_contrat() -> None:
    garde = UserContext(
        role="formateur",
        est_interne=False,
        utilisateur_id="u-1",
        outils_autorises=frozenset({"query_analytics"}),
    )
    middleware = ContexteUtilisateurMiddleware()
    runtime = SimpleNamespace(context=UserContext(**CLES_DU_FIL))
    demande = SimpleNamespace(runtime=runtime, tool_call={"name": "query_analytics", "id": "c1"})
    jeton = poser_contexte_utilisateur(garde)
    try:
        assert middleware.wrap_tool_call(demande, lambda request: "exécuté") == "exécuté"
    finally:
        retablir_contexte_utilisateur(jeton)


class _RequeteModel(SimpleNamespace):
    """Double minimal de ``ModelRequest`` : le middleware relit l'override."""

    def override(self, **champs: object) -> "_RequeteModel":
        return _RequeteModel(**{**self.__dict__, **champs})


def test_le_prompt_annonce_le_role_du_compte() -> None:
    """Le bloc de contexte vient bien de la garde, pas du contexte reconstruit."""
    garde = UserContext(
        role="formateur",
        est_interne=False,
        utilisateur_id="u-1",
        nom_complet="Fara Formatrice",
        missions_autorisees=("M-1 — Gestion budgétaire",),
    )
    middleware = ContexteUtilisateurMiddleware()
    runtime = SimpleNamespace(context=UserContext(**CLES_DU_FIL))
    demande = _RequeteModel(runtime=runtime, system_message=SystemMessage(content="Système"))
    jeton = poser_contexte_utilisateur(garde)
    try:
        relue: list[SystemMessage] = []
        middleware.wrap_model_call(demande, lambda request: relue.append(request.system_message))
    finally:
        retablir_contexte_utilisateur(jeton)

    contenu = relue[0].text
    assert "Fara Formatrice" in contenu
    assert "formateur" in contenu
    assert "M-1 — Gestion budgétaire" in contenu


@pytest.mark.parametrize("cle", ["thread_id", "checkpoint_ns", "checkpoint_id"])
def test_une_cle_d_execution_ne_casse_pas_la_construction(cle: str) -> None:
    """Chaque clé du fil, prise isolément, reste inoffensive."""
    contexte = UserContext(**{cle: "valeur"})

    assert contexte.cles_execution == (cle,)