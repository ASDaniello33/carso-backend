"""Routes d'administration—paramètres par agent, skills, connecteurs MCP.

Réservé aux administrateurs (``AdminUser``). Le runtime (provider, modèle, clé)
garde ses routes dédiées sous ``/agents/runtime``—cette page ne les duplique
pas, elle les complète avec ce qui n'existe que **par agent**.

Chaque écriture se termine par un rechargement du runtime—un réglage enregistré
mais non appliqué serait un mensonge. Le rechargement journalise les agents
reconstruits, donc l'effet du changement reste auditable.
"""

from __future__ import annotations

import json
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.agents.catalogue import AGENT_DEFINITIONS
from app.agents.manager import get_runtime_manager
from app.agents.providers import SUPPORTED_PROVIDERS
from app.agents.registre_skills import lister_skills, racine_skills
from app.api.deps import AdminUser, CurrentUser, DbSession, get_current_user
from app.api.schemas.parametres import (
    AgentParametrableRead,
    AutorisationMcpRead,
    McpPersonnelRead,
    ParametresEtatRead,
    ServeurMcpRead,
    ServeurMcpUpdate,
    SkillsDisponiblesRead,
    SondeMcpRead,
    SurchargeAgentRead,
    SurchargeAgentUpdate,
)
from app.application.services import AgentRuntimeService, mcp_oauth_service
from app.application.services.parametres_service import (
    ParametresAgentService,
    ServeurMcpService,
)
from app.core.chiffrement import chiffrement_disponible
from app.core.config import get_settings
from app.core.errors import NotFoundError
from app.core.errors import ValidationError as BusinessValidationError
from app.domain.agent_runtime import SurchargeAgent
from app.domain.mcp import ServeurMcp

router = APIRouter(
    prefix="/parametres",
    tags=["parametres"],
    dependencies=[Depends(get_current_user)],
)

#: Router **public** du retour OAuth : le navigateur arrive d'un site tiers, sans
#: en-tête d'authentification. La sécurité tient à ``state`` — valeur secrète à
#: usage unique, créée par une requête authentifiée, retrouvée ici comme clé
#: d'attente et validée par le SDK avant l'échange du code (incr. 47).
router_oauth = APIRouter(prefix="/parametres/mcp/personnel/oauth", tags=["parametres"])


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


