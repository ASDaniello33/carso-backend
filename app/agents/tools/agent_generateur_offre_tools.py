"""Tools de ``agent_generateur_offre`` (clinrules 02, refactor outils).

Lieu unique du périmètre outils du générateur—**agent unique de la chaîne
de l'appel**—réception (appel à proposition ou manifestation d'intérêt),
analyse documentaire, extraction des lots, sélection, offres par type,
documents. L'ancien agent analyseur d'appel à proposition a été supprimé :
ses outils de lecture et d'extraction vivent désormais ici (aucun doublon,
``list_appel_a_proposition_documents`` étant fusionné dans ``list_appel_documents``).

Matrice d'accès (validée) :

- **Read/Write (écritures sous HITL)**—``Document``, ``Appel``,
  ``Lot``, ``Offre`` ;
- **Lecture seule**—les autres tables (organisations, missions…).

Écritures—toute mutation passe par un service applicatif qui exige une
décision humaine (zone *proposal*, ``Decision``) et le tool est déclaré dans
``AgentDefinition.approval_required`` (interrupt LangGraph).
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agents.consultation import (
    rechercher_appels,
    rechercher_documents,
    rechercher_equipes,
    rechercher_missions,
    rechercher_organisations,
)
from app.agents.extraction.schemas import ExtractionAppel
from app.agents.toolkit import AgentTools, OperationSpec, PageSearchInput
from app.agents.tools.agent_generaliste_tools import (
    AffectationSearchInput,
    BeneficiaireSearchInput,
    OffreSearchInput,
    SessionSearchInput,
    rechercher_affectations,
    rechercher_beneficiaires,
    rechercher_offres,
    rechercher_sessions,
)
from app.application.dto import AppelInput, LieuInput, LotUpdateInput
from app.application.services import (
    AppelService,
    DocumentService,
    LieuService,
    LotService,
    ModeleDocumentService,
    OffreService,
    OrganisationService,
)
from app.core.config import get_settings
from app.core.errors import ValidationError
from app.domain.document import Document
from app.domain.enums import StatutAppel, TypeAppel, TypeDocument
from app.domain.execution import Lieu
from app.domain.organization import Appel, Lot

AGENT_ID = "agent_generateur_offre"
TASK_TYPE = "preparer_offre"

NOMS_TOOLS: tuple[str, ...] = (
    "enregistrer_appel",
    "situer_zone_lot",
    "rechercher_lieu",
    "calculer_itineraire",
    "rattacher_document_appel",
    "read_appel",
    "read_document_text",
    "submit_rfp_extraction",
    "get_lot",
    "get_organization",
    "get_document_template",
    "create_offer_draft",
    "list_lots",
    "list_appel_documents",
    "propose_lot_update",
    "propose_appel_update",
    "search_organisations",
    "search_appels",
    "search_offres",
    "search_missions",
    "search_sessions",
    "search_beneficiaires",
    "search_equipes",
    "search_affectations",
    "search_documents",
    "lire_document",
    "read_document",
    "generer_document",
    "generer_document_professionnel",
    "generer_document_html",
    "valider_document",
    "render_document",
    "corriger_document",
    "analyze_document_reference",
    "importer_tableau_xlsx",
    "fill_template",
    "clone_document_structure",
    "get_document_structure",
    "read_document_range",
    "chercher_dans_document",
    "lire_element_document",
    # Un appel scanné (photo, capture) se lit dans l'image.
    "lire_image",
    "langsearch_web_search",
    # Lecture d'une page trouvée : le texte réel d'un appel ou d'un cahier des
    # charges est lu dans la source, borné, jamais reconstitué de mémoire.
    "fetch_url",
    "read_urls",
    # Recherche de vidéos (module ``app.tools.recherche_video``) : la surface de
    # recherche externe est la **même pour tous les agents** (capacité
    # ``web_search``). Un document ou une image se cherchent, eux, dans
    # ``langsearch_web_search`` avec l'opérateur ``filetype:``.
    "rechercher_video",
    "request_agent_task",
    "get_agent_task_result",
)

# --- Schémas d'entrée ----------------------------------------------------------


class LotIdInput(BaseModel):
    lot_id: UUID


class SituerZoneInput(BaseModel):
    """Zone d'un lot à placer sur la carte (HITL).

    ``recherche`` permet de préciser (« Toliara, Madagascar ») quand la zone du
    document est trop vague ; à défaut, c'est la zone du lot qui est cherchée.
    Aucune coordonnée n'est fournie par le modèle : c'est le service qui la
    trouve, et elle est refusée si rien ne correspond.
    """

    lot_id: UUID
    recherche: str | None = Field(default=None, max_length=255)


class OrganisationIdInput(BaseModel):
    organisation_id: UUID


class TemplateInput(BaseModel):
    organisation_id: UUID
    type_document: str = Field(default=TypeDocument.OFFRE.value)


class OffreBrouillonInput(BaseModel):
    """Brouillon d'offre : ``type`` explicite (technique / financière / autre)."""

    lot_id: UUID
    type: str = Field(
        description="Type d'offre : 'offre_technique' | 'offre_financiere' | 'autre'."
    )
    reference: str = Field(min_length=1, max_length=100)
    titre: str = Field(min_length=1, max_length=255)
    modele_document_id: UUID | None = None


