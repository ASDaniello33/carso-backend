"""Service — skills « automatiques » écrits par l'administration.

Deux familles de skills coexistent, et il ne faut pas les confondre :

- les skills de **l'organisation** (``app/domain/skill.py``) : un texte court
  stocké en base, invoqué par ``/nom`` dans le chat et recopié en entier dans le
  prompt de **tous** les runs. Leur écriture vit dans ``SkillsService`` ;
- les skills **Deep Agents** : un répertoire ``<racine>/<nom>/SKILL.md`` dont le
  frontmatter (``name``, ``description``) entre dans le contexte de l'agent
  déclaré, et dont le **corps n'est lu qu'à la demande**, quand la tâche relève
  de la description. C'est le second paramètre demandé : l'agent décide seul de
  charger le guide, sans que l'utilisateur ait à taper ``/nom``.

Jusqu'ici, cette seconde famille ne pouvait être créée qu'en déposant un dossier
à la main sur le disque. Ce service la rend administrable depuis
Paramètres > Skills, avec exactement la structure attendue par Deep Agents :

```
<racine>/<nom>/SKILL.md
```

Trois règles, pour que l'écriture ne casse aucun agent :

1. **le nom est canonique** (même normalisation que les skills d'organisation) et
   **immuable** après création : un renommage laisserait des surcharges pointer
   vers un dossier absent, et un skill inconnu fait échouer le montage du graphe
   (``resoudre_skills`` lève). Renommer = supprimer puis recréer ;
2. **les skills livrés avec le paquet sont protégés** : ils ne portent pas le
   marqueur ``carso_origine`` et sont refusés en modification comme en
   suppression — ils voyagent avec le code, pas avec les données ;
3. **le rattachement est retiré avant toute suppression** : une surcharge qui
   cite un skill disparu empêcherait l'agent de se monter. L'ordre inverse serait
   une panne silencieuse au premier message suivant.

Aucun secret n'a sa place dans un skill : le contenu est renvoyé par l'API et
recopié (borné) dans l'audit.
"""

from __future__ import annotations

import re
from pathlib import Path
from shutil import rmtree

from sqlalchemy.orm import Session

from app.agents.registre_skills import (
    description_skill,
    frontmatter_skill,
    lister_skills,
    racine_skills,
)
from app.application.services.parametres_service import ParametresAgentService
from app.application.services.skills_service import normaliser_nom
from app.application.trace import TraceContext
from app.core.errors import NotFoundError
from app.core.errors import ValidationError as BusinessValidationError
from app.domain.enums import ActionAudit, ActorType

#: Marqueur de propriété : seuls les skills écrits **depuis l'écran** le portent.
#: Les skills du paquet n'en ont pas et restent en lecture seule.
ORIGINE_ADMIN = "admin"

#: Clé du frontmatter qui distingue les deux origines.
CLE_ORIGINE = "carso_origine"

#: Longueurs admises (le nom est aussi un nom de dossier).
NOM_MIN = 2
NOM_MAX = 40
DESCRIPTION_MAX = 400
#: Le corps n'entre pas dans le prompt : il est lu à la demande. La borne
#: protège le disque et l'écran, pas le budget de contexte.
CORPS_MIN = 20
CORPS_MAX = 20_000

_NOM_VALIDE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_FRONTMATTER = "---"


def _valider_nom(nom: str) -> str:
    """Nom canonique d'un dossier de skill, ou erreur actionnable."""
    canonique = normaliser_nom(nom)
    if not canonique:
        raise BusinessValidationError(
            "Nom de skill requis (lettres, chiffres et tirets)",
            details={"nom": nom},
        )
    if not NOM_MIN <= len(canonique) <= NOM_MAX:
        raise BusinessValidationError(
            f"Nom de skill : {NOM_MIN} à {NOM_MAX} caractères après normalisation",
            details={"nom": canonique, "longueur": len(canonique)},
        )
    if not _NOM_VALIDE.match(canonique):
        raise BusinessValidationError(
            "Nom de skill invalide : il doit commencer par une lettre ou un chiffre",
            details={"nom": canonique},
        )
    return canonique


def _valider_description(description: str) -> str:
    """Description sur une ligne : c'est le texte que lit l'agent pour décider."""
    phrase = " ".join((description or "").split())
    if not phrase:
        raise BusinessValidationError(
            "Description requise : elle dit quand charger le skill",
            details={},
        )
    if len(phrase) > DESCRIPTION_MAX:
        raise BusinessValidationError(
            f"Description trop longue (maximum {DESCRIPTION_MAX} caractères)",
            details={"longueur": len(phrase), "maximum": DESCRIPTION_MAX},
        )
    return phrase


