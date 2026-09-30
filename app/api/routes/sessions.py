"""Routes — sessions de formation et participations (Lot C1, instruction/02 §F/§G).

Contrôleurs minces ; les décisions de statut suivent la machine à états
``session`` (planifiee → confirmee → realisee, annulable).

Ajouts du 20/09 (espace « jour J ») : liste globale des sessions, documents
propres à une session, pointage en lot, fiche de présence générée, et import
des bénéficiaires depuis un classeur — ce dernier partage son service avec les
outils de l'agent assistant formateur (AGENTS.md §2.5).
"""

from datetime import date as date_type
from datetime import time
from io import BytesIO
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from app.api.deps import CurrentUser, DbSession, get_current_user
from app.api.routes.suppression import enregistrer_suppression
from app.api.schemas import (
    DocumentRead,
    FichePresenceCreate,
    ImportApercuCreate,
    ImportApercuRead,
    ImportConfirmationCreate,
    ImportResultatRead,
    ParticipationCreate,
    ParticipationRead,
    ParticipationUpdate,
    PresenceLotUpdate,
    PresencePointageCreate,
    PresenceRead,
    SessionCreate,
    SessionRead,
    SessionStatutUpdate,
    SessionUpdate,
)
from app.application.dto import (
    DocumentUploadInput,
    ParticipationInscriptionInput,
    ParticipationUpdateInput,
    PresenceLotEntree,
    PresencePointageInput,
    SessionPlanificationInput,
    SessionUpdateInput,
)
from app.application.services import (
    DocumentService,
    ImportBeneficiairesService,
    ParticipationService,
    PresenceService,
    SessionService,
)
from app.application.services.import_beneficiaires_service import LigneImport
from app.documents.generation import build_xlsx
from app.domain.document import Document
from app.domain.enums import TypeDocument
from app.domain.execution import Participation, Presence, SessionFormation

#: En-tête de la fiche de présence générée (ordre des colonnes du document).
_ENTETE_FICHE_PRESENCE = (
    "Nom",
    "Prénom",
    "Présence",
    "Arrivée",
    "Départ",
    "Organisation d'origine",
)

router = APIRouter(tags=["sessions"], dependencies=[Depends(get_current_user)])

#: Suppression réelle cascadée (impact + exécution) — module partagé.
#: Les routes de session vivent à la racine du routeur (chemins explicites) :
#: un sous-routeur préfixé leur donne ``/sessions/{identifiant}``.
_router_suppression = APIRouter(prefix="/sessions")
enregistrer_suppression(_router_suppression, "sessions")
router.include_router(_router_suppression)


@router.post(
    "/missions/{mission_id}/sessions",
    response_model=SessionRead,
    status_code=status.HTTP_201_CREATED,
)
def create_session(
    mission_id: UUID, payload: SessionCreate, session: DbSession
) -> SessionFormation:
    """Planifie une session rattachée à une mission existante."""
    return SessionService(session).planifier(
        SessionPlanificationInput(
            mission_id=mission_id,
            theme=payload.theme,
            date_debut=payload.date_debut,
            date_fin=payload.date_fin,
            lieu=payload.lieu,
        )
    )


@router.get("/missions/{mission_id}/sessions", response_model=list[SessionRead])
def list_sessions(
    mission_id: UUID, session: DbSession
) -> list[SessionFormation]:
    """Sessions d'une mission (404 si la mission est absente)."""
    return SessionService(session).lister_pour_mission(mission_id)


@router.get("/sessions", response_model=list[SessionRead])
@router.get("/sessions/", response_model=list[SessionRead])
def list_all_sessions(
    session: DbSession,
    mission_id: Annotated[UUID | None, Query()] = None,
    statut: Annotated[
        str | None, Query(description="Filtre facultatif : statut de session")
    ] = None,
) -> list[SessionFormation]:
    """Toutes les sessions, filtres facultatifs (page Sessions).

    Lecture de tableau de bord : la mission n'a pas besoin d'être « ouverte »
    pour lister ses sessions.
    """
    return SessionService(session).lister(mission_id=mission_id, statut=statut)


@router.get("/sessions/{session_id}", response_model=SessionRead)
def get_session_route(
    session_id: UUID, session: DbSession
) -> SessionFormation:
    """Consultation d'une session (404 si absente)."""
    return SessionService(session).obtenir(session_id)


@router.post("/sessions/{session_id}/statut", response_model=SessionRead)
def change_session_statut(
    session_id: UUID,
    payload: SessionStatutUpdate,
    session: DbSession,
) -> SessionFormation:
    """Transition planifiee → confirmee → realisee (annulable), machine à états."""
    return SessionService(session).changer_statut(session_id, payload.statut)


