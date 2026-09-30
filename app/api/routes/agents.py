"""Routes d'administration du runtime d'agents (modèle / provider à chaud).

Séparation des droits (instruction/08 §2) :

- ``GET /agents/catalogue`` : tout utilisateur authentifié — introspection utile
  à l'interface CopilotKit (quels agents existent, quels tools ils ont) ;
- ``GET|PUT|POST /agents/runtime*`` : **administrateur uniquement** — changer le
  modèle, redémarrer le runtime, consulter l'historique.

La clé du provider n'est jamais exposée ni modifiable ici : elle reste dans
``backend/.env`` (instruction/08 §6).
"""

from fastapi import APIRouter, Depends, Query

from app.agents.catalogue import AGENT_DEFINITIONS
from app.agents.manager import get_runtime_manager
from app.agents.providers import AgentConfigurationError, tester_configuration
from app.api.deps import AdminUser, CurrentUser, DbSession, get_current_user
from app.api.schemas.agent_runtime import (
    AgentCatalogueItem,
    AgentRuntimeApplicationRead,
    AgentRuntimeConfigRead,
    AgentRuntimeStateRead,
    AgentRuntimeTestRead,
    AgentRuntimeUpdate,
    AgentRuntimeValuesRead,
    HitlReponseItem,
    HitlScenarioItem,
)
from app.application.services import AgentRuntimeService
from app.application.services.parametres_service import ParametresAgentService
from app.core.chiffrement import dechiffrer
from app.domain.agent_runtime import ConfigurationRuntimeAgent

router = APIRouter(
    prefix="/agents",
    tags=["agents"],
    dependencies=[Depends(get_current_user)],
)


# --- Catalogue (tout utilisateur authentifié) --------------------------------


@router.get("/catalogue", response_model=list[AgentCatalogueItem])
def lister_catalogue(user: CurrentUser, session: DbSession) -> list[AgentCatalogueItem]:
    """Décrit les agents exposés : identité, tools, collaboration, approbations, HITL.

    Les tools affichés sont les tools **effectifs** : si un administrateur a
    désactivé un outil depuis la page Paramètres, l'interface ne doit pas
    continuer à l'annoncer.
    """
    _ = user
    surcharges = ParametresAgentService(session).effectives()
    from app.agents.surcharges import definition_effective

    definitions = [
        definition_effective(d, surcharges.get(d.agent_id)) for d in AGENT_DEFINITIONS
    ]
    return [
        AgentCatalogueItem(
            agent_id=definition.agent_id,
            display_name=definition.display_name,
            description=definition.description,
            tools=sorted(definition.tools),
            capabilities=sorted(definition.capabilities),
            collaboration=list(definition.collaborates_with()),
            skills=sorted(definition.skills),
            approval_required=sorted(definition.approval_required),
            output_schema_name=definition.output_schema_name,
            hitl_scenarios=[
                HitlScenarioItem(
                    id=scenario.id,
                    titre=scenario.titre,
                    contexte=scenario.contexte,
                    action=scenario.action,
                    reponses=[
                        HitlReponseItem(
                            valeur=reponse.valeur,
                            libelle=reponse.libelle,
                            description=reponse.description,
                        )
                        for reponse in scenario.reponses
                    ],
                )
                for scenario in definition.hitl_scenarios
            ],
        )
        for definition in definitions
    ]


# --- Runtime des agents (administrateur) -------------------------------------


def _texte(valeur: object) -> str | None:
    """Normalise une valeur optionnelle en ``str | None`` (réponses d'état)."""
    return None if valeur is None else str(valeur)


def _liste_texte(valeur: object) -> list[str]:
    """Normalise une valeur optionnelle en liste de chaînes."""
    if isinstance(valeur, (list, tuple, set, frozenset)):
        return [str(item) for item in valeur]
    return []


def _etat(manager_values: dict[str, object]) -> AgentRuntimeApplicationRead:
    """Projette l'état du manager en réponse d'application."""
    brut = manager_values.get("values")
    return AgentRuntimeApplicationRead(
        configured=bool(manager_values.get("configured")),
        values=AgentRuntimeValuesRead(**brut) if isinstance(brut, dict) else None,
        built_agents=_liste_texte(manager_values.get("built_agents")),
        last_error=_texte(manager_values.get("last_error")),
    )


@router.get("/runtime", response_model=AgentRuntimeStateRead)
def etat_runtime(admin: AdminUser) -> AgentRuntimeStateRead:
    """Configuration effective, agents construits et dernière erreur éventuelle."""
    _ = admin
    etat = get_runtime_manager().state()
    brut = etat.get("values")
    return AgentRuntimeStateRead(
        configured=bool(etat.get("configured")),
        values=AgentRuntimeValuesRead(**brut) if isinstance(brut, dict) else None,
        agents=[str(a) for a in (etat.get("agents") or [])],
        built_agents=[str(a) for a in (etat.get("built_agents") or [])],
        last_error=_texte(etat.get("last_error")),
    )


