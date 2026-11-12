"""Suppression logique, corbeille, restauration et purge.

Supprimer retire le fichier et passe la fiche en ``supprime`` sans
effacer la ligne. Restaurer remet la fiche dans son état antérieur.
Seul un administrateur purge, et seulement si plus rien ne référence
la fiche. Ces règles sont inchangées par le découpage.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from math import ceil
from typing import BinaryIO
from uuid import UUID

from app.application.dto import DecisionInput, FicheCorbeille, FicheNonPurgee, RapportPurgeCorbeille
from app.application.services.document_service_commun import (
    _ENTITY,
    _META_RESTAURATIONS,
    _META_SUPPRESSION,
    _date_iso,
    _texte_ou_none,
    logger,
)
from app.core.errors import ConflictError
from app.documents.paths import versioned_filename
from app.domain.document import Document
from app.domain.enums import ActionAudit, ActorType, StatutDocument
from app.domain.state_machines import validate_transition


class CorbeilleDocumentMixin:
    """See ``document_service`` pour le socle partagé."""

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

        Un compte restreint (formateur) supprime **dans son périmètre** : la
        suppression est logique (la fiche reste, le fichier part), donc
        réversible par l'équipe interne. La règle d'accessibilité est appliquée
        par ``_get_modifiable`` → ``_get`` → ``_est_accessible`` (404 hors
        périmètre) : rien à réimplémenter ici.

        Returns:
            Nombre de fichiers réellement retirés du stockage (fichier + aperçus).

        Raises:
            NotFoundError: document inexistant ou hors périmètre.
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
        except Exception as exc:
            # Compensation : un échec base ne laisse pas le fichier redéposé
            # orphelin sur le disque (même règle que le rattachement).
            if chemin_ecrit is not None:
                try:
                    self.storage.remove(chemin_ecrit)
                except Exception as disque:  # pragma: no cover - dépend du disque
                    logger.warning("Fichier redéposé non retiré (%s) : %s", chemin_ecrit, disque)
            self._traduire_integrite(exc, document)
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
        self._perimetre.exiger_complet("purger un document")
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

    def purger_corbeille(self, decision: DecisionInput) -> RapportPurgeCorbeille:
        """Purge **toute la corbeille** — administrateurs, geste tracé fiche par fiche.

        Chaque fiche suit **exactement** la règle de ``purger_document`` : une
        fiche encore référencée (gabarit de modèle, support de formation, modèle
        d'offre) n'est jamais détruite. La différence est le traitement de ce
        refus : une purge de masse ne s'arrête pas à la première fiche bloquée —
        elle purge ce qui peut l'être et **rend la liste** de ce qu'elle a laissé,
        avec le motif du refus. Un lot partiellement purgé qui se tairait ferait
        croire à une corbeille vide.

        Chaque fiche conserve son propre événement d'audit (nom, version, motif),
        écrit par ``purger_document`` avant la suppression de la ligne.

        Returns:
            Compte des fiches purgées, fichiers retirés et fiches conservées.

        Raises:
            PermissionDeniedError: périmètre restreint (défense en profondeur —
                la route exige déjà un administrateur).
        """
        self._perimetre.exiger_complet("purger la corbeille")
        fiches = self.lister_corbeille()
        purgees = 0
        fichiers = 0
        ignores: list[FicheNonPurgee] = []
        for fiche in fiches:
            try:
                fichiers += self.purger_document(fiche.document_id, decision)
            except ConflictError as exc:
                ignores.append(
                    FicheNonPurgee(
                        document_id=fiche.document_id,
                        nom=fiche.nom,
                        motif=exc.message,
                    )
                )
                continue
            purgees += 1
        return RapportPurgeCorbeille(
            fiches_purgees=purgees,
            fichiers_supprimes=fichiers,
            ignores=tuple(ignores),
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
            proprietaire_id=self._proprietaire_du_chemin(ancre, document),
        )

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
            appel_id=document.appel_id,
            offre_id=document.offre_id,
            mission_id=document.mission_id,
            equipe_id=document.equipe_id,
            session_id=document.session_id,
        )

    def _trace_suppression(self, document: Document) -> dict[str, object]:
        """Tombstone écrit à la suppression (``{}`` si la fiche n'en porte pas)."""
        trace = (document.doc_metadata or {}).get(_META_SUPPRESSION)
        return trace if isinstance(trace, dict) else {}
