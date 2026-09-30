"""Analyse d'un document de référence — structure et style (ADR 0005, Phase 4).

Deux sorties **disjointes**, et c'est tout l'intérêt :

- ``content_structure`` : le **plan** du document (sections, niveaux, nombre de
  tableaux/images, présence d'une couverture) — jamais son texte intégral ;
- ``style_spec`` : l'**identité visuelle** (format, marges, typographie, couleurs,
  tableaux, en-tête/pied, couverture) — réutilisable telle quelle sur un autre
  contenu, indépendamment du document source.

L'agent peut donc dire « utilise le style de ce document » sans jamais recopier
un mot de son contenu. ``style_depuis_reference`` fusionne la spécification
extraite sur ``CARSO_DEFAUT`` : ce qui n'a pas pu être mesuré garde une valeur
saine, et ce qui l'a été est appliqué.

Honnêteté des mesures — chaque point non mesurable est listé dans ``avertissements`` :

- les marges d'un PDF sont **mesurées** sur la boîte réelle des caractères, pas
  déclarées (un PDF ne porte pas de notion de marges) ;
- le **texte** d'en-tête/pied d'une référence n'est **jamais** repris : seule sa
  mise en forme l'est (recopier l'en-tête d'un client serait une faute) ;
- une référence tabulaire (XLSX) n'a pas de style de document : seules sa
  structure et ses mises en forme de feuille sont renvoyées.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.core.errors import ValidationError
from app.documents.extraction.base import require_library
from app.documents.inspection import inspecter_docx, inspecter_pdf, inspecter_xlsx
from app.documents.styles import (
    CARSO_DEFAUT,
    FORMATS_MM,
    DocumentStyle,
    Marges,
    StyleCouverture,
    StyleEncadre,
    StyleEntetePied,
    StylePage,
    StyleTableau,
    StyleTexte,
)

__all__ = [
    "analyser_reference",
    "style_depuis_reference",
]

#: Sections typiques d'une proposition, dans l'ordre attendu (heuristique de lecture).
_SECTIONS_PROPOSITION = (
    "contexte",
    "objectif",
    "objectifs",
    "méthod",
    "methodolog",
    "démarche",
    "demarche",
    "planning",
    "calendrier",
    "budget",
    "coût",
    "cout",
    "résultat",
    "resultat",
    "conclusion",
    "annexe",
)

#: Familles de police lisibles depuis un nom de police PDF.
_FAMILLES_PDF = (
    ("times", "Times New Roman"),
    ("georgia", "Georgia"),
    ("garamond", "Garamond"),
    ("courier", "Courier New"),
    ("consol", "Consolas"),
    ("arial", "Arial"),
    ("helvetica", "Arial"),
    ("calibri", "Calibri"),
    ("cambria", "Cambria"),
)


def analyser_reference(chemin: Path, *, niveau: str = "resume") -> dict[str, Any]:
    """Analyse un document de référence (DOCX, PDF ou XLSX).

    Args:
        chemin: fichier de référence (validé en amont par la couche storage).
        niveau: ``resume`` (défaut) ou ``complet`` — profondeur d'inspection.

    Returns:
        ``{"format", "content_structure", "style_spec", "avertissements"}``.

    Raises:
        ValidationError: format sans analyseur, ou document illisible.
    """
    extension = chemin.suffix.lower()
    if extension == ".docx":
        return _reference_docx(chemin, niveau)
    if extension == ".pdf":
        return _reference_pdf(chemin, niveau)
    if extension == ".xlsx":
        return _reference_xlsx(chemin, niveau)
    raise ValidationError(
        f"Analyse de référence non supportée pour {extension or '?'}",
        details={"extensions": [".docx", ".pdf", ".xlsx"]},
    )


def style_depuis_reference(
    style_spec: dict[str, Any], *, base: DocumentStyle | None = None
) -> DocumentStyle:
    """Convertit une ``style_spec`` extraite en ``DocumentStyle`` utilisable.

    La fusion se fait sur ``CARSO_DEFAUT`` (ou ``base``) : un attribut absent de
    la référence conserve sa valeur saine — jamais de trou dans le rendu.
    """
    if not style_spec:
        return base or CARSO_DEFAUT
    reference = base or CARSO_DEFAUT
    mise_a_jour: dict[str, Any] = {}

    page = style_spec.get("page") or {}
    if page:
        marges = page.get("marges") or {}
        mise_a_jour["page"] = StylePage(
            format=page.get("format") or reference.page.format,
            orientation=page.get("orientation") or reference.page.orientation,
            marges=Marges(
                haut_mm=_nombre(marges.get("haut"), reference.page.marges.haut_mm),
                bas_mm=_nombre(marges.get("bas"), reference.page.marges.bas_mm),
                gauche_mm=_nombre(marges.get("gauche"), reference.page.marges.gauche_mm),
                droite_mm=_nombre(marges.get("droite"), reference.page.marges.droite_mm),
            ),
        )

    typographie = style_spec.get("typographie") or {}
    if typographie.get("corps"):
        mise_a_jour["corps"] = _style_texte(typographie["corps"], reference.corps)
    titres = {
        int(niveau): _style_texte(valeurs, reference.titre_niveau(int(niveau)))
        for niveau, valeurs in (typographie.get("titres") or {}).items()
    }
    if titres:
        complet = dict(reference.titres)
        complet.update(titres)
        mise_a_jour["titres"] = complet
    if typographie.get("titre"):
        mise_a_jour["titre"] = _style_texte(typographie["titre"], reference.titre)

    if style_spec.get("tableau"):
        mise_a_jour["tableau"] = _style_tableau(style_spec["tableau"], reference.tableau)

    if style_spec.get("couverture"):
        mise_a_jour["couverture"] = _style_couverture(
            style_spec["couverture"], reference.couverture
        )

    for genre in ("entete", "pied"):
        if style_spec.get(genre):
            mise_a_jour[genre] = _style_zone(style_spec[genre], getattr(reference, genre))

    if style_spec.get("encadres"):
        encadres = dict(reference.encadres)
        for genre, valeurs in style_spec["encadres"].items():
            encadres[genre] = StyleEncadre(
                fond=valeurs.get("fond") or encadres.get(genre, StyleEncadre()).fond,
                bordure=valeurs.get("bordure") or encadres.get(genre, StyleEncadre()).bordure,
            )
        mise_a_jour["encadres"] = encadres

    return reference.model_copy(update={"nom": "reference", **mise_a_jour})


# --- DOCX ---------------------------------------------------------------------


def _reference_docx(chemin: Path, niveau: str) -> dict[str, Any]:
    """Analyse d'une référence DOCX : plan + identité visuelle."""
    structure = inspecter_docx(chemin, niveau="complet")
    sections = structure["sections_detaillees"][0] if structure["sections_detaillees"] else {}
    avertissements: list[str] = []

    plan = _plan_depuis_hierarchie(structure["hierarchie"])
    plan.update(
        {
            "nb_tableaux": structure["nb_tableaux"],
            "nb_images": len(structure["images"]),
            "a_entete": bool(structure["entetes"] and structure["entetes"][0]),
            "a_pied": bool(structure["pieds"] and structure["pieds"][0]),
            "a_couverture": _a_couverture(plan["sections"]),
        }
    )

    style_spec: dict[str, Any] = {
        "page": {
            "format": _format_depuis_mm(sections.get("largeur_mm")),
            "orientation": sections.get("orientation"),
            "marges": sections.get("marges_mm") or {},
        },
        "typographie": _typographie_docx(structure),
        "tableau": _style_tableau_docx(chemin),
        "couverture": _couverture_docx(structure),
    }
    if plan["a_entete"] or plan["a_pied"]:
        style_spec["entete"] = {"present": True, "mise_en_forme": "non_extraite"}
        style_spec["pied"] = {"present": True, "mise_en_forme": "non_extraite"}
        avertissements.append(
            "texte_entete_pied_non_repris (recopier l'en-tête d'un tiers serait une faute)"
        )
    if niveau == "resume":
        avertissements.append("analyse_resumee (niveau='complet' pour les runs détaillés)")
    return {
        "format": "docx",
        "content_structure": plan,
        "style_spec": style_spec,
        "avertissements": avertissements,
    }


