"""Rédaction progressive d'un livrable professionnel.

Handlers qui ouvrent un brouillon, écrivent une section, puis finalisent
en rendant le document complet. Ce flux orchestre la rédaction : c'est
pourquoi le dispatcheur ``_generer_professionnel_ou_brouillon`` vit ici
et non dans ``redaction``, ce qui garde la dépendance unidirectionnelle.

Extrait de ``build_document_tools`` sans modification de contenu.
"""

from __future__ import annotations

from typing import Any

from app.core.errors import ValidationError
from app.tools.documents.redaction import (
    _refuser_generation_si_modele as _refuser_generation_si_modele,
)
from app.tools.documents.redaction import (
    _rendre_et_deposer_professionnel as _rendre_et_deposer_professionnel,
)
from app.tools.documents.schemas import _BORNE_SECTION_CARACTERES


def _generer_professionnel_ou_brouillon(ctx: Any, payload: dict[str, Any]) -> dict[str, Any]:
    """Aiguille ``action`` : génération d'un tenant, ou rédaction progressive.

    ``demarrer`` / ``section`` / ``finaliser`` font écrire au modèle de
    **petites** parties (le plan annoncé avant génération), pendant que
    ``generer`` garde le corps complet pour un document court.
    """
    action = payload.get("action") or "generer"
    if action == "generer":
        # Le garde-fou porte sur le DÉBUT d'un document (une correction vise
        # un document existant, pas une création).
        _refuser_generation_si_modele(ctx, payload)
    if action == "demarrer":
        return _brouillon_demarrer(ctx, payload)
    if action == "section":
        return _brouillon_section(ctx, payload)
    if action == "finaliser":
        return _brouillon_finaliser(ctx, payload)
    return _rendre_et_deposer_professionnel(ctx, payload)


def _brouillon_demarrer(ctx: Any, payload: dict[str, Any]) -> dict[str, Any]:
    """Ouvre un brouillon : le corps s'écrira ensuite partie par partie.

    Le **plan** (une entrée par appel de section) est figé avec l'habillage :
    il donne la progression (« Partie 2/4 ») affichée à côté de l'appel
    d'outil dans le chat — l'utilisateur voit avancer le document au lieu de
    croire à une boucle. Sans plan, la progression reste un simple compteur.
    """
    _refuser_generation_si_modele(ctx, payload)
    from app.documents.brouillons import ouvrir

    meta = {
        cle: payload[cle] for cle in _CLES_BROUILLON if payload.get(cle) is not None
    }
    plan = [
        item.strip() for item in (payload.get("plan") or []) if item and item.strip()
    ]
    if plan:
        meta["plan"] = plan
    etat = ouvrir(ctx.racine_brouillons(), ctx.identite.agent_id, meta)
    if plan:
        etapes = "; ".join(
            f"{numero}. {titre}" for numero, titre in enumerate(plan, start=1)
        )
        message = (
            f"Brouillon ouvert, plan en {len(plan)} partie(s) : {etapes}. "
            "Écris maintenant la partie 1/"
            f"{len(plan)} : action='section' avec brouillon_id et le HTML "
            f"d'UNE partie (≤ {_BORNE_SECTION_CARACTERES} caractères par appel, "
            "plusieurs pages possibles dans une même partie), puis les "
            "suivantes dans l'ordre. Remplacer une partie déjà écrite : "
            "action='section' avec section_index. Terminer par "
            "action='finaliser' (rendu et dépôt du document complet)."
        )
    else:
        message = (
            "Brouillon ouvert. Écris maintenant le corps partie par partie : "
            "action='section' avec brouillon_id et le HTML d'UNE partie "
            f"(≤ {_BORNE_SECTION_CARACTERES} caractères par appel), dans "
            "l'ordre du document. Remplacer une partie déjà écrite : "
            "action='section' avec section_index. Terminer par "
            "action='finaliser' (rendu et dépôt du document complet)."
        )
    return {
        "action": "demarrer",
        "brouillon_id": etat["brouillon_id"],
        "titre": meta.get("titre"),
        "format": meta.get("format"),
        "sections": 0,
        "parties": len(plan) or None,
        "partie": 0 if plan else None,
        "progression": _progression(0, len(plan)) if plan else None,
        "message": message,
    }