@router.put("/runtime", response_model=AgentRuntimeApplicationRead)
def appliquer_runtime(
    payload: AgentRuntimeUpdate,
    admin: AdminUser,
    session: DbSession,
) -> AgentRuntimeApplicationRead:
    """Change la configuration **à chaud** : persistée puis appliquée sans redémarrage.

    La configuration est validée puis écrite par le service (audit compris) avant
    d'être appliquée au runtime : une valeur refusée n'atteint jamais la base.
    """
    service = AgentRuntimeService(session, actor_id=str(admin.id))
    ligne = service.enregistrer(
        provider=payload.provider,
        model=payload.model,
        base_url=payload.base_url,
        temperature=payload.temperature,
        max_iterations=payload.max_iterations,
        modifie_par=str(admin.id),
        api_key=payload.api_key,
        conserver_cle=payload.conserver_cle,
    )
    manager = get_runtime_manager()
    manager.apply_persisted(ligne)
    return _etat(manager.state())


@router.post("/runtime/test", response_model=AgentRuntimeTestRead)
async def tester_runtime(
    payload: AgentRuntimeUpdate,
    admin: AdminUser,
    session: DbSession,
) -> AgentRuntimeTestRead:
    """Interroge le modèle candidat **avant** d'appliquer la configuration.

    La clé utilisée est celle fournie, sinon celle déjà enregistrée par
    l'administrateur, sinon celle du ``.env``. Elle n'est jamais renvoyée : le
    test répond seulement si le modèle a répondu, et par quoi.
    """
    _ = admin
    cle = payload.api_key
    if not cle:
        active = AgentRuntimeService(session).obtenir_active()
        cle = dechiffrer(active.api_key_chiffree) if active is not None else None
    try:
        reponse = await tester_configuration(
            provider=payload.provider,
            model=payload.model,
            base_url=payload.base_url,
            api_key=cle,
        )
    except AgentConfigurationError as exc:
        return AgentRuntimeTestRead(
            ok=False,
            provider=payload.provider,
            model=payload.model,
            erreur=str(exc),
        )
    except Exception as exc:  # réseau, clé refusée, quota…
        return AgentRuntimeTestRead(
            ok=False,
            provider=payload.provider,
            model=payload.model,
            erreur=f"{type(exc).__name__} : {exc}",
        )
    return AgentRuntimeTestRead(
        ok=True,
        provider=payload.provider,
        model=payload.model,
        reponse=reponse,
    )


@router.delete("/runtime/cle", response_model=AgentRuntimeApplicationRead)
def retirer_cle_administree(
    admin: AdminUser, session: DbSession
) -> AgentRuntimeApplicationRead:
    """Retire la clé administrée : le runtime revient à celle du ``.env``.

    Les paramètres (provider, modèle) sont conservés : on retire un secret, on ne
    réinitialise pas la configuration. Sans cela, la seule façon de retirer une
    clé compromise serait de tout remettre à zéro.
    """
    service = AgentRuntimeService(session, actor_id=str(admin.id))
    active = service.obtenir_active()
    if active is None:
        return _etat(get_runtime_manager().state())
    ligne = service.enregistrer(
        provider=active.provider,
        model=active.model,
        base_url=active.base_url,
        temperature=float(active.temperature),
        max_iterations=int(active.max_iterations),
        modifie_par=str(admin.id),
        conserver_cle=False,
    )
    manager = get_runtime_manager()
    manager.apply_persisted(ligne)
    return _etat(manager.state())


@router.post("/runtime/reload", response_model=AgentRuntimeApplicationRead)
def recharger_runtime(admin: AdminUser, session: DbSession) -> AgentRuntimeApplicationRead:
    """Redémarre le runtime d'agents (graphes reconstruits) sans redémarrer le backend.

    La configuration persistée est conservée : seuls les graphes sont reconstruits
    (utile après un changement de ``.env`` ou pour repartir d'un état sain).
    """
    service = AgentRuntimeService(session, actor_id=str(admin.id))
    manager = get_runtime_manager()
    agents = manager.reload()
    service.journaliser_rechargement(list(agents))
    return _etat(manager.state())


@router.post("/runtime/reset", response_model=AgentRuntimeApplicationRead)
def revenir_au_env(admin: AdminUser, session: DbSession) -> AgentRuntimeApplicationRead:
    """Abandonne la configuration administrée et revient à celle de ``.env``."""
    service = AgentRuntimeService(session, actor_id=str(admin.id))
    service.desactiver_active()
    manager = get_runtime_manager()
    manager.reset_to_env()
    return _etat(manager.state())


@router.get("/runtime/historique", response_model=list[AgentRuntimeConfigRead])
def historique_runtime(
    admin: AdminUser,
    session: DbSession,
    limit: int = Query(default=20, ge=1, le=100),
) -> list[ConfigurationRuntimeAgent]:
    """Versions successives de la configuration (la plus récente d'abord)."""
    _ = admin
    return AgentRuntimeService(session).historique(limit=limit)


__all__ = ["router"]