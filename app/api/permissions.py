"""Contrôles d'accès des routes—« qui peut appeler quoi ».

Trois familles de routes, une seule barrière—le backend.

1. **Administration**—``AdminUser`` (``app/api/deps.py``)—comptes, paramètres
   de runtime. L'interface masque, elle ne décide pas.
2. **Interne**—``InterneUser``—tout le métier sauf l'administration. Un
   formateur (formateurs et équipes externes) est refusé ici (**403**).
3. **Scopée**—le périmètre est calculé pour la requête (``PerimetreDep``) puis
   **passé explicitement** aux services, qui filtrent lectures et écritures
   autorisées. Hors périmètre, une entité est introuvable (**404**)—un refus
   ne doit jamais révéler l'existence d'un objet d'une autre mission.

Le calcul du périmètre est identique pour les routes, les tools d'agent et le
chat social (``pour_utilisateur``), pour qu'aucun canal ne contourne la règle.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable
from typing import Annotated, TypeVar

from fastapi import Depends
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, DbSession
from app.application.perimetre import (
    AGENTS_FORMATEUR,
    ROLES_INTERNES,
    Perimetre,
    est_interne,
    poser_perimetre,
    pour_utilisateur,
)
from app.application.trace import poser_demande_par
from app.core.errors import PermissionDeniedError
from app.domain.identity import Utilisateur

#: Rôles autorisés à lire le journal d'audit (décision validée).
_ROLES_AUDIT = ROLES_INTERNES

#: Type générique : permet de filtrer des définitions d'agents sans que ce module
#: importe le catalogue (il reste un module de gardes, pas de contenu).
_T = TypeVar("_T")


def agent_autorise(role: str | None, agent_id: str) -> bool:
    """Vrai si ce rôle peut ouvrir cet agent AG-UI.

    Un compte restreint (formateur) n'ouvre que ``AGENTS_FORMATEUR`` ; l'équipe
    interne et l'administration ouvrent tout le catalogue. Règle unique, utilisée
    par l'endpoint AG-UI **et** par les catalogues (l'interface ne montre que ce
    que le backend accepte — et le backend refuse de toute façon).
    """
    return est_interne(role) or agent_id in AGENTS_FORMATEUR


def agents_autorises(user: Utilisateur, definitions: Iterable[_T]) -> tuple[_T, ...]:
    """Filtre un catalogue d'agents selon le rôle (accès complet ⇒ tout)."""
    if est_interne(user.role):
        return tuple(definitions)
    return tuple(
        definition
        for definition in definitions
        if getattr(definition, "agent_id", None) in AGENTS_FORMATEUR
    )


def exiger_interne(user: CurrentUser) -> Utilisateur:
    """Exige l'équipe interne ou l'administration (403 sinon)."""
    if not est_interne(user.role):
        raise PermissionDeniedError(
            "Réservé à l'équipe interne",
            details={"role": user.role},
        )
    return user


def exiger_audit(user: CurrentUser) -> Utilisateur:
    """Exige le droit de consulter le journal d'audit (admin + équipe interne)."""
    if user.role not in _ROLES_AUDIT:
        raise PermissionDeniedError(
            "Journal d'audit réservé à l'équipe interne et à l'administration",
            details={"role": user.role},
        )
    return user


def calculer_perimetre(user: CurrentUser, session: DbSession) -> Perimetre:
    """Périmètre de la requête, calculé depuis les affectations approuvées.

    Dépendance **synchrone** : sa valeur est passée explicitement au service
    appelé (``MissionService(session, perimetre=perimetre)``). Les chemins
    agentiques, eux, posent le même périmètre dans le contexte (``ContextVar``)
    — voir ``app/api/agui.py`` et ``social_mention_service``.
    """
    return pour_utilisateur(session, user)


def agent_formateur_autorise(agent_id: str) -> bool:
    """Vrai si un compte restreint peut ouvrir cet agent AG-UI."""
    return agent_id in AGENTS_FORMATEUR


async def preparer_acces_agent(
    agent_id: str,
    user: Utilisateur,
    session: Session,
) -> None:
    """Règle d'entrée d'un endpoint AG-UI : rôle autorisé, puis périmètre posé.

    Fonction appelée explicitement (sans dépendance FastAPI) : le
    ``agent_id`` est fourni par l'appelant, ce qui la rend testable et évite
    que FastAPI ne cherche un paramètre de requête inexistant (les paths AG-UI
    sont des littéraux : ``/agui/agent_rh``, **pas** ``/agui/{agent_id}``).

    - **403** si le rôle n'a pas cet agent dans son contrat ;
    - le périmètre du compte est ensuite posé dans le contexte du run : les
      tools de l'agent appellent les **mêmes** services que l'API, ils héritent
      donc du filtrage sans le réimplémenter ;
    - le demandeur est mémorisé pour l'audit (« qui fait quoi »).
    """
    if not agent_autorise(user.role, agent_id):
        raise PermissionDeniedError(
            "Agent non autorisé pour ce rôle",
            details={"agent_id": agent_id, "role": user.role},
        )
    perimetre = pour_utilisateur(session, user)
    poser_perimetre(perimetre)
    poser_demande_par(perimetre.utilisateur_id)
    # Contexte d'exécution : identité, rôle, profil et surface d'outils. Le
    # modèle reçoit un bloc de prompt ; le ToolNode refuse les outils hors du
    # contrat de ce rôle (app/agents/contexte.py). Import local : la garde ne
    # charge l'outillage agentique que lorsqu'un run démarre.
    from app.agents.contexte import depuis, poser_contexte_utilisateur

    poser_contexte_utilisateur(depuis(session, user, perimetre=perimetre))


def garde_agui(agent_id: str) -> Callable[..., Awaitable[None]]:
    """Dépendance FastAPI d'un endpoint AG-UI, **fermée sur son agent**.

    Les endpoints sont montés un par agent sur un chemin littéral
    (``/api/v1/agui/agent_rh``) : il n'y a donc aucun paramètre de chemin
    auquel lier ``agent_id``. La dépendance est donc construite ici, avec
    l'identifiant capturé — c'est la seule forme correcte pour ces paths.

    Asynchrone volontairement : le ``ContextVar`` doit être posé dans la tâche
    qui exécute l'endpoint **et** itère le flux SSE (les tâches filles — dont
    l'exécution des tools — en héritent).
    """

    async def _garde(user: CurrentUser, session: DbSession) -> None:
        await preparer_acces_agent(agent_id, user, session)

    # Nom lisible dans les traces et dans le schéma OpenAPI du run.
    _garde.__name__ = f"garde_agui_{agent_id}"
    _garde.__qualname__ = _garde.__name__
    return _garde


InterneUser = Annotated[Utilisateur, Depends(exiger_interne)]
AuditUser = Annotated[Utilisateur, Depends(exiger_audit)]
PerimetreDep = Annotated[Perimetre, Depends(calculer_perimetre)]

__all__ = [
    "AuditUser",
    "InterneUser",
    "PerimetreDep",
    "agent_autorise",
    "agent_formateur_autorise",
    "agents_autorises",
    "calculer_perimetre",
    "est_interne",
    "exiger_audit",
    "exiger_interne",
    "garde_agui",
    "preparer_acces_agent",
]
