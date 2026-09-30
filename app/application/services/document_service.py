"""Service métier — documents (Phase 4, instruction/11 ; instruction/06).

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

- **règle 6 [C]** : un document produit par un agent est créé en ``proposed``,
  jamais ``approved`` — le dépôt ne vaut pas validation ;
- **instruction/06 §9** : un document officiel n'est **jamais écrasé**. Une
  nouvelle version est une nouvelle ligne ``version + 1`` et un nouveau
  fichier ; l'ancienne version reste intacte sur disque jusqu'à l'archivage ;
- **instruction/08 §7** : l'approbation d'une nouvelle version (remplacement
  d'un document officiel) écrit la mutation officielle + une ``Approbation`` +
  un ``AuditEvent`` dans **la même transaction** (``TraceContext``) ;
- **suppression logique** (règle validée 23/09) : supprimer un document retire
  son **fichier** du stockage et passe la fiche en ``supprime``. La ligne, les
  versions et le motif restent : un document ne disparaît jamais de la base, et
  ses dépendances (modèles de documents, supports) sont conservées. On archive
  pour retirer un document de l'usage courant, on supprime pour retirer le
  fichier ;
- **corbeille** (ADR 0006) : la fiche supprimée conserve une échéance
  (``doc_metadata.suppression.expire_le``) et se **restaure** dans l'état qu'elle
  avait avant sa suppression — le fichier se redépose, il n'est jamais retrouvé
  tout seul. Seul un **administrateur** peut **purger** définitivement une fiche,
  et seulement si plus rien ne la référence.

Le service ne fait ni ``commit`` ni ``rollback`` : la transaction appartient à
l'appelant (``instruction/04 §5``).
"""

from __future__ import annotations

