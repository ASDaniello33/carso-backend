"""Skill de l'organisation — un guide court, nommé ``/nom`` dans le chat.

Un skill CARSO livré avec le paquet (``app/agents/skills/<nom>/SKILL.md``) est
un document de développement : il voyage dans l'image, il est versionné avec le
code, il est chargé à la demande par Deep Agents. Un **skill d'organisation**
est l'inverse : une consigne de travail commune (« nos offres se terminent
par … », « pour nos fiches de présence, … »), écrite par un administrateur dans
Paramètres > Skills, bornée, et portée par le prompt de **tous** les runs.

Pourquoi l'écriture est réservée à l'administration : un skill change la façon
dont **tous** les agents répondent aux comptes. Un utilisateur peut l'invoquer
(``/nom``), jamais le redéfinir — sinon le fonctionnement des agents serait
modifiable depuis n'importe quelle conversation.

Un skill n'est ni un droit, ni un outil : il ne peut rien autoriser. Il ajoute
du texte au prompt système ; le harnais garde son allow-list, les services
gardent leur périmètre.

Aucun secret n'a sa place ici : le contenu est renvoyé par l'API et recopié
(borné) dans l'audit.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.base import Base, TimestampMixin, UuidPkMixin

#: Longueur maximale du texte d'un skill — un guide court, pas un manuel.
#: Le prompt de **chaque** run le porte : la borne est un budget.
INSTRUCTIONS_MAX = 2_000

#: Nombre maximal de skills (même raison : le prompt de chaque run).
MAX_SKILLS = 8


class Skill(Base, UuidPkMixin, TimestampMixin):
    """Guide court de l'organisation, nommé ``/nom`` dans le chat.

    Attributes:
        nom: identifiant tapé après ``/`` (minuscules, chiffres, tirets).
            Unique : ``/facturation`` ne peut désigner deux textes, et
            l'unicité est globale puisqu'aucun compte n'est propriétaire.
        description: une phrase qui dit **quand** appliquer le skill (affichée
            dans le menu du chat et dans le prompt de l'agent).
        instructions: le texte du skill, borné par le service
            (``INSTRUCTIONS_MAX``) : un guide, pas un manuel.
        modifie_par: identifiant du compte à l'origine de la dernière écriture
            (ligne d'audit : « qui a changé le comportement des agents »).
    """

    __tablename__ = "skills"
    __table_args__ = (
        Index("uq_skills_nom", "nom", unique=True),
    )

    nom: Mapped[str] = mapped_column(String(40), nullable=False)
    description: Mapped[str] = mapped_column(String(200), nullable=False)
    instructions: Mapped[str] = mapped_column(Text, nullable=False)
    modifie_par: Mapped[str | None] = mapped_column(String(64))

    def resume(self) -> dict[str, Any]:
        """Vue sûre pour l'audit et l'API (aucun objet ORM)."""
        return {
            "nom": self.nom,
            "description": self.description,
            "instructions": self.instructions,
        }


__all__ = ["INSTRUCTIONS_MAX", "MAX_SKILLS", "Skill"]
