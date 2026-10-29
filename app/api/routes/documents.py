"""Routes â€” documents (Phase 4, instruction/11).

ContrÃ´leurs minces—validation d'entrÃ©e, appel du service dans la session
transactionnelle de la requÃªte (``Depends(get_session)``), mapping de rÃ©ponse.
Aucune logique mÃ©tier, aucun accÃ¨s disque direct.

SÃ©mantique HTTP :

- ``201`` crÃ©ation d'un document (``draft``) ;
- ``202`` dÃ©pÃ´t d'une **proposition** de version (zone *proposal*, pas officielle) ;
- ``200`` dÃ©cisions humaines (approbation, refus, archivage) ;
- ``409`` conflit d'Ã©tat (version dÃ©jÃ  publiÃ©e, transition interdite) ;
- ``422`` validation (extension interdite, contenu incohÃ©rent, ancre manquante) ;
- ``404`` document, ancre ou fichier absent—**corbeille** :
  ``GET /documents/corbeille`` liste les fiches supprimées avec leur échéance ;
- ``409`` fiche de corbeille non restaurable (version déjà reprise, version
  officielle déjà en place, fichier absent sans redéposition) ;
- ``403`` geste réservé à l'administration (``POST /documents/{id}/purge``).
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Path, Query, UploadFile, status
from fastapi.responses import FileResponse, StreamingResponse

from app.api.deps import AdminUser, CurrentUser, DbSession, get_current_user
from app.api.permissions import PerimetreDep, exiger_interne
from app.api.routes.suppression import enregistrer_suppression
from app.api.schemas import (
    DocumentCorbeilleRead,
    DocumentDecisionCreate,
    DocumentImportMediaCreate,
    DocumentMetadonneesUpdate,
    DocumentVisibiliteUpdate,
    DocumentPurgeCreate,
    DocumentRead,
    ExtractionRead,
    FicheNonPurgeeRead,
    PisteFluxRead,
    RapportPurgeCorbeilleRead,
    RapportPurgeRead,
    ResolutionFluxRead,
    VideoFluxRead,
)
from app.application.dto import (
    DecisionInput,
    DocumentMetadonneesUpdateInput,
    DocumentUploadInput,
    FicheCorbeille,
    ImportMediaInput,
)
from app.application.services import DocumentService
from app.application.services.import_media_service import ImportMediaService
from app.application.services.video_web_service import RelaisVideoService, VideoWebService
from app.core.errors import ValidationError
from app.documents.relais_video import CibleRelais, entete_disposition
from app.documents.video_web import PisteFlux, ResolutionFlux
from app.domain.document import Document

router = APIRouter(
    prefix="/documents",
    tags=["documents"],
    dependencies=[Depends(get_current_user)],
)

#: Téléchargement d'un fichier relayé (``/documents/media/fichier``) : route
#: **sans** la garde d'authentification du router principal. Un ``<a href>`` du
#: navigateur ne peut pas porter de ``Bearer`` : l'autorisation est le **jeton
#: signé** fabriqué par ``/documents/media/flux`` (même logique que le retour
#: OAuth des connecteurs MCP, incréments 47 et 57). Un router séparé est ce qui
#: rend ce clic possible — sur le router principal, le navigateur recevrait 401.
router_relais = APIRouter(prefix="/documents", tags=["documents"])

#: Suppression **logique** (règle du 23/09) : le fichier et ses aperçus quittent le
#: stockage, la fiche reste en base avec le statut « Supprimé », et les dépendants
#: (modèles de documents, supports) sont conservés. Aucun document officiel n'est
#: bloqué : la décision est tracée dans l'audit.
#:
#: Ouverte **au périmètre** (correctif 03/10) : un formateur retire ses propres
#: documents et ceux de ses missions/sessions — la suppression reste logique,
#: donc réversible par l'équipe interne. Hors périmètre, la fiche est
#: introuvable (404).
enregistrer_suppression(router, "documents", perimetre=True)

_FILTERS = (
    "organisation_id",
    "appel_id",
    "offre_id",
    "mission_id",
    "equipe_id",
    "session_id",
)


def _corbeille_reponse(fiche: FicheCorbeille) -> DocumentCorbeilleRead:
    """Projection d'une fiche de corbeille en schéma d'API (aucun calcul ici)."""
    return DocumentCorbeilleRead(
        document_id=fiche.document_id,
        nom=fiche.nom,
        type_document=fiche.type_document,
        version=fiche.version,
        statut_avant=fiche.statut_avant,
        supprime_le=fiche.supprime_le,
        supprime_par=fiche.supprime_par,
        motif=fiche.motif,
        expire_le=fiche.expire_le,
        expiree=fiche.expiree,
        jours_restants=fiche.jours_restants,
        retention_jours=fiche.retention_jours,
        fichier_present=fiche.fichier_present,
        restaurable=fiche.restaurable,
        blocage=fiche.blocage,
        organisation_id=fiche.organisation_id,
        appel_id=fiche.appel_id,
        offre_id=fiche.offre_id,
        mission_id=fiche.mission_id,
        equipe_id=fiche.equipe_id,
        session_id=fiche.session_id,
    )


def _entree(
    file: UploadFile,
    type_document: str | None,
    nom: str | None,
    created_by: str | None,
    proposed_by_agent: str | None,
    **ancres: UUID | None,
) -> DocumentUploadInput:
    """Construit le contrat de dÃ©pÃ´t depuis le formulaire multipart."""
    return DocumentUploadInput(
        type_document=type_document or "",
        nom=nom or file.filename or "",
        stream=file.file,
        created_by=created_by,
        proposed_by_agent=proposed_by_agent,
        **ancres,
    )


@router.post("", response_model=DocumentRead, status_code=status.HTTP_201_CREATED)
def upload_document(
    file: Annotated[UploadFile, File(description="Fichier documentaire")],
    type_document: Annotated[str, Form(description="Type de document CARSO")],
    session: DbSession,
    user: CurrentUser,
    nom: Annotated[str | None, Form()] = None,
    proposed_by_agent: Annotated[str | None, Form()] = None,
    organisation_id: Annotated[UUID | None, Form()] = None,
    appel_id: Annotated[UUID | None, Form()] = None,
    offre_id: Annotated[UUID | None, Form()] = None,
    mission_id: Annotated[UUID | None, Form()] = None,
    equipe_id: Annotated[UUID | None, Form()] = None,
    session_id: Annotated[UUID | None, Form()] = None,
    perimetre: PerimetreDep = None,  # type: ignore[assignment]
) -> Document:
    """Dépose un document rattaché à **une** ancre métier (détermine le dossier).

    Un formateur ne dépose que sur une ancre de son périmètre (sa mission, sa
    session, sa fiche) — le service refuse le reste (404).
    """
    service = DocumentService(session, actor_id=str(user.id), perimetre=perimetre)
    return service.enregistrer_document(
        _entree(
            file,
            type_document,
            nom,
            str(user.id),
            proposed_by_agent,
            organisation_id=organisation_id,
            appel_id=appel_id,
            offre_id=offre_id,
            mission_id=mission_id,
            equipe_id=equipe_id,
            session_id=session_id,
        )
    )


@router.post(
    "/import-media",
    response_model=DocumentRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(exiger_interne)],
)
def import_media(
    payload: DocumentImportMediaCreate,
    session: DbSession,
    user: CurrentUser,
    perimetre: PerimetreDep = None,  # type: ignore[assignment]
) -> Document:
    """Rapatrie une **image** du web et l'enregistre comme document CARSO.

    Geste **humain** : l'utilisateur a vu l'image dans un écran de lecture et
    décide de la garder. Aucun agent ne déclenche cet appel — le modèle ne
    choisit donc jamais l'adresse qui sera appelée (SSRF).

    **Les vidéos ne sont jamais rapatriées** (correctif de spécification du
    08/10/2026) : elles se téléchargent depuis la source vers le disque de
    l'utilisateur. Une adresse qui sert une vidéo est refusée ici ; un fichier
    vidéo à conserver se dépose par le dépôt documentaire ordinaire.

    L'image est rapatriée sous garde-fous (hôte public résolu, redirections
    revalidées, taille bornée, contenu reconnu à sa signature), puis déposée par
    le service documentaire : chemin logique, checksum SHA-256, taille, MIME,
    version, statut et auteur sont renseignés comme pour tout autre dépôt. Sa
    provenance (URL d'origine et finale) est conservée dans ses métadonnées.

    Sémantique : ``201`` document créé (``draft``, à valider par la chaîne de
    décision existante) ; ``422`` adresse refusée, hôte non public, image trop
    volumineuse, réponse du site en erreur, contenu qui n'est pas une image
    reconnue (vidéo comprise), ou plusieurs ancres fournies ; ``403`` hors équipe
    interne.

    **Rattachement** : au plus une ancre métier (organisation, appel, offre,
    mission, équipe, session). Fournie, elle range le média dans le dossier
    métier et le rend visible aux comptes de son périmètre. Absente, le média
    entre ``non_classe`` sous ``generated/{id}/`` — l'ancre n'est jamais devinée
    à partir de l'URL.

    Réservé à l'**équipe interne** : le mode sans ancre reste possible, et un
    document sans ancre n'est visible d'aucun compte restreint — un formateur ne
    verrait donc rien de ce qu'il vient d'importer dans ce mode. Une vidéo, elle,
    ne passe jamais par ici : elle se télécharge depuis la source sur le poste. Le geste
    appartient à la gestion documentaire générale (mêmes règles que
    ``/documents/all`` et la corbeille) ; l'ouvrir aux formateurs demanderait
    d'abord de les contraindre à une ancre de leur périmètre.
    """
    service = ImportMediaService(session, actor_id=str(user.id), perimetre=perimetre)
    return service.importer(
        ImportMediaInput(
            url=payload.url,
            nom=payload.nom,
            organisation_id=payload.organisation_id,
            appel_id=payload.appel_id,
            offre_id=payload.offre_id,
            mission_id=payload.mission_id,
            equipe_id=payload.equipe_id,
            session_id=payload.session_id,
        )
    )


@router.get(
    "/media/flux",
    response_model=VideoFluxRead,
    dependencies=[Depends(exiger_interne)],
)
def lire_flux_video_web(
    url: Annotated[
        str,
        Query(
            min_length=8,
            max_length=2048,
            description="Adresse http(s) de la page vidéo (YouTube, Vimeo…).",
        ),
    ],
) -> VideoFluxRead:
    """Ce qu'une page vidéo propose, **avec les adresses réelles de ses pistes**.

    Lecture seule : le serveur interroge la page et rend les adresses, **chacune
    signée** pour le relais de téléchargement
    (``GET /documents/media/fichier``). Le fichier finit sur le disque de
    l'utilisateur, mais il n'y a pas de lien direct possible : le CDN des
    plateformes interdit au navigateur de l'enregistrer lui-même (mesuré le
    09/10/2026 — ni CORS pour notre origine, ni ``Content-Disposition``). Rien
    n'est conservé côté CARSO : ni fichier, ni document, ni ligne en base, et
    aucun muxage. C'est aussi pourquoi la route est un ``GET`` : rien n'est
    demandé au serveur, seulement lu.

    Une **entrée par résolution**, de la plus haute à la basse, chacune avec ce
    qu'elle vaut : taille estimée quand la source l'annonce, adresses réelles des
    pistes, et deux vérités qui évitent les fausses promesses —
    ``telechargement_possible`` (faux quand la piste vidéo n'a pas d'adresse de
    fichier : un manifeste HLS/DASH, par exemple) et ``muxage_requis`` (vrai quand
    vidéo et son sont deux fichiers distincts).

    ``jeton_complet`` répond au cas dominant depuis le 09/10 : les plateformes ne
    publient pas de fichier image+son (et leurs liens combinés exigent un PO
    Token anti-bot), et une vidéo muette ne s'ouvre pas toujours avec le lecteur
    du poste. Le relais **assemble** alors les deux pistes en un seul MP4
    lisible, à la volée, sans rien écrire — un clic, un fichier, avec le son.

    ``telechargeable`` au niveau de la réponse est faux quand **aucune**
    résolution n'a d'adresse : l'écran dit alors que la vidéo n'est pas
    téléchargeable, au lieu d'afficher des boutons qui ne mèneraient nulle part.

    Les adresses des plateformes sont **signées et temporaires**
    (``adresse_temporaire``) : elles ne sont jamais présentées comme un lien CARSO
    permanent — CARSO n'héberge aucun de ces octets. Le jeton rendu avec chaque
    piste est signé par CARSO (30 minutes) : c'est lui, et lui seul, qui ouvre le
    relais.

    Sémantique : ``200`` résolutions décrites ; ``422`` adresse refusée (schéma,
    identifiants, hôte non public), extracteur absent, site qui refuse le serveur,
    ou page sans résolution exploitable ; ``403`` hors équipe interne.
    """
    service = VideoWebService()
    flux = service.analyser(url)
    return VideoFluxRead(
        url=flux.url,
        titre=flux.titre,
        auteur=flux.auteur,
        duree_secondes=flux.duree_secondes,
        extracteur=flux.extracteur,
        miniature=flux.miniature,
        resolutions=[
            _resolution_lue(resolution, service, page=flux.url)
            for resolution in flux.resolutions
        ],
        adresse_temporaire=flux.adresse_temporaire,
        telechargeable=flux.telechargeable,
    )


def _piste_lue(
    piste: PisteFlux | None,
    service: VideoWebService,
    *,
    page: str,
    hauteur: int,
    genre: str,
) -> PisteFluxRead | None:
    """Piste du domaine → schéma d'API (``None`` reste ``None``).

    Un fichier réel reçoit son **lien signé** : c'est lui que le bouton de
    téléchargement utilise. Le jeton porte aussi la page, la hauteur et le genre
    de la piste, ce qui permet au relais de **reprendre** le téléchargement si
    l'adresse expire entre l'affichage et le clic. Un manifeste n'en reçoit pas —
    il n'y a rien à enregistrer, donc rien à signer.
    """
    if piste is None:
        return None
    signable = piste.url is not None and piste.telechargeable_directement
    jeton = (
        service.signer_lien(
            CibleRelais(url=str(piste.url), page=page, hauteur=hauteur, piste=genre)
        )
        if signable
        else None
    )
    return PisteFluxRead(
        format_id=piste.format_id,
        url=piste.url,
        extension=piste.extension,
        protocole=piste.protocole,
        vcodec=piste.vcodec,
        acodec=piste.acodec,
        hauteur=piste.hauteur,
        fps=piste.fps,
        debit_kbits=piste.debit_kbits,
        taille_octets=piste.taille_octets,
        contient_video=piste.contient_video,
        contient_audio=piste.contient_audio,
        telechargeable_directement=piste.telechargeable_directement,
        jeton=jeton,
    )


def _resolution_lue(
    resolution: ResolutionFlux, service: VideoWebService, *, page: str
) -> ResolutionFluxRead:
    """Résolution du domaine → schéma d'API, capacités calculées comprises.

    Chaque piste est signée avec **sa** nature (``video``, ``audio``,
    ``combine``) et la hauteur de sa résolution : c'est ce qui rend une reprise
    possible après un refus de la source, sans jamais changer de résolution.
    """
    return ResolutionFluxRead(
        hauteur=resolution.hauteur,
        taille_estimee_octets=resolution.taille_estimee_octets,
        video=_piste_lue(
            resolution.video,
            service,
            page=page,
            hauteur=resolution.hauteur,
            genre="video",
        ),
        audio=_piste_lue(
            resolution.audio,
            service,
            page=page,
            hauteur=resolution.hauteur,
            genre="audio",
        ),
        combine=_piste_lue(
            resolution.combine,
            service,
            page=page,
            hauteur=resolution.hauteur,
            genre="combine",
        ),
        telechargement_possible=resolution.telechargement_possible,
        muxage_requis=resolution.muxage_requis,
        # Le lien qui rend les deux fichiers inutiles : un MP4 vidéo + son,
        # assemblé à la volée par le relais. Absent quand la source n'a pas de
        # quoi l'assembler (pas de son, manifeste, ou ffmpeg absent du serveur).
        jeton_complet=service.signer_complet(resolution, page=page),
    )


@router_relais.get("/media/fichier")
def telecharger_fichier_video(
    jeton: Annotated[
        str,
        Query(
            min_length=16,
            max_length=8192,
            description="Lien signé rendu par /documents/media/flux (valable quelques minutes).",
        ),
    ],
    nom: Annotated[
        str | None,
        Query(max_length=200, description="Nom d'enregistrement proposé (nettoyé par le serveur)."),
    ] = None,
) -> StreamingResponse:
    """Retransmet le fichier d'une résolution — **CARSO n'en conserve aucun octet**.

    Pourquoi ce relais : les plateformes servent leurs fichiers depuis un CDN qui
    interdit au navigateur de les lire (mesuré le 09/10/2026 sur YouTube — pas de
    ``Access-Control-Allow-Origin`` pour notre origine, pas de
    ``Content-Disposition``). Un lien direct ouvrait donc un lecteur au lieu
    d'enregistrer. Ici le serveur reçoit le flux et le retransmet tel quel avec
    ``Content-Disposition: attachment`` : le navigateur enregistre sur le disque
    de l'utilisateur, comme un téléchargement ordinaire.

    Ce que le relais ne fait **jamais** : écrire un fichier, créer un document,
    insérer une ligne, assembler image et son. Il ne garde rien ; il ne peut donc
    pas remplir le stockage de CARSO.

    **Pas d'en-tête d'authentification** : un ``<a href>`` du navigateur ne peut
    pas porter de ``Bearer``. L'autorisation est le **jeton signé** — fabriqué par
    ``/documents/media/flux``, valable quelques minutes et pour une seule adresse.
    Sans jeton valide, aucune adresse n'est acceptée : ce n'est pas un proxy
    ouvert. Une redirection de la source est refusée (elle pourrait viser le
    réseau interne) et l'adresse signée repasse par la garde anti-SSRF.

    Sémantique : ``200`` flux retransmis ; ``422`` jeton absent, invalide, expiré,
    adresse refusée, redirection non suivie, ou source en erreur.

    La réponse est marquée ``Cache-Control: private, no-store`` : un fichier
    signé ne doit rester ni dans un cache intermédiaire, ni dans celui du
    navigateur — la promesse « rien n'est conservé » vaut aussi pour eux.
    """
    flux = RelaisVideoService().ouvrir(jeton, nom=nom)
    entetes = {
        "Content-Disposition": entete_disposition(flux.nom_fichier),
        # Rien ne doit rester nulle part : ni dans un cache partagé, ni dans
        # celui du navigateur. La promesse « CARSO ne conserve rien » vaut
        # aussi pour les intermédiaires.
        "Cache-Control": "private, no-store",
    }
    if flux.taille_octets is not None:
        entetes["Content-Length"] = str(flux.taille_octets)
    return StreamingResponse(flux.octets, media_type=flux.type_mime, headers=entetes)


@router.get("/all", response_model=list[DocumentRead])
def list_all_documents(
    session: DbSession,
    statut: Annotated[
        str | None, Query(description="Filtre facultatif : draft | proposed | approved | archived")
    ] = None,
    perimetre: PerimetreDep = None,  # type: ignore[assignment]
) -> list[Document]:
    """Tous les documents (gestion générale), du plus récent au plus ancien.

    Le filtrage de périmètre appartient au service : un formateur ne voit que
    ses documents et ceux de ses missions/sessions — jamais la collection
    complète (correctif 03/10 : la page Documents ne répond plus 403).
    """
    return DocumentService(session, perimetre=perimetre).lister_tous(statut=statut)


@router.get("", response_model=list[DocumentRead])
def list_documents(
    session: DbSession,
    organisation_id: Annotated[UUID | None, Query()] = None,
    appel_id: Annotated[UUID | None, Query()] = None,
    offre_id: Annotated[UUID | None, Query()] = None,
    mission_id: Annotated[UUID | None, Query()] = None,
    equipe_id: Annotated[UUID | None, Query()] = None,
    session_id: Annotated[UUID | None, Query()] = None,
    perimetre: PerimetreDep = None,  # type: ignore[assignment]
) -> list[Document]:
    """Documents d'un objet métier — **exactement un** filtre est requis.

    Sous périmètre restreint, seules les ancres autorisées répondent (mission /
    session affectée, sa fiche) : le filtrage appartient au service.
    """
    filtres = {
        "organisation_id": organisation_id,
        "appel_id": appel_id,
        "offre_id": offre_id,
        "mission_id": mission_id,
        "equipe_id": equipe_id,
        "session_id": session_id,
    }
    fournis = {nom: valeur for nom, valeur in filtres.items() if valeur is not None}
    if len(fournis) != 1:
        msg = "Exactement un filtre mÃ©tier est requis pour lister des documents"
        raise ValidationError(msg, details={"filtres_possibles": list(_FILTERS)})

    service = DocumentService(session, perimetre=perimetre)
    nom, valeur = next(iter(fournis.items()))
    return service.lister_pour(nom, valeur)


@router.get(
    "/corbeille",
    response_model=list[DocumentCorbeilleRead],
    dependencies=[Depends(exiger_interne)],
)
def list_corbeille(session: DbSession) -> list[DocumentCorbeilleRead]:
    """Corbeille : fiches supprimées, échéance de conservation, restaurabilité.

    Lecture seule — consulter la corbeille ne modifie rien. Déclarée **avant**
    ``/{document_id}`` : un chemin littéral doit gagner sur un paramètre de
    chemin, sinon « corbeille » serait reçu comme un identifiant (et refusé en
    ``422`` sans jamais atteindre cette route).
    """
    return [_corbeille_reponse(fiche) for fiche in DocumentService(session).lister_corbeille()]


@router.post(
    "/{document_id}/restauration",
    response_model=DocumentRead,
    dependencies=[Depends(exiger_interne)],
)
def restore_document(
    document_id: UUID,
    session: DbSession,
    user: CurrentUser,
    file: Annotated[
        UploadFile | None,
        File(description="Fichier redéposé — requis si le stockage ne l'a plus"),
    ] = None,
    reason: Annotated[str | None, Form(description="Motif de la restauration")] = None,
) -> Document:
    """Restaure une fiche supprimée (corbeille) dans son état d'avant suppression.

    Le fichier a quitté le stockage à la suppression : il se **redépose** ici.
    Sans fichier, la restauration n'aboutit que si un fichier occupe déjà
    l'emplacement d'origine ; sinon le service répond ``409`` et le dit.

    Le motif voyage en ``multipart`` (comme le fichier) : la route n'accepte pas
    un corps JSON en plus du fichier — deux formats de requête pour un même geste
    finiraient par diverger.
    """
    flux = file.file if file is not None else None
    return DocumentService(session, actor_id=str(user.id)).restaurer_document(
        document_id,
        DecisionInput(decided_by=str(user.id), reason=reason),
        flux=flux,
    )


@router.post("/corbeille/purge", response_model=RapportPurgeCorbeilleRead)
def purge_corbeille(
    payload: DocumentPurgeCreate,
    user: AdminUser,
    session: DbSession,
) -> RapportPurgeCorbeilleRead:
    """Purge **définitive de toute la corbeille** — administrateurs seulement.

    Déclarée **avant** ``/{document_id}/purge`` : un chemin littéral doit gagner
    sur un paramètre de chemin, sinon « corbeille » serait reçu comme un
    identifiant (``422``) sans jamais atteindre cette route.

    Une fiche encore référencée n'est pas détruite : elle est **ignorée** et
    rendue dans ``ignores`` avec le refus du service. La purge n'échoue donc pas
    en bloc à cause d'une seule fiche bloquée ; elle ne cache pas non plus ce
    qu'elle a laissé. Le motif est obligatoire et chaque fiche garde son
    événement d'audit (nom, version, motif).
    """
    rapport = DocumentService(session, actor_id=str(user.id)).purger_corbeille(
        DecisionInput(decided_by=str(user.id), reason=payload.reason)
    )
    return RapportPurgeCorbeilleRead(
        fiches_purgees=rapport.fiches_purgees,
        fichiers_supprimes=rapport.fichiers_supprimes,
        ignores=[
            FicheNonPurgeeRead(
                document_id=fiche.document_id, nom=fiche.nom, motif=fiche.motif
            )
            for fiche in rapport.ignores
        ],
    )


@router.post("/{document_id}/purge", response_model=RapportPurgeRead)
def purge_document(
    document_id: UUID,
    payload: DocumentPurgeCreate,
    user: AdminUser,
    session: DbSession,
) -> RapportPurgeRead:
    """Purge **définitive** d'une fiche de la corbeille — administrateurs.

    C'est la seule route du système qui efface une ligne documentaire : elle est
    protégée par ``require_admin`` (``403`` pour tout autre rôle), le motif est
    obligatoire, et une fiche encore référencée est refusée avec la liste de ce
    qui la référence — jamais de destruction en cascade d'un dépendant.
    """
    retires = DocumentService(session, actor_id=str(user.id)).purger_document(
        document_id, DecisionInput(decided_by=str(user.id), reason=payload.reason)
    )
    return RapportPurgeRead(document_id=document_id, fichiers_supprimes=retires)


@router.delete("/{document_id}/depot", status_code=status.HTTP_204_NO_CONTENT)
def annuler_depot_document(
    document_id: UUID,
    session: DbSession,
    user: CurrentUser,
    perimetre: PerimetreDep = None,  # type: ignore[assignment]
) -> None:
    """Annule le dépôt d'un document encore vierge — pièce jointe retirée avant envoi.

    Ouvert **au périmètre** comme les autres écritures de documents : le
    déposant lui-même (hors périmètre, 404) et lui seul. Le fichier et la ligne
    partent ensemble, sinon le même nom resterait pris et ne pourrait plus être
    redéposé. Un document engagé dans le cycle (proposé, importé, généré…) ou
    déjà référencé est refusé (409).
    """
    DocumentService(session, actor_id=str(user.id), perimetre=perimetre).annuler_depot(
        document_id, str(user.id)
    )


@router.get("/{document_id}", response_model=DocumentRead)
def get_document(
    document_id: UUID,
    session: DbSession,
    perimetre: PerimetreDep,
) -> Document:
    """Métadonnées d'un document (404 si absent ou hors de son périmètre)."""
    return DocumentService(session, perimetre=perimetre).obtenir(document_id)


