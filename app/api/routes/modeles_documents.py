"""Routes—registre des modèles de documents par organisation cliente (instruction/06 §7).

Contrôleurs minces sur ``ModeleDocumentService``. La résolution du modèle actif
échoue explicitement (404) lorsqu'aucun modèle n'est enregistré—aucun modèle
par défaut n'est appliqué en silence.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from app.api.deps import CurrentUser, DbSession, get_current_user
from app.api.permissions import exiger_interne
from app.api.schemas import (
    ModeleConfigurationInput,
    ModeleDocumentCreate,
    ModeleDocumentDetailRead,
    ModeleDocumentRead,
    ModeleGenerationInput,
    ModeleGenerationRead,
)
from app.application.services import ModeleDocumentService
from app.domain.execution import ModeleDocument

router = APIRouter(
    prefix="/organisations/{organisation_id}/modeles-documents",
    tags=["modeles-documents"],
    # Gabarits d'entreprise : enregistrer un modèle est un geste d'équipe interne.
    dependencies=[Depends(get_current_user), Depends(exiger_interne)],
)


@router.post("", response_model=ModeleDocumentRead, status_code=status.HTTP_201_CREATED)
def create_modele(
    organisation_id: UUID,
    payload: ModeleDocumentCreate,
    session: DbSession,
    user: CurrentUser,
) -> ModeleDocument:
    """Enregistre une nouvelle version de modèle pour (organisation, type)."""
    return ModeleDocumentService(session, actor_id=str(user.id)).enregistrer_modele(
        organisation_id,
        nom=payload.nom,
        type_document=payload.type_document,
        document_template_id=payload.document_template_id,
        registered_by=str(user.id),
    )


@router.get("", response_model=list[ModeleDocumentRead])
def list_modeles(
    organisation_id: UUID,
    session: DbSession,
) -> list[ModeleDocument]:
    """Modèles enregistrés pour une organisation (toutes versions)."""
    return ModeleDocumentService(session).lister(organisation_id)


@router.get("/actif", response_model=ModeleDocumentRead)
def get_modele_actif(
    organisation_id: UUID,
    type_document: Annotated[str, Query(description="Type de document ciblé")],
    session: DbSession,
) -> ModeleDocument:
    """Modèle de plus haute version pour ce type — 404 si aucun n'est enregistré."""
    return ModeleDocumentService(session).resoudre_modele_actif(
        organisation_id, type_document
    )


# --- Moteur documentaire (INC-128) -------------------------------------------
#
# Le modèle est toujours cherché **sous son organisation** : le service compare
# ``modele.organisation_id`` et répond « introuvable » sinon. Un identifiant
# fourni par le client n'est jamais cru sur parole.


def _service(session: DbSession, user: CurrentUser):
    from app.application.services.moteur_documentaire_service import (
        MoteurDocumentaireService,
    )

    return MoteurDocumentaireService(session, actor_id=str(user.id))


@router.get("/{modele_id}", response_model=ModeleDocumentDetailRead)
def get_modele(
    organisation_id: UUID,
    modele_id: UUID,
    session: DbSession,
    user: CurrentUser,
) -> ModeleDocument:
    """Modèle, son analyse et sa configuration (lecture d'un seul modèle)."""
    return _service(session, user).obtenir_modele(organisation_id, modele_id)


@router.post("/{modele_id}/analyse", response_model=ModeleDocumentDetailRead)
def analyser_modele(
    organisation_id: UUID,
    modele_id: UUID,
    session: DbSession,
    user: CurrentUser,
    proposer: Annotated[
        bool, Query(description="Proposer une classification par l'IA (brouillon)")
    ] = False,
) -> ModeleDocument:
    """Analyse structurelle du DOCX du modèle — remet la validation à zéro."""
    return _service(session, user).analyser(organisation_id, modele_id, proposer=proposer)


@router.put("/{modele_id}/configuration", response_model=ModeleDocumentDetailRead)
def enregistrer_configuration(
    organisation_id: UUID,
    modele_id: UUID,
    payload: ModeleConfigurationInput,
    session: DbSession,
    user: CurrentUser,
) -> ModeleDocument:
    """Enregistre une configuration **brouillon** (validation humaine ensuite)."""
    from app.documents.moteur import schemas as moteur

    configuration = moteur.ConfigurationModele(**payload.model_dump())
    return _service(session, user).enregistrer_configuration(
        organisation_id, modele_id, configuration
    )


@router.post("/{modele_id}/validation", response_model=ModeleDocumentDetailRead)
def valider_modele(
    organisation_id: UUID,
    modele_id: UUID,
    session: DbSession,
    user: CurrentUser,
) -> ModeleDocument:
    """Valide la configuration : instrumente une copie et la contrôle.

    Aucun document n'est rattaché au modèle si la copie instrumentée est
    invalide ; l'erreur porte le détail du contrôle.
    """
    modele, _rapport = _service(session, user).valider(organisation_id, modele_id)
    return modele


@router.post("/{modele_id}/generation", response_model=ModeleGenerationRead)
def generer_depuis_modele(
    organisation_id: UUID,
    modele_id: UUID,
    payload: ModeleGenerationInput,
    session: DbSession,
    user: CurrentUser,
) -> ModeleGenerationRead:
    """Génère un livrable depuis le modèle validé et le dépose en ``proposed``."""
    document, rendu, rapport = _service(session, user).generer(
        organisation_id,
        modele_id,
        payload.valeurs,
        nom_fichier=payload.nom_fichier,
        type_document=payload.type_document,
        propose_par=payload.propose_par or str(user.id),
        appel_id=payload.appel_id,
        offre_id=payload.offre_id,
    )
    return ModeleGenerationRead(
        document_id=document.id,
        nom=document.nom,
        statut=document.statut,
        version=document.version,
        champs_remplaces=rendu.champs_remplaces,
        champs_conserves=rendu.champs_conserves,
        champs_supprimes=rendu.champs_supprimes,
        lignes=rendu.lignes,
        blocs_conserves=rendu.blocs_conserves,
        avertissements=rendu.avertissements,
        validation_ok=rapport.ok,
        validation_visuelle=rapport.visuel,
        validation_erreurs=rapport.erreurs,
    )
