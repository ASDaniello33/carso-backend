"""Diffusion temps réel du social—signaux « cette conversation a changé ».

**Retour explicite d'une décision d'architecture** (ADR-0001, revisitée le
04/10)—la diffusion in-app se faisait uniquement par *polling*—« pas
d'email, pas de websocket ». Cette décision est **remise en cause**—à
1 000 utilisateurs, le seul ``recharger()`` de la page sociale (4 requêtes
toutes les 10 s) représente ~500 requêtes/seconde, et le fil de discussion
peut rester « périmé » jusqu'à 30 s.

Le canal ``WebSocket`` ne transporte **aucune donnée métier**—il pousse un
signal opaque ``{conversation_id, kind}`` et le client relit par l'API REST,
qui reste le seul chemin d'autorisation et de sérialisation. Conséquences
directes :

- un utilisateur retiré d'une conférence peut encore recevoir un signal, mais
  sa relecture REST renvoie ``403``—**aucune fuite possible**, car rien de
  métier ne circule sur le socket ;
- le filtre « appartient au fil » est fait une seule fois, à l'abonnement ;
- le canal ne duplique aucun contrat de données.

**Limite assumée (à traiter avant tout multi-processus)**—le registre de
connexions et les billets vivent en mémoire du processus. Deux workers
n'échangent rien—il faudra un bus partagé (Redis) et un routage affinant.
Documenté dans ``docs/validation/INCREMENT-45-REALTIME-SOCIAL-WEBSOCKET.md``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
import threading
import time
from collections import defaultdict
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import event
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

#: Durée de vie d'un billet : le temps d'ouvrir le socket, pas plus. Un jeton
#: de session dans l'URL serait journalisé par le serveur web — interdit
#: (AGENTS.md §9, aucun secret dans les logs).
DUREE_BILLET_S = 30.0

#: Clé ``session.info`` : les événements en attente du commit courant.
CLE_EVENEMENTS = "carso.evenements_social"

#: ``kind`` émis — permet au client d'ignorer un signal sans relire.
KIND_MESSAGE = "message"
KIND_REACTION = "reaction"
KIND_CONVERSATION = "conversation"

#: Délai minimal entre deux signaux « liste des conversations » sur un même
#: socket. Un fil animé (réactions, suppressions en rafale) ne doit pas
#: provoquer une relecture par écriture : on regroupe.
DELAI_SIGNAL_LISTE_S = 2.0


@dataclass(frozen=True)
class EvenementSocial:
    """Signal « cette conversation a changé ». Aucun contenu métier."""

    conversation_id: str
    kind: str = KIND_MESSAGE


def signaler(session: Session, evenement: EvenementSocial) -> None:
    """Met l'événement en file ; il part **après** le commit (voir ``_diffuser``).

    Émettre avant le commit ferait relire au client un instantané sans
    l'écriture — exactement la course qui faisait disparaître la bulle.
    """
    file: list[EvenementSocial] = session.info.setdefault(CLE_EVENEMENTS, [])
    file.append(evenement)


# --- Billets d'ouverture ------------------------------------------------------

_billets: dict[str, tuple[UUID, float]] = {}
_verrou_billets = threading.Lock()


def emettre_billet(utilisateur_id: UUID) -> str:
    """Ticket à usage unique, valable ``DUREE_BILLET_S`` secondes."""
    ticket = secrets.token_urlsafe(24)
    maintenant = time.monotonic()
    with _verrou_billets:
        _purger_billets(maintenant)
        _billets[ticket] = (utilisateur_id, maintenant + DUREE_BILLET_S)
    return ticket


def consommer_billet(ticket: str) -> UUID | None:
    """Échange le billet contre l'identifiant du compte, puis l'invalide."""
    with _verrou_billets:
        entree = _billets.pop(ticket, None)
    if entree is None:
        return None
    identifiant, expire = entree
    if expire < time.monotonic():
        return None
    return identifiant


def _purger_billets(maintenant: float) -> None:
    for ticket, (_, expire) in list(_billets.items()):
        if expire < maintenant:
            del _billets[ticket]


# --- Connexions ---------------------------------------------------------------

#: ``conversation_id -> sockets``. Une socket peut suivre plusieurs fils.
_abonnes: dict[str, set[Any]] = defaultdict(set)
_sockets: set[Any] = set()
_dernier_signal: dict[Any, float] = {}
_verrou_abonnes = threading.Lock()
_boucle: asyncio.AbstractEventLoop | None = None


def memoriser_boucle(boucle: asyncio.AbstractEventLoop) -> None:
    """Mémorise la boucle de l'application (à la première connexion)."""
    global _boucle
    _boucle = boucle