class LotUpdateInput(BaseModel):
    """Modification de lot : proposition (HITL) — aucun changement direct."""

    lot_id: UUID
    titre: str | None = Field(default=None, min_length=1, max_length=255)
    zone: str | None = Field(default=None, min_length=1, max_length=255)
    objectifs: str | None = Field(default=None, max_length=4000)


class AppelIdInput(BaseModel):
    """Identifiant d'un appel (lectures bornées)."""

    appel_id: UUID


class AppelUpdateInput(BaseModel):
    """Modification de l'appel : proposition (HITL) — statut via workflow API."""

    appel_id: UUID
    titre: str | None = Field(default=None, min_length=1, max_length=255)


class DocumentIdInput(BaseModel):
    """Document dont on veut le texte extrait (pièce jointe ou document de l'appel)."""

    document_id: UUID


class ExtractionProposeeInput(BaseModel):
    """Proposition d'extraction d'un appel, soumise à validation humaine (HITL).

    ``extraction`` est typée par le schéma de sortie (``ExtractionAppel``)
    et non ``Any`` : une chaîne JSON sérialisée est tolérée (les LLM sérialisent
    parfois malgré le schéma du tool) mais un type non objet est refusé à la
    frontière avec une erreur actionnable — jamais avalée en ``(racine)``.
    """

    appel_id: UUID
    extraction: ExtractionAppel = Field(
        description=(
            "Données extraites du document source, structurées "
            "(schéma ExtractionAppel : organisation, resume, lots[], extra). "
            "Aucune valeur absente du document n'est inventée."
        )
    )

    @field_validator("extraction", mode="wrap")
    @classmethod
    def _accepter_extraction_json(cls, valeur: Any, handler: Any) -> Any:
        """Parse une proposition sérialisée en chaîne JSON (tolérance LLM)."""
        if isinstance(valeur, str):
            try:
                valeur = json.loads(valeur)
            except json.JSONDecodeError as erreur:
                raise ValueError(
                    "extraction doit être un objet (ou une chaîne JSON valide), "
                    f"texte non décodable reçu : {erreur.msg[:80]}"
                ) from erreur
        return handler(valeur)


class AppelCreateInput(BaseModel):
    """Enregistrement d'un appel reçu (HITL) — aucune valeur inventée.

    ``type`` est **obligatoire** : la nature de l'appel n'est jamais déduite
    d'un autre champ (règle confirmée CARSO).
    """

    organisation_id: UUID = Field(
        description="Organisation émettrice, déjà enregistrée (jamais créée ici)."
    )
    reference: str = Field(min_length=1, max_length=100)
    titre: str = Field(min_length=1, max_length=255)
    type: str = Field(
        description=(
            "Nature de l'appel : 'appel_a_proposition', "
            "'appel_a_manifestation_interet' ou 'autre'."
        )
    )
    description: str | None = Field(default=None, max_length=4000)
    date_reception: date | None = None
    date_limite: date | None = None
    document_source_id: UUID | None = Field(
        default=None,
        description=(
            "Document source de l'appel (pièce jointe du chat), rattaché à "
            "l'appel une fois celui-ci créé."
        ),
    )


class RattachementDocumentInput(BaseModel):
    """Rattachement d'un document déjà déposé à l'appel qu'il concerne (HITL)."""

    document_id: UUID
    appel_id: UUID