def _valider_corps(corps: str) -> str:
    """Corps du guide : borné, mais large — il n'est pas injecté dans le prompt."""
    texte = (corps or "").strip()
    if len(texte) < CORPS_MIN:
        raise BusinessValidationError(
            f"Contenu du skill trop court (minimum {CORPS_MIN} caractères)",
            details={"longueur": len(texte)},
        )
    if len(texte) > CORPS_MAX:
        raise BusinessValidationError(
            f"Contenu du skill trop long (maximum {CORPS_MAX} caractères)",
            details={"longueur": len(texte), "maximum": CORPS_MAX},
        )
    return texte


def _dossier(nom: str) -> Path:
    """Dossier absolu d'un skill, contenu dans la racine (jamais ailleurs).

    Le nom a déjà été validé, mais le contrôle de confinement reste : c'est lui
    qui garantit qu'aucun chemin ne sort de ``skills_root``, y compris si une
    valeur arrivait d'ailleurs que de l'API.
    """
    racine = racine_skills().resolve()
    dossier = (racine / nom).resolve()
    try:
        dossier.relative_to(racine)
    except ValueError as exc:
        raise BusinessValidationError(
            f"Skill hors racine : {nom!r}", details={"skill": nom}
        ) from exc
    return dossier


def _origine(chemin: Path) -> str:
    """``admin`` si le fichier porte notre marqueur, ``paquet`` sinon."""
    try:
        bloc = frontmatter_skill(chemin)
    except OSError:
        return "paquet"
    if not bloc:
        return "paquet"
    for ligne in bloc.splitlines():
        if ligne.strip().lower() == f"{CLE_ORIGINE}: {ORIGINE_ADMIN}":
            return ORIGINE_ADMIN
    return "paquet"


def corps_skill(chemin: Path) -> str:
    """Texte qui suit le frontmatter (le guide lui-même)."""
    try:
        texte = chemin.read_text(encoding="utf-8")
    except OSError:
        return ""
    lignes = texte.lstrip().splitlines()
    if not lignes or lignes[0].strip() != _FRONTMATTER:
        return texte.strip()
    for index, ligne in enumerate(lignes[1:], start=1):
        if ligne.strip() == _FRONTMATTER:
            return "\n".join(lignes[index + 1 :]).strip()
    return ""


def texte_skill(chemin: Path) -> str:
    """Contenu exact du fichier, tel que l'agent le télécharge.

    L'aperçu de l'écran montre ce texte, sans recomposition : un rendu fabriqué
    côté interface pourrait diverger de ce que lit réellement l'agent (ordre des
    clés, pliage de la description), et l'aperçu perdrait son unique intérêt.
    """
    try:
        return chemin.read_text(encoding="utf-8")
    except OSError:
        return ""


def _contenu(nom: str, description: str, corps: str) -> str:
    """Fichier ``SKILL.md`` complet.

    ``description: >`` est un scalaire plié YAML : il supporte les deux-points,
    les apostrophes et les guillemets sans échappement, ce qu'un simple
    ``description: texte`` ne garantit pas. ``carso_origine`` marque la
    propriété : c'est lui qui autorise la modification et la suppression.
    """
    pliee = "\n".join(f"  {ligne}" for ligne in description.splitlines() if ligne.strip())
    return (
        f"{_FRONTMATTER}\n"
        f"name: {nom}\n"
        f"description: >\n{pliee}\n"
        f"{CLE_ORIGINE}: {ORIGINE_ADMIN}\n"
        f"{_FRONTMATTER}\n\n"
        f"{corps}\n"
    )


