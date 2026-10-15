"""Tools de ``agent_rh`` (clinrules 03, refactor outils).

Lieu unique du périmètre outils de l'agent RH. Matrice d'accès (validée) :

- **Read/Write (écritures sous HITL)** : ``Document``, ``Equipe``, ``Mission`` ;
- **Lecture seule** : les autres tables.

Le rôle vit dans l'affectation, jamais sur la personne. Aucun diplôme,
langue ou disponibilité inventé : un score d'adéquation est explicable
(présence de profil / CV / rôle déjà tenu), jamais une précision scientifique.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agents.consultation import (
    rechercher_appels_a_proposition,
    rechercher_documents,
    rechercher_organisations,
)
from app.agents.toolkit import (
    AgentTools,
    OperationSpec,
    PageSearchInput,
    serialiser_entite,
)
from app.agents.tools.agent_generaliste_tools import (
    BeneficiaireSearchInput,
    LotSearchInput,
    OffreSearchInput,
    SessionSearchInput,
    rechercher_beneficiaires,
    rechercher_lots,
    rechercher_offres,
    rechercher_sessions,
)
from app.application.dto import AffectationProposee
from app.application.services import (
    AffectationService,
    DocumentService,
    EquipeService,
    MissionService,
)
from app.application.trace import TraceContext
from app.core.config import get_settings
from app.core.errors import ValidationError as BusinessValidationError
from app.domain.enums import ActionAudit, ActorType, SourceAffectation, TypeDocument
from app.domain.execution import Equipe, Mission
from app.domain.identity import Utilisateur
from app.domain.organization import Organisation
from app.infrastructure.email import EnvoyeurEmail, PieceJointeEmail
from app.infrastructure.repositories import AffectationEquipeRepository, DocumentRepository

AGENT_ID = "agent_rh"
TASK_TYPE = "propose_team_assignment"

#: Capacité exigée pour écrire à un contact (vérifiée **à la construction** du
#: kit : un contrat qui déclare ``envoyer_email`` sans cette capacité échoue au
#: montage, jamais à l'appel).
CAPACITE_EMAIL = "email_send"

#: Bornes de service d'un envoi collectif (pas une règle métier) : au-delà, le
#: refus est explicite — un envoi silencieusement tronqué serait un mensonge.
MAX_DESTINATAIRES = 10
MAX_PIECES_JOINTES = 5

NOMS_TOOLS: tuple[str, ...] = (
    "search_equipes",
    "get_equipe",
    "list_equipe_cv",
    "get_mission",
    "list_mission_assignments",
    "search_missions",
    "create_assignment_proposal",
    "propose_equipe_update",
    "propose_mission_update",
    "search_organisations",
    "search_appels_a_proposition",
    "search_lots",
    "search_offres",
    "search_sessions",
    "search_beneficiaires",
    "search_documents",
    "lire_document",
    "read_document",
    "get_document_structure",
    "chercher_dans_document",
    "lire_element_document",
    "generer_document",
    "generer_document_professionnel",
    "generer_document_html",
    # Envoi d'un email au nom de CARSO à un contact **enregistré** (HITL :
    # approbation avant envoi, cf. ``approval_required`` du contrat).
    "envoyer_email",
    # Recherche web fédérée + lecture de pages, et recherche média : la même
    # surface de lecture externe que les autres agents (capacité ``web_search``).
    "langsearch_web_search",
    "fetch_url",
    "read_urls",
    "rechercher_image",
    "rechercher_video",
    "rechercher_document",
)


class EquipeIdInput(BaseModel):
    equipe_id: UUID


class MissionIdInput(BaseModel):
    mission_id: UUID


class MissionSearchInput(PageSearchInput):
    """Missions par statut (lecture toutes missions, périmètre RH)."""

    statut: str | None = Field(default=None, max_length=50)


class AssignmentProposalInput(BaseModel):
    mission_id: UUID
    equipe_id: UUID
    role_dans_mission: str = Field(min_length=1, max_length=100)
    justification: str = Field(min_length=1, max_length=2000)


class EquipeUpdateInput(BaseModel):
    """Mise à jour du profil d'une personne du vivier : proposition (HITL)."""

    equipe_id: UUID
    profil: str | None = Field(default=None, max_length=4000)
    email: str | None = Field(default=None, max_length=255)
    telephone: str | None = Field(default=None, max_length=50)


