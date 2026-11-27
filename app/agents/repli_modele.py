"""Repli de modèle : un run ne meurt plus d'une panne passagère du fournisseur.

**Constat (25/11).** Un run de l'agent généraliste a passé trois tours de modèle
(49 s, 120 s, puis 269 s sur 51 717 caractères d'entrée) avant de finir en
``APIError`` — l'utilisateur a attendu sept minutes pour un échec. Les journaux
montrent que ce n'est pas un incident isolé : ``Service temporarily unavailable``
(logfare, 19/11 et 25/11), ``503 server_error`` (freeinference, 16/11 et 19/11),
``rate limiting: inference request per min rate reached`` (10/11 et 19/11). À
chaque fois, un modèle était saturé alors que la passerelle en servait d'autres.

**Correctif.** Le modèle porte un **modèle de secours** optionnel
(``AGENT_MODEL_SECOURS``). Quand un tour échoue pour une raison qui vient du
fournisseur — et non de la requête —, le tour est rejoué **une fois** avec le
modèle de secours, dans le même run, avec les mêmes messages et les mêmes outils
liés. L'utilisateur reçoit une réponse au lieu d'un ``RUN_ERROR``.

**Ce qui déclenche le repli.** Uniquement les pannes qu'un autre modèle peut
couvrir (``erreur_repliable``) :

- 5xx, 429, 408, 425, 529 (modèle saturé, passerelle indisponible, débit dépassé) ;
- coupure de connexion ou délai d'attente réseau ;
- ``openai.APIError`` **sans statut** : c'est la forme que prennent une coupure de
  flux SSE et certains messages de passerelle (``Service temporarily
  unavailable``, ``AiError: rate limiting …``) ;
- ``TimeoutError`` : la garde ``AGENT_STREAM_CHUNK_TIMEOUT`` a fait expirer un
  flux resté muet.

Une erreur **de requête** (400, 401, 403, 404, 422) n'est jamais rejouée : elle
échouerait à l'identique sur le second modèle, en coûtant un appel de plus. Un
``CancelledError`` (client parti) n'est pas une ``Exception`` : il traverse sans
être intercepté.

**Couverture : les chemins asynchrones.** Deux points d'entrée seulement sont
décorés — ``_astream`` (les runs d'agents, donc tout AG-UI) et ``_agenerate``
(``ainvoke``, utilisé notamment par le résumé d'historique). Les variantes
synchrones (``_generate``/``_stream``) ne sont **pas** réécrites : un seul
appelant les emprunte (la compréhension de modèle documentaire,
``app/documents/moteur/comprehension.py``), et dupliquer la même décision en
synchrone pour cet unique cas coûtait plus de code qu'il ne protégeait.

**Limites, dites explicitement.**

- Le secours est un **autre modèle du même endpoint** : si la passerelle entière
  est tombée, le repli échoue aussi et l'erreur d'origine remonte. Une URL ou une
  clé distincte n'est pas gérée ici.
- Le nom du secours est **vérifié contre ``/models`` de la passerelle
  effectivement configurée** au montage de l'agent
  (``providers._avec_modele_de_secours``) : un modèle absent n'est jamais
  attaché, un modèle non vérifiable non plus.
- Un repli **après émission de morceaux** de réponse rejoue la génération depuis
  le début : l'interface peut alors afficher un fragment suivi de la réponse
  complète du secours. C'est le comportement de ``with_fallbacks`` de LangChain,
  et il est journalisé (``après N morceaux déjà émis``) pour que l'incident reste
  lisible. Aucun cas de ce type n'est constaté à ce jour : les pannes observées
  surviennent avant le premier chunk.
- Le repli est **par appel**, sans mémoire : un modèle principal momentanément
  saturé redevient prioritaire au tour suivant.

Aucune donnée métier n'est journalisée : seulement les noms de modèles, la durée
de la tentative perdue et le type d'erreur.
"""

from __future__ import annotations

import logging
import time
from typing import Any

_logger = logging.getLogger("app.agents.repli_modele")

#: Statuts HTTP qui trahissent une panne du fournisseur, pas une faute de requête.
STATUTS_REPLIABLES: frozenset[int] = frozenset(
    {408, 409, 425, 429, 500, 502, 503, 504, 529}
)