# --- Requêtes nommées (read) ---------------------------------------------------


def _get_lot(session: Session, payload: dict[str, Any]) -> dict[str, Any]:
    lot = LotService(session).obtenir(UUID(str(payload["lot_id"])))
    return {
        "id": str(lot.id),
        "numero": lot.numero,
        "titre": lot.titre,
        "appel_id": str(lot.appel_id),
        "zone": lot.zone,
        "objectifs": lot.objectifs,
    }


def _situer_zone_lot(session: Session, payload: dict[str, Any]) -> dict[str, Any]:
    """Place la zone d'un lot sur la carte : lieu réutilisé, sinon géocodé.

    La zone vient du **document** (« Zone : Atsimo-Andrefana ») ; sa position
    vient de la recherche d'adresses, jamais d'une coordonnée inventée par le
    modèle. Un lieu du même nom déjà connu est réutilisé, pour ne pas créer un
    doublon à chaque appel.
    """
    service_lots = LotService(session, actor_id=AGENT_ID)
    lot = service_lots.obtenir(UUID(str(payload["lot_id"])))
    requete = (payload.get("recherche") or lot.zone or "").strip()
    if not requete:
        raise ValidationError(
            "Aucune zone à situer : le lot n'a pas de zone, précisez une recherche.",
            details={"lot_id": str(lot.id)},
        )

    lieu = session.scalar(select(Lieu).where(func.lower(Lieu.nom) == requete.lower()))
    cree = False
    if lieu is None:
        from app.geo.photon import RechercheLieux

        reglages = get_settings()
        trouves = RechercheLieux(
            reglages.photon_base_url, timeout_s=reglages.photon_timeout_s
        ).chercher(requete, limite=1)
        if not trouves:
            raise ValidationError(
                f"Aucun lieu trouvé pour « {requete} » : précisez la ville ou le pays.",
                details={"lot_id": str(lot.id), "recherche": requete},
            )
        trouve = trouves[0]
        lieu = LieuService(session, actor_id=AGENT_ID).creer(
            LieuInput(
                nom=requete,
                adresse=trouve.adresse or trouve.libelle,
                ville=trouve.ville,
                pays=trouve.pays,
                zone=lot.zone,
                latitude=trouve.latitude,
                longitude=trouve.longitude,
            )
        )
        cree = True

    if lot.lieu_id != lieu.id:
        service_lots.modifier(lot.id, LotUpdateInput(lieu_id=lieu.id))

    return {
        "lot_id": str(lot.id),
        "numero": lot.numero,
        "lieu_id": str(lieu.id),
        "nom": lieu.nom,
        "latitude": lieu.latitude,
        "longitude": lieu.longitude,
        "cree": cree,
        "message": (
            f"Zone du lot {lot.numero} située sur la carte ({lieu.nom}). "
            "Les autres données du lot sont inchangées."
        ),
    }


def _get_organization(session: Session, payload: dict[str, Any]) -> dict[str, Any]:
    org = OrganisationService(session).obtenir(UUID(str(payload["organisation_id"])))
    return {"id": str(org.id), "nom": org.nom, "type": org.type}


def _get_template(session: Session, payload: dict[str, Any]) -> dict[str, Any]:
    modele = ModeleDocumentService(session).resoudre_modele_actif(
        UUID(str(payload["organisation_id"])),
        payload.get("type_document") or TypeDocument.OFFRE_FORMATION.value,
    )
    return {
        "id": str(modele.id),
        "nom": modele.nom,
        "version": modele.version,
        "document_template_id": str(modele.document_template_id),
        "type_document": modele.type_document,
    }


def _list_lots(session: Session, payload: dict[str, Any]) -> dict[str, Any]:
    """Lots d'un appel : permet de sélectionner sans lire tout l'appel."""
    appel_id = UUID(str(payload["appel_id"]))
    if session.get(Appel, appel_id) is None:
        raise ValidationError(f"Appel {appel_id} introuvable")
    lots = list(
        session.scalars(
            select(Lot)
            .where(Lot.appel_id == appel_id)
            .order_by(Lot.numero)
        )
    )
    return {
        "lots": [
            {
                "id": str(lot.id),
                "numero": lot.numero,
                "titre": lot.titre,
                "zone": lot.zone,
                "objectifs": lot.objectifs,
            }
            for lot in lots
        ]
    }


