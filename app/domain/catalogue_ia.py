"""Catalogue des fournisseurs d'IA et des modèles enregistrés.

**Pourquoi.** La configuration runtime (`configurations_runtime_agent`) ne garde
qu'**une** ligne active : changer de modèle obligeait l'administrateur à
ressaisir provider, URL de base et clé. Le catalogue mémorise les connexions
une fois et laisse choisir un modèle déjà enregistré.

Deux niveaux, comme le veut le vocabulaire du produit :

- ``FournisseurIA`` : la **connexion** (protocole, URL de base, clé chiffrée).
  Sa clé sert tous ses modèles : une clé renouvelée se corrige à un seul endroit.
- ``ModeleIA`` : un identifiant de modèle chez ce fournisseur. Le nom est unique
  par fournisseur — enregistrer deux fois le même modèle est impossible.

Aucun secret en clair : ``api_key_chiffree`` porte un jeton Fernet
(``app.core.chiffrement``), comme la configuration runtime. Rien n'est renvoyé
par l'API au-delà du drapeau « une clé est enregistrée ».

La configuration runtime reste la **source de vérité de ce qui est appliqué** :
le catalogue propose, ``AgentRuntimeService.enregistrer`` applique (et trace).
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import Boolean, ForeignKey, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.domain.base import Base, TimestampMixin, UuidPkMixin

#: Protocoles reconnus par le catalogue. Ils correspondent aux providers de
#: ``app.agents.providers.SUPPORTED_PROVIDERS`` : le catalogue ne crée pas de
#: famille de connexion que le runtime ne saurait pas construire.
PROTOCOLES_IA: tuple[str, ...] = ("openai", "openai_compatible", "google_genai")


class FournisseurIA(Base, UuidPkMixin, TimestampMixin):
    """Connexion à un fournisseur d'IA (une clé, une URL de base)."""

    __tablename__ = "fournisseurs_ia"
    __table_args__ = (
        # Un fournisseur = une connexion nommée : deux connexions au même
        # service (deux clés, deux URL) restent possibles, mais sous deux noms.
        Index("uq_fournisseurs_ia_nom", "nom", unique=True),
    )

    nom: Mapped[str] = mapped_column(String(120), nullable=False)
    #: ``openai``, ``openai_compatible`` ou ``google_genai`` (voir ``PROTOCOLES_IA``).
    protocole: Mapped[str] = mapped_column(String(50), nullable=False)
    base_url: Mapped[str | None] = mapped_column(String(500))
    #: Jeton Fernet. Jamais renvoyé, jamais journalisé.
    api_key_chiffree: Mapped[str | None] = mapped_column(String(1000))
    actif: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    modifie_par: Mapped[str | None] = mapped_column(String(64))

    modeles: Mapped[list[ModeleIA]] = relationship(
        back_populates="fournisseur",
        cascade="all, delete-orphan",
        order_by="ModeleIA.nom_modele",
    )

    def resume(self) -> dict[str, Any]:
        """Vue sûre pour l'API et l'audit (aucun secret, aucun objet ORM)."""
        return {
            "id": str(self.id),
            "nom": self.nom,
            "protocole": self.protocole,
            "base_url": self.base_url,
            "cle_enregistree": bool(self.api_key_chiffree),
            "actif": bool(self.actif),
        }


class ModeleIA(Base, UuidPkMixin, TimestampMixin):
    """Modèle enregistré chez un fournisseur (identifiant + nom d'usage)."""

    __tablename__ = "modeles_ia"
    __table_args__ = (
        # Pas de doublon : le couple (fournisseur, nom du modèle) est unique.
        Index("uq_modeles_ia_fournisseur_nom", "fournisseur_id", "nom_modele", unique=True),
    )

    # Type déduit de la colonne référencée (``Uuid``) : le schéma se crée
    # aussi sur SQLite, comme le reste du domaine.
    fournisseur_id: Mapped[UUID] = mapped_column(
        ForeignKey("fournisseurs_ia.id", ondelete="CASCADE"), nullable=False
    )
    #: Identifiant envoyé au fournisseur (``gpt-5.1``, ``deepseek-v4-flash``…).
    nom_modele: Mapped[str] = mapped_column(String(150), nullable=False)
    #: Libellé d'affichage facultatif (sinon le nom technique).
    libelle: Mapped[str | None] = mapped_column(String(150))
    actif: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    modifie_par: Mapped[str | None] = mapped_column(String(64))

    fournisseur: Mapped[FournisseurIA] = relationship(back_populates="modeles")

    def resume(self) -> dict[str, Any]:
        """Vue sûre pour l'API et l'audit."""
        return {
            "id": str(self.id),
            "nom_modele": self.nom_modele,
            "libelle": self.libelle,
            "actif": bool(self.actif),
        }


__all__ = ["PROTOCOLES_IA", "FournisseurIA", "ModeleIA"]
