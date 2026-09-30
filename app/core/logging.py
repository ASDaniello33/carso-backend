"""Application logging setup (instruction/09 §9: useful, never secret-bearing)."""

import logging
import sys

#: Librairies tierces qui parlent en DEBUG pendant chaque appel LLM (requêtes
#: HTTP token par token, en-têtes, timings). Leur contenu est utile au
#: développement ciblé d'un provider, jamais au quotidien : en ``debug=True``
#: elles noyaient les logs applicatifs (dizaines de lignes par chunk SSE).
_LIBRAIRIES_BRUITEUSES = (
    "httpcore",
    "httpx",
    "openai",
    "openai._base_client",
    "langsmith",
    "asyncio",
    "fpdf",
    # deepagents sonde à chaque construction de graphe les middlewares de
    # caching optionnels (langchain_aws, langchain_fireworks…) : leur absence
    # est normale et déjà interceptée — son DEBUG avec traceback n'est pas
    # une erreur (voir docs/validation INCREMENT-13).
    "deepagents",
)


def configure_logging(level: int = logging.INFO) -> None:
    """Configure root logging with a concise, structured-enough format.

    Quel que soit le niveau racine, les loggers de transport des librairies
    tierces restent bornés à INFO : le diagnostic détaillé d'un provider se
    fait en surchargeant localement le logger concerné, pas en noyant la
    console entière.
    """
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)

    # debug=True met la racine en DEBUG : les loggers de transport restent
    # verrouillés à INFO (sinon chaque chunk SSE produit des lignes DEBUG).
    # Aucune perte : WARNING/ERROR de ces librairies restent affichés.
    for nom in _LIBRAIRIES_BRUITEUSES:
        logging.getLogger(nom).setLevel(logging.INFO)