def _list_appel_documents(session: Session, payload: dict[str, Any]) -> dict[str, Any]:
    """Documents rattachés à un appel (métadonnées, jamais le chemin disque)."""
    appel_id = UUID(str(payload["appel_id"]))
    docs = list(
        session.scalars(
            select(Document)
            .where(Document.appel_id == appel_id)
            .order_by(Document.version.desc())
        )
    )
    return {
        "documents": [
            {
                "document_id": str(d.id),
                "nom": d.nom,
                "type_document": d.type_document,
                "mime_type": d.mime_type,
                "version": d.version,
                "statut": d.statut,
            }
            for d in docs
        ]
    }


def _statut(entite: Any) -> str:
    """Statut sous forme de chaîne (les colonnes sont stockées en ``String``)."""
    statut = entite.statut
    return statut.value if hasattr(statut, "value") else str(statut)


def _read_appel(session: Session, payload: dict[str, Any]) -> dict[str, Any]:
    """Métadonnées de l'appel (jamais le contenu du document, jamais un chemin disque)."""
    appel = AppelService(session, actor_id=AGENT_ID).obtenir(
        UUID(str(payload["appel_id"]))
    )
    return {
        "appel_id": str(appel.id),
        "reference": appel.reference,
        "titre": appel.titre,
        "type": appel.type,
        "statut": _statut(appel),
        "organisation_id": str(appel.organisation_id),
        "date_reception": appel.date_reception.isoformat() if appel.date_reception else None,
        "date_limite": appel.date_limite.isoformat() if appel.date_limite else None,
        "a_une_proposition": appel.donnees_extraites is not None,
        # Ce qui manque pour continuer : l'agent voit d'un coup d'œil qu'un
        # appel validé est resté sans lot et peut le dire, au lieu d'affirmer
        # qu'un lot existe ou de proposer une re-analyse impossible.
        **etat_lots(session, appel),
    }


def etat_lots(session: Session, appel: Appel) -> dict[str, Any]:
    """État des lots d'un appel : officiels, proposés, et statut figé ou non.

    Lecture seule. ``proposition_sans_lot`` est le signal que la validation a
    eu lieu sans rien officialiser — l'appel ne peut plus avancer par la voie
    normale.
    """
    officiels = list(
        session.scalars(
            select(Lot)
            .where(Lot.appel_id == appel.id)
            .order_by(Lot.numero)
        )
    )
    proposition = appel.donnees_extraites or {}
    proposes = proposition.get("lots")
    nb_proposes = len(proposes) if isinstance(proposes, list) else 0
    valide = appel.statut == StatutAppel.VALIDE.value
    return {
        "nb_lots_officiels": len(officiels),
        "numeros_lots_officiels": [lot.numero for lot in officiels],
        "nb_lots_proposes": nb_proposes,
        "statut_fige": valide,
        "proposition_sans_lot": valide and not officiels and nb_proposes == 0,
    }


def _read_document_text(session: Session, payload: dict[str, Any]) -> dict[str, Any]:
    """Texte extrait d'un document (PDF/DOCX/XLSX/texte) — lecture seule."""
    extrait = DocumentService(session, actor_id=AGENT_ID).extraire_texte(
        UUID(str(payload["document_id"]))
    )
    return {
        "document_id": str(extrait.document_id),
        "nom": extrait.nom,
        "extension": extrait.extension,
        "texte": extrait.texte,
        "nb_pages": extrait.nb_pages,
        "tronque": extrait.tronque,
    }


# --- Opérations d'écriture (HITL : approval_required) ---------------------------


def _create_offer_draft(session: Session, payload: dict[str, Any]) -> dict[str, Any]:
    """Brouillon d'offre depuis un lot officiel (HITL).

    Le service refuse uniquement un appel ``rejete``. Un lot saisi à la main
    sur un appel encore ``recu`` est déjà officiel : l'extraction n'est pas
    un prérequis.
    """
    entree = OffreBrouillonInput.model_validate(payload)
    offre = OffreService(session, actor_id=AGENT_ID).creer_brouillon(
        entree.lot_id,
        entree.reference,
        entree.titre,
        type=entree.type,
        modele_document_id=entree.modele_document_id,
    )
    return {
        "offre_id": str(offre.id),
        "type": offre.type,
        "statut": offre.statut,
        "requires_approval": True,
        "message": "Brouillon créé. Non officiel tant qu'il n'est pas publié.",
    }


