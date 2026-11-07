"""Lecture des modèles DOCX des organisations (INC-125, réduit en INC-129).

Deux usages subsistent, tous deux en **lecture** :

- ``inventaire_modele`` : plan, styles de titre, ``{{clé}}`` et zones du modèle,
  pour qu'un agent sache à quoi il a affaire avant de reprendre un document
  (``analyze_document_reference``) ;
- ``style_depuis_modele`` : l'identité visuelle du modèle (polices, couleurs,
  marges, gabarit de page) quand un rendu CARSO doit s'y conformer.

Le **remplissage** du fichier ne vit plus ici : il est assuré par l'édition de
la copie (``app/documents/moteur/edition.py``, INC-129) — le fichier du client
est copié tel quel puis modifié élément par élément. La voie « sections par
titres » de l'INC-125 a été retirée avec ses outils : deux mécanismes pour un
même geste faisaient choisir le mauvais à l'agent.

Le fichier d'origine n'est jamais modifié : on entre par un chemin résolu par le
service documentaire, on sort des valeurs.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from app.core.errors import ValidationError
from app.documents.extraction.base import require_library

__all__ = [
    "inventaire_modele",
    "style_depuis_modele",
    "titres_du_modele",
]

#: Placeholder de modèle : ``{{nom}}`` (mêmes règles que ``cloning``).
_PLACEHOLDER = re.compile(r"\{\{\s*([a-zA-Z0-9_.-]+)\s*\}\}")

#: Numérotation en tête de titre (« 1. », « 2) », « IV — ») : elle ne fait pas
#: partie du titre pour la comparaison, l'agent écrit le titre tel qu'il le lit.
_NUMEROTATION = re.compile(r"^\s*(?:\d+(?:\.\d+)*[.)]?|[IVXLC]+[.)])\s+", re.I)

#: Styles Word considérés comme des titres, dans l'ordre où on les reconnaît.
_STYLES_TITRE = re.compile(r"^(?:heading|titre)\s*(\d)$", re.I)


def _nettoyer_titre(texte: str) -> str:
    """Titre comparable : sans numérotation, sans casse, sans espaces superflus."""
    return " ".join(_NUMEROTATION.sub("", texte or "").split()).casefold()


def _niveau_titre(paragraphe: Any) -> int | None:
    """Niveau de titre d'un paragraphe (``Heading 1``…), ou ``None``.

    Remonte la chaîne ``base_style`` : un style maison (« Titre CARSO ») dérivé
    d'un ``Heading`` reste un titre pour le modèle.
    """
    style = getattr(paragraphe, "style", None)
    vus: set[int] = set()
    while style is not None and id(style) not in vus:
        vus.add(id(style))
        nom = str(getattr(style, "name", "") or "").strip()
        # « Title »/« Titre » est le titre du document (niveau 0) : il fait
        # partie du plan — le masquer rendrait la couverture invisible de
        # l'inventaire et des sections conservées.
        if nom.casefold() in ("title", "titre"):
            return 0
        correspondance = _STYLES_TITRE.match(nom)
        if correspondance:
            return int(correspondance.group(1))
        style = getattr(style, "base_style", None)
    return None


# --- Inventaire ---------------------------------------------------------------


def _textes_document(document: Any) -> list[str]:
    """Tous les textes du modèle : corps, tableaux, en-têtes et pieds."""
    textes = [paragraphe.text for paragraphe in document.paragraphs]
    for tableau in document.tables:
        for ligne in tableau.rows:
            for cellule in ligne.cells:
                textes.extend(paragraphe.text for paragraphe in cellule.paragraphs)
    for section in document.sections:
        for zone in (section.header, section.footer):
            if zone is None:
                continue
            textes.extend(paragraphe.text for paragraphe in zone.paragraphs)
            for tableau in zone.tables:
                for ligne in tableau.rows:
                    for cellule in ligne.cells:
                        textes.extend(paragraphe.text for paragraphe in cellule.paragraphs)
    return [texte for texte in textes if texte]


def titres_du_modele(document: Any) -> list[dict[str, Any]]:
    """Titres du modèle, dans l'ordre : c'est le plan que l'agent doit remplir."""
    titres: list[dict[str, Any]] = []
    for index, paragraphe in enumerate(document.paragraphs):
        niveau = _niveau_titre(paragraphe)
        texte = (paragraphe.text or "").strip()
        if niveau is None or not texte:
            continue
        titres.append(
            {
                "index": index,
                "niveau": niveau,
                "texte": texte,
                "style": str(getattr(paragraphe.style, "name", "") or ""),
            }
        )
    return titres


