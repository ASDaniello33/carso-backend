"""Index vectoriel local de la base de connaissance (Qdrant embarqué).

Pourquoi Qdrant **local** plutôt qu'un serveur à exploiter : la bibliothèque
CARSO vit sur un poste ou un petit serveur, sans équipe d'exploitation pour un
service de plus. ``qdrant-client`` en mode ``path=`` persiste les vecteurs sur
disque, avec un index HNSW — une recherche sur des centaines de milliers de
passages reste interactive, là où un balayage Python en mémoire ne tiendrait
pas.

Deux conséquences assumées et documentées :

- **un seul processus** peut ouvrir le répertoire (verrou du moteur local) :
  le déploiement visé est mono-worker, comme le reste du backend ;
- l'index n'est **pas** la source de vérité : les passages vivent dans
  PostgreSQL (``chunks_connaissance``). Supprimer le répertoire de vecteurs
  n'efface aucun texte — une réindexation le reconstruit.

La ``signature`` du modèle d'embedding est écrite **à côté** du répertoire
Qdrant : si elle diffère de la configuration courante, l'appelant refuse de
mélanger deux espaces vectoriels (résultats faux en silence).
"""

from __future__ import annotations

import threading
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from app.core.errors import CarsoError, ConflictError, NotFoundError

#: Nom de collection : stable, une seule bibliothèque par installation.
COLLECTION = "connaissances"

#: Taille d'un envoi (Qdrant accepte davantage ; ce lot garde la requête bornée).
LOT_ENVOI = 256


class IndexIndisponibleError(CarsoError):
    """L'index vectoriel ne peut pas être ouvert (paquet absent, disque)."""


@dataclass(frozen=True, slots=True)
class PointVecteur:
    """Un passage prêt pour l'index : identifiant, vecteur, payload filtrable."""

    chunk_id: str
    document_id: str
    position: int
    vecteur: tuple[float, ...]
    page: int | None = None
    section: str | None = None
    categorie: str | None = None
    langue: str = "fr"
    titre: str = ""


@dataclass(frozen=True, slots=True)
class ResultatVecteur:
    """Correspondance vectorielle (le texte est relu en base, pas dans l'index)."""

    chunk_id: str
    document_id: str
    score: float
    page: int | None = None
    section: str | None = None


class IndexVecteurs(Protocol):
    """Contrat de l'index : testable avec une implémentation en mémoire."""

    def signature(self) -> str | None: ...

    def assurer_collection(self, signature: str, dimensions: int) -> None: ...

    def reinitialiser(self, signature: str, dimensions: int) -> None: ...

    def remplacer(self, document_id: str, points: Sequence[PointVecteur]) -> None: ...

    def supprimer(self, document_id: str) -> None: ...

    def rechercher(
        self,
        vecteur: Sequence[float],
        *,
        limite: int,
        document_id: str | None = None,
        categorie: str | None = None,
    ) -> list[ResultatVecteur]: ...

    def compter(self) -> int: ...


