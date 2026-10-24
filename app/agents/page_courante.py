"""Page ouverte dans l'interface — les alias ``@MissionActuel``, ``@OffreActuel``…

L'utilisateur travaille sur une fiche précise (une offre, une mission, une
session). Quand il écrit « génère la fiche technique de @MissionActuel », il ne
doit pas avoir à recopier un UUID : le frontend joint la page courante au run,
et ce module en fait le **référent** du modèle.

Deux responsabilités, une seule source :

1. **lecture** de l'item de contexte AG-UI posé par l'interface
   (``useAgentContext``) : type, identifiant, route. Rien n'est deviné — un
   identifiant qui n'est pas un UUID valide est ignoré, un type hors allowlist
   aussi ;
2. **en-tête** du dernier message utilisateur : une ligne
   ``[Page ouverte : mission <uuid> (route /missions/<uuid>)]`` précède son
   texte. Le message lui-même n'est **jamais réécrit** : ce que l'utilisateur a
tapé reste ce qu'il a tapé (et ce que le fil affiche).

Les alias (`@MissionActuel`, `@OffreActuel`, `@SessionActuel`, `@PageActuel`)
renvoient à cette ligne ; le bloc de prompt explique la correspondance, y
compris le cas où l'alias ne correspond pas au type de la page ouverte. Un
message sans en-tête signifie « aucune fiche ouverte » : l'agent demande
laquelle au lieu d'inventer un identifiant.

Ce que le module ne fait pas : il n'autorise rien. L'identifiant fourni par le
navigateur n'est qu'un **référent d'affichage** ; les services et les tools
revérifient le périmètre du compte à chaque lecture (``app/application/perimetre``).
Un compte qui forgerait un UUID hors périmètre reçoit un 404 du service, pas un
accès.

Aucune exception ne sort de ce module : une anomalie de forme laisse le corps de
requête intact (même contrat que les pièces jointes du chat).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any
from uuid import UUID

#: Le dernier message d'un tour est la même notion pour les pièces jointes et
#: pour les alias : une seule implémentation, dans le module des pièces jointes.
from app.agents.pieces_jointes_contexte import dernier_message_utilisateur

_logger = logging.getLogger(__name__)

#: Description exacte de l'item de contexte envoyé par ``useAgentContext``.
DESCRIPTION_CONTEXTE = "Page ouverte dans l'interface CARSO"

#: Types acceptés, avec leur libellé français (jamais un type libre).
TYPES: dict[str, str] = {
    "offre": "offre de formation",
    "mission": "mission",
    "session": "session",
    "page": "page",
}

_LONGUEUR_ROUTE_MAX = 300


@dataclass(frozen=True, slots=True)
class PageCourante:
    """Fiche affichée par l'utilisateur, telle que le frontend l'a déclarée."""

    type: str
    identifiant: str | None
    route: str

    @property
    def libelle(self) -> str:
        """Libellé lisible du type (« mission », « offre de formation »…)."""
        return TYPES[self.type]

    def reference(self) -> str:
        """Référence compacte pour le message : type + identifiant + route."""
        cible = f"{self.libelle} {self.identifiant}" if self.identifiant else self.libelle
        return f"{cible} (route {self.route})"


def _valeur_lue(item: dict[str, Any]) -> dict[str, Any] | None:
    """Charge l'item de contexte : objet JSON, ou chaîne JSON (forme AG-UI)."""
    valeur = item.get("value")
    if isinstance(valeur, dict):
        return valeur
    if not isinstance(valeur, str):
        return None
    try:
        charge = json.loads(valeur)
    except ValueError:
        return None
    return charge if isinstance(charge, dict) else None


def _uuid_ou_rien(valeur: object) -> str | None:
    """Identifiant canonique, ou ``None`` — un UUID invalide n'est jamais retenu."""
    if not isinstance(valeur, str):
        return None
    try:
        return str(UUID(valeur.strip()))
    except ValueError:
        return None


