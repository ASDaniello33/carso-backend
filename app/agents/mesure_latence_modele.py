"""Durée de chaque étape « model » dans le journal — latence attribuable.

**Constat (15/11).** Une latence de 20 à 45 s sur un simple « Bonjour » a dû être
attribuée en rejouant une campagne de mesures hors production : le journal ne
disait rien du temps passé dans le tour de modèle. Les mesures ont montré que la
chaîne applicative coûte 0,06 à 0,14 s et que le reste est l'attente du
fournisseur (`docs/validation/MESURE-LATENCE-COUPLES-2026-11-15.md`), mais il a
fallu instrumenter à la main pour l'établir.

**Correctif.** Un middleware de dernier passage avant le fournisseur écrit une
ligne par appel de modèle :

    étape model : 4.12 s (agent=agent_generaliste_readonly, model=qwen-3.8-27b,
    base_url=https://logfare.ai/v1, entree=43712 car., messages=3, thread=…)

Il ne mesure **que** le tour de modèle (l'appel au fournisseur, streaming
compris) : la différence avec le temps total d'un run est ce que coûte le reste
de la chaîne. Un échec est journalisé aussi, avec sa durée : un tour refusé au
bout de 3 s et un tour refusé au bout de 60 s ne racontent pas la même panne.

Trois signaux supplémentaires, tous en lecture (aucun effet sur la réponse) :

- `WARNING` au-delà de ``SEUIL_ETAPE_LENTE`` secondes : une étape anormalement
  lente doit sauter aux yeux dans le journal, sans campagne de mesures ;
- `WARNING` `réponse vide` quand un tour de modèle ne rend **ni texte ni appel
  d'outil** (constat du 15/11 : le générateur d'offre a terminé un run de 423 s
  sans un mot) ;
- une ligne de bilan par run (`run terminé : … duree=… s, etapes=N`), qui donne
  directement l'écart entre le run et la somme des étapes.

**Complément (15/11, seconde mesure).** Le tour de modèle n'est pas le seul
poste : l'utilisateur décrit « un peu de latence avant et après l'exécution des
outils », et une génération de document dure 5 à 6 minutes. Ce temps se passe
**dans les outils**, que les lignes `étape model` ne voyaient pas — l'écart
`run − étapes` restait un bloc inexpliqué (38 % du run `01a1df03`). Le middleware
enveloppe donc aussi les appels d'outils :

    outil : agent=agent_generateur_offre, outil=generer_document_offre,
    duree=312.47 s, thread=…, run=…

et le bilan du run porte désormais le total : `outils=N, temps_outils=X s`. Un
écart `run − étapes − temps_outils` ne doit plus représenter que de la mise en
forme, pas un appel non mesuré.

Aucune donnée métier n'est journalisée : ni texte de message, ni clé, ni réponse
du modèle — seulement le modèle, l'adresse, la taille de l'entrée et les
identifiants techniques du fil.
"""

from __future__ import annotations

import logging
import time
from typing import Any

try:  # pragma: no cover - dépendance toujours présente en production
    from langchain.agents.middleware.types import AgentMiddleware as _AgentMiddleware
except ImportError:  # pragma: no cover
    _AgentMiddleware = object  # type: ignore[misc,assignment]

_logger = logging.getLogger("app.agents.mesure_modele")

#: Longueur maximale de l'adresse du fournisseur dans le journal.
_LONGUEUR_ADRESSE = 80

#: Au-delà, l'étape de modèle est signalée en `WARNING` : c'est le seuil au-delà
#: duquel un tour n'est plus perçu comme « un peu lent » mais comme une panne.
SEUIL_ETAPE_LENTE = 45.0

#: Au-delà, l'exécution d'un outil est signalée en `WARNING`.
#:
#: **Pourquoi ce seuil existe.** Les lignes `étape model` ne comptent que les
#: appels au fournisseur : le temps passé *dans* un outil (génération de
#: document, import Excel, requête lourde) n'apparaissait nulle part. L'écart
#: entre la somme des étapes et la durée du run était donc un bloc opaque —
#: c'est précisément ce que l'utilisateur décrit comme « un peu de latence
#: avant et après l'exécution des outils » (15/11).
SEUIL_OUTIL_LENT = 20.0