def _plan_depuis_hierarchie(hierarchie: list[dict[str, Any]]) -> dict[str, Any]:
    """Plan du document : titres, niveaux, contenus par section."""
    sections: list[dict[str, Any]] = []
    courante: dict[str, Any] | None = None
    for bloc in hierarchie:
        if bloc["type"] == "titre":
            courante = {
                "titre": bloc["texte"],
                "niveau": bloc["niveau"],
                "nb_paragraphes": 0,
                "nb_listes": 0,
                "nb_tableaux": 0,
                "nb_images": 0,
            }
            sections.append(courante)
            continue
        if courante is None:
            courante = {
                "titre": None,
                "niveau": None,
                "nb_paragraphes": 0,
                "nb_listes": 0,
                "nb_tableaux": 0,
                "nb_images": 0,
            }
            sections.append(courante)
        if bloc["type"] == "paragraphe":
            courante["nb_paragraphes"] += 1
            courante["nb_images"] += bloc.get("nb_images", 0)
        elif bloc["type"] == "liste":
            courante["nb_listes"] += 1
        elif bloc["type"] == "tableau":
            courante["nb_tableaux"] += 1
    return {
        "type_probable": _type_probable(sections),
        "sections": sections,
        "nb_sections": len(sections),
        "nb_paragraphes": sum(section["nb_paragraphes"] for section in sections),
    }


