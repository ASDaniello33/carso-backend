"""Collisions d'IDs AG-UI : call_0 réutilisé d'un tour à l'autre."""

from __future__ import annotations

from types import SimpleNamespace

from app.api.agui_ids import CorrespondanceIds, ids_historique


def _entree(*, messages, run_id="run-b", resume=None):
    return SimpleNamespace(messages=messages, run_id=run_id, resume=resume)


def test_ids_historique_collecte_messages_et_appels() -> None:
    vus = ids_historique(
        [
            {
                "id": "msg-1",
                "role": "assistant",
                "tool_calls": [{"id": "call_0", "function": {"name": "search_offres"}}],
            },
            {"id": "tool-1", "role": "tool", "tool_call_id": "call_0"},
        ]
    )
    assert vus == {"msg-1", "call_0", "tool-1"}


def test_start_outil_collisionne_est_suffixe_par_run() -> None:
    historique = [
        {
            "id": "msg-1",
            "toolCalls": [{"id": "call_0"}],
        }
    ]
    mapper = CorrespondanceIds.depuis_entree(_entree(messages=historique))
    start = SimpleNamespace(
        type="TOOL_CALL_START",
        tool_call_id="call_0",
        tool_call_name="get_lot",
        parent_message_id="msg-2",
        message_id=None,
        messages=None,
    )
    args = SimpleNamespace(
        type="TOOL_CALL_ARGS",
        tool_call_id="call_0",
        parent_message_id=None,
        message_id=None,
        messages=None,
    )
    result = SimpleNamespace(
        type="TOOL_CALL_RESULT",
        tool_call_id="call_0",
        message_id="tool-new",
        parent_message_id=None,
        messages=None,
    )

    start_out = mapper.appliquer(start)
    args_out = mapper.appliquer(args)
    result_out = mapper.appliquer(result)

    assert start_out.tool_call_id == "call_0::run-b"
    assert args_out.tool_call_id == "call_0::run-b"
    assert result_out.tool_call_id == "call_0::run-b"
    assert start_out.parent_message_id == "msg-2"


def test_id_inconnu_reste_verbatim() -> None:
    mapper = CorrespondanceIds.depuis_entree(_entree(messages=[]))
    start = SimpleNamespace(
        type="TOOL_CALL_START",
        tool_call_id="call_0",
        parent_message_id=None,
        message_id=None,
        messages=None,
    )
    assert mapper.appliquer(start).tool_call_id == "call_0"


def test_reprise_hitl_ne_renomme_pas() -> None:
    """Resume sans interruptId (ancien canal forwardedProps) : figer tel quel."""
    historique = [{"id": "msg-1", "tool_calls": [{"id": "call_0"}]}]
    mapper = CorrespondanceIds.depuis_entree(
        _entree(messages=historique, resume=[{"decisions": []}])
    )
    start = SimpleNamespace(
        type="TOOL_CALL_START",
        tool_call_id="call_0",
        parent_message_id=None,
        message_id=None,
        messages=None,
    )
    assert mapper.appliquer(start).tool_call_id == "call_0"


def test_reprise_hitl_reprend_le_tool_call_comme_nouvelle_carte() -> None:
    """Régression (formulaire invisible après reprise HITL) : le tool call du
    tour repris porte le MÊME id brut que celui du tour interrompu déjà affiché
    (call_0…). Figer le renommage faisait que le provider client réutilisait
    l'ancienne entrée (arguments/resultat du tour interrompu) : la carte
    restait vide alors que le backend voyait son stub répondre ok. L'id repris
    est désormais suffixé avec l'interruptId → nouvelle entrée, nouveau rendu."""
    historique = [
        {
            "id": "msg-1",
            "tool_calls": [{"id": "call_0"}],
            "role": "assistant",
        }
    ]
    mapper = CorrespondanceIds.depuis_entree(
        _entree(messages=historique, resume=[{"interruptId": "interrupt-1"}])
    )
    start = SimpleNamespace(
        type="TOOL_CALL_START",
        tool_call_id="call_0",
        parent_message_id="msg-1",
        message_id=None,
        messages=None,
    )
    renomme = mapper.appliquer(start).tool_call_id
    assert renomme != "call_0"
    # Le suffixe distingue la reprise du tour interrompu (ici, le run_id).
    assert renomme.startswith("call_0::")

    # ARGS / END suivent la table : même id public que le START.
    args = SimpleNamespace(
        type="TOOL_CALL_ARGS",
        tool_call_id="call_0",
        parent_message_id=None,
        message_id=None,
        messages=None,
    )
    assert mapper.appliquer(args).tool_call_id == renomme

    # Le snapshot réécrit le tool call du message repris vers l'id public.
    snapshot = SimpleNamespace(
        type="MESSAGES_SNAPSHOT",
        tool_call_id=None,
        parent_message_id=None,
        message_id=None,
        messages=[
            {
                "id": "msg-2",
                "role": "assistant",
                "toolCalls": [
                    {
                        "id": "call_0",
                        "function": {
                            "name": "poser_questionnaire",
                            "arguments": "{}",
                        },
                    }
                ],
            }
        ],
    )
    reecrit = mapper.appliquer(snapshot)
    assert reecrit.messages[0]["toolCalls"][0]["id"] == renomme


