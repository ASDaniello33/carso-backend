"""Routes — base de connaissance (bibliothèque, recherche RAG, embedding).

Contrôleurs minces : validation d'entrée, appel du service dans la transaction
de la requête, projection de réponse. Aucune logique métier, aucun accès disque.

Périmètre d'accès, assumé et documenté :

- **lecture** (liste, fiche, passages, téléchargement, recherche) : tout compte
  authentifié actif — la bibliothèque de CARSO est un savoir commun, comme le
  web consulté par les agents ; les documents métier, eux, restent filtrés par
  périmètre ;
- **gestion** (ajout, correction, retrait, réindexation) : équipe interne et
  administration (``interne``) ;
- **réglages du modèle d'embedding** et réindexation globale : administration.

Sémantique HTTP : ``201`` ouverture d'un ouvrage, ``200`` recherche
(synchrone : les passages sont renvoyés dans la réponse), ``409`` contenu déjà
présent ou index incompatible, ``422`` format/schéma, ``404`` ouvrage ou
fichier absent, ``403`` geste hors périmètre.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile, status
from fastapi.responses import FileResponse

from app.api.deps import AdminUser, CurrentUser, DbSession, get_current_user
from app.api.permissions import InterneUser
from app.api.schemas import (
    ConnaissanceEtatRead,
    ConnaissanceListeRead,
    ConnaissanceMessageRead,
    ConnaissanceMiseAJour,
    ConnaissancePassageRead,
    ConnaissanceRead,
    ConnaissanceRechercheCreate,
    ConnaissanceRechercheRead,
    EmbeddingConfigurationCreate,
    EmbeddingConfigurationEnregistreeRead,
    EmbeddingConfigurationRead,
    EmbeddingTestCreate,
    EmbeddingTestRead,
    ReindexationRead,
)
from app.application.services import ConnaissanceService

router = APIRouter(
    prefix="/connaissances",
    tags=["connaissances"],
    dependencies=[Depends(get_current_user)],
)


def _service(session: DbSession, user: CurrentUser) -> ConnaissanceService:
    """Service sur la session de la requête, auteur = compte authentifié."""
    return ConnaissanceService(session, actor_id=str(user.id))


# --- Lecture (statique avant les chemins paramétrés) -------------------------


@router.get("/etat", response_model=ConnaissanceEtatRead)
def etat(session: DbSession, user: CurrentUser) -> ConnaissanceEtatRead:
    """Volumétrie, état de l'index et modèle d'embedding appliqué."""
    return ConnaissanceEtatRead(**_service(session, user).etat())


@router.get("", response_model=ConnaissanceListeRead)
def lister(
    session: DbSession,
    user: CurrentUser,
    recherche: Annotated[str | None, Query(max_length=255)] = None,
    categorie: Annotated[str | None, Query(max_length=100)] = None,
    statut: Annotated[str | None, Query(max_length=30)] = None,
    langue: Annotated[str | None, Query(max_length=10)] = None,
    limite: Annotated[int, Query(ge=1, le=1000)] = 500,
) -> ConnaissanceListeRead:
    """Bibliothèque filtrée, du plus récent au plus ancien."""
    documents = _service(session, user).lister(
        recherche=recherche,
        categorie=categorie,
        statut=statut,
        langue=langue,
        limite=limite,
    )
    return ConnaissanceListeRead(
        elements=[ConnaissanceRead.model_validate(document) for document in documents],
        total=len(documents),
    )


@router.post("/recherche", response_model=ConnaissanceRechercheRead)
def rechercher(
    entree: ConnaissanceRechercheCreate, session: DbSession, user: CurrentUser
) -> ConnaissanceRechercheRead:
    """Recherche sémantique — exactement ce que voit un agent.

    Sert d'essai de réglage : la question, les passages et la similarité sont
    ceux que ``search_knowledge_base`` renvoie au modèle.
    """
    resultat = _service(session, user).rechercher(
        entree.question,
        limite=entree.limite,
        document_id=entree.document_id,
        categorie=entree.categorie,
    )
    return ConnaissanceRechercheRead(**resultat)


# --- Configuration d'embedding (administration) ------------------------------


@router.get("/configuration", response_model=EmbeddingConfigurationRead)
def configuration(session: DbSession, user: AdminUser) -> EmbeddingConfigurationRead:
    """Modèle appliqué (administration prioritaire, sinon ``.env``)."""
    effective = _service(session, user).configuration_effective()
    return EmbeddingConfigurationRead(**effective.resume())


@router.post("/configuration/test", response_model=EmbeddingTestRead)
def tester_configuration(
    entree: EmbeddingTestCreate, session: DbSession, user: AdminUser
) -> EmbeddingTestRead:
    """Exécute un **vrai** appel d'embedding et rend la dimension mesurée."""
    return EmbeddingTestRead(
        **_service(session, user).tester_configuration(
            provider=entree.provider,
            model=entree.model,
            base_url=entree.base_url,
            api_key=entree.api_key,
        )
    )


@router.put("/configuration", response_model=EmbeddingConfigurationEnregistreeRead)
def enregistrer_configuration(
    entree: EmbeddingConfigurationCreate, session: DbSession, user: AdminUser
) -> EmbeddingConfigurationEnregistreeRead:
    """Enregistre le modèle après validation réelle (réindexation si besoin)."""
    ligne, reindexation = _service(session, user).configurer(
        provider=entree.provider,
        model=entree.model,
        base_url=entree.base_url,
        api_key=entree.api_key,
        conserver_cle=entree.conserver_cle,
        modifie_par=str(user.id),
    )
    message = (
        "Modèle enregistré. La bibliothèque est réindexée en tâche de fond "
        "(les vecteurs de l'ancien modèle n'étaient pas comparables)."
        if reindexation
        else "Modèle enregistré : l'index existant reste valable."
    )
    effective = _service(session, user).configuration_effective()
    return EmbeddingConfigurationEnregistreeRead(
        configuration=EmbeddingConfigurationRead(**effective.resume()),
        reindexation_requise=reindexation,
        message=message,
    )