@router.patch("/{document_id}", response_model=DocumentRead)
def update_document(
    document_id: UUID,
    payload: DocumentMetadonneesUpdate,
    session: DbSession,
    user: CurrentUser,
    perimetre: PerimetreDep = None,  # type: ignore[assignment]
) -> Document:
    """Modifie les métadonnées d'un document — jamais son contenu.

    Nom et type forment la clé de regroupement des versions : ils ne sont
    acceptés que sur un document à version unique (le service refuse sinon). Le
    contenu se remplace par une nouvelle version, jamais en place.

    Ouvert **au périmètre** (correctif 03/10) : un formateur corrige ses
    documents et ceux de ses missions/sessions ; hors périmètre, 404.
    """
    return DocumentService(
        session, actor_id=str(user.id), perimetre=perimetre
    ).modifier_metadonnees(
        document_id,
        DocumentMetadonneesUpdateInput(
            nom=payload.nom,
            type_document=payload.type_document,
            doc_metadata=payload.doc_metadata,
        ),
    )


@router.patch("/{document_id}/visibilite", response_model=DocumentRead)
def changer_visibilite_document(
    document_id: UUID,
    payload: DocumentVisibiliteUpdate,
    session: DbSession,
    user: CurrentUser,
    perimetre: PerimetreDep,
) -> Document:
    """Bascule un document **sans objet métier** entre privé et public (ADR 0013).

    Réservé au propriétaire du document et à l'administration. Un document
    rattaché à un objet métier suit le périmètre de cet objet : la bascule est
    refusée (422) plutôt que d'écrire un indicateur sans effet.
    """
    return DocumentService(
        session, actor_id=str(user.id), perimetre=perimetre
    ).changer_visibilite(document_id, payload.visibilite)


