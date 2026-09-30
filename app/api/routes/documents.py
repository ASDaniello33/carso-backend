"""Routes â€” documents (Phase 4, instruction/11).

ContrÃ´leurs minces : validation d'entrÃ©e, appel du service dans la session
transactionnelle de la requÃªte (``Depends(get_session)``), mapping de rÃ©ponse.
Aucune logique mÃ©tier, aucun accÃ¨s disque direct.

SÃ©mantique HTTP :

- ``201`` crÃ©ation d'un document (``draft``) ;
- ``202`` dÃ©pÃ´t d'une **proposition** de version (zone *proposal*, pas officielle) ;
- ``200`` dÃ©cisions humaines (approbation, refus, archivage) ;
- ``409`` conflit d'Ã©tat (version dÃ©jÃ  publiÃ©e, transition interdite) ;
- ``422`` validation (extension interdite, contenu incohÃ©rent, ancre manquante) ;
- ``404`` document, ancre ou fichier absent — **corbeille** :
  ``GET /documents/corbeille`` liste les fiches supprimées avec leur échéance ;
- ``409`` fiche de corbeille non restaurable (version déjà reprise, version
  officielle déjà en place, fichier absent sans redéposition) ;
- ``403`` geste réservé à l'administration (``POST /documents/{id}/purge``).
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Path, Query, UploadFile, status
from fastapi.responses import FileResponse

from app.api.deps import AdminUser, CurrentUser, DbSession, get_current_user
from app.api.routes.suppression import enregistrer_suppression
from app.api.schemas import (
    DocumentCorbeilleRead,
    DocumentDecisionCreate,
    DocumentMetadonneesUpdate,
    DocumentPurgeCreate,
    DocumentRead,
    ExtractionRead,
    RapportPurgeRead,
)
from app.application.dto import (
    DecisionInput,
    DocumentMetadonneesUpdateInput,
    DocumentUploadInput,
    FicheCorbeille,
)
from app.application.services import DocumentService
from app.core.errors import ValidationError
from app.domain.document import Document

router = APIRouter(
    prefix="/documents",
    tags=["documents"],
    dependencies=[Depends(get_current_user)],
)

#: Suppression **logique** (règle du 23/09) : le fichier et ses aperçus quittent le
#: stockage, la fiche reste en base avec le statut « Supprimé », et les dépendants
#: (modèles de documents, supports) sont conservés. Aucun document officiel n'est
#: bloqué : la décision est tracée dans l'audit.
enregistrer_suppression(router, "documents")

_FILTERS = (
    "organisation_id",
    "appel_a_proposition_id",
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
        appel_a_proposition_id=fiche.appel_a_proposition_id,
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
    appel_a_proposition_id: Annotated[UUID | None, Form()] = None,
    offre_id: Annotated[UUID | None, Form()] = None,
    mission_id: Annotated[UUID | None, Form()] = None,
    equipe_id: Annotated[UUID | None, Form()] = None,
    session_id: Annotated[UUID | None, Form()] = None,
) -> Document:
    """DÃ©pose un document rattachÃ© Ã  **une** ancre mÃ©tier (dÃ©termine le dossier)."""
    service = DocumentService(session)
    return service.enregistrer_document(
        _entree(
            file,
            type_document,
            nom,
            str(user.id),
            proposed_by_agent,
            organisation_id=organisation_id,
            appel_a_proposition_id=appel_a_proposition_id,
            offre_id=offre_id,
            mission_id=mission_id,
            equipe_id=equipe_id,
            session_id=session_id,
        )
    )


@router.get("/all", response_model=list[DocumentRead])
def list_all_documents(
    session: DbSession,
    statut: Annotated[
        str | None, Query(description="Filtre facultatif : draft | proposed | approved | archived")
    ] = None,
) -> list[Document]:
    """Tous les documents (gestion générale), du plus récent au plus ancien."""
    return DocumentService(session).lister_tous(statut=statut)


@router.get("", response_model=list[DocumentRead])
def list_documents(
    session: DbSession,
    organisation_id: Annotated[UUID | None, Query()] = None,
    appel_a_proposition_id: Annotated[UUID | None, Query()] = None,
    offre_id: Annotated[UUID | None, Query()] = None,
    mission_id: Annotated[UUID | None, Query()] = None,
    equipe_id: Annotated[UUID | None, Query()] = None,
    session_id: Annotated[UUID | None, Query()] = None,
) -> list[Document]:
    """Documents d'un objet mÃ©tier â€” **exactement un** filtre est requis."""
    filtres = {
        "organisation_id": organisation_id,
        "appel_a_proposition_id": appel_a_proposition_id,
        "offre_id": offre_id,
        "mission_id": mission_id,
        "equipe_id": equipe_id,
        "session_id": session_id,
    }
    fournis = {nom: valeur for nom, valeur in filtres.items() if valeur is not None}
    if len(fournis) != 1:
        msg = "Exactement un filtre mÃ©tier est requis pour lister des documents"
        raise ValidationError(msg, details={"filtres_possibles": list(_FILTERS)})

    service = DocumentService(session)
    nom, valeur = next(iter(fournis.items()))
    return service.lister_pour(nom, valeur)


