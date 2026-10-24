"""Service — skills de l'organisation.

Un administrateur écrit les guides communs et les nomme ``/nom`` dans le chat ;
tout compte les invoque, aucun ne les modifie. Le service est le seul endroit où
les règles sont appliquées, pour que l'API et les appels internes ne divergent
pas :

- le nom est **normalisé** (minuscules, sans accents, tirets) : ``/Facturation
  Client`` et ``/facturation-client`` désignent le même skill, et un nom
  inutilisable est refusé avec la forme attendue ;
- deux skills ne peuvent pas porter le même nom — sinon ``/facturation`` serait
  ambigu pour le modèle comme pour l'utilisateur ;
- les bornes (nombre, longueurs) protègent le prompt de chaque run : un skill
  ne peut pas y pousser un manuel ;
- la suppression et la modification exigent l'identifiant du skill et rendent un
  404 s'il n'existe pas.

Le service ne fait pas de ``commit`` — la transaction appartient à la route.
Aucun secret n'est lu ni journalisé ici.
"""

from __future__ import annotations

import re
import unicodedata
from uuid import UUID

from sqlalchemy.orm import Session

from app.application.trace import TraceContext
from app.core.errors import NotFoundError
from app.core.errors import ValidationError as BusinessValidationError
from app.domain.enums import ActionAudit, ActorType
from app.domain.skill import INSTRUCTIONS_MAX, MAX_SKILLS, Skill
from app.infrastructure.repositories.skill import SkillRepository

#: Type d'entité de l'audit.
_ENTITY = "skill"

#: Longueurs admises pour le nom (après normalisation).
NOM_MIN = 2
NOM_MAX = 40

#: Une phrase : le menu du chat n'affiche pas un paragraphe.
DESCRIPTION_MAX = 200

#: Un skill est un guide, pas un document.
INSTRUCTIONS_MIN = 10

_NOM_VALIDE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_SEPARATEURS = re.compile(r"[^a-z0-9]+")


def normaliser_nom(valeur: str) -> str:
    """Nom canonique d'un skill : ``/Facturation Client`` → ``facturation-client``.

    Les accents sont retirés (``NFKD``), tout ce qui n'est pas lettre/chiffre
    devient un tiret, les tirets successifs sont fusionnés. Le résultat reste
    vérifié par l'appelant : normaliser n'est pas accepter.
    """
    sans_accent = "".join(
        caractere
        for caractere in unicodedata.normalize("NFKD", valeur.strip().lower())
        if not unicodedata.combining(caractere)
    )
    return _SEPARATEURS.sub("-", sans_accent).strip("-")


def _valider_nom(nom: str) -> str:
    """Nom canonique accepté, ou erreur actionnable."""
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
    """Description sur une ligne, bornée."""
    phrase = " ".join((description or "").split())
    if not phrase:
        raise BusinessValidationError(
            "Description requise : elle dit quand appliquer le skill",
            details={},
        )
    if len(phrase) > DESCRIPTION_MAX:
        raise BusinessValidationError(
            f"Description trop longue (maximum {DESCRIPTION_MAX} caractères)",
            details={"longueur": len(phrase), "maximum": DESCRIPTION_MAX},
        )
    return phrase


def _valider_instructions(instructions: str) -> str:
    """Texte du guide, borné (c'est lui qui entre dans chaque prompt)."""
    texte = (instructions or "").strip()
    if len(texte) < INSTRUCTIONS_MIN:
        raise BusinessValidationError(
            f"Contenu du skill trop court (minimum {INSTRUCTIONS_MIN} caractères)",
            details={"longueur": len(texte)},
        )
    if len(texte) > INSTRUCTIONS_MAX:
        raise BusinessValidationError(
            f"Contenu du skill trop long (maximum {INSTRUCTIONS_MAX} caractères)",
            details={"longueur": len(texte), "maximum": INSTRUCTIONS_MAX},
        )
    return texte