import io
import logging
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from math import ceil
from pathlib import Path
from typing import BinaryIO
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from app.application.dto import (
    ApercuDocument,
    DecisionInput,
    DocumentAnchor,
    DocumentMetadonneesUpdateInput,
    DocumentUploadInput,
    ExtractedDocument,
    FicheCorbeille,
    LigneLecturePlage,
    PageApercuDocument,
)
from app.application.services import notifications_evenements as notifications
from app.application.trace import TraceContext
from app.core.config import get_settings
from app.core.errors import CarsoError, ConflictError, NotFoundError, ValidationError
from app.documents.extraction import extract_text
from app.documents.paths import (
    build_logical_path,
    extension_of,
    sanitize_filename,
    versioned_filename,
)
from app.documents.previews import chemin_apercu, enregistrer_apercu, resoudre_apercu
from app.documents.rasterize import pages_pdf, rasteriser_pdf
from app.documents.storage import DocumentStorage, LocalDocumentStorage
from app.documents.validation import ALLOWED_EXTENSIONS
from app.domain.document import Document
from app.domain.enums import (
    ActionAudit,
    ActorType,
    DecisionApprobation,
    StatutDocument,
    TypeDocument,
    TypeProposition,
)
from app.domain.state_machines import validate_transition
from app.infrastructure.repositories import (
    AppelAPropositionRepository,
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

_ENTITY = "document"

#: Scope des documents sans objet métier (génération ou dépôt hors fiche).
_SCOPE_GENERE = "generated"
#: Champ fictif : aucune colonne FK n'est écrite pour ce repli.
_CHAMP_GENERE = ""

#: Ancres métier acceptées → dossier de stockage (instruction/06 §3).
#: ``organisations`` et ``sessions`` sont des scopes ajoutés depuis la cible
#: (voir ``app.documents.paths`` et l'ADR 0003).
_ANCHORS: dict[str, str] = {
    "organisation_id": "organisations",
    "appel_a_proposition_id": "appel_proposition",
    "offre_id": "offres",
    "mission_id": "missions",
    "equipe_id": "equipes",
    "session_id": "sessions",
}

_META_KEY = "proposed_by_agent"

#: Clé du *tombstone* écrit dans ``doc_metadata`` à la suppression logique :
#: qui, quand, pourquoi — la fiche survit au fichier. Il porte aussi l'échéance
#: de conservation et le statut de retour (ADR 0006).
_META_SUPPRESSION = "suppression"

#: Clé du journal des restaurations : chaque entrée dit quand, par qui, et si les
#: octets ont été **redéposés** (un fichier ne revient pas tout seul du disque).
_META_RESTAURATIONS = "restaurations"

#: Filtres de listing exposés par l'API → méthode du repository.
_LISTERS: dict[str, str] = {
    "organisation_id": "list_for_organisation",
    "appel_a_proposition_id": "list_for_appel_a_proposition",
    "offre_id": "list_for_offre",
    "mission_id": "list_for_mission",
    "equipe_id": "list_for_equipe",
    "session_id": "list_for_session",
}

logger = logging.getLogger(__name__)


class DocumentService:
    """Use cases documentaires : enregistrer, remplacer, approuver, refuser,
    archiver, extraire, télécharger. Aucun commit."""

    def __init__(
        self,
        session: Session,
        *,
        storage: DocumentStorage | None = None,
        actor_id: str | None = None,
    ) -> None:
        reglages = get_settings()
        self._session = session
        self.storage: DocumentStorage = storage or LocalDocumentStorage(
            reglages.storage_root, max_bytes=reglages.max_upload_bytes
        )
        self._max_chars = reglages.extraction_max_chars
        self._previews_max_pages = reglages.previews_max_pages
        self.documents = DocumentRepository(session)
        self.modeles = ModeleDocumentRepository(session)
        self._organisations = OrganisationRepository(session)
        self._appels = AppelAPropositionRepository(session)
        self._offres = OffreRepository(session)
        self._missions = MissionRepository(session)
        self._equipes = EquipeRepository(session)
        self._sessions = SessionRepository(session)
        self._supports = SupportFormationRepository(session)
        self._corbeille_jours = reglages.documents_corbeille_jours
        self._trace = TraceContext(session, actor_id=actor_id)

    # --- dépôt et versioning ------------------------------------------------

    def enregistrer_document(self, entree: DocumentUploadInput) -> Document:
        """Dépose un nouveau document (version 1).

        Une ancre réelle range le fichier sous le dossier métier. Une ancre
        absente ou inventée range le document sous ``generated/{id}/`` avec
        le type ``non_classe`` — aucune FK n'est inventée.

        Raises:
            ValidationError: plusieurs ancres, type de document ou nom
                invalide, fichier refusé par la validation de contenu.
            ConflictError: une version 1 existe déjà pour ce nom et ce type.
        """
        ancre = self._resoudre_ancre(entree)
        type_document = (
            TypeDocument.NON_CLASSE.value
            if ancre.scope == _SCOPE_GENERE
            else self._valider_type(entree.type_document)
        )
        nom = sanitize_filename(entree.nom)

        if self.documents.find_version(nom, type_document, 1) is not None:
            msg = "Un document de ce nom et de ce type existe déjà (version 1)"
            raise ConflictError(msg, details={"nom": nom, "type_document": type_document})

        chemin_logique = build_logical_path(ancre.scope, ancre.entity_id, nom)
        kwargs_ancre = {ancre.champ: ancre.entity_id} if ancre.champ else {}
        document = Document(
            id=ancre.entity_id if ancre.scope == _SCOPE_GENERE else uuid4(),
            nom=nom,
            type_document=type_document,
            version=1,
            statut=self._statut_depot(entree),
            created_by=entree.created_by or entree.proposed_by_agent,
            doc_metadata=(
                {_META_KEY: entree.proposed_by_agent} if entree.proposed_by_agent else None
            ),
            **kwargs_ancre,
        )
        self._materialiser(document, chemin_logique, entree)
        self._trace.record_event(
            actor_type=self._acteur(entree),
            action=ActionAudit.DOCUMENT_ENREGISTRE.value,
            entity_type=_ENTITY,
            entity_id=document.id,
            after={"statut": document.statut, "version": document.version},
            event_metadata={"storage_path": document.storage_path},
        )
        return document

    def remplacer_document(self, document_id: UUID, entree: DocumentUploadInput) -> Document:
        """Dépose une **nouvelle version** d'un document, sans écraser l'ancienne.

        Le nom de fichier disque porte le numéro de version (``x_v2.pdf``) tandis
        que ``documents.nom`` reste le nom logique : l'historique des versions
        reste requêtable par nom, et aucun fichier officiel n'est modifié.

        La nouvelle version naît en ``draft`` (ou ``proposed`` si un agent en est
        l'auteur) : elle ne devient officielle que par ``approuver_document``.
        """
        courant = self._get_modifiable(document_id)
        ancre = self._ancre_du_document(courant)
        nom = sanitize_filename(entree.nom or courant.nom)

        if nom != courant.nom:
            msg = (
                "Le nom du document ne peut pas changer en cours de versionnement "
                "(créer un nouveau document à la place)"
            )
            raise ValidationError(msg, details={"nom": nom, "nom_attendu": courant.nom})

        nouvelle_version = self.documents.current_version(nom, courant.type_document) + 1
        if self.documents.find_version(nom, courant.type_document, nouvelle_version) is not None:
            msg = f"La version {nouvelle_version} existe déjà pour ce document"
            raise ConflictError(msg, details={"version": nouvelle_version})

        nom_fichier = versioned_filename(nom, nouvelle_version)
        chemin_logique = build_logical_path(ancre.scope, ancre.entity_id, nom_fichier)

        document = Document(
            nom=nom,
            type_document=courant.type_document,
            version=nouvelle_version,
            statut=self._statut_depot(entree),
            created_by=entree.created_by or entree.proposed_by_agent,
            doc_metadata=(
                {_META_KEY: entree.proposed_by_agent} if entree.proposed_by_agent else None
            ),
            **({ancre.champ: ancre.entity_id} if ancre.champ else {}),
        )
        self._materialiser(document, chemin_logique, entree)
        self._trace.record_event(
            actor_type=self._acteur(entree),
            action=ActionAudit.DOCUMENT_REMPLACE.value,
            entity_type=_ENTITY,
            entity_id=document.id,
            after={"statut": document.statut, "version": document.version},
            event_metadata={
                "document_remplace_id": str(courant.id),
                "version_remplacee": courant.version,
                "storage_path": document.storage_path,
            },
        )
        return document

    def rattacher_document(
        self,
        document_id: UUID,
        *,
        appel_a_proposition_id: UUID | None = None,
        offre_id: UUID | None = None,
        mission_id: UUID | None = None,
        equipe_id: UUID | None = None,
        session_id: UUID | None = None,
        organisation_id: UUID | None = None,
    ) -> Document:
        """Rattache un document **déjà déposé** à une ancre métier (HITL).

        Cas d'usage : une pièce jointe du chat déposée hors fiche métier vit sous
        ``generated/{id}/`` avec le type ``non_classe``. Une fois l'objet créé
        (ex. l'appel reçu), le document source lui est rattaché : les octets sont
        **copiés** dans le dossier de l'ancre (``appel_proposition/{id}/``), la
        copie est **vérifiée** (checksum), la ligne ``documents`` est repointée,
        puis l'ancienne copie est retirée.

        Ce n'est **pas** une nouvelle version : aucun contenu n'est modifié, ni
        le nom logique, ni le numéro de version. Le document déménage.

        Raises:
            ValidationError: aucune ancre ou plusieurs ancres fournies.
            NotFoundError: document ou ancre introuvable.
            ConflictError: document déjà rattaché, ou dossier cible saturé.
            CarsoError: copie non vérifiable — l'original est conservé.
        """
        courant = self._get_modifiable(document_id)
        champ, entity_id, scope = self._cible_rattachement(
            courant,
            appel_a_proposition_id=appel_a_proposition_id,
            offre_id=offre_id,
            mission_id=mission_id,
            equipe_id=equipe_id,
            session_id=session_id,
            organisation_id=organisation_id,
        )

        ancien_chemin = courant.storage_path
        nom_fichier = versioned_filename(courant.nom, courant.version)
        chemin_logique = build_logical_path(scope, entity_id, nom_fichier)
        if self.storage.exists(chemin_logique):
            chemin_logique = self._chemin_logique_libre(scope, entity_id, nom_fichier)

        octets = self.storage.read(ancien_chemin)
        stocke = self.storage.save(chemin_logique, io.BytesIO(octets))
        if courant.checksum_sha256 and stocke.checksum_sha256 != courant.checksum_sha256:
            # La copie diverge de l'original : on la retire et on garde l'original.
            self.storage.remove(stocke.logical_path)
            raise CarsoError(
                "Rattachement annulé : la copie du document ne correspond pas à "
                "l'original (l'original est conservé)",
                details={"document_id": str(courant.id)},
            )

        setattr(courant, champ, entity_id)
        courant.storage_path = stocke.logical_path
        courant.storage_disk = self.storage.disk_name
        self._trace.record_event(
            actor_type=(
                ActorType.AGENT.value if self._trace.actor_id else ActorType.SYSTEME.value
            ),
            action=ActionAudit.DOCUMENT_RATTACHE.value,
            entity_type=_ENTITY,
            entity_id=courant.id,
            after={"storage_path": stocke.logical_path, champ: str(entity_id)},
            event_metadata={
                "ancre": champ,
                "storage_path_precedent": ancien_chemin,
            },
        )
        try:
            self._session.flush()
        except Exception:
            # Compensation : un échec base ne laisse pas de copie orpheline.
            self.storage.remove(stocke.logical_path)
            raise

        # L'ancienne copie n'est retirée qu'une fois la nouvelle adresse écrite.
        self.storage.remove(ancien_chemin)
        return courant

    # --- décisions humaines -------------------------------------------------

    def soumettre_document(self, document_id: UUID) -> Document:
        """``draft`` → ``proposed`` : met une version à disposition d'une décision humaine.

        La machine à états interdit ``draft → approved`` : un document déposé par
        un humain doit passer par la proposition, exactement comme un document
        produit par un agent (règle 6 [C]).
        """
        courant = self._get_modifiable(document_id)
        validate_transition(_ENTITY, courant.statut, StatutDocument.PROPOSED.value)

        courant.statut = StatutDocument.PROPOSED
        self._trace.record_event(
            actor_type=ActorType.HUMAIN.value,
            action=ActionAudit.DOCUMENT_SOUMIS.value,
            entity_type=_ENTITY,
            entity_id=courant.id,
            after={"statut": courant.statut, "version": courant.version},
        )
        notifications.notifier_document_a_approuver(self._session, courant)
        return courant

    def approuver_document(self, document_id: UUID, decision: DecisionInput) -> Document:
        """Officialise une version ; les versions officielles précédentes sont archivées.

        L'archivage des versions antérieures et l'approbation de la nouvelle se
        font dans **la même transaction** (instruction/08 §7 : remplacement d'un
        document officiel = opération à impact).
        """
        courant = self._get_modifiable(document_id)
        validate_transition(_ENTITY, courant.statut, StatutDocument.APPROVED.value)

        archivees = [
            version
            for version in self.documents.list_approved(courant.nom, courant.type_document)
            if version.id != courant.id
        ]
        for ancienne in archivees:
            validate_transition(_ENTITY, ancienne.statut, StatutDocument.ARCHIVED.value)
            ancienne.statut = StatutDocument.ARCHIVED

        courant.statut = StatutDocument.APPROVED
        self._trace.record_decision(
            entity_type=_ENTITY,
            entity_id=courant.id,
            proposal_type=TypeProposition.DOCUMENT_OFFICIEL.value,
            decision=DecisionApprobation.APPROUVE.value,
            proposed_by_agent=self._proposed_by_agent(courant),
            decided_by=decision.decided_by,
            reason=decision.reason,
            after={
                "statut": courant.statut,
                "version": courant.version,
                "versions_archivees": [str(ancienne.id) for ancienne in archivees],
            },
        )
        notifications.notifier_document_approuve(self._session, courant, "approuvé")
        return courant

    def refuser_document(self, document_id: UUID, decision: DecisionInput) -> Document:
        """Rejette une version **proposée** (agent ou humain) : elle est archivée.

        Rien n'est supprimé : la proposition et son motif de refus restent
        auditables.
        """
        courant = self._get_modifiable(document_id)
        validate_transition(_ENTITY, courant.statut, StatutDocument.ARCHIVED.value)

        courant.statut = StatutDocument.ARCHIVED
        self._trace.record_decision(
            entity_type=_ENTITY,
            entity_id=courant.id,
            proposal_type=TypeProposition.DOCUMENT_OFFICIEL.value,
            decision=DecisionApprobation.REJETE.value,
            proposed_by_agent=self._proposed_by_agent(courant),
            decided_by=decision.decided_by,
            reason=decision.reason,
            after={"statut": courant.statut, "version": courant.version},
        )
        return courant

    def archiver_document(self, document_id: UUID, decision: DecisionInput) -> Document:
        """Archive un document (décision humaine tracée, aucune suppression).

        Consigné comme **événement d'audit** et non comme approbation : archiver
        n'est pas approuver une proposition.
        """
        courant = self._get_modifiable(document_id)
        validate_transition(_ENTITY, courant.statut, StatutDocument.ARCHIVED.value)

        courant.statut = StatutDocument.ARCHIVED
        self._trace.record_event(
            actor_type=ActorType.HUMAIN.value,
            action=ActionAudit.DOCUMENT_ARCHIVE.value,
            entity_type=_ENTITY,
            entity_id=courant.id,
            after={"statut": courant.statut, "version": courant.version},
            event_metadata={"decided_by": decision.decided_by, "reason": decision.reason},
        )
        return courant

    def supprimer_document(self, document_id: UUID, decision: DecisionInput) -> int:
        """Supprime un document : **fichier retiré, fiche conservée** (23/09).

        Un document ne disparaît jamais de la base CARSO : la suppression retire
        le fichier (et ses aperçus dérivés) du stockage, passe la fiche en
        ``supprime`` et laisse **intactes** les dépendances (modèles de documents,
        supports de formation). L'``AuditEvent`` conserve qui, quand et pourquoi.

        Un document officiel (``approved``) n'est pas bloqué : il est supprimable
        comme les autres — la décision reste tracée, et le fichier n'est plus sur
        le disque. La fiche garde un *tombstone* lisible dans ``doc_metadata``,
        qui porte aussi l'**échéance** de conservation : c'est ce que la corbeille
        affiche, et ce que ``restaurer_document`` relit pour savoir dans quel état
        remettre la fiche (ADR 0006).

        Returns:
            Nombre de fichiers réellement retirés du stockage (fichier + aperçus).

        Raises:
            NotFoundError: document inexistant.
            ConflictError: document déjà supprimé.
        """
        courant = self._get_modifiable(document_id)
        statut_avant = courant.statut
        validate_transition(_ENTITY, statut_avant, StatutDocument.SUPPRIME.value)

        retires = self._retirer_fichiers(courant)
        supprime_le = datetime.now(UTC)
        courant.statut = StatutDocument.SUPPRIME
        # Le chemin est conservé : il dit d'où le fichier a été retiré, sans
        # jamais redevenir une donnée exploitable (le disque ne le porte plus).
        courant.doc_metadata = {
            **(courant.doc_metadata or {}),
            _META_SUPPRESSION: {
                "le": supprime_le.isoformat(),
                "par": decision.decided_by,
                "motif": decision.reason,
                "statut_avant": statut_avant,
                "fichiers_retires": retires,
                # Échéance écrite ici, une fois pour toutes : changer le réglage
                # de conservation ne déplace pas une date déjà annoncée.
                "retention_jours": self._corbeille_jours,
                "expire_le": (
                    supprime_le + timedelta(days=self._corbeille_jours)
                ).isoformat(),
            },
        }
        self._trace.record_event(
            actor_type=ActorType.HUMAIN.value,
            action=ActionAudit.DOCUMENT_SUPPRIME.value,
            entity_type=_ENTITY,
            entity_id=courant.id,
            after={
                "nom": courant.nom,
                "type_document": courant.type_document,
                "statut": courant.statut,
                "version": courant.version,
                "storage_path": courant.storage_path,
                "fichiers_retires": retires,
            },
            event_metadata={
                "decided_by": decision.decided_by,
                "reason": decision.reason,
                "suppression_logique": True,
                "expire_le": (supprime_le + timedelta(days=self._corbeille_jours)).isoformat(),
            },
        )
        return retires

    # --- corbeille (ADR 0006) ------------------------------------------------

    def lister_corbeille(self) -> list[FicheCorbeille]:
        """Fiches supprimées, avec échéance, blocage éventuel et fichier présent.

        Lecture seule : rien n'est modifié, la corbeille ne décide pas à la place
        de l'humain. ``restaurable`` et ``blocage`` sont calculés par le service
        (pas par l'interface) : c'est la même règle qui refuse une restauration
        impossible et qui l'annonce ici.
        """
        return [self._fiche_corbeille(document) for document in self.documents.list_supprimes()]

    def restaurer_document(
        self,
        document_id: UUID,
        decision: DecisionInput,
        *,
        flux: BinaryIO | None = None,
    ) -> Document:
        """Remet en service une fiche supprimée (ADR 0006).

        La suppression n'a conservé que la **fiche** : restaurer remet le statut à
        *exactement* celui d'avant la suppression — il est relu dans le tombstone,
        jamais choisi par l'appelant. Une fiche restaurée dans un autre état
        raconterait une histoire qui n'a pas eu lieu.

        Le fichier, lui, a quitté le stockage : il faut le **redéposer**
        (``flux``). Un seul cas dispense de ce dépôt — un fichier occupe déjà le
        chemin d'origine (restauration d'une sauvegarde par un exploitant) : la
        fiche est alors remise en service sans toucher au disque.

        Args:
            decision: qui restaure et pourquoi (tracé dans l'audit).
            flux: octets du fichier redéposé ; ``None`` exige un fichier présent.

        Returns:
            La fiche restaurée, dans son statut d'origine.

        Raises:
            NotFoundError: document inexistant.
            ConflictError: fiche non supprimée, trace de suppression
                incomplète, version déjà reprise, version officielle déjà en
                place, ou fichier absent sans redéposition.
        """
        document = self._get(document_id)
        if document.statut != StatutDocument.SUPPRIME.value:
            msg = (
                "Ce document n'est pas supprimé : la restauration ne s'applique "
                "qu'à une fiche de la corbeille"
            )
            raise ConflictError(msg, details={"document_id": str(document_id)})

        trace = self._trace_suppression(document)
        statut_avant = trace.get("statut_avant")
        if not isinstance(statut_avant, str) or not statut_avant:
            msg = (
                "Trace de suppression incomplète : l'état de retour de cette fiche "
                "est inconnu, la restauration est refusée"
            )
            raise ConflictError(msg, details={"document_id": str(document_id)})

        # La machine autorise l'arête ``supprime → …`` ; l'égalité avec l'état
        # enregistré est vérifiée ici, parce qu'elle dépend de l'historique de
        # cette fiche et non du vocabulaire des statuts.
        validate_transition(_ENTITY, StatutDocument.SUPPRIME.value, statut_avant)
        blocage = self._blocage_restauration(document, statut_avant)
        if blocage:
            raise ConflictError(
                blocage,
                details={
                    "document_id": str(document_id),
                    "nom": document.nom,
                    "version": document.version,
                },
            )

        chemin_ecrit: str | None = None
        try:
            if flux is not None:
                stocke = self.storage.save(self._chemin_restauration(document), flux)
                chemin_ecrit = stocke.logical_path
                document.mime_type = stocke.mime_type
                document.taille_octets = stocke.taille_octets
                document.storage_path = stocke.logical_path
                document.storage_disk = self.storage.disk_name
                document.checksum_sha256 = stocke.checksum_sha256
            elif not self.storage.exists(document.storage_path):
                msg = (
                    "Le fichier de ce document a été retiré du stockage : "
                    "redéposez-le pour restaurer la fiche"
                )
                raise ConflictError(
                    msg, details={"document_id": str(document_id)}
                )

            journal = list((document.doc_metadata or {}).get(_META_RESTAURATIONS) or [])
            journal.append(
                {
                    "le": datetime.now(UTC).isoformat(),
                    "par": decision.decided_by,
                    "motif": decision.reason,
                    "statut_retabli": statut_avant,
                    "fichier_redepose": flux is not None,
                }
            )
            document.statut = statut_avant
            document.doc_metadata = {
                **(document.doc_metadata or {}),
                _META_RESTAURATIONS: journal,
            }
            self._trace.record_event(
                actor_type=ActorType.HUMAIN.value,
                action=ActionAudit.DOCUMENT_RESTAURE.value,
                entity_type=_ENTITY,
                entity_id=document.id,
                before={"statut": StatutDocument.SUPPRIME.value},
                after={
                    "statut": document.statut,
                    "version": document.version,
                    "storage_path": document.storage_path,
                },
                event_metadata={
                    "decided_by": decision.decided_by,
                    "reason": decision.reason,
                    "fichier_redepose": flux is not None,
                },
            )
            self._session.flush()
        except Exception:
            # Compensation : un échec base ne laisse pas le fichier redéposé
            # orphelin sur le disque (même règle que le rattachement).
            if chemin_ecrit is not None:
                try:
                    self.storage.remove(chemin_ecrit)
                except Exception as exc:  # pragma: no cover - dépend du disque
                    logger.warning("Fichier redéposé non retiré (%s) : %s", chemin_ecrit, exc)
            raise
        return document

    def purger_document(self, document_id: UUID, decision: DecisionInput) -> int:
        """Purge **définitive** d'une fiche de la corbeille (ADR 0006).

        C'est la seule opération du système qui efface une ligne documentaire :
        elle est réservée aux administrateurs (couche API) et son motif est
        obligatoire. La fiche avait déjà perdu son fichier à la suppression ; si
        des octets sont revenus sur le disque entre-temps, ils sont retirés.

        Une fiche encore **référencée** n'est pas purgée : un modèle de document,
        un support de formation ou une offre qui pointe vers elle ne sont jamais
        détruits par effet de bord (règle 23/09). L'appelant reçoit la liste de ce
        qui la référence — jamais une erreur de contrainte brute.

        Returns:
            Nombre de fichiers réellement retirés du stockage.

        Raises:
            NotFoundError: document inexistant.
            ConflictError: fiche non supprimée, ou encore référencée.
        """
        document = self._get(document_id)
        if document.statut != StatutDocument.SUPPRIME.value:
            msg = "Seule une fiche supprimée se purge définitivement (corbeille)"
            raise ConflictError(msg, details={"document_id": str(document_id)})

        references = self._references(document)
        if references:
            msg = (
                "Ce document est encore référencé : le purger détruirait du même "
                "coup des objets qui ne lui appartiennent pas. Détachez d'abord ce "
                "qui le référence."
            )
            raise ConflictError(
                msg,
                details={
                    "document_id": str(document_id),
                    "nom": document.nom,
                    "references": references,
                },
            )

        retires = self._retirer_fichiers(document)
        self._trace.record_event(
            actor_type=ActorType.HUMAIN.value,
            action=ActionAudit.DOCUMENT_PURGE.value,
            entity_type=_ENTITY,
            entity_id=document.id,
            before={
                "nom": document.nom,
                "type_document": document.type_document,
                "version": document.version,
                "statut": document.statut,
                "storage_path": document.storage_path,
                "doc_metadata": document.doc_metadata,
            },
            event_metadata={
                "decided_by": decision.decided_by,
                "reason": decision.reason,
                "purge_definitive": True,
                "fichiers_retires": retires,
            },
        )
        # La fiche disparaît après l'écriture de sa trace : l'audit garde ce que
        # la purge a effacé (nom, version, motif de suppression, date).
        self.documents.delete(document)
        return retires

    # --- lecture ------------------------------------------------------------

    def obtenir(self, document_id: UUID) -> Document:
        """Charge un document (lecture pure)."""
        return self._get(document_id)

    def versions(self, document_id: UUID) -> list[Document]:
        """Historique complet des versions d'un document, plus récente d'abord."""
        courant = self._get(document_id)
        return self.documents.list_versions(courant.nom, courant.type_document)

    def lister_pour(self, filtre: str, entity_id: UUID) -> list[Document]:
        """Documents rattachés à un objet métier (un filtre à la fois)."""
        methode = _LISTERS.get(filtre)
        if methode is None:
            msg = f"Filtre de listing documentaire inconnu : {filtre!r}"
            raise ValidationError(msg, details={"filtres_possibles": sorted(_LISTERS)})
        return list(getattr(self.documents, methode)(entity_id))

    def lister_tous(self, statut: str | None = None) -> list[Document]:
        """Tous les documents (gestion générale), filtrables par statut."""
        return self.documents.list_all(statut=statut)

    def generer_document_offre(self, offre_id: UUID) -> Document:
        """Génère le document de l'offre de formation (chemin déterministe).

        Compose les données réelles (offre + lot + organisation) en un DOCX
        déposé comme **proposition** rattachée à l'offre : l'humain télécharge,
        révise, puis approuve ou demande une régénération. Ce chemin ne dépend
        pas d'un LLM configuré — l'agent générateur reste le chemin enrichi
        (analyse, rédaction), celui-ci garantit un livrable revoyable.

        Raises:
            ValidationError: l'offre n'a pas de modèle actif résoluble → le DOCX
                standard CARSO est utilisé (jamais de contenu inventé).
            ConflictError: la version 1 du document existe déjà (régénérer via
                ``remplacer_document`` pour conserver l'historique).
        """
        from app.documents.generation import GenerateurDocument

        offre = self._offres.get(offre_id)
        if offre is None:
            raise NotFoundError(f"Offre {offre_id} introuvable")
        lot = offre.lot
        organisation = offre.organisation

        generateur = GenerateurDocument()
        paragraphes = [
            f"Référence : {offre.reference}",
            f"Organisation : {organisation.nom}",
            f"Lot {lot.numero} — {lot.titre}",
            f"Zone : {lot.zone or 'non précisée'}",
            "",
            "Objectifs du lot :",
            lot.objectifs or "(non précisés)",
            "",
            "Résultats attendus :",
            lot.resultats_attendus or "(non précisés)",
            "",
            "Ce document est une proposition générée par le système CARSO AI : "
            "il n'est pas officiel tant qu'il n'a pas été approuvé par un "
            "humain.",
        ]
        tableau = [
            ["Champ", "Valeur"],
            ["Offre", offre.titre],
            ["Statut offre", offre.statut],
            ["Version offre", str(offre.version)],
            ["Appel à proposition", lot.appel_a_proposition.reference],
        ]
        octets = generateur.docx(titre=offre.titre, paragraphes=paragraphes, tableau=tableau)

        nom = f"Offre_{offre.reference}.docx"
        entree = DocumentUploadInput(
            type_document=TypeDocument.OFFRE.value,
            nom=nom,
            stream=io.BytesIO(octets),
            created_by=self._trace.actor_id,
            offre_id=offre.id,
        )
        # Régénération : si le document existe déjà, on dépose une **nouvelle
        # version** (l'historique est conservé) au lieu d'échouer en conflit.
        existant = self.documents.find_active_by_name(nom, TypeDocument.OFFRE.value)
        if existant is not None:
            nouvelle = self.remplacer_document(existant.id, entree)
            return self.soumettre_document(nouvelle.id)
        # Le système place la proposition en zone *proposal* (règle 6 [C]) :
        # ``draft → proposed``, la décision humaine reste obligatoire.
        document = self.enregistrer_document(entree)
        return self.soumettre_document(document.id)

    def telecharger(self, document_id: UUID) -> tuple[str, Path]:
        """Nom de fichier proposé au téléchargement + chemin physique contrôlé.

        Le chemin physique n'est jamais renvoyé par l'API : il est consommé
        directement par la réponse de fichier.
        """
        courant = self._get_fichier(document_id)
        chemin = self.storage.resolve_existing(courant.storage_path)
        nom_fichier = versioned_filename(courant.nom, courant.version)
        return nom_fichier, chemin

    def apercu_document(
        self,
        document_id: UUID,
        *,
        pages: Sequence[int] | None = None,
        dpi: int | None = None,
    ) -> ApercuDocument:
        """Rend les pages d'un document en images inspectables (aperçu visuel).

        Le document doit être un **PDF** : sans moteur bureautique (LibreOffice)
        sur l'hôte, un DOCX ou un XLSX n'est pas convertible — l'erreur est
        explicite et indique qu'il faut la version PDF du document (celle que
        produit la génération d'offre).

        Images écrites **à côté du document** (``previews/<tige>/pNN.png``),
        jamais réécrites : l'aperçu d'une version immuable est stable, et un
        second appel ne retravaille rien (idempotent).

        Args:
            document_id: document PDF à prévisualiser.
            pages: numéros de pages (1-based) ; ``None`` = depuis la première.
            dpi: résolution, par défaut ``settings.previews_dpi``.

        Raises:
            ValidationError: document non PDF (conversion indisponible).
            NotFoundError: document ou fichier absent.
            GenerationError: poppler absent, PDF illisible.
        """
        courant = self._get_fichier(document_id)
        extension = extension_of(courant.nom)
        if extension != "pdf":
            msg = (
                f"Aperçu visuel impossible pour un fichier .{extension} : la "
                "conversion bureautique n'est pas disponible sur ce serveur. "
                "Générer (ou joindre) la version PDF du document, puis demander "
                "son aperçu."
            )
            raise ValidationError(
                msg, details={"document_id": str(document_id), "extension": extension}
            )

        settings = get_settings()
        resolution = dpi or settings.previews_dpi
        plafond = settings.previews_max_pages
        chemin = self.storage.resolve_existing(courant.storage_path)
        total = pages_pdf(chemin)
        rendues = rasteriser_pdf(
            chemin,
            dpi=resolution,
            pages=pages,
            max_pages=plafond,
            outil=settings.pdftoppm_path,
        )

        apercus: list[PageApercuDocument] = []
        for page in rendues:
            publiee = enregistrer_apercu(
                self.storage,
                courant.storage_path,
                page=page.numero,
                octets=page.octets,
                largeur_px=page.largeur_px,
                hauteur_px=page.hauteur_px,
            )
            apercus.append(
                PageApercuDocument(
                    numero=publiee.numero,
                    chemin_logique=publiee.chemin_logique,
                    largeur_px=publiee.largeur_px,
                    hauteur_px=publiee.hauteur_px,
                    cree=publiee.cree,
                )
            )
        return ApercuDocument(
            document_id=courant.id,
            nom=courant.nom,
            nb_pages=total if total is not None else len(rendues),
            dpi=resolution,
            pages=tuple(apercus),
            tronque=total is not None and total > len(rendues),
        )

    def chemin_page_apercu(self, document_id: UUID, page: int) -> tuple[str, Path]:
        """Nom de fichier proposé au navigateur + chemin physique d'une page d'aperçu.

        Même contrat que ``telecharger`` : le chemin physique n'est jamais
        renvoyé par l'API, il est consommé par la réponse de fichier.

        Raises:
            NotFoundError: document absent, ou page jamais rendue.
        """
        courant = self._get_fichier(document_id)
        chemin = resoudre_apercu(self.storage, courant.storage_path, page)
        nom_fichier = f"{Path(courant.nom).stem}-p{int(page):02d}.png"
        return nom_fichier, chemin

    def modifier_metadonnees(
        self, document_id: UUID, entree: DocumentMetadonneesUpdateInput
    ) -> Document:
        """Modifie les **métadonnées** d'un document, jamais son contenu.

        Le contenu se remplace par ``remplacer_document`` (nouvelle version) : une
        version officielle reste immuable.

        ``nom`` et ``type_document`` servent de clé de versionnement
        (``(nom, type_document)`` regroupe les versions) : les changer séparerait
        l'historique. Ils ne sont donc acceptés que sur un document à **une seule
        version** ; au-delà, le service refuse et indique la sortie (archiver puis
        déposer un nouveau document).

        Raises:
            ConflictError: renommage d'un document déjà versionné.
            ValidationError: extension interdite, type inconnu, aucune modification.
        """
        document = self._get_modifiable(document_id)
        modifie = False

        nouveau_nom = entree.nom.strip() if entree.nom is not None else None
        nouveau_type = entree.type_document
        if nouveau_nom is not None and nouveau_nom != document.nom:
            self._valider_renommage_versionnable(document, nouveau_nom)
            self._valider_extension(nouveau_nom)
            document.nom = sanitize_filename(nouveau_nom)
            modifie = True

        if nouveau_type is not None and nouveau_type != document.type_document:
            self._valider_renommage_versionnable(document, nouveau_type)
            document.type_document = self._valider_type(nouveau_type)
            modifie = True

        if entree.doc_metadata is not None and entree.doc_metadata != document.doc_metadata:
            document.doc_metadata = entree.doc_metadata
            modifie = True

        if not modifie:
            raise ValidationError(
                "Aucune modification fournie pour le document",
                details={"document_id": str(document_id)},
            )

        self._trace.record_event(
            actor_type=ActorType.HUMAIN.value,
            action=ActionAudit.DOCUMENT_METADONNEES_MODIFIEES.value,
            entity_type=_ENTITY,
            entity_id=document.id,
            after={
                "nom": document.nom,
                "type_document": document.type_document,
                "version": document.version,
            },
        )
        return document

    def lire_plage(
        self,
        document_id: UUID,
        *,
        from_page: int | None = None,
        to_page: int | None = None,
        unite: str | None = None,
        feuille: str | None = None,
        max_chars: int | None = None,
    ) -> LigneLecturePlage:
        """Lit une **plage** de document (pages PDF, paragraphes DOCX, lignes XLSX).

        Lecture seule, bornée : c'est le chemin attendu pour un document volumineux
        (un appel à proposition de 80 pages ne se lit pas d'un bloc). ``unite``
        n'est jamais devinée — c'est le format qui décide, et la réponse le dit.

        Args:
            from_page: premier élément de la plage (1-indexé, inclusif).
            to_page: dernier élément inclusif ; borné à la fin du document.
            unite: ``pages`` | ``paragraphes`` | ``lignes`` — contrôle explicite
                (une unité qui ne correspond pas au format est refusée).
            feuille: nom de feuille XLSX (sans effet sur les autres formats).
            max_chars: plafond de caractères (défaut : réglage d'extraction).

        Raises:
            NotFoundError: document ou fichier absent.
            ValidationError: format sans lecture par plage, ou unité incohérente.
        """
        from app.documents.inspection import lire_docx_plage, lire_pdf_plage, lire_xlsx_plage

        courant = self._get_fichier(document_id)
        chemin = self.storage.resolve_existing(courant.storage_path)
        suffixe = chemin.suffix.lower()
        plafond = max_chars or self._max_chars

        if suffixe == ".pdf":
            _verifier_unite(unite, "pages", courant.nom)
            brut = lire_pdf_plage(chemin, from_page=from_page, to_page=to_page)
        elif suffixe == ".docx":
            _verifier_unite(unite, "paragraphes", courant.nom)
            brut = lire_docx_plage(chemin, from_page=from_page, to_page=to_page)
        elif suffixe == ".xlsx":
            _verifier_unite(unite, "lignes", courant.nom)
            brut = lire_xlsx_plage(
                chemin, sheet=feuille, from_row=from_page, to_row=to_page
            )
        else:
            raise ValidationError(
                f"Lecture par plage non supportée pour {courant.nom} "
                "(formats : PDF, DOCX, XLSX)",
                details={"extension": suffixe},
            )

        texte = str(brut.get("texte") or "")
        tronque = len(texte) > plafond
        if tronque:
            texte = texte[:plafond]
        total = brut.get("total_pages") or brut.get("total_paragraphes") or brut.get("total_lignes")
        return LigneLecturePlage(
            document_id=courant.id,
            nom=courant.nom,
            unite=str(brut.get("unite") or "pages"),
            debut=int(brut.get("debut") or 1),
            fin=int(brut.get("fin") or 1),
            total=int(total) if total else None,
            texte=texte,
            tronque=tronque or bool(brut.get("tronque")),
        )

    def extraire_texte(self, document_id: UUID) -> ExtractedDocument:
        """Extrait le texte d'un document (**lecture seule**, rien n'est persisté).

        C'est le point d'entrée que consommera ``AgentAnalyseurAppelAProposition`` : il
        obtient du texte, jamais un chemin de fichier ni un accès disque.
        """
        courant = self._get_fichier(document_id)
        chemin = self.storage.resolve_existing(courant.storage_path)
        contenu = extract_text(chemin, nom_fichier=courant.nom, max_chars=self._max_chars)

        return ExtractedDocument(
            document_id=courant.id,
            nom=courant.nom,
            extension=extension_of(courant.nom),
            mime_type=courant.mime_type,
            texte=contenu.texte,
            adaptateur=contenu.adaptateur,
            nb_caracteres=contenu.nb_caracteres,
            nb_pages=contenu.nb_pages,
            nb_feuilles=contenu.nb_feuilles,
            feuilles=contenu.feuilles,
            tronque=contenu.tronque,
        )

    # --- internes -----------------------------------------------------------

    def _get(self, document_id: UUID) -> Document:
        document = self.documents.get(document_id)
        if document is None:
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

    def _get_fichier(self, document_id: UUID) -> Document:
        """Document dont le fichier est encore sur disque (téléchargement, lecture).

        Raises:
            NotFoundError: document inexistant ou supprimé (fichier retiré).
        """
        document = self._get(document_id)
        if document.statut == StatutDocument.SUPPRIME.value:
            msg = "Le fichier de ce document a été retiré du stockage (document supprimé)"
            raise NotFoundError(msg, details={"document_id": str(document_id)})
        return document

    def _fiche_corbeille(self, document: Document) -> FicheCorbeille:
        """Fiche supprimée → état de corbeille (échéance, restaurabilité, blocage)."""
        trace = self._trace_suppression(document)
        supprime_le, expire_le = self._dates_suppression(document)
        statut_avant = str(trace.get("statut_avant") or "")
        blocage = (
            "Trace de suppression incomplète : l'état de retour est inconnu."
            if not statut_avant
            else self._blocage_restauration(document, statut_avant)
        )
        maintenant = datetime.now(UTC)
        expiree = expire_le is not None and expire_le <= maintenant
        jours = (
            None
            if expire_le is None
            else max(ceil((expire_le - maintenant).total_seconds() / 86400), 0)
        )
        return FicheCorbeille(
            document_id=document.id,
            nom=document.nom,
            type_document=document.type_document,
            version=document.version,
            statut_avant=statut_avant,
            supprime_le=supprime_le,
            supprime_par=_texte_ou_none(trace.get("par")),
            motif=_texte_ou_none(trace.get("motif")),
            expire_le=expire_le,
            expiree=expiree,
            jours_restants=jours,
            retention_jours=int(trace.get("retention_jours") or self._corbeille_jours),
            fichier_present=self.storage.exists(document.storage_path),
            restaurable=blocage is None,
            blocage=blocage,
            organisation_id=document.organisation_id,
            appel_a_proposition_id=document.appel_a_proposition_id,
            offre_id=document.offre_id,
            mission_id=document.mission_id,
            equipe_id=document.equipe_id,
            session_id=document.session_id,
        )

    def _blocage_restauration(self, document: Document, statut_avant: str) -> str | None:
        """Pourquoi cette fiche ne peut pas être restaurée (``None`` : elle peut).

        Deux protections, jamais un refus gratuit :

        1. **le nom et la version sont repris** — après une suppression, redéposer
           le même nom crée une nouvelle version 1 (ADR 0005 §7). Restaurer
           l'ancienne fiche donnerait deux versions 1 exploitables portant le même
           nom, donc une ambiguïté de version impossible à lever après coup ;
        2. **une seule version officielle** — restaurer une version ``approved``
           alors qu'une autre version de ce document est officielle créerait deux
           documents officiels simultanés, ce que ``approuver_document`` interdit.
           Le service refuse et indique l'ordre des gestes, plutôt que d'archiver
           silencieusement un document officiel.
        """
        reprise = self.documents.find_version(
            document.nom, document.type_document, document.version
        )
        if reprise is not None:
            return (
                f"La version {document.version} de « {document.nom} » existe déjà : "
                "un document de ce nom a été redéposé depuis. Traitez le document "
                "en place avant de restaurer cette fiche."
            )
        if statut_avant == StatutDocument.APPROVED.value:
            officielles = [
                version
                for version in self.documents.list_approved(
                    document.nom, document.type_document
                )
                if version.id != document.id
            ]
            if officielles:
                return (
                    f"« {document.nom} » a déjà une version officielle "
                    f"(v{officielles[0].version}) : archivez-la avant de restaurer "
                    "celle-ci, sinon le document aurait deux versions officielles."
                )
        return None

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

    def _chemin_restauration(self, document: Document) -> str:
        """Chemin d'écriture du fichier redéposé.

        L'emplacement d'origine est réutilisé s'il est libre : une restauration
        remet le document à sa place, elle ne le range pas ailleurs. S'il est
        occupé (un autre document a été déposé au même endroit), le nom versionné
        du document et le dossier de son ancre fournissent un chemin libre — même
        dé-collision que le rattachement, sans jamais écraser un fichier.
        """
        if not self.storage.exists(document.storage_path):
            return document.storage_path
        ancre = self._ancre_du_document(document)
        return self._chemin_logique_libre(
            ancre.scope,
            ancre.entity_id,
            versioned_filename(document.nom, document.version),
        )

    def _trace_suppression(self, document: Document) -> dict[str, object]:
        """Tombstone écrit à la suppression (``{}`` si la fiche n'en porte pas)."""
        trace = (document.doc_metadata or {}).get(_META_SUPPRESSION)
        return trace if isinstance(trace, dict) else {}

    def _dates_suppression(
        self, document: Document
    ) -> tuple[datetime | None, datetime | None]:
        """Date de suppression et échéance de conservation d'une fiche.

        Une fiche supprimée **avant** la corbeille n'a pas d'échéance écrite :
        elle est recalculée avec la durée courante plutôt que laissée sans date —
        sans quoi elle serait impurgeable et affichée sans terme.
        """
        trace = self._trace_suppression(document)
        supprime_le = _date_iso(trace.get("le"))
        expire_le = _date_iso(trace.get("expire_le"))
        if expire_le is None and supprime_le is not None:
            expire_le = supprime_le + timedelta(
                days=int(trace.get("retention_jours") or self._corbeille_jours)
            )
        return supprime_le, expire_le

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

    def _materialiser(
        self, document: Document, chemin_logique: str, entree: DocumentUploadInput
    ) -> None:
        """Écrit le fichier puis la ligne ``documents`` (+ compensation)."""
        stocke = self.storage.save(chemin_logique, entree.stream)
        document.mime_type = stocke.mime_type
        document.taille_octets = stocke.taille_octets
        document.storage_path = stocke.logical_path
        document.storage_disk = self.storage.disk_name
        document.checksum_sha256 = stocke.checksum_sha256

        try:
            self.documents.add(document)
            self._session.flush()  # identifiant disponible pour la trace
            # created_at/updated_at sont générés par la base : une relecture les
            # matérialise pour la réponse API (une seule requête supplémentaire).
            self._session.refresh(document)
        except Exception:
            # Compensation : un échec base ne laisse pas de fichier orphelin.
            # (Fenêtre résiduelle : un rollback de l'appelant après ce point,
            # ou un arrêt brutal, laisse le fichier sans ligne — limitation
            # documentée, cf. docs/design/DOCUMENT_INFRASTRUCTURE.md.)
            self.storage.remove(stocke.logical_path)
            raise

    def _resoudre_ancre(self, entree: DocumentUploadInput) -> DocumentAnchor:
        """Détermine l'ancre : réelle si elle existe, sinon ``generated/{id}``."""
        fournies = [
            (champ, valeur)
            for champ, _scope in _ANCHORS.items()
            if (valeur := getattr(entree, champ)) is not None
        ]
        if len(fournies) > 1:
            msg = (
                "Un document ne peut être rattaché qu'à une seule ancre métier "
                "(organisation, appel à proposition, offre, mission, équipe ou session)"
            )
            raise ValidationError(msg, details={"ancres_fournies": [c for c, _ in fournies]})

        if len(fournies) == 1:
            champ, entity_id = fournies[0]
            if self._ancre_existe(champ, entity_id):
                return DocumentAnchor(
                    scope=_ANCHORS[champ], champ=champ, entity_id=entity_id
                )
        return DocumentAnchor(scope=_SCOPE_GENERE, champ=_CHAMP_GENERE, entity_id=uuid4())

    def _ancre_existe(self, champ: str, entity_id: UUID) -> bool:
        depots: dict[str, BaseRepository] = {
            "organisation_id": self._organisations,
            "appel_a_proposition_id": self._appels,
            "offre_id": self._offres,
            "mission_id": self._missions,
            "equipe_id": self._equipes,
            "session_id": self._sessions,
        }
        return depots[champ].get(entity_id) is not None

    def _cible_rattachement(
        self,
        courant: Document,
        *,
        appel_a_proposition_id: UUID | None = None,
        offre_id: UUID | None = None,
        mission_id: UUID | None = None,
        equipe_id: UUID | None = None,
        session_id: UUID | None = None,
        organisation_id: UUID | None = None,
    ) -> tuple[str, UUID, str]:
        """Valide la cible d'un rattachement : ``(champ, identifiant, scope)``.

        Un document déjà ancré ne change pas d'ancre silencieusement : il faut
        le dire à l'humain plutôt que de réécrire une classification.
        """
        fournies = [
            (champ, valeur)
            for champ, valeur in (
                ("appel_a_proposition_id", appel_a_proposition_id),
                ("offre_id", offre_id),
                ("mission_id", mission_id),
                ("equipe_id", equipe_id),
                ("session_id", session_id),
                ("organisation_id", organisation_id),
            )
            if valeur is not None
        ]
        if len(fournies) != 1:
            raise ValidationError(
                "Le rattachement exige exactement une ancre métier",
                details={"ancres_fournies": [champ for champ, _ in fournies]},
            )

        deja = [
            champ
            for champ in _ANCHORS
            if getattr(courant, champ, None) is not None
        ]
        if deja:
            raise ConflictError(
                "Ce document est déjà rattaché à une ancre métier",
                details={
                    "document_id": str(courant.id),
                    "ancres_existantes": deja,
                },
            )

        champ, entity_id = fournies[0]
        if not self._ancre_existe(champ, entity_id):
            raise NotFoundError(
                f"Ancre {champ} introuvable : {entity_id}",
                details={"champ": champ, "entity_id": str(entity_id)},
            )
        return champ, entity_id, _ANCHORS[champ]

    def _chemin_logique_libre(self, scope: str, entity_id: UUID, nom: str) -> str:
        """Chemin libre dans le dossier cible (dé-collision purement technique).

        Deux pièces jointes homonymes d'un même appel ne peuvent pas occuper le
        même chemin : le suffixe ``_r{n}`` évite l'écrasement sans toucher au nom
        **logique** du document (``documents.nom`` reste inchangé).
        """
        fichier = Path(nom)
        for indice in range(1, 100):
            candidat = build_logical_path(
                scope, entity_id, f"{fichier.stem}_r{indice}{fichier.suffix}"
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
            scope=_SCOPE_GENERE, champ=_CHAMP_GENERE, entity_id=document.id
        )

    def _valider_type(self, type_document: str) -> str:
        valides = {type_.value for type_ in TypeDocument}
        if type_document not in valides:
            msg = f"Type de document inconnu : {type_document!r}"
            raise ValidationError(msg, details={"types_valides": sorted(valides)})
        return type_document

    @staticmethod
    def _valider_extension(nom: str) -> None:
        """Le renommage ne doit pas sortir de l'allowlist documentaire."""
        extension = extension_of(nom)
        if extension not in ALLOWED_EXTENSIONS:
            msg = f"Extension de fichier non autorisée : .{extension or '?'}"
            raise ValidationError(
                msg, details={"extensions_autorisees": sorted(ALLOWED_EXTENSIONS)}
            )

    def _valider_renommage_versionnable(self, document: Document, cible: str) -> None:
        """Nom et type forment la clé de versionnement ``(nom, type_document)``.

        Les changer sur un document déjà versionné séparerait l'historique : le
        service refuse et indique la sortie (archiver, puis déposer un nouveau
        document) plutôt que de casser silencieusement la chaîne des versions.
        """
        versions = self.documents.list_versions(document.nom, document.type_document)
        if len(versions) <= 1:
            return
        raise ConflictError(
            "Un document à plusieurs versions ne peut pas être renommé ni changer "
            "de type : archivez-le puis déposez un nouveau document",
            details={
                "document_id": str(document.id),
                "nom_actuel": document.nom,
                "type_actuel": document.type_document,
                "cible": cible,
                "versions": len(versions),
            },
        )

    def _statut_depot(self, entree: DocumentUploadInput) -> str:
        """Statut de naissance d'un dépôt, selon sa nature.

        - document de **session** ancré (demande 30/09) : pas un cycle d'offre —
          il naît ``genere`` quand l'assistant formateur (ou l'outil de
          génération) le produit, ``importe`` quand un humain le dépose ;
        - tout autre document agent naît ``proposed`` (règle 6 [C]) ;
        - un dépôt humain hors session naît ``draft``.
        """
        if entree.session_id is not None:
            if entree.proposed_by_agent:
                return StatutDocument.GENERE.value
            return StatutDocument.IMPORTE.value
        if entree.proposed_by_agent:
            return StatutDocument.PROPOSED.value
        return StatutDocument.DRAFT.value

    def _acteur(self, entree: DocumentUploadInput) -> str:
        if entree.proposed_by_agent:
            return ActorType.AGENT.value
        return ActorType.HUMAIN.value

    def _proposed_by_agent(self, document: Document) -> str | None:
        if not document.doc_metadata:
            return None
        agent = document.doc_metadata.get(_META_KEY)
        return str(agent) if agent else None


def _date_iso(valeur: object) -> datetime | None:
    """Lit une date ISO écrite dans un tombstone ; ``None`` si absente ou illisible.

    Une valeur corrompue ne fait jamais échouer la corbeille : la fiche s'affiche
    sans échéance plutôt que de rendre la page inaccessible. Les dates sont
    normalisées en UTC — comparer une date naïve à ``datetime.now(UTC)`` lèverait
    une ``TypeError``, donc une fiche illisible en apparence.
    """
    if not isinstance(valeur, str) or not valeur:
        return None
    try:
        lue = datetime.fromisoformat(valeur)
    except ValueError:
        return None
    return lue if lue.tzinfo is not None else lue.replace(tzinfo=UTC)


def _texte_ou_none(valeur: object) -> str | None:
    """Texte d'un champ de tombstone, ou ``None`` (jamais ``"None"`` affiché)."""
    if valeur is None:
        return None
    texte = str(valeur)
    return texte or None


def _verifier_unite(unite: str | None, attendue: str, nom: str) -> None:
    """Refuse une unité de lecture qui ne correspond pas au format du fichier.

    Une unité fausse (« pages » pour un DOCX) produirait une lecture
    trompeuse : mieux vaut un refus explicite qu'un texte pris au hasard.
    """
    if unite is None or unite == attendue:
        return
    msg = (
        f"Unité de lecture incompatible avec {nom} : attendu {attendue!r}, "
        f"reçu {unite!r}"
    )
    raise ValidationError(msg, details={"unite": unite, "unite_attendue": attendue})


__all__ = ["DocumentService"]