class SkillsFichiersService:
    """Création, lecture, correction et suppression des skills ``SKILL.md``.

    L'autorisation (administrateur) est portée par la route. Le service applique
    les règles de contenu, de propriété et l'ordre des opérations (détacher avant
    de supprimer).
    """

    def __init__(self, session: Session, actor_id: str | None = None) -> None:
        self._session = session
        self._trace = TraceContext(session, actor_id=actor_id)
        # Résolu paresseusement : ``parametres_service`` importe le registre de
        # skills, un import au chargement créerait un cycle avec ce module.
        self._surcharges: ParametresAgentService | None = None

    # --- lectures -------------------------------------------------------------

    def lister(self) -> list[dict[str, object]]:
        """Skills présents sur le disque, avec leur origine et leurs agents."""
        racine = racine_skills()
        usages = self._usages()
        elements: list[dict[str, object]] = []
        for nom in lister_skills():
            chemin = racine / nom / "SKILL.md"
            elements.append(
                {
                    "nom": nom,
                    "description": description_skill(chemin),
                    "origine": _origine(chemin),
                    "agents": usages.get(nom, []),
                }
            )
        return elements

    def lire(self, nom: str) -> dict[str, object]:
        """Skill complet (description + corps), pour préremplir la correction."""
        canonique = _valider_nom(nom)
        chemin = _dossier(canonique) / "SKILL.md"
        if not chemin.is_file():
            raise NotFoundError(
                "Skill introuvable", details={"skill": canonique, "disponibles": lister_skills()}
            )
        return {
            "nom": canonique,
            "description": description_skill(chemin),
            "corps": corps_skill(chemin),
            # Texte intégral : c'est lui que l'aperçu affiche, et lui que l'agent
            # reçoit au démarrage (frontmatter) puis à la demande (corps).
            "apercu": texte_skill(chemin),
            "origine": _origine(chemin),
            "agents": self._usages().get(canonique, []),
        }

    # --- écritures ------------------------------------------------------------

    def creer(
        self,
        *,
        nom: str,
        description: str,
        corps: str,
        agents: list[str] | None = None,
        modifie_par: str | None = None,
    ) -> dict[str, object]:
        """Crée ``<racine>/<nom>/SKILL.md`` puis le rattache aux agents choisis.

        Raises:
            BusinessValidationError: nom invalide ou déjà pris, contenu hors
                bornes, ou dossier non inscriptible.
        """
        canonique = _valider_nom(nom)
        phrase = _valider_description(description)
        texte = _valider_corps(corps)
        if canonique in lister_skills():
            raise BusinessValidationError(
                f"Un skill nommé {canonique!r} existe déjà : corrigez-le ou supprimez-le",
                details={"nom": canonique},
            )
        dossier = _dossier(canonique)
        try:
            dossier.mkdir(parents=True, exist_ok=False)
        except FileExistsError as exc:  # pragma: no cover - défense en profondeur
            raise BusinessValidationError(
                f"Un dossier {canonique!r} existe déjà dans la racine des skills",
                details={"nom": canonique},
            ) from exc
        except OSError as exc:
            raise BusinessValidationError(
                "Racine des skills non inscriptible : pointez SKILLS_ROOT vers un "
                "volume persistant, ou déposez le dossier à la main",
                details={"racine": str(dossier.parent), "cause": str(exc)},
            ) from exc

        chemin = dossier / "SKILL.md"
        try:
            chemin.write_text(_contenu(canonique, phrase, texte), encoding="utf-8")
        except OSError as exc:
            # Un dossier à moitié écrit ferait un skill sans frontmatter : il
            # ferait échouer le montage de l'agent. On retire ce qu'on vient de
            # créer avant de rendre l'erreur.
            rmtree(dossier, ignore_errors=True)
            raise BusinessValidationError(
                "Écriture du SKILL.md impossible",
                details={"racine": str(dossier.parent), "cause": str(exc)},
            ) from exc

        self._trace.record_event(
            actor_type=ActorType.HUMAIN.value,
            action=ActionAudit.SKILL_AGENT_ENREGISTRE.value,
            entity_type="skill_fichier",
            entity_id=canonique,
            after={
                "nom": canonique,
                "description": phrase,
                "longueur_corps": len(texte),
                "agents": sorted(agents or []),
            },
        )
        self._rattacher(canonique, agents or [], modifie_par=modifie_par)
        return self.lire(canonique)

    def modifier(
        self,
        nom: str,
        *,
        description: str | None = None,
        corps: str | None = None,
        agents: list[str] | None = None,
        modifie_par: str | None = None,
    ) -> dict[str, object]:
        """Corrige un skill administré ; ``nom`` n'est jamais modifiable.

        ``None`` conserve le champ. Le fichier est réécrit en une passe : l'état
        intermédiaire n'est jamais visible d'un agent.

        Raises:
            NotFoundError: skill absent.
            BusinessValidationError: skill du paquet, ou contenu refusé.
        """
        canonique = _valider_nom(nom)
        chemin = _dossier(canonique) / "SKILL.md"
        if not chemin.is_file():
            raise NotFoundError(
                "Skill introuvable", details={"skill": canonique, "disponibles": lister_skills()}
            )
        if _origine(chemin) != ORIGINE_ADMIN:
            raise BusinessValidationError(
                f"Skill {canonique!r} livré avec le paquet : il se modifie dans le code, "
                "pas depuis l'écran",
                details={"skill": canonique, "origine": "paquet"},
            )

        ancienne_description = description_skill(chemin)
        ancien_corps = corps_skill(chemin)
        phrase = (
            _valider_description(description)
            if description is not None
            else _valider_description(ancienne_description)
        )
        texte = (
            _valider_corps(corps) if corps is not None else _valider_corps(ancien_corps)
        )
        try:
            chemin.write_text(_contenu(canonique, phrase, texte), encoding="utf-8")
        except OSError as exc:
            raise BusinessValidationError(
                "Écriture du SKILL.md impossible",
                details={"racine": str(chemin.parent), "cause": str(exc)},
            ) from exc

        self._trace.record_event(
            actor_type=ActorType.HUMAIN.value,
            action=ActionAudit.SKILL_AGENT_ENREGISTRE.value,
            entity_type="skill_fichier",
            entity_id=canonique,
            before={"nom": canonique, "description": ancienne_description},
            after={"nom": canonique, "description": phrase},
            event_metadata={
                "operation": "modification",
                "agents": sorted(agents) if agents is not None else None,
            },
        )
        if agents is not None:
            self._rattacher(canonique, agents, modifie_par=modifie_par)
        return self.lire(canonique)

    def supprimer(self, nom: str, *, modifie_par: str | None = None) -> None:
        """Retire le skill des agents **puis** efface son dossier.

        L'ordre est la règle : une surcharge qui cite un skill absent fait lever
        ``resoudre_skills`` au montage du graphe, donc l'agent entier devient
        inutilisable. Détacher d'abord rend la suppression sûre.

        Raises:
            NotFoundError: skill absent.
            BusinessValidationError: skill du paquet.
        """
        canonique = _valider_nom(nom)
        dossier = _dossier(canonique)
        chemin = dossier / "SKILL.md"
        if not chemin.is_file():
            raise NotFoundError(
                "Skill introuvable", details={"skill": canonique, "disponibles": lister_skills()}
            )
        if _origine(chemin) != ORIGINE_ADMIN:
            raise BusinessValidationError(
                f"Skill {canonique!r} livré avec le paquet : il ne se supprime pas depuis l'écran",
                details={"skill": canonique, "origine": "paquet"},
            )

        self._rattacher(canonique, [], modifie_par=modifie_par)
        description = description_skill(chemin)
        try:
            rmtree(dossier)
        except OSError as exc:
            raise BusinessValidationError(
                "Suppression du dossier impossible",
                details={"racine": str(dossier.parent), "cause": str(exc)},
            ) from exc

        self._trace.record_event(
            actor_type=ActorType.HUMAIN.value,
            action=ActionAudit.SKILL_SUPPRIME.value,
            entity_type="skill_fichier",
            entity_id=canonique,
            before={"nom": canonique, "description": description},
        )

    # --- rattachement ---------------------------------------------------------

    def _rattacher(self, nom: str, agents: list[str], *, modifie_par: str | None) -> None:
        """Applique la liste d'agents d'un skill (ajout **et** retrait).

        Les agents non cités qui utilisaient déjà ce skill le perdent : la liste
        reçue est la vérité, pas un ajout incrémental — sans quoi décocher une
        case n'aurait aucun effet.
        """
        if self._surcharges is None:
            self._surcharges = ParametresAgentService(self._session, actor_id=modifie_par)
        self._surcharges.definir_skill_pour_agents(
            nom, agents, modifie_par=modifie_par
        )

    def _usages(self) -> dict[str, list[str]]:
        """Agents qui reçoivent déjà chaque skill (surcharges actives)."""
        from app.infrastructure.repositories.parametres import SurchargeAgentRepository

        usages: dict[str, list[str]] = {}
        for ligne in SurchargeAgentRepository(self._session).lister_actives():
            for nom in ligne.skills_ajoutes or []:
                usages.setdefault(nom, []).append(ligne.agent_id)
        for noms in usages.values():
            noms.sort()
        return usages


__all__ = [
    "CORPS_MAX",
    "DESCRIPTION_MAX",
    "ORIGINE_ADMIN",
    "SkillsFichiersService",
    "corps_skill",
    "texte_skill",
]