@router.get("/{document_id}/versions", response_model=list[DocumentRead])
def list_versions(
    document_id: UUID,
    session: DbSession,
    perimetre: PerimetreDep,
) -> list[Document]:
    """Historique des versions, la plus récente d'abord."""
    return DocumentService(session, perimetre=perimetre).versions(document_id)


@router.get("/{document_id}/contenu", response_class=FileResponse)
def download_document(
    document_id: UUID,
    session: DbSession,
    perimetre: PerimetreDep,
) -> FileResponse:
    """Télécharge le fichier d'une version (nom de fichier assaini)."""
    nom_fichier, chemin = DocumentService(session, perimetre=perimetre).telecharger(
        document_id
    )
    return FileResponse(path=chemin, filename=nom_fichier)


@router.get(
    "/{document_id}/apercu/{page}",
    response_class=FileResponse,
    responses={404: {"description": "Aperçu indisponible : cette page n'a pas été rendue"}},
)
def download_preview(
    document_id: UUID,
    page: Annotated[int, Path(ge=1, le=500, description="Numéro de page (1-based)")],
    session: DbSession,
    perimetre: PerimetreDep,
) -> FileResponse:
    """Sert l'image d'une page d'aperçu (PNG) — jamais un chemin disque.

    L'aperçu est produit par ``render_document`` (outil d'agent) ou par le
    service documentaire : le navigateur ne demande qu'un couple
    ``document``/``page``, le chemin est reconstruit côté serveur.
    """
    nom_fichier, chemin = DocumentService(session, perimetre=perimetre).chemin_page_apercu(
        document_id, page
    )
    return FileResponse(path=chemin, filename=nom_fichier, media_type="image/png")


