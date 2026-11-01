"""Déroulé d'indexation d'un ouvrage (lecture → passages → vecteurs → base).

Un seul chemin, utilisé par le worker de fond **et** par une réindexation
demandée : le comportement ne dépend pas du déclencheur.

Ordre des écritures, choisi pour rester réparable :

1. les vecteurs sont remplacés **avant** les lignes de passages : une recherche
   ne renvoie donc jamais un passage absent de l'index ;
2. les lignes ``chunks_connaissance`` sont ensuite remplacées dans la même
   transaction ;
3. l'état du document est mis à jour en dernier.

En cas d'échec, le document passe en ``erreur`` avec un message lisible ; les
passages déjà indexés restent en place (une recherche continue de fonctionner
sur la version précédente) et une réindexation manuelle répare.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.connaissances.decoupage import decouper
from app.connaissances.embeddings import Embedder
from app.connaissances.lecture import lire_segments
from app.connaissances.racine import resoudre_fichier
from app.connaissances.vecteurs import IndexVecteurs, PointVecteur
from app.core.config import Settings, get_settings
from app.core.errors import CarsoError, NotFoundError
from app.domain.connaissance import ChunkConnaissance, DocumentConnaissance
from app.domain.enums import ActionAudit, ActorType, StatutIndexationConnaissance

logger = logging.getLogger(__name__)

#: Entité d'audit (stable : le journal filtre dessus).
ENTITY = "document_connaissance"


@dataclass(frozen=True, slots=True)
class RapportIndexation:
    """Résultat d'une indexation (succès ou échec), lisible par l'API."""

    document_id: str
    statut: str
    nombre_chunks: int = 0
    nombre_caracteres: int = 0
    signature: str | None = None
    message: str | None = None
    tronque: bool = False


def _borner_segments(segments: list[Any], plafond: int) -> tuple[list[Any], bool]:
    """Coupe la lecture au plafond de caractères (l'ouvrage reste signalé tronqué)."""
    gardes: list[Any] = []
    total = 0
    for segment in segments:
        reste = plafond - total
        if reste <= 0:
            return gardes, True
        if len(segment.texte) <= reste:
            gardes.append(segment)
            total += len(segment.texte)
            continue
        from dataclasses import replace

        gardes.append(replace(segment, texte=segment.texte[:reste]))
        return gardes, True
    return gardes, False


class IndexationConnaissances:
    """Indexe un ``DocumentConnaissance`` déjà enregistré (fichier présent)."""

    def __init__(
        self,
        session: Session,
        *,
        embedder: Embedder,
        index: IndexVecteurs,
        racine: Path | None = None,
        settings: Settings | None = None,
    ) -> None:
        self._session = session
        self._embedder = embedder
        self._index = index
        self._cfg = settings or get_settings()
        if racine is None:
            from app.connaissances.racine import racine_connaissances

            racine = racine_connaissances(self._cfg)
        self._racine = racine

    def indexer(self, document: DocumentConnaissance) -> RapportIndexation:
        """Indexe (ou réindexe) un ouvrage ; ne commite pas (transaction appelante)."""
        document.statut = StatutIndexationConnaissance.INDEXATION.value
        self._session.flush()
        try:
            rapport = self._executer(document)
        except (CarsoError, OSError) as exc:
            return self._echouer(document, exc)
        except Exception as exc:  # noqa: BLE001 — un texte imprévu ne doit pas casser le worker
            logger.exception("Indexation de %s : échec inattendu", document.id)
            return self._echouer(document, exc)
        self._tracer(ActionAudit.CONNAISSANCE_INDEXEE, document, rapport)
        return rapport

    # --- internes ---------------------------------------------------------

    def _executer(self, document: DocumentConnaissance) -> RapportIndexation:
        chemin = resoudre_fichier(self._racine, document.chemin_stockage)
        if not chemin.is_file():
            raise NotFoundError(
                "Le fichier de cet ouvrage est absent du répertoire géré",
                details={"nom_fichier": document.nom_fichier},
            )

        segments, tronque = _borner_segments(
            lire_segments(chemin, nom_fichier=document.nom_fichier),
            self._cfg.knowledge_max_caracteres,
        )
        passages = decouper(
            segments,
            taille=self._cfg.knowledge_chunk_taille,
            chevauchement=self._cfg.knowledge_chunk_chevauchement,
        )
        if len(passages) > self._cfg.knowledge_max_chunks:
            passages = passages[: self._cfg.knowledge_max_chunks]
            tronque = True
        if not passages:
            raise CarsoError("Découpage vide : aucun passage indexable")

        configuration = self._embedder.configuration
        self._index.assurer_collection(
            configuration.signature, configuration.dimensions
        )
        vecteurs = self._embedder.vectoriser([passage.texte for passage in passages])
        if len(vecteurs) != len(passages):
            raise CarsoError(
                "Le provider d'embedding n'a pas renvoyé un vecteur par passage",
                details={"attendu": len(passages), "recu": len(vecteurs)},
            )

        # L'identifiant du point est celui du passage en base : un seul espace
        # d'identifiants entre PostgreSQL et l'index.
        import uuid as _uuid

        lignes: list[ChunkConnaissance] = []
        points: list[PointVecteur] = []
        for passage, vecteur in zip(passages, vecteurs, strict=True):
            identifiant = _uuid.uuid4()
            lignes.append(
                ChunkConnaissance(
                    id=identifiant,
                    document_id=document.id,
                    position=passage.position,
                    page=passage.page,
                    section=passage.section,
                    contenu=passage.texte,
                    nombre_caracteres=len(passage.texte),
                    signature_embedding=configuration.signature,
                )
            )
            points.append(
                PointVecteur(
                    chunk_id=str(identifiant),
                    document_id=str(document.id),
                    position=passage.position,
                    vecteur=tuple(float(valeur) for valeur in vecteur),
                    page=passage.page,
                    section=passage.section,
                    categorie=document.categorie,
                    langue=document.langue,
                    titre=document.titre,
                )
            )

        self._index.remplacer(str(document.id), points)
        self._session.execute(
            delete(ChunkConnaissance).where(ChunkConnaissance.document_id == document.id)
        )
        self._session.add_all(lignes)

        pages = [passage.page for passage in passages if passage.page]
        document.nombre_chunks = len(lignes)
        document.nombre_caracteres = sum(ligne.nombre_caracteres for ligne in lignes)
        document.nombre_pages = max(pages) if pages else document.nombre_pages
        document.signature_embedding = configuration.signature
        document.date_indexation = datetime.now(UTC)
        document.erreur = None
        document.statut = StatutIndexationConnaissance.INDEXE.value
        self._session.flush()
        return RapportIndexation(
            document_id=str(document.id),
            statut=document.statut,
            nombre_chunks=document.nombre_chunks,
            nombre_caracteres=document.nombre_caracteres,
            signature=configuration.signature,
            tronque=tronque,
        )

    def _echouer(self, document: DocumentConnaissance, exc: Exception) -> RapportIndexation:
        message = str(exc) or type(exc).__name__
        if isinstance(exc, OSError):
            message = "Fichier illisible sur le disque"
        document.statut = StatutIndexationConnaissance.ERREUR.value
        document.erreur = message[:2000]
        self._session.flush()
        rapport = RapportIndexation(
            document_id=str(document.id),
            statut=document.statut,
            nombre_chunks=document.nombre_chunks,
            signature=document.signature_embedding,
            message=message,
        )
        self._tracer(ActionAudit.CONNAISSANCE_INDEXATION_ECHOUEE, document, rapport)
        return rapport

    def _tracer(
        self,
        action: ActionAudit,
        document: DocumentConnaissance,
        rapport: RapportIndexation,
    ) -> None:
        from app.application.trace import TraceContext

        TraceContext(self._session, actor_id="systeme:indexation").record_event(
            actor_type=ActorType.SYSTEME.value,
            action=action.value,
            entity_type=ENTITY,
            entity_id=document.id,
            after={
                "titre": document.titre,
                "statut": rapport.statut,
                "nombre_chunks": rapport.nombre_chunks,
                "signature": rapport.signature,
                "tronque": rapport.tronque,
                "erreur": rapport.message,
            },
        )


__all__ = ["ENTITY", "IndexationConnaissances", "RapportIndexation"]