class MissionUpdateInput(BaseModel):
    """Mise à jour d'une mission : proposition (HITL).

    Le lieu d'exécution est une **référence** (``lieux``), pas un texte libre :
    le lot d'un appel n'est pas un lieu, et un lieu inventé ne doit jamais être
    écrit.
    """

    mission_id: UUID
    titre: str | None = Field(default=None, min_length=1, max_length=255)
    lieu_id: UUID | None = Field(
        default=None, description="Référence d'un lieu existant (jamais un texte libre)."
    )
    description: str | None = Field(default=None, max_length=4000)


class EnvoyerEmailInput(BaseModel):
    """Email sortant de l'agent RH — vers un ou plusieurs contacts **enregistrés**.

    ``destinataires`` n'est pas une liste d'adresses libres : chaque adresse doit
    correspondre à l'email d'un intervenant du vivier, d'une organisation cliente
    ou d'un compte. Une adresse hors base fait échouer l'envoi **entier** (garde
    anti-exfiltration) : aucun destinataire ne reçoit un message partiel.

    Les pièces jointes sont désignées par **identifiant de document enregistré**
    (document, image, feuille HTML...) : jamais un chemin fourni par le modèle.
    """

    destinataires: list[str] = Field(
        min_length=1,
        max_length=MAX_DESTINATAIRES,
        description=(
            "Emails de contacts enregistrés (1 à 10) : intervenant du vivier, "
            "organisation cliente ou compte. Adresse inconnue ⇒ envoi refusé."
        ),
    )
    sujet: str = Field(min_length=3, max_length=200)
    corps: str = Field(
        min_length=1,
        max_length=10_000,
        description="Corps du message en texte simple (version de repli de la variante HTML).",
    )
    corps_html: str | None = Field(
        default=None,
        max_length=50_000,
        description=(
            "Variante HTML du même message (mise en forme : titres, listes, "
            "tableaux). Le texte de ``corps`` reste la version de repli."
        ),
    )
    pieces_jointes: list[UUID] = Field(
        default_factory=list,
        max_length=MAX_PIECES_JOINTES,
        description=(
            "Identifiants de documents **déjà enregistrés** joints au message "
            "(document, image, feuille HTML) — trouvés via ``search_documents``, "
            "``list_equipe_cv`` ou les listes de documents d'une fiche."
        ),
    )
    motif: str | None = Field(
        default=None,
        max_length=255,
        description="Raison en une phrase, tracée dans le journal (jamais le corps).",
    )

    @field_validator("destinataires")
    @classmethod
    def _adresses_plausibles(cls, valeurs: list[str]) -> list[str]:
        """Adresses normalisées et dédoublonnées, forme vérifiée.

        Une forme invalide arrête la construction : jamais un envoi à un texte
        libre, jamais deux fois la même adresse (une casse différente ne crée pas
        un second destinataire).
        """
        propres: list[str] = []
        for valeur in valeurs:
            adresse = valeur.strip().lower()
            local, arobase, domaine = adresse.partition("@")
            if not (local and arobase and "." in domaine and " " not in adresse):
                raise ValueError(
                    f"adresse email invalide : forme attendue nom@domaine ({valeur.strip()})"
                )
            if adresse not in propres:
                propres.append(adresse)
        if not propres:
            raise ValueError("aucun destinataire exploitable")
        return propres


_CHAMPS_MISSION = (
    "id",
    "reference",
    "titre",
    "statut",
    "lieu_id",
    "date_debut",
    "date_fin",
)