@router.get("/corbeille", response_model=list[DocumentCorbeilleRead])
def list_corbeille(session: DbSession) -> list[DocumentCorbeilleRead]:
    """Corbeille : fiches supprimées, échéance de conservation, restaurabilité.

    Lecture seule — consulter la corbeille ne modifie rien. Déclarée **avant**
    ``/{document_id}`` : un chemin littéral doit gagner sur un paramètre de
    chemin, sinon « corbeille » serait reçu comme un identifiant (et refusé en
    ``422`` sans jamais atteindre cette route).
    """
    return [_corbeille_reponse(fiche) for fiche in DocumentService(session).lister_corbeille()]


@router.post("/{document_id}/restauration", response_model=DocumentRead)
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
    return DocumentService(session).restaurer_document(
        document_id,
        DecisionInput(decided_by=str(user.id), reason=reason),
        flux=flux,
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
    retires = DocumentService(session).purger_document(
        document_id, DecisionInput(decided_by=str(user.id), reason=payload.reason)
    )
    return RapportPurgeRead(document_id=document_id, fichiers_supprimes=retires)


@router.get("/{document_id}", response_model=DocumentRead)
def get_document(
    document_id: UUID,
    session: DbSession,
) -> Document:
    """MÃ©tadonnÃ©es d'un document (404 si absent)."""
    return DocumentService(session).obtenir(document_id)


@router.patch("/{document_id}", response_model=DocumentRead)
def update_document(
    document_id: UUID,
    payload: DocumentMetadonneesUpdate,
    session: DbSession,
) -> Document:
    """Modifie les métadonnées d'un document — jamais son contenu.

    Nom et type forment la clé de regroupement des versions : ils ne sont
    acceptés que sur un document à version unique (le service refuse sinon). Le
    contenu se remplace par une nouvelle version, jamais en place.
    """
    return DocumentService(session).modifier_metadonnees(
        document_id,
        DocumentMetadonneesUpdateInput(
            nom=payload.nom,
            type_document=payload.type_document,
            doc_metadata=payload.doc_metadata,
        ),
    )


@router.get("/{document_id}/versions", response_model=list[DocumentRead])
def list_versions(
    document_id: UUID,
    session: DbSession,
) -> list[Document]:
    """Historique des versions, la plus rÃ©cente d'abord."""
    return DocumentService(session).versions(document_id)


@router.get("/{document_id}/contenu", response_class=FileResponse)
def download_document(
    document_id: UUID,
    session: DbSession,
) -> FileResponse:
    """TÃ©lÃ©charge le fichier d'une version (nom de fichier assaini)."""
    nom_fichier, chemin = DocumentService(session).telecharger(document_id)
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
) -> FileResponse:
    """Sert l'image d'une page d'aperçu (PNG) — jamais un chemin disque.

    L'aperçu est produit par ``render_document`` (outil d'agent) ou par le
    service documentaire : le navigateur ne demande qu'un couple
    ``document``/``page``, le chemin est reconstruit côté serveur.
    """
    nom_fichier, chemin = DocumentService(session).chemin_page_apercu(document_id, page)
    return FileResponse(path=chemin, filename=nom_fichier, media_type="image/png")


@router.get("/{document_id}/extraction", response_model=ExtractionRead)
def extract_document(
    document_id: UUID,
    session: DbSession,
) -> ExtractionRead:
    """Extrait le texte (PDF/DOCX/XLSX/texte) â€” **lecture seule**, rien n'est persistÃ©.

    Point d'entrÃ©e destinÃ© aux agents : ils reÃ§oivent du texte, jamais un chemin
    de fichier.
    """
    extrait = DocumentService(session).extraire_texte(document_id)
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
) -> Document:
    """DÃ©pose une nouvelle version â€” **l'ancienne n'est jamais Ã©crasÃ©e**."""
    service = DocumentService(session)
    return service.remplacer_document(
        document_id,
        _entree(file, None, nom, str(user.id), proposed_by_agent),
    )


@router.post("/{document_id}/soumission", response_model=DocumentRead)
def submit_document(
    document_id: UUID,
    session: DbSession,
) -> Document:
    """``draft`` â†’ ``proposed`` : soumet la version Ã  dÃ©cision humaine."""
    return DocumentService(session).soumettre_document(document_id)


@router.post("/{document_id}/approbation", response_model=DocumentRead)
def approve_document(
    document_id: UUID,
    payload: DocumentDecisionCreate,
    user: CurrentUser,
    session: DbSession,
) -> Document:
    """DÃ©cision humaine : la version devient officielle, les prÃ©cÃ©dentes sont archivÃ©es."""
    return DocumentService(session).approuver_document(
        document_id, DecisionInput(decided_by=str(user.id), reason=payload.reason)
    )


@router.post("/{document_id}/refus", response_model=DocumentRead)
def refuse_document(
    document_id: UUID,
    payload: DocumentDecisionCreate,
    user: CurrentUser,
    session: DbSession,
) -> Document:
    """DÃ©cision humaine dÃ©favorable sur une version proposÃ©e (archivÃ©e, jamais supprimÃ©e)."""
    return DocumentService(session).refuser_document(
        document_id, DecisionInput(decided_by=str(user.id), reason=payload.reason)
    )


@router.post("/{document_id}/archivage", response_model=DocumentRead)
def archive_document(
    document_id: UUID,
    payload: DocumentDecisionCreate,
    user: CurrentUser,
    session: DbSession,
) -> Document:
    """Archive un document (aucune suppression physique)."""
    return DocumentService(session).archiver_document(
        document_id, DecisionInput(decided_by=str(user.id), reason=payload.reason)
    )




