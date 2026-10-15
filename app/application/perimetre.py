"""Périmètre d'accès d'un compte—« qui peut voir quoi ».

Décision validée—le **rôle de compte** porte le niveau d'accès.

- ``administrateur``—tout ;
- ``collaborateur`` (équipe interne)—tout le métier, sauf la gestion des
  comptes et les paramètres de runtime (gardés par ``AdminUser``) ;
- ``formateur`` (formateurs et équipes externes)—uniquement les missions où
  la personne est **affectée et approuvée**, leurs sessions, leurs documents et
  leurs données—plus le social et *son* tableau de bord.

Le périmètre est **calculé**, jamais déclaré—il se déduit des affectations en
base (``statut == approuvee``). Une proposition en attente ou refusée n'ouvre
aucun droit, et un compte formateur non lié à une personne du vivier a un
périmètre **vide** (aucun accès—on ne présume jamais).

Deux voies transportent un périmètre :

1. les routes le passent **explicitement** aux services (``MissionService(
   session, perimetre=perimetre)``) ;
2. le runtime agentique le pose dans un :class:`ContextVar` (même patron que la
   session des tools documentaires, ``app/agents/outillage.py``) pour que les
   tools, qui appellent les mêmes services, héritent du filtrage sans le
   réimplémenter.

Sans périmètre posé (tests, scripts, mode lab), ``perimetre_effectif()``
retombe sur un accès complet—c'est le comportement historique, et le seul
cas où « pas de contexte » signifie « pas de restriction » est celui d'un
appel interne—jamais celui d'une requête utilisateur, qui pose toujours le
sien.
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.errors import PermissionDeniedError
from app.domain.enums import RoleUtilisateur
from app.domain.identity import Utilisateur
from app.infrastructure.repositories import AffectationEquipeRepository

#: Rôles qui voient tout le métier (l'administrateur voit en plus l'administration).
#: Règle unique : les gardes d'API, le contexte d'agent et les catalogues la
#: lisent ici — jamais une seconde définition qui pourrait diverger.
ROLES_INTERNES: frozenset[str] = frozenset(
    {RoleUtilisateur.ADMINISTRATEUR.value, RoleUtilisateur.COLLABORATEUR.value}
)


#: Agents AG-UI ouverts à un formateur (et à tout compte restreint) — décisions
#: validées (03/10, complétée le 10/10) : l'assistant formateur pour les
#: documents de session, le généraliste pour la consultation **de ses données**,
#: le statisticien pour ses KPI, et l'**agent personnel** — l'agent du compte,
#: ouvert à tout compte actif, précisément parce qu'il ne lit que son périmètre.
#: Les autres agents supposent un accès complet au métier et restent réservés à
#: l'équipe interne.
#:
#: Conséquence assumée : ``outils_du_role`` dérive de cet ensemble, donc les
#: outils de lecture de l'agent personnel deviennent exécutables pour un compte
#: restreint — c'est le filtrage de périmètre, pas la surface d'outils, qui
#: borne ce qu'il atteint.
AGENTS_FORMATEUR: frozenset[str] = frozenset(
    {
        "agent_assistant_formateur",
        "agent_generaliste_readonly",
        "agent_statistique",
        "agent_personnel",
    }
)


@dataclass(frozen=True, slots=True)
class Perimetre:
    """Ce qu'un compte a le droit d'atteindre.

    Attributes:
        role: rôle de compte de l'utilisateur (``RoleUtilisateur``).
        utilisateur_id: identifiant du compte (traçabilité, ``None`` si lab).
        equipe_id: personne du vivier liée (``None`` pour un administrateur).
        missions_autorisees: missions où la personne est affectée et approuvée.
        equipes_autorisees: personnes visibles — sa propre fiche et les collègues
            affectés aux mêmes missions (l'``answer`` validée : « profils et CV
            des personnes affectées à la même mission »).
        est_complet: accès complet au métier (administrateur, équipe interne).
    """

    role: str | None = None
    utilisateur_id: UUID | None = None
    equipe_id: UUID | None = None
    missions_autorisees: frozenset[UUID] = field(default_factory=frozenset)
    equipes_autorisees: frozenset[UUID] = field(default_factory=frozenset)
    est_complet: bool = True

    @property
    def est_restreint(self) -> bool:
        """Vrai quand le compte n'a accès qu'à son périmètre (formateur)."""
        return not self.est_complet

    def mission_autorisee(self, mission_id: UUID | None) -> bool:
        """Vrai si la mission est dans le périmètre (accès complet ⇒ toujours)."""
        if self.est_complet:
            return True
        return mission_id is not None and mission_id in self.missions_autorisees

    def equipe_autorisee(self, equipe_id: UUID | None) -> bool:
        """Vrai si la personne du vivier est visible (elle-même ou un collègue)."""
        if self.est_complet:
            return True
        return equipe_id is not None and equipe_id in self.equipes_autorisees

    def exiger_complet(self, action: str) -> None:
        """Refuse une action de pilotage à un compte restreint (défense en profondeur).

        Raises:
            PermissionDeniedError: périmètre restreint — l'action demande un
                accès complet au métier (création, statut, offre, affectation…).
        """
        if self.est_restreint:
            raise PermissionDeniedError(
                f"Action réservée à l'équipe interne : {action}",
                details={"role": self.role},
            )


#: Accès complet implicite (tests, scripts, appels internes hors requête).
PERIMETRE_COMPLET = Perimetre(est_complet=True)

#: Périmètre restreint **sans aucune mission** : refus par défaut quand le compte
#: lié à l'appel est introuvable (on ne présume jamais d'un droit).
PERIMETRE_VIDE = Perimetre(est_complet=False)


def est_interne(role: str | None) -> bool:
    """Vrai pour l'administration et l'équipe interne (accès complet au métier)."""
    return role in ROLES_INTERNES

_PERIMETRE: ContextVar[Perimetre | None] = ContextVar("perimetre_courant", default=None)


def perimetre_effectif() -> Perimetre:
    """Périmètre posé dans le contexte courant, sinon accès complet implicite."""
    return _PERIMETRE.get() or PERIMETRE_COMPLET


def poser_perimetre(perimetre: Perimetre) -> Token[Perimetre | None]:
    """Pose le périmètre du contexte (retourne le jeton de restauration)."""
    return _PERIMETRE.set(perimetre)


def retablir_perimetre(jeton: Token[Perimetre | None]) -> None:
    """Restaure le périmètre précédent (chemin agentique : un run = un contexte)."""
    _PERIMETRE.reset(jeton)


def pour_utilisateur(session: Session, utilisateur: Utilisateur) -> Perimetre:
    """Calcule le périmètre d'un compte depuis ses affectations approuvées.

    Args:
        session: session SQLAlchemy (lecture seule).
        utilisateur: compte authentifié (rôle + personne du vivier liée).

    Returns:
        Accès complet pour l'administrateur et l'équipe interne ; pour un
        formateur, l'ensemble des missions où il est affecté **et approuvé**,
        plus les personnes affectées à ces mêmes missions. Sans personne du
        vivier liée, le périmètre est vide.
    """
    if utilisateur.role != RoleUtilisateur.FORMATEUR.value:
        return Perimetre(
            role=utilisateur.role,
            utilisateur_id=utilisateur.id,
            equipe_id=utilisateur.equipe_id,
            est_complet=True,
        )
    if utilisateur.equipe_id is None:
        return Perimetre(
            role=utilisateur.role,
            utilisateur_id=utilisateur.id,
            equipe_id=None,
            est_complet=False,
        )

    affectations = AffectationEquipeRepository(session)
    miennes = affectations.list_approuvees_pour_equipe(utilisateur.equipe_id)
    missions = frozenset(affectation.mission_id for affectation in miennes)
    collegues = affectations.list_approuvees_pour_missions(missions)
    equipes = frozenset({utilisateur.equipe_id}) | frozenset(
        affectation.equipe_id for affectation in collegues
    )
    return Perimetre(
        role=utilisateur.role,
        utilisateur_id=utilisateur.id,
        equipe_id=utilisateur.equipe_id,
        missions_autorisees=missions,
        equipes_autorisees=equipes,
        est_complet=False,
    )


__all__ = [
    "AGENTS_FORMATEUR",
    "PERIMETRE_COMPLET",
    "PERIMETRE_VIDE",
    "ROLES_INTERNES",
    "Perimetre",
    "est_interne",
    "perimetre_effectif",
    "poser_perimetre",
    "pour_utilisateur",
    "retablir_perimetre",
]
