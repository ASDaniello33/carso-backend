"""Routes d'administration — paramètres par agent, skills, connecteurs MCP.

Réservé aux administrateurs (``AdminUser``). Le runtime (provider, modèle, clé)
garde ses routes dédiées sous ``/agents/runtime`` : cette page ne les duplique
pas, elle les complète avec ce qui n'existe que **par agent**.

Chaque écriture se termine par un rechargement du runtime : un réglage enregistré
mais non appliqué serait un mensonge. Le rechargement journalise les agents
reconstruits, donc l'effet du changement reste auditable.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.agents.catalogue import AGENT_DEFINITIONS
from app.agents.manager import get_runtime_manager
from app.agents.providers import SUPPORTED_PROVIDERS
from app.agents.skills import lister_skills, racine_skills
from app.api.deps import AdminUser, DbSession, get_current_user
from app.api.schemas.parametres import (
    AgentParametrableRead,
    ParametresEtatRead,
    ServeurMcpRead,
    ServeurMcpUpdate,
    SkillsDisponiblesRead,
    SurchargeAgentRead,
    SurchargeAgentUpdate,
)
from app.application.services import AgentRuntimeService
from app.application.services.parametres_service import (
    ParametresAgentService,
    ServeurMcpService,
)
from app.core.chiffrement import chiffrement_disponible
from app.domain.agent_runtime import SurchargeAgent
from app.domain.mcp import ServeurMcp

router = APIRouter(
    prefix="/parametres",
    tags=["parametres"],
    dependencies=[Depends(get_current_user)],
)


# --- Projections --------------------------------------------------------------


def _surcharge_read(ligne: SurchargeAgent | None, agent_id: str) -> SurchargeAgentRead:
    """Surcharge (ou contrat vierge) projetée pour l'API."""
    if ligne is None:
        return SurchargeAgentRead(agent_id=agent_id, actif=False)
    return SurchargeAgentRead(
        agent_id=ligne.agent_id,
        outils_desactives=list(ligne.outils_desactives or []),
        instructions_supplementaires=ligne.instructions_supplementaires,
        skills_ajoutes=list(ligne.skills_ajoutes or []),
        actif=bool(ligne.actif),
        modifie_par=ligne.modifie_par,
        updated_at=ligne.updated_at,
    )


def _serveur_read(serveur: ServeurMcp, noms_en_tetes: list[str]) -> ServeurMcpRead:
    """Connecteur projeté : jamais la valeur d'un en-tête, seulement son nom."""
    return ServeurMcpRead(
        id=serveur.id,
        nom=serveur.nom,
        transport=serveur.transport,
        url=serveur.url,
        commande=serveur.commande,
        arguments=list(serveur.arguments or []),
        agents=list(serveur.agents or []),
        en_tetes=noms_en_tetes,
        actif=bool(serveur.actif),
        modifie_par=serveur.modifie_par,
        updated_at=serveur.updated_at,
    )


# --- État complet (ouverture de la page) --------------------------------------


@router.get("/etat", response_model=ParametresEtatRead)
def etat_parametres(admin: AdminUser, session: DbSession) -> ParametresEtatRead:
    """Tout ce que l'écran doit afficher, en une requête.

    ``chiffrement_disponible`` est exposé pour que l'interface explique **avant**
    la saisie pourquoi un secret serait refusé, plutôt qu'après un 422.
    """
    _ = admin
    surcharges = ParametresAgentService(session).effectives()
    mcp_service = ServeurMcpService(session)
    connecteurs = mcp_service.lister()

    agents: list[AgentParametrableRead] = []
    for definition in AGENT_DEFINITIONS:
        surcharge = surcharges.get(definition.agent_id)
        retires = list(surcharge.outils_desactives) if surcharge else []
        skills_contrat = list(definition.skills)
        skills_ajoutes = list(surcharge.skills_ajoutes) if surcharge else []
        agents.append(
            AgentParametrableRead(
                agent_id=definition.agent_id,
                display_name=definition.display_name,
                description=definition.description,
                # Un agent hors catalogue AG-UI est interne (appelé par un autre agent) :
        # l'écran le dit, pour qu'on ne cherche pas son onglet de conversation.
        expose_ui=definition.agent_id in agents_exposes(),
                outils_du_contrat=sorted(definition.tools),
                outils_effectifs=sorted(
                    nom for nom in definition.tools if nom not in set(retires)
                ),
                outils_desactives=retires,
                skills_du_contrat=sorted(skills_contrat),
                skills_effectifs=sorted({*skills_contrat, *skills_ajoutes}),
                instructions_supplementaires=(
                    surcharge.instructions_supplementaires if surcharge else None
                ),
                approval_required=sorted(definition.approval_required),
                surcharge_active=surcharge is not None,
                connecteurs_mcp=[
                    serveur.nom
                    for serveur in connecteurs
                    if definition.agent_id in (serveur.agents or [])
                ],
            )
        )

    return ParametresEtatRead(
        chiffrement_disponible=chiffrement_disponible(),
        fournisseurs_supportes=sorted(SUPPORTED_PROVIDERS),
        agents=agents,
        connecteurs_mcp=[
            _serveur_read(serveur, mcp_service.noms_en_tetes(serveur))
            for serveur in connecteurs
        ],
        skills=SkillsDisponiblesRead(
            racine=str(racine_skills()), disponibles=lister_skills()
        ),
    )