@router.patch("/sessions/{session_id}", response_model=SessionRead)
def update_session(
    session_id: UUID,
    payload: SessionUpdate,
    session: DbSession,
) -> SessionFormation:
    """Modification partielle d'une session : thème, dates, lieu.

    La mission de rattachement ne bouge pas (une session vit dans le contexte
    d'une mission). Seuls les champs fournis sont appliqués ; la machine à états
    passe par ``/statut``.
    """
    return SessionService(session).modifier(
        session_id,
        SessionUpdateInput(
            theme=payload.theme,
            date_debut=payload.date_debut,
            date_fin=payload.date_fin,
            lieu=payload.lieu,
        ),
    )


@router.post(
    "/sessions/{session_id}/participations",
    response_model=ParticipationRead,
    status_code=status.HTTP_201_CREATED,
)
def inscrire_beneficiaire(
    session_id: UUID,
    payload: ParticipationCreate,
    session: DbSession,
) -> Participation:
    """Inscrit un bénéficiaire à la session (409 si déjà inscrit)."""
    return ParticipationService(session).inscrire(
        session_id,
        ParticipationInscriptionInput(beneficiaire_id=payload.beneficiaire_id),
    )


@router.get("/sessions/{session_id}/participations", response_model=list[ParticipationRead])
def list_participations(
    session_id: UUID, session: DbSession
) -> list[Participation]:
    """Participations d'une session (404 si la session est absente)."""
    return ParticipationService(session).lister_pour_session(session_id)


@router.get("/sessions/{session_id}/documents", response_model=list[DocumentRead])
def list_session_documents(session_id: UUID, session: DbSession) -> list[Document]:
    """Documents propres à une session : fiche de présence, checklist, rapport."""
    SessionService(session).obtenir(session_id)
    return DocumentService(session).lister_pour("session_id", session_id)


@router.get("/sessions/{session_id}/presences", response_model=list[PresenceRead])
def list_presences(
    session_id: UUID,
    session: DbSession,
    date: Annotated[
        date_type | None,
        Query(description="Date du pointage ; absente = toutes les dates de la session"),
    ] = None,
) -> list[Presence]:
    """Pointages d'une session — toute la plage, ou une seule journée."""
    return PresenceService(session).lister_pour_session(session_id, date)


@router.patch("/sessions/{session_id}/presences", response_model=list[PresenceRead])
def pointer_presences(
    session_id: UUID, payload: PresenceLotUpdate, session: DbSession
) -> list[Presence]:
    """Pointage de la fiche de présence **pour une date**, en une transaction.

    Re-pointer la même date corrige les valeurs existantes : aucun doublon, la
    contrainte ``uq_presences_participation_id_date`` le garantit.
    """
    return PresenceService(session).pointer(
        session_id,
        payload.date,
        [
            PresenceLotEntree(
                participation_id=ligne.participation_id,
                presence=ligne.presence,
                heure_arrivee=ligne.heure_arrivee,
                heure_depart=ligne.heure_depart,
            )
            for ligne in payload.lignes
        ],
    )


@router.post(
    "/sessions/{session_id}/imports/beneficiaires/apercu",
    response_model=ImportApercuRead,
)
def apercu_import_beneficiaires(
    session_id: UUID, payload: ImportApercuCreate, session: DbSession
) -> ImportApercuRead:
    """Aperçu corrigeable — **aucun bénéficiaire n'est créé ici**."""
    apercu = ImportBeneficiairesService(session).preparer(
        payload.document_id,
        sheet=payload.sheet,
        max_lignes=payload.max_lignes,
        session_id=session_id,
    )
    return ImportApercuRead(**apercu.en_donnees())


@router.post(
    "/sessions/{session_id}/imports/beneficiaires",
    response_model=ImportResultatRead,
    status_code=status.HTTP_201_CREATED,
)
def confirmer_import_beneficiaires(
    session_id: UUID, payload: ImportConfirmationCreate, session: DbSession
) -> ImportResultatRead:
    """Enregistre les lignes confirmées et les inscrit à la session."""
    resultat = ImportBeneficiairesService(session).confirmer(
        session_id=session_id,
        lignes=[
            LigneImport(
                nom=ligne.nom.strip(),
                prenom=ligne.prenom.strip(),
                contact=ligne.contact,
                organisation_origine=ligne.organisation_origine,
                identifiant_externe=ligne.identifiant_externe,
            )
            for ligne in payload.lignes
        ],
    )
    return ImportResultatRead(**resultat.en_donnees())


