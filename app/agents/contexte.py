"""Contexte d'exécution d'un agent—**l'utilisateur connecté**, tel que le
serveur le connaît.

Pourquoi ce module
------------------
Un agent qui ne sait pas *qui* lui parle répond à côté—il propose un geste
interdit, ou il cherche une donnée que le compte n'a pas le droit de voir.
LangChain traite ce besoin avec un **contexte d'exécution typé**
(``context_schema``) que les tools lisent via ``runtime.context`` et que le
middleware peut injecter dans le prompt système.

Deux voies transportent ce contexte chez CARSO :

1. la voie explicite—``context_schema=UserContext`` déclarée au graphe, et
   ``context=UserContext(...)`` passée à l'invocation (tests, scripts, futurs
   transports, tâches inter-agents) ;
2. la voie du chat AG-UI—l'adaptateur ``ag-ui-langgraph`` monte chaque graphe
   avec une configuration **partagée par tous les fils**—il ne peut pas y
   placer l'identité d'un utilisateur. La garde HTTP (``preparer_acces_agent``)
   pose donc le contexte dans un ``ContextVar``—le même patron que le
   périmètre—et il voyage dans les tâches filles du run, dont l'exécution des
   tools.

Conséquence directe—sur ce chemin, ``runtime.context`` n'est pas une
identité mais la configuration de reprise (``thread_id``…), reconstruite par
l'adaptateur. C'est pourquoi :func:`contexte_du_runtime` traite le
``ContextVar`` comme autorité, et la classe note les clés absorbées
(:attr:`UserContext.cles_execution`) pour distinguer les deux origines.

Les deux voies sont **construites par le serveur**—rien ne vient du client.
Un contexte envoyé par le navigateur (``RunAgentInput.context``) n'est jamais
une source d'autorisation—c'est du texte d'affichage, pas un droit.

Ce que le contexte apporte
--------------------------
- **au modèle**—un bloc de prompt qui dit qui parle (nom, rôle, profil, ses
  missions) et ce qu'il peut faire—l'agent arrête de proposer l'impossible ;
- **aux tools**—``runtime.context`` (ou le repli serveur)—le contrôle
  d'exécution ne dépend jamais de la bonne volonté du prompt ;
- **à l'infrastructure**—un refus **actionnable** (« Accès refusé … Ce que
  vous pouvez faire—… ») qui revient au modèle comme résultat d'outil, pour
  qu'il propose l'alternative au lieu d'insister.
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import MISSING, dataclass, field, fields
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.application.perimetre import (
    AGENTS_FORMATEUR,
    Perimetre,
    est_interne,
    pour_utilisateur,
)
from app.domain.identity import Utilisateur

#: Madagascar n'a pas d'heure d'été : UTC+3 toute l'année. Le fuseau est fixé
#: ici plutôt que lu sur la machine (et sans dépendre de la base tzdata).
FUSEAU_CARSO = timezone(timedelta(hours=3), "EAT")

_JOURS = ("lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche")
_MOIS = (
    "janvier",
    "février",
    "mars",
    "avril",
    "mai",
    "juin",
    "juillet",
    "août",
    "septembre",
    "octobre",
    "novembre",
    "décembre",
)


def _date_longue(instant: datetime) -> str:
    """Date du jour en clair, à Toliara, avec sa forme ISO et l'heure locale.

    Un agent a besoin des deux : « vendredi 30 octobre 2026 » pour parler, et
    « 2026-10-30 » pour comparer une échéance ou calculer une ancienneté.
    """
    local = instant.astimezone(FUSEAU_CARSO)
    return (
        f"{_JOURS[local.weekday()]} {local.day} {_MOIS[local.month - 1]} "
        f"{local.year} ({local.strftime('%Y-%m-%d')}, "
        f"{local.strftime('%H:%M')} à Toliara, UTC+3)"
    )

try:  # pragma: no cover - dépend de l'extra agents
    from langchain.agents.middleware.types import AgentMiddleware as _AgentMiddleware
except ImportError:  # pragma: no cover - extra agents absent
    _AgentMiddleware = object  # type: ignore[misc,assignment]


def outils_du_role(role: str | None) -> frozenset[str]:
    """Tools qu'un compte restreint peut réellement exécuter.

    Un compte restreint (formateur) n'ouvre que ``AGENTS_FORMATEUR`` : ses
    outils exécutables sont **exactement** ceux déclarés par ces agents (plus
    leurs outils d'affichage, qui ne touchent aucune donnée). Aucune liste n'est
    écrite à la main : ajouter un tool au contrat d'un de ces agents l'ouvre, le
    retirer le ferme. Pour l'équipe interne, l'ensemble est vide — « vide »
    signifiant ici « pas de filtre » (accès complet).
    """
    if est_interne(role):
        return frozenset()
    from app.agents.catalogue import AGENT_DEFINITIONS
    from app.agents.frontend_tools_catalogue import noms_pour_agent

    noms: set[str] = set()
    for definition in AGENT_DEFINITIONS:
        if definition.agent_id not in AGENTS_FORMATEUR:
            continue
        noms.update(definition.allowed_tools())
        noms.update(noms_pour_agent(definition.agent_id))
    return frozenset(noms)


@dataclass(frozen=True, slots=True, init=False)
class UserContext:
    """Ce que l'agent sait de la personne qui lui parle (jamais deviné).

    Constructeur tolérant aux clés d'exécution
    ------------------------------------------
    L'adaptateur AG-UI ne passe pas ``context=`` : il fait de la
    configuration **partagée par tous les fils** le contexte du graphe
    (``get_stream_kwargs`` : ``base_context.update(config['configurable'])``).
    Le runtime ``langgraph`` coerce alors ce dictionnaire avec le schéma du
    graphe (``context_schema(**context)``). Or cette configuration porte les
    clés de reprise — ``thread_id`` (contrôlé par le client), et
    ``checkpoint_id`` / ``checkpoint_ns`` sur les chemins *fork* /
    *regenerate* — qu'aucun champ métier ne décrit : le run se soldait par
    ``TypeError: UserContext.__init__() got an unexpected keyword argument
    'thread_id'`` et **tous** les agents tombaient.

    Le constructeur absorbe donc les clés inconnues **sans les appliquer** et
    les note dans :attr:`cles_execution`. Cette note est ce qui distingue un
    contexte construit par le serveur d'un contexte reconstruit par
    l'adaptateur : voir :func:`contexte_du_runtime`. Absorber une clé
    inconnue n'est jamais élargir un droit.

    Attributes:
        utilisateur_id: identifiant du compte (traçabilité, ``None`` en lab).
        email: courriel du compte (affichage, jamais un secret).
        nom_complet: prénom + nom, tels qu'enregistrés.
        role: rôle de compte (``administrateur``, ``collaborateur``,
            ``formateur``, ``en_attente``).
        profil: profil métier de la personne du vivier liée, s'il existe.
        equipe_id: personne du vivier liée au compte (``None`` sinon).
        est_interne: accès complet au métier (admin + équipe interne).
        missions_autorisees: missions affectées et approuvées (libellés).
        agents_ouverts: agents AG-UI que ce compte peut ouvrir.
        outils_autorises: tools exécutables ; **vide = pas de filtre**.
        perimetre: périmètre de données calculé (source unique du filtrage).
        cles_execution: clés LangGraph absorbées à la construction
            (``thread_id``, ``checkpoint_id``…). **Vide = contexte construit
            par le serveur** ; non vide = contexte reconstruit par l'adaptateur
            à partir de la configuration partagée, donc sans identité — il ne
            sert jamais d'autorité (hors comparaison / affichage).
    """

    utilisateur_id: str | None = None
    email: str | None = None
    nom_complet: str = ""
    role: str = ""
    profil: str | None = None
    equipe_id: str | None = None
    est_interne: bool = True
    missions_autorisees: tuple[str, ...] = ()
    agents_ouverts: tuple[str, ...] = ()
    outils_autorises: frozenset[str] = field(default_factory=frozenset)
    perimetre: Perimetre | None = None
    cles_execution: tuple[str, ...] = field(default=(), compare=False, repr=False)

    def __init__(self, *args: Any, **champs: Any) -> None:
        """Construit le contexte, en absorbant les clés d'exécution inconnues.

        Les champs déclarés sont acceptés comme arguments nommés ou
        positionnels (ordre de déclaration). Toute autre clé — ``thread_id``
        et consorts — est **ignorée** et mémorisée dans
        :attr:`cles_execution` : elle décrit la reprise du run, pas la
        personne qui parle.
        """
        declares = fields(self)
        noms = [champ.name for champ in declares]
        if len(args) > len(noms):
            raise TypeError(
                f"{type(self).__name__}.__init__() accepte au plus "
                f"{len(noms)} arguments positionnels"
            )
        valeurs: dict[str, Any] = dict(zip(noms, args, strict=False))
        inconnus = tuple(sorted(set(champs) - set(noms)))
        for champ in declares:
            if champ.name in champs:
                valeurs[champ.name] = champs[champ.name]
            elif champ.name not in valeurs:
                if champ.default is not MISSING:
                    valeurs[champ.name] = champ.default
                elif champ.default_factory is not MISSING:  # type: ignore[misc]
                    valeurs[champ.name] = champ.default_factory()  # type: ignore[misc]
                else:
                    raise TypeError(
                        f"{type(self).__name__}.__init__() : "
                        f"champ obligatoire manquant « {champ.name} »"
                    )
        explicite = champs.get("cles_execution")
        valeurs["cles_execution"] = inconnus if explicite is None else tuple(explicite)
        for champ in declares:
            object.__setattr__(self, champ.name, valeurs[champ.name])

    @property
    def est_construit_par_le_serveur(self) -> bool:
        """Vrai quand ce contexte vient du serveur (et non de l'adaptateur)."""
        return not self.cles_execution

    @property
    def est_restreint(self) -> bool:
        """Vrai quand le compte n'a accès qu'à son périmètre (formateur)."""
        return not self.est_interne

    def peut_executer(self, outil: str) -> bool:
        """Vrai si ce compte a le droit d'exécuter cet outil."""
        if self.est_interne or not self.outils_autorises:
            return True
        return outil in self.outils_autorises

    def bloc_prompt(self, maintenant: datetime | None = None) -> str:
        """Bloc de prompt : qui parle, ce qu'il peut faire, et quel jour on est.

        Écrit pour un agent CARSO : il dit le rôle, le périmètre, et surtout ce
        qui est **hors** périmètre — c'est ce qui empêche de proposer un geste
        refusé en boucle. La date du jour y figure : sans elle, « cette
        semaine » se déduit du document lu, et une donnée de la semaine
        dernière passe pour actuelle.

        Args:
            maintenant: instant de référence (les tests en fixent un).
        """
        lignes = ["## Contexte de l'utilisateur connecté (fourni par le serveur)"]
        lignes.append(f"- Nom : {self.nom_complet or 'compte de service'}")
        lignes.append(f"- Rôle : {self.role or 'inconnu'}")
        lignes.append(
            f"- Date du jour : {_date_longue(maintenant or datetime.now(UTC))}."
        )
        lignes.append(
            "- « Aujourd'hui », « cette semaine », une échéance, un retard ou une "
            "ancienneté se calculent à partir de cette date — jamais déduits d'un "
            "document."
        )
        lignes.append(
            "- Une donnée datée est présentée avec sa date (ou son âge) ; si elle est "
            "ancienne, dis-le au lieu de la présenter comme actuelle."
        )
        if self.profil:
            lignes.append(f"- Profil : {self.profil}")
        if self.est_interne:
            lignes.append(
                "- Accès : équipe interne — tu peux consulter tout le métier "
                "autorisé par tes outils."
            )
        else:
            lignes.append(
                "- Accès : **restreint** — tu ne vois que ses missions, leurs "
                "sessions, leurs documents et leurs bénéficiaires."
            )
            if self.missions_autorisees:
                lignes.append(
                    "- Ses missions : " + ", ".join(self.missions_autorisees)
                )
            else:
                lignes.append(
                    "- Ses missions : aucune à ce jour — dis-le franchement, ne "
                    "cherche pas ailleurs."
                )
            lignes.append(
                "- Tu n'exécutes pas d'action de pilotage (créer, valider, "
                "affecter, supprimer une entité métier) : oriente vers l'équipe "
                "interne."
            )
        if self.agents_ouverts:
            lignes.append("- Agents ouverts à ce compte : " + ", ".join(self.agents_ouverts))
        return "\n".join(lignes)


def depuis(
    session: Session,
    utilisateur: Utilisateur,
    *,
    perimetre: Perimetre | None = None,
) -> UserContext:
    """Construit le contexte d'un compte, **depuis les données enregistrées**.

    Args:
        session: session SQLAlchemy (lecture seule).
        utilisateur: compte authentifié.
        perimetre: périmètre déjà calculé (évite un second calcul dans la garde
            HTTP) ; ``None`` le calcule ici.

    Returns:
        Le contexte du compte : identité, rôle, périmètre et surface d'outils.
    """
    effectif = perimetre if perimetre is not None else pour_utilisateur(session, utilisateur)
    ouvert = est_interne(utilisateur.role)
    noms_missions: tuple[str, ...] = ()
    if not ouvert and effectif.missions_autorisees:
        # Import local : le module de consultation porte les lectures métier de
        # l'agent — la garde HTTP (qui construit ce contexte) n'a pas à charger
        # l'outillage des agents pour connaître l'identité d'un compte.
        from app.agents.consultation import libelles_missions

        noms_missions = tuple(libelles_missions(session, effectif.missions_autorisees))
    return UserContext(
        utilisateur_id=str(utilisateur.id) if utilisateur.id else None,
        email=utilisateur.email,
        nom_complet=" ".join(
            partie for partie in (utilisateur.prenom, utilisateur.nom) if partie
        ),
        role=utilisateur.role,
        profil=_profil_du_vivier(session, utilisateur),
        equipe_id=str(utilisateur.equipe_id) if utilisateur.equipe_id else None,
        est_interne=ouvert,
        missions_autorisees=noms_missions,
        agents_ouverts=tuple(sorted(agents_pour_role(utilisateur.role))),
        outils_autorises=outils_du_role(utilisateur.role),
        perimetre=effectif,
    )


def agents_pour_role(role: str | None) -> frozenset[str]:
    """Agents AG-UI ouverts à ce rôle (règle partagée avec les catalogues)."""
    from app.agents.catalogue import AGENT_DEFINITIONS

    if est_interne(role):
        return frozenset(definition.agent_id for definition in AGENT_DEFINITIONS)
    return AGENTS_FORMATEUR


def _profil_du_vivier(session: Session, utilisateur: Utilisateur) -> str | None:
    """Profil métier de la personne du vivier liée au compte (``None`` sinon)."""
    if utilisateur.equipe_id is None:
        return None
    from app.infrastructure.repositories import EquipeRepository

    personne = EquipeRepository(session).get(utilisateur.equipe_id)
    return personne.profil if personne is not None else None


# --- transport du contexte dans le run ------------------------------------------

_CONTEXTE: ContextVar[UserContext | None] = ContextVar("contexte_utilisateur", default=None)


def poser_contexte_utilisateur(contexte: UserContext) -> Token[UserContext | None]:
    """Pose le contexte du run courant (retourne le jeton de restauration)."""
    return _CONTEXTE.set(contexte)


def retablir_contexte_utilisateur(jeton: Token[UserContext | None]) -> None:
    """Restaure le contexte précédent (un run = un contexte)."""
    _CONTEXTE.reset(jeton)


def contexte_utilisateur_effectif() -> UserContext | None:
    """Contexte posé dans le run courant, ``None`` hors requête utilisateur."""
    return _CONTEXTE.get()


def contexte_du_runtime(runtime: Any) -> UserContext | None:
    """Contexte d'un appel de tool : le ``ContextVar`` du serveur, d'abord.

    Deux voies transportent un contexte, et une seule fait autorité :

    1. le ``ContextVar`` posé par la garde serveur (``preparer_acces_agent``,
       mentions sociales) — il est repris par les tâches filles du run, dont
       l'exécution des tools ;
    2. ``runtime.context`` — la voie LangChain. Elle n'est retenue que si le
       contexte a été **construit par le serveur** (tests, scripts, tâches
       inter-agents).

    L'ordre est délibéré. Sur le chat AG-UI, l'adaptateur reconstruit
    ``runtime.context`` à partir de la configuration partagée, donc sans
    identité : ce contexte porterait `est_interne=True` par défaut et
    remplacerait le contexte réel du compte par un accès complet. Le retenir
    en repli seulement — et refuser tout contexte porteur de clés d'exécution
    — supprime à la fois la panne (``thread_id``) et cette escalade latente.
    Les deux voies sortent du serveur : jamais du corps de la requête.
    """
    pose = contexte_utilisateur_effectif()
    if pose is not None:
        return pose
    candidat = getattr(runtime, "context", None)
    if isinstance(candidat, UserContext) and candidat.est_construit_par_le_serveur:
        return candidat
    return None


def outils_autorises_pour(runtime: Any) -> frozenset[str]:
    """Surface d'outils du compte courant (vide = accès complet)."""
    contexte = contexte_du_runtime(runtime)
    return contexte.outils_autorises if contexte is not None else frozenset()


def refus_outil(outil: str) -> str:
    """Message de refus d'exécution — explicite et actionnable pour le modèle."""
    return (
        f"Accès refusé : l'outil « {outil} » n'est pas ouvert à ce compte. "
        "Ce compte travaille sur ses missions, ses sessions et leurs documents : "
        "consulte les données de son périmètre, prépare les documents de session, "
        "et pour toute action de pilotage (créer, valider, affecter, supprimer une "
        "entité métier), invite l'utilisateur à passer par l'équipe interne. "
        "N'insiste pas : aucun autre appel ne sera autorisé."
    )


class ContexteUtilisateurMiddleware(_AgentMiddleware):
    """Middleware de contexte : le prompt sait qui parle, le ToolNode contrôle.

    Deux responsabilités, un seul endroit :

    1. ``wrap_model_call`` — ajoute le bloc de contexte au prompt système
       (transitoire : rien n'est écrit dans l'état ni dans l'historique) ;
    2. ``wrap_tool_call`` — refuse l'exécution d'un outil hors de la surface du
       compte **avant** que le handler ne tourne, et renvoie au modèle un refus
       lisible plutôt qu'une exception. Un refus d'autorisation n'est pas une
       panne : le modèle doit pouvoir proposer autre chose.
    """

    def __init__(self, contexte: UserContext | None = None) -> None:
        self._contexte = contexte

    @property
    def name(self) -> str:
        return "ContexteUtilisateurMiddleware"

    # --- prompt ---------------------------------------------------------------

    def _contexte_du_run(self, request: Any) -> UserContext | None:
        """Contexte effectif : celui du run, sinon celui posé au montage."""
        return contexte_du_runtime(request.runtime) or self._contexte

    def _avec_contexte(self, request: Any) -> Any:
        contexte = self._contexte_du_run(request)
        if contexte is None:
            return request
        bloc = contexte.bloc_prompt()
        message = request.system_message
        contenu = getattr(message, "text", None) or getattr(message, "content", None)
        if not isinstance(contenu, str):
            return request
        if bloc in contenu:  # déjà injecté (pas de doublon entre les tours)
            return request
        from langchain_core.messages import SystemMessage

        return request.override(
            system_message=SystemMessage(content=f"{contenu}\n\n{bloc}")
        )

    def wrap_model_call(self, request: Any, handler: Any) -> Any:
        return handler(self._avec_contexte(request))

    async def awrap_model_call(self, request: Any, handler: Any) -> Any:
        return await handler(self._avec_contexte(request))

    # --- outils ---------------------------------------------------------------

    def _autorise(self, request: Any) -> str | None:
        """Nom de l'outil refusé, ou ``None`` si l'exécution est autorisée."""
        contexte = self._contexte_du_run(request)
        appel = getattr(request, "tool_call", None) or {}
        outil = appel.get("name") if isinstance(appel, dict) else None
        if not isinstance(outil, str):
            return None
        if contexte is None or contexte.peut_executer(outil):
            return None
        return outil

    @staticmethod
    def _refus(request: Any, outil: str) -> Any:
        """Réponse d'outil refusée : le modèle reçoit une raison, pas un crash."""
        from langchain_core.messages import ToolMessage

        appel = getattr(request, "tool_call", None) or {}
        return ToolMessage(
            content=refus_outil(outil),
            tool_call_id=appel.get("id", "") if isinstance(appel, dict) else "",
            status="error",
        )

    def wrap_tool_call(self, request: Any, handler: Any) -> Any:
        outil = self._autorise(request)
        if outil is not None:
            return self._refus(request, outil)
        return handler(request)

    async def awrap_tool_call(self, request: Any, handler: Any) -> Any:
        outil = self._autorise(request)
        if outil is not None:
            return self._refus(request, outil)
        return await handler(request)


__all__ = [
    "ContexteUtilisateurMiddleware",
    "UserContext",
    "agents_pour_role",
    "contexte_du_runtime",
    "contexte_utilisateur_effectif",
    "depuis",
    "outils_autorises_pour",
    "outils_du_role",
    "poser_contexte_utilisateur",
    "refus_outil",
    "retablir_contexte_utilisateur",
]
