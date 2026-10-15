"""Collaboration inter-agent — tests du runtime (instruction/10 §8).

Couverture exigée par `instruction/10` §8 : cible inexistante, timeout, réponse
invalide, permissions insuffisantes, boucle inter-agent, corrélation
``task_id``/``correlation_id``, absence de transfert de privilèges.

Aucun modèle, aucun réseau : les agents appelés sont des handlers de test qui
respectent le contrat ``AgentHandler`` (instruction/05 §7).
"""

from __future__ import annotations

import dataclasses
import json
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.agents import (
    GET_RESULT_CAPABILITY,
    REQUEST_TASK_CAPABILITY,
    AgentDefinition,
    AgentHandlerRegistry,
    AgentRequest,
    AgentResponse,
    AgentTaskService,
    CollaborationGrant,
    InterAgentGateway,
    build_collaboration_tools,
)
from app.agents.harness import allowed_tools_only
from app.core.config import Settings
from app.core.errors import (
    AgentLoopError,
    AgentTimeoutError,
    ConflictError,
    InvalidAgentResponseError,
    NotFoundError,
    PermissionDeniedError,
    ValidationError,
)
from app.domain.document import AgentTask
from app.tools.permissions import PermissionPolicy

TACHE = "proposer_equipe"
CIBLE = "AgentCible"
APPELANT = "AgentAppelant"


# --- Outils de test -------------------------------------------------------------


class RecordingHandler:
    """Handler d'agent de test : enregistre la demande, renvoie une réponse scriptée."""

    def __init__(
        self,
        agent_id: str,
        task_types: set[str],
        *,
        result: dict | None = None,
        response: object | None = None,
        error: BaseException | None = None,
    ) -> None:
        self._agent_id = agent_id
        self._task_types = frozenset(task_types)
        self._result = result
        self._response = response
        self._error = error
        self.requests: list[AgentRequest] = []

    @property
    def agent_id(self) -> str:
        return self._agent_id

    def supported_task_types(self) -> frozenset[str]:
        return self._task_types

    def handle(self, request: AgentRequest) -> AgentResponse:
        self.requests.append(request)
        if self._error is not None:
            raise self._error
        if self._response is not None:
            return self._response  # type: ignore[return-value]
        return AgentResponse(
            task_id=request.task_id,
            status="completed",
            result=self._result if self._result is not None else {"ok": True},
            requires_approval=request.requires_approval,
        )


class ScriptedClock:
    """Horloge monotone scriptée : deux valeurs par appel de gateway."""

    def __init__(self, *values: float) -> None:
        self._values = list(values)
        self._index = 0

    def __call__(self) -> float:
        value = self._values[min(self._index, len(self._values) - 1)]
        self._index += 1
        return value


def definition(
    agent_id: str,
    *,
    capabilities: tuple[str, ...] = (REQUEST_TASK_CAPABILITY, GET_RESULT_CAPABILITY),
    grants: tuple[CollaborationGrant, ...] = (),
    tools: tuple[str, ...] = (),
) -> AgentDefinition:
    """Contrat d'agent de test (capacités explicites, refus par défaut sinon)."""
    return AgentDefinition(
        agent_id=agent_id,
        display_name=agent_id,
        description="Agent de test",
        policy=PermissionPolicy(allowed_capabilities=frozenset(capabilities)),
        capabilities=capabilities,
        tools=tools,
        collaboration=grants,
    )


APPELANT_DEF = definition(
    APPELANT,
    grants=(CollaborationGrant(CIBLE, frozenset({TACHE})),),
)


@pytest.fixture
def registry() -> AgentHandlerRegistry:
    """Registre des agents appelables (vide : chaque test enregistre le sien)."""
    return AgentHandlerRegistry()


@pytest.fixture
def gateway(
    api_session_factory: sessionmaker[Session], registry: AgentHandlerRegistry
) -> InterAgentGateway:
    """Passerelle par défaut : profondeur 3, budget 60 s (config production)."""
    return InterAgentGateway(api_session_factory, handlers=registry)