def _type_probable(sections: list[dict[str, Any]]) -> str | None:
    """Type deviné à partir des intitulés de section — annoncé comme *probable*.

    Rien n'est affirmé : le champ s'appelle ``type_probable`` et reste ``None``
    quand aucun indice n'est trouvé.
    """
    intitules = " ".join((section.get("titre") or "").lower() for section in sections)
    if not intitules.strip():
        return None
    if "budget" in intitules and ("méthod" in intitules or "methodol" in intitules):
        return "proposition"
    if "checklist" in intitules or "contrôle" in intitules:
        return "checklist"
    if "rapport" in intitules:
        return "rapport"
    for mot in _SECTIONS_PROPOSITION:
        if mot in intitules:
            return "document_structure"
    return None


def _a_couverture(sections: list[dict[str, Any]]) -> bool:
    """Vrai si la première section est un titre sans contenu (page de couverture)."""
    if not sections:
        return False
    premiere = sections[0]
    return (
        premiere.get("nb_paragraphes", 0) == 0
        and premiere.get("nb_tableaux", 0) == 0
        and bool(premiere.get("titre"))
    )


def _typographie_docx(structure: dict[str, Any]) -> dict[str, Any]:
    """Typographie des paragraphes et des titres, par fréquence d'occurrence."""
    paragraphes = structure.get("paragraphes") or []
    # Le titre du document est **le premier titre rencontré**, jamais celui qui
    # revient le plus souvent : un vote majoritaire confondrait le titre avec les
    # intertitres de section.
    titre: dict[str, Any] = {}
    for paragraphe in paragraphes:
        if _est_titre(paragraphe.get("style") or "") and paragraphe["runs"]:
            titre = _style_texte_dominant(paragraphe["runs"])
            if paragraphe.get("alignement"):
                titre["alignement"] = _alignement(paragraphe["alignement"])
            break
    titres: dict[int, dict[str, Any]] = {}
    for paragraphe in paragraphes:
        niveau = _niveau_style(paragraphe.get("style") or "")
        if niveau is None:
            continue
        style = _style_texte_dominant(paragraphe["runs"])
        if style:
            titres.setdefault(niveau, style)
    corps = _style_texte_dominant(
        [
            run
            for paragraphe in paragraphes
            if not _est_titre(paragraphe.get("style") or "")
            for run in paragraphe["runs"]
        ]
    )
    resultat: dict[str, Any] = {}
    if corps:
        resultat["corps"] = corps
    if titre:
        resultat["titre"] = titre
    if titres:
        resultat["titres"] = titres
    resultat["alignements"] = {
        alignement: sum(
            1 for paragraphe in paragraphes if paragraphe.get("alignement") == alignement
        )
        for alignement in {
            paragraphe.get("alignement")
            for paragraphe in paragraphes
            if paragraphe.get("alignement")
        }
    }
    return resultat