class IndexQdrant:
    """Implémentation Qdrant embarquée (persistée sous ``chemin``)."""

    def __init__(self, chemin: str | Path, *, collection: str = COLLECTION) -> None:
        self._chemin = Path(chemin)
        self._collection = collection
        self._client: Any | None = None
        self._verrou = threading.RLock()

    # --- cycle de vie -----------------------------------------------------

    @property
    def chemin(self) -> Path:
        return self._chemin

    def _fichier_signature(self) -> Path:
        # À côté du répertoire Qdrant, pas dedans : le moteur possède son dossier.
        return self._chemin.parent / f"{self._chemin.name}.signature"

    def _ouvrir(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            from qdrant_client import QdrantClient
        except ImportError as exc:  # pragma: no cover - dépend du venv
            msg = (
                "Index vectoriel indisponible : le paquet 'qdrant-client' n'est pas "
                "installé (pip install -e '.[connaissances]')"
            )
            raise IndexIndisponibleError(msg) from exc
        try:
            self._chemin.mkdir(parents=True, exist_ok=True)
            self._client = QdrantClient(path=str(self._chemin))
        except Exception as exc:  # noqa: BLE001 — verrou de répertoire, droits, disque
            msg = (
                "Ouverture de l'index vectoriel impossible : le répertoire est "
                "peut-être déjà utilisé par un autre processus, ou inaccessible"
            )
            raise IndexIndisponibleError(msg, details={"cause": type(exc).__name__}) from exc
        return self._client

    def fermer(self) -> None:
        """Libère le répertoire (arrêt propre, tests)."""
        with self._verrou:
            client, self._client = self._client, None
        if client is not None:
            try:
                client.close()
            except Exception:  # noqa: BLE001 — une fermeture ne fait jamais échouer le service
                pass

    # --- collection -------------------------------------------------------

    def signature(self) -> str | None:
        """Signature du modèle ayant produit les vecteurs présents."""
        fichier = self._fichier_signature()
        if not fichier.is_file():
            return None
        return fichier.read_text(encoding="utf-8").strip() or None

    def _existe(self, client: Any) -> bool:
        try:
            return bool(client.collection_exists(self._collection))
        except Exception:  # noqa: BLE001 — anciennes versions : lister les collections
            collections = client.get_collections().collections
            return any(item.name == self._collection for item in collections)

    def _creer(self, client: Any, dimensions: int) -> None:
        from qdrant_client.http import models as qmodels

        client.create_collection(
            collection_name=self._collection,
            vectors_config=qmodels.VectorParams(
                size=int(dimensions), distance=qmodels.Distance.COSINE
            ),
        )

    def _ecrire_signature(self, signature: str) -> None:
        self._fichier_signature().write_text(signature, encoding="utf-8")

    def assurer_collection(self, signature: str, dimensions: int) -> None:
        """Crée la collection si besoin ; refuse un index construit autrement.

        Raises:
            ConflictError: l'index existant vient d'un autre modèle — le
                mélanger produirait des scores comparables mais faux.
        """
        with self._verrou:
            client = self._ouvrir()
            if not self._existe(client):
                self._creer(client, dimensions)
                self._ecrire_signature(signature)
                return
            existante = self.signature()
            if existante and existante != signature:
                raise ConflictError(
                    "L'index vectoriel a été construit avec un autre modèle "
                    "d'embedding : une réindexation complète est nécessaire",
                    details={"index": existante, "attendu": signature},
                )
            if not existante:
                self._ecrire_signature(signature)

    def reinitialiser(self, signature: str, dimensions: int) -> None:
        """Repart d'un index **vide** pour un nouveau modèle.

        Le stockage local de Qdrant ne supprime pas réellement les données d'une
        collection supprimée : recréer le même nom ressuscite les anciens
        vecteurs (constaté sur qdrant-client 1.20). Le répertoire est donc
        effacé — geste sûr, l'index n'est qu'une projection reconstructible des
        passages PostgreSQL et des fichiers d'origine.

        Raises:
            IndexIndisponibleError: répertoire verrouillé (autre client) ou
                impossible à supprimer.
        """
        import shutil

        with self._verrou:
            self.fermer()
            if self._chemin.exists():
                try:
                    shutil.rmtree(self._chemin)
                except OSError as exc:
                    msg = (
                        "Réinitialisation de l'index vectoriel impossible : le "
                        "répertoire est encore utilisé par un autre processus"
                    )
                    raise IndexIndisponibleError(msg) from exc
            self._fichier_signature().unlink(missing_ok=True)
            client = self._ouvrir()
            self._creer(client, dimensions)
            self._ecrire_signature(signature)

    # --- écriture ---------------------------------------------------------

    def remplacer(self, document_id: str, points: Sequence[PointVecteur]) -> None:
        """Remplace **tous** les passages d'un ouvrage (aucun doublon croisé)."""
        self.supprimer(document_id)
        if not points:
            return
        from qdrant_client.http import models as qmodels

        with self._verrou:
            client = self._ouvrir()
            for debut in range(0, len(points), LOT_ENVOI):
                lot = points[debut : debut + LOT_ENVOI]
                client.upsert(
                    collection_name=self._collection,
                    points=[
                        qmodels.PointStruct(
                            id=point.chunk_id,
                            vector=list(point.vecteur),
                            payload={
                                "document_id": point.document_id,
                                "chunk_id": point.chunk_id,
                                "position": point.position,
                                "page": point.page,
                                "section": point.section,
                                "categorie": point.categorie,
                                "langue": point.langue,
                                "titre": point.titre,
                            },
                        )
                        for point in lot
                    ],
                    wait=True,
                )

    def supprimer(self, document_id: str) -> None:
        """Retire les passages d'un ouvrage (idempotent)."""
        from qdrant_client.http import models as qmodels

        with self._verrou:
            client = self._ouvrir()
            if not self._existe(client):
                return
            client.delete(
                collection_name=self._collection,
                points_selector=qmodels.FilterSelector(
                    filter=qmodels.Filter(
                        must=[
                            qmodels.FieldCondition(
                                key="document_id",
                                match=qmodels.MatchValue(value=document_id),
                            )
                        ]
                    )
                ),
                wait=True,
            )

    # --- lecture ----------------------------------------------------------

    def rechercher(
        self,
        vecteur: Sequence[float],
        *,
        limite: int,
        document_id: str | None = None,
        categorie: str | None = None,
    ) -> list[ResultatVecteur]:
        """Recherche les passages les plus proches, filtres appliqués dans l'index."""
        from qdrant_client.http import models as qmodels

        with self._verrou:
            client = self._ouvrir()
            if not self._existe(client):
                raise NotFoundError(
                    "Aucun ouvrage indexé : la base de connaissance est vide "
                    "(ou son index doit être reconstruit)"
                )
            conditions: list[Any] = []
            if document_id:
                conditions.append(
                    qmodels.FieldCondition(
                        key="document_id", match=qmodels.MatchValue(value=document_id)
                    )
                )
            if categorie:
                conditions.append(
                    qmodels.FieldCondition(
                        key="categorie", match=qmodels.MatchValue(value=categorie)
                    )
                )
            reponse = client.query_points(
                collection_name=self._collection,
                query=list(vecteur),
                limit=max(1, int(limite)),
                query_filter=qmodels.Filter(must=conditions) if conditions else None,
                with_payload=True,
            )
        resultats: list[ResultatVecteur] = []
        for point in reponse.points:
            payload = point.payload or {}
            resultats.append(
                ResultatVecteur(
                    chunk_id=str(payload.get("chunk_id") or point.id),
                    document_id=str(payload.get("document_id") or ""),
                    score=float(point.score),
                    page=payload.get("page"),
                    section=payload.get("section"),
                )
            )
        return resultats

    def compter(self) -> int:
        """Nombre de passages vectorisés (0 si la collection n'existe pas)."""
        with self._verrou:
            client = self._ouvrir()
            if not self._existe(client):
                return 0
            return int(client.count(self._collection, exact=True).count)


#: Index partagé par le processus : le moteur local n'accepte qu'un client par
#: répertoire, et le service comme le worker doivent voir le même.
_INDEX: IndexQdrant | None = None
_VERROU_INDEX = threading.Lock()


def obtenir_index(chemin: str | Path | None = None) -> IndexQdrant:
    """Index du processus (construit une fois, réutilisé)."""
    global _INDEX
    from app.connaissances.racine import repertoire_vecteurs

    cible = Path(chemin) if chemin is not None else repertoire_vecteurs()
    with _VERROU_INDEX:
        if _INDEX is not None and _INDEX.chemin == cible:
            return _INDEX
        if _INDEX is not None:
            _INDEX.fermer()
        _INDEX = IndexQdrant(cible)
        return _INDEX


def reinitialiser_index_processus() -> None:
    """Ferme et oublie l'index partagé (tests, changement de répertoire)."""
    global _INDEX
    with _VERROU_INDEX:
        if _INDEX is not None:
            _INDEX.fermer()
        _INDEX = None


__all__ = [
    "COLLECTION",
    "IndexIndisponibleError",
    "IndexQdrant",
    "IndexVecteurs",
    "PointVecteur",
    "ResultatVecteur",
    "obtenir_index",
    "reinitialiser_index_processus",
]