@router.get("/{document_id}/extraction", response_model=ExtractionRead)
def extract_document(
    document_id: UUID,
    session: DbSession,
    perimetre: PerimetreDep,
) -> ExtractionRead:
    """Extrait le texte (PDF/DOCX/XLSX/texte) â€” **lecture seule**, rien n'est persistÃ©.

    Point d'entrÃ©e destinÃ© aux agents : ils reÃ§oivent du texte, jamais un chemin
    de fichier.
    """
    extrait = DocumentService(session, perimetre=perimetre).extraire_texte(document_id)
    return ExtractionRead(
        document_id=extrait.document_id,
        nom=extrait.nom,
        extension=extrait.extension,
        mime_type=extrait.mime_type,
        adaptateur=extrait.adaptateur,
        texte=extrait.texte,
        nb_caracteres=extrait.nb_caracteres,
        nb_pages=extrait.nb_pages,
        nb_feuilles=extrait.nb_feuilles,
        feuilles=list(extrait.feuilles),
        tronque=extrait.tronque,
    )


@router.post(
    "/{document_id}/remplacement",
    response_model=DocumentRead,
    status_code=status.HTTP_202_ACCEPTED,
)
def replace_document(
    document_id: UUID,
    file: Annotated[UploadFile, File(description="Nouvelle version du fichier")],
    session: DbSession,
    user: CurrentUser,
    nom: Annotated[str | None, Form()] = None,
    proposed_by_agent: Annotated[str | None, Form()] = None,
    perimetre: PerimetreDep = None,  # type: ignore[assignment]
) -> Document:
    """Dépose une nouvelle version — **l'ancienne n'est jamais écrasée**.

    Le **nom logique** du document est conservé : remplacer le fichier n'est pas
    un renommage (nom + type forment la clé de regroupement des versions, voir
    ``DocumentService.remplacer_document``). L'interface ne transmet pas de
    ``nom`` : le fichier déposé porte alors le nom physique du nouveau document
    et le service refusait la version (« le nom ne peut pas changer »). Seul un
    ``nom`` **explicite** différent du nom courant exprime une intention de
    renommage, et reste refusé comme tel.
    """
    service = DocumentService(session, actor_id=str(user.id), perimetre=perimetre)
    nom_logique = nom or service.obtenir(document_id).nom
    return service.remplacer_document(
        document_id,
        _entree(file, None, nom_logique, str(user.id), proposed_by_agent),
    )