def _get_equipe(session: Session, payload: dict[str, Any]) -> dict[str, Any]:
    equipe = EquipeService(session).obtenir(UUID(str(payload["equipe_id"])))
    return {
        "id": str(equipe.id),
        "nom": equipe.nom,
        "prenom": equipe.prenom,
        # Assemblé par le service : le modèle recopie, il ne compose pas.
        "nom_complet": " ".join(
            part for part in (equipe.prenom, equipe.nom) if (part or "").strip()
        ),
        "profil": equipe.profil,
        "statut": equipe.statut,
        "email": equipe.email,
    }


def _list_cv(session: Session, payload: dict[str, Any]) -> dict[str, Any]:
    docs = DocumentRepository(session).list_for_equipe(UUID(str(payload["equipe_id"])))
    cvs = [d for d in docs if d.type_document == TypeDocument.CV.value]
    return {
        "equipe_id": str(payload["equipe_id"]),
        "cv": [
            {
                "document_id": str(d.id),
                "nom": d.nom,
                "version": d.version,
                "statut": d.statut,
            }
            for d in cvs
        ],
        "nb_cv": len(cvs),
    }


def _get_mission(session: Session, payload: dict[str, Any]) -> dict[str, Any]:
    mission = MissionService(session).obtenir(UUID(str(payload["mission_id"])))
    return serialiser_entite(mission, *_CHAMPS_MISSION)


def _search_missions(session: Session, payload: dict[str, Any]) -> dict[str, Any]:
    filtres = MissionSearchInput.model_validate(payload)
    stmt = select(Mission).order_by(Mission.date_debut.desc())
    if filtres.statut is not None:
        stmt = stmt.where(Mission.statut == filtres.statut)
    if filtres.recherche:
        motif = f"%{filtres.recherche.strip()}%"
        stmt = stmt.where(Mission.titre.ilike(motif) | Mission.reference.ilike(motif))
    missions = list(session.scalars(stmt.limit(filtres.limite)))
    return {"missions": [serialiser_entite(m, *_CHAMPS_MISSION) for m in missions]}


def _list_assignments(session: Session, payload: dict[str, Any]) -> dict[str, Any]:
    items = AffectationEquipeRepository(session).list_for_mission(
        UUID(str(payload["mission_id"]))
    )
    return {
        "affectations": [
            {
                "id": str(a.id),
                "equipe_id": str(a.equipe_id),
                "role_dans_mission": a.role_dans_mission,
                "statut": a.statut,
                "source": a.source_affectation,
            }
            for a in items
        ]
    }


def _score(session: Session, equipe_id: UUID, role: str) -> dict[str, Any]:
    """Score explicable, déterministe, sans invention."""
    equipe = EquipeService(session).obtenir(equipe_id)
    cvs = [
        d
        for d in DocumentRepository(session).list_for_equipe(equipe_id)
        if d.type_document == TypeDocument.CV.value
    ]
    criteres = {
        "profil_renseigne": bool((equipe.profil or "").strip()),
        "cv_present": bool(cvs),
        "role_dans_profil": role.lower() in (equipe.profil or "").lower(),
    }
    points = sum(1 for v in criteres.values() if v)
    return {
        "score": points,
        "score_max": 3,
        "criteres": criteres,
        "limite": "Score indicatif à partir des données enregistrées uniquement.",
    }


def _create_proposal(session: Session, payload: dict[str, Any]) -> dict[str, Any]:
    entree = AssignmentProposalInput.model_validate(payload)
    score = _score(session, entree.equipe_id, entree.role_dans_mission)
    affectation = AffectationService(session, actor_id=AGENT_ID).proposer(
        AffectationProposee(
            mission_id=entree.mission_id,
            equipe_id=entree.equipe_id,
            role_dans_mission=entree.role_dans_mission,
            proposed_by=AGENT_ID,
            source=SourceAffectation.AI_PROPOSAL.value,
        )
    )
    return {
        "affectation_id": str(affectation.id),
        "statut": affectation.statut,
        "requires_approval": True,
        "justification": entree.justification,
        **score,
        "message": "Proposition enregistrée. Non officielle tant qu'elle n'est pas approuvée.",
    }


