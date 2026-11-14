"""Cycle de vie métier d'un document.

Dépôt, nouvelle version, rattachement, soumission, approbation, refus,
archivage, dépôt de livrable et génération d'offre. Le statut d'un
document produit par un agent reste ``proposed`` : un dépôt ne vaut
jamais validation humaine.
"""

from __future__ import annotations

import io
from pathlib import Path
from uuid import UUID, uuid4

from app.application.dto import DecisionInput, DocumentAnchor, DocumentUploadInput
from app.application.services import notifications_evenements as notifications
from app.application.services.document_service_commun import (
    _ANCHORS,
    _CHAMP_PERSONNEL,
    _ENTITY,
    _META_GENERE_POUR,
    _META_KEY,
    _SCOPE_PERSONNEL,
    _VISIBILITE_PRIVEE,
    logger,
)
from app.core.errors import CarsoError, ConflictError, NotFoundError, ValidationError
from app.documents.paths import build_logical_path, sanitize_filename, versioned_filename
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


class MetadonneesDocumentMixin:
    """See ``document_service`` pour le socle partagé."""

    def enregistrer_document(self, entree: DocumentUploadInput) -> Document:
        """Dépose un nouveau document (version 1).

        Une ancre réelle range le fichier sous le dossier métier. Une ancre
        absente ou inventée range le document dans le **dossier de travail de
        son propriétaire** (``workdir/{compte}/{id}/``) avec le type
        ``non_classe`` — aucune FK n'est inventée, et sans compte identifié le
        dépôt est refusé : une fiche personnelle sans propriétaire n'apparaît
        dans le dossier de personne.

        Raises:
            ValidationError: plusieurs ancres, type de document ou nom
                invalide, fichier refusé par la validation de contenu.
            ConflictError: une version 1 existe déjà pour ce nom et ce type.
        """
        ancre = self._resoudre_ancre(entree)
        self._exiger_ancre_autorisee(ancre.champ, ancre.entity_id)
        # Un document sans ancre n'existe que dans le dossier d'un compte :
        # le propriétaire est une **condition du chemin**, pas un décor.
        proprietaire_chemin = self._proprietaire_du_chemin(ancre)
        type_document = (
            TypeDocument.NON_CLASSE.value
            if ancre.scope == _SCOPE_PERSONNEL
            else self._valider_type(entree.type_document)
        )
        nom = sanitize_filename(entree.nom)

        # Révision d'un dépôt « nouveau document » : celle du **livrable**.
        # `offre_technique.docx` puis `offre_technique.pdf` sont le même
        # livrable en deux formats, donc la même révision — le nom de fichier
        # seul ne suffisait plus. Une nouvelle révision reste le geste de
        # `remplacer_document` : redéposer le même fichier n'est pas une
        # révision silencieuse, c'est un doublon, et il est refusé.
        ancres_depot = (
            {ancre.champ: ancre.entity_id}
            if ancre.champ and ancre.scope != _SCOPE_PERSONNEL
            else None
        )
        if entree.revision_imposee is not None:
            revision = int(entree.revision_imposee)
        else:
            if self.documents.find_active_by_name(nom, type_document) is not None:
                msg = "Un document de ce nom et de ce type existe déjà"
                raise ConflictError(
                    msg, details={"nom": nom, "type_document": type_document}
                )
            # Premier dépôt de ce format : il rejoint la révision courante du
            # livrable (1 si le livrable est neuf).
            revision = self._revision_courante(nom, type_document, ancres_depot) or 1
        if self.documents.find_version(nom, type_document, revision) is not None:
            msg = (
                f"La révision {revision} existe déjà pour ce livrable "
                "(ce nom, ce type et même rattachement)"
            )
            raise ConflictError(
                msg,
                details={"nom": nom, "type_document": type_document, "version": revision},
            )

        # Le fichier disque porte la révision : deux formats du même livrable
        # (`offre_technique.docx` et `.pdf`) coexistent dans un même dossier,
        # donc à la même révision ils partagent le suffixe `_v{n}` et jamais un
        # chemin — un écrasement silencieux de l'un par l'autre est impossible.
        chemin_logique = build_logical_path(
            ancre.scope,
            ancre.entity_id,
            versioned_filename(nom, revision),
            proprietaire_id=proprietaire_chemin,
        )
        kwargs_ancre = {ancre.champ: ancre.entity_id} if ancre.champ else {}
        # Seconde ancre mission/session : la pièce est rangée une seule fois,
        # sous le dossier du scope choisi, mais elle appartient aussi à l'autre
        # objet quand il existe (une mission a une session). Aucune copie.
        for champ_complement, identifiant in self._ancre_complementaire(
            ancre, entree
        ).items():
            kwargs_ancre.setdefault(champ_complement, identifiant)
        document = Document(
            id=ancre.entity_id if ancre.scope == _SCOPE_PERSONNEL else uuid4(),
            nom=nom,
            type_document=type_document,
            version=revision,
            statut=self._statut_depot(entree),
            created_by=entree.created_by or entree.proposed_by_agent,
            #: Propriétaire (ADR 0013) : le compte authentifié qui dépose (ou
            #: fait générer par un agent). C'est lui qui décidera de la
            #: visibilité d'un document sans ancre.
            proprietaire_id=self._perimetre.utilisateur_id,
            #: Un document naît **privé** : publier est un geste de son
            #: propriétaire, jamais un effet de bord d'un dépôt. Écrit ici plutôt
            #: que laissé au défaut du serveur, pour que la règle soit lisible là
            #: où la fiche est créée.
            visibilite=_VISIBILITE_PRIVEE,
            doc_metadata=self._metadonnees(entree),
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

    def _revision_courante(
        self, nom: str, type_document: str, ancres: dict[str, UUID] | None = None
    ) -> int:
        """Révision la plus haute du **livrable** (0 si aucune version).

        Sert à un premier dépôt d'un format : `offre_technique.pdf` rejoint la
        révision du `offre_technique.docx` déjà déposé, au lieu d'ouvrir une
        série en v1 qui ferait diverger les deux formats du même livrable.
        """
        versions = self.documents.versions_du_livrable(
            Path(nom).stem, type_document, ancres or None
        )
        return max((document.version for document in versions), default=0)

    def _revision_suivante(
        self,
        nom: str,
        type_document: str,
        imposee: int | None = None,
        ancres: dict[str, UUID] | None = None,
    ) -> int:
        """Révision à donner à un dépôt de ce livrable.

        La révision est celle du **livrable**, pas du seul nom de fichier :
        `offre_technique.docx` et `offre_technique.pdf` partagent leur série,
        donc leur numéro (décision du 11/11, portée retenue par l'utilisateur).
        Le format ne crée plus une nouvelle série à chaque fois.

        Args:
            nom: nom du fichier déposé (avec son extension).
            type_document: type effectif du dépôt.
            imposee: révision imposée par l'appelant (jumeau PDF du même
                livrable). Elle est respectée **telle quelle** : c'est ce qui
                garde le DOCX et son PDF à la même révision même déposés dans
                deux appels successifs.
            ancres: rattachement du document, pour ne pas mélanger deux
                livrables de même nom sur deux objets différents.
        """
        if imposee is not None:
            return int(imposee)
        return self._revision_courante(nom, type_document, ancres) + 1

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
        # Une ancre dont l'objet a disparu entre la lecture et l'écriture ne doit
        # pas devenir une violation de clé étrangère : on refuse avec le motif.
        self._exiger_ancre_presente(ancre, courant)
        proprietaire_chemin = self._proprietaire_du_chemin(ancre, courant)
        nom = sanitize_filename(entree.nom or courant.nom)

        if nom != courant.nom:
            msg = (
                "Le nom du document ne peut pas changer en cours de versionnement "
                "(créer un nouveau document à la place)"
            )
            raise ValidationError(msg, details={"nom": nom, "nom_attendu": courant.nom})

        nouvelle_version = self._revision_suivante(
            nom,
            courant.type_document,
            entree.revision_imposee,
            ancres=self._ancres_de_version(courant),
        )
        if self.documents.find_version(nom, courant.type_document, nouvelle_version) is not None:
            msg = f"La version {nouvelle_version} existe déjà pour ce document"
            raise ConflictError(msg, details={"version": nouvelle_version})

        nom_fichier = versioned_filename(nom, nouvelle_version)
        chemin_logique = build_logical_path(
            ancre.scope,
            ancre.entity_id,
            nom_fichier,
            proprietaire_id=proprietaire_chemin,
        )

        document = Document(
            nom=nom,
            type_document=courant.type_document,
            version=nouvelle_version,
            statut=self._statut_depot(entree),
            created_by=entree.created_by or entree.proposed_by_agent,
            #: La nouvelle version reste la propriété du compte qui la dépose,
            #: même si la précédente appartenait à un autre compte.
            proprietaire_id=self._perimetre.utilisateur_id,
            doc_metadata=self._metadonnees(entree),
            #: Les deux ancres mission/session suivent la version : réviser une
            #: pièce ne doit pas la faire disparaître de l'autre écran.
            **self._ancres_de_version(courant),
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
        appel_id: UUID | None = None,
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
            appel_id=appel_id,
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
        except Exception as exc:
            # Compensation : un échec base ne laisse pas de copie orpheline.
            self.storage.remove(stocke.logical_path)
            self._traduire_integrite(exc, courant, champ=champ)
            raise

        # L'ancienne copie n'est retirée qu'une fois la nouvelle adresse écrite.
        self.storage.remove(ancien_chemin)
        return courant

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
        self._perimetre.exiger_complet("approuver un document")
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
        self._perimetre.exiger_complet("refuser un document")
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
        self._perimetre.exiger_complet("archiver un document")
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

    def deposer_livrable(self, entree: DocumentUploadInput) -> Document:
        """Dépose un livrable d'agent, en **révisant** s'il existe déjà.

        Même nom, même type et même ancre ⇒ **nouvelle version** du document
        existant (règle déjà appliquée par les tools de génération) ; sinon un
        nouveau document naît, en ``proposed``. Le service reste le **seul**
        écrivain de l'arborescence officielle — un script d'agent n'y écrit
        jamais directement (AGENTS.md §2.5 et §7).

        Returns:
            Le document déposé (ou la nouvelle version).

        Raises:
            ValidationError: type de document inconnu, nom invalide, contenu
                refusé par la validation de fichier.
        """
        ancre = self._resoudre_ancre(entree)
        if ancre.scope == _SCOPE_PERSONNEL:
            # Sans ancre, le rangement est le dossier du compte et le type réel
            # ``non_classe`` : il n'y a pas de famille de versions à réviser.
            return self.enregistrer_document(entree)
        type_reel = self._valider_type(entree.type_document)
        nom = sanitize_filename(entree.nom)
        existant = self.derniere_version(nom, type_reel)
        if (
            existant is not None
            and existant.statut
            not in (StatutDocument.ARCHIVED.value, StatutDocument.SUPPRIME.value)
            and self._memes_ancres(
                existant, ancre, self._ancre_complementaire(ancre, entree)
            )
        ):
            return self.remplacer_document(existant.id, entree)
        return self.enregistrer_document(entree)

    def generer_document_offre(self, offre_id: UUID) -> Document:
        """Génère le document de l'offre (chemin déterministe).

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
        self._perimetre.exiger_complet("générer le document d'une offre")
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
            f"Lot {lot.numero} : {lot.titre}",
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
            ["Appel", lot.appel.reference],
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

    def derniere_version(self, nom: str, type_document: str) -> Document | None:
        """Version exploitable la plus récente d'un document nommé (lecture pure).

        Sert à savoir si un dépôt **révise** un document existant (nouvelle
        version) ou en crée un nouveau : la décision métier reste à l'appelant,
        le service se contente de répondre « voici l'état courant ».
        """
        numero = self.documents.current_version(nom, type_document)
        if numero == 0:
            return None
        return self.documents.find_version(nom, type_document, numero)

    def versions(self, document_id: UUID) -> list[Document]:
        """Historique complet des versions d'un document, plus récente d'abord."""
        courant = self._get(document_id)
        return self.documents.list_versions(courant.nom, courant.type_document)

    def obtenir(self, document_id: UUID) -> Document:
        """Charge un document (lecture pure)."""
        return self._get(document_id)

    def annuler_depot(self, document_id: UUID, acteur_id: str) -> int:
        """Annule un **dépôt** resté intact — la pièce jointe retirée avant envoi.

        Un fichier joint au chat est d'abord un document CARSO (§7) : si
        l'utilisateur le retire avant d'envoyer son message, le laisser en base
        interdit de le redéposer (même nom, même type) et laisse un orphelin que
        personne n'a jamais vu. Seul un dépôt humain encore ``draft`` peut donc
        s'annuler — jamais un document généré, proposé, importé, approuvé,
        archivé ou supprimé, qui a déjà vécu dans le cycle.

        Trois verrous : le **déposant** lui-même, un statut de naissance intact,
        et aucune référence vivante (un modèle, un support ou une offre ne perd
        jamais son fichier par effet de bord — même règle que la purge).

        Returns:
            Nombre de fichiers réellement retirés du stockage.

        Raises:
            NotFoundError: document inexistant, ou déposé par quelqu'un d'autre.
            ConflictError: document engagé dans le cycle, ou encore référencé.
        """
        document = self._get(document_id)
        if document.statut != StatutDocument.DRAFT.value:
            raise ConflictError(
                "Seul un dépôt encore vierge s'annule : ce document a déjà une "
                "vie dans le cycle documentaire.",
                details={"document_id": str(document_id), "statut": document.statut},
            )
        if (document.created_by or "") != acteur_id:
            # Même réponse qu'un document absent : on ne révèle pas l'existence
            # d'un dépôt qui n'appartient pas à l'appelant.
            raise NotFoundError(f"Document {document_id} introuvable")

        references = self._references(document)
        if references:
            raise ConflictError(
                "Ce document est déjà référencé : l'annuler détacherait des "
                "objets qui ne lui appartiennent pas.",
                details={
                    "document_id": str(document_id),
                    "nom": document.nom,
                    "references": references,
                },
            )

        retires = self._retirer_fichiers(document)
        self._trace.record_event(
            actor_type=ActorType.HUMAIN.value,
            action=ActionAudit.DOCUMENT_DEPOT_ANNULE.value,
            entity_type=_ENTITY,
            entity_id=document.id,
            before={
                "nom": document.nom,
                "type_document": document.type_document,
                "version": document.version,
                "statut": document.statut,
                "storage_path": document.storage_path,
            },
            event_metadata={
                "annulation_depot": True,
                "fichiers_retires": retires,
            },
        )
        # La fiche disparaît après sa trace : l'audit garde ce que l'annulation a
        # effacé. Une annulation n'est pas une corbeille — la fiche n'a jamais servi.
        self.documents.delete(document)
        return retires

    def _acteur(self, entree: DocumentUploadInput) -> str:
        if entree.proposed_by_agent:
            return ActorType.AGENT.value
        return ActorType.HUMAIN.value

    def _ancre_complementaire(
        self, ancre: DocumentAnchor, entree: DocumentUploadInput
    ) -> dict[str, UUID]:
        """Seconde ancre **mission/session** à poser, sans copier la pièce.

        Règle (décision utilisateur 10/11) : la fiche de présence appartient à
        la session, et la liste des bénéficiaires, la checklist et le rapport
        appartiennent à la fois à la mission et à sa session. Une mission a
        **une** session (``sessions.mission_id`` unique), donc le rattachement
        est déterminé, jamais deviné.

        - Dépôt sur la mission → la session de cette mission reçoit la pièce.
        - Dépôt sur la session → la mission de cette session la reçoit.
        - Une ancre complémentaire fournie par l'appelant et contredisant la
          relation réelle est ignorée : la base fait foi, pas le formulaire.

        Returns:
            Les champs d'ancre à poser en plus (``{}`` si aucun).
        """
        complementaires: dict[str, UUID] = {}
        if ancre.champ == "mission_id":
            session_formation = self._sessions.get_for_mission(ancre.entity_id)
            if session_formation is not None:
                complementaires["session_id"] = session_formation.id
        elif ancre.champ == "session_id":
            session_formation = self._sessions.get(ancre.entity_id)
            if session_formation is not None and session_formation.mission_id is not None:
                complementaires["mission_id"] = session_formation.mission_id
        # Une ancre complémentaire explicitement demandée ne peut que confirmer
        # la relation réelle : sinon elle est écartée, avec une trace.
        demandee = entree.mission_id if ancre.champ == "session_id" else entree.session_id
        if demandee is not None and demandee not in complementaires.values():
            logger.info(
                "Ancre complémentaire ignorée (relation réelle différente)",
                extra={"ancre": ancre.champ, "demandee": str(demandee)},
            )
        return complementaires

    @staticmethod
    def _ancres_de_version(courant: Document) -> dict[str, UUID]:
        """Ancres à recopier sur la nouvelle version d'un document.

        La version rangée garde **toutes** les ancres de la fiche qu'elle révise :
        son ancre principale (organisation, appel, offre, mission, équipe ou
        session) et son ancre complémentaire mission/session. Réviser une pièce
        ne la fait donc jamais disparaître de l'écran de son objet — ni de la
        mission quand elle est relue depuis la session, ni de l'offre quand la
        copie de modèle est rééditée.

        Le modèle autorise une ancre principale **au plus**, plus la paire
        mission/session : recopier toutes les valeurs non nulles restitue
        exactement l'appartenance de la fiche, sans en inventer aucune.
        """
        ancres: dict[str, UUID] = {}
        for champ in _ANCHORS:
            valeur = getattr(courant, champ)
            if valeur is not None:
                ancres[champ] = valeur
        return ancres

    def _cible_rattachement(
        self,
        courant: Document,
        *,
        appel_id: UUID | None = None,
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
                ("appel_id", appel_id),
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

    def _exiger_ancre_presente(self, ancre: DocumentAnchor, document: Document) -> None:
        """Refuse une révision dont l'objet d'ancre n'existe plus.

        Le recopiage des ancres sur la nouvelle version s'appuie sur la fiche
        révisée, pas sur les données du formulaire. Si l'objet a été supprimé
        entre la lecture de la fiche et l'écriture — ou si sa ligne a disparu
        hors du circuit habituel — la base refuserait l'insertion par contrainte
        de clé étrangère, ce qui remonterait comme une panne SQL. Le motif métier
        est plus utile : la fiche est conservée, seule la révision est refusée.

        Cinq ancres sont en ``ON DELETE CASCADE`` (la fiche part avec son objet),
        donc le cas ordinaire ne se présente pas ; ``organisation_id`` est en
        ``SET NULL`` et devient simplement une fiche sans ancre.

        Raises:
            NotFoundError: l'objet de l'ancre n'existe plus en base.
        """
        if not ancre.champ or self._ancre_existe(ancre.champ, ancre.entity_id):
            return
        libelle = ancre.champ.removesuffix("_id")
        msg = (
            f"La révision est refusée : l'objet « {libelle} » auquel ce document "
            "est rattaché n'existe plus. La fiche et son fichier sont conservés ; "
            "rattachez-la à un objet existant ou laissez-la hors rattachement."
        )
        raise NotFoundError(
            msg,
            details={
                "document_id": str(document.id),
                "nom": document.nom,
                "ancre": ancre.champ,
                "entity_id": str(ancre.entity_id),
            },
        )

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
        except Exception as exc:
            # Compensation : un échec base ne laisse pas de fichier orphelin.
            # (Fenêtre résiduelle : un rollback de l'appelant après ce point,
            # ou un arrêt brutal, laisse le fichier sans ligne — limitation
            # documentée, cf. docs/design/DOCUMENT_INFRASTRUCTURE.md.)
            self.storage.remove(stocke.logical_path)
            self._traduire_integrite(exc, document)
            raise

    def _memes_ancres(
        self,
        document: Document,
        ancre: DocumentAnchor,
        complementaires: dict[str, UUID] | None = None,
    ) -> bool:
        """Vrai si le document appartient au **même objet métier**.

        Un document homonyme rangé ailleurs n'est jamais révisé : deux objets
        métier différents gardent chacun leur livrable. L'ancre complémentaire
        mission/session (``complementaires``) fait partie de l'identité : une
        pièce déposée sur la mission puis redéposée depuis sa session est la
        **même** pièce, elle se révise au lieu d'être dupliquée.
        """
        complementaires = complementaires or {}
        for champ in _ANCHORS:
            if champ == ancre.champ:
                attendu: UUID | None = ancre.entity_id
            else:
                attendu = complementaires.get(champ)
            if getattr(document, champ) != attendu:
                return False
        return True

    def _metadonnees(self, entree: DocumentUploadInput) -> dict[str, object] | None:
        """Métadonnées de la fiche : celles fournies, plus les traces d'origine.

        Les métadonnées apportées par l'appelant (provenance d'un média importé
        du web, par exemple) sont conservées telles quelles ; les clés de trace
        (agent, compte demandeur) sont ajoutées **après**, donc jamais écrasées
        par une valeur d'appelant.
        """
        meta: dict[str, object] = dict(entree.doc_metadata or {})
        if entree.proposed_by_agent:
            meta[_META_KEY] = entree.proposed_by_agent
        if not self._perimetre.est_complet and self._perimetre.utilisateur_id is not None:
            # Créé sous périmètre restreint : le compte garde la vue sur son
            # document, ancre ou pas (voir ``document_dans_perimetre``).
            meta.setdefault(_META_GENERE_POUR, str(self._perimetre.utilisateur_id))
        return meta or None

    def _proposed_by_agent(self, document: Document) -> str | None:
        if not document.doc_metadata:
            return None
        agent = document.doc_metadata.get(_META_KEY)
        return str(agent) if agent else None

    def _resoudre_ancre(self, entree: DocumentUploadInput) -> DocumentAnchor:
        """Détermine l'ancre : réelle si elle existe, sinon le dossier du compte."""
        fournies = [
            (champ, valeur)
            for champ, _scope in _ANCHORS.items()
            if (valeur := getattr(entree, champ)) is not None
        ]
        if len(fournies) > 1:
            msg = (
                "Un document ne peut être rattaché qu'à une seule ancre métier "
                "(organisation, appel, offre, mission, équipe ou session)"
            )
            raise ValidationError(msg, details={"ancres_fournies": [c for c, _ in fournies]})

        if len(fournies) == 1:
            champ, entity_id = fournies[0]
            if self._ancre_existe(champ, entity_id):
                return DocumentAnchor(
                    scope=_ANCHORS[champ], champ=champ, entity_id=entity_id
                )
        return DocumentAnchor(scope=_SCOPE_PERSONNEL, champ=_CHAMP_PERSONNEL, entity_id=uuid4())

    def _statut_depot(self, entree: DocumentUploadInput) -> str:
        """Statut de naissance d'un dépôt, selon sa nature.

        - document de **session** ancré (demande 30/09) : pas un cycle d'offre —
          il naît ``genere`` quand l'assistant formateur (ou l'outil de
          génération) le produit, ``importe`` quand un humain le dépose ;
        - **CV et documents d'appel** (règle CARSO 26/10) : pas non plus de
          cycle de décision — un CV ou une pièce d'appel déposée par un
          humain naît ``importe``, puis devient ``archived`` ou ``supprime``
          selon son cycle de vie, jamais ``draft`` ;
        - tout autre document agent naît ``proposed`` (règle 6 [C]) ;
        - un dépôt humain hors session naît ``draft``.

        Le dépôt d'agent d'un CV ou d'une pièce d'appel reste ``proposed`` :
        la règle 6 [C] n'est pas contournée — seule la décision humaine
        officialise un dépôt d'agent.
        """
        if entree.session_id is not None:
            if entree.proposed_by_agent:
                return StatutDocument.GENERE.value
            return StatutDocument.IMPORTE.value
        if not entree.proposed_by_agent and (
            entree.type_document == TypeDocument.CV.value
            or entree.appel_id is not None
        ):
            return StatutDocument.IMPORTE.value
        if entree.proposed_by_agent:
            return StatutDocument.PROPOSED.value
        return StatutDocument.DRAFT.value
