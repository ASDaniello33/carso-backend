"""Balayage périodique du cycle de vie mission/session (ADR 0012).

Un thread démon, démarré dans le ``lifespan`` de l'API, applique les
transitions dues toutes les ``CYCLE_DE_VIE_INTERVALLE_SECONDES`` secondes
(défaut : 300). Garanties :

- **un seul balayage à la fois** — verrou consultatif PostgreSQL
  (``pg_try_advisory_lock``) : sur un déploiement multi-instances, celui qui
  prend le verrou travaille, les autres passent leur tour ;
- **idempotent** — chaque transition est re-testée avant écriture ; rejouer
  le balayage le même jour ne réécrit rien ;
- **résilient** — une erreur de base de données annule le tour
  (rollback), le tour suivant retente ; l'erreur est journalisée ;
- **arrêtable** — un ``threading.Event`` sort de la boucle proprement à
  l'arrêt de l'API (le sommeil est interruptible).

En SQLite (tests, mode lab), le verrou consultatif n'existe pas : le
balayage s'exécute sans verrou — acceptable, car le mode test n'a qu'une
seule instance et le service est idempotent.
"""

from __future__ import annotations

import logging
import threading
from datetime import date

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.application.services.cycle_de_vie_service import CycleDeVieService
from app.core.config import get_settings
from app.infrastructure.database import SessionLocal

logger = logging.getLogger(__name__)

#: Clé du verrou consultatif — constante arbitraire, stable entre versions :
#: tant qu'elle ne change pas, deux instances de la même application se
#: coordonnent même si leurs déploiements diffèrent.
CLE_ADVISORY = 725_001_012


def _prendre_verrou(session: Session) -> bool:
    """Vrai si ce processus peut balayer. PostgreSQL : verrou consultatif.

    Sur un moteur sans advisory lock (SQLite), le balayage s'exécute sans
    verrou : le service reste idempotent, et le mode test est mono-instance.
    """
    if session.bind is None or session.bind.dialect.name != "postgresql":
        return True
    return bool(
        session.execute(
            text("SELECT pg_try_advisory_lock(:cle)"), {"cle": CLE_ADVISORY}
        ).scalar()
    )


def _liberer_verrou(session: Session) -> None:
    if session.bind is None or session.bind.dialect.name != "postgresql":
        return
    session.execute(text("SELECT pg_advisory_unlock(:cle)"), {"cle": CLE_ADVISORY})


def balayer_once(session: Session, aujourdhui: date) -> dict[str, int]:
    """Un tour de balayage sur une session ouverte ; aucun commit ici."""
    if not _prendre_verrou(session):
        logger.info("Cycle de vie : un autre balayage détient le verrou, tour sauté.")
        return {}
    try:
        service = CycleDeVieService(session, actor_id="systeme:cycle_de_vie")
        return service.appliquer_transitions_dues(aujourdhui)
    finally:
        _liberer_verrou(session)


class BalayageCycleDeVie(threading.Thread):
    """Boucle de balayage : un tour, un commit, un sommeil interruptible."""

    def __init__(self) -> None:
        super().__init__(name="carso-balayage-cycle-de-vie", daemon=True)
        self._arret = threading.Event()

    def run(self) -> None:
        intervalle = max(1, get_settings().cycle_de_vie_intervalle_secondes)
        logger.info("Cycle de vie : balayage démarré (toutes les %s s).", intervalle)
        while not self._arret.is_set():
            try:
                session = SessionLocal()
                try:
                    compteurs = balayer_once(session, date.today())
                    if any(compteurs.values()):
                        logger.info("Cycle de vie : transitions appliquées %s.", compteurs)
                    session.commit()
                except Exception:  # noqa: BLE001 — un tour échoué ne doit pas tuer la boucle
                    session.rollback()
                    logger.exception("Cycle de vie : tour de balayage échoué.")
                finally:
                    session.close()
            except Exception:  # noqa: BLE001 — même l'ouverture de session ne doit pas tuer la boucle
                logger.exception("Cycle de vie : session de balayage impossible.")
            self._arret.wait(intervalle)
        logger.info("Cycle de vie : balayage arrêté.")

    def arreter(self) -> None:
        self._arret.set()


def demarrer_balayage() -> BalayageCycleDeVie | None:
    """Démarre le balayage si la configuration l'active ; ``None`` sinon."""
    if not get_settings().cycle_de_vie_balayage_actif:
        logger.info("Cycle de vie : balayage désactivé par la configuration.")
        return None
    thread = BalayageCycleDeVie()
    thread.start()
    return thread


__all__ = ["BalayageCycleDeVie", "balayer_once", "demarrer_balayage"]