def agents_exposes() -> frozenset[str]:
    """Agents du catalogue AG-UI (les autres sont internes, hors interface).

    Import paresseux : ``app.api.agui`` importe le runtime, l'importer au
    chargement de ce module créerait un cycle.
    """
    from app.api.agui import AGENTS_EXPOSES

    return frozenset(definition.agent_id for definition in AGENTS_EXPOSES)


# --- Surcharges par agent -----------------------------------------------------


@router.get("/agents/{agent_id}", response_model=SurchargeAgentRead)
def lire_surcharge(agent_id: str, admin: AdminUser, session: DbSession) -> SurchargeAgentRead:
    """Surcharge active d'un agent (champs vides s'il suit son contrat)."""
    _ = admin
    return _surcharge_read(
        ParametresAgentService(session).surcharge(agent_id), agent_id
    )


@router.put("/agents/{agent_id}", response_model=SurchargeAgentRead)
def enregistrer_surcharge(
    agent_id: str,
    payload: SurchargeAgentUpdate,
    admin: AdminUser,
    session: DbSession,
) -> SurchargeAgentRead:
    """Enregistre la surcharge d'un agent et reconstruit le runtime.

    Un outil hors contrat est refusé avec la liste des outils valides : refuser
    vaut mieux qu'accepter un réglage qui ne ferait rien.
    """
    service = ParametresAgentService(session, actor_id=str(admin.id))
    ligne = service.enregistrer(
        agent_id,
        outils_desactives=payload.outils_desactives,
        instructions_supplementaires=payload.instructions_supplementaires,
        skills_ajoutes=payload.skills_ajoutes,
        modifie_par=str(admin.id),
    )
    _recharger(session, str(admin.id))
    return _surcharge_read(ligne, agent_id)


@router.delete("/agents/{agent_id}", response_model=SurchargeAgentRead)
def supprimer_surcharge(
    agent_id: str, admin: AdminUser, session: DbSession
) -> SurchargeAgentRead:
    """Rend l'agent à son contrat déclaré et reconstruit le runtime."""
    service = ParametresAgentService(session, actor_id=str(admin.id))
    service.supprimer(agent_id)
    _recharger(session, str(admin.id))
    return SurchargeAgentRead(agent_id=agent_id, actif=False)


# --- Skills -------------------------------------------------------------------


@router.get("/skills", response_model=SkillsDisponiblesRead)
def lister_skills_disponibles(admin: AdminUser) -> SkillsDisponiblesRead:
    """Skills installables sur le disque (``<racine>/<nom>/SKILL.md``)."""
    _ = admin
    return SkillsDisponiblesRead(racine=str(racine_skills()), disponibles=lister_skills())


# --- Connecteurs MCP ----------------------------------------------------------


@router.get("/mcp", response_model=list[ServeurMcpRead])
def lister_connecteurs(admin: AdminUser, session: DbSession) -> list[ServeurMcpRead]:
    """Connecteurs déclarés, actifs ou non."""
    _ = admin
    service = ServeurMcpService(session)
    return [
        _serveur_read(serveur, service.noms_en_tetes(serveur))
        for serveur in service.lister()
    ]


@router.put("/mcp/{nom}", response_model=ServeurMcpRead)
def enregistrer_connecteur(
    nom: str,
    payload: ServeurMcpUpdate,
    admin: AdminUser,
    session: DbSession,
) -> ServeurMcpRead:
    """Déclare ou met à jour un connecteur, puis reconstruit le runtime.

    Les en-têtes fournis sont chiffrés avant écriture ; la réponse n'en donne que
    les noms.
    """
    service = ServeurMcpService(session, actor_id=str(admin.id))
    ligne = service.enregistrer(
        nom=nom,
        transport=payload.transport,
        url=payload.url,
        commande=payload.commande,
        arguments=payload.arguments,
        agents=payload.agents,
        en_tetes=payload.en_tetes,
        actif=payload.actif,
        modifie_par=str(admin.id),
    )
    _recharger(session, str(admin.id))
    return _serveur_read(ligne, service.noms_en_tetes(ligne))


@router.delete("/mcp/{nom}", status_code=204)
def supprimer_connecteur(nom: str, admin: AdminUser, session: DbSession) -> None:
    """Retire un connecteur et reconstruit le runtime."""
    service = ServeurMcpService(session, actor_id=str(admin.id))
    service.supprimer(nom)
    _recharger(session, str(admin.id))


# --- internes -----------------------------------------------------------------


def _recharger(session: Session, acteur: str) -> None:
    """Reconstruit les graphes après un changement de paramètres.

    Sans ce rechargement, un réglage persistant resterait sans effet jusqu'au
    prochain redémarrage — et l'interface afficherait un état trompeur.
    """
    manager = get_runtime_manager()
    agents = manager.reload()
    AgentRuntimeService(session, actor_id=acteur).journaliser_rechargement(list(agents))


__all__ = ["router", "agents_exposes"]
