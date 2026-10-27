"""Schémas API—documents, extraction et modèles de documents.

Les schémas traduisent les contrats applicatifs (``app/application/dto.py``).
Le fichier lui-même ne transite jamais par un schéma—il arrive en ``multipart``
et repart en ``FileResponse``.
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class DocumentRead(BaseModel):
    """Métadonnées d'un document (le ``storage_path`` exposé est **logique**)."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    nom: str
    type_document: str
    mime_type: str | None = None
    taille_octets: int | None = None
    storage_path: str
    storage_disk: str | None = None
    checksum_sha256: str | None = None
    version: int
    statut: str
    organisation_id: UUID | None = None
    appel_id: UUID | None = None
    offre_id: UUID | None = None
    mission_id: UUID | None = None
    equipe_id: UUID | None = None
    session_id: UUID | None = None
    #: Propriétaire du document (ADR 0013) — compte qui l'a déposé ou fait
    #: générer. Décide seul, avec l'administration, de la visibilité d'un
    #: document **sans objet métier**.
    proprietaire_id: UUID | None = None
    #: ``prive`` (défaut) | ``public`` — n'a d'effet que sans objet métier.
    visibilite: str = "prive"
    created_by: str | None = None
    #: Métadonnées de la fiche (provenance d'un média importé du web, par
    #: exemple). Exposées en lecture : une provenance qu'on ne peut pas relire
    #: n'est pas une provenance.
    doc_metadata: dict[str, Any] | None = None
    created_at: datetime
    updated_at: datetime


class DocumentMetadonneesUpdate(BaseModel):
    """Métadonnées d'un document — jamais son contenu.

    Le contenu se remplace par une nouvelle version (``POST .../versions``).
    ``nom`` et ``type_document`` ne sont acceptés que sur un document à une seule
    version : ils forment la clé de regroupement des versions.
    """

    nom: str | None = Field(default=None, min_length=1, max_length=255)
    type_document: str | None = Field(default=None, min_length=1, max_length=50)
    doc_metadata: dict[str, Any] | None = None


class DocumentImportMediaCreate(BaseModel):
    """Import d'une **image** externe trouvée sur le web.

    Le geste est **humain** : l'adresse vient de l'utilisateur, jamais du modèle.
    Le serveur rapatrie l'image sous garde-fous — hôte public résolu,
    redirections revalidées, taille bornée, contenu reconnu à sa signature — puis
    la dépose comme document (le type et le MIME enregistrés sont ceux du
    contenu réel, pas ceux annoncés par le site distant). Les vidéos ne sont
    jamais rapatriées : elles se téléchargent depuis la source vers le poste.
    """

    url: str = Field(
        min_length=8,
        max_length=2048,
        description="Adresse http(s) de l'image à importer.",
    )
    nom: str | None = Field(
        default=None,
        max_length=255,
        description="Nom souhaité, sans extension (l'extension vient du type réel).",
    )
    #: Ancre métier facultative (une seule) : rattache le média à un objet et le
    #: range dans son dossier. Absente, le média entre ``non_classe``.
    organisation_id: UUID | None = None
    appel_id: UUID | None = None
    offre_id: UUID | None = None
    mission_id: UUID | None = None
    equipe_id: UUID | None = None
    session_id: UUID | None = None


class PisteFluxRead(BaseModel):
    """Une piste telle que le site la sert : vidéo **ou** son, rarement les deux.

    ``telechargeable_directement`` est faux pour un manifeste (HLS, DASH) et pour
    une piste sans adresse : l'interface ne propose un lien que lorsqu'il mène
    réellement à un fichier.

    ``jeton`` est le **lien de téléchargement signé** (``GET
    /documents/media/fichier``) : les plateformes interdisent au navigateur de
    lire leurs fichiers directement, donc c'est CARSO qui les retransmet — en
    flux, sans rien conserver. Le jeton vaut pour cette adresse et quelques
    minutes seulement ; il est absent quand la piste n'est pas un fichier. Pour un
    fichier **complet** (vidéo + son assemblés), voir ``jeton_complet`` sur la
    résolution.
    """

    format_id: str
    url: str | None = None
    extension: str
    protocole: str
    vcodec: str
    acodec: str
    hauteur: int | None = None
    fps: int | None = None
    debit_kbits: float | None = None
    taille_octets: int | None = None
    contient_video: bool
    contient_audio: bool
    telechargeable_directement: bool
    jeton: str | None = None