def _propose_equipe_update(session: Session, payload: dict[str, Any]) -> dict[str, Any]:
    """Applique une mise à jour du vivier après HITL."""
    from app.application.dto import EquipeUpdateInput as EquipeUpdateDto

    entree = EquipeUpdateInput.model_validate(payload)
    equipe = EquipeService(session, actor_id=AGENT_ID).modifier(
        entree.equipe_id,
        EquipeUpdateDto(
            profil=entree.profil,
            email=entree.email,
            telephone=entree.telephone,
        ),
    )
    return {
        "equipe_id": str(equipe.id),
        "profil": equipe.profil,
        "email": equipe.email,
        "telephone": equipe.telephone,
        "requires_approval": True,
        "message": "Fiche vivier mise à jour après votre approbation.",
    }


def _propose_mission_update(session: Session, payload: dict[str, Any]) -> dict[str, Any]:
    """Applique une mise à jour de mission après HITL."""
    entree = MissionUpdateInput.model_validate(payload)
    mission = MissionService(session, actor_id=AGENT_ID).modifier(
        entree.mission_id,
        titre=entree.titre,
        lieu_id=entree.lieu_id,
        description=entree.description,
    )
    return {
        "mission_id": str(mission.id),
        "titre": mission.titre,
        "lieu_id": str(mission.lieu_id) if mission.lieu_id else None,
        "description": mission.description,
        "requires_approval": True,
        "message": "Mission mise à jour après votre approbation.",
    }


@dataclass(frozen=True, slots=True)
class ContactEmail:
    """Contact destinataire **résolu en base** (jamais une adresse libre)."""

    email: str
    nom: str
    entity_type: str
    entity_id: UUID


def _resoudre_contact(session: Session, adresse: str) -> ContactEmail | None:
    """Retrouve le contact porteur d'une adresse : intervenant, organisation, compte.

    Ordre de résolution : vivier (``equipes``), organisation cliente, compte
    utilisateur. L'adresse est comparée **en minuscules** — une casse différente
    ne crée pas un second destinataire.
    """
    cible = adresse.strip().lower()
    personne = session.scalars(
        select(Equipe).where(func.lower(Equipe.email) == cible).limit(1)
    ).first()
    if personne is not None:
        nom = " ".join(part for part in (personne.prenom, personne.nom) if (part or "").strip())
        return ContactEmail(personne.email or cible, nom, "equipes", personne.id)
    organisation = session.scalars(
        select(Organisation).where(func.lower(Organisation.email) == cible).limit(1)
    ).first()
    if organisation is not None:
        return ContactEmail(
            organisation.email or cible, organisation.nom, "organisations", organisation.id
        )
    compte = session.scalars(
        select(Utilisateur).where(func.lower(Utilisateur.email) == cible).limit(1)
    ).first()
    if compte is not None:
        nom = " ".join(part for part in (compte.prenom, compte.nom) if (part or "").strip())
        return ContactEmail(compte.email, nom, "utilisateurs", compte.id)
    return None


