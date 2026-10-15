"""Tests du kit d'outils d'agent (Lot A1) — la seam profonde unique.

Le kit est testé isolément : refus de mutation en readonly à la
construction, refus des doublons, capacité exigée, transaction par tool
(commit/rollback), sérialisation JSON, projection d'entité sans fuite.
Aucun LLM, aucun graphe — interface = test surface.
"""

from typing import Any
from uuid import UUID

import pytest
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.agents.base import AgentDefinition
from app.agents.toolkit import (
    KIT_CAPABILITY,
    AgentTools,
    OperationSpec,
    PageSearchInput,
    serialiser_entite,
)
from app.core.errors import PermissionDeniedError
from app.core.errors import ValidationError as BusinessValidationError
from app.domain.organization import Organisation
from app.tools.permissions import PermissionPolicy


class _EchoInput(BaseModel):
    texte: str


def _definition(*, capacites: frozenset[str]) -> AgentDefinition:
    return AgentDefinition(
        agent_id="agent_test",
        display_name="Agent Test",
        description="Agent de test du kit.",
        policy=PermissionPolicy(allowed_capabilities=capacites),
        capabilities=tuple(sorted(capacites)),
    )


def _operation_lecture() -> OperationSpec:
    def run(session: Session, payload: dict[str, Any]) -> dict[str, Any]:
        _ = session
        return {"echo": payload["texte"]}

    return OperationSpec(
        name="echo",
        description="Renvoie le texte.",
        input_schema=_EchoInput,
        run=run,
    )


def _operation_mutation() -> OperationSpec:
    def run(session: Session, payload: dict[str, Any]) -> dict[str, Any]:
        _ = session
        return {"ecrit": True}

    return OperationSpec(
        name="ecrire",
        description="Écrit quelque chose.",
        input_schema=_EchoInput,
        mutation=True,
        run=run,
    )


def test_readonly_refuse_mutation_a_la_construction() -> None:
    kit = AgentTools(_definition(capacites=frozenset({KIT_CAPABILITY})), readonly=True)
    with pytest.raises(PermissionDeniedError):
        kit.enregistrer(_operation_mutation())
    # la lecture reste autorisée
    kit.enregistrer(_operation_lecture())
    assert kit.noms() == ["echo"]


def test_kit_mutable_accepte_tout() -> None:
    kit = AgentTools(_definition(capacites=frozenset({KIT_CAPABILITY})))
    kit.enregistrer(_operation_mutation())
    kit.enregistrer(_operation_lecture())
    assert kit.noms() == ["echo", "ecrire"]
    assert len(kit.construire()) == 2


def test_doublon_refuse() -> None:
    kit = AgentTools(_definition(capacites=frozenset({KIT_CAPABILITY})))
    kit.enregistrer(_operation_lecture())
    with pytest.raises(BusinessValidationError):
        kit.enregistrer(_operation_lecture())


def test_capacite_absente_refusee_a_la_construction() -> None:
    with pytest.raises(PermissionDeniedError):
        AgentTools(_definition(capacites=frozenset()))


def test_tool_execute_dans_sa_transaction() -> None:
    """Le handler ouvre sa session via la fabrique et la referme (le commit
    appartient à l'``unit_of_work`` du kit, pas au handler)."""
    appels: list[str] = []

    class _SessionFactice:
        def commit(self) -> None:
            appels.append("commit")

        def rollback(self) -> None:
            appels.append("rollback")

        def close(self) -> None:
            appels.append("close")

    vues: list[Any] = []

    def run(session: Session, payload: dict[str, Any]) -> dict[str, Any]:
        vues.append(session)
        return {"echo": payload["texte"]}

    kit = AgentTools(
        _definition(capacites=frozenset({KIT_CAPABILITY})),
        session_factory=_SessionFactice,  # type: ignore[arg-type]
    )
    kit.enregistrer(
        OperationSpec(name="echo", description="Echo.", input_schema=_EchoInput, run=run)
    )
    (tool,) = kit.construire()
    assert tool.invoke({"texte": "bonjour"}) == {"ok": True, "echo": "bonjour"}
    assert len(vues) == 1 and isinstance(vues[0], _SessionFactice)
    assert appels == ["commit", "close"]


def test_payload_invalide_refuse_par_le_schema() -> None:

    kit = AgentTools(_definition(capacites=frozenset({KIT_CAPABILITY})))
    kit.enregistrer(_operation_lecture())
    (tool,) = kit.construire()
    sortie = tool.invoke({})
    assert sortie["ok"] is False
    assert sortie["error"]["code"] == "business_validation_error"


def test_serialiser_entite_projection_stricte() -> None:
    org = Organisation(nom="Acme", email="c@acme.ma")
    projete = serialiser_entite(org, "nom", "email", "champ_inexistant")
    assert projete == {"nom": "Acme", "email": "c@acme.ma"}
    # aucun champ non demandé ne fuit
    assert "id" not in projete


def test_serialiser_uuid() -> None:
    assert serialiser_entite({"cle": UUID(int=0)}, "cle") == {
        "cle": "00000000-0000-0000-0000-000000000000"
    }


def test_pagination_bornee() -> None:
    from pydantic import ValidationError as PydanticValidationError

    assert PageSearchInput.model_validate({}).limite == 20
    # limite excessive : resserrée au plafond, jamais rejetée
    assert PageSearchInput.model_validate({"limite": 200}).limite == 100
    with pytest.raises(PydanticValidationError):
        PageSearchInput.model_validate({"limite": 0})


def test_kit_sur_sqlite_memoire() -> None:
    """La plomberie transactionnelle fonctionne sur une vraie session."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.domain.base import Base

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)

    kit = AgentTools(
        _definition(capacites=frozenset({KIT_CAPABILITY})),
        readonly=True,
        session_factory=factory,
    )
    kit.enregistrer(_operation_lecture())
    (tool,) = kit.construire()
    assert tool.invoke({"texte": "ping"}) == {"ok": True, "echo": "ping"}
    engine.dispose()
