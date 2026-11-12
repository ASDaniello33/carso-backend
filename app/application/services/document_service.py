"""Service métier—documents (Phase 4, instruction/11 ; instruction/06).

Pipeline d'enregistrement (instruction/06 §5) :

```text
Upload
 ↓ validation (allowlist + contenu réel)
 ↓ chemin logique dérivé de l'ancre métier (jamais du nom fourni)
 ↓ stockage sécurisé (atomique + checksum, aucun écrasement)
 ↓ ligne documents  +  AuditEvent        ← même transaction
 ↓ (plus tard) extraction → agent → proposition → validation humaine
```

Règles appliquées :

- **appartenance mission/session** (décision utilisateur 10/11)—une mission a
  une session (``sessions.mission_id`` unique). Une pièce de mission (liste des
  bénéficiaires, checklist, fiche de présence, rapport) **appartient aussi à la
  session** de cette mission, sans être copiée : le fichier et la ligne
  ``documents`` restent **uniques**, seule la seconde ancre est renseignée
  (``session_id`` posée sur un document déjà ancré ``mission_id``). Aucune
  fiche en double, aucun octet en double ;
- **règle 6 [C]**—un document produit par un agent est créé en ``proposed``,
  jamais ``approved``—le dépôt ne vaut pas validation ;
- **instruction/06 §9**—un document officiel n'est **jamais écrasé**. Une
  nouvelle version est une nouvelle ligne ``version + 1`` et un nouveau
  fichier ; l'ancienne version reste intacte sur disque jusqu'à l'archivage ;
- **instruction/08 §7**—l'approbation d'une nouvelle version (remplacement
  d'un document officiel) écrit la mutation officielle + une ``Approbation`` +
  un ``AuditEvent`` dans **la même transaction** (``TraceContext``) ;
- **suppression logique** (règle validée 23/09)—supprimer un document retire
  son **fichier** du stockage et passe la fiche en ``supprime``. La ligne, les
  versions et le motif restent—un document ne disparaît jamais de la base, et
  ses dépendances (modèles de documents, supports) sont conservées. On archive
  pour retirer un document de l'usage courant, on supprime pour retirer le
  fichier ;
- **corbeille** (ADR 0006)—la fiche supprimée conserve une échéance
  (``doc_metadata.suppression.expire_le``) et se **restaure** dans l'état qu'elle
  avait avant sa suppression—le fichier se redépose, il n'est jamais retrouvé
  tout seul. Seul un **administrateur** peut **purger** définitivement une fiche,
  et seulement si plus rien ne la référence.

Le service ne fait ni ``commit`` ni ``rollback``—la transaction appartient à
l'appelant (``instruction/04 §5``).
"""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.application.dto import DocumentAnchor
from app.application.perimetre import Perimetre, perimetre_effectif
from app.application.services.document_service_acces import AccesDocumentMixin
from app.application.services.document_service_commun import (
    _ANCHORS,
    _CHAMP_PERSONNEL,
    _SCOPE_PERSONNEL,
    CHAMP_PAR_ANCRE,
    _code_integrite,
    document_dans_perimetre,
    logger,
)
from app.application.services.document_service_corbeille import CorbeilleDocumentMixin
from app.application.services.document_service_metadonnees import MetadonneesDocumentMixin
from app.application.services.document_service_visibilite import VisibiliteDocumentMixin
from app.application.trace import TraceContext
from app.core.config import get_settings
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.documents.paths import build_logical_path
from app.documents.previews import chemin_apercu
from app.documents.storage import DocumentStorage, LocalDocumentStorage
from app.domain.document import Document
from app.domain.enums import StatutDocument, TypeDocument
from app.infrastructure.repositories import (
    AppelRepository,
    DocumentRepository,
    EquipeRepository,
    MissionRepository,
    ModeleDocumentRepository,
    OffreRepository,
    OrganisationRepository,
    SessionRepository,
    SupportFormationRepository,
)
from app.infrastructure.repositories.base import BaseRepository

__all__ = ['CHAMP_PAR_ANCRE', 'document_dans_perimetre', 'DocumentService']


