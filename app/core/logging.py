"""Journalisation applicative : un flux qui n'échoue jamais sur un caractère.

Constaté en run réel sous Windows : la console écrit en `cp1252`, et une ligne
de log contenant `→` (ou tout caractère hors de cette page de code) lève
`UnicodeEncodeError` **dans le handler**. Python ne perd pas la ligne : il
imprime une trace « --- Logging error --- » à la place, donc le diagnostic utile
disparaît au moment précis où il sert.

La sortie est donc préparée avant d'être branchée sur le handler :

1. un flux qui accepte `reconfigure` passe en UTF-8 avec
   `errors="backslashreplace"` — rien ne peut plus lever pendant l'écriture ;
2. sinon, si le flux expose son tampon binaire, il est réenveloppé en UTF-8 avec
   les mêmes options ;
3. sinon le flux est rendu tel quel : mieux vaut un log imparfait qu'un log
   perdu.
"""

import io
import logging
import sys
from typing import Any

#: Option d'erreur d'écriture : un caractère non représentable est échappé
#: (`\\u2192`) au lieu de faire échouer la ligne entière.
ERREURS_SUR = "backslashreplace"


def flux_journalisation(flux: Any) -> Any:
    """Flux rendu sûr à l'écriture (UTF-8, jamais d'exception d'encodage)."""
    encodage = str(getattr(flux, "encoding", "") or "").lower().replace("-", "")
    erreurs = str(getattr(flux, "errors", "") or "")
    if encodage in ("utf8", "utf8mb4") and erreurs == ERREURS_SUR:
        return flux

    reconfigurer = getattr(flux, "reconfigure", None)
    if callable(reconfigurer):
        try:
            reconfigurer(encoding="utf-8", errors=ERREURS_SUR)
            return flux
        except (ValueError, OSError, LookupError):
            pass

    tampon = getattr(flux, "buffer", None)
    if tampon is not None:
        try:
            return io.TextIOWrapper(
                tampon,
                encoding="utf-8",
                errors=ERREURS_SUR,
                line_buffering=True,
                write_through=True,
            )
        except (ValueError, OSError, LookupError):
            pass
    return flux


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
    handler = logging.StreamHandler(flux_journalisation(sys.stdout))
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