def _tasks(api_session_factory: sessionmaker[Session]) -> list[AgentTask]:
    """Toutes les traces ``agent_tasks`` (ordre chronologique)."""
    session = api_session_factory()
    try:
        return list(session.scalars(select(AgentTask).order_by(AgentTask.created_at)))
    finally:
        session.close()


# --- Corrélation et trace nominales ---------------------------------------------


class TestDelegationNominale:
    def test_delegation_tracee_et_correlee(
        self, gateway, registry, api_session_factory
    ) -> None:
        handler = RecordingHandler(CIBLE, {TACHE}, result={"proposition": 3})
        registry.register(handler)

        reponse = gateway.request(
            APPELANT_DEF, to_agent=CIBLE, task_type=TACHE, payload={"mission": "M-1"}
        )

        assert reponse.status == "completed"
        assert reponse.result == {"proposition": 3}
        assert reponse.task_id == handler.requests[0].task_id

        demande = handler.requests[0]
        assert demande.from_agent == APPELANT
        assert demande.to_agent == CIBLE
        assert demande.task_type == TACHE
        assert demande.correlation_id  # corrélation obligatoire (instruction/05 §7)

        traces = _tasks(api_session_factory)
        assert len(traces) == 1
        trace = traces[0]
        assert str(trace.id) == reponse.task_id
        assert trace.from_agent == APPELANT
        assert trace.to_agent == CIBLE
        assert trace.task_type == TACHE
        assert trace.status == "completed"
        assert trace.correlation_id == demande.correlation_id
        assert trace.requires_approval is True
        assert trace.result == {"proposition": 3}
        assert trace.payload == {"mission": "M-1"}

    def test_correlation_partagee_entre_appels(
        self, gateway, registry, api_session_factory
    ) -> None:
        registry.register(RecordingHandler(CIBLE, {TACHE}))
        correlation = "corr-partagee-1"

        premier = gateway.request(
            APPELANT_DEF, to_agent=CIBLE, task_type=TACHE, correlation_id=correlation
        )
        second = gateway.request(
            APPELANT_DEF, to_agent=CIBLE, task_type=TACHE, correlation_id=correlation
        )

        assert premier.task_id != second.task_id
        traces = _tasks(api_session_factory)
        assert [t.correlation_id for t in traces] == [correlation, correlation]

    def test_secrets_retires_du_payload_trace(
        self, gateway, registry, api_session_factory
    ) -> None:
        registry.register(RecordingHandler(CIBLE, {TACHE}))

        gateway.request(
            APPELANT_DEF,
            to_agent=CIBLE,
            task_type=TACHE,
            payload={"mission": "M-1", "api_key": "sk-ne-doit-pas-etre-trace"},
        )

        trace = _tasks(api_session_factory)[0]
        assert trace.payload is not None
        assert "api_key" not in trace.payload
        assert trace.payload["mission"] == "M-1"


# --- Autorisation (permissions insuffisantes) -----------------------------------


class TestAutorisation:
    def test_capacite_de_delegation_absente_refusee(
        self, gateway, registry, api_session_factory
    ) -> None:
        registry.register(RecordingHandler(CIBLE, {TACHE}))
        sans_capacite = definition(
            APPELANT,
            capabilities=(),
            grants=(CollaborationGrant(CIBLE, frozenset({TACHE})),),
        )

        with pytest.raises(PermissionDeniedError):
            gateway.request(sans_capacite, to_agent=CIBLE, task_type=TACHE)

        assert _tasks(api_session_factory) == []

    def test_delegation_non_autorisee_refusee(
        self, gateway, registry, api_session_factory
    ) -> None:
        registry.register(RecordingHandler(CIBLE, {TACHE}))
        sans_grant = definition(APPELANT)  # capacité présente, aucun grant

        with pytest.raises(PermissionDeniedError) as erreur:
            gateway.request(sans_grant, to_agent=CIBLE, task_type=TACHE)

        assert erreur.value.details["delegations_autorisees"] == []
        assert _tasks(api_session_factory) == []

    def test_type_de_tache_hors_grant_refuse(self, gateway, registry) -> None:
        registry.register(RecordingHandler(CIBLE, {TACHE}))

        with pytest.raises(PermissionDeniedError):
            gateway.request(APPELANT_DEF, to_agent=CIBLE, task_type="supprimer_tout")

    def test_grant_limite_au_couple_agent_tache(self, gateway, registry) -> None:
        """Un grant visant un autre agent n'ouvre aucune délégation."""
        registry.register(RecordingHandler("AgentAutre", {TACHE}))

        with pytest.raises(PermissionDeniedError):
            gateway.request(APPELANT_DEF, to_agent="AgentAutre", task_type=TACHE)