def _style_texte_dominant(runs: list[dict[str, Any]]) -> dict[str, Any]:
    """Rôle typographique dominant d'une série de runs (la valeur la plus fréquente).

    Un attribut laissé au style Word (``None``) n'est **pas** inventé : il est
    simplement absent du résultat.
    """
    if not runs:
        return {}
    tailles = _valeurs_frequentes([run.get("taille_pt") for run in runs])
    polices = _valeurs_frequentes([run.get("police") for run in runs])
    couleurs = _valeurs_frequentes([run.get("couleur") for run in runs])
    resultat: dict[str, Any] = {}
    if polices:
        resultat["police"] = polices
    if tailles:
        resultat["taille_pt"] = tailles
    if couleurs:
        resultat["couleur"] = couleurs
    resultat["gras"] = sum(1 for run in runs if run.get("gras")) > len(runs) / 2
    resultat["italique"] = sum(1 for run in runs if run.get("italique")) > len(runs) / 2
    return resultat


def _valeurs_frequentes(valeurs: list[Any]) -> Any:
    """Valeur non nulle la plus fréquente (``None`` si aucune)."""
    propres = [valeur for valeur in valeurs if valeur is not None]
    if not propres:
        return None
    return max(sorted(set(propres)), key=propres.count)


def _est_titre(nom_style: str) -> bool:
    """Vrai si le style est un style de titre Word."""
    return nom_style == "Title" or nom_style.startswith("Heading ")


def _niveau_style(nom_style: str) -> int | None:
    """Niveau numérique d'un style de titre (``Heading 2`` → 2).

    Le style ``Title`` (titre du document) n'est **pas** un niveau de section :
    il alimente le rôle ``titre`` du style, pas la hiérarchie 1→4. Les niveaux
    Word au-delà de 4 sont ramenés à 4 (le modèle documentaire CARSO en connaît
    quatre ; un document réel peut en déclarer davantage).
    """
    if nom_style.startswith("Heading ") and nom_style.split(" ")[1].isdigit():
        return min(int(nom_style.split(" ")[1]), 4)
    return None


def _est_titre_de_document(nom_style: str) -> bool:
    """Vrai pour la page de titre : style ``Title`` ou premier niveau."""
    return nom_style == "Title" or _niveau_style(nom_style) == 1


def _style_tableau_docx(chemin: Path) -> dict[str, Any]:
    """Filets, trame d'en-tête et graisse de l'en-tête du premier tableau."""
    docx = require_library("docx")
    from docx.oxml.ns import qn

    document = docx.Document(str(chemin))
    if not document.tables:
        return {}
    tableau = document.tables[0]
    resultat: dict[str, Any] = {}
    tbl_pr = tableau._tbl.tblPr
    bordures = tbl_pr.find(qn("w:tblBorders")) if tbl_pr is not None else None
    if bordures is not None:
        haut = bordures.find(qn("w:top"))
        resultat["bordures"] = haut is not None and haut.get(qn("w:val")) not in (None, "none")
        if haut is not None and haut.get(qn("w:color")) not in (None, "auto"):
            resultat["bordure_couleur"] = f"#{haut.get(qn('w:color')).upper()}"
    cellule_entete = document.tables[0].cell(0, 0)
    tc_pr = cellule_entete._tc.find(qn("w:tcPr"))
    trame = tc_pr.find(qn("w:shd")) if tc_pr is not None else None
    if trame is not None and trame.get(qn("w:fill")) not in (None, "auto", "FFFFFF"):
        resultat["entete_fond"] = f"#{trame.get(qn('w:fill')).upper()}"
    resultat["entete_gras"] = bool(
        cellule_entete.paragraphs and cellule_entete.paragraphs[0].runs and
        cellule_entete.paragraphs[0].runs[0].font.bold
    )
    return resultat


