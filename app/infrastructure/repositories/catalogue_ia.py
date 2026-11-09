"""Repositories du catalogue des fournisseurs et modèles d'IA.

Aucune logique métier ici : les règles (unicité, dépendances avant suppression,
chiffrement de la clé) vivent dans
``app.application.services.catalogue_modeles_service``.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.domain.catalogue_ia import FournisseurIA, ModeleIA
from app.infrastructure.repositories.base import BaseRepository


class FournisseurIARepository(BaseRepository[FournisseurIA]):
    model = FournisseurIA

    def lister(self) -> list[FournisseurIA]:
        """Tous les fournisseurs, modèles chargés, ordre d'affichage stable."""
        stmt = (
            select(FournisseurIA)
            .options(selectinload(FournisseurIA.modeles))
            .order_by(FournisseurIA.nom.asc())
        )
        return list(self.session.scalars(stmt).all())

    def par_nom(self, nom: str) -> FournisseurIA | None:
        """Fournisseur portant ce nom (comparaison insensible à la casse)."""
        stmt = select(FournisseurIA).where(FournisseurIA.nom.ilike(nom.strip()))
        return self.session.scalars(stmt).first()

    def obtenir(self, identifiant: uuid.UUID) -> FournisseurIA | None:
        """Fournisseur avec ses modèles chargés."""
        stmt = (
            select(FournisseurIA)
            .options(selectinload(FournisseurIA.modeles))
            .where(FournisseurIA.id == identifiant)
        )
        return self.session.scalars(stmt).first()


class ModeleIARepository(BaseRepository[ModeleIA]):
    model = ModeleIA

    def lister_pour(self, fournisseur_id: uuid.UUID) -> list[ModeleIA]:
        """Modèles d'un fournisseur, par nom."""
        stmt = (
            select(ModeleIA)
            .where(ModeleIA.fournisseur_id == fournisseur_id)
            .order_by(ModeleIA.nom_modele.asc())
        )
        return list(self.session.scalars(stmt).all())

    def obtenir_avec_fournisseur(self, identifiant: uuid.UUID) -> ModeleIA | None:
        """Modèle et son fournisseur, en une requête (application au runtime)."""
        stmt = (
            select(ModeleIA)
            .options(selectinload(ModeleIA.fournisseur))
            .where(ModeleIA.id == identifiant)
        )
        return self.session.scalars(stmt).first()

    def par_nom(self, fournisseur_id: uuid.UUID, nom_modele: str) -> ModeleIA | None:
        """Modèle déjà enregistré sous ce nom chez ce fournisseur."""
        stmt = select(ModeleIA).where(
            ModeleIA.fournisseur_id == fournisseur_id,
            ModeleIA.nom_modele == nom_modele.strip(),
        )
        return self.session.scalars(stmt).first()


__all__ = ["FournisseurIARepository", "ModeleIARepository"]
