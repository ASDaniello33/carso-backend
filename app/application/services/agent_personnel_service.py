"""Service—configuration personnelle de l'agent personnel.

Un compte règle **son** agent—des instructions (style, habitudes de travail)
et une liste d'outils qu'il ne veut pas exposer. Le service est le seul endroit
où ces réglages sont validés :

- les instructions sont bornées et nettoyées ; elles ne peuvent jamais
  contredire le cadre CARSO (elles s'ajoutent, le harnais les place après) ;
- un outil hors contrat est **refusé** avec la liste valide—un compte ne peut
  pas obtenir un outil que le contrat ne déclare pas, et désactiver un outil
  inexistant serait une incompréhension, pas un réglage ;
- les outils proposés sont ceux du **contrat effectif**—la surcharge
  administrée d'abord (l'administration peut fermer un outil à tous), la
  configuration du compte ensuite.

Aucun secret n'entre ici, et le service ne fait pas de ``commit``—la
transaction appartient à la route appelante.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy.orm import Session

from app.application.trace import TraceContext
from app.core.errors import ValidationError as BusinessValidationError
from app.domain.agent_personnel import ConfigurationAgentPersonnel
from app.domain.enums import ActionAudit, ActorType
from app.infrastructure.repositories import (
    ConfigurationAgentPersonnelRepository,
    SurchargeAgentRepository,
)

if TYPE_CHECKING:  # pragma: no cover - typage seul, aucun import au chargement
    from app.agents.base import AgentDefinition

#: Type d'entité de l'audit (jamais un identifiant métier : la configuration
#: appartient au compte, l'événement est relié à son propriétaire).
_ENTITY = "configuration_agent_personnel"

#: Borne des instructions personnelles — même ordre de grandeur que la
#: surcharge administrée (20 000 caractères) : un prompt personnel n'est pas un
#: document.
INSTRUCTIONS_MAX = 20_000


class AgentPersonnelService:
    """Lecture et écriture de la configuration d'un compte pour son agent."""

    def __init__(self, session: Session, actor_id: str | None = None) -> None:
        self._session = session
        self.configurations = ConfigurationAgentPersonnelRepository(session)
        self._surcharges = SurchargeAgentRepository(session)
        self._trace = TraceContext(session, actor_id=actor_id)

    # --- lectures -------------------------------------------------------------

    def contrat_effectif(self) -> AgentDefinition:
        """Contrat de l'agent personnel **moins** la surcharge administrée.

        Ce que l'administration a fermé ne doit pas être proposé au compte : la
        page Paramètres et la page Agent Personnel décrivent le même contrat.

        Imports **paresseux** : le service est chargé par
        ``app.application.services`` (très tôt), alors que ``app.agents``
        importe ce package — un import au chargement fermerait le cycle
        (``app/agents/__init__.py`` construit tous les contrats d'agents).
        """
        from app.agents.agent_personnel import DEFINITION
        from app.agents.surcharges import definition_effective

        return definition_effective(
            DEFINITION, self._surcharges.get_par_agent(DEFINITION.agent_id)
        )

    def configuration(self, utilisateur_id: UUID) -> ConfigurationAgentPersonnel | None:
        """Configuration active d'un compte, ou ``None`` s'il suit son contrat."""
        return self.configurations.get_pour_utilisateur(utilisateur_id)

    def outils_du_contrat(self) -> list[str]:
        """Outils réellement proposables au compte (contrat effectif, triés)."""
        return sorted(self.contrat_effectif().tools)

    # --- écritures ------------------------------------------------------------

    def enregistrer(
        self,
        utilisateur_id: UUID,
        *,
        instructions: str | None,
        outils_desactives: list[str],
        modifie_par: str | None = None,
    ) -> ConfigurationAgentPersonnel:
        """Crée ou remplace la configuration d'un compte.

        Raises:
            BusinessValidationError: outil hors contrat, ou instructions trop
                longues (le champ API les borne déjà ; la règle reste ici pour
                les appels internes).
        """
        from app.agents.surcharges import outils_hors_contrat

        definition = self.contrat_effectif()
        retires = sorted({nom for nom in outils_desactives if nom})
        hors_contrat = outils_hors_contrat(definition, retires)
        if hors_contrat:
            raise BusinessValidationError(
                "Outil hors contrat de l'agent personnel : " + ", ".join(hors_contrat),
                details={
                    "outils_hors_contrat": hors_contrat,
                    "outils_du_contrat": sorted(definition.tools),
                },
            )
        texte = (instructions or "").strip() or None
        if texte is not None and len(texte) > INSTRUCTIONS_MAX:
            raise BusinessValidationError(
                f"Instructions trop longues (maximum {INSTRUCTIONS_MAX} caractères)",
                details={"longueur": len(texte), "maximum": INSTRUCTIONS_MAX},
            )

        # La contrainte unique porte sur ``utilisateur_id``, lignes désactivées
        # comprises : on réutilise la ligne existante au lieu d'en insérer une
        # seconde (UniqueViolation après une réinitialisation).
        ligne = self.configurations.get_toute_ligne(utilisateur_id)
        avant = ligne.resume() if ligne is not None else None
        if ligne is None:
            ligne = ConfigurationAgentPersonnel(utilisateur_id=utilisateur_id, actif=True)
            self.configurations.add(ligne)

        ligne.instructions = texte
        ligne.outils_desactives = retires
        ligne.actif = True
        ligne.modifie_par = modifie_par
        self._session.flush()

        self._trace.record_event(
            actor_type=ActorType.HUMAIN.value,
            action=ActionAudit.CONFIGURATION_AGENT_PERSONNEL_MODIFIEE.value,
            entity_type=_ENTITY,
            entity_id=ligne.id,
            after=ligne.resume(),
            event_metadata={"avant": avant},
        )
        return ligne

    def supprimer(self, utilisateur_id: UUID) -> bool:
        """Rend l'agent personnel du compte à son contrat.

        Returns:
            ``True`` si une configuration active existait, ``False`` sinon.
        """
        ligne = self.configurations.get_pour_utilisateur(utilisateur_id)
        if ligne is None:
            return False
        avant = ligne.resume()
        ligne.actif = False
        self._session.flush()
        self._trace.record_event(
            actor_type=ActorType.HUMAIN.value,
            action=ActionAudit.CONFIGURATION_AGENT_PERSONNEL_MODIFIEE.value,
            entity_type=_ENTITY,
            entity_id=ligne.id,
            after={**avant, "actif": False},
            event_metadata={"avant": avant, "raison": "retour au contrat de l'agent personnel"},
        )
        return True

__all__ = ["INSTRUCTIONS_MAX", "AgentPersonnelService"]