@router.post(
    "/sessions/{session_id}/fiche-presence",
    response_model=DocumentRead,
    status_code=status.HTTP_201_CREATED,
)
def generer_fiche_presence(
    session_id: UUID, payload: FichePresenceCreate, session: DbSession, user: CurrentUser
) -> Document:
    """Génère la fiche de présence **d'une date** (XLSX) et la dépose.

    Le document naît en ``draft`` : c'est un document de travail humain, pas une
    proposition d'agent (règle 6 [C]). Il est ancré sur la session.

    Seules les personnes **pointées ce jour-là** figurent sur la feuille : une
    fiche de présence est le constat d'une journée, pas d'une session entière.
    """
    # Vérifie l'existence de la session avant de générer quoi que ce soit.
    SessionService(session).obtenir(session_id)
    presences = PresenceService(session).lister_pour_session(session_id, payload.date)
    par_participation = {presence.participation_id: presence for presence in presences}
    participations = ParticipationService(session).lister_pour_session(session_id)

    lignes: list[list[object]] = [list(_ENTETE_FICHE_PRESENCE)]
    for participation in participations:
        presence = par_participation.get(participation.id)
        if presence is None:
            continue
        beneficiaire = participation.beneficiaire
        lignes.append(
            [
                beneficiaire.nom,
                beneficiaire.prenom,
                presence.presence or "",
                _heure(presence.heure_arrivee),
                _heure(presence.heure_depart),
                beneficiaire.organisation_origine or "",
            ]
        )

    contenu = build_xlsx(titre_feuille="Presence", lignes=lignes)
    # Nom stable par session **et par date** : chaque journée a sa fiche, et la
    # régénérer après un correctif de pointage produit une **nouvelle version**
    # du même jour (jamais un 409, jamais un écrasement, jamais un mélange).
    nom = f"fiche_presence_{session_id}_{payload.date.isoformat()}.xlsx"
    service = DocumentService(session, actor_id=str(user.id))
    entree = DocumentUploadInput(
        type_document=TypeDocument.FICHE_PRESENCE.value,
        nom=nom,
        stream=BytesIO(contenu),
        created_by=str(user.id),
        session_id=session_id,
    )

    existante = service.documents.find_active_by_name(
        nom, TypeDocument.FICHE_PRESENCE.value
    )
    if existante is not None:
        return service.remplacer_document(existante.id, entree)
    return service.enregistrer_document(entree)


def _heure(valeur: time | None) -> str:
    """Formate une heure de pointage pour le classeur (vide si non pointée)."""
    return valeur.strftime("%H:%M") if valeur is not None else ""


@router.get("/participations/{participation_id}", response_model=ParticipationRead)
def get_participation(
    participation_id: UUID, session: DbSession
) -> Participation:
    """Consultation d'une participation (404 si absente)."""
    return ParticipationService(session).obtenir(participation_id)


@router.post("/participations/{participation_id}/presence", response_model=PresenceRead)
def pointer_presence(
    participation_id: UUID,
    payload: PresencePointageCreate,
    session: DbSession,
) -> Presence:
    """Pointage d'une personne pour **une date** (heures optionnelles)."""
    return PresenceService(session).pointer_participation(
        participation_id,
        PresencePointageInput(
            date=payload.date,
            presence=payload.presence,
            heure_arrivee=payload.heure_arrivee,
            heure_depart=payload.heure_depart,
        ),
    )


@router.patch("/participations/{participation_id}", response_model=ParticipationRead)
def update_participation(
    participation_id: UUID,
    payload: ParticipationUpdate,
    session: DbSession,
) -> Participation:
    """Complète évaluation/observations (seuls les champs fournis)."""
    return ParticipationService(session).modifier(
        participation_id,
        ParticipationUpdateInput(
            evaluation=payload.evaluation,
            observations=payload.observations,
        ),
    )


@router.delete(
    "/participations/{participation_id}", status_code=status.HTTP_204_NO_CONTENT
)
def retirer_participation(participation_id: UUID, session: DbSession) -> None:
    """Retire un bénéficiaire d'une session (désinscription).

    La participation est un **lien** : la personne et la session survivent. Le
    geste reste auditable (``participation.retiree``) ; les pointages de cette
    inscription partent avec elle (``CASCADE``) et leur nombre est conservé dans
    la trace — un pointage sans inscription n'aurait aucun sens.
    """
    ParticipationService(session).retirer(participation_id)