#: Nombre maximal de runs suivis simultanément (borne mémoire du bilan par run).
_RUNS_SUIVIS_MAX = 256


def nom_modele(modele: Any) -> str:
    """Nom du modèle effectivement appelé (``model_name``, ``model`` ou classe)."""
    for attribut in ("model_name", "model"):
        valeur = getattr(modele, attribut, None)
        if isinstance(valeur, str) and valeur.strip():
            return valeur.strip()
    return modele.__class__.__name__


def adresse_modele(modele: Any) -> str | None:
    """Adresse du fournisseur, ou ``None`` (provider natif sans URL)."""
    for attribut in ("openai_api_base", "base_url"):
        valeur = getattr(modele, attribut, None)
        if isinstance(valeur, str) and valeur.strip():
            return valeur.strip()[:_LONGUEUR_ADRESSE]
    return None


def taille_entree(request: Any) -> int:
    """Nombre de caractères envoyés au modèle (messages + prompt système).

    Sert à distinguer une entrée lourde d'une attente du fournisseur : c'est le
    seul chiffre qui a un sens pour comparer deux runs de latence.
    """
    total = 0
    for message in getattr(request, "messages", None) or []:
        total += len(str(getattr(message, "content", "") or ""))
    systeme = getattr(request, "system_message", None)
    if systeme is not None:
        total += len(str(getattr(systeme, "content", "") or ""))
    return total


def texte_reponse(reponse: Any) -> str:
    """Texte rendu par un tour de modèle (``ModelResponse`` ou message simple)."""
    if isinstance(reponse, str):
        return reponse.strip()
    resultat = getattr(reponse, "result", reponse)
    messages = resultat if isinstance(resultat, list) else [resultat]
    textes: list[str] = []
    for message in messages:
        contenu = getattr(message, "content", None)
        if isinstance(contenu, str):
            textes.append(contenu)
        elif isinstance(contenu, list):
            textes.extend(
                str(partie.get("text", ""))
                for partie in contenu
                if isinstance(partie, dict)
            )
    return "".join(textes).strip()


def nom_outil(request: Any) -> str:
    """Nom de l'outil demandé par un ``ToolCallRequest``.

    Plusieurs formes coexistent selon la version de LangChain (dict, objet) :
    on lit les deux, sans supposer laquelle est en place.
    """
    appel = getattr(request, "tool_call", None)
    if isinstance(appel, dict):
        nom = appel.get("name")
        if isinstance(nom, str) and nom.strip():
            return nom.strip()
    outil = getattr(request, "tool", None)
    nom = getattr(outil, "name", None)
    if isinstance(nom, str) and nom.strip():
        return nom.strip()
    return "inconnu"


def a_appels_outils(reponse: Any) -> bool:
    """Vrai si le tour demande au moins un outil (réponse sans texte normale)."""
    resultat = getattr(reponse, "result", reponse)
    messages = resultat if isinstance(resultat, list) else [resultat]
    return any(getattr(message, "tool_calls", None) for message in messages)


def identifiants(request: Any) -> dict[str, str]:
    """Identifiants techniques du run (fil, run) quand ils sont disponibles."""
    return identifiants_runtime(getattr(request, "runtime", None))


def identifiants_runtime(runtime: Any) -> dict[str, str]:
    """Identifiants du run portés par ``Runtime`` (fil, run).

    ``Runtime`` n'expose pas la configuration : LangGraph place le fil courant
    dans ``runtime.execution_info`` (``thread_id``, ``run_id``). La configuration
    reste lue en second recours pour les exécutions hors graphe (tests, appels
    directs), où seul ``config`` est renseigné.
    """
    config = getattr(runtime, "config", None)
    sources = [
        getattr(runtime, "execution_info", None),
        config.get("configurable") if isinstance(config, dict) else None,
    ]
    trouves: dict[str, str] = {}
    for source in sources:
        if source is None:
            continue
        for cle, etiquette in (("thread_id", "thread"), ("run_id", "run")):
            if etiquette in trouves:
                continue
            valeur = (
                source.get(cle) if isinstance(source, dict) else getattr(source, cle, None)
            )
            if isinstance(valeur, str) and valeur:
                trouves[etiquette] = valeur
    return trouves


