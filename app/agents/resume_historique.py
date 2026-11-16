"""Résumé d'historique : seuil explicite, comptage honnête, durée mesurée.

**Constat (15/11).** Sur un run du générateur d'offre (974 s, `qwen-3.8-27b`),
deux trous de 331 s et 20 s séparent des étapes de modèle qui ne totalisent que
566 s. À chaque trou : un appel `chat/completions` rattaché à aucune étape, puis
un historique ramené de 39 à 7 messages. C'était le **résumé d'historique** de
Deep Agents : un appel de modèle interne, non chronométré par
``MesureLatenceModeleMiddleware`` (qui ne voit que les tours de l'agent).

Deux défauts de la configuration implicite expliquent le déclenchement :

1. **Seuil.** ``create_deep_agent`` sans réglage appelle
   ``create_summarization_middleware``, qui prend ses défauts dans
   ``compute_summarization_defaults``. Nos modèles (``openai_compatible``, sans
   ``max_input_tokens`` déclaré) tombent dans la branche « sans profil » :
   déclenchement à **170 000 jetons**, conservation des **6 derniers messages**.
2. **Comptage.** Le compteur par défaut est
   ``count_tokens_approximately(use_usage_metadata_scaling=True)`` : il recale
   l'estimation caractères→jetons sur le ``total_tokens`` **déclaré par le
   fournisseur** au dernier message. Ce total inclut la sortie et le
   raisonnement du tour précédent, qui ne font pas partie de la requête
   suivante. Et ``_should_summarize`` déclenche **aussi** sur ce total déclaré
   dès qu'il atteint le seuil. D'où un résumé lancé sur ~157 000 caractères
   d'entrée, très en dessous de 170 000 jetons réels.

**Correctif.** Le harnais fixe le seuil lui-même, en **fractions de la fenêtre
déclarée** (``trigger=("fraction", 0.75)``, ``keep=("fraction", 0.10)``), et
compte l'entrée réelle :

- compteur **déterministe** (``use_usage_metadata_scaling=False``, 4 caractères
  par jeton) sur les messages, le prompt système et les schémas d'outils ;
- déclenchement sur ce seul compte : le total déclaré par le fournisseur n'est
  plus un déclencheur (il surestime l'entrée du tour suivant) ;
- seuil et part conservée configurables
  (``AGENT_RESUME_HISTORIQUE_FRACTION``, ``AGENT_RESUME_HISTORIQUE_FRACTION_GARDEE``),
  sur la fenêtre ``AGENT_MAX_INPUT_TOKENS`` déclarée par le modèle ;
- **durée mesurée et journalisée**, avec un `WARNING` au-delà de
  ``SEUIL_RESUME_LENT`` : un trou de cinq minutes dans un run a désormais un nom.

Une fraction exige que le modèle expose ``max_input_tokens`` : sans profil,
LangChain refuse le seuil à la construction. Les providers construisent donc le
modèle avec la fenêtre déclarée (``app/agents/providers.py``).

Le repli sur dépassement de contexte du middleware d'origine est conservé (un
``ContextOverflowError`` déclenche encore un résumé suivi d'une nouvelle
tentative) : ignorer le total déclaré ne supprime pas ce filet.

Ce module remplace le middleware par défaut **par son nom** : la pile construite
par ``create_deep_agent`` garde sa position, il n'y en a pas deux.
"""

from __future__ import annotations

import logging
import time
from functools import partial
from typing import TYPE_CHECKING, Any

from app.agents.mesure_latence_modele import nom_modele

if TYPE_CHECKING:  # pragma: no cover - types seulement
    from deepagents.backends.protocol import BackendProtocol
    from langchain.chat_models import BaseChatModel
    from langchain_core.messages import AnyMessage

_logger = logging.getLogger("app.agents.mesure_modele")

#: Au-delà, le résumé est signalé en `WARNING` : c'est le seuil au-delà duquel un
#: appel interne cesse d'être un détail et devient un incident de latence.
SEUIL_RESUME_LENT = 30.0

#: Nom sous lequel ``create_deep_agent`` reconnaît le middleware à remplacer.
NOM_MIDDLEWARE = "SummarizationMiddleware"


def compteur_jetons_deterministe() -> Any:
    """Compteur d'entrée réel : caractères / 4, sans recalage fournisseur.

    Le recalage (``use_usage_metadata_scaling``) prend le ``total_tokens`` du
    dernier message — sortie et raisonnement compris — pour estimer l'entrée du
    tour suivant, qui ne les contient pas. Le 15/11, ce biais a lancé un résumé
    de 331 s sur une entrée bien en dessous du seuil.
    """
    from langchain_core.messages.utils import count_tokens_approximately

    return partial(count_tokens_approximately, use_usage_metadata_scaling=False)


def _fenetre_declaree(model: Any) -> int | None:
    """Fenêtre d'entrée déclarée par le modèle, ou ``None``."""
    profile = getattr(model, "profile", None)
    if not isinstance(profile, dict):
        return None
    valeur = profile.get("max_input_tokens")
    return valeur if isinstance(valeur, int) and not isinstance(valeur, bool) else None


