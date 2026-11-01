"""Repositories de la base de connaissance (aucune règle métier ici)."""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import delete, func, or_, select

from app.domain.connaissance import (
    ChunkConnaissance,
    ConfigurationEmbedding,
    DocumentConnaissance,
)
from app.domain.enums import StatutIndexationConnaissance
from app.infrastructure.repositories.base import BaseRepository


class DocumentConnaissanceRepository(BaseRepository[DocumentConnaissance]):
    """Accès aux ouvrages de la bibliothèque."""

    model = DocumentConnaissance

    def get_par_checksum(self, checksum: str) -> DocumentConnaissance | None:
        """Ouvrage déjà présent avec ce contenu exact (anti-doublon)."""
        return self.session.scalars(
            select(DocumentConnaissance).where(
                DocumentConnaissance.checksum_sha256 == checksum
            )
        ).first()

    def lister(
        self,
        *,
        recherche: str | None = None,
        categorie: str | None = None,
        statut: str | None = None,
        langue: str | None = None,
        limite: int = 500,
    ) -> list[DocumentConnaissance]:
        """Liste filtrée, du plus récemment ajouté au plus ancien."""
        requete = select(DocumentConnaissance)
        if recherche:
            motif = f"%{recherche.strip()}%"
            requete = requete.where(
                or_(
                    DocumentConnaissance.titre.ilike(motif),
                    DocumentConnaissance.auteur.ilike(motif),
                    DocumentConnaissance.description.ilike(motif),
                )
            )
        if categorie:
            requete = requete.where(DocumentConnaissance.categorie == categorie)
        if statut:
            requete = requete.where(DocumentConnaissance.statut == statut)
        if langue:
            requete = requete.where(DocumentConnaissance.langue == langue)
        requete = requete.order_by(DocumentConnaissance.created_at.desc()).limit(limite)
        return list(self.session.scalars(requete))

    def prochain_a_indexer(self) -> DocumentConnaissance | None:
        """Premier ouvrage en attente, verrouillé pour ce processus.

        ``SKIP LOCKED`` : sur un déploiement multi-instances, chaque worker
        prend un document différent ; aucun n'indexe le même deux fois.
        """
        requete = (
            select(DocumentConnaissance)
            .where(
                DocumentConnaissance.statut
                == StatutIndexationConnaissance.EN_ATTENTE.value
            )
            .order_by(DocumentConnaissance.created_at)
            .limit(1)
        )
        bind = self.session.get_bind()
        if bind is not None and bind.dialect.name == "postgresql":
            requete = requete.with_for_update(skip_locked=True)
        return self.session.scalars(requete).first()

    def compter_par_statut(self) -> dict[str, int]:
        """Répartition des ouvrages par statut d'indexation."""
        lignes = self.session.execute(
            select(DocumentConnaissance.statut, func.count()).group_by(
                DocumentConnaissance.statut
            )
        ).all()
        return {str(statut): int(nombre) for statut, nombre in lignes}

    def totaux(self) -> tuple[int, int]:
        """``(ouvrages, octets)`` — volumétrie de la bibliothèque."""
        ouvrages, octets = self.session.execute(
            select(
                func.count(DocumentConnaissance.id),
                func.coalesce(func.sum(DocumentConnaissance.taille_octets), 0),
            )
        ).one()
        return int(ouvrages), int(octets)

    def ids_tous(self) -> list[UUID]:
        """Identifiants de tous les ouvrages (réindexation globale)."""
        return list(self.session.scalars(select(DocumentConnaissance.id)))

    def langues(self) -> list[str]:
        """Langues réellement présentes (filtre de l'interface, sans plafond de page)."""
        lignes = self.session.scalars(
            select(DocumentConnaissance.langue).distinct().order_by(DocumentConnaissance.langue)
        )
        return [langue for langue in lignes if langue]

    def categories(self) -> list[str]:
        """Catégories réellement présentes (vocabulaire ouvert, jamais figé en code)."""
        lignes = self.session.scalars(
            select(DocumentConnaissance.categorie)
            .where(DocumentConnaissance.categorie.is_not(None))
            .distinct()
            .order_by(DocumentConnaissance.categorie)
        )
        return [categorie for categorie in lignes if categorie]


class ChunkConnaissanceRepository(BaseRepository[ChunkConnaissance]):
    """Passages indexés (le texte fait foi, l'index ne porte que les vecteurs)."""

    model = ChunkConnaissance

    def par_ids(self, ids: Sequence[UUID]) -> dict[UUID, ChunkConnaissance]:
        """Passages par identifiant (une requête pour tous les résultats d'une recherche)."""
        if not ids:
            return {}
        lignes = self.session.scalars(
            select(ChunkConnaissance).where(ChunkConnaissance.id.in_(list(ids)))
        )
        return {ligne.id: ligne for ligne in lignes}

    def supprimer_document(self, document_id: UUID) -> int:
        """Retire tous les passages d'un ouvrage ; renvoie le nombre supprimé."""
        resultat = self.session.execute(
            delete(ChunkConnaissance).where(
                ChunkConnaissance.document_id == document_id
            )
        )
        return int(resultat.rowcount or 0)

    def compter(self) -> int:
        return int(self.session.scalar(select(func.count(ChunkConnaissance.id))) or 0)

    def compter_pour(self, document_id: UUID) -> int:
        return int(
            self.session.scalar(
                select(func.count(ChunkConnaissance.id)).where(
                    ChunkConnaissance.document_id == document_id
                )
            )
            or 0
        )


class ConfigurationEmbeddingRepository(BaseRepository[ConfigurationEmbedding]):
    """Historique des configurations d'embedding (une seule active)."""

    model = ConfigurationEmbedding

    def get_active(self) -> ConfigurationEmbedding | None:
        return self.session.scalars(
            select(ConfigurationEmbedding)
            .where(ConfigurationEmbedding.actif.is_(True))
            .order_by(ConfigurationEmbedding.created_at.desc())
            .limit(1)
        ).first()

    def lister_recentes(self, limite: int = 10) -> list[ConfigurationEmbedding]:
        return list(
            self.session.scalars(
                select(ConfigurationEmbedding)
                .order_by(ConfigurationEmbedding.created_at.desc())
                .limit(limite)
            )
        )


__all__ = [
    "ChunkConnaissanceRepository",
    "ConfigurationEmbeddingRepository",
    "DocumentConnaissanceRepository",
]
