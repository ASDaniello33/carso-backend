"""Métadonnées modifiables, visibilité et listages.

Un document rattaché suit le périmètre de son objet ; un document sans
objet appartient à son propriétaire et devient visible de tous les
comptes actifs s'il est déclaré public.
"""

from __future__ import annotations

from uuid import UUID

from app.application.dto import DocumentMetadonneesUpdateInput
from app.application.services.document_service_commun import (
    _ENTITY,
    _LISTERS,
    _VISIBILITE_PRIVEE,
    _VISIBILITE_PUBLIQUE,
    _est_administrateur,
)
from app.core.errors import ConflictError, ValidationError
from app.documents.paths import extension_of, sanitize_filename
from app.documents.validation import ALLOWED_EXTENSIONS
from app.domain.document import Document
from app.domain.enums import ActionAudit, ActorType


class VisibiliteDocumentMixin:
    """See ``document_service`` pour le socle partagé."""

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

        Un compte restreint (formateur) corrige **dans son périmètre** : les
        documents de ses missions et de ses sessions. L'accessibilité est
        appliquée par ``_get_modifiable`` (404 hors périmètre) ; les décisions de
        cycle de vie (approuver, refuser, archiver) restent internes.

        Raises:
            NotFoundError: document inexistant ou hors périmètre.
            ConflictError: renommage d'un document déjà versionné.
            ValidationError: extension interdite, type inconnu, aucune modification.
        """
        document = self._get_modifiable(document_id)
        modifie = False

        nouveau_nom = entree.nom.strip() if entree.nom is not None else None
        nouveau_type = entree.type_document
        if nouveau_nom is not None and nouveau_nom != document.nom:
            self._refuser_renommage_copie_modele(document, nouveau_nom)
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

    def changer_visibilite(self, document_id: UUID, visibilite: str) -> Document:
        """Bascule un document sans ancre entre ``prive`` et ``public`` (ADR 0013).

        Réservé au **propriétaire** du document et à l'**administration** : la
        visibilité d'un document personnel n'est la décision de personne
        d'autre. Sur un document **ancré**, l'indicateur n'a aucun effet — le
        document suit le périmètre de son ancre — l'opération est donc refusée
        plutôt que d'écrire une donnée qui ne ferait rien.

        Raises:
            NotFoundError: document inexistant ou hors périmètre.
            PermissionDeniedError: compte ni propriétaire ni administrateur.
            ValidationError: valeur hors vocabulaire, ou document ancré.
        """
        from app.core.errors import PermissionDeniedError

        if visibilite not in {_VISIBILITE_PRIVEE, _VISIBILITE_PUBLIQUE}:
            raise ValidationError(
                f"Visibilité inconnue : {visibilite!r}",
                details={
                    "visibilites_valides": sorted(
                        {_VISIBILITE_PRIVEE, _VISIBILITE_PUBLIQUE}
                    )
                },
            )
        document = self._get_modifiable(document_id)
        if document.mission_id is not None or document.session_id is not None or (
            document.equipe_id is not None
        ) or document.organisation_id is not None or (
            document.appel_id is not None
        ) or document.offre_id is not None:
            raise ValidationError(
                "Ce document est rattaché à un objet métier : sa visibilité "
                "suit le périmètre de cet objet, elle ne se règle pas ici.",
                details={"document_id": str(document_id)},
            )
        est_proprietaire = (
            self._perimetre.utilisateur_id is not None
            and document.proprietaire_id == self._perimetre.utilisateur_id
        )
        if not est_proprietaire and not _est_administrateur(self._perimetre):
            raise PermissionDeniedError(
                "Seuls le propriétaire du document et les administrateurs "
                "peuvent changer sa visibilité.",
                details={
                    "document_id": str(document_id),
                    "role": self._perimetre.role,
                },
            )
        if document.visibilite == visibilite:
            return document  # rien à changer : ni écriture, ni trace

        avant = document.visibilite
        document.visibilite = visibilite
        self._session.flush()
        self._trace.record_event(
            actor_type=ActorType.HUMAIN.value,
            action=ActionAudit.DOCUMENT_VISIBILITE_MODIFIEE.value,
            entity_type=_ENTITY,
            entity_id=document.id,
            before={"visibilite": avant},
            after={"visibilite": visibilite},
        )
        return document

    def lister_pour(self, filtre: str, entity_id: UUID) -> list[Document]:
        """Documents rattachés à un objet métier (un filtre à la fois).

        Sous périmètre restreint, seules les ancres autorisées répondent : une
        mission/session affectée ou sa propre fiche. Les autres ancres
        (organisation, appel, offre) restent des listes de l'équipe interne.
        """
        methode = _LISTERS.get(filtre)
        if methode is None:
            msg = f"Filtre de listing documentaire inconnu : {filtre!r}"
            raise ValidationError(msg, details={"filtres_possibles": sorted(_LISTERS)})
        self._exiger_ancre_autorisee(filtre, entity_id)
        return list(getattr(self.documents, methode)(entity_id))

    def lister_personnels(self) -> list[Document]:
        """Documents personnels du compte connecté (dossier privé).

        C'est la lecture de « Mon dossier », et la **seule** : l'écran n'a pas à
        recalculer la règle, sinon deux définitions finissent par diverger — le
        défaut déjà constaté quand l'écran filtrait sans le propriétaire.

        Raises:
            PermissionDeniedError: appel sans compte identifié — un dossier
                personnel sans personne n'existe pas (scripts, balayage).
        """
        from app.core.errors import PermissionDeniedError

        compte = self._perimetre.utilisateur_id
        if compte is None:
            raise PermissionDeniedError(
                "Les documents personnels appartiennent à un compte identifié.",
            )
        return self.documents.list_personnels_de(compte)

    def lister_tous(self, statut: str | None = None) -> list[Document]:
        """Tous les documents (gestion générale), filtrables par statut.

        Le filtre d'accessibilité s'applique à **tous** les comptes (ADR 0013) :
        un collaborateur ne voit pas les documents personnels d'un autre
        compte — seuls le propriétaire et l'administration les couvrent.
        """
        documents = self.documents.list_all(statut=statut)
        return [document for document in documents if self._est_accessible(document)]

    def _refuser_renommage_copie_modele(self, document: Document, cible: str) -> None:
        """Une copie de travail de modèle garde son nom déterministe.

        La chaîne modèle (INC-129) retrouve la copie par
        ``modele-<référence>.docx`` sur l'objet : la renommer la rend
        introuvable pour ``remplir_modele``, ``remplacer_element_model`` et
        ``obtenir_structure_document``, et l'agent n'a plus aucun moyen
        d'éditer le document qu'il vient de copier. Le refus est porté par le
        service — l'interdiction dans la description de l'outil ne suffit pas.

        Raises:
            ValidationError: la cible ne respecte pas le nom déterministe.
        """
        if (document.doc_metadata or {}).get("role") != "copie_modele":
            return
        propre = sanitize_filename(cible)
        if propre == document.nom:
            return
        # Remettre le nom déterministe reste permis : c'est la réparation d'une
        # copie déjà renommée, et c'est un état que l'édition sait retrouver.
        if propre.startswith("modele-") and propre.endswith(".docx"):
            return
        raise ValidationError(
            "La copie de travail d'un modèle ne peut pas être renommée : "
            "l'édition la retrouve par son nom déterministe "
            "« modele-<référence>.docx ». Renommer le livrable final après "
            "avoir terminé le contenu, jamais la copie.",
            details={
                "document_id": str(document.id),
                "nom_actuel": document.nom,
                "cible": cible,
            },
        )

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
