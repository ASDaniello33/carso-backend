"""Routes—canal temps réel du social (WebSocket).

Décision d'architecture (04/10)—le canal remplace le polling périodique de
la page sociale et du fil de discussion. Voir
``app/application/services/social_evenements.py`` pour le contrat et
``docs/validation/INCREMENT-45-REALTIME-SOCIAL-WEBSOCKET.md``.

Deux surface, volontairement minces :

- ``POST /social/ticket`` rend un **billet** à usage unique valable 30 s. Un
  jeton de session dans l'URL de la socket serait écrit dans les journaux du
  serveur web—interdit par AGENTS.md §9 (aucun secret dans les logs). Le
  navigateur ne peut pas non plus poser un en-tête ``Authorization`` sur une
  connexion WebSocket ;
- ``GET /social/ws?ticket=…`` ouvre la socket. Elle n'échange **que** des
  signaux de relecture, jamais de donnée métier—l'autorisation reste celle
  des endpoints REST, le client relit par l'API qu'il connaît déjà.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Annotated, Any

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from app.api.deps import CurrentUser
from app.application.services.social_evenements import (
    DUREE_BILLET_S,
    abonner,
    consommer_billet,
    desabonner,
    emettre_billet,
    memoriser_boucle,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/social", tags=["social"])


class TicketRead(BaseModel):
    """Billet d'ouverture du canal temps réel."""

    ticket: str
    expire_dans_s: int


@router.post("/ticket", response_model=TicketRead)
def ouvrir_ticket(utilisateur: CurrentUser) -> TicketRead:
    """Ticket à usage unique : le transforme en accès à la socket."""
    return TicketRead(
        ticket=emettre_billet(utilisateur.id),
        expire_dans_s=int(DUREE_BILLET_S),
    )


@router.websocket("/ws")
async def canal_social(
    websocket: WebSocket,
    ticket: Annotated[str, Query()],
) -> None:
    """Socket de signaux : écoute « suivre », relaie les changements de fil.

    Le client s'abonne à la conversation qu'il affiche. Toute écriture social
    validée en base émet un signal que cette socket relaie — la réponse d'un
    agent compris, ce qui rend le fil vivant sans interroger le serveur en
    boucle.
    """
    identifiant = consommer_billet(ticket)
    if identifiant is None:
        # Pas de jeton à renvoyer : on refuse la connexion, sans détail.
        await websocket.close(code=4401)
        return

    await websocket.accept()
    memoriser_boucle(asyncio.get_running_loop())
    await _boucler(websocket, identifiant)


async def _boucler(websocket: WebSocket, utilisateur_id: Any) -> None:
    """Écoute les abonnements et relaie les signaux jusqu'à la fermeture."""
    from app.infrastructure.database import SessionLocal

    try:
        while True:
            try:
                recu = await websocket.receive_json()
            except (WebSocketDisconnect, ValueError, TypeError):
                return
            if not isinstance(recu, dict) or recu.get("action") != "suivre":
                continue
            conversation_id = recu.get("conversation_id")
            if not isinstance(conversation_id, str):
                continue
            if _est_membre(SessionLocal, conversation_id, utilisateur_id):
                abonner(websocket, conversation_id)
    except WebSocketDisconnect:
        return
    finally:
        desabonner(websocket)


def _est_membre(factory: Any, conversation_id: str, utilisateur_id: Any) -> bool:
    """Appartenance vérifiée **une fois**, à l'abonnement.

    Une révocation ultérieure n'empêche pas de recevoir un signal — sans
    conséquence : le signal ne porte aucune donnée et la relecture REST
    répondra ``403``. C'est le prix de l'économie d'un test d'appartenance par
    écriture, et il est assumé.
    """
    from uuid import UUID

    from app.application.services.social_service import SocialService

    session = factory()
    try:
        return SocialService(session).est_membre(UUID(conversation_id), utilisateur_id)
    except Exception as exc:  # noqa: BLE001 - conversation illisible = pas d'abonnement
        logger.debug("Abonnement refusé (%s) : %s", type(exc).__name__, conversation_id)
        return False
    finally:
        session.close()