@router.post("/{document_id}/soumission", response_model=DocumentRead)
def submit_document(
    document_id: UUID,
    session: DbSession,
    perimetre: PerimetreDep,
    utilisateur: CurrentUser,
) -> Document:
    """``draft`` → ``proposed`` : soumet la version à décision humaine.

    Geste opérationnel d'un formateur sur **son** document de session ;
    l'approbation, elle, reste à l'équipe interne.
    """
    return DocumentService(
        session, actor_id=str(utilisateur.id), perimetre=perimetre
    ).soumettre_document(document_id)


@router.post(
    "/{document_id}/approbation",
    response_model=DocumentRead,
    dependencies=[Depends(exiger_interne)],
)
def approve_document(
    document_id: UUID,
    payload: DocumentDecisionCreate,
    user: CurrentUser,
    session: DbSession,
) -> Document:
    """DÃ©cision humaine : la version devient officielle, les prÃ©cÃ©dentes sont archivÃ©es."""
    return DocumentService(session, actor_id=str(user.id)).approuver_document(
        document_id, DecisionInput(decided_by=str(user.id), reason=payload.reason)
    )


@router.post(
    "/{document_id}/refus",
    response_model=DocumentRead,
    dependencies=[Depends(exiger_interne)],
)
def refuse_document(
    document_id: UUID,
    payload: DocumentDecisionCreate,
    user: CurrentUser,
    session: DbSession,
) -> Document:
    """DÃ©cision humaine dÃ©favorable sur une version proposÃ©e (archivÃ©e, jamais supprimÃ©e)."""
    return DocumentService(session, actor_id=str(user.id)).refuser_document(
        document_id, DecisionInput(decided_by=str(user.id), reason=payload.reason)
    )


@router.post(
    "/{document_id}/archivage",
    response_model=DocumentRead,
    dependencies=[Depends(exiger_interne)],
)
def archive_document(
    document_id: UUID,
    payload: DocumentDecisionCreate,
    user: CurrentUser,
    session: DbSession,
) -> Document:
    """Archive un document (aucune suppression physique)."""
    return DocumentService(session, actor_id=str(user.id)).archiver_document(
        document_id, DecisionInput(decided_by=str(user.id), reason=payload.reason)
    )




