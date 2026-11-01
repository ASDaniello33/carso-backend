"""Indexation de fond de la base de connaissance (thread démon).

Pourquoi un worker plutôt qu'une requête HTTP synchrone : indexer un ouvrage de
500 pages, c'est lire le fichier, le découper en milliers de passages et
vectoriser — plusieurs minutes dans le pire cas. La requête d'import doit
revenir tout de suite ; l'état (``en_attente`` → ``indexation`` → ``indexe`` ou
``erreur``) est **visible** dans la fiche.

Garanties :

- **un ouvrage pris une seule fois** — ``SELECT … FOR UPDATE SKIP LOCKED`` :
  sur un déploiement multi-instances, deux workers ne traitent pas le même
  document (et l'état ``indexation`` est commité avant le travail long) ;
- **réveil immédiat** après un import (``reveiller``), plus un tour périodique
  qui rattrape les reprises après arrêt ;
- **aucun échec silencieux** — un ouvrage en erreur porte son message et reste
  réindexable ; le worker ne meurt pas d'un document illisible ;
- **arrêtable** — un ``threading.Event`` sort de la boucle à l'arrêt de l'API.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from uuid import UUID

from sqlalchemy.orm import Session

from app.application.services.connaissance_service import resoudre_embedder
from app.connaissances.embeddings import Embedder
from app.connaissances.indexation import IndexationConnaissances, RapportIndexation
from app.connaissances.vecteurs import IndexVecteurs, obtenir_index
from app.core.config import Settings, get_settings
from app.core.errors import CarsoError
from app.domain.enums import ActionAudit, ActorType, StatutIndexationConnaissance
from app.infrastructure.database import SessionLocal
from app.infrastructure.repositories import DocumentConnaissanceRepository

logger = logging.getLogger(__name__)


def _marquer_erreur(
    fabrique: Callable[[], Session], document_id: UUID, message: str
) -> None:
    """Inscrit l'échec sur la fiche (transaction courte et dédiée)."""
    from app.application.trace import TraceContext

    session = fabrique()
    try:
        document = DocumentConnaissanceRepository(session).get(document_id)
        if document is None:
            return
        document.statut = StatutIndexationConnaissance.ERREUR.value
        document.erreur = (message or "Indexation impossible")[:2000]
        TraceContext(session, actor_id="systeme:indexation").record_event(
            actor_type=ActorType.SYSTEME.value,
            action=ActionAudit.CONNAISSANCE_INDEXATION_ECHOUEE.value,
            entity_type="document_connaissance",
            entity_id=document.id,
            after={"titre": document.titre, "erreur": document.erreur},
        )
        session.commit()
    except Exception:  # noqa: BLE001 — un échec de marquage ne tue pas le worker
        session.rollback()
        logger.exception("Marquage d'erreur d'indexation impossible (%s)", document_id)
    finally:
        session.close()


def indexer_prochain(
    *,
    session_factory: Callable[[], Session] | None = None,
    settings: Settings | None = None,
    index: IndexVecteurs | None = None,
    embedder: Embedder | None = None,
) -> RapportIndexation | None:
    """Indexe **un** ouvrage en attente (``None`` s'il n'y en a aucun).

    Utilisé par le worker et par les tests : le chemin est unique.
    """
    fabrique = session_factory or SessionLocal
    cfg = settings or get_settings()
    session = fabrique()
    document_id: UUID | None = None
    try:
        document = DocumentConnaissanceRepository(session).prochain_a_indexer()
        if document is None:
            return None
        document_id = document.id
        document.statut = StatutIndexationConnaissance.INDEXATION.value
        session.commit()
        try:
            modele = embedder or resoudre_embedder(session, settings=cfg)
            index_effectif = index or obtenir_index()
            rapport = IndexationConnaissances(
                session,
                embedder=modele,
                index=index_effectif,
                settings=cfg,
            ).indexer(document)
            session.commit()
            return rapport
        except CarsoError as exc:
            session.rollback()
            _marquer_erreur(fabrique, document_id, str(exc))
            return RapportIndexation(
                document_id=str(document_id),
                statut=StatutIndexationConnaissance.ERREUR.value,
                message=str(exc),
            )
        except Exception as exc:  # noqa: BLE001 — un imprévu est signalé, pas avalé
            session.rollback()
            logger.exception("Indexation de %s : échec inattendu", document_id)
            _marquer_erreur(fabrique, document_id, f"{type(exc).__name__}: {exc}")
            return RapportIndexation(
                document_id=str(document_id),
                statut=StatutIndexationConnaissance.ERREUR.value,
                message=str(exc),
            )
    finally:
        session.close()


def indexer_en_attente(
    *,
    session_factory: Callable[[], Session] | None = None,
    settings: Settings | None = None,
    index: IndexVecteurs | None = None,
    embedder: Embedder | None = None,
    limite: int = 10,
) -> list[RapportIndexation]:
    """Traite jusqu'à ``limite`` ouvrages en attente (un commit par ouvrage)."""
    rapports: list[RapportIndexation] = []
    for _ in range(max(1, limite)):
        rapport = indexer_prochain(
            session_factory=session_factory,
            settings=settings,
            index=index,
            embedder=embedder,
        )
        if rapport is None:
            break
        rapports.append(rapport)
        logger.info(
            "Indexation %s : %s (%s passages)",
            rapport.document_id,
            rapport.statut,
            rapport.nombre_chunks,
        )
    return rapports


class WorkerIndexation(threading.Thread):
    """Boucle d'indexation : un tour, puis un sommeil interruptible."""

    def __init__(self, *, intervalle: int | None = None) -> None:
        super().__init__(name="carso-indexation-connaissances", daemon=True)
        self._arret = threading.Event()
        self._reveil = threading.Event()
        self._intervalle = intervalle

    def reveiller(self) -> None:
        """Demande un tour immédiat (import, réindexation)."""
        self._reveil.set()

    def run(self) -> None:
        intervalle = self._intervalle or max(
            1, get_settings().knowledge_indexation_intervalle_secondes
        )
        logger.info("Base de connaissance : indexation démarrée (toutes les %s s).", intervalle)
        while not self._arret.is_set():
            try:
                indexer_en_attente(limite=5)
            except Exception:  # noqa: BLE001 — un tour échoué ne tue pas la boucle
                logger.exception("Base de connaissance : tour d'indexation échoué.")
            self._reveil.wait(intervalle)
            self._reveil.clear()
        logger.info("Base de connaissance : indexation arrêtée.")

    def arreter(self) -> None:
        self._arret.set()
        self._reveil.set()


#: Worker du processus (un seul thread, un seul index local).
_WORKER: WorkerIndexation | None = None


def demarrer_indexation() -> WorkerIndexation | None:
    """Démarre le worker si la configuration l'active ; ``None`` sinon."""
    global _WORKER
    if not get_settings().knowledge_indexation_active:
        logger.info("Base de connaissance : indexation de fond désactivée.")
        return None
    if _WORKER is not None and _WORKER.is_alive():
        return _WORKER
    worker = WorkerIndexation()
    worker.start()
    _WORKER = worker
    return worker


def reveiller() -> None:
    """Réveille le worker du processus s'il tourne (sans erreur sinon)."""
    if _WORKER is not None and _WORKER.is_alive():
        _WORKER.reveiller()


__all__ = [
    "WorkerIndexation",
    "demarrer_indexation",
    "indexer_en_attente",
    "indexer_prochain",
    "reveiller",
]