def _propose_lot_update(session: Session, payload: dict[str, Any]) -> dict[str, Any]:
    """Applique une correction de lot après HITL (LangGraph interrupt avant run)."""
    from app.application.dto import LotUpdateInput as LotUpdateDto

    entree = LotUpdateInput.model_validate(payload)
    lot = LotService(session, actor_id=AGENT_ID).modifier(
        entree.lot_id,
        LotUpdateDto(
            titre=entree.titre,
            zone=entree.zone,
            objectifs=entree.objectifs,
        ),
    )
    return {
        "lot_id": str(lot.id),
        "titre": lot.titre,
        "zone": lot.zone,
        "objectifs": lot.objectifs,
        "requires_approval": True,
        "message": "Lot mis à jour après votre approbation.",
    }


def _propose_appel_update(session: Session, payload: dict[str, Any]) -> dict[str, Any]:
    """Applique une correction de l'appel après HITL."""
    from app.application.services import AppelService

    entree = AppelUpdateInput.model_validate(payload)
    appel = AppelService(session, actor_id=AGENT_ID).modifier_titre(
        entree.appel_id,
        titre=entree.titre,
    )
    return {
        "appel_id": str(appel.id),
        "titre": appel.titre,
        "requires_approval": True,
        "message": "Appel mis à jour après votre approbation.",
    }


def _enregistrer_appel(session: Session, payload: dict[str, Any]) -> dict[str, Any]:
    """Enregistre l'appel reçu (proposition ou manifestation d'intérêt) — HITL.

    L'organisation doit déjà exister : l'agent ne crée jamais d'organisation ni
    de référence. Le document source, s'il est fourni, est rattaché dans la
    même transaction (dossier ``appel_proposition/{id}/``).
    """
    entree = AppelCreateInput.model_validate(payload)
    if entree.type not in {type_.value for type_ in TypeAppel}:
        raise ValidationError(
            f"Type d'appel inconnu : {entree.type!r}",
            details={"types_valides": sorted(type_.value for type_ in TypeAppel)},
        )

    appel = AppelService(session, actor_id=AGENT_ID).enregistrer(
        AppelInput(
            organisation_id=entree.organisation_id,
            reference=entree.reference,
            titre=entree.titre,
            description=entree.description,
            date_reception=entree.date_reception,
            date_limite=entree.date_limite,
            type=entree.type,
        )
    )

    document_source: dict[str, Any] | None = None
    if entree.document_source_id is not None:
        document = DocumentService(session, actor_id=AGENT_ID).rattacher_document(
            entree.document_source_id, appel_id=appel.id
        )
        document_source = {
            "document_id": str(document.id),
            "nom": document.nom,
            "storage_path": document.storage_path,
        }

    return {
        "appel_id": str(appel.id),
        "reference": appel.reference,
        "titre": appel.titre,
        "type": appel.type,
        "statut": _statut(appel),
        "date_reception": (
            appel.date_reception.isoformat() if appel.date_reception else None
        ),
        "date_limite": appel.date_limite.isoformat() if appel.date_limite else None,
        "document_source": document_source,
        "requires_approval": True,
        "message": (
            "Appel enregistré au statut 'recu'. Aucune donnée n'est extraite ni "
            "officielle à ce stade : l'analyse vient ensuite."
        ),
    }


def _rattacher_document_appel(session: Session, payload: dict[str, Any]) -> dict[str, Any]:
    """Rattache un document déjà déposé à l'appel qu'il concerne (HITL)."""
    entree = RattachementDocumentInput.model_validate(payload)
    document = DocumentService(session, actor_id=AGENT_ID).rattacher_document(
        entree.document_id,
        appel_id=entree.appel_id,
    )
    return {
        "document_id": str(document.id),
        "nom": document.nom,
        "type_document": document.type_document,
        "storage_path": document.storage_path,
        "appel_id": str(entree.appel_id),
        "requires_approval": True,
        "message": "Document source rattaché à l'appel.",
    }