def _couverture_docx(structure: dict[str, Any]) -> dict[str, Any]:
    """Style de couverture déduit du premier titre de niveau 0/1 du document."""
    paragraphes = structure.get("paragraphes") or []
    titre = next(
        (
            paragraphe
            for paragraphe in paragraphes
            if _est_titre_de_document(paragraphe.get("style") or "")
        ),
        None,
    )
    if titre is None or not titre.get("runs"):
        return {}
    style = _style_texte_dominant(titre["runs"])
    resultat: dict[str, Any] = {"present": True}
    if style.get("taille_pt"):
        resultat["titre_taille_pt"] = style["taille_pt"]
    if style.get("couleur"):
        resultat["couleur_accent"] = style["couleur"]
    if titre.get("alignement"):
        resultat["alignement"] = _alignement(titre["alignement"])
    return resultat


# --- PDF ----------------------------------------------------------------------


def _reference_pdf(chemin: Path, niveau: str) -> dict[str, Any]:
    """Analyse d'une référence PDF : plan par titres probables + style mesuré."""
    structure = inspecter_pdf(chemin, niveau=niveau)
    avertissements = ["marges_estimees_depuis_le_texte (un PDF ne déclare pas ses marges)"]
    if structure.get("limites"):
        avertissements.extend(str(limite) for limite in structure["limites"])

    sections = [
        {
            "titre": titre,
            "niveau": 1,
            "nb_paragraphes": 0,
            "nb_listes": 0,
            "nb_tableaux": 0,
            "nb_images": 0,
        }
        for page in structure["pages"]
        for titre in page.get("titres_probables", [])[:3]
    ]
    plan = {
        "type_probable": _type_probable(sections),
        "sections": sections,
        "nb_sections": len(sections),
        "nb_tableaux": sum(page.get("nb_tableaux", 0) for page in structure["pages"]),
        "nb_images": sum(page.get("nb_images", 0) for page in structure["pages"]),
        "a_entete": bool(structure["zones_entete_pied"].get("entete")),
        "a_pied": bool(structure["zones_entete_pied"].get("pied")),
        "a_couverture": False,
    }
    typographie = structure.get("typographie") or {}
    style_spec: dict[str, Any] = {
        "page": {
            "format": _format_depuis_mm(structure.get("page_width")),
            "orientation": structure.get("orientation"),
            "marges": _marges_pdf(chemin),
        },
        "typographie": _typographie_pdf(typographie),
        "tableau": {"present": plan["nb_tableaux"] > 0},
        "entete": {"present": plan["a_entete"], "mise_en_forme": "non_extraite"},
        "pied": {"present": plan["a_pied"], "mise_en_forme": "non_extraite"},
    }
    if plan["a_entete"] or plan["a_pied"]:
        avertissements.append("texte_entete_pied_non_repris")
    return {
        "format": "pdf",
        "content_structure": plan,
        "style_spec": style_spec,
        "avertissements": avertissements,
    }


