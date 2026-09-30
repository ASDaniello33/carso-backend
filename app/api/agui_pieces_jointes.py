"""Pièces jointes AG-UI : du binaire à la référence documentaire CARSO.

Règle métier : un fichier envoyé dans le chat est un **document CARSO**, pas un
blob à pousser au modèle. Le frontend dépose le fichier via ``POST /documents``
(il est enregistré, ancré à l'entité de la fiche courante) puis joint au message
la référence de ce document. Ce module transforme la partie binaire reçue en une
**ligne de texte** — ``[Pièce jointe : nom — document <id>]`` — avant que
``ag_ui_langgraph`` ne construise les messages LangChain :

- le modèle ne reçoit jamais de contenu binaire ni d'URL locale injoignable ;
- l'agent lit ensuite le document avec ses propres outils (matrice d'accès,
  permissions de son rôle) — AGENTS.md §2.5 et §9 ;
- aucun secret, aucun chemin disque et aucun octet de fichier ne traverse ce
  module.

Après cette normalisation, le même tour reçoit dans ``context`` AG-UI la
**fiche** du document et un **extrait borné** (``AGENT_ATTACHMENT_EXCERPT_CHARS``).
Le modèle n'a plus à redemander l'UUID. Les octets ne passent jamais.

Le module n'écrit que sur les endpoints AG-UI d'agents
(``{prefixe}/agui/{agent_id}``) : le catalogue et le reste de l'API ne sont pas
touchés. Toute anomalie (corps illisible, forme inattendue, session absente)
laisse la requête **intacte** : une panne de normalisation ne casse jamais une
conversation.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

_logger = logging.getLogger(__name__)

Scope = dict[str, Any]
Receive = Callable[[], Awaitable[dict[str, Any]]]
Send = Callable[[dict[str, Any]], Awaitable[None]]

#: Types de parties non textuelles d'un message AG-UI (cf. ``InputContent``).
TYPES_PIECES_JOINTES = frozenset({"image", "audio", "video", "document", "binary", "file"})


def _premier_texte(*candidats: Any) -> str | None:
    """Première valeur textuelle non vide — les champs arrivent du réseau."""
    for candidat in candidats:
        if isinstance(candidat, str) and candidat.strip():
            return candidat.strip()
    return None


def _source(partie: dict[str, Any]) -> dict[str, Any]:
    source = partie.get("source")
    return source if isinstance(source, dict) else {}


def _document_depuis_source(source: dict[str, Any]) -> str | None:
    """Identifiant lu dans une valeur ``document://<id>`` ou ``carso://``."""
    valeur = source.get("value")
    if not isinstance(valeur, str):
        return None
    for schema in ("document://", "carso://document/"):
        if valeur.startswith(schema):
            reste = valeur[len(schema) :].strip("/ ")
            return reste or None
    return None


def _reference_piece_jointe(partie: dict[str, Any]) -> str:
    """Ligne de texte remplaçant une partie binaire — jamais son contenu."""
    metadonnees = partie.get("metadata")
    metadonnees = metadonnees if isinstance(metadonnees, dict) else {}
    source = _source(partie)
    nom = _premier_texte(
        metadonnees.get("filename"),
        metadonnees.get("nom"),
        source.get("filename"),
    )
    document_id = _premier_texte(
        metadonnees.get("document_id"),
        metadonnees.get("documentId"),
        _document_depuis_source(source),
    )
    type_document = _premier_texte(metadonnees.get("type_document"))

    morceaux = [f"Pièce jointe : {nom}" if nom else "Pièce jointe"]
    if type_document:
        morceaux.append(f"type {type_document}")
    if document_id:
        morceaux.append(f"document {document_id}")
    return "[{}]".format(" — ".join(morceaux))


def normaliser_message(message: dict[str, Any]) -> dict[str, Any]:
    """Message utilisateur à contenu multiple → message à texte seul.

    Le texte saisi est conservé tel quel ; chaque partie binaire devient sa
    référence. Un message déjà textuel est rendu inchangé.
    """
    contenu = message.get("content")
    if not isinstance(contenu, list):
        return message

    textes: list[str] = []
    references: list[str] = []
    for partie in contenu:
        if not isinstance(partie, dict):
            continue
        type_partie = partie.get("type")
        if type_partie == "text":
            texte = partie.get("text")
            if isinstance(texte, str) and texte:
                textes.append(texte)
        elif type_partie in TYPES_PIECES_JOINTES:
            references.append(_reference_piece_jointe(partie))

    # Message sans pièce jointe : rendu tel quel (pas de réécriture de corps
    # pour le cas courant d'un message purement textuel).
    if not references:
        return message

    nouveau = dict(message)
    nouveau["content"] = "\n".join([*textes, *references])
    return nouveau


def normaliser_pieces_jointes(payload: dict[str, Any]) -> dict[str, Any]:
    """Charge utile AG-UI dont les messages utilisateur sont ramenés au texte.

    Le payload d'origine est rendu **tel quel** (même objet) quand rien n'a
    changé : l'appelant sait alors qu'il n'a pas de corps à réécrire.
    """
    messages = payload.get("messages")
    if not isinstance(messages, list):
        return payload

    normalises: list[Any] = []
    modifie = False
    for message in messages:
        normalises.append(message)
        if not (
            isinstance(message, dict)
            and message.get("role") == "user"
            and isinstance(message.get("content"), list)
        ):
            continue
        normalise = normaliser_message(message)
        if normalise is not message:
            normalises[-1] = normalise
            modifie = True

    if not modifie:
        return payload
    copie = dict(payload)
    copie["messages"] = normalises
    return copie