def _soumettre_extraction(session: Session, payload: dict[str, Any]) -> dict[str, Any]:
    """Soumet une proposition d'extraction (zone *proposal*, HITL).

    Sur un appel **déjà validé**, la soumission emprunte le chemin de
    réparation : l'appel revient en ``propose`` avec la nouvelle proposition,
    au lieu de lever un conflit de transition qui laissait l'utilisateur sans
    issue. Refusé si l'appel a déjà des offres (la proposition n'est alors
    plus modifiable).
    """
    entree = ExtractionProposeeInput.model_validate(payload)
    extraction = entree.extraction
    service = AppelService(session, actor_id=AGENT_ID)
    appel = service.obtenir(entree.appel_id)
    if appel.statut == StatutAppel.VALIDE.value:
        appel = service.enregistrer_proposition_corrigee(
            entree.appel_id,
            extraction.model_dump(mode="json"),
            proposed_by_agent=AGENT_ID,
        )
        message = (
            "Proposition remplacée sur un appel déjà validé : l'appel revient "
            "en attente de votre décision. Il n'a produit aucune offre, sa "
            "proposition reste donc modifiable."
        )
    else:
        appel = service.enregistrer_extraction(
            entree.appel_id,
            extraction.model_dump(mode="json"),
            proposed_by_agent=AGENT_ID,
        )
        message = (
            "Proposition enregistrée en zone de validation humaine. "
            "Elle n'est pas officielle : les lots officiels naissent de votre "
            "approbation."
        )
    return {
        "appel_id": str(appel.id),
        "statut": _statut(appel),
        "requires_approval": True,
        "nb_lots": len(extraction.lots),
        **etat_lots(session, appel),
        "message": message,
    }