def _marges_pdf(chemin: Path) -> dict[str, float]:
    """Marges mesurées : boîte réelle du texte moins bords de la page (mm)."""
    try:
        pdfplumber = require_library("pdfplumber")
    except Exception:  # pragma: no cover - pdfplumber absent
        return {}
    points_vers_mm = 25.4 / 72.0
    try:
        with pdfplumber.open(str(chemin)) as document:
            pages = document.pages[:3]
            if not pages:
                return {}
            caracteres = [caractere for page in pages for caractere in (page.chars or [])]
            if not caracteres:
                return {}
            largeur = pages[0].width
            hauteur = pages[0].height
            gauche = min(float(caractere["x0"]) for caractere in caracteres)
            haut = min(float(caractere["top"]) for caractere in caracteres)
            droite = largeur - max(float(caractere["x1"]) for caractere in caracteres)
            bas = hauteur - max(float(caractere["bottom"]) for caractere in caracteres)
    except Exception:  # pragma: no cover - PDF exotique
        return {}
    return {
        "haut": _borne_marge(haut * points_vers_mm),
        "bas": _borne_marge(bas * points_vers_mm),
        "gauche": _borne_marge(gauche * points_vers_mm),
        "droite": _borne_marge(droite * points_vers_mm),
    }


def _borne_marge(valeur_mm: float) -> float:
    """Ramène une marge mesurée dans la plage exploitable (5 à 80 mm).

    Une référence dont le texte ne descend pas en bas de page produit une « marge
    basse » énorme : cette mesure est une borne inférieure, pas une marge. La
    borner est plus honnête que de produire un gabarit inutilisable.
    """
    return round(min(max(valeur_mm, 5.0), 80.0), 1)


def _typographie_pdf(typographie: dict[str, Any]) -> dict[str, Any]:
    """Rôles typographiques d'un PDF : corps, titres et police dominante."""
    if not typographie:
        return {}
    police = _famille_lisible(
        max(typographie.get("polices", {}), key=typographie["polices"].get, default="")
    )
    resultat: dict[str, Any] = {}
    corps = typographie.get("taille_corps_probable")
    titre = typographie.get("taille_titre_probable")
    if corps:
        resultat["corps"] = {"taille_pt": float(corps), "police": police}
    if titre:
        resultat["titre"] = {"taille_pt": float(titre), "police": police, "gras": True}
        resultat["titres"] = {
            1: {"taille_pt": float(titre), "police": police, "gras": True}
        }
    return resultat


def _famille_lisible(nom_pdf: str) -> str:
    """Nom de police lisible depuis un nom de police PDF (``Times-Bold`` → Times)."""
    minuscule = (nom_pdf or "").lower()
    for cle, famille in _FAMILLES_PDF:
        if cle in minuscule:
            return famille
    return CARSO_DEFAUT.corps.police


# --- XLSX ---------------------------------------------------------------------


def _reference_xlsx(chemin: Path, niveau: str) -> dict[str, Any]:
    """Analyse d'une référence tabulaire : structure de classeur, pas de style."""
    structure = inspecter_xlsx(chemin, niveau=niveau)
    sections = [
        {"titre": feuille["nom"], "niveau": 1, "nb_paragraphes": feuille["nb_lignes"],
         "nb_listes": 0, "nb_tableaux": 1, "nb_images": 0}
        for feuille in structure["feuilles"]
    ]
    return {
        "format": "xlsx",
        "content_structure": {
            "type_probable": "classeur",
            "sections": sections,
            "nb_sections": len(sections),
            "nb_tableaux": len(sections),
            "nb_images": 0,
            "a_entete": False,
            "a_pied": False,
            "a_couverture": False,
            "colonnes": [
                colonne["lettre"]
                for feuille in structure["feuilles"]
                for colonne in feuille.get("colonnes", [])
            ],
            "fusions": sum(feuille.get("nb_fusions", 0) for feuille in structure["feuilles"]),
            "formules": sum(feuille.get("nb_formules", 0) for feuille in structure["feuilles"]),
        },
        "style_spec": {"tableau": {"present": True}},
        "avertissements": [
            "classeur_reference (aucun style de document à réutiliser : structure seule)"
        ],
    }


