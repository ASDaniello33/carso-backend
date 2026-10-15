"""Configuration personnelle de l'agent personnel—un réglage par compte.

Pourquoi une table—l'agent personnel est **unique** côté code (contrat
``agent_personnel``) mais chacun le règle pour soi—instructions propres et
outils retirés. Ces réglages n'ont rien à faire dans ``surcharges_agent``
(réglages d'administration, une ligne par agent, appliqués à tous les comptes) :
les mêler ferait qu'un compte réglerait l'agent des autres.

Deux leviers, tous deux **restrictifs ou additifs sans élargir les droits** :

- ``instructions``—consignes ajoutées au system prompt du compte (ton,
  vocabulaire, habitudes de travail). Elles ne remplacent jamais le cadre CARSO
  ni les règles de lecture ;
- ``outils_desactives``—sous-ensemble des outils du contrat que le compte
  choisit de ne pas exposer (lecture seule—le contrat seul décide de ce qui
  existe, cette liste ne peut que retirer).

Une seule ligne par compte (``utilisateur_id`` unique). La supprimer rend
l'agent à son contrat. Aucun secret n'y est stocké.
"""

from __future__ import annotations

from hashlib import sha256
from typing import Any
from uuid import UUID

from sqlalchemy import Boolean, ForeignKey, Index, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.base import Base, TimestampMixin, UuidPkMixin

#: Empreinte d'une configuration **absente ou inactive** : un compte sans
#: réglage partage le graphe « contrat nu » avec les autres comptes sans
#: réglage ? Non — voir ``signature_configuration`` : la clé de graphe des
#: agents par utilisateur porte de toute façon l'identifiant du compte.
SIGNATURE_ABSENTE = "-"


class ConfigurationAgentPersonnel(Base, UuidPkMixin, TimestampMixin):
    """Réglages d'un compte pour **son** agent personnel.

    Attributes:
        utilisateur_id: compte propriétaire — unique, jamais fourni par le
            client (lu sur le jeton).
        instructions: consignes ajoutées au system prompt (jamais un droit).
        outils_desactives: noms d'outils retirés du contrat effectif (un nom
            hors contrat est refusé par le service, avec la liste valide).
        actif: faux = configuration désactivée (retour au contrat) ; la ligne
            est conservée pour ne pas recréer d'historique incohérent.
        modifie_par: identifiant du compte à l'origine du dernier changement.
    """

    __tablename__ = "configurations_agent_personnel"
    __table_args__ = (
        Index(
            "uq_configurations_agent_personnel_utilisateur",
            "utilisateur_id",
            unique=True,
        ),
    )

    utilisateur_id: Mapped[UUID] = mapped_column(
        ForeignKey("utilisateurs.id", ondelete="CASCADE"), nullable=False
    )
    instructions: Mapped[str | None] = mapped_column(Text)
    # Pas de ``server_default`` JSONB (dialecte PostgreSQL) : la valeur par
    # défaut vit côté Python, comme ailleurs dans le modèle — le schéma se crée
    # aussi sur SQLite (tests).
    outils_desactives: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    actif: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    modifie_par: Mapped[str | None] = mapped_column(String(64))

    def resume(self) -> dict[str, Any]:
        """Vue sûre pour l'audit et l'API (aucun objet ORM)."""
        return {
            "utilisateur_id": str(self.utilisateur_id),
            "instructions": self.instructions,
            "outils_desactives": list(self.outils_desactives or []),
            "actif": bool(self.actif),
        }


def signature_configuration(config: ConfigurationAgentPersonnel | None) -> str:
    """Empreinte stable de la configuration d'un compte (clé de cache des graphes).

    Deux configurations identiques rendent la même empreinte — le graphe est
    réutilisé ; une instruction modifiée, un outil retiré ou une réactivation
    change l'empreinte — le graphe est reconstruit au prochain run. Une
    configuration absente ou inactive rend ``-`` : le contrat nu.
    """
    if config is None or not config.actif:
        return SIGNATURE_ABSENTE
    charge = "\x1f".join(
        [
            (config.instructions or "").strip(),
            "|".join(sorted(config.outils_desactives or [])),
        ]
    )
    return sha256(charge.encode("utf-8")).hexdigest()[:16]


__all__ = ["SIGNATURE_ABSENTE", "ConfigurationAgentPersonnel", "signature_configuration"]