def construire_tools_generateur_offre(
    definition: Any,
    session_factory: Any,
    *,
    extra_tools: tuple[Any, ...] = (),
) -> tuple[Any, ...]:
    """Tools du générateur : kit CRUD borné (écritures HITL) + documents + A2A.

    Les tools de collaboration (``request_agent_task`` /
    ``get_agent_task_result``) et documentaires (``fill_template``…) sont
    ajoutés par l'appelant via ``extra_tools`` — le harnais applique
    l'allow-list du contrat.
    """
    kit = AgentTools(definition, session_factory=session_factory)
    # --- Réception de l'appel (proposition ou manifestation d'intérêt) -----
    kit.enregistrer(OperationSpec(
        name="enregistrer_appel",
        description=(
            "Enregistre un appel reçu (appel à proposition ou appel à "
            "manifestation d'intérêt) pour une organisation déjà connue, et "
            "rattache son document source (HITL). Le type est explicite ; "
            "aucune organisation, référence ou date n'est inventée."
        ),
        input_schema=AppelCreateInput,
        mutation=True,
        run=_enregistrer_appel,
    ))
    kit.enregistrer(OperationSpec(
        name="rattacher_document_appel",
        description=(
            "Rattache un document déjà déposé (pièce jointe non classée) à "
            "l'appel qu'il concerne : le fichier rejoint "
            "``appel_proposition/{id}/`` (HITL)."
        ),
        input_schema=RattachementDocumentInput,
        mutation=True,
        run=_rattacher_document_appel,
    ))
    # --- Analyse documentaire et extraction (ex-outils de l'analyseur) -----
    kit.enregistrer(OperationSpec(
        name="read_appel",
        description=(
            "Lit les métadonnées d'un appel (référence, titre, "
            "type, statut, échéances, présence d'une proposition antérieure). "
            "Lecture seule."
        ),
        input_schema=AppelIdInput,
        run=_read_appel,
    ))
    kit.enregistrer(OperationSpec(
        name="read_document_text",
        description=(
            "Retourne le texte extrait d'un document (PDF/DOCX/XLSX/texte). "
            "Lecture seule : aucun contenu n'est modifié ni persisté."
        ),
        input_schema=DocumentIdInput,
        run=_read_document_text,
    ))
    kit.enregistrer(OperationSpec(
        name="submit_rfp_extraction",
        description=(
            "Soumet la proposition d'extraction structurée de l'appel "
            "(organisation, résumé, lots) pour validation humaine. "
            "N'officialise aucune donnée : les lots officiels naissent de "
            "l'approbation humaine."
        ),
        input_schema=ExtractionProposeeInput,
        mutation=True,
        run=_soumettre_extraction,
    ))
    kit.enregistrer(OperationSpec(
        name="get_lot",
        description="Lit un lot (numéro, titre, zone, objectifs).",
        input_schema=LotIdInput,
        run=_get_lot,
    ))
    kit.enregistrer(OperationSpec(
        name="situer_zone_lot",
        description=(
            "Crée (ou réutilise) le lieu de la zone d'un lot et le rattache, "
            "pour que la zone apparaisse sur la carte de l'appel. La position "
            "vient de la recherche d'adresses, jamais d'une coordonnée inventée "
            "(HITL)."
        ),
        input_schema=SituerZoneInput,
        mutation=True,
        run=_situer_zone_lot,
    ))
    kit.enregistrer(OperationSpec(
        name="get_organization",
        description="Lit une organisation cliente.",
        input_schema=OrganisationIdInput,
        run=_get_organization,
    ))
    kit.enregistrer(OperationSpec(
        name="get_document_template",
        description="Modèle de document actif de l'organisation (404 si absent).",
        input_schema=TemplateInput,
        run=_get_template,
    ))
    kit.enregistrer(OperationSpec(
        name="list_lots",
        description="Liste les lots d'un appel (sélection utilisateur).",
        input_schema=AppelIdInput,
        run=_list_lots,
    ))
    kit.enregistrer(OperationSpec(
        name="list_appel_documents",
        description=(
            "Documents rattachés à un appel (métadonnées, jamais le chemin "
            "disque) — une seule implémentation pour l'agent générateur."
        ),
        input_schema=AppelIdInput,
        run=_list_appel_documents,
    ))
    kit.enregistrer(OperationSpec(
        name="create_offer_draft",
        description=(
            "Brouillon d'offre depuis un lot officiel (HITL). Un lot saisi "
            "manuellement suffit : l'appel n'a pas besoin d'être au statut "
            "valide. Un appel rejeté bloque la création."
        ),
        input_schema=OffreBrouillonInput,
        mutation=True,
        run=_create_offer_draft,
    ))
    kit.enregistrer(OperationSpec(
        name="propose_lot_update",
        description=(
            "Propose une correction de lot (titre, zone, objectifs) : aucune "
            "écriture directe, l'approbation humaine déclenche l'application."
        ),
        input_schema=LotUpdateInput,
        mutation=True,
        run=_propose_lot_update,
    ))
    kit.enregistrer(OperationSpec(
        name="propose_appel_update",
        description=(
            "Propose une correction de l'appel (titre) : aucune "
            "écriture directe, l'approbation humaine déclenche l'application."
        ),
        input_schema=AppelUpdateInput,
        mutation=True,
        run=_propose_appel_update,
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
        name="search_offres",
        description="Offres de formation (statut, organisation) — lecture seule.",
        input_schema=OffreSearchInput,
        run=rechercher_offres,
    ))
    kit.enregistrer(OperationSpec(
        name="search_missions",
        description="Missions (statut, organisation, texte) — lecture seule.",
        input_schema=PageSearchInput,
        run=rechercher_missions,
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
        name="search_equipes",
        description="Vivier (nom, prénom, profil). Contenu des CV exclu.",
        input_schema=PageSearchInput,
        run=rechercher_equipes,
    ))
    kit.enregistrer(OperationSpec(
        name="search_affectations",
        description="Affectations d'une mission — lecture seule.",
        input_schema=AffectationSearchInput,
        run=rechercher_affectations,
    ))
    kit.enregistrer(OperationSpec(
        name="search_documents",
        description="Documents (une ancre métier, type). Métadonnées seules.",
        input_schema=PageSearchInput,
        run=rechercher_documents,
    ))

    from app.tools.recherche_video import attacher_recherche_video
    from app.tools.web import attacher_outils_web

    from app.agents.tools.agent_lieux_tools import construire_tools_lieux

    return attacher_recherche_video(
        definition,
        attacher_outils_web(
            definition,
            tuple(kit.construire())
            + extra_tools
            + construire_tools_lieux(definition, session_factory),
        ),
    )


__all__ = [
    "AGENT_ID",
    "NOMS_TOOLS",
    "TASK_TYPE",
    "construire_tools_generateur_offre",
    "OffreBrouillonInput",
    "SituerZoneInput",
]