# --- Cible inconnue / type de tâche non supporté --------------------------------


class TestCibleEtTache:
    def test_cible_inexistante_refusee(self, gateway, api_session_factory) -> None:
        appelant = definition(
            APPELANT,
            grants=(CollaborationGrant("AgentFantome", frozenset({TACHE})),),
        )

        with pytest.raises(ValidationError) as erreur:
            gateway.request(appelant, to_agent="AgentFantome", task_type=TACHE)

        assert erreur.value.details["agents_disponibles"] == []
        assert _tasks(api_session_factory) == []

    def test_type_de_tache_non_supporte_refuse(self, gateway, registry) -> None:
        registry.register(RecordingHandler(CIBLE, {"autre_tache"}))
        appelant = definition(
            APPELANT, grants=(CollaborationGrant(CIBLE, frozenset({TACHE})),)
        )

        with pytest.raises(ValidationError) as erreur:
            gateway.request(appelant, to_agent=CIBLE, task_type=TACHE)

        assert erreur.value.details["taches_supportees"] == ["autre_tache"]

    def test_registre_refuse_les_doublons(self, registry) -> None:
        registry.register(RecordingHandler(CIBLE, {TACHE}))
        assert registry.ids() == [CIBLE]

        with pytest.raises(ValidationError):
            registry.register(RecordingHandler(CIBLE, {TACHE}))

        with pytest.raises(ValidationError):
            registry.resolve("AgentFantome")


# --- Boucle inter-agent (instruction/10 §8) -------------------------------------


class TestGardeAntiBoucle:
    def test_auto_delegation_refusee(self, gateway, registry, api_session_factory) -> None:
        registre = AgentHandlerRegistry()
        registre.register(RecordingHandler(APPELANT, {TACHE}))
        passerelle = InterAgentGateway(api_session_factory, handlers=registre)
        recursif = definition(
            APPELANT, grants=(CollaborationGrant(APPELANT, frozenset({TACHE})),)
        )

        with pytest.raises(AgentLoopError):
            passerelle.request(recursif, to_agent=APPELANT, task_type=TACHE)

        assert _tasks(api_session_factory) == []

    def test_profondeur_maximale_refusee(self, registry, api_session_factory) -> None:
        registry.register(RecordingHandler(CIBLE, {TACHE}))
        passerelle = InterAgentGateway(
            api_session_factory,
            handlers=registry,
            settings=Settings(agent_max_collaboration_depth=2),
        )
        correlation = "corr-boucle"

        passerelle.request(
            APPELANT_DEF, to_agent=CIBLE, task_type=TACHE, correlation_id=correlation
        )
        passerelle.request(
            APPELANT_DEF, to_agent=CIBLE, task_type=TACHE, correlation_id=correlation
        )

        with pytest.raises(AgentLoopError) as erreur:
            passerelle.request(
                APPELANT_DEF,
                to_agent=CIBLE,
                task_type=TACHE,
                correlation_id=correlation,
            )

        assert erreur.value.details["profondeur_max"] == 2
        assert len(_tasks(api_session_factory)) == 2  # le 3e appel n'est pas tracé

    def test_cycle_de_lineage_refuse(self, gateway, registry, api_session_factory) -> None:
        registry.register(RecordingHandler(CIBLE, {TACHE}))

        with pytest.raises(AgentLoopError) as erreur:
            gateway.request(
                APPELANT_DEF,
                to_agent=CIBLE,
                task_type=TACHE,
                constraints={"lineage": ["AgentAppelant", "AgentCible"]},
            )

        assert erreur.value.details["lineage"] == ["AgentAppelant", "AgentCible"]
        assert _tasks(api_session_factory) == []