class MesureLatenceModeleMiddleware(_AgentMiddleware):  # type: ignore[misc,valid-type]
    """Journalise la durée de chaque étape « model » et le modèle utilisé."""

    def __init__(self, agent_id: str | None = None) -> None:
        self._agent_id = agent_id
        # Bilan par run : le middleware ne fait que lire et écrire ce suivi.
        self._runs: dict[str, dict[str, Any]] = {}

    @property
    def name(self) -> str:
        return "MesureLatenceModeleMiddleware"

    def _journaliser(
        self,
        request: Any,
        duree: float,
        *,
        nombre_messages: int,
        erreur: BaseException | None = None,
        reponse_vide: bool = False,
    ) -> None:
        model = getattr(request, "model", None)
        ids = identifiants(request)
        morceaux = [
            f"agent={self._agent_id}" if self._agent_id else "",
            f"model={nom_modele(model)}",
            f"base_url={adresse_modele(model)}" if adresse_modele(model) else "",
            f"entree={taille_entree(request)} car.",
            f"messages={nombre_messages}",
            f"duree={duree:.2f} s",
        ]
        morceaux.extend(f"{cle}={valeur}" for cle, valeur in ids.items())
        details = ", ".join(morceau for morceau in morceaux if morceau)

        suivi = self._runs.get(ids.get("run") or ids.get("thread") or "")
        if suivi is not None:
            suivi["etapes"] = int(suivi.get("etapes", 0)) + 1
            suivi["modele"] = nom_modele(model)

        if erreur is not None:
            _logger.warning(
                "étape model en échec : %s, erreur=%s", details, type(erreur).__name__
            )
            return
        if reponse_vide:
            # Constat du 15/11 : le générateur d'offre a terminé un run de 423 s
            # sans un mot — le produit n'avait aucun moyen de le savoir.
            _logger.warning("étape model : réponse vide : %s", details)
            return
        if duree >= SEUIL_ETAPE_LENTE:
            _logger.warning("étape model lente (seuil %.0f s) : %s", SEUIL_ETAPE_LENTE, details)
            return
        _logger.info("étape model : %s", details)

    def _executer(self, request: Any, handler: Any) -> Any:
        debut = time.monotonic()
        nombre = len(getattr(request, "messages", None) or [])
        try:
            reponse = handler(request)
        except BaseException as erreur:  # noqa: BLE001 - mesuré puis relancé tel quel
            self._journaliser(
                request, time.monotonic() - debut, nombre_messages=nombre, erreur=erreur
            )
            raise
        self._journaliser(
            request,
            time.monotonic() - debut,
            nombre_messages=nombre,
            reponse_vide=not texte_reponse(reponse) and not a_appels_outils(reponse),
        )
        return reponse

    async def _executer_async(self, request: Any, handler: Any) -> Any:
        debut = time.monotonic()
        nombre = len(getattr(request, "messages", None) or [])
        try:
            reponse = await handler(request)
        except BaseException as erreur:  # noqa: BLE001
            self._journaliser(
                request, time.monotonic() - debut, nombre_messages=nombre, erreur=erreur
            )
            raise
        self._journaliser(
            request,
            time.monotonic() - debut,
            nombre_messages=nombre,
            reponse_vide=not texte_reponse(reponse) and not a_appels_outils(reponse),
        )
        return reponse

    # --- Bilan par run : lecture seule, l'état du graphe n'est pas touché -----

    def _demarrer_run(self, runtime: Any) -> None:
        ids = identifiants_runtime(runtime)
        cle = ids.get("run") or ids.get("thread")
        if not cle:
            return
        if len(self._runs) >= _RUNS_SUIVIS_MAX:
            self._runs.clear()
        self._runs[cle] = {
            "debut": time.monotonic(),
            "etapes": 0,
            "modele": None,
            "thread": ids.get("thread"),
            "outils": 0,
            "temps_outils": 0.0,
        }

    def _journaliser_outil(
        self, runtime: Any, nom: str, duree: float, erreur: BaseException | None = None
    ) -> None:
        """Écrit la durée d'un outil et l'accumule pour le bilan du run.

        Lit seulement : aucun effet sur l'outil ni sur l'état du graphe.
        """
        ids = identifiants_runtime(runtime)
        cle = ids.get("run") or ids.get("thread")
        suivi = self._runs.get(cle) if cle else None
        if suivi is not None:
            suivi["outils"] = int(suivi.get("outils", 0)) + 1
            suivi["temps_outils"] = float(suivi.get("temps_outils", 0.0)) + duree

        morceaux = [
            f"agent={self._agent_id}" if self._agent_id else "",
            f"outil={nom}",
            f"duree={duree:.2f} s",
            f"thread={ids.get('thread')}" if ids.get("thread") else "",
            f"run={ids.get('run')}" if ids.get("run") else "",
        ]
        details = ", ".join(morceau for morceau in morceaux if morceau)
        if erreur is not None:
            _logger.warning(
                "outil en échec : %s, erreur=%s", details, type(erreur).__name__
            )
            return
        if duree >= SEUIL_OUTIL_LENT:
            _logger.warning("outil lent (seuil %.0f s) : %s", SEUIL_OUTIL_LENT, details)
            return
        _logger.info("outil : %s", details)

    def _executer_outil(self, request: Any, handler: Any) -> Any:
        debut = time.monotonic()
        nom = nom_outil(request)
        runtime = getattr(request, "runtime", None)
        try:
            resultat = handler(request)
        except BaseException as erreur:  # noqa: BLE001 - mesuré puis relancé tel quel
            self._journaliser_outil(runtime, nom, time.monotonic() - debut, erreur)
            raise
        self._journaliser_outil(runtime, nom, time.monotonic() - debut)
        return resultat

    async def _executer_outil_async(self, request: Any, handler: Any) -> Any:
        debut = time.monotonic()
        nom = nom_outil(request)
        runtime = getattr(request, "runtime", None)
        try:
            resultat = await handler(request)
        except BaseException as erreur:  # noqa: BLE001
            self._journaliser_outil(runtime, nom, time.monotonic() - debut, erreur)
            raise
        self._journaliser_outil(runtime, nom, time.monotonic() - debut)
        return resultat

    def _terminer_run(self, runtime: Any, erreur: BaseException | None = None) -> None:
        ids = identifiants_runtime(runtime)
        cle = ids.get("run") or ids.get("thread")
        if not cle:
            return
        suivi = self._runs.pop(cle, None)
        if suivi is None:
            return
        duree = time.monotonic() - float(suivi["debut"])
        morceaux = [
            f"agent={self._agent_id}" if self._agent_id else "",
            f"model={suivi.get('modele') or 'inconnu'}",
            f"duree={duree:.2f} s",
            f"etapes={suivi.get('etapes', 0)}",
            # Temps passé dans les outils : le poste que les étapes model ne
            # comptent pas. Son absence rendait l'écart run − étapes opaque.
            f"outils={suivi.get('outils', 0)}",
            f"temps_outils={float(suivi.get('temps_outils', 0.0)):.2f} s",
            f"thread={suivi['thread']}" if suivi.get("thread") else "",
            f"run={ids.get('run')}" if ids.get("run") else "",
        ]
        details = ", ".join(morceau for morceau in morceaux if morceau)
        if erreur is None:
            _logger.info("run terminé : %s", details)
        else:
            _logger.warning(
                "run interrompu : %s, erreur=%s", details, type(erreur).__name__
            )

    def before_agent(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        self._demarrer_run(runtime)
        return None

    async def abefore_agent(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        self._demarrer_run(runtime)
        return None

    def after_agent(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        self._terminer_run(runtime)
        return None

    async def aafter_agent(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        self._terminer_run(runtime)
        return None

    def wrap_model_call(self, request: Any, handler: Any) -> Any:
        return self._executer(request, handler)

    async def awrap_model_call(self, request: Any, handler: Any) -> Any:
        return await self._executer_async(request, handler)

    def wrap_tool_call(self, request: Any, handler: Any) -> Any:
        return self._executer_outil(request, handler)

    async def awrap_tool_call(self, request: Any, handler: Any) -> Any:
        return await self._executer_outil_async(request, handler)


__all__ = [
    "SEUIL_ETAPE_LENTE",
    "SEUIL_OUTIL_LENT",
    "MesureLatenceModeleMiddleware",
    "a_appels_outils",
    "adresse_modele",
    "identifiants",
    "identifiants_runtime",
    "nom_modele",
    "nom_outil",
    "taille_entree",
    "texte_reponse",
]
