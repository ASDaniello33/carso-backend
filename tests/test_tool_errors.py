"""Erreurs métier → payload / Tool Message (doc LangChain handle_tool_error)."""

from __future__ import annotations

import json

from pydantic import BaseModel

from app.core.errors import NotFoundError, PermissionDeniedError
from app.tools.base import TypedTool, to_langchain_tool


class _In(BaseModel):
    x: str = "ok"


def test_invoke_carso_error_devient_payload() -> None:
    def _fail(payload: dict) -> dict:
        raise NotFoundError("Document introuvable")

    tool = TypedTool(
        name="lire",
        description="Lecture.",
        input_schema=_In,
        handler=_fail,
    )
    sortie = tool.invoke({"x": "a"})
    assert sortie["ok"] is False
    assert sortie["error"]["code"] == "not_found"
    assert "introuvable" in sortie["error"]["message"]


def test_invoke_succes_enveloppe_ok() -> None:
    tool = TypedTool(
        name="echo",
        description="Echo.",
        input_schema=_In,
        handler=lambda p: {"echo": p["x"]},
    )
    assert tool.invoke({"x": "hi"}) == {"ok": True, "echo": "hi"}


def test_langchain_tool_erreur_est_un_message_pas_un_crash() -> None:
    """handle_tool_error : le run LangChain ne lève pas, il renvoie le JSON."""

    def _fail(payload: dict) -> dict:
        raise PermissionDeniedError("pas le droit")

    tool = TypedTool(
        name="secret",
        description="Secret.",
        input_schema=_In,
        handler=_fail,
    )
    lc = to_langchain_tool(tool)
    assert lc.handle_tool_error is True
    brut = lc.invoke({"x": "a"})
    donnees = json.loads(brut) if isinstance(brut, str) else brut
    assert donnees["ok"] is False
    assert donnees["error"]["code"] == "permission_denied"


def test_schema_base_model_brut_refuse() -> None:
    """BaseModel lui-même n'est pas un schéma : LangChain plante sur
    tool_call_schema (AttributeError: __pydantic_generic_metadata__)."""
    from app.core.errors import ValidationError as BusinessValidationError

    tool = TypedTool(
        name="vide",
        description="Sans schéma.",
        input_schema=BaseModel,
        handler=lambda p: p,
    )
    try:
        to_langchain_tool(tool)
    except BusinessValidationError as exc:
        assert "vide" in str(exc)
    else:
        raise AssertionError("BaseModel brut aurait dû être refusé")


def test_exception_inattendue_n_est_pas_avalee() -> None:
    def _boom(payload: dict) -> dict:
        raise RuntimeError("bug interne")

    tool = TypedTool(
        name="boom",
        description="Boom.",
        input_schema=_In,
        handler=_boom,
    )
    try:
        tool.invoke({"x": "a"})
    except RuntimeError as exc:
        assert "bug interne" in str(exc)
    else:
        raise AssertionError("RuntimeError aurait dû remonter")
