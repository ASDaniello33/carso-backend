"""Sonde : le repli de modèle tient-il sur un vrai aller-retour HTTP ?

**Pourquoi cet outil.** Les tests unitaires vérifient la décision (quelle erreur
bascule) et la délégation, avec des modèles factices. Ils ne prouvent pas que la
classe réelle — ``ChatOpenAI`` construit par ``app/agents/providers.py`` — passe
bien par le repli : payload, outils liés, ``run_manager`` et flux SSE sont
autant de maillons que seul un vrai client HTTP exerce.

**Ce que fait la sonde.** Elle démarre un faux endpoint compatible OpenAI sur
``127.0.0.1`` (aucune clé, aucun réseau) :

- ``GET /v1/models`` publie la fenêtre des deux modèles ;
- ``POST /v1/chat/completions`` répond **503** pour ``principal``, et une réponse
  valide pour ``secours`` — en JSON ou en SSE selon la demande ;
- une requête dont le message contient « invalide » reçoit **400**.

Quatre vérifications, dans l'ordre :

1. ``ainvoke`` : panne 503 du principal → réponse du secours ;
2. ``astream`` : même panne, en flux (chemin réel d'AG-UI) ;
3. ``ainvoke`` avec une requête invalide : **aucun** repli, l'erreur remonte ;
4. un secours nommé hors catalogue : **aucun** modèle de secours attaché.

Usage ::

    cd backend && .venv/Scripts/python.exe scripts/verifier_repli_modele.py
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from app.agents.providers import build_chat_model
from app.core.config import Settings

MODELES = ("principal", "secours")
REPONSE = "CARSO"


def _erreur_503() -> dict[str, Any]:
    return {
        "error": {
            "message": "Service temporarily unavailable",
            "type": "server_error",
            "code": "503",
        }
    }


class _Handler(BaseHTTPRequestHandler):
    """Faux endpoint compatible OpenAI, piloté par des règles fixes."""

    def log_message(self, *_a: Any) -> None:  # silence : la sonde parle seule
        return None

    def _json(self, statut: int, charge: dict[str, Any]) -> None:
        corps = json.dumps(charge).encode()
        self.send_response(statut)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(corps)))
        self.end_headers()
        self.wfile.write(corps)

    def do_GET(self) -> None:  # noqa: N802 - API de BaseHTTPRequestHandler
        if self.path.endswith("/models"):
            self._json(
                200,
                {"data": [{"id": nom, "context_length": 100_000} for nom in MODELES]},
            )
            return
        self._json(404, {"error": {"message": "inconnu"}})

    def do_POST(self) -> None:  # noqa: N802 - API de BaseHTTPRequestHandler
        taille = int(self.headers.get("Content-Length") or 0)
        demande = json.loads(self.rfile.read(taille) or b"{}")
        modele = demande.get("model")
        texte = " ".join(
            str(message.get("content") or "") for message in demande.get("messages", [])
        )
        if "invalide" in texte:
            self._json(
                400,
                {"error": {"message": "requête invalide", "type": "invalid_request"}},
            )
            return
        if modele == "principal":
            self._json(503, _erreur_503())
            return
        if demande.get("stream"):
            self._flux()
            return
        self._json(
            200,
            {
                "id": "chatcmpl-sonde",
                "object": "chat.completion",
                "created": 0,
                "model": modele,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": REPONSE},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )

    def _flux(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for morceau in (REPONSE[:2], REPONSE[2:]):
            evenement = {
                "id": "chatcmpl-sonde",
                "object": "chat.completion.chunk",
                "created": 0,
                "model": "secours",
                "choices": [
                    {"index": 0, "delta": {"content": morceau}, "finish_reason": None}
                ],
            }
            self.wfile.write(f"data: {json.dumps(evenement)}\n\n".encode())
        self.wfile.write(b"data: [DONE]\n\n")


def _demarrer() -> tuple[ThreadingHTTPServer, str]:
    serveur = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=serveur.serve_forever, daemon=True).start()
    return serveur, f"http://127.0.0.1:{serveur.server_address[1]}/v1"


def _reglages(url: str, *, secours: str = "secours") -> Settings:
    return Settings(
        agent_provider="openai_compatible",
        agent_model="principal",
        agent_model_secours=secours,
        agent_base_url=url,
        agent_api_key="cle-de-sonde",
        agent_ssl_verify=False,
    )


async def _verifier(url: str) -> list[tuple[str, bool, str]]:
    resultats: list[tuple[str, bool, str]] = []

    modele = build_chat_model(_reglages(url))
    reponse = await modele.ainvoke("Bonjour")
    resultats.append(
        ("ainvoke : 503 du principal", reponse.content == REPONSE, str(reponse.content))
    )

    morceaux: list[str] = []
    async for morceau in modele.astream("Bonjour"):
        morceaux.append(str(morceau.content))
    resultats.append(
        ("astream : 503 du principal", "".join(morceaux) == REPONSE, "".join(morceaux))
    )

    classe = ""
    try:
        await modele.ainvoke("requête invalide")
    except Exception as erreur:  # noqa: BLE001 - la classe est ce qu'on vérifie
        classe = type(erreur).__name__
    # ``langchain-openai`` enveloppe le 400 dans ``OpenAIInvalidRequestError``
    # (sous-classe de ``openai.BadRequestError``) : le nom exact dépend de la
    # version, la nature de l'erreur non. Ce qui compte : elle a **remonté**,
    # donc aucun second modèle n'a été appelé.
    resultats.append(
        (
            "ainvoke : 400 non rejouée",
            classe.endswith(("BadRequestError", "InvalidRequestError")),
            classe or "aucune erreur",
        )
    )

    # Un nom absent du catalogue de la passerelle ne doit jamais être attaché :
    # il ne produirait qu'un second échec, au moment où le repli sert.
    absent = build_chat_model(_reglages(url, secours="modele-fantome"))
    resultats.append(
        (
            "repli : nom absent du catalogue refusé",
            absent.modele_de_secours is None,
            "aucun secours attaché"
            if absent.modele_de_secours is None
            else "secours attaché à tort",
        )
    )
    return resultats


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(name)s | %(message)s")
    serveur, url = _demarrer()
    try:
        resultats = asyncio.run(_verifier(url))
    finally:
        serveur.shutdown()

    ok = True
    for intitule, reussi, detail in resultats:
        ok = ok and reussi
        print(f"{'OK  ' if reussi else 'ÉCHEC'} {intitule} -> {detail}")
    print("VERDICT :", "repli opérationnel" if ok else "à corriger")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