class DocumentService(
    MetadonneesDocumentMixin, CorbeilleDocumentMixin, VisibiliteDocumentMixin, AccesDocumentMixin,
):
    """Services documentaires : socle partagé et mixins de responsabilité.

    Les quatre mixins portent le cycle de vie, la corbeille, la
    visibilité et l’accès. Cette classe garde ce qu’ils partagent :
    la session, le stockage, les dépôts, la politique de périmètre et
    les garde-fous de permission (`_get`, `_get_modifiable`,
    `_est_accessible`, `_exiger_ancre_autorisee`).
    """

    def __init__(
        self,
        session: Session,
        *,
        storage: DocumentStorage | None = None,
        actor_id: str | None = None,
        perimetre: Perimetre | None = None,
    ) -> None:
        reglages = get_settings()
        self._session = session
        self._perimetre = perimetre if perimetre is not None else perimetre_effectif()
        self.storage: DocumentStorage = storage or LocalDocumentStorage(
            reglages.storage_root, max_bytes=reglages.max_upload_bytes
        )
        self._max_chars = reglages.extraction_max_chars
        self._previews_max_pages = reglages.previews_max_pages
        self.documents = DocumentRepository(session)
        self.modeles = ModeleDocumentRepository(session)
        self._organisations = OrganisationRepository(session)
        self._appels = AppelRepository(session)
        self._offres = OffreRepository(session)
        self._missions = MissionRepository(session)
        self._equipes = EquipeRepository(session)
        self._sessions = SessionRepository(session)
        self._supports = SupportFormationRepository(session)
        self._corbeille_jours = reglages.documents_corbeille_jours
        self._trace = TraceContext(session, actor_id=actor_id)

    @property
    def session(self) -> Session:
        """Session transactionnelle partagée avec les services qui le composent.

        Le moteur documentaire (INC-128) a besoin de la **même** transaction que
        le service de documents : il lit le registre des modèles et dépose ses
        livrables. Exposer la session évite de la faire passer en douce par un
        attribut privé, sans ouvrir un second accès en écriture au référentiel.
        """
        return self._session

    def _est_accessible(self, document: Document) -> bool:
        """Vrai si le document appartient au périmètre (règle partagée)."""
        return document_dans_perimetre(document, self._perimetre, self._session)

    def _exiger_ancre_autorisee(self, filtre: str, entity_id: UUID) -> None:
        """Refuse une ancre hors périmètre (404 : l'objet reste invisible).

        Raises:
            NotFoundError: ancre hors périmètre pour un compte restreint.
        """
        perimetre = self._perimetre
        if perimetre.est_complet:
            return
        if not filtre:
            # Sans ancre, le document part dans le dossier de son compte :
            # le dépôt d'un compte restreint est légitime et la fiche reste
            # visible de son demandeur via ``genere_pour`` (incrément 74).
            return
        if filtre == "mission_id" and perimetre.mission_autorisee(entity_id):
            return
        if filtre == "session_id":
            session_formation = self._sessions.get(entity_id)
            if session_formation is not None and perimetre.mission_autorisee(
                session_formation.mission_id
            ):
                return
        if filtre == "equipe_id" and perimetre.equipe_autorisee(entity_id):
            return
        raise NotFoundError(
            "Documents introuvables pour votre périmètre",
            details={"filtre": filtre},
        )

    def _get(self, document_id: UUID) -> Document:
        document = self.documents.get(document_id)
        if document is None or not self._est_accessible(document):
            raise NotFoundError(f"Document {document_id} introuvable")
        return document

    def _get_modifiable(self, document_id: UUID) -> Document:
        """Document encore modifiable : une fiche supprimée n'évolue plus.

        Raises:
            NotFoundError: document inexistant.
            ConflictError: document supprimé (fiche conservée, fichier retiré).
        """
        document = self._get(document_id)
        if document.statut == StatutDocument.SUPPRIME.value:
            msg = (
                "Ce document est supprimé : sa fiche est conservée (statut "
                "« Supprimé ») et son fichier a été retiré du stockage — "
                "déposez un nouveau document."
            )
            raise ConflictError(msg, details={"document_id": str(document_id)})
        return document

    def _references(self, document: Document) -> dict[str, int]:
        """Objets qui pointent encore vers ce document (jamais détruits par la purge).

        Trois références existent dans le modèle : le gabarit d'un modèle de
        document, le support de formation porté par un formateur, et le modèle
        choisi par une offre. Les clés sont celles de l'inventaire de suppression
        (``modeles``, ``supports``, ``offres``) pour que l'interface les nomme avec
        le vocabulaire métier déjà en place.
        """
        references: dict[str, int] = {}
        if modeles := self.modeles.list_for_template(document.id):
            references["modeles"] = len(modeles)
        if supports := self._supports.list_for_document(document.id):
            references["supports"] = len(supports)
        if offres := self._offres.list_for_modele_document(document.id):
            references["offres"] = len(offres)
        return references

    def _retirer_fichiers(self, document: Document) -> int:
        """Retire le fichier d'un document et ses aperçus dérivés (*best effort*).

        Un échec disque ne casse pas la suppression : un fichier orphelin est moins
        grave qu'une transaction annulée. L'échec est journalisé, jamais avalé en
        silence (AGENTS.md §5).
        """
        chemins = [document.storage_path]
        for page in range(1, self._previews_max_pages + 1):
            chemins.append(chemin_apercu(document.storage_path, page))
        retires = 0
        for chemin in chemins:
            try:
                if self.storage.exists(chemin):
                    self.storage.remove(chemin)
                    retires += 1
            except Exception as exc:  # pragma: no cover - dépend du disque
                logger.warning("Fichier documentaire non retiré (%s) : %s", chemin, exc)
        return retires

    def _ancre_existe(self, champ: str, entity_id: UUID) -> bool:
        depots: dict[str, BaseRepository] = {
            "organisation_id": self._organisations,
            "appel_id": self._appels,
            "offre_id": self._offres,
            "mission_id": self._missions,
            "equipe_id": self._equipes,
            "session_id": self._sessions,
        }
        return depots[champ].get(entity_id) is not None

    def _chemin_logique_libre(
        self,
        scope: str,
        entity_id: UUID,
        nom: str,
        *,
        proprietaire_id: UUID | None = None,
    ) -> str:
        """Chemin libre dans le dossier cible (dé-collision purement technique).

        Deux pièces jointes homonymes d'un même appel ne peuvent pas occuper le
        même chemin : le suffixe ``_r{n}`` évite l'écrasement sans toucher au nom
        **logique** du document (``documents.nom`` reste inchangé).
        """
        fichier = Path(nom)
        for indice in range(1, 100):
            candidat = build_logical_path(
                scope,
                entity_id,
                f"{fichier.stem}_r{indice}{fichier.suffix}",
                proprietaire_id=proprietaire_id,
            )
            if not self.storage.exists(candidat):
                return candidat
        raise ConflictError(
            "Aucun emplacement libre dans le dossier cible",
            details={"scope": scope, "nom": nom},
        )

    def _ancre_du_document(self, document: Document) -> DocumentAnchor:
        """Ancre d'un document existant — le dossier de stockage n'est jamais
        redéduit d'une entrée utilisateur lors d'un remplacement."""
        for champ, scope in _ANCHORS.items():
            entity_id = getattr(document, champ)
            if entity_id is not None:
                return DocumentAnchor(scope=scope, champ=champ, entity_id=entity_id)
        return DocumentAnchor(
            scope=_SCOPE_PERSONNEL, champ=_CHAMP_PERSONNEL, entity_id=document.id
        )

    def _traduire_integrite(
        self,
        erreur: Exception,
        document: Document,
        *,
        champ: str | None = None,
    ) -> None:
        """Traduit une violation d'intégrité en erreur métier lisible.

        Appelée dans le ``except`` des trois écritures documentaires (dépôt,
        révision, rattachement), **avant** de relancer. Une ``IntegrityError``
        brute finit en 500 sans message d'interface : l'utilisateur ne saurait
        ni ce qui a échoué ni quoi faire. Deux familles seulement sont traduites,
        parce que ce sont celles que ce service peut provoquer :

        - **clé étrangère** : l'objet d'ancre a disparu entre la lecture et
          l'écriture (concurrence, suppression hors du service) ;
        - **unicité** : la fiche visée a été créée entre-temps.

        Toute autre violation remonte inchangée : la masquer derrière un message
        métier ferait perdre l'information à l'exploitant.
        """
        if not isinstance(erreur, IntegrityError):
            return
        code = _code_integrite(erreur)
        if code == "23505":
            msg = (
                "Un document de ce nom et de ce type vient d'être enregistré : "
                "la contrainte d'unicité a refusé le doublon"
            )
            raise ConflictError(
                msg,
                details={"document_id": str(document.id), "nom": document.nom},
            ) from erreur
        if code == "23503":
            ancre = champ or self._ancre_du_document(document).champ
            libelle = ancre.removesuffix("_id") if ancre else "métier"
            msg = (
                f"L'objet « {libelle} » auquel ce document est rattaché n'existe "
                "plus : aucun document n'a été enregistré"
            )
            raise NotFoundError(
                msg,
                details={
                    "document_id": str(document.id),
                    "nom": document.nom,
                    **({"ancre": ancre} if ancre else {}),
                },
            ) from erreur

    def _proprietaire_du_chemin(
        self, ancre: DocumentAnchor, document: Document | None = None
    ) -> UUID | None:
        """Compte dont le dossier de travail reçoit le fichier.

        ``None`` pour un document ancré sur un objet métier : il suit le
        périmètre de cet objet et n'a pas de dossier personnel. Sans ancre,
        c'est le compte qui dépose ; à défaut (remplacement, restauration d'une
        fiche plus ancienne), celui que la fiche porte déjà. Aucun des deux :
        refus explicite. Un chemin ne s'invente pas, et une fiche personnelle
        sans propriétaire ne serait le document de personne.

        Raises:
            ValidationError: dépôt personnel par un appel sans compte, ou
                fiche personnelle sans propriétaire.
        """
        if ancre.scope != _SCOPE_PERSONNEL:
            return None
        proprietaire = self._perimetre.utilisateur_id or (
            document.proprietaire_id if document is not None else None
        )
        if proprietaire is None:
            msg = (
                "Un document sans rattachement métier doit appartenir à un "
                "compte : son dossier de travail est indéterminé sans "
                "propriétaire"
            )
            raise ValidationError(
                msg,
                details={
                    "document_id": str(document.id) if document is not None else None
                },
            )
        return proprietaire

    def _valider_type(self, type_document: str) -> str:
        valides = {type_.value for type_ in TypeDocument}
        if type_document not in valides:
            msg = f"Type de document inconnu : {type_document!r}"
            raise ValidationError(msg, details={"types_valides": sorted(valides)})
        return type_document