# --- Timeout (budget de temps) --------------------------------------------------


class TestTimeout:
    def test_budget_depasse_marque_la_tache_timeout(
        self, registry, api_session_factory
    ) -> None:
        registry.register(RecordingHandler(CIBLE, {TACHE}))
        passerelle = InterAgentGateway(
            api_session_factory,
            handlers=registry,
            settings=Settings(agent_task_timeout_seconds=10.0),
            clock=ScriptedClock(0.0, 42.0),
        )

        with pytest.raises(AgentTimeoutError) as erreur:
            passerelle.request(APPELANT_DEF, to_agent=CIBLE, task_type=TACHE)

        assert erreur.value.details["duree_secondes"] == 42.0
        trace = _tasks(api_session_factory)[0]
        assert trace.status == "timeout"
        assert trace.result == {"error": "timeout"}

    def test_budget_respecte_ne_marque_pas_timeout(
        self, registry, api_session_factory
    ) -> None:
        registry.register(RecordingHandler(CIBLE, {TACHE}))
        passerelle = InterAgentGateway(
            api_session_factory,
            handlers=registry,
            settings=Settings(agent_task_timeout_seconds=10.0),
            clock=ScriptedClock(0.0, 3.0),
        )

        reponse = passerelle.request(APPELANT_DEF, to_agent=CIBLE, task_type=TACHE)

        assert reponse.status == "completed"
        assert _tasks(api_session_factory)[0].status == "completed"


# --- Réponse invalide -----------------------------------------------------------


class TestReponseInvalide:
    def test_reponse_hors_contrat_refusee(self, registry, api_session_factory) -> None:
        registry.register(RecordingHandler(CIBLE, {TACHE}, response={"status": "ok"}))
        passerelle = InterAgentGateway(api_session_factory, handlers=registry)

        with pytest.raises(InvalidAgentResponseError):
            passerelle.request(APPELANT_DEF, to_agent=CIBLE, task_type=TACHE)

        assert _tasks(api_session_factory)[0].status == "failed"

    def test_task_id_incoherent_refuse(self, registry, api_session_factory) -> None:
        registry.register(
            RecordingHandler(
                CIBLE,
                {TACHE},
                response=AgentResponse(
                    task_id=str(uuid4()), status="completed", result={"ok": True}
                ),
            )
        )
        passerelle = InterAgentGateway(api_session_factory, handlers=registry)

        with pytest.raises(InvalidAgentResponseError) as erreur:
            passerelle.request(APPELANT_DEF, to_agent=CIBLE, task_type=TACHE)

        details = erreur.value.details
        assert details["task_id_recu"] != details["task_id_attendu"]

    def test_statut_non_terminal_refuse(self, registry, api_session_factory) -> None:
        class HandlerStatut:
            agent_id = CIBLE

            def supported_task_types(self) -> frozenset[str]:
                return frozenset({TACHE})

            def handle(self, request: AgentRequest) -> AgentResponse:
                return AgentResponse(task_id=request.task_id, status="failed", result={})

        registry.register(HandlerStatut())
        passerelle = InterAgentGateway(api_session_factory, handlers=registry)

        with pytest.raises(InvalidAgentResponseError):
            passerelle.request(APPELANT_DEF, to_agent=CIBLE, task_type=TACHE)

        assert _tasks(api_session_factory)[0].status == "failed"

    def test_echec_du_handler_trace_failed(self, registry, api_session_factory) -> None:
        registry.register(RecordingHandler(CIBLE, {TACHE}, error=RuntimeError("panne")))
        passerelle = InterAgentGateway(api_session_factory, handlers=registry)

        with pytest.raises(RuntimeError, match="panne"):
            passerelle.request(APPELANT_DEF, to_agent=CIBLE, task_type=TACHE)

        trace = _tasks(api_session_factory)[0]
        assert trace.status == "failed"
        assert trace.result == {"error": "RuntimeError"}


# --- Absence de transfert de privilèges -----------------------------------------