def test_deuxieme_start_du_meme_run_est_minte() -> None:
    """call_0 réutilisé au 2e tour modèle du même run : nouvelle carte."""
    mapper = CorrespondanceIds.depuis_entree(_entree(messages=[]))
    premier = SimpleNamespace(
        type="TOOL_CALL_START",
        tool_call_id="call_0",
        parent_message_id=None,
        message_id=None,
        messages=None,
    )
    premier_args = SimpleNamespace(
        type="TOOL_CALL_ARGS",
        tool_call_id="call_0",
        parent_message_id=None,
        message_id=None,
        messages=None,
    )
    second = SimpleNamespace(
        type="TOOL_CALL_START",
        tool_call_id="call_0",
        parent_message_id=None,
        message_id=None,
        messages=None,
    )
    second_args = SimpleNamespace(
        type="TOOL_CALL_ARGS",
        tool_call_id="call_0",
        parent_message_id=None,
        message_id=None,
        messages=None,
    )
    assert mapper.appliquer(premier).tool_call_id == "call_0"
    assert mapper.appliquer(premier_args).tool_call_id == "call_0"
    assert mapper.appliquer(second).tool_call_id == "call_0::run-b"
    assert mapper.appliquer(second_args).tool_call_id == "call_0::run-b"


def test_chunk_ne_remint_pas() -> None:
    mapper = CorrespondanceIds.depuis_entree(_entree(messages=[]))
    start = SimpleNamespace(
        type="TOOL_CALL_START",
        tool_call_id="call_0",
        parent_message_id=None,
        message_id=None,
        messages=None,
    )
    chunk = SimpleNamespace(
        type="TOOL_CALL_CHUNK",
        tool_call_id="call_0",
        parent_message_id=None,
        message_id=None,
        messages=None,
    )
    assert mapper.appliquer(start).tool_call_id == "call_0"
    assert mapper.appliquer(chunk).tool_call_id == "call_0"


def test_snapshot_suit_la_table() -> None:
    historique = [{"id": "ancien", "tool_calls": [{"id": "call_0"}]}]
    mapper = CorrespondanceIds.depuis_entree(_entree(messages=historique))
    mapper.appliquer(
        SimpleNamespace(
            type="TOOL_CALL_START",
            tool_call_id="call_0",
            parent_message_id=None,
            message_id=None,
            messages=None,
        )
    )
    snapshot = SimpleNamespace(
        type="MESSAGES_SNAPSHOT",
        tool_call_id=None,
        parent_message_id=None,
        message_id=None,
        messages=[
            {
                "id": "nouveau",
                "tool_calls": [{"id": "call_0", "function": {"name": "get_lot"}}],
            },
            {"id": "tool-new", "role": "tool", "tool_call_id": "call_0"},
        ],
    )
    out = mapper.appliquer(snapshot)
    assert out.messages[0]["tool_calls"][0]["id"] == "call_0::run-b"
    assert out.messages[1]["tool_call_id"] == "call_0::run-b"
    assert out.messages[0]["id"] == "nouveau"


def test_snapshot_deux_tours_meme_call_id() -> None:
    mapper = CorrespondanceIds.depuis_entree(_entree(messages=[]))
    for _ in range(2):
        mapper.appliquer(
            SimpleNamespace(
                type="TOOL_CALL_START",
                tool_call_id="call_0",
                parent_message_id=None,
                message_id=None,
                messages=None,
            )
        )
    snapshot = SimpleNamespace(
        type="MESSAGES_SNAPSHOT",
        tool_call_id=None,
        parent_message_id=None,
        message_id=None,
        messages=[
            {"id": "a", "tool_calls": [{"id": "call_0"}]},
            {"id": "ta", "role": "tool", "tool_call_id": "call_0"},
            {"id": "b", "tool_calls": [{"id": "call_0"}]},
            {"id": "tb", "role": "tool", "tool_call_id": "call_0"},
        ],
    )
    out = mapper.appliquer(snapshot)
    assert out.messages[0]["tool_calls"][0]["id"] == "call_0"
    assert out.messages[1]["tool_call_id"] == "call_0"
    assert out.messages[2]["tool_calls"][0]["id"] == "call_0::run-b"
    assert out.messages[3]["tool_call_id"] == "call_0::run-b"
