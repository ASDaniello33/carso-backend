"""Import d'une **image** externe trouvée sur le web dans CARSO.

Le geste appartient à l'**utilisateur**—il voit une image dans un écran de
lecture et décide de la faire entrer dans CARSO. Aucun agent ne déclenche cet
import—c'est une décision humaine, et c'est aussi ce qui garde la surface
d'attaque du rapatriement (SSRF) sous contrôle—le modèle ne choisit pas
l'adresse qui sera appelée.

**Les vidéos ne sont jamais rapatriées** (correctif de spécification du
08/10/2026)—elles se téléchargent depuis la source vers le disque de
l'utilisateur. Un fichier gardé se dépose ensuite comme document, par le dépôt
ordinaire.

Ce que le service garantit, dans cet ordre :

1. **rapatriement contrôlé** (``app.documents.telechargement``)—schéma http(s),
   hôte public résolu, redirections revalidées, taille bornée, contenu reconnu à
   sa signature (une image ; une vidéo est refusée) ;
2. **type réel**—l'extension et le MIME enregistrés viennent de la signature
   des octets, jamais du ``Content-Type`` annoncé par le site distant ;
3. **fiche documentaire complète** par le service documentaire—chemin logique,
   checksum SHA-256, taille, MIME, version, statut, auteur, horodatage—et le
   **rattachement** fourni par l'appelant (au plus une ancre métier), qui décide
   du dossier de classement ;
4. **provenance conservée**—l'URL d'origine et l'URL finale après redirections
   sont écrites dans ``doc_metadata.provenance``—un média sans sa source est un
   média qu'on ne peut plus vérifier ;
5. **aucune officialisation**—le dépôt suit les règles du service documentaire
   (règle 6 [C])—un import humain naît ``draft`` et ne devient un document
   officiel que par la chaîne de validation existante (soumission, approbation).
"""

from __future__ import annotations

from datetime import UTC, datetime
from io import BytesIO
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy.orm import Session

from app.application.dto import DocumentUploadInput, ImportMediaInput
from app.application.services.document_service import DocumentService
from app.core.config import get_settings
from app.documents.storage import DocumentStorage
from app.documents.telechargement import recuperer_media
from app.domain.document import Document
from app.domain.enums import TypeDocument

#: Clé de provenance d'une fiche documentaire (même style que les autres clés de
#: ``doc_metadata`` : un objet par préoccupation).
_META_PROVENANCE = "provenance"

__all__ = ["ImportMediaService"]


class ImportMediaService:
    """Fait entrer une image du web dans la documentation CARSO."""

    def __init__(
        self,
        session: Session,
        *,
        storage: DocumentStorage | None = None,
        actor_id: str | None = None,
        perimetre: Any | None = None,
        client: Any | None = None,
    ) -> None:
        """Prépare le service.

        Args:
            session: session SQLAlchemy de l'appelant.
            storage: stockage documentaire (par défaut : celui des réglages).
            actor_id: auteur humain du dépôt (traçabilité).
            perimetre: périmètre de l'utilisateur, appliqué par le service
                documentaire (les mêmes règles que tout autre dépôt).
            client: client ``httpx`` injectable — les tests n'atteignent pas le
                réseau.
        """
        self._reglages = get_settings()
        self._actor_id = actor_id
        self._client = client
        self._documents = DocumentService(
            session, storage=storage, actor_id=actor_id, perimetre=perimetre
        )

    def importer(self, entree: ImportMediaInput) -> Document:
        """Rapatrie l'image et l'enregistre comme document CARSO.

        Returns:
            Le document créé : rattaché au dossier métier si une ancre a été
            fournie, ``non_classe`` sous ``generated/{id}/`` sinon — dans les
            deux cas au statut du dépôt humain, provenance dans ses métadonnées.

        Raises:
            ValidationError: adresse refusée, hôte non public, média trop
                volumineux, réponse non exploitable, contenu qui n'est pas une
                image reconnue, une **vidéo** (jamais rapatriée), ou
                **plusieurs ancres** fournies.
            ConflictError: un document de ce nom et de ce type existe déjà.
        """
        media = recuperer_media(
            entree.url,
            max_bytes=self._reglages.media_import_max_bytes,
            timeout=self._reglages.media_import_timeout_seconds,
            nom=entree.nom,
            client=self._client,
        )
        return self._documents.enregistrer_document(
            DocumentUploadInput(
                # Le type déclaré n'est qu'un repli, jamais une classification
                # inventée : ``AUTRE`` est honnête sur une image du web, et sans
                # ancre le service documentaire impose de toute façon
                # ``non_classe``. Le type peut être précisé ensuite par la mise à
                # jour des métadonnées, une fois que l'humain sait ce qu'il a.
                type_document=TypeDocument.AUTRE.value,
                nom=media.nom_fichier,
                stream=BytesIO(media.octets),
                created_by=self._actor_id,
                doc_metadata={
                    _META_PROVENANCE: {
                        "type": "web",
                        "url_source": media.url_source,
                        "url_finale": media.url_finale,
                        "hote": _hote(media.url_finale),
                        "importe_le": datetime.now(UTC).isoformat(),
                    }
                },
                # Ancre fournie par l'appelant (au plus une) : c'est elle qui
                # range le média dans le dossier métier et le rend visible aux
                # comptes restreints de son périmètre. Sans elle, le service
                # documentaire impose ``non_classe`` sous ``generated/{id}/``.
                organisation_id=entree.organisation_id,
                appel_a_proposition_id=entree.appel_a_proposition_id,
                offre_id=entree.offre_id,
                mission_id=entree.mission_id,
                equipe_id=entree.equipe_id,
                session_id=entree.session_id,
            )
        )


def _hote(url: str) -> str:
    """Hôte affichable d'une URL (chaîne vide si l'URL n'en porte pas)."""
    return urlsplit(url).hostname or ""