@router.delete("/configuration", response_model=ConnaissanceMessageRead)
def desactiver_configuration(
    session: DbSession, user: AdminUser
) -> ConnaissanceMessageRead:
    """Revient au modèle du ``.env`` (décision tracée, réindexée si besoin)."""
    desactive = _service(session, user).desactiver_configuration()
    return ConnaissanceMessageRead(
        message=(
            "Modèle d'administration retiré : le modèle du .env s'applique."
            if desactive
            else "Aucun modèle d'administration n'était enregistré."
        )
    )


@router.post("/reindexer", response_model=ReindexationRead)
def reindexer_tout(session: DbSession, user: AdminUser) -> ReindexationRead:
    """Replanifie l'indexation de toute la bibliothèque (admin)."""
    total = _service(session, user).reindexer_tout()
    return ReindexationRead(
        ouvrages=total,
        message=f"{total} ouvrage(s) replanifié(s) — l'indexation continue en tâche de fond.",
    )


# --- Gestion de la bibliothèque ---------------------------------------------


@router.post("", response_model=ConnaissanceRead, status_code=status.HTTP_201_CREATED)
def ajouter(
    session: DbSession,
    user: InterneUser,
    file: Annotated[UploadFile, File(description="Fichier de l'ouvrage")],
    titre: Annotated[str, Form(description="Titre de l'ouvrage")],
    auteur: Annotated[str | None, Form()] = None,
    editeur: Annotated[str | None, Form()] = None,
    annee: Annotated[int | None, Form()] = None,
    categorie: Annotated[str | None, Form()] = None,
    langue: Annotated[str, Form()] = "fr",
    description: Annotated[str | None, Form()] = None,
    mots_cles: Annotated[str | None, Form(description="Mots-clés séparés par des virgules")] = None,
) -> ConnaissanceRead:
    """Dépose un ouvrage ; l'indexation continue en tâche de fond."""
    service = ConnaissanceService(session, actor_id=str(user.id))
    document = service.ajouter(
        flux=file.file,
        nom_fichier=file.filename or "",
        titre=titre,
        auteur=auteur,
        editeur=editeur,
        annee=annee,
        categorie=categorie,
        langue=langue,
        description=description,
        mots_cles=[mot.strip() for mot in (mots_cles or "").split(",") if mot.strip()],
        ajoute_par_id=user.id,
    )
    return ConnaissanceRead.model_validate(document)


@router.get("/{document_id}", response_model=ConnaissanceRead)
def obtenir(
    document_id: UUID, session: DbSession, user: CurrentUser
) -> ConnaissanceRead:
    """Fiche complète d'un ouvrage (état d'indexation inclus)."""
    return ConnaissanceRead.model_validate(_service(session, user).obtenir(document_id))


@router.get("/{document_id}/extraits", response_model=list[ConnaissancePassageRead])
def extraits(
    document_id: UUID,
    session: DbSession,
    user: CurrentUser,
    limite: Annotated[int, Query(ge=1, le=50)] = 5,
) -> list[ConnaissancePassageRead]:
    """Premiers passages indexés — aperçu de ce que la recherche peut citer."""
    return [
        ConnaissancePassageRead.model_validate(chunk)
        for chunk in _service(session, user).extraits(document_id, limite=limite)
    ]


@router.get("/{document_id}/fichier")
def telecharger(
    document_id: UUID, session: DbSession, user: CurrentUser
) -> FileResponse:
    """Télécharge le fichier d'origine (le document reste vérifiable)."""
    nom, chemin = _service(session, user).telecharger(document_id)
    return FileResponse(
        path=chemin,
        filename=nom,
        media_type="application/octet-stream",
    )


@router.patch("/{document_id}", response_model=ConnaissanceRead)
def modifier(
    document_id: UUID,
    entree: ConnaissanceMiseAJour,
    session: DbSession,
    user: InterneUser,
) -> ConnaissanceRead:
    """Corrige la fiche (le fichier et les passages restent en place)."""
    document = _service(session, user).modifier(
        document_id,
        titre=entree.titre,
        auteur=entree.auteur,
        editeur=entree.editeur,
        annee=entree.annee,
        categorie=entree.categorie,
        langue=entree.langue,
        description=entree.description,
        mots_cles=entree.mots_cles,
    )
    return ConnaissanceRead.model_validate(document)


@router.delete("/{document_id}", response_model=ConnaissanceMessageRead)
def supprimer(
    document_id: UUID, session: DbSession, user: InterneUser
) -> ConnaissanceMessageRead:
    """Retire un ouvrage (vecteurs, passages, fiche, fichier) — tracé."""
    passages = _service(session, user).supprimer(document_id)
    return ConnaissanceMessageRead(
        message="Ouvrage retiré de la base de connaissance.",
        details={"passages_supprimes": passages},
    )


@router.post("/{document_id}/reindexer", response_model=ConnaissanceRead)
def reindexer(
    document_id: UUID, session: DbSession, user: InterneUser
) -> ConnaissanceRead:
    """Remet un ouvrage en file d'indexation (sans attendre le tour périodique)."""
    return ConnaissanceRead.model_validate(_service(session, user).reindexer(document_id))
