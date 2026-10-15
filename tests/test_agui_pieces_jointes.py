"""Pièces jointes du chat AG-UI : du binaire à la référence documentaire.

Vérifie la règle métier : un fichier joint dans le chat est un **document
CARSO** déposé via l'API (``POST /documents``) ; le modèle ne reçoit jamais ses
octets, seulement ``[Pièce jointe : nom — document <id>]``. L'agent lit ensuite
le document par ses propres outils, sous ses permissions.

Le module est pur (aucune base, aucun réseau) : la normalisation est testée
directement, puis le middleware est exercé comme un ASGI réel — l'app interne
capture le corps qu'elle reçoit.
"""

from __future__ import annotations

import json

from fastapi import FastAPI
from starlette.testclient import TestClient

from app.api.agui_pieces_jointes import (
    MiddlewarePiecesJointes,
    normaliser_message,
    normaliser_pieces_jointes,
    reecrire_corps,
)
from app.main import app as app_carso

# Fragment d'image réel : s'il ressortait dans le corps transmis, le test le
# verrait — c'est exactement ce qu'on ne veut jamais envoyer au modèle.
BASE64_IMAGE = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def _message_piece_jointe() -> dict:
    """Message utilisateur tel que CopilotKit l'envoie après un dépôt réussi."""
    return {
        "role": "user",
        "id": "m-1",
        "content": [
            {"type": "text", "text": "Voici l'appel à proposition reçu."},
            {
                "type": "binary",
                "source": {
                    "type": "url",
                    "value": "document://8f1c5d2e-0000-4000-8000-000000000001",
                    "mimeType": "application/pdf",
                },
                "metadata": {
                    "filename": "appel-2026.pdf",
                    "document_id": "8f1c5d2e-0000-4000-8000-000000000001",
                    "type_document": "appel_proposition",
                },
            },
        ],
    }


def _payload_piece_jointe() -> dict:
    return {
        "threadId": "fil-1",
        "runId": "run-1",
        "messages": [_message_piece_jointe()],
        "tools": [],
        "context": [],
        "state": {},
        "forwardedProps": {},
    }


# --- Normalisation ---------------------------------------------------------------


def test_message_textuel_unique_reste_intact() -> None:
    """Un message sans pièce jointe n'est jamais réécrit (même objet)."""
    message = {"role": "user", "content": [{"type": "text", "text": "Bonjour"}]}
    assert normaliser_message(message) is message


def test_piece_jointe_devient_reference_documentaire() -> None:
    """Le binaire disparaît au profit d'une ligne de texte référençant le document."""
    normalise = normaliser_message(_message_piece_jointe())

    contenu = normalise["content"]
    assert isinstance(contenu, str)
    assert "Voici l'appel à proposition reçu." in contenu
    assert "Pièce jointe : appel-2026.pdf" in contenu
    assert "document 8f1c5d2e-0000-4000-8000-000000000001" in contenu
    assert "type appel_proposition" in contenu
    # Aucun contenu binaire, aucune URL locale ne traverse.
    assert BASE64_IMAGE not in contenu
    assert "data:" not in contenu


def test_reference_deduite_de_la_source_quand_les_metadonnees_manquent() -> None:
    """``document://<id>`` suffit : la référence reste exploitable par l'agent."""
    message = {
        "role": "user",
        "content": [
            {
                "type": "image",
                "source": {"type": "url", "value": "document://doc-42"},
            }
        ],
    }
    assert normaliser_message(message)["content"] == "[Pièce jointe · document doc-42]"


def test_message_sans_piece_jointe_rendu_tel_quel() -> None:
    """Sans binaire, la charge utile est rendue à l'identique (pas de copie)."""
    payload = {"messages": [{"role": "user", "content": [{"type": "text", "text": "Salut"}]}]}
    assert normaliser_pieces_jointes(payload) is payload


