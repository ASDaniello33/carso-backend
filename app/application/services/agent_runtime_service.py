"""Service de la configuration runtime des agents (persistée, auditée).

Rôle : valider une configuration d'exécution, la persister en créant une
**nouvelle version** (l'ancienne est désactivée) et tracer la décision. Le
service ne construit aucun graphe : appliquer la configuration au runtime est le
rôle de ``app.agents.manager`` (séparation service / infrastructure).

Sécurité (instruction/08 §6) : aucun secret n'entre ici. ``AGENT_API_KEY`` reste
dans ``backend/.env`` ; seuls provider/modèle/``base_url``/température/borne
d'itérations sont administrables.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.application.trace import TraceContext
from app.core.chiffrement import chiffrement_disponible, chiffrer
from app.core.errors import ValidationError as BusinessValidationError
from app.domain.agent_runtime import ConfigurationRuntimeAgent
from app.domain.enums import ActionAudit, ActorType
from app.infrastructure.repositories import ConfigurationRuntimeAgentRepository

_ENTITY = "configuration_agent"
_TEMPERATURE_MAX = 2.0


def _valider_ou_refuser(
    *, provider: str, model: str, base_url: str | None
) -> tuple[str, str, str | None]:
    """Valide la configuration en traduisant l'erreur de déploiement en 422.

    Une valeur fournie par un administrateur (provider inconnu, ``base_url``
    manquante) est une **erreur de requête**, pas une panne de déploiement :
    l'API doit répondre 422 et non 500.
    """
    # Import tardif : ``app.agents`` importe les services métier (tools des
    # agents) — un import au niveau module créerait un cycle avec le package
    # ``app.application.services`` selon l'ordre d'initialisation.
    from app.agents.providers import AgentConfigurationError, valider_parametres

    try:
        return valider_parametres(provider=provider, model=model, base_url=base_url)
    except AgentConfigurationError as exc:
        raise BusinessValidationError(
            str(exc), details=getattr(exc, "details", None)
        ) from exc


class AgentRuntimeService:
    """Persiste et historise la configuration runtime des agents."""

    def __init__(self, session: Session, actor_id: str | None = None) -> None:
        self._session = session
        self.configurations = ConfigurationRuntimeAgentRepository(session)
        self._trace = TraceContext(session, actor_id=actor_id)

    # --- Lecture ---------------------------------------------------------------

    def obtenir_active(self) -> ConfigurationRuntimeAgent | None:
        """Configuration appliquée, ou ``None`` si jamais administrée."""
        return self.configurations.get_active()

    def historique(self, limit: int = 20) -> list[ConfigurationRuntimeAgent]:
        """Versions successives, de la plus récente à la plus ancienne."""
        return self.configurations.list_recent(limit=limit)

    # --- Écriture --------------------------------------------------------------

    def enregistrer(
        self,
        *,
        provider: str,
        model: str,
        base_url: str | None = None,
        temperature: float = 0.0,
        max_iterations: int = 1000,
        modifie_par: str | None = None,
        api_key: str | None = None,
        conserver_cle: bool = True,
    ) -> ConfigurationRuntimeAgent:
        """Crée une nouvelle configuration active et désactive la précédente.

        Args:
            api_key: clé du provider saisie par l'administrateur. Elle est
                **chiffrée** avant écriture (``app.core.chiffrement``) et
                n'apparaît ni dans l'audit ni dans la réponse.
            conserver_cle: ``False`` retire la clé administrée (retour à celle du
                ``.env``) ; sans ``api_key`` et avec ``conserver_cle=True``, la
                clé déjà enregistrée est reprise — changer le modèle ne doit pas
                obligatoirement ressaisir la clé.

        La validation est faite **avant** toute écriture : une configuration
        refusée ne doit jamais atteindre la base.

        Raises:
            AgentConfigurationError: provider inconnu, modèle absent, ``base_url``
                absente pour un endpoint compatible OpenAI.
            BusinessValidationError: température ou borne d'itérations hors limites,
                ou clé fournie alors que le chiffrement est indisponible.
        """
        provider_n, model_n, base_url_n = _valider_ou_refuser(
            provider=provider, model=model, base_url=base_url
        )
        if not 0.0 <= float(temperature) <= _TEMPERATURE_MAX:
            msg = f"Température hors limites (0 à {_TEMPERATURE_MAX})"
            raise BusinessValidationError(msg, details={"temperature": temperature})
        if int(max_iterations) < 1:
            msg = "Borne d'itérations invalide : au moins 1"
            raise BusinessValidationError(
                msg, details={"max_iterations": max_iterations}
            )

        precedente = self.configurations.get_active()
        avant = self._projection(precedente) if precedente is not None else None

        # Clé : celle fournie, sinon celle déjà enregistrée (sauf retrait explicite).
        cle_chiffree: str | None = None
        if api_key and api_key.strip():
            if not chiffrement_disponible():
                raise BusinessValidationError(
                    "Aucune clé de chiffrement configurée : renseigner "
                    "PARAMETRES_CHIFFREMENT_KEY (ou AUTH_SECRET) avant d'enregistrer "
                    "une clé de provider.",
                    details={"champ": "api_key"},
                )
            cle_chiffree = chiffrer(api_key.strip())
        elif conserver_cle and precedente is not None:
            cle_chiffree = precedente.api_key_chiffree

        if precedente is not None:
            # Une seule configuration active : l'historique reste lisible.
            precedente.actif = False

        ligne = ConfigurationRuntimeAgent(
            provider=provider_n,
            model=model_n,
            base_url=base_url_n,
            temperature=float(temperature),
            max_iterations=int(max_iterations),
            actif=True,
            modifie_par=modifie_par,
            api_key_chiffree=cle_chiffree,
        )
        self.configurations.add(ligne)
        # Flush (pas commit : la transaction appartient à l'appelant) pour
        # disposer de l'identifiant dans l'événement d'audit.
        self._session.flush()
        self._trace.record_event(
            actor_type=ActorType.HUMAIN.value,
            action=ActionAudit.CONFIGURATION_AGENT_MODIFIEE.value,
            entity_type=_ENTITY,
            entity_id=ligne.id,
            after=self._projection(ligne),
            event_metadata={"avant": avant},
        )
        return ligne

    def desactiver_active(self) -> bool:
        """Désactive la configuration administrée (retour au ``.env``).

        Returns:
            ``True`` si une configuration active a été désactivée.

        Sans cette désactivation, un redémarrage rechargerait la configuration
        précédente : le système doit mémoriser *la dernière décision*, y compris
        le retour à la configuration de déploiement.
        """
        precedente = self.configurations.get_active()
        if precedente is None:
            return False
        avant = self._projection(precedente)
        precedente.actif = False
        self._session.flush()
        self._trace.record_event(
            actor_type=ActorType.HUMAIN.value,
            action=ActionAudit.CONFIGURATION_AGENT_MODIFIEE.value,
            entity_type=_ENTITY,
            entity_id=precedente.id,
            after={**avant, "actif": False},
            event_metadata={"avant": avant, "raison": "retour a la configuration .env"},
        )
        return True

    def journaliser_rechargement(
        self, agents: list[str], *, par: str | None = None
    ) -> None:
        """Trace un redémarrage du runtime d'agents (sans changement de config)."""
        self._trace.record_event(
            actor_type=ActorType.HUMAIN.value,
            action=ActionAudit.CONFIGURATION_AGENT_RECHARGEE.value,
            entity_type=_ENTITY,
            entity_id=None,
            after={"agents": list(agents), "par": par or self._trace.actor_id},
        )

    # --- internes --------------------------------------------------------------

    @staticmethod
    def _projection(ligne: ConfigurationRuntimeAgent) -> dict[str, Any]:
        """Vue sans secret ni objet ORM, sûre pour l'audit.

        Un changement de clé est visible dans la trace **sans jamais en révéler
        la valeur** : seul le fait qu'une clé administrée existe est consigné.
        """
        return {
            "provider": ligne.provider,
            "model": ligne.model,
            "base_url": ligne.base_url,
            "temperature": float(ligne.temperature),
            "max_iterations": int(ligne.max_iterations),
            "cle_administree": bool(ligne.api_key_chiffree),
        }


__all__ = ["AgentRuntimeService"]