class ResolutionFluxRead(BaseModel):
    """Une résolution proposée au client, avec les adresses réelles de ses pistes.

    ``taille_estimee_octets`` peut être ``None`` : certains sites n'annoncent
    aucune taille (manifeste). Une taille inconnue n'est jamais présentée comme
    un chiffre — l'interface le dit autrement.

    ``telechargement_possible`` faux ⇒ la vidéo de cette résolution n'a pas
    d'adresse de fichier (manifeste HLS/DASH) : l'écran le dit plutôt que
    d'afficher un bouton qui ne mènerait à rien. ``muxage_requis`` vrai ⇒ la
    source publie vidéo et son en deux fichiers (``video`` + ``audio``).

    ``jeton_complet`` est le lien qui rend ces deux fichiers **inutiles** : le
    relais assemble la vidéo et le son à la volée et sert **un seul** MP4, lisible
    avec le son par le lecteur du poste. Il vaut ``None`` quand l'assemblage n'est
    pas possible (pas de piste son, ou ffmpeg absent du serveur) : l'interface
    retombe alors sur les deux fichiers séparés et le dit, au lieu de promettre un
    fichier complet qu'elle ne peut pas produire.
    """

    hauteur: int
    taille_estimee_octets: int | None = None
    video: PisteFluxRead | None = None
    audio: PisteFluxRead | None = None
    combine: PisteFluxRead | None = None
    telechargement_possible: bool
    muxage_requis: bool
    #: Lien signé d'un MP4 **vidéo + son** (assemblage à la volée), ou ``None``.
    jeton_complet: str | None = None


class VideoFluxRead(BaseModel):
    """Ce qu'une page vidéo propose, avec les adresses — et ce qu'elles ne sont pas.

    ``adresse_temporaire`` est presque toujours vrai : les plateformes signent
    leurs adresses et les font expirer. Rien ici ne les présente comme un lien
    CARSO permanent : CARSO n'héberge aucun de ces octets. Le téléchargement passe
    par le relais signé (``jeton``), qui retransmet le fichier sans le conserver.
    """

    url: str
    titre: str
    auteur: str | None = None
    duree_secondes: int | None = None
    extracteur: str
    miniature: str | None = None
    resolutions: list[ResolutionFluxRead]
    adresse_temporaire: bool
    #: Faux ⇒ aucune résolution n'a d'adresse de fichier : l'écran doit dire que
    #: cette vidéo n'est pas téléchargeable, au lieu d'afficher des boutons vides.
    telechargeable: bool


class DocumentDecisionCreate(BaseModel):
    """Décision humaine. ``decided_by`` vient du JWT."""

    reason: str | None = Field(default=None, max_length=2000)


class DocumentVisibiliteUpdate(BaseModel):
    """Bascule privé ↔ public d'un document **sans objet métier** (ADR 0013).

    Réservé au propriétaire du document et à l'administration : un document
    rattaché à un objet métier suit le périmètre de cet objet, la bascule est
    alors refusée par le service (422).
    """

    model_config = ConfigDict(extra="forbid")

    visibilite: str = Field(pattern="^(prive|public)$")


class DocumentPurgeCreate(BaseModel):
    """Purge définitive d'une fiche de la corbeille (administrateurs).

    ``reason`` est **obligatoire** ici, contrairement aux autres décisions : la
    purge efface la dernière trace du document, un motif vide rendrait l'audit
    muet sur le geste le plus destructeur du système.
    """

    reason: str = Field(min_length=1, max_length=2000)


class DocumentCorbeilleRead(BaseModel):
    """Fiche supprimée telle que la corbeille l'expose (ADR 0006).

    Le fichier n'est plus sur le disque (sauf s'il y est revenu) : ``fichier_present``
    dit s'il faut le redéposer pour restaurer, et ``restaurable`` / ``blocage``
    disent si la restauration est possible — le calcul vient du service, jamais de
    l'interface.
    """

    document_id: UUID
    nom: str
    type_document: str
    version: int
    statut_avant: str
    supprime_le: datetime | None = None
    supprime_par: str | None = None
    motif: str | None = None
    expire_le: datetime | None = None
    expiree: bool
    jours_restants: int | None = None
    retention_jours: int
    fichier_present: bool
    restaurable: bool
    blocage: str | None = None
    #: Ancres conservées : la corbeille se filtre par fiche métier.
    organisation_id: UUID | None = None
    appel_id: UUID | None = None
    offre_id: UUID | None = None
    mission_id: UUID | None = None
    equipe_id: UUID | None = None
    session_id: UUID | None = None


class RapportPurgeRead(BaseModel):
    """Ce qu'une purge définitive a effectivement effacé."""

    document_id: UUID
    fichiers_supprimes: int = 0


class ExtractionRead(BaseModel):
    """Texte extrait d'un document (lecture seule, bornée par la configuration)."""

    document_id: UUID
    nom: str
    extension: str
    mime_type: str | None = None
    adaptateur: str
    texte: str
    nb_caracteres: int
    nb_pages: int | None = None
    nb_feuilles: int | None = None
    feuilles: list[str] = Field(default_factory=list)
    tronque: bool


class ModeleDocumentCreate(BaseModel):
    """Enregistrement d'un modèle de document pour une organisation cliente."""

    nom: str = Field(min_length=1, max_length=255)
    type_document: str = Field(min_length=1, max_length=50)
    document_template_id: UUID


class ModeleDocumentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    organisation_id: UUID | None = None
    nom: str
    type_document: str
    document_template_id: UUID
    version: int
    statut: str | None = None
    created_at: datetime
    updated_at: datetime