def _modele_avec_fenetre(model: Any, fenetre_jetons: int) -> Any:
    """Rend la fenêtre d'entrée disponible pour les seuils en fraction.

    Une fraction exige ``profile["max_input_tokens"]`` : sans lui, LangChain
    refuse la construction (``ValueError``). Les providers le déclarent ; un
    modèle construit autrement (test, usage direct) ne l'a pas, et ce n'est pas
    au harnais de retomber silencieusement sur un seuil absolu : la même fenêtre
    lui est posée, celle de la configuration.
    """
    if _fenetre_declaree(model) is not None:
        return model
    model.profile = {"max_input_tokens": int(fenetre_jetons)}
    return model


def construire_resume_historique(
    model: BaseChatModel,
    backend: BackendProtocol,
    *,
    agent_id: str,
    fraction_seuil: float,
    fraction_gardee: float,
    fenetre_jetons: int,
) -> Any:
    """Construit le middleware de résumé configuré et instrumenté.

    Args:
        model: modèle qui produit le résumé (le même que l'agent : aucune
            dépendance nouvelle, aucun modèle « rapide » supposé disponible).
            Il doit exposer ``profile["max_input_tokens"]`` — sinon la fraction
            est refusée (``ValueError``) par la bibliothèque.
        backend: backend du harnais, où l'historique évincé est archivé.
        agent_id: identifiant d'agent, pour attribuer la ligne de journal.
        fraction_seuil: part de la fenêtre d'entrée qui déclenche le résumé.
        fraction_gardee: part de la fenêtre conservée telle quelle.
        fenetre_jetons: fenêtre déclarée si le modèle n'en annonce aucune.

    Returns:
        Une instance de ``SummarizationMiddleware`` (Deep Agents) dont le nom
        remplace le défaut dans la pile construite par ``create_deep_agent``.
    """
    from deepagents.middleware.summarization import (
        SummarizationMiddleware,
        compute_summarization_defaults,
    )

    seuil_fraction = float(fraction_seuil)
    garde_fraction = float(fraction_gardee)
    model = _modele_avec_fenetre(model, fenetre_jetons)
    fenetre = _fenetre_declaree(model) or int(fenetre_jetons)
    seuil = max(1, int(fenetre * seuil_fraction))
    # Troncature pré-résumé des gros arguments d'anciens messages : gardée aux
    # valeurs de la bibliothèque (adaptées au profil), pas réinventées ici.
    defauts = compute_summarization_defaults(model)

    class ResumeHistorique(SummarizationMiddleware):  # type: ignore[misc,valid-type]
        """Résumé à seuil explicite, journalisé, déclenché sur l'entrée réelle."""

        _dernier_compte = 0

        @property
        def name(self) -> str:
            # Remplace le middleware par défaut **à sa place** dans la pile.
            return NOM_MIDDLEWARE

        def _should_summarize(
            self, messages: list[AnyMessage], total_tokens: int
        ) -> bool:
            """Déclenche sur le compte déterministe, jamais sur le total déclaré.

            ``super()._should_summarize`` consulte aussi le ``total_tokens``
            rendu par le fournisseur au message précédent ; ce total contient la
            sortie et le raisonnement, donc il dépasse l'entrée réelle et
            déclenche trop tôt (constat du 15/11).
            """
            self._dernier_compte = total_tokens
            return total_tokens >= seuil

        def _create_summary(self, messages: list[AnyMessage]) -> str:
            debut = time.perf_counter()
            try:
                resume = super()._create_summary(messages)
            except Exception as erreur:
                self._journaliser(time.perf_counter() - debut, len(messages), erreur)
                raise
            self._journaliser(time.perf_counter() - debut, len(messages))
            return resume

        async def _acreate_summary(self, messages: list[AnyMessage]) -> str:
            debut = time.perf_counter()
            try:
                resume = await super()._acreate_summary(messages)
            except Exception as erreur:
                self._journaliser(time.perf_counter() - debut, len(messages), erreur)
                raise
            self._journaliser(time.perf_counter() - debut, len(messages))
            return resume

        def _journaliser(
            self, duree: float, nb_messages: int, erreur: Exception | None = None
        ) -> None:
            """Écrit la ligne du résumé : durée, taille, seuil, agent.

            Aucune donnée métier : ni texte de message, ni contenu du résumé.
            """
            details = (
                f"agent={agent_id}, modele={nom_modele(self.model)}, "
                f"messages={nb_messages}, jetons={self._dernier_compte}, "
                f"seuil={seuil} ({seuil_fraction:g} de {fenetre}), "
                f"garde={garde_fraction:g} de {fenetre}"
            )
            if erreur is not None:
                _logger.warning(
                    "résumé historique en échec : %.2f s (%s, erreur=%s)",
                    duree,
                    details,
                    type(erreur).__name__,
                )
            elif duree >= SEUIL_RESUME_LENT:
                _logger.warning("résumé historique LENT : %.2f s (%s)", duree, details)
            else:
                _logger.info("résumé historique : %.2f s (%s)", duree, details)

    return ResumeHistorique(
        model=model,
        backend=backend,
        trigger=("fraction", seuil_fraction),
        keep=("fraction", garde_fraction),
        token_counter=compteur_jetons_deterministe(),
        truncate_args_settings=defauts.get("truncate_args_settings"),
    )