def abonner(socket: Any, conversation_id: str) -> None:
    with _verrou_abonnes:
        _abonnes[conversation_id].add(socket)
        _sockets.add(socket)


def desabonner(socket: Any) -> None:
    """Retire la socket de **tous** les fils qu'elle suivait."""
    with _verrou_abonnes:
        _sockets.discard(socket)
        _dernier_signal.pop(socket, None)
        for conversation_id, sockets in list(_abonnes.items()):
            sockets.discard(socket)
            if not sockets:
                del _abonnes[conversation_id]


def nb_connexions() -> int:
    """Connexions ouvertes (diagnostic et tests)."""
    with _verrou_abonnes:
        return len(_sockets)


async def diffuser(evenements: list[EvenementSocial]) -> None:
    """Envoie les signaux aux sockets abonnées et, en plus, à toutes les autres.

    Deux signaux par écriture, aux usages distincts :

    - ``conversation_id`` + ``kind`` → la socket **abonnée à ce fil** relit le
      fil. Précis, et c'est le chemin du bug corrigé (réponse d'un agent) ;
    - ``kind: conversation`` → **toutes** les sockets, y compris celles qui
      n'affichent pas ce fil, pour rafraîchir la liste de gauche. Regroupé à
      ``DELAI_SIGNAL_LISTE_S`` : une rafale d'écritures ne déclenche pas une
      relecture par écriture.
    """
    for evenement in evenements:
        with _verrou_abonnes:
            cibles = list(_abonnes.get(evenement.conversation_id, ()))
            tous = list(_sockets)
        if not cibles and not tous:
            continue
        charge = json.dumps(
            {
                "type": "social",
                "conversation_id": evenement.conversation_id,
                "kind": evenement.kind,
            },
            ensure_ascii=False,
        )
        liste = json.dumps({"type": "social", "kind": KIND_CONVERSATION}, ensure_ascii=False)
        for socket in set(cibles) | set(tous):
            messages = [charge] if socket in cibles else []
            if _signaler_liste(socket):
                messages.append(liste)
            if not messages:
                continue
            try:
                for charge_envoyee in messages:
                    await socket.send_text(charge_envoyee)
            except Exception as exc:  # noqa: BLE001 - un client mort ne casse rien
                logger.debug(
                    "Envoi temps réel impossible (%s), socket abandonnée.",
                    type(exc).__name__,
                )
                desabonner(socket)


def _signaler_liste(socket: Any) -> bool:
    """Vrai si le signal « liste » est dû pour cette socket (regroupé)."""
    maintenant = time.monotonic()
    with _verrou_abonnes:
        precedent = _dernier_signal.get(socket, 0.0)
        if maintenant - precedent < DELAI_SIGNAL_LISTE_S:
            return False
        _dernier_signal[socket] = maintenant
        return True


def _diffuser_apres_commit(session: Session) -> None:
    """Hook SQLAlchemy : part la file une fois l'écriture validée.

    ``after_commit`` s'exécute dans le fil de la requête — souvent un worker —
    d'où le repli sur la boucle de l'application remembered par
    ``memoriser_boucle``. Sans boucle (aucune connexion ouverte), personne
    n'attend l'événement : la file est simplement jetée.
    """
    evenements: list[EvenementSocial] = session.info.pop(CLE_EVENEMENTS, [])
    if not evenements:
        return
    boucle = _boucle
    if boucle is None or not boucle.is_running():
        return
    asyncio.run_coroutine_threadsafe(diffuser(evenements), boucle)


event.listen(Session, "after_commit", _diffuser_apres_commit)


__all__ = [
    "EvenementSocial",
    "KIND_CONVERSATION",
    "KIND_MESSAGE",
    "KIND_REACTION",
    "abonner",
    "consommer_billet",
    "desabonner",
    "diffuser",
    "emettre_billet",
    "memoriser_boucle",
    "nb_connexions",
    "signaler",
]