def _brouillon_section(ctx: Any, payload: dict[str, Any]) -> dict[str, Any]:
    """Ajoute (ou remplace) une partie du document dans le brouillon de l'agent."""
    from app.documents.brouillons import charger, enregistrer

    racine = ctx.racine_brouillons()
    etat = charger(racine, ctx.identite.agent_id, payload["brouillon_id"])
    sections: list[str] = list(etat["sections"])
    plan = _plan_brouillon(etat)
    contenu = str(payload["contenu_html"])
    index = payload.get("section_index")
    remplacee: int | None = None
    if index is not None:
        if int(index) > len(sections):
            raise ValidationError(
                f"section_index {index} hors bornes : le brouillon contient "
                f"{len(sections)} section(s) (indices 1 à {len(sections)}). "
                "Sans section_index, la section est ajoutée à la suite."
            )
        remplacee = int(index)
        sections[remplacee - 1] = contenu
    else:
        sections.append(contenu)
    etat["sections"] = sections
    enregistrer(racine, ctx.identite.agent_id, etat)
    total = sum(len(partie) for partie in sections)
    progression = _progression(len(sections), len(plan)) if plan else None
    if plan and len(sections) < len(plan):
        suite = (
            f"Écrire maintenant la partie {len(sections) + 1}/{len(plan)}"
            f" (« {plan[len(sections)]} »)"
        )
    else:
        suite = "Terminer par action='finaliser' (rendu et dépôt du document)"
    quoi = progression or ("Partie" if plan else "Section")
    if remplacee is not None:
        quoi = f"{quoi} n° {remplacee}" if not plan else quoi
    action = "remplacée" if remplacee is not None else "ajoutée"
    return {
        "action": "section",
        "brouillon_id": etat["brouillon_id"],
        "sections": len(sections),
        "caracteres": total,
        "remplacee": remplacee,
        "parties": len(plan) or None,
        "partie": len(sections) if plan else None,
        "progression": progression,
        "message": (
            f"{quoi} {action} : {len(sections)} section(s), "
            f"{total} caractères cumulés. {suite}."
        ),
    }


def _brouillon_finaliser(ctx: Any, payload: dict[str, Any]) -> dict[str, Any]:
    """Assemble les parties, rend le document et le dépose en proposition."""
    from app.documents.brouillons import charger, supprimer

    racine = ctx.racine_brouillons()
    etat = charger(racine, ctx.identite.agent_id, payload["brouillon_id"])
    if not etat["sections"]:
        raise ValidationError(
            "Brouillon vide : écris au moins une section "
            "(action='section') avant de finaliser."
        )
    plan = _plan_brouillon(etat)
    complet = {
        **etat["meta"],
        **{
            cle: payload[cle]
            for cle in ("graphiques", "images", "css_supplementaire")
            if payload.get(cle) is not None
        },
    }
    # Séparateur réel (fin de ligne), jamais la séquence littérale : écrit
    # ``"\\n"`` entre deux parties introduisait les deux caractères barre
    # inversée + n dans le HTML assemblé, que le convertisseur recopiait
    # fidèlement — le document imprimait ``\n`` entre deux sections. Constaté
    # sur l'offre HI-LAB du 11/11 (5 paragraphes fautifs restants).
    complet["contenu_html"] = "\n".join(etat["sections"])
    nombre_sections = len(etat["sections"])
    # Le brouillon n'est supprimé qu'après un dépôt réussi : un échec de
    # rendu laisse l'agent corriger la partie fautive (action='section'
    # avec section_index) puis re-finaliser, sans rien réécrire.
    resultat = _rendre_et_deposer_professionnel(ctx, complet)
    supprimer(racine, ctx.identite.agent_id, payload["brouillon_id"])
    resultat["action"] = "finaliser"
    resultat["sections"] = nombre_sections
    resultat["parties"] = len(plan) or None
    resultat["partie"] = len(plan) if plan else None
    resultat["progression"] = (
        _progression(len(plan), len(plan)) if plan else None
    )
    return resultat


#: Champs d'habillage figés à l'ouverture d'un brouillon (rédaction progressive)
#: et relus tels quels à la finalisation.
_CLES_BROUILLON = (
    "titre",
    "format",
    "theme",
    "orientation",
    "auteur",
    "css_supplementaire",
    "nom_fichier",
    # Les graphiques se déclarent à l'ouverture : une ancre vit dans une
    # partie, mais sa spec n'a pas à être répétée à chaque appel de section.
    # Sans cette clé, la finalisation les perdait silencieusement et le
    # classeur sortait sans aucun graphique, ancres vides.
    "graphiques",
    # Une ancre <figure data-image> vit dans une partie : sa référence se
    # déclare à l'ouverture (ou à la finalisation) et suit le brouillon.
    "images",
    "type_document",
    "ancre",
    "entity_id",
    "propose_par",
)


def _progression(ecrites: int, total: int) -> str | None:
    """Libellé « Partie 2/4 » du plan de rédaction (``None`` sans plan).

    C'est ce texte que le chat affiche à côté de l'appel d'outil : l'utilisateur
    voit avancer le document au lieu de croire à une boucle.
    """
    if total <= 0:
        return None
    return f"Partie {min(ecrites, total)}/{total}"


def _plan_brouillon(etat: dict[str, Any]) -> list[str]:
    """Plan figé à l'ouverture du brouillon : une entrée par appel de section."""
    return [str(item) for item in (etat.get("meta") or {}).get("plan") or []]