def test_corps_json_invalide_laisse_intact() -> None:
    """Un corps illisible est transmis tel quel : jamais de panne de conversation."""
    assert reecrire_corps(b"pas du json") == b"pas du json"
    assert reecrire_corps(b'"une chaine"') == b'"une chaine"'


def test_reecriture_retire_le_binaire_du_corps() -> None:
    corps = json.dumps(_payload_piece_jointe()).encode("utf-8")

    def _sans_base() -> object:
        raise RuntimeError("pas de base")

    reecrit = reecrire_corps(corps, session_factory=_sans_base)

    assert reecrit != corps
    assert BASE64_IMAGE.encode() not in reecrit
    payload = json.loads(reecrit)
    assert "document 8f1c5d2e-0000-4000-8000-000000000001" in payload["messages"][0]["content"]


def test_reecriture_injecte_fiche_et_extrait(monkeypatch) -> None:
    """Le même tour porte la fiche + un extrait dans ``context``, sans octets."""
    from types import SimpleNamespace
    from uuid import UUID

    from app.application.dto import ExtractedDocument

    doc_id = UUID("8f1c5d2e-0000-4000-8000-000000000001")

    class _Doc:
        id = doc_id
        nom = "appel-2026.pdf"
        type_document = "appel_proposition"
        statut = "draft"
        version = 1
        mime_type = "application/pdf"
        taille_octets = 100
        appel_a_proposition_id = UUID("aaaaaaaa-0000-4000-8000-000000000001")
        offre_id = None
        mission_id = None
        equipe_id = None
        session_id = None
        organisation_id = None

    class _Service:
        def __init__(self, _session: object) -> None:
            pass

        def obtenir(self, identifiant: UUID) -> _Doc:
            assert identifiant == doc_id
            return _Doc()

        def extraire_texte(self, identifiant: UUID) -> ExtractedDocument:
            return ExtractedDocument(
                document_id=identifiant,
                nom="appel-2026.pdf",
                extension=".pdf",
                mime_type="application/pdf",
                texte="MISSION CARSO 2026",
                adaptateur="pdf",
                nb_caracteres=18,
                nb_pages=1,
                nb_feuilles=None,
                feuilles=(),
                tronque=False,
            )

    monkeypatch.setattr(
        "app.application.services.document_service.DocumentService",
        _Service,
    )
    corps = json.dumps(_payload_piece_jointe()).encode("utf-8")
    reecrit = reecrire_corps(corps, session_factory=lambda: SimpleNamespace(close=lambda: None))
    payload = json.loads(reecrit)

    assert BASE64_IMAGE.encode() not in reecrit
    assert payload["context"]
    assert "MISSION CARSO 2026" in payload["context"][0]["value"]
    assert "Ne redemande pas" in payload["context"][0]["value"]


# --- Middleware ASGI --------------------------------------------------------------


def _app_espion(captures: list[bytes]) -> FastAPI:
    """App interne qui mémorise le corps réellement reçu après normalisation."""
    interne = FastAPI()

    @interne.post("/api/v1/agui/{agent_id}")
    async def _agui_agent(agent_id: str, corps: dict) -> dict:  # noqa: ARG001
        captures.append(json.dumps(corps).encode("utf-8"))
        return {"ok": True}

    @interne.post("/api/v1/appels-a-proposition")
    async def _metier(corps: dict) -> dict:
        captures.append(json.dumps(corps).encode("utf-8"))
        return {"ok": True}

    return interne


def _client_middleware() -> tuple[TestClient, list[bytes]]:
    captures: list[bytes] = []
    enveloppe = MiddlewarePiecesJointes(_app_espion(captures), prefixe="/api/v1")
    return TestClient(enveloppe), captures