def page_depuis_payload(payload: dict[str, Any]) -> PageCourante | None:
    """Page déclarée par l'interface, ou ``None`` si elle est absente/illisible.

    Le dernier item valide gagne : plusieurs composants peuvent enregistrer un
    contexte (le plus récent est celui de la fiche affichée).
    """
    contextes = payload.get("context")
    if not isinstance(contextes, list):
        return None

    page: PageCourante | None = None
    for item in contextes:
        if not isinstance(item, dict):
            continue
        description = item.get("description")
        if not isinstance(description, str) or not description.startswith(
            DESCRIPTION_CONTEXTE
        ):
            continue
        charge = _valeur_lue(item)
        if charge is None:
            continue
        type_page = charge.get("type")
        if not isinstance(type_page, str) or type_page not in TYPES:
            continue
        identifiant = charge.get("id")
        if identifiant is not None:
            identifiant = _uuid_ou_rien(identifiant)
            if identifiant is None:
                # Un identifiant mal formé ne devient pas un référent : on
                # préfère aucune page à un UUID inventé.
                continue
        route = charge.get("route")
        if not isinstance(route, str) or not route.startswith("/"):
            route = ""
        page = PageCourante(
            type=type_page,
            identifiant=identifiant,
            route=route[:_LONGUEUR_ROUTE_MAX],
        )
    return page


def entete_page(page: PageCourante) -> str:
    """Ligne qui précède le message : la fiche ouverte et son identifiant."""
    return f"[Page ouverte : {page.reference()}]"


def _poser_entete(contenu: Any, page: PageCourante) -> tuple[Any, bool]:
    """Préfixe l'en-tête une fois : chaîne ou parties de texte AG-UI."""
    entete = entete_page(page)
    if isinstance(contenu, str):
        if contenu.startswith(entete):
            return contenu, False
        return f"{entete}\n{contenu}", True
    if isinstance(contenu, list):
        premier = next(
            (
                partie.get("text")
                for partie in contenu
                if isinstance(partie, dict) and isinstance(partie.get("text"), str)
            ),
            None,
        )
        if isinstance(premier, str) and premier.startswith(entete):
            return contenu, False
        return [{"type": "text", "text": entete}, *contenu], True
    return contenu, False


def injecter_page_dans_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Préfixe le dernier message utilisateur de la page ouverte.

    Le texte de l'utilisateur n'est pas modifié : seule une ligne de contexte
    est ajoutée devant. Rend le payload **inchangé (même objet)** quand aucune
    page n'est ouverte ou que l'en-tête est déjà là.
    """
    try:
        page = page_depuis_payload(payload)
        if page is None:
            return payload
        message = dernier_message_utilisateur(payload)
        if message is None:
            return payload

        nouveau_contenu, modifie = _poser_entete(message.get("content"), page)
        if not modifie:
            return payload

        nouveau_message = {**message, "content": nouveau_contenu}
        messages = [
            nouveau_message if autre is message else autre
            for autre in payload["messages"]
        ]
        return {**payload, "messages": messages}
    except Exception:  # pragma: no cover - forme inattendue côté réseau
        _logger.warning("Page courante : en-tête impossible", exc_info=True)
        return payload


#: Bloc de prompt — ajouté à tous les agents exposés (``harness.build_system_prompt``).
BLOC_PAGE_COURANTE = """
## Page ouverte dans l'interface
Quand l'utilisateur regarde une fiche, son message est précédé d'une ligne
`[Page ouverte : mission <uuid> (route /missions/<uuid>)]` — de même pour une
offre ou une session. C'est **cette** fiche que désignent les alias :
- `@MissionActuel` : la mission ouverte ;
- `@OffreActuel` : l'offre de formation ouverte ;
- `@SessionActuel` : la session ouverte ;
- `@PageActuel` : la page courante, quel que soit son type.
L'alias ne remplace pas l'identifiant : il renvoie à la ligne ci-dessus.
- Utilise l'identifiant de cette ligne tel quel pour lire la fiche ou y
  rattacher un document : ne le redemande pas, ne l'invente jamais.
- Sans ligne `[Page ouverte …]`, aucune fiche n'est ouverte : demande laquelle,
  ou propose de la choisir.
- Si l'alias ne correspond pas au type de la page ouverte (`@OffreActuel` alors
  que la page est une mission), dis qu'aucune fiche de ce type n'est ouverte.
- Quand la demande ne parle pas de la fiche affichée (« les missions de la
  semaine »), ignore cette page : elle ne restreint pas la question.
""".strip()


__all__ = [
    "BLOC_PAGE_COURANTE",
    "DESCRIPTION_CONTEXTE",
    "PageCourante",
    "TYPES",
    "dernier_message_utilisateur",
    "entete_page",
    "injecter_page_dans_payload",
    "page_depuis_payload",
]