# --- Utilitaires ---------------------------------------------------------------


def _format_depuis_mm(largeur_mm: float | None) -> str | None:
    """Format de page le plus proche d'une largeur mesurée (mm)."""
    if not largeur_mm:
        return None
    largeur = float(largeur_mm)
    # Un PDF exprime ses dimensions en points : on accepte les deux unités.
    if largeur > 400:
        largeur *= 25.4 / 72.0
    return min(FORMATS_MM, key=lambda nom: abs(FORMATS_MM[nom][0] - largeur))


def _alignement(valeur: str | None) -> str | None:
    """Canalise un alignement python-docx vers le vocabulaire du domaine."""
    if not valeur:
        return None
    minuscule = str(valeur).lower()
    if "center" in minuscule:
        return "centre"
    if "right" in minuscule:
        return "droite"
    if "justify" in minuscule:
        return "justifie"
    return "gauche"


def _nombre(valeur: Any, defaut: float) -> float:
    """Nombre utilisable, sinon valeur par défaut."""
    try:
        return float(valeur)
    except (TypeError, ValueError):
        return defaut


def _style_texte(valeurs: dict[str, Any], defaut: StyleTexte) -> StyleTexte:
    """Fusionne un rôle typographique extrait sur un rôle de référence."""
    mise_a_jour: dict[str, Any] = {}
    if valeurs.get("police"):
        mise_a_jour["police"] = str(valeurs["police"])
    if valeurs.get("taille_pt"):
        mise_a_jour["taille_pt"] = _nombre(valeurs["taille_pt"], defaut.taille_pt)
    if valeurs.get("couleur"):
        mise_a_jour["couleur"] = str(valeurs["couleur"])
    if isinstance(valeurs.get("gras"), bool):
        mise_a_jour["gras"] = valeurs["gras"] and defaut.gras
    if isinstance(valeurs.get("italique"), bool) and valeurs["italique"]:
        mise_a_jour["italique"] = True
    if valeurs.get("alignement"):
        mise_a_jour["alignement"] = valeurs["alignement"]
    return defaut.model_copy(update=mise_a_jour)


def _style_tableau(valeurs: dict[str, Any], defaut: StyleTableau) -> StyleTableau:
    """Fusionne un style de tableau extrait sur le style de référence."""
    mise_a_jour: dict[str, Any] = {}
    for cle in ("bordures", "entete_gras"):
        if isinstance(valeurs.get(cle), bool):
            mise_a_jour[cle] = valeurs[cle]
    for cle in ("bordure_couleur", "entete_fond", "entete_couleur", "bande_fond"):
        if valeurs.get(cle):
            mise_a_jour[cle] = str(valeurs[cle])
    return defaut.model_copy(update=mise_a_jour)


def _style_couverture(valeurs: dict[str, Any], defaut: StyleCouverture) -> StyleCouverture:
    """Fusionne un style de couverture extrait sur le style de référence."""
    mise_a_jour: dict[str, Any] = {}
    if valeurs.get("titre_taille_pt"):
        mise_a_jour["titre_taille_pt"] = _nombre(
            valeurs["titre_taille_pt"], defaut.titre_taille_pt
        )
    if valeurs.get("couleur_accent"):
        mise_a_jour["couleur_accent"] = str(valeurs["couleur_accent"])
    if valeurs.get("alignement"):
        mise_a_jour["alignement"] = valeurs["alignement"]
    return defaut.model_copy(update=mise_a_jour)


def _style_zone(valeurs: dict[str, Any], defaut: StyleEntetePied) -> StyleEntetePied:
    """Fusionne une zone d'en-tête/pied : **jamais** le texte de la référence.

    Le texte d'un en-tête client est du **contenu** : seule sa présence est
    retenue, le libellé CARSO par défaut est conservé.
    """
    if not valeurs.get("present"):
        return defaut
    return defaut.model_copy(update={"texte": defaut.texte})