def _charger_pieces_jointes(session: Session, ids: list[UUID]) -> list[PieceJointeEmail]:
    """Charge les pièces jointes **depuis la base** (jamais un chemin fourni).

    Chaque identifiant désigne un document enregistré : le contenu est lu par la
    couche de stockage à partir de son chemin logique contrôlé. Un document
    introuvable, hors périmètre ou supprimé (fichier retiré) arrête l'envoi — et
    le poids total est borné par ``EMAIL_PIECES_JOINTES_MAX_OCTETS``.

    Raises:
        NotFoundError: document inconnu, hors périmètre ou fichier retiré.
        ValidationError: poids total des pièces jointes au-delà du plafond.
    """
    if not ids:
        return []
    service = DocumentService(session, actor_id=AGENT_ID)
    plafond = get_settings().email_pieces_jointes_max_octets
    pieces: list[PieceJointeEmail] = []
    total = 0
    for document_id in ids:
        # ``telecharger`` refuse un document supprimé et résout un chemin
        # contrôlé ; ``obtenir`` fournit le type MIME déclaré au dépôt.
        document = service.obtenir(document_id)
        nom_fichier, _ = service.telecharger(document_id)
        contenu = service.storage.read(document.storage_path)
        total += len(contenu)
        if total > plafond:
            raise BusinessValidationError(
                "Pièces jointes trop lourdes : "
                f"{total} octets au total pour un plafond de {plafond}. "
                "Retirer un document ou en joindre un plus léger.",
                details={"document_id": str(document_id), "plafond_octets": plafond},
            )
        pieces.append(
            PieceJointeEmail(
                nom=nom_fichier,
                contenu=contenu,
                mime_type=document.mime_type or "application/octet-stream",
            )
        )
    return pieces


def _envoyer_email(session: Session, payload: dict[str, Any]) -> dict[str, Any]:
    """Envoie un email au nom de CARSO à un ou plusieurs contacts **enregistrés**.

    Quatre garanties, dans cet ordre :

    1. chaque destinataire est **résolu en base** — une adresse inconnue arrête
       l'envoi entier avant tout départ : aucune exfiltration vers une adresse
       arbitraire, et jamais un envoi partiel ;
    2. les pièces jointes sont des **documents enregistrés** chargés par la
       couche de stockage (identifiant choisi par l'agent, jamais un chemin) ;
    3. l'approbation humaine est portée par le contrat (``envoyer_email`` est
       dans ``approval_required`` : le harnais interrompt avant l'exécution) ;
    4. l'envoi est **tracé** — une ligne d'audit par destinataire (acteur =
       agent, destinataire, sujet, taille du corps, pièces jointes jointes en
       métadonnées). Le corps lui-même n'entre jamais dans l'audit, un email
       pouvant porter des données personnelles.

    Les destinataires figurent tous en clair (``To``) : c'est l'envoi collectif
    visible — une copie cachée relèverait d'une règle métier à confirmer avec
    CARSO, jamais d'un choix silencieux du code.

    Le transport vient de ``Settings`` (``SMTP_*`` dans ``.env``) : sans
    ``SMTP_HOST``, le message part sur les journaux **en développement** et
    l'envoi refuse explicitement en production (voir
    ``app/infrastructure/email.py``). Le canal réellement utilisé est dit dans
    la réponse — jamais une réussite supposée.
    """
    entree = EnvoyerEmailInput.model_validate(payload)
    contacts: list[ContactEmail] = []
    inconnus: list[str] = []
    for adresse in entree.destinataires:
        contact = _resoudre_contact(session, adresse)
        if contact is None:
            inconnus.append(adresse)
        else:
            contacts.append(contact)
    if inconnus:
        raise BusinessValidationError(
            "Adresse inconnue de la base : l'agent RH n'écrit qu'à un contact "
            "enregistré (intervenant du vivier, organisation cliente ou compte). "
            "Vérifier l'email des fiches concernées — une adresse inventée n'est "
            "jamais utilisée, et rien n'a été envoyé.",
            details={"destinataires_inconnus": inconnus},
        )
    pieces = _charger_pieces_jointes(session, entree.pieces_jointes)
    reglages = get_settings()
    EnvoyeurEmail(reglages).envoyer(
        destinataires=[contact.email for contact in contacts],
        sujet=entree.sujet,
        corps=entree.corps,
        corps_html=entree.corps_html,
        pieces_jointes=pieces,
    )
    annexes = [
        {"nom": piece.nom, "mime_type": piece.mime_type, "octets": piece.taille}
        for piece in pieces
    ]
    trace = TraceContext(session, actor_id=AGENT_ID)
    for contact in contacts:
        trace.record_event(
            actor_type=ActorType.AGENT.value,
            action=ActionAudit.EMAIL_ENVOYE.value,
            entity_type=contact.entity_type,
            entity_id=contact.entity_id,
            after={
                "destinataire": contact.email,
                "type_destinataire": contact.entity_type,
                "sujet": entree.sujet,
                "caracteres": len(entree.corps),
                "destinataires_total": len(contacts),
                "corps_html": bool((entree.corps_html or "").strip()),
                "pieces_jointes": annexes,
            },
            event_metadata={"motif": entree.motif} if entree.motif else None,
        )
    return {
        "envoye": True,
        "destinataires": [
            {
                "email": contact.email,
                "nom": contact.nom,
                "type": contact.entity_type,
                "entity_id": str(contact.entity_id),
            }
            for contact in contacts
        ],
        "nb_destinataires": len(contacts),
        "sujet": entree.sujet,
        "corps_html": bool((entree.corps_html or "").strip()),
        "pieces_jointes": annexes,
        "canal": "smtp" if reglages.smtp_host else "console (développement, SMTP_HOST absent)",
        "message": (
            "Email envoyé au contact enregistré. L'envoi est définitif : "
            "aucune annulation n'est possible."
            if len(contacts) == 1
            else (
                f"Email envoyé à {len(contacts)} contacts enregistrés. "
                "L'envoi est définitif : aucune annulation n'est possible."
            )
        ),
    }