def _style_titre_resume(paragraphe: Any) -> dict[str, Any]:
    """Police, taille, couleur et gras effectifs d'un style de titre du modèle."""
    style = getattr(paragraphe, "style", None)
    police = getattr(style, "font", None)
    couleur = getattr(police, "color", None) if police is not None else None
    taille = getattr(police, "size", None) if police is not None else None
    return {
        "style": str(getattr(style, "name", "") or ""),
        "police": getattr(police, "name", None) if police is not None else None,
        "taille_pt": round(taille.pt, 1) if taille is not None else None,
        "gras": getattr(police, "bold", None) if police is not None else None,
        "couleur": str(getattr(couleur, "rgb", None) or "") if couleur is not None else None,
    }


def inventaire_modele(chemin: Path) -> dict[str, Any]:
    """Décrit un modèle DOCX : placeholders, plan, styles, tableaux, zones.

    L'agent s'en sert pour savoir **exactement** quoi fournir : noms de
    placeholders, titres à remplir, styles de titre du client.

    Raises:
        ValidationError: fichier DOCX illisible.
    """
    docx = require_library("docx")
    try:
        document = docx.Document(str(chemin))
    except Exception as exc:  # noqa: BLE001 - la cause réelle est transmise
        raise ValidationError("Modèle DOCX illisible") from exc

    textes = _textes_document(document)
    placeholders = sorted(
        {
            correspondance.group(1)
            for texte in textes
            for correspondance in _PLACEHOLDER.finditer(texte)
        }
    )
    titres = titres_du_modele(document)
    sections: list[dict[str, Any]] = []
    for section in document.sections:
        sections.append(
            {
                "largeur_mm": round(section.page_width.mm, 1) if section.page_width else None,
                "hauteur_mm": round(section.page_height.mm, 1) if section.page_height else None,
                "marges_mm": {
                    "haut": round(section.top_margin.mm, 1) if section.top_margin else None,
                    "bas": round(section.bottom_margin.mm, 1) if section.bottom_margin else None,
                    "gauche": round(section.left_margin.mm, 1) if section.left_margin else None,
                    "droite": round(section.right_margin.mm, 1) if section.right_margin else None,
                },
                "entete": " ".join(
                    paragraphe.text for paragraphe in section.header.paragraphs if paragraphe.text
                ).strip(),
                "pied": " ".join(
                    paragraphe.text for paragraphe in section.footer.paragraphs if paragraphe.text
                ).strip(),
            }
        )

    styles_titres: dict[str, dict[str, Any]] = {}
    for paragraphe in document.paragraphs:
        if _niveau_titre(paragraphe) is None:
            continue
        nom = str(getattr(paragraphe.style, "name", "") or "")
        if nom and nom not in styles_titres:
            styles_titres[nom] = _style_titre_resume(paragraphe)

    tableaux = [
        {
            "index": index,
            "style": str(getattr(tableau.style, "name", "") or ""),
            "lignes": len(tableau.rows),
            "colonnes": len(tableau.columns),
        }
        for index, tableau in enumerate(document.tables)
    ]

    return {
        "format": "docx",
        "placeholders": placeholders,
        "titres": titres,
        "styles_titres": styles_titres,
        "tableaux": tableaux,
        "sections": sections,
        "couverture_probable": any(
            titre["niveau"] <= 1 and titre["index"] == titres[0]["index"]
            for titre in titres[:1]
        )
        if titres
        else False,
    }


# --- Remplissage --------------------------------------------------------------


# --- Modèles non DOCX ---------------------------------------------------------


def style_depuis_modele(chemin: Path) -> Any:
    """Identité visuelle d'un modèle (PDF, XLSX ou DOCX) → ``DocumentStyle``.

    Pour un modèle PDF, c'est **la seule** fidélité possible : polices, couleurs,
    marges et gabarit de page, jamais la structure (aucun moteur ne reconstitue
    la mise en page d'un PDF sans le rééditer).

    Raises:
        ValidationError: format sans analyseur, ou document illisible.
    """
    from app.documents.reference import analyser_reference, style_depuis_reference

    reference = analyser_reference(chemin, niveau="resume")
    return style_depuis_reference(reference.get("style_spec") or {})