def erreur_repliable(erreur: BaseException) -> bool:
    """Vrai si un second modèle peut raisonnablement couvrir cette panne.

    Args:
        erreur: l'exception levée par le tour de modèle.

    Returns:
        ``True`` pour une panne du fournisseur (statut rejouable, connexion
        coupée, flux muet, ``APIError`` sans statut) ; ``False`` pour une erreur
        de requête ou une erreur locale.
    """
    statut = getattr(erreur, "status_code", None)
    if isinstance(statut, int) and not isinstance(statut, bool):
        return statut in STATUTS_REPLIABLES

    if isinstance(erreur, TimeoutError):
        # ``StreamChunkTimeoutError`` (langchain-openai) dérive de
        # ``TimeoutError`` : le fournisseur est resté muet au-delà de
        # ``AGENT_STREAM_CHUNK_TIMEOUT``.
        return True

    try:  # pragma: no cover - openai accompagne toujours langchain-openai
        import openai
    except ImportError:
        return False

    # ``APITimeoutError`` hérite d'``APIConnectionError`` : un seul test suffit.
    if isinstance(erreur, openai.APIConnectionError):
        return True
    if isinstance(erreur, openai.APIError):
        # Pas de statut : flux SSE coupé, ou passerelle qui répond une erreur
        # applicative sans code HTTP exploitable (cas logfare.ai).
        return True
    return False


def _nom(modele: Any) -> str:
    """Nom du modèle, tel que le journal doit le montrer."""
    from app.agents.mesure_latence_modele import nom_modele

    return nom_modele(modele)


class RepliModele:
    """Mixin de repli : à placer **avant** la classe de modèle dans les bases.

    ``class Modele(MixiRepli, ChatOpenAI)`` — les méthodes ci-dessous appellent
    ``super()``, qui atteint alors l'implémentation réelle du provider.
    """

    #: Modèle de secours, construit avec la même configuration que le principal
    #: (même endpoint, même clé) et un autre nom de modèle. ``None`` : pas de
    #: repli, comportement historique.
    modele_de_secours: Any = None

    async def _agenerate(
        self,
        messages: Any,
        stop: Any = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> Any:
        debut = time.perf_counter()
        try:
            return await super()._agenerate(
                messages, stop=stop, run_manager=run_manager, **kwargs
            )
        except Exception as erreur:  # noqa: BLE001 - tri explicite ci-dessous
            secours = self._secours_pour(erreur, morceaux=0, duree=_ecoule(debut))
            if secours is None:
                raise
            return await secours._agenerate(
                messages, stop=stop, run_manager=run_manager, **kwargs
            )

    async def _astream(
        self,
        messages: Any,
        stop: Any = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> Any:
        debut = time.perf_counter()
        morceaux = 0
        try:
            async for morceau in super()._astream(
                messages, stop=stop, run_manager=run_manager, **kwargs
            ):
                morceaux += 1
                yield morceau
            return
        except Exception as erreur:  # noqa: BLE001 - tri explicite ci-dessous
            secours = self._secours_pour(
                erreur, morceaux=morceaux, duree=_ecoule(debut)
            )
            if secours is None:
                raise
        # Le ``run_manager`` du tour est réutilisé : les morceaux du secours
        # restent des jetons du **même** run, donc le flux AG-UI continue au
        # lieu de recevoir un RUN_ERROR.
        async for morceau in secours._astream(
            messages, stop=stop, run_manager=run_manager, **kwargs
        ):
            yield morceau

    def _secours_pour(
        self, erreur: BaseException, *, morceaux: int, duree: float
    ) -> Any | None:
        """Le modèle de secours si la panne le justifie, sinon ``None``.

        Unique endroit qui décide de la bascule, pour les deux chemins couverts.
        Journalise en ``WARNING`` : un repli silencieux ferait croire que le
        modèle configuré a répondu, alors qu'un autre a travaillé — et
        masquerait une passerelle en difficulté.
        """
        secours = self.modele_de_secours
        if secours is None or not erreur_repliable(erreur):
            return None
        detail = (
            f"repli de modèle : {_nom(self)} → {_nom(secours)} "
            f"après {duree:.1f} s ({type(erreur).__name__})"
        )
        if morceaux:
            detail += f", {morceaux} morceaux déjà émis (réponse rejouée)"
        _logger.warning(detail)
        return secours


def _ecoule(debut: float) -> float:
    """Durée de la tentative qui vient d'échouer, en secondes."""
    return time.perf_counter() - debut
