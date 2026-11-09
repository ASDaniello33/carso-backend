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
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agents.consultation import (
    rechercher_appels,
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
from app.application.dto import AffectationProposee, LigneBudgetInput
from app.application.services import (
    AffectationService,
    BudgetService,
    DocumentService,
    EquipeService,
    MissionService,
)
from app.application.trace import TraceContext
from app.core.config import get_settings
from app.core.errors import NotFoundError
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

#: Plafond de fiches lues en un appel (borne de service, pas une règle métier) :
#: au-delà, la réponse déborderait la conversation. Un vivier entier se lit par
#: pages de cette taille, pas par un appel par personne.
MAX_FICHES_PAR_LOT = 50

NOMS_TOOLS: tuple[str, ...] = (
    # Base de connaissance : consulter une méthode, un cadre de référence,
    # une expertise déjà écrite — à la demande, jamais injectée d'office.
    "search_knowledge_base",
    "rechercher_lieu",
    "calculer_itineraire",
    "search_equipes",
    "get_equipe",
    "list_equipe_cv",
    "get_mission",
    "list_mission_assignments",
    "search_missions",
    # Disponibilité **calculée** sur une période : la règle du service,
    # exposée en lecture pour que le classement écarte les personnes en
    # conflit avant de proposer (constat playtest 28/10).
    "verifier_disponibilite",
    "create_assignment_proposal",
    "propose_equipe_update",
    "propose_mission_update",
    # Budgets de mission (Phase 2 §5) : créer, ajuster, soumettre. Écritures
    # sous HITL ; les montants sont TOUJOURS recalculés côté serveur (règle
    # 7 [C]) et un budget approuvé n'est modifiable que par nouvelle version.
    "lister_budgets_mission",
    "lire_budget",
    "creer_budget_mission",
    "ajouter_ligne_budget",
    "modifier_ligne_budget",
    "retirer_ligne_budget",
    "soumettre_budget",
    "search_organisations",
    "search_appels",
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
    # Un CV ou une pièce scannée se lit en voyant l'image.
    "lire_image",
    # Renommage et suppression d'un document existant : contenu jamais
    # régénéré ; la suppression passe par une validation humaine.
        "renommer_document",
    "supprimer_document",
    "generer_document",
    "generer_document_professionnel",
    "modifier_section_document",
    "generer_document_html",
    # Image (INC-135) : organigramme ou figure fournie en base64, sinon en SVG
    # rasterisé ; affichée et téléchargeable par ``afficher_image``.
    "generer_image",
    "generer_image_svg",
    "composer_image",
    "verifier_image_svg",
    # Un modèle de document existant se lit (structure, plan) avant d'être
    # repris : la chaîne « copier le modèle dans une offre » appartient au
    # générateur d'offre, elle s'ancre sur une offre.
    "analyze_document_reference",
    # Traitement documentaire en série (CV, fiches) dans SON espace de travail.
    "run_python_script",
    # Envoi d'un email au nom de CARSO à un contact **enregistré** (HITL :
    # approbation avant envoi, cf. ``approval_required`` du contrat).
    "envoyer_email",
    # Recherche web fédérée + lecture de pages, et recherche de vidéos : la
    # même surface de lecture externe que les autres agents (``web_search``).
    "langsearch_web_search",
    "fetch_url",
    "read_urls",
    "rechercher_video",
)


class EquipeIdInput(BaseModel):
    equipe_id: UUID


class EquipesIdsInput(BaseModel):
    """Lot de fiches à lire en **un** appel.

    Une lecture par identifiant obligeait l'agent à un appel par personne :
    cinquante appels pour cinquante personnes, chacun consommant le tour de
    conversation. Le lot est la forme normale ; la borne évite seulement
    qu'une demande démesurée produise une réponse ingérable.
    """

    equipe_ids: list[UUID] = Field(
        min_length=1,
        max_length=MAX_FICHES_PAR_LOT,
        description=(
            "Personnes du vivier à lire, dans l'ordre où les fiches sont "
            f"attendues ({MAX_FICHES_PAR_LOT} au plus par appel)."
        ),
    )


class MissionIdInput(BaseModel):
    mission_id: UUID


class MissionSearchInput(PageSearchInput):
    """Missions par statut (lecture toutes missions, périmètre RH)."""

    statut: str | None = Field(default=None, max_length=50)


class DisponibiliteInput(BaseModel):
    """Période évaluée, et périmètre de personnes (défaut : tout le vivier).

    Lecture seule : le calcul s'appuie sur les affectations **approuvées** et
    les dates de mission — exactement la règle appliquée au moment de
    proposer. Aucune disponibilité n'est stockée.
    """

    date_debut: date = Field(description="Premier jour de la période (inclus).")
    date_fin: date = Field(description="Dernier jour de la période (inclus).")
    equipe_ids: list[UUID] = Field(
        default_factory=list,
        max_length=50,
        description=(
            "Personnes à évaluer. Vide = tout le vivier (200 personnes au plus)."
        ),
    )
    mission_id: UUID | None = Field(
        default=None,
        description=(
            "Mission écartée du calcul : renseigner la mission pour laquelle on "
            "cherche (une proposition déjà approuvée sur cette mission ne compte "
            "pas comme un conflit avec elle-même)."
        ),
    )


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
    écrit. Les dates se changent à tout état vivant (ADR 0015) : la session
    existante les reçoit et les statuts se réalignent après approbation.
    """

    mission_id: UUID
    titre: str | None = Field(default=None, min_length=1, max_length=255)
    lieu_id: UUID | None = Field(
        default=None, description="Référence d'un lieu existant (jamais un texte libre)."
    )
    description: str | None = Field(default=None, max_length=4000)
    date_debut: date | None = Field(default=None)
    date_fin: date | None = Field(default=None)


class MissionBudgetInput(BaseModel):
    """Porteur d'un budget côté RH : la **mission** seule.

    Le budget d'une offre appartient au dossier de l'offre (offre financière) :
    l'exécution budgétise la mission, et la reprise d'un budget d'offre reste
    un acte de service contrôlé (``reprendre_pour_mission``), pas un tool.
    """

    mission_id: UUID
    devise: str | None = Field(default=None, min_length=1, max_length=8)


class BudgetIdInput(BaseModel):
    budget_id: UUID


class LigneBudgetToolInput(BaseModel):
    """Saisie d'une ligne de budget par l'agent (règle 7 [C]).

    ``cout_total`` n'existe **pas** dans ce contrat : un champ de ce nom fourni
    par le modèle est ignoré — le serveur calcule ``quantite × cout_unitaire``.
    Montants négatifs refusés (une dépense n'est pas un avoir).
    """

    budget_id: UUID
    categorie: str = Field(min_length=1, max_length=120)
    quantite: Decimal = Field(ge=0)
    cout_unitaire: Decimal = Field(ge=0)
    description: str | None = Field(default=None, max_length=400)
    unite: str | None = Field(default=None, max_length=40)


class LigneBudgetModifInput(LigneBudgetToolInput):
    """Correction d'une ligne existante (même calcul déterministe)."""

    ligne_id: UUID


class LigneBudgetSuppressionInput(BaseModel):
    """Retrait d'une ligne : budget **et** ligne sont référencés (jamais
    une ligne devinée dans le budget courant)."""

    budget_id: UUID
    ligne_id: UUID


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


def _fiche_equipe(equipe: Equipe) -> dict[str, Any]:
    """Fiche d'une personne — champs lus, jamais complétés par déduction."""
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


def _get_equipes(session: Session, payload: dict[str, Any]) -> dict[str, Any]:
    """Fiches de plusieurs personnes en **un** appel (lecture seule).

    Un identifiant inconnu — ou hors périmètre d'un compte restreint, que le
    service traite de la même façon — ne fait pas échouer les autres : il est
    listé dans ``introuvables``. Une fiche manquante doit être **dite** : la
    remplacer par une fiche vide reviendrait à inventer une personne.
    """
    entree = EquipesIdsInput.model_validate(payload)
    service = EquipeService(session)
    fiches: list[dict[str, Any]] = []
    introuvables: list[str] = []
    vus: set[UUID] = set()
    for equipe_id in entree.equipe_ids:
        if equipe_id in vus:
            continue
        vus.add(equipe_id)
        try:
            equipe = service.obtenir(equipe_id)
        except NotFoundError:
            introuvables.append(str(equipe_id))
            continue
        fiches.append(_fiche_equipe(equipe))
    return {
        "equipes": fiches,
        "nb_equipes": len(fiches),
        "introuvables": introuvables,
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


def _disponibilites(session: Session, payload: dict[str, Any]) -> dict[str, Any]:
    """Disponibilité du vivier sur une période — lecture seule, règle partagée.

    Réutilise :meth:`AffectationService.conflits_disponibilite`, la **même**
    règle que celle qui refuse une proposition : l'agent ne peut donc pas
    annoncer « libre » quelqu'un que le serveur refusera. Une personne sans
    conflit est dite disponible ; une mission sans dates n'étant pas évaluable,
    la période est ici obligatoire.
    """
    entree = DisponibiliteInput.model_validate(payload)
    if entree.date_fin < entree.date_debut:
        raise BusinessValidationError(
            "Période incohérente : la date de fin précède la date de début.",
            details={
                "date_debut": str(entree.date_debut),
                "date_fin": str(entree.date_fin),
            },
        )
    if entree.equipe_ids:
        personnes = [EquipeService(session).obtenir(equipe_id) for equipe_id in entree.equipe_ids]
    else:
        personnes = list(
            session.scalars(select(Equipe).order_by(Equipe.nom, Equipe.prenom).limit(200))
        )
    service = AffectationService(session)
    resultats: list[dict[str, Any]] = []
    for personne in personnes:
        conflits = service.conflits_disponibilite(
            personne.id,
            entree.date_debut,
            entree.date_fin,
            mission_id=entree.mission_id,
        )
        resultats.append(
            {
                "equipe_id": str(personne.id),
                "nom_complet": " ".join(
                    part for part in (personne.prenom, personne.nom) if (part or "").strip()
                ),
                "disponible": not conflits,
                "conflits": conflits,
            }
        )
    disponibles = sum(1 for item in resultats if item["disponible"])
    return {
        "periode": {
            "date_debut": str(entree.date_debut),
            "date_fin": str(entree.date_fin),
        },
        "mission_id": str(entree.mission_id) if entree.mission_id else None,
        "nb_personnes": len(resultats),
        "nb_disponibles": disponibles,
        "nb_en_conflit": len(resultats) - disponibles,
        "personnes": resultats,
        "regle": (
            "Disponibilité calculée : affectations approuvées sur des missions "
            "non annulées dont les dates chevauchent la période. Une mission "
            "annulée ne bloque personne."
        ),
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
        date_debut=entree.date_debut,
        date_fin=entree.date_fin,
    )
    return {
        "mission_id": str(mission.id),
        "titre": mission.titre,
        "lieu_id": str(mission.lieu_id) if mission.lieu_id else None,
        "description": mission.description,
        "requires_approval": True,
        "message": "Mission mise à jour après votre approbation.",
    }


# --- Budgets de mission (Phase 2 §5, service BudgetService) ------------------
# Toute écriture passe par le service : calcul déterministe, machine à états
# (brouillon → proposé → approuvé), verrou du budget approuvé, AuditEvent
# dans la transaction courante. L'agent n'écrit jamais une ligne directement.


def _lister_budgets(session: Session, payload: dict[str, Any]) -> dict[str, Any]:
    """Budgets d'une mission, version la plus récente d'abord (lecture)."""
    entree = MissionBudgetInput.model_validate(payload)
    resumes = BudgetService(session, actor_id=AGENT_ID).lister_budgets_mission(
        entree.mission_id
    )
    return {
        "mission_id": str(entree.mission_id),
        "nb_budgets": len(resumes),
        "budgets": [
            {
                "budget_id": str(resume.budget_id),
                "offre_id": str(resume.offre_id) if resume.offre_id else None,
                "devise": resume.devise,
                "statut": resume.statut,
                "version": resume.version,
                "total": float(resume.total),
                "nb_lignes": resume.nb_lignes,
            }
            for resume in resumes
        ],
    }


def _lire_budget(session: Session, payload: dict[str, Any]) -> dict[str, Any]:
    """Détail d'un budget : entête, lignes ordonnées, total recalculé."""
    entree = BudgetIdInput.model_validate(payload)
    detail = BudgetService(session, actor_id=AGENT_ID).lire_budget(entree.budget_id)
    return {
        "budget_id": str(detail.budget_id),
        "offre_id": str(detail.offre_id) if detail.offre_id else None,
        "mission_id": str(detail.mission_id) if detail.mission_id else None,
        "devise": detail.devise,
        "statut": detail.statut,
        "version": detail.version,
        "total": float(detail.total),
        "lignes": [
            {
                "ligne_id": str(ligne.id),
                "categorie": ligne.categorie,
                "description": ligne.description,
                "unite": ligne.unite,
                "quantite": float(ligne.quantite),
                "cout_unitaire": float(ligne.cout_unitaire),
                "cout_total": float(ligne.cout_total),
                "ordre": ligne.ordre,
            }
            for ligne in detail.lignes
        ],
    }


def _creer_budget_mission(session: Session, payload: dict[str, Any]) -> dict[str, Any]:
    """Crée le budget (brouillon) de la mission — un seul par mission."""
    entree = MissionBudgetInput.model_validate(payload)
    service = BudgetService(session, actor_id=AGENT_ID)
    budget = service.creer_budget(mission_id=entree.mission_id, devise=entree.devise)
    return {
        "budget_id": str(budget.id),
        "mission_id": str(entree.mission_id),
        "devise": budget.devise,
        "statut": budget.statut,
        "version": budget.version,
        "requires_approval": True,
        "message": (
            "Budget créé en brouillon, sans ligne. Ajouter des lignes puis le "
            "soumettre : aucun montant n'est officiel tant que le budget n'est "
            "pas approuvé."
        ),
    }


def _ajouter_ligne_budget(session: Session, payload: dict[str, Any]) -> dict[str, Any]:
    """Ajoute une ligne : ``cout_total`` recalculé par le service (règle 7)."""
    entree = LigneBudgetToolInput.model_validate(payload)
    ligne = BudgetService(session, actor_id=AGENT_ID).ajouter_ligne_budget(
        entree.budget_id,
        LigneBudgetInput(
            categorie=entree.categorie,
            description=entree.description,
            unite=entree.unite,
            quantite=entree.quantite,
            cout_unitaire=entree.cout_unitaire,
        ),
    )
    return {
        "budget_id": str(entree.budget_id),
        "ligne_id": str(ligne.id),
        "categorie": ligne.categorie,
        "quantite": float(ligne.quantite),
        "cout_unitaire": float(ligne.cout_unitaire),
        "cout_total": float(ligne.cout_total),
        "requires_approval": True,
        "message": "Ligne ajoutée (coût total recalculé côté serveur).",
    }


def _modifier_ligne_budget(session: Session, payload: dict[str, Any]) -> dict[str, Any]:
    """Corrige une ligne existante (même calcul déterministe)."""
    entree = LigneBudgetModifInput.model_validate(payload)
    ligne = BudgetService(session, actor_id=AGENT_ID).modifier_ligne_budget(
        entree.budget_id,
        entree.ligne_id,
        LigneBudgetInput(
            categorie=entree.categorie,
            description=entree.description,
            unite=entree.unite,
            quantite=entree.quantite,
            cout_unitaire=entree.cout_unitaire,
        ),
    )
    return {
        "budget_id": str(entree.budget_id),
        "ligne_id": str(ligne.id),
        "categorie": ligne.categorie,
        "quantite": float(ligne.quantite),
        "cout_unitaire": float(ligne.cout_unitaire),
        "cout_total": float(ligne.cout_total),
        "requires_approval": True,
        "message": "Ligne modifiée (coût total recalculé côté serveur).",
    }


def _retirer_ligne_budget(session: Session, payload: dict[str, Any]) -> dict[str, Any]:
    """Retire une ligne d'un budget non approuvé (geste tracé).

    Le nom du tool est ``retirer_ligne_budget`` : la suppression de ligne est
    un **ajustement de contenu** (service : « retire une ligne »), pas un geste
    de cycle de vie — la matrice réserve « supprimer » aux gestes humains de
    mission/session. L'événement d'audit reste ``budget_ligne_supprimee``.
    """
    entree = LigneBudgetSuppressionInput.model_validate(payload)
    BudgetService(session, actor_id=AGENT_ID).supprimer_ligne_budget(
        entree.budget_id, entree.ligne_id
    )
    return {
        "budget_id": str(entree.budget_id),
        "ligne_id": str(entree.ligne_id),
        "requires_approval": True,
        "message": "Ligne supprimée du budget (geste tracé dans l'audit).",
    }


def _soumettre_budget(session: Session, payload: dict[str, Any]) -> dict[str, Any]:
    """``brouillon → proposé`` : le budget entre en file de décision humaine."""
    entree = BudgetIdInput.model_validate(payload)
    service = BudgetService(session, actor_id=AGENT_ID)
    budget = service.soumettre_budget(entree.budget_id)
    total = service.total_budget(budget.id)
    return {
        "budget_id": str(budget.id),
        "statut": budget.statut,
        "devise": budget.devise,
        "total": float(total.total),
        "nb_lignes": total.nb_lignes,
        "requires_approval": True,
        "message": "Budget soumis : sa décision d'approbation reste humaine.",
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
        description=(
            "Fiches de plusieurs personnes en un seul appel (`equipe_ids`) : "
            "identité, profil, statut, email (sans inventer de compétences). "
            "Un identifiant inconnu est listé dans `introuvables` sans faire "
            "échouer les autres fiches."
        ),
        input_schema=EquipesIdsInput,
        run=_get_equipes,
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
        name="verifier_disponibilite",
        description=(
            "Disponibilité CALCULÉE sur une période (lecture seule) : à appeler "
            "AVANT de classer ou de proposer, avec les dates de la mission. "
            "Renvoie pour chaque personne `disponible` et les missions en "
            "conflit (référence, titre, dates). Une personne indisponible ne "
            "doit pas être proposée : le serveur la refuserait."
        ),
        input_schema=DisponibiliteInput,
        run=_disponibilites,
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
            "description, dates de début et de fin) : lieu_id doit référencer un "
            "lieu existant ; les nouvelles dates sont reportées sur la session "
            "existante et les statuts se réalignent sur le nouveau calendrier. "
            "Aucune écriture directe, approbation humaine requise."
        ),
        input_schema=MissionUpdateInput,
        mutation=True,
        run=_propose_mission_update,
    ))
    # --- Budgets de mission (Phase 2 §5) -----------------------------------
    # Lecture : lister/lire. Écritures (créer, lignes, soumettre) : toutes
    # sous HITL (``approval_required`` du contrat). Un budget approuvé est
    # verrouillé par le service : une révision = nouvelle version, jamais une
    # réécriture des montants validés.
    kit.enregistrer(OperationSpec(
        name="lister_budgets_mission",
        description=(
            "Budgets d'une mission (versions, statuts, totaux recalculés), la "
            "version la plus récente d'abord. À lire avant toute écriture."
        ),
        input_schema=MissionBudgetInput,
        run=_lister_budgets,
    ))
    kit.enregistrer(OperationSpec(
        name="lire_budget",
        description=(
            "Détail d'un budget : lignes ordonnées (catégorie, quantité, coût "
            "unitaire, coût total) et total recalculé."
        ),
        input_schema=BudgetIdInput,
        run=_lire_budget,
    ))
    kit.enregistrer(OperationSpec(
        name="creer_budget_mission",
        description=(
            "Crée le budget (brouillon) d'une mission. Un seul budget par "
            "mission : une révision passe par une nouvelle version, jamais par "
            "un second budget. Aucun montant n'est officiel tant que le budget "
            "n'est pas approuvé."
        ),
        input_schema=MissionBudgetInput,
        mutation=True,
        run=_creer_budget_mission,
    ))
    kit.enregistrer(OperationSpec(
        name="ajouter_ligne_budget",
        description=(
            "Ajoute une ligne à un budget brouillon ou proposé : catégorie "
            "(obligatoire), description, unité, quantité et coût unitaire (≥ 0). "
            "Le coût total est TOUJOURS recalculé côté serveur : jamais fourni "
            "par l'agent."
        ),
        input_schema=LigneBudgetToolInput,
        mutation=True,
        run=_ajouter_ligne_budget,
    ))
    kit.enregistrer(OperationSpec(
        name="modifier_ligne_budget",
        description=(
            "Corrige une ligne de budget non approuvé (même recalcul "
            "déterministe du coût total)."
        ),
        input_schema=LigneBudgetModifInput,
        mutation=True,
        run=_modifier_ligne_budget,
    ))
    kit.enregistrer(OperationSpec(
        name="retirer_ligne_budget",
        description=(
            "Retire une ligne d'un budget non approuvé (geste tracé). Le "
            "budget et la ligne sont référencés par identifiant."
        ),
        input_schema=LigneBudgetSuppressionInput,
        mutation=True,
        run=_retirer_ligne_budget,
    ))
    kit.enregistrer(OperationSpec(
        name="soumettre_budget",
        description=(
            "Soumet un budget (brouillon → proposé) : il entre en file de "
            "décision humaine. Un budget sans ligne est refusé — jamais un "
            "chiffrage vide."
        ),
        input_schema=BudgetIdInput,
        mutation=True,
        run=_soumettre_budget,
    ))
    # Autres tables : lecture seule (matrice validée).
    kit.enregistrer(OperationSpec(
        name="search_organisations",
        description="Organisations clientes par nom (lecture seule).",
        input_schema=PageSearchInput,
        run=rechercher_organisations,
    ))
    kit.enregistrer(OperationSpec(
        name="search_appels",
        description="Appels à proposition (organisation, statut, texte) + lots.",
        input_schema=PageSearchInput,
        run=rechercher_appels,
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

    from app.agents.tools.agent_lieux_tools import construire_tools_lieux
    from app.agents.tools.connaissances import outils_connaissance_pour
    from app.tools.recherche_video import attacher_recherche_video
    from app.tools.web import attacher_outils_web

    return attacher_recherche_video(
        definition,
        attacher_outils_web(
            definition,
            tuple(kit.construire())
            + extra_tools
            + construire_tools_lieux(definition, session_factory)
            + outils_connaissance_pour(definition, session_factory),
        ),
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
    "BudgetIdInput",
    "DisponibiliteInput",
    "EnvoyerEmailInput",
    "LigneBudgetModifInput",
    "LigneBudgetSuppressionInput",
    "LigneBudgetToolInput",
    "MissionBudgetInput",
    "construire_tools_rh",
]