def _sonde_read(brut: str | None) -> SondeMcpRead | None:
    """Dernière sonde relue pour l'API — ``None`` si absente ou illisible.

    Une sonde illisible vaut « aucune sonde » : jamais un succès supposé. Le
    contraire (afficher vert sur un contenu corrompu) serait précisément le
    mensonge que la sonde existe pour éviter.
    """
    if not brut:
        return None
    try:
        contenu = json.loads(brut)
        return SondeMcpRead.model_validate(contenu)
    except Exception:  # noqa: BLE001 - contenu non exploitable : on l'ignore
        return None


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
        personnel=serveur.utilisateur_id is not None,
        mode_authentification=serveur.mode_authentification,
        oauth_client_id=serveur.oauth_client_id,
        oauth_statut=serveur.oauth_statut,
        oauth_derniere_erreur=serveur.oauth_derniere_erreur,
        sonde=_sonde_read(serveur.derniere_sonde),
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
    # Perimetre **global** : l'ecran d'administration decrit le Runtime partage.
    # Les connecteurs personnels d'un compte n'y ont pas de legitimite
    # (l'ecriture admin ne les atteint pas) et leur URL peut porter une cle
    # dans la chaine de requete : les lister ici n'apporte rien et expose un
    # secret. Chacun les declare dans son propre perimetre.
    connecteurs = mcp_service.lister(perimetre_global_seulement=True)

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
    """Connecteurs **globaux** déclarés, actifs ou non.

    Les connecteurs personnels des comptes n'y figurent pas : ils appartiennent
    au perimetre de leur proprietaire, qui les declare et les supprime lui-meme.
    """
    _ = admin
    service = ServeurMcpService(session)
    return [
        _serveur_read(serveur, service.noms_en_tetes(serveur))
        for serveur in service.lister(perimetre_global_seulement=True)
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

    Un nom deja porte par le connecteur **personnel** d'un compte est refuse :
    l'unicité est par propriétaire, et ecrire en global creerait une seconde
    ligne partagee a tous, sans toucher a celle du collaborateur. Le renommer est
    le seul moyen de reprendre ce nom.
    """
    service = ServeurMcpService(session, actor_id=str(admin.id))
    if service.nom_pris_par_un_compte(nom):
        raise BusinessValidationError(
            f"« {nom.strip()} » est le nom d'un connecteur personnel : "
            "choisissez un autre nom pour le connecteur partagé.",
            details={"nom": nom.strip()},
        )
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
        mode_authentification=payload.mode_authentification,
        oauth_client_id=payload.oauth_client_id,
    )
    _recharger(session, str(admin.id))
    return _serveur_read(ligne, service.noms_en_tetes(ligne))


@router.delete("/mcp/{nom}", status_code=204)
def supprimer_connecteur(nom: str, admin: AdminUser, session: DbSession) -> None:
    """Retire un connecteur et reconstruit le runtime."""
    service = ServeurMcpService(session, actor_id=str(admin.id))
    service.supprimer(nom)
    _recharger(session, str(admin.id))


# --- Connecteurs MCP personnels ------------------------------------------------
#
# Meme surface que les routes d'administration, mais **scoped** au compte
# appelant, et — surtout — **sans rechargement du Runtime**. Un changement de
# connecteur personnel ne reconstruit que le contexte de son proprietaire : le
# `manager.reload()` des routes admin reconstruirait *tous* les graphes, donc
# ceux de tous les autres comptes. C'est l'effet de bord explicitement exclu.


@router.get("/mcp/personnel", response_model=McpPersonnelRead)
def lister_mes_connecteurs(
    utilisateur: CurrentUser, session: DbSession
) -> McpPersonnelRead:
    """Connecteurs **du compte appelant**, plus les globaux qu'il voit.

    Les connecteurs personnels d'un autre compte ne sont jamais renvoyes. La
    reponse porte aussi la disponibilite du chiffrement : c'est ce qui permet a
    l'ecran d'expliquer *avant* la saisie qu'un en-tete serait refuse.
    """
    service = ServeurMcpService(session)
    visibles = service.lister(
        perimetre_global_seulement=True
    ) + service.lister(utilisateur_id=utilisateur.id)
    uniques = {s.nom: s for s in visibles}
    return McpPersonnelRead(
        chiffrement_disponible=chiffrement_disponible(),
        uri_redirection=mcp_oauth_service.uri_redirection(),
        connecteurs=[
            _serveur_read(ligne, service.noms_en_tetes(ligne))
            for ligne in sorted(uniques.values(), key=lambda s: s.nom)
        ],
    )


@router.put("/mcp/personnel/{nom}", response_model=ServeurMcpRead)
def enregistrer_mon_connecteur(
    nom: str,
    payload: ServeurMcpUpdate,
    utilisateur: CurrentUser,
    session: DbSession,
) -> ServeurMcpRead:
    """Declare ou met a jour **mon** connecteur, sans toucher au Runtime.

    Le nom n'etend que mon perimetre : un connecteur global homonyme n'est ni
    lu, ni ecrase, ni masque. Les en-tetes sont chiffres avant ecriture et la
    reponse n'en donne que les noms.
    """
    service = ServeurMcpService(session, actor_id=str(utilisateur.id))
    ligne = service.enregistrer(
        nom=nom,
        transport=payload.transport,
        url=payload.url,
        commande=payload.commande,
        arguments=payload.arguments,
        agents=payload.agents,
        en_tetes=payload.en_tetes,
        actif=payload.actif,
        modifie_par=str(utilisateur.id),
        utilisateur_id=utilisateur.id,
        mode_authentification=payload.mode_authentification,
        oauth_client_id=payload.oauth_client_id,
    )
    _vider_mon_contexte(session, utilisateur.id)
    return _serveur_read(ligne, service.noms_en_tetes(ligne))


@router.delete("/mcp/personnel/{nom}", status_code=204)
def supprimer_mon_connecteur(
    nom: str, utilisateur: CurrentUser, session: DbSession
) -> None:
    """Retire un connecteur de **mon** perimetre, sans toucher au Runtime."""
    service = ServeurMcpService(session, actor_id=str(utilisateur.id))
    service.supprimer(nom, utilisateur_id=utilisateur.id)
    _vider_mon_contexte(session, utilisateur.id)


@router.post("/mcp/personnel/{nom}/tester", response_model=ServeurMcpRead)
def tester_mon_connecteur(
    nom: str, utilisateur: CurrentUser, session: DbSession
) -> ServeurMcpRead:
    """Sonde **réelle** de mon connecteur : outils obtenus, erreur dépliée, durée.

    Hors cache, à la demande : sans cette preuve, un connecteur enregistré mais
    inutilisable (401, 404, autorisation absente) reste indiscernable d'un
    connecteur sain — l'écart qui a motivé l'incrément 47.
    """
    service = ServeurMcpService(session)
    ligne = service.serveurs.get_par_nom(nom, utilisateur_id=utilisateur.id)
    if ligne is None:
        raise NotFoundError(f"Connecteur personnel inconnu : {nom!r}")
    from app.agents.mcp import sonder_serveur

    ligne.derniere_sonde = json.dumps(sonder_serveur(ligne), ensure_ascii=False)
    session.flush()
    session.refresh(ligne)
    return _serveur_read(ligne, service.noms_en_tetes(ligne))


@router.post("/mcp/personnel/{nom}/autoriser", response_model=AutorisationMcpRead)
def autoriser_mon_connecteur(
    nom: str, utilisateur: CurrentUser, session: DbSession
) -> AutorisationMcpRead:
    """Ouvre un consentement OAuth pour **mon** connecteur personnel.

    La réponse porte l'URL à ouvrir dans le navigateur ; aucun jeton n'y figure —
    il n'en existe un qu'après le retour du navigateur sur la route de rappel,
    échangé côté serveur. Le flux s'exécute sur la boucle MCP, jamais dans cette
    requête : la route attend seulement que l'URL soit publiée.
    """
    service = ServeurMcpService(session)
    ligne = service.serveurs.get_par_nom(nom, utilisateur_id=utilisateur.id)
    if ligne is None:
        raise NotFoundError(f"Connecteur personnel inconnu : {nom!r}")
    url, expire_le = mcp_oauth_service.demarrer(ligne, utilisateur_id=utilisateur.id)
    return AutorisationMcpRead(url_autorisation=url, expire_le=expire_le)


@router.post("/mcp/personnel/{nom}/revoquer", response_model=ServeurMcpRead)
def revoquer_mon_connecteur(
    nom: str, utilisateur: CurrentUser, session: DbSession
) -> ServeurMcpRead:
    """Retire les jetons OAuth de **mon** connecteur (le client est conservé)."""
    service = ServeurMcpService(session)
    ligne = service.serveurs.get_par_nom(nom, utilisateur_id=utilisateur.id)
    if ligne is None:
        raise NotFoundError(f"Connecteur personnel inconnu : {nom!r}")
    mcp_oauth_service.revoquer(ligne)
    session.refresh(ligne)
    return _serveur_read(ligne, service.noms_en_tetes(ligne))


@router.post("/mcp/{nom}/tester", response_model=ServeurMcpRead)
def tester_connecteur(nom: str, admin: AdminUser, session: DbSession) -> ServeurMcpRead:
    """Sonde réelle d'un connecteur **global** (administration).

    Le même bouton que côté personnel, mais sur les connecteurs partagés : un
    connecteur global mort prive tous les agents qui le nomment.
    """
    _ = admin
    service = ServeurMcpService(session)
    ligne = service.serveurs.get_par_nom(nom, utilisateur_id=None)
    if ligne is None:
        raise NotFoundError(f"Connecteur global inconnu : {nom!r}")
    from app.agents.mcp import sonder_serveur

    ligne.derniere_sonde = json.dumps(sonder_serveur(ligne), ensure_ascii=False)
    session.flush()
    session.refresh(ligne)
    return _serveur_read(ligne, service.noms_en_tetes(ligne))


@router_oauth.get("/callback")
def retour_oauth_mcp(
    state: str = "",
    code: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
) -> RedirectResponse:
    """Retour du navigateur après consentement — route **publique**.

    Elle ne peut pas exiger d'en-tête d'authentification : le navigateur arrive
    d'un site tiers. La sécurité tient à ``state`` : valeur secrète à usage
    unique, créée par une requête authentifiée, retrouvée ici comme clé d'attente
    et **validée par le SDK** avant l'échange du code. Aucun jeton ne passe par le
    navigateur — seul un code à usage unique, échangeable une fois.
    """
    issue = mcp_oauth_service.terminer(
        state=state, code=code, erreur=error_description or error
    )
    reglages = get_settings()
    cible = (
        f"{reglages.frontend_public_url.rstrip('/')}/parametres"
        f"?mcp={'autorise' if issue.get('ok') else 'echec'}"
        f"&connecteur={quote(str(issue.get('nom') or ''))}"
    )
    return RedirectResponse(url=cible, status_code=303)


def _vider_mon_contexte(session: Session, utilisateur_id: UUID) -> None:
    """Purge **ciblee** du cache MCP — jamais globale.

    Le graphe de chaque agent est memorise par empreinte de configuration
    effective : la prochaine invocation relit la base et, la configuration
    ayant change, construit le sien. Invalider l'entree de cache ne fait que
    liberer les outils maintenant caducs — **les contextes des autres comptes,
    dont la configuration n'a pas bouge, restent intacts**.

    Seuls les agents concernes par les connecteurs **de ce compte** sont
    vises : un agent qu'il n'utilise pas ne voit pas son cache invalide.
    """
    from app.agents.mcp import vider_cache
    from app.infrastructure.repositories import ServeurMcpRepository

    mes_connecteurs = ServeurMcpRepository(session).lister(
        utilisateur_id=utilisateur_id
    )
    for agent_id in sorted({a for c in mes_connecteurs for a in (c.agents or [])}):
        vider_cache(agent_id=agent_id)


# --- internes -----------------------------------------------------------------


def _recharger(session: Session, acteur: str) -> None:
    """Reconstruit les graphes après un changement de paramètres.

    Sans ce rechargement, un réglage persistant resterait sans effet jusqu'au
    prochain redémarrage — et l'interface afficherait un état trompeur.
    """
    manager = get_runtime_manager()
    agents = manager.reload()
    AgentRuntimeService(session, actor_id=acteur).journaliser_rechargement(list(agents))


__all__ = ["router", "router_oauth", "agents_exposes"]