class SkillsService:
    """Lecture et écriture des skills de l'organisation.

    L'autorisation (administrateur) est portée par la route : le service ne
    connaît aucun rôle, il applique les règles de contenu et d'unicité.
    """

    def __init__(self, session: Session, actor_id: str | None = None) -> None:
        self._session = session
        self.skills = SkillRepository(session)
        self._trace = TraceContext(session, actor_id=actor_id)

    # --- lectures -------------------------------------------------------------

    def lister(self) -> list[Skill]:
        """Skills de l'organisation, ordre alphabétique."""
        return self.skills.lister()

    # --- écritures ------------------------------------------------------------

    def enregistrer(
        self,
        *,
        nom: str,
        description: str,
        instructions: str,
        modifie_par: str | None = None,
    ) -> Skill:
        """Crée un skill pour l'organisation.

        Raises:
            BusinessValidationError: nom vide/invalide/déjà pris, description
                absente ou trop longue, texte absent ou hors bornes, ou nombre
                maximal de skills atteint.
        """
        canonique = _valider_nom(nom)
        phrase = _valider_description(description)
        texte = _valider_instructions(instructions)

        if self.skills.get_par_nom(canonique) is not None:
            raise BusinessValidationError(
                f"Un skill nommé /{canonique} existe déjà : modifiez-le ou supprimez-le",
                details={"nom": canonique},
            )

        existants = len(self.skills.lister())
        if existants >= MAX_SKILLS:
            raise BusinessValidationError(
                f"Maximum {MAX_SKILLS} skills : supprimez-en un d'abord",
                details={"nombre": existants, "maximum": MAX_SKILLS},
            )

        ligne = Skill(
            nom=canonique,
            description=phrase,
            instructions=texte,
            modifie_par=modifie_par,
        )
        self.skills.add(ligne)
        self._session.flush()

        self._trace.record_event(
            actor_type=ActorType.HUMAIN.value,
            action=ActionAudit.SKILL_CREE.value,
            entity_type=_ENTITY,
            entity_id=ligne.id,
            after=ligne.resume(),
        )
        return ligne

    def modifier(
        self,
        skill_id: UUID,
        *,
        nom: str | None = None,
        description: str | None = None,
        instructions: str | None = None,
        modifie_par: str | None = None,
    ) -> Skill:
        """Met à jour un skill existant, champ par champ.

        Un champ absent (``None``) est **conservé** : corriger une phrase ne
        doit pas obliger à renvoyer tout le contenu. Un renommage reste soumis
        à l'unicité du nom.

        Raises:
            NotFoundError: skill inexistant.
            BusinessValidationError: contenu refusé, ou nom déjà porté par un
                autre skill.
        """
        ligne = self.skills.get(skill_id)
        if ligne is None:
            raise NotFoundError("Skill introuvable", details={"skill_id": str(skill_id)})
        avant = ligne.resume()

        if nom is not None:
            canonique = _valider_nom(nom)
            if canonique != ligne.nom:
                autre = self.skills.get_par_nom(canonique)
                if autre is not None and autre.id != ligne.id:
                    raise BusinessValidationError(
                        f"Un skill nommé /{canonique} existe déjà",
                        details={"nom": canonique},
                    )
                ligne.nom = canonique
        if description is not None:
            ligne.description = _valider_description(description)
        if instructions is not None:
            ligne.instructions = _valider_instructions(instructions)
        if modifie_par is not None:
            ligne.modifie_par = modifie_par

        self._session.flush()
        self._trace.record_event(
            actor_type=ActorType.HUMAIN.value,
            action=ActionAudit.SKILL_MODIFIE.value,
            entity_type=_ENTITY,
            entity_id=ligne.id,
            before=avant,
            after=ligne.resume(),
        )
        return ligne

    def supprimer(self, skill_id: UUID) -> None:
        """Supprime un skill de l'organisation.

        Raises:
            NotFoundError: skill inexistant.
        """
        ligne = self.skills.get(skill_id)
        if ligne is None:
            raise NotFoundError("Skill introuvable", details={"skill_id": str(skill_id)})
        instantane = ligne.resume()
        self.skills.delete(ligne)
        self._session.flush()
        self._trace.record_event(
            actor_type=ActorType.HUMAIN.value,
            action=ActionAudit.SKILL_SUPPRIME.value,
            entity_type=_ENTITY,
            entity_id=skill_id,
            before=instantane,
        )


__all__ = ["SkillsService", "normaliser_nom"]