class TestAucunTransfertDePrivileges:
    def test_la_demande_ne_porte_aucun_privilege(self, gateway, registry) -> None:
        """La demande inter-agent ne transporte ni politique ni jeton d'appel."""
        handler = RecordingHandler(CIBLE, {TACHE})
        registry.register(handler)

        gateway.request(APPELANT_DEF, to_agent=CIBLE, task_type=TACHE)

        champs = {champ.name for champ in dataclasses.fields(AgentRequest)}
        assert champs == {
            "task_id",
            "correlation_id",
            "from_agent",
            "to_agent",
            "task_type",
            "payload",
            "constraints",
            "requires_approval",
        }
        assert "policy" not in champs
        assert "capabilities" not in champs

    def test_l_agent_appele_reverifie_ses_propres_permissions(
        self, registry, api_session_factory
    ) -> None:
        """Un appelant privilégié n'obtient pas les droits de l'agent appelé."""
        cible_stricte = definition(CIBLE, capabilities=())  # refus par défaut

        class HandlerStrict:
            @property
            def agent_id(self) -> str:
                return CIBLE

            def supported_task_types(self) -> frozenset[str]:
                return frozenset({TACHE})

            def handle(self, request: AgentRequest) -> AgentResponse:
                cible_stricte.policy.require(CIBLE, "write_officiel")
                raise AssertionError("l'agent appelé ne doit pas franchir sa frontière")

        registre = AgentHandlerRegistry()
        registre.register(HandlerStrict())
        passerelle = InterAgentGateway(api_session_factory, handlers=registre)

        with pytest.raises(PermissionDeniedError):
            passerelle.request(APPELANT_DEF, to_agent=CIBLE, task_type=TACHE)


# --- Relecture d'une tâche (get_agent_task_result) ------------------------------


class TestGetAgentTaskResult:
    def _deleguer(self, passerelle, registre) -> str:
        registre.register(RecordingHandler(CIBLE, {TACHE}, result={"ok": 1}))
        return passerelle.request(
            APPELANT_DEF, to_agent=CIBLE, task_type=TACHE
        ).task_id

    def test_appelant_relit_sa_tache(self, gateway, registry, api_session_factory) -> None:
        task_id = self._deleguer(gateway, registry)

        reponse = gateway.get_result(APPELANT_DEF, task_id)

        assert reponse.task_id == task_id
        assert reponse.status == "completed"
        assert reponse.result == {"ok": 1}

    def test_agent_etranger_ne_lit_pas_la_tache(self, gateway, registry) -> None:
        task_id = self._deleguer(gateway, registry)
        etranger = definition("AgentEtranger")

        with pytest.raises(PermissionDeniedError):
            gateway.get_result(etranger, task_id)

    def test_capacite_de_lecture_absente_refusee(self, gateway, registry) -> None:
        task_id = self._deleguer(gateway, registry)
        sans_capacite = definition(APPELANT, capabilities=())

        with pytest.raises(PermissionDeniedError):
            gateway.get_result(sans_capacite, task_id)

    def test_tache_inexistante_leve_not_found(self, gateway) -> None:
        with pytest.raises(NotFoundError):
            gateway.get_result(APPELANT_DEF, uuid4())

    def test_tache_en_cours_refusee(self, gateway, api_session_factory) -> None:
        session = api_session_factory()
        try:
            task = AgentTaskService(session).start(
                from_agent=APPELANT,
                to_agent=CIBLE,
                task_type=TACHE,
                payload={},
                correlation_id="corr-en-cours",
            )
            task_id = str(task.id)
            session.commit()
        finally:
            session.close()

        with pytest.raises(ConflictError):
            gateway.get_result(APPELANT_DEF, task_id)


# --- Tools de collaboration (instruction/05 §10) --------------------------------