def construire_tools_rh(
    definition: Any,
    session_factory: Any,
    *,
    extra_tools: tuple[Any, ...] = (),
) -> tuple[Any, ...]:
    """Tools du RH : vivier + missions + propositions (écritures HITL) + email."""
    kit = AgentTools(definition, session_factory=session_factory)
    kit.enregistrer(OperationSpec(
        name="search_equipes",
        description="Recherche dans le vivier (nom, prénom, profil).",
        input_schema=PageSearchInput,
        run=_rechercher_equipes_rh,
    ))
    kit.enregistrer(OperationSpec(
        name="get_equipe",
        description="Fiche personne (sans inventer de compétences).",
        input_schema=EquipeIdInput,
        run=_get_equipe,
    ))
    kit.enregistrer(OperationSpec(
        name="list_equipe_cv",
        description="CV rattachés (métadonnées uniquement).",
        input_schema=EquipeIdInput,
        run=_list_cv,
    ))
    kit.enregistrer(OperationSpec(
        name="get_mission",
        description="Fiche mission (exigences, dates, lieu).",
        input_schema=MissionIdInput,
        run=_get_mission,
    ))
    kit.enregistrer(OperationSpec(
        name="search_missions",
        description="Missions par statut ou texte (cibler l'analyse).",
        input_schema=MissionSearchInput,
        run=_search_missions,
    ))
    kit.enregistrer(OperationSpec(
        name="list_mission_assignments",
        description="Affectations déjà posées sur la mission.",
        input_schema=MissionIdInput,
        run=_list_assignments,
    ))
    kit.enregistrer(OperationSpec(
        name="create_assignment_proposal",
        description="Crée une affectation proposed (HITL).",
        input_schema=AssignmentProposalInput,
        mutation=True,
        run=_create_proposal,
    ))
    kit.enregistrer(OperationSpec(
        name="propose_equipe_update",
        description=(
            "Propose une mise à jour de fiche vivier (profil, email, téléphone) : "
            "aucune écriture directe, approbation humaine requise."
        ),
        input_schema=EquipeUpdateInput,
        mutation=True,
        run=_propose_equipe_update,
    ))
    kit.enregistrer(OperationSpec(
        name="propose_mission_update",
        description=(
            "Propose une mise à jour de mission (titre, lieu d'exécution, "
            "description) : lieu_id doit référencer un lieu existant. Aucune "
            "écriture directe, approbation humaine requise."
        ),
        input_schema=MissionUpdateInput,
        mutation=True,
        run=_propose_mission_update,
    ))
    # Autres tables : lecture seule (matrice validée).
    kit.enregistrer(OperationSpec(
        name="search_organisations",
        description="Organisations clientes par nom (lecture seule).",
        input_schema=PageSearchInput,
        run=rechercher_organisations,
    ))
    kit.enregistrer(OperationSpec(
        name="search_appels_a_proposition",
        description="Appels à proposition (organisation, statut, texte) + lots.",
        input_schema=PageSearchInput,
        run=rechercher_appels_a_proposition,
    ))
    kit.enregistrer(OperationSpec(
        name="search_lots",
        description="Lots d'un appel (ou tous) : numéro, titre, zone.",
        input_schema=LotSearchInput,
        run=rechercher_lots,
    ))
    kit.enregistrer(OperationSpec(
        name="search_offres",
        description="Offres de formation (statut, organisation) — lecture seule.",
        input_schema=OffreSearchInput,
        run=rechercher_offres,
    ))
    kit.enregistrer(OperationSpec(
        name="search_sessions",
        description="Sessions de formation — lecture seule.",
        input_schema=SessionSearchInput,
        run=rechercher_sessions,
    ))
    kit.enregistrer(OperationSpec(
        name="search_beneficiaires",
        description="Bénéficiaires par nom/prénom (données minimisées).",
        input_schema=BeneficiaireSearchInput,
        run=rechercher_beneficiaires,
    ))
    kit.enregistrer(OperationSpec(
        name="search_documents",
        description="Documents (une ancre métier, type). Métadonnées seules.",
        input_schema=PageSearchInput,
        run=rechercher_documents,
    ))

    # Email sortant : la capacité est exigée **à la construction** (un contrat
    # qui déclare le tool sans la capacité échoue au montage, pas à l'appel).
    definition.policy.require(definition.agent_id, CAPACITE_EMAIL)
    kit.enregistrer(OperationSpec(
        name="envoyer_email",
        description=(
            "Envoie le même email au nom de CARSO à un ou plusieurs contacts "
            "enregistrés (intervenants du vivier, organisations clientes, "
            "comptes). Une seule adresse inconnue fait échouer l'envoi entier : "
            "jamais un envoi partiel. Pièces jointes : identifiants de documents "
            "déjà enregistrés (document, image, feuille HTML). Variante HTML "
            "possible (corps_html). Approbation humaine avant envoi ; envoi "
            "tracé, définitif."
        ),
        input_schema=EnvoyerEmailInput,
        mutation=True,
        run=_envoyer_email,
    ))

    from app.tools.recherche_media import attacher_recherche_media
    from app.tools.web import attacher_outils_web

    return attacher_recherche_media(
        definition, attacher_outils_web(definition, tuple(kit.construire()) + extra_tools)
    )


def _rechercher_equipes_rh(session: Session, payload: dict[str, Any]) -> dict[str, Any]:
    """Vivier RH : nom/prénom/profil + nb d'affectations (comme consultation)."""
    terme = (payload.get("recherche") or "").strip()
    stmt = select(Equipe).order_by(Equipe.nom, Equipe.prenom)
    if terme:
        motif = f"%{terme}%"
        stmt = stmt.where(
            Equipe.nom.ilike(motif) | Equipe.prenom.ilike(motif) | Equipe.profil.ilike(motif)
        )
    equipes = list(session.scalars(stmt.limit(100)))
    return {
        "equipes": [
            {
                **serialiser_entite(equipe, "id", "nom", "prenom", "email", "profil", "statut"),
                "nom_complet": " ".join(
                    part for part in (equipe.prenom, equipe.nom) if (part or "").strip()
                ),
                "nb_affectations": len(equipe.affectations),
            }
            for equipe in equipes
        ]
    }


__all__ = [
    "AGENT_ID",
    "CAPACITE_EMAIL",
    "NOMS_TOOLS",
    "TASK_TYPE",
    "AssignmentProposalInput",
    "EnvoyerEmailInput",
    "construire_tools_rh",
]
