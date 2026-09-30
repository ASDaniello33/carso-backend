"""Configuration runtime des agents (modèle / provider) — persistée et versionnée.

Pourquoi une table et pas seulement ``.env`` : l'administrateur doit pouvoir
changer le modèle **à chaud**, sans redémarrage du backend, et le système doit
mémoriser la dernière configuration pour la recharger au démarrage suivant
(instruction/05 §3 : le modèle est une décision de déploiement, pas de contrat
métier).

La clé du provider (``agent_api_key``) peut désormais être **saisie par
l'administrateur** depuis la page Paramètres. Elle n'est jamais stockée en clair :
``api_key_chiffree`` contient un jeton Fernet (``app.core.chiffrement``) et la
valeur en clair n'est déchiffrée qu'au moment de construire le modèle. La clé du
``.env`` reste le repli quand aucune clé administrée n'est enregistrée.

Une seule ligne est ``actif=True`` à un instant donné : c'est l'unique
configuration appliquée au runtime. Chaque modification crée une nouvelle ligne
(historique conservé) et désactive la précédente — l'audit reste lisible.

``SurchargeAgent`` porte les réglages **par agent** (outils désactivés,
instructions supplémentaires, skills ajoutés). Ces surcharges n'élargissent
jamais le contrat : elles ne peuvent que retirer un outil déclaré, jamais en
ajouter un que l'agent n'a pas signé (``AgentDefinition.tools``).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import Boolean, Float, Index, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.base import Base, TimestampMixin, UuidPkMixin


class ConfigurationRuntimeAgent(Base, UuidPkMixin, TimestampMixin):
    """Paramètres d'exécution des agents applicables à chaud par un administrateur."""

    __tablename__ = "configurations_runtime_agent"
    __table_args__ = (
        Index("ix_configurations_runtime_agent_actif", "actif"),
        Index("ix_configurations_runtime_agent_created_at", "created_at"),
    )

    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    model: Mapped[str] = mapped_column(String(150), nullable=False)
    base_url: Mapped[str | None] = mapped_column(String(500))
    temperature: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0, server_default=text("0")
    )
    max_iterations: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1000, server_default=text("1000")
    )
    actif: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    # Identifiant de l'administrateur à l'origine du changement (pas de FK :
    # même convention que ``Utilisateur.approved_by``).
    modifie_par: Mapped[str | None] = mapped_column(String(64))
    # Clé du provider chiffrée au repos (jeton Fernet). Jamais renvoyée par
    # l'API, jamais journalisée : l'interface affiche seulement si une clé
    # administrée est enregistrée.
    api_key_chiffree: Mapped[str | None] = mapped_column(String(1000))


class SurchargeAgent(Base, UuidPkMixin, TimestampMixin):
    """Réglages administrés d'un agent, appliqués par-dessus son contrat.

    Trois leviers, tous **restrictifs ou additifs sans élargir les droits** :

    - ``outils_desactives`` : noms d'outils retirés de l'allow-list de l'agent
      (sous-ensemble de ``AgentDefinition.tools`` — un nom hors contrat est
      refusé, jamais « ajouté ») ;
    - ``instructions_supplementaires`` : consignes ajoutées au system prompt
      (précisions de conduite, ton, vocabulaire CARSO) ;
    - ``skills_ajoutes`` : noms de skills Deep Agents chargés en plus.

    Une seule ligne par agent (``agent_id`` unique) : c'est la surcharge courante.
    La supprimer rend l'agent à son contrat.
    """

    __tablename__ = "surcharges_agent"
    __table_args__ = (
        Index("uq_surcharges_agent_agent_id", "agent_id", unique=True),
    )

    agent_id: Mapped[str] = mapped_column(String(100), nullable=False)
    # Pas de ``server_default`` sur les colonnes JSONB : la valeur par défaut est
    # portée par le Python (``default=list``), comme partout ailleurs dans le
    # modèle. Un ``DEFAULT '[]'::jsonb`` est propre à PostgreSQL et casserait la
    # création du schéma sur SQLite (tests).
    outils_desactives: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    instructions_supplementaires: Mapped[str | None] = mapped_column(Text)
    skills_ajoutes: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    actif: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    modifie_par: Mapped[str | None] = mapped_column(String(64))

    def resume(self) -> dict[str, Any]:
        """Vue sûre pour l'audit et l'API (aucun objet ORM)."""
        return {
            "agent_id": self.agent_id,
            "outils_desactives": list(self.outils_desactives or []),
            "instructions_supplementaires": self.instructions_supplementaires,
            "skills_ajoutes": list(self.skills_ajoutes or []),
            "actif": bool(self.actif),
        }