class TestToolsDeCollaboration:
    def test_noms_et_sortie_json(self, gateway, registry) -> None:
        registry.register(RecordingHandler(CIBLE, {TACHE}, result={"ok": 1}))
        request_tool, get_tool = build_collaboration_tools(
            caller=APPELANT_DEF, gateway=gateway
        )

        assert request_tool.name == REQUEST_TASK_CAPABILITY
        assert get_tool.name == GET_RESULT_CAPABILITY

        sortie = request_tool.invoke(
            {"to_agent": CIBLE, "task_type": TACHE, "payload": {"mission": "M-1"}}
        )
        json.dumps(sortie)  # aucune conversion nécessaire : sortie sérialisable
        assert sortie["status"] == "completed"
        assert sortie["result"] == {"ok": 1}
        assert sortie["requires_approval"] is True

        relu = get_tool.invoke({"task_id": sortie["task_id"]})
        assert relu["task_id"] == sortie["task_id"]
        assert relu["result"] == {"ok": 1}

    def test_tools_non_declares_refuses_par_le_contrat(self, gateway) -> None:
        outils = build_collaboration_tools(caller=APPELANT_DEF, gateway=gateway)

        with pytest.raises(ValidationError):
            allowed_tools_only(definition(CIBLE, tools=()), outils)

        autorises = allowed_tools_only(
            definition(CIBLE, tools=(REQUEST_TASK_CAPABILITY, GET_RESULT_CAPABILITY)),
            outils,
        )
        assert [tool.name for tool in autorises] == [
            REQUEST_TASK_CAPABILITY,
            GET_RESULT_CAPABILITY,
        ]

    def test_entree_de_tool_validee(self, gateway) -> None:
        request_tool, _ = build_collaboration_tools(caller=APPELANT_DEF, gateway=gateway)

        sortie = request_tool.invoke({"to_agent": CIBLE})  # task_type obligatoire
        assert sortie["ok"] is False
        assert sortie["error"]["code"] == "business_validation_error"


# --- Contrat de collaboration (refus par défaut) --------------------------------


class TestContratDeCollaboration:
    def test_aucun_grant_aucune_delegation(self) -> None:
        assert definition(CIBLE).granted_task_types(CIBLE) == frozenset()

    def test_collaborates_with_liste_les_cibles(self) -> None:
        assert APPELANT_DEF.collaborates_with() == (CIBLE,)

    def test_agent_sans_collaboration(self) -> None:
        """Un agent sans grant ne gagne aucune capacité de délégation."""
        from app.agents.agent_statistique import DEFINITION as STATISTIQUE_DEFINITION

        assert STATISTIQUE_DEFINITION.collaboration == ()
        assert STATISTIQUE_DEFINITION.collaborates_with() == ()
        assert not STATISTIQUE_DEFINITION.policy.allows(REQUEST_TASK_CAPABILITY)
        assert not STATISTIQUE_DEFINITION.policy.allows(GET_RESULT_CAPABILITY)


# --- Handler réel (registre) : run() + stream intact ----------------------------


class TestRegistreDesAgentsAppelables:
    def test_registre_expose_rh_seulement(self, api_session_factory) -> None:
        """L'analyseur a été supprimé : le RH reste le seul agent appelable."""
        from app.agents.agent_rh import DEFINITION as RH_DEF
        from app.agents.collaboration_registre import construire_registre

        registre = construire_registre(api_session_factory)
        assert set(registre.ids()) == {RH_DEF.agent_id}

    def test_aucun_agent_supprime_ne_reste_appelable(self, api_session_factory) -> None:
        from app.agents.collaboration_registre import construire_registre

        registre = construire_registre(api_session_factory)
        assert not any("analyseur" in agent_id for agent_id in registre.ids())


class TestToolA2ANexplosePasLeStream:
    def test_exception_handler_devient_tool_message(
        self, gateway, registry
    ) -> None:
        """Un échec interne du handler (KeyError, etc.) ne doit pas tuer le
        stream AG-UI : le tool renvoie ``{ok: false}``."""
        registry.register(
            RecordingHandler(CIBLE, {TACHE}, error=KeyError("extraction"))
        )
        request_tool, _ = build_collaboration_tools(
            caller=APPELANT_DEF, gateway=gateway
        )

        sortie = request_tool.invoke(
            {"to_agent": CIBLE, "task_type": TACHE, "payload": {}}
        )

        assert sortie["ok"] is False
        assert "error" in sortie
        assert sortie["error"]["code"] == "business_validation_error"