def reecrire_corps(
    corps: bytes,
    *,
    session_factory: Callable[[], Any] | None = None,
) -> bytes:
    """Corps JSON normalisé + contexte de pièces, ou l'original si rien ne change."""
    try:
        payload = json.loads(corps)
    except (ValueError, UnicodeDecodeError):
        return corps
    if not isinstance(payload, dict):
        return corps
    normalise = normaliser_pieces_jointes(payload)
    from app.agents.pieces_jointes_contexte import injecter_contexte_dans_payload

    enrichi = injecter_contexte_dans_payload(
        normalise, session_factory=session_factory
    )
    if enrichi is payload:
        return corps
    try:
        return json.dumps(enrichi).encode("utf-8")
    except (TypeError, ValueError):  # pragma: no cover - payload non sérialisable
        return corps


async def _lire_corps(receive: Receive) -> bytes | None:
    """Corps complet de la requête, ou ``None`` si le client a coupé."""
    morceaux: list[bytes] = []
    while True:
        message = await receive()
        type_message = message.get("type")
        if type_message == "http.disconnect":
            return None
        if type_message != "http.request":
            continue
        corps = message.get("body", b"")
        if isinstance(corps, bytes):
            morceaux.append(corps)
        if not message.get("more_body"):
            return b"".join(morceaux)


def _receive_rejouee(corps: bytes, receive_original: Receive) -> Receive:
    """Canal ``receive`` rejouant ``corps`` une fois, puis le canal client.

    Un corps ASGI n'a qu'une vie : celui lu par le middleware ne peut plus être
    relu par l'app en aval. On le rejoue donc une fois.

    Ensuite on **relaye** le ``receive`` réel du client — jamais un faux
    ``http.disconnect``. Les endpoints AG-UI sont en SSE : Starlette relit
    ``receive`` pendant le stream pour détecter une coupure. Un disconnect
    inventé abort le flux (« ASGI callable returned without completing
    response ») et le navigateur voit ``TypeError: network error``.
    """
    restants: list[dict[str, Any]] = [
        {"type": "http.request", "body": corps, "more_body": False}
    ]

    async def receive_reecrite() -> dict[str, Any]:
        if restants:
            return restants.pop(0)
        return await receive_original()

    return receive_reecrite


def _remplacer_longueur(
    en_tetes: list[tuple[bytes, bytes]], longueur: int
) -> list[tuple[bytes, bytes]]:
    """En-têtes dont ``content-length`` correspond au nouveau corps."""
    restantes = [
        (cle, valeur) for cle, valeur in en_tetes if cle.lower() != b"content-length"
    ]
    restantes.append((b"content-length", str(longueur).encode("ascii")))
    return restantes


class MiddlewarePiecesJointes:
    """Middleware ASGI : normalise le corps des requêtes AG-UI avant routage.

    Aucune modification des internes d'``ag_ui_langgraph`` : le flux SSE et le
    protocole restent ceux de la lib, seule l'entrée du modèle est assainie.
    """

    def __init__(self, app: Any, prefixe: str) -> None:
        self.app = app
        self.base = f"{prefixe.rstrip('/')}/agui/"

    def _concerne(self, scope: Scope) -> bool:
        if scope.get("type") != "http" or scope.get("method") != "POST":
            return False
        chemin = scope.get("path") or ""
        # Le catalogue (`{base}`) reste en lecture : seuls les endpoints
        # d'agents (`{base}{agent_id}`) portent des messages.
        return isinstance(chemin, str) and chemin.startswith(self.base) and chemin != self.base

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if not self._concerne(scope):
            await self.app(scope, receive, send)
            return

        try:
            corps = await _lire_corps(receive)
        except Exception:  # pragma: no cover - dépend du serveur ASGI
            _logger.warning(
                "Pièces jointes AG-UI : corps illisible, requête transmise telle quelle"
            )
            await self.app(scope, receive, send)
            return

        if corps is None:
            await self.app(scope, receive, send)
            return

        # Le corps est déjà consommé : l'app en aval ne peut plus le relire
        # depuis le serveur, il faut donc **toujours** le rejouer — même quand
        # il n'est pas modifié (sinon la requête resterait en attente).
        nouveau = reecrire_corps(corps)
        normale = nouveau is corps or nouveau == corps
        if normale:
            await self.app(scope, _receive_rejouee(nouveau, receive), send)
            return

        _logger.debug("Pièces jointes AG-UI normalisées (%s)", scope.get("path"))
        scope_reecrit = dict(scope)
        scope_reecrit["headers"] = _remplacer_longueur(
            list(scope.get("headers") or []), len(nouveau)
        )
        await self.app(scope_reecrit, _receive_rejouee(nouveau, receive), send)


__all__ = [
    "MiddlewarePiecesJointes",
    "TYPES_PIECES_JOINTES",
    "normaliser_message",
    "normaliser_pieces_jointes",
    "reecrire_corps",
]