def test_middleware_normalise_le_corps_agui_transmis() -> None:
    """L'app interne ne voit jamais la partie binaire — et le corps reste valide."""
    client, captures = _client_middleware()

    reponse = client.post(
        "/api/v1/agui/agent_generateur_offre", json=_payload_piece_jointe()
    )

    assert reponse.status_code == 200
    assert len(captures) == 1
    corps = captures[0]
    assert BASE64_IMAGE.encode() not in corps
    payload = json.loads(corps)
    assert "document 8f1c5d2e-0000-4000-8000-000000000001" in payload["messages"][0]["content"]


def test_middleware_ne_touche_pas_le_catalogue() -> None:
    """``GET {base}/agui/`` est un catalogue : aucune normalisation, aucun corps."""
    captures: list[bytes] = []
    interne = FastAPI()

    @interne.get("/api/v1/agui/")
    async def _catalogue() -> list[dict]:
        return [{"agent_id": "agent_rh"}]

    client = TestClient(MiddlewarePiecesJointes(interne, prefixe="/api/v1"))
    assert client.get("/api/v1/agui/").json() == [{"agent_id": "agent_rh"}]
    assert captures == []


def test_middleware_ne_touche_pas_les_routes_metier() -> None:
    """Les routes REST ordinaires gardent leur corps d'origine."""
    payload = {"nom": "Appel 2026", "organisation_id": "org-1"}
    client, captures = _client_middleware()

    assert client.post("/api/v1/appels-a-proposition", json=payload).status_code == 200
    assert json.loads(captures[0]) == payload


def test_middleware_relaye_receive_apres_le_corps() -> None:
    """Après le corps rejoué, le canal relie le ``receive`` client — pas un faux disconnect.

    Starlette relit ``receive`` pendant un SSE pour détecter une coupure. Un
    ``http.disconnect`` inventé abort le flux (« ASGI without completing »).
    """
    import asyncio

    recus: list[dict] = []
    file_client: list[dict] = [
        {"type": "http.request", "body": b'{"messages":[]}', "more_body": False},
        {"type": "http.request", "body": b"", "more_body": False},
    ]

    async def receive_client() -> dict:
        if file_client:
            return file_client.pop(0)
        return {"type": "lifespan.startup.complete"}

    async def interne(scope, receive, send):  # noqa: ANN001, ARG001
        recus.append(await receive())
        recus.append(await receive())
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    envoyes: list[dict] = []

    async def send(message: dict) -> None:
        envoyes.append(message)

    enveloppe = MiddlewarePiecesJointes(interne, prefixe="/api/v1")
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/v1/agui/agent_rh",
        "headers": [(b"content-type", b"application/json")],
    }
    asyncio.run(enveloppe(scope, receive_client, send))

    assert recus[0]["type"] == "http.request"
    assert recus[0]["body"] == b'{"messages":[]}'
    # Relais du second message client — pas un disconnect inventé.
    assert recus[1]["type"] == "http.request"
    assert envoyes[0]["status"] == 200


def test_middleware_transmet_le_message_textuel_sans_reecriture() -> None:
    """Cas courant (aucune pièce jointe) : la charge utile arrive inchangée."""
    payload = {
        "threadId": "fil-2",
        "messages": [{"role": "user", "content": [{"type": "text", "text": "Bonjour"}]}],
    }
    client, captures = _client_middleware()

    assert client.post("/api/v1/agui/agent_rh", json=payload).status_code == 200
    assert json.loads(captures[0]) == payload


# --- Montage sur l'application réelle ---------------------------------------------


def test_application_carso_monte_le_middleware() -> None:
    """Le backend réel monte la normalisation **sous** CORS.

    Starlette construit la pile en enveloppant la liste à l'envers : le premier
    élément est le plus externe. La normalisation doit donc venir après CORS —
    le préflight ``OPTIONS`` reste ainsi traité sans lecture de corps.
    """
    from starlette.middleware.cors import CORSMiddleware

    classes = [middleware.cls for middleware in app_carso.user_middleware]
    assert MiddlewarePiecesJointes in classes
    assert classes.index(CORSMiddleware) < classes.index(MiddlewarePiecesJointes)
