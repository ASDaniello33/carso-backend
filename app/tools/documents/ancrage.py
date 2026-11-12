"""Ancrage et vocabulaire de dépôt d'un document produit.

Extrait de ``app.tools.documents.fabrique`` sans modification de contenu : ces
helpers traduisent une entrée déjà validée (ancre métier, type, format) en
valeurs de dépôt, et décident si un document existant porte le même
rattachement. Ils ne dépendent que des schémas, pas de la fabrique : c'est ce
qui permet au contexte des outils de les utiliser sans import circulaire.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from app.core.errors import ValidationError
from app.tools.documents.schemas import _ANCRES, _FORMATS

#: Alias fréquents du modèle → type ``TypeDocument`` valide. Les noms de
#: **profils** de livrable (offre_financiere, offre_technique...) ne sont pas
#: des types de dépôt : ils sont convertis, jamais rejetés — chaque conversion
#: évite un aller-retour d'erreur tool call.
_ALIAS_TYPE_DOCUMENT: dict[str, str] = {
    # Profils de livrable → type de dépôt réel.
    "offre_financiere": "offre",
    "offre_technique": "offre",
    "offre_autre": "offre",
    # Variantes de formulation.
    "offre_formation": "offre",  # historique lisible, dépôt → nouveau vocab.
    "appel": "appel_proposition",
    "appel_a_projet": "appel_proposition",
    "fiche_presence": "fiche_presence",
    "liste_des_beneficiaires": "liste_beneficiaires",
    "presence": "fiche_presence",
    "check_list": "checklist",
    "photo": "autre",
    "image": "autre",
    "note": "rapport",
    "note_de_synthese": "rapport",
    "facture": "budget",
}

def _memes_ancres(document: Any, ancres: dict[str, UUID]) -> bool:
    """Le document porte-t-il exactement le rattachement demandé ?

    Deux documents de même nom sur deux offres différentes sont deux documents
    distincts : versionner l'un depuis l'autre mélangerait deux dossiers.
    """
    for champ in _ANCRES.values():
        if getattr(document, champ, None) != ancres.get(champ):
            return False
    return True

def _kwargs_ancre(payload: dict[str, Any]) -> dict[str, UUID]:
    """Ancre réelle → FK. Ancre absente / nom inconnu / UUID manquant → {} (repli).

    Un nom d'ancre hors vocabulaire reste une erreur : ce n'est pas un oubli,
    c'est une valeur inventée. Un UUID manquant ou une ancre omise n'en est pas
    une — le service range alors dans le dossier de travail du compte.
    """
    brute = payload.get("ancre")
    if brute is None:
        return {}
    ancre = str(brute).strip().lower()
    if not ancre:
        return {}
    if ancre not in _ANCRES:
        raise ValidationError(
            f"Ancre inconnue : {brute} (attendues : {', '.join(sorted(_ANCRES))})"
        )
    identifiant = payload.get("entity_id")
    if identifiant is None or not str(identifiant).strip():
        return {}
    try:
        return {_ANCRES[ancre]: UUID(str(identifiant).strip())}
    except ValueError:
        # UUID inventé / mal formé : on ne l'écrit pas, le service replie.
        return {}

def _format_depot(demande: Any) -> str:
    """Format de dépôt normalisé, ou erreur explicite listant les formats valides."""
    fmt = str(demande or "docx").strip().lower()
    if fmt not in _FORMATS:
        raise ValidationError(
            f"Format inconnu : {demande} (attendus : {', '.join(sorted(_FORMATS))})"
        )
    return fmt

def _type_depot(payload: dict[str, Any]) -> str:
    """Type de dépôt normalisé : alias converti, inconnu → erreur pédagogique.

    L'erreur liste les valeurs valides **et** la conversion des alias : le
    modèle corrige son appel au coup suivant sans deviner (les erreurs tool
    call viennent presque toujours d'un vocabulaire supposé, pas d'une
    intention fausse).
    """
    brute = str(payload.get("type_document") or "").strip().lower()
    if not brute:
        from app.domain.enums import TypeDocument

        return TypeDocument.NON_CLASSE.value
    converti = _ALIAS_TYPE_DOCUMENT.get(brute)
    if converti is not None:
        return converti
    from app.domain.enums import TypeDocument

    valides = sorted(type_.value for type_ in TypeDocument)
    if brute not in valides:
        raise ValidationError(
            f"Type de document inconnu : {payload.get('type_document')!r}",
            details={
                "types_valides": valides,
                "suggestion": (
                    "Pour une offre financière/technique, utiliser le type "
                    "'offre' — le profil de livrable se règle dans le contenu. "
                    "Les alias usuels (offre_financiere, note_de_synthese, "
                    "check_list...) sont convertis automatiquement."
                ),
            },
        )
    return brute
