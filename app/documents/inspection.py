"""Inspection structurée DOCX / PDF / XLSX (Lots T1 → T2, ADR 0005 Phase 3).

Complète l'extraction texte : l'agent ne reçoit plus une longue chaîne, mais une
**structure** — hiérarchie des titres, tableaux avec leurs dimensions, images,
en-tête/pied, typographie, fusions, formules. C'est ce qui lui permet de dire
« la colonne 3 du tableau budget est trop large » : l'information existe.

Deux niveaux de détail, pour ne jamais déverser un gros document dans le
contexte (Phase 19) :

- ``resume`` (défaut) : structure, comptages, extraits courts — le survol ;
- ``complet`` : ajoute les runs (police, taille, gras/italique/couleur), le texte
  intégral des pages PDF, les matrices de cellules et le détail des fusions.

Aucune librairie de rendu n'est requise : un DOCX n'a **pas** de pages physiques
fiables (``read_document_range`` parle donc de paragraphes), et l'OCR reste hors
périmètre (un PDF sans couche texte lève une erreur explicite).

Bornes volontaires : ``MAX_BLOCS`` blocs de hiérarchie, ``MAX_CELLULES`` cellules
de tableau, ``ANALYSE_MAX_PAGES`` pages analysées finement (typographie,
tableaux) — au-delà, les comptages restent exacts mais l'analyse détaillée
s'arrête, et l'information est renvoyée dans ``limites``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.core.errors import ValidationError
from app.documents.extraction.base import require_library
from app.documents.styles import EMU_PAR_MM

__all__ = [
    "inspecter_docx",
    "inspecter_pdf",
    "inspecter_xlsx",
    "lire_docx_plage",
    "lire_pdf_plage",
    "lire_xlsx_plage",
    "NIVEAUX",
]

#: Niveaux de détail acceptés.
NIVEAUX = ("resume", "complet")

MAX_BLOCS = 400
MAX_CELLULES = 2000
ANALYSE_MAX_PAGES = 10
_EXTRAIT_RESUME = 240


def _niveau(valeur: str) -> str:
    """Valide le niveau de détail demandé."""
    propre = str(valeur or "resume").strip().lower()
    if propre not in NIVEAUX:
        raise ValidationError(
            f"Niveau d'inspection inconnu : {valeur!r}",
            details={"niveaux": list(NIVEAUX)},
        )
    return propre


def _texte_court(texte: str, niveau: str) -> str:
    """Tronque un texte en mode résumé (le mode complet renvoie tout)."""
    propre = (texte or "").strip()
    if niveau == "complet" or len(propre) <= _EXTRAIT_RESUME:
        return propre
    return f"{propre[:_EXTRAIT_RESUME]}…"


# --- DOCX ---------------------------------------------------------------------

#: Styles Word de titre → niveau de titre (0 = style « Title »).
_TITRES_WORD = {"Title": 0}


def _niveau_titre(nom_style: str) -> int | None:
    """Niveau de titre d'un style Word (``Heading 2`` → 2), sinon ``None``."""
    if nom_style in _TITRES_WORD:
        return _TITRES_WORD[nom_style]
    if nom_style.startswith("Heading "):
        reste = nom_style.split(" ", 1)[1].strip()
        if reste.isdigit():
            return int(reste)
    return None


def inspecter_docx(chemin: Path, *, niveau: str = "resume") -> dict[str, Any]:
    """Structure d'un DOCX : hiérarchie, styles, tableaux, images, sections.

    Args:
        chemin: fichier DOCX (validé en amont par la couche storage).
        niveau: ``resume`` (défaut) ou ``complet``.

    Raises:
        ValidationError: document illisible.
    """
    detail = _niveau(niveau)
    docx = require_library("docx")
    try:
        document = docx.Document(str(chemin))
    except Exception as exc:
        raise ValidationError("DOCX illisible") from exc

    hierarchie, styles, tableaux = _hierarchie_docx(document, detail)
    sections = _sections_docx(document)
    liens = _liens_docx(document)
    images = _images_docx(document)

    resultat: dict[str, Any] = {
        # Clés historiques (Lot T1) — conservées pour ne rien casser.
        "format": "docx",
        "nb_paragraphes": len(document.paragraphs),
        "nb_tableaux": len(document.tables),
        "styles": [{"nom": nom, "occurrences": n} for nom, n in sorted(styles.items())],
        "sections": [
            {
                "orientation": section["orientation"],
                "page_width_emu": section["page_width_emu"],
                "page_height_emu": section["page_height_emu"],
            }
            for section in sections
        ],
        "has_headers_footers": any(
            (s.header.paragraphs or s.footer.paragraphs) for s in document.sections
        ),
        "metadata": _metadonnees_docx(document),
        # Structure (Lot T2).
        "hierarchie": hierarchie,
        "tableaux": tableaux,
        "images": images,
        "liens": liens,
        "sections_detaillees": sections,
        "entetes": [section["entete"] for section in sections],
        "pieds": [section["pied"] for section in sections],
        "limites": _limites(hierarchie, tableaux),
    }
    if detail == "complet":
        resultat["paragraphes"] = _paragraphes_docx(document)
    return resultat


def _metadonnees_docx(document: Any) -> dict[str, Any]:
    """Métadonnées du DOCX (titre, auteur, société, mots-clés, dates)."""
    props = document.core_properties
    return {
        "title": props.title,
        "author": props.author,
        "last_modified_by": props.last_modified_by,
        "subject": props.subject,
        "keywords": props.keywords,
        "category": props.category,
        "created": str(props.created) if props.created else None,
        "modified": str(props.modified) if props.modified else None,
    }


def _hierarchie_docx(
    document: Any, niveau: str
) -> tuple[list[dict[str, Any]], dict[str, int], list[dict[str, Any]]]:
    """Hiérarchie du corps dans l'ordre réel (paragraphes, listes, tableaux).

    ``iter_inner_content`` restitue l'ordre **réel** du document : parcourir
    ``document.paragraphs`` puis ``document.tables`` séparément ferait perdre la
    position des tableaux au milieu du texte.
    """
    hierarchie: list[dict[str, Any]] = []
    styles: dict[str, int] = {}
    tableaux: list[dict[str, Any]] = []
    liste_courante: dict[str, Any] | None = None

    for bloc in document.iter_inner_content():
        if hasattr(bloc, "rows"):  # Table
            description = _decrire_tableau_docx(bloc, niveau)
            tableaux.append(description)
            liste_courante = None
            if len(hierarchie) < MAX_BLOCS:
                hierarchie.append({"type": "tableau", **description})
            continue

        nom_style = getattr(getattr(bloc, "style", None), "name", None) or "Normal"
        styles[nom_style] = styles.get(nom_style, 0) + 1
        texte = (bloc.text or "").strip()
        niveau_titre = _niveau_titre(nom_style)
        en_liste = nom_style in ("List Bullet", "List Number", "List Paragraph")

        if en_liste and texte:
            numerotee = nom_style == "List Number"
            if (
                liste_courante is not None
                and liste_courante["numerotee"] == numerotee
                and len(hierarchie) < MAX_BLOCS
            ):
                liste_courante["items"].append(_texte_court(texte, niveau))
            else:
                liste_courante = {
                    "type": "liste",
                    "numerotee": numerotee,
                    "items": [_texte_court(texte, niveau)],
                }
                if len(hierarchie) < MAX_BLOCS:
                    hierarchie.append(liste_courante)
            continue

        liste_courante = None
        nb_images = _nb_images_paragraphe(bloc)
        if niveau_titre is not None and texte:
            if len(hierarchie) < MAX_BLOCS:
                hierarchie.append(
                    {"type": "titre", "niveau": niveau_titre, "texte": _texte_court(texte, niveau)}
                )
            continue
        if texte or nb_images or _a_saut_de_page(bloc):
            if len(hierarchie) < MAX_BLOCS:
                hierarchie.append(
                    {
                        "type": "paragraphe",
                        "style": nom_style,
                        "texte": _texte_court(texte, niveau),
                        "nb_images": nb_images,
                        "saut_de_page": _a_saut_de_page(bloc),
                    }
                )
    return hierarchie, styles, tableaux


def _decrire_tableau_docx(tableau: Any, niveau: str) -> dict[str, Any]:
    """Description d'un tableau DOCX : dimensions, largeurs, fusions, en-tête."""
    nb_lignes = len(tableau.rows)
    nb_colonnes = len(tableau.columns)
    entetes = [
        (cellule.text or "").strip() for cellule in tableau.rows[0].cells
    ] if nb_lignes else []
    largeurs = _largeurs_tableau_docx(tableau)
    fusions, nb_fusions = _fusions_tableau_docx(tableau)
    description: dict[str, Any] = {
        "nb_lignes": nb_lignes,
        "nb_colonnes": nb_colonnes,
        "entete": entetes,
        "largeurs_mm": largeurs,
        "nb_fusions": nb_fusions,
        "fusions": fusions,
    }
    if niveau == "complet":
        lignes: list[list[str]] = []
        cellules = 0
        for ligne in tableau.rows:
            valeurs: list[str] = []
            for cellule in ligne.cells:
                if cellules >= MAX_CELLULES:
                    break
                valeurs.append((cellule.text or "").strip())
                cellules += 1
            lignes.append(valeurs)
        description["lignes"] = lignes
    return description


def _largeurs_tableau_docx(tableau: Any) -> list[float]:
    """Largeurs de colonnes en millimètres.

    Les largeurs **déclarées cellule par cellule** (``w:tcW``) priment sur la
    grille ``w:tblGrid`` : un document construit par un outil peut laisser la
    grille à sa valeur par défaut tout en fixant ses colonnes.
    """
    from docx.oxml.ns import qn

    if tableau.rows:
        largeurs_cellules = [
            round(float(cellule.width) / EMU_PAR_MM, 2)
            for cellule in tableau.rows[0].cells
            if cellule.width is not None
        ]
        if largeurs_cellules and len(largeurs_cellules) == len(tableau.columns):
            return largeurs_cellules
    grille = tableau._tbl.find(qn("w:tblGrid"))
    if grille is not None:
        largeurs = [
            round(float(twips) / 56.6929, 2)
            for colonne in grille.findall(qn("w:gridCol"))
            if (twips := colonne.get(qn("w:w")))
        ]
        if largeurs:
            return largeurs
    return []


def _fusions_tableau_docx(tableau: Any) -> tuple[list[dict[str, Any]], int]:
    """Fusions déclarées dans le tableau (``gridSpan``, ``vMerge``)."""
    from docx.oxml.ns import qn

    fusions: list[dict[str, Any]] = []
    for index_ligne, ligne in enumerate(tableau.rows):
        for index_colonne, cellule in enumerate(ligne.cells):
            tc_pr = cellule._tc.find(qn("w:tcPr"))
            if tc_pr is None:
                continue
            span = tc_pr.find(qn("w:gridSpan"))
            if span is not None and span.get(qn("w:val")) not in (None, "1"):
                fusions.append(
                    {
                        "ligne": index_ligne,
                        "colonne": index_colonne,
                        "type": "horizontale",
                        "etendue": int(span.get(qn("w:val")) or 1),
                    }
                )
            vertical = tc_pr.find(qn("w:vMerge"))
            if vertical is not None and (vertical.get(qn("w:val")) or "continue") == "restart":
                fusions.append(
                    {
                        "ligne": index_ligne,
                        "colonne": index_colonne,
                        "type": "verticale",
                        "etendue": None,
                    }
                )
    return fusions, len(fusions)


def _nb_images_paragraphe(paragraphe: Any) -> int:
    """Nombre d'images intégrées à un paragraphe (dessins et objets liés)."""
    from docx.oxml.ns import qn

    xml = paragraphe._p
    return len(xml.findall(".//" + qn("w:drawing"))) + len(xml.findall(".//" + qn("w:pict")))


def _a_saut_de_page(paragraphe: Any) -> bool:
    """Vrai si le paragraphe porte un saut de page explicite."""
    from docx.oxml.ns import qn

    xml = paragraphe._p
    for saut in xml.findall(".//" + qn("w:br")):
        if saut.get(qn("w:type")) == "page":
            return True
    p_pr = xml.find(qn("w:pPr"))
    return p_pr is not None and p_pr.find(qn("w:pageBreakBefore")) is not None


def _paragraphes_docx(document: Any) -> list[dict[str, Any]]:
    """Détail des paragraphes : style, alignement, espacements et runs."""
    paragraphes: list[dict[str, Any]] = []
    for paragraphe in document.paragraphs:
        if not (paragraphe.text or "").strip() and not _nb_images_paragraphe(paragraphe):
            continue
        format_ = paragraphe.paragraph_format
        paragraphes.append(
            {
                "style": getattr(getattr(paragraphe, "style", None), "name", None),
                "texte": paragraphe.text,
                "alignement": str(paragraphe.alignment) if paragraphe.alignment else None,
                "indentation_mm": _emu_vers_mm(format_.left_indent),
                "espace_avant_pt": _pt(format_.space_before),
                "espace_apres_pt": _pt(format_.space_after),
                "interligne": float(format_.line_spacing)
                if isinstance(format_.line_spacing, float)
                else None,
                "saut_de_page": _a_saut_de_page(paragraphe),
                "runs": [
                    {
                        "texte": run.text,
                        "police": run.font.name,
                        "taille_pt": _pt(run.font.size),
                        "gras": bool(run.font.bold),
                        "italique": bool(run.font.italic),
                        "souligne": bool(run.font.underline),
                        "couleur": _couleur_run(run),
                    }
                    for run in paragraphe.runs
                    if (run.text or "").strip()
                ],
            }
        )
    return paragraphes


def _couleur_run(run: Any) -> str | None:
    """Couleur d'un run en ``#RRGGBB`` (``None`` si laissée au style)."""
    try:
        couleur = run.font.color.rgb
    except Exception:  # pragma: no cover - couleur de thème
        return None
    return f"#{couleur}" if couleur else None


def _sections_docx(document: Any) -> list[dict[str, Any]]:
    """Propriétés de section : format, orientation, marges, en-tête et pied."""
    sections: list[dict[str, Any]] = []
    for section in document.sections:
        entete = "\n".join(p.text for p in section.header.paragraphs if p.text.strip())
        pied = "\n".join(p.text for p in section.footer.paragraphs if p.text.strip())
        sections.append(
            {
                "orientation": (
                    "paysage"
                    if float(section.page_width) > float(section.page_height)
                    else "portrait"
                ),
                "page_width_emu": int(section.page_width),
                "page_height_emu": int(section.page_height),
                "largeur_mm": round(float(section.page_width) / EMU_PAR_MM, 2),
                "hauteur_mm": round(float(section.page_height) / EMU_PAR_MM, 2),
                "marges_mm": {
                    "haut": round(float(section.top_margin or 0) / EMU_PAR_MM, 2),
                    "bas": round(float(section.bottom_margin or 0) / EMU_PAR_MM, 2),
                    "gauche": round(float(section.left_margin or 0) / EMU_PAR_MM, 2),
                    "droite": round(float(section.right_margin or 0) / EMU_PAR_MM, 2),
                },
                "premiere_page_distincte": bool(
                    section.different_first_page_header_footer
                ),
                "entete": entete,
                "pied": pied,
            }
        )
    return sections


def _liens_docx(document: Any) -> list[dict[str, str]]:
    """Liens hypertextes réellement résolus par les relations du document."""
    liens: list[dict[str, str]] = []
    rels = getattr(document.part, "rels", {})
    for relation in rels.values():
        if relation.reltype.endswith("/hyperlink"):
            liens.append({"url": relation.target_ref})
    return liens


def _images_docx(document: Any) -> list[dict[str, Any]]:
    """Images intégrées avec leurs dimensions en millimètres."""
    return [
        {
            "largeur_mm": round(forme.width / EMU_PAR_MM, 2),
            "hauteur_mm": round(forme.height / EMU_PAR_MM, 2),
            "type": str(forme.type) if forme.type is not None else None,
        }
        for forme in document.inline_shapes
    ]


def _limites(hierarchie: list[dict[str, Any]], tableaux: list[dict[str, Any]]) -> list[str]:
    """Signale ce qui a été borné (jamais de troncature silencieuse)."""
    limites: list[str] = []
    if len(hierarchie) >= MAX_BLOCS:
        limites.append(f"hierarchie_tronquee_a_{MAX_BLOCS}_blocs")
    if any(
        tableau["nb_lignes"] * max(1, tableau["nb_colonnes"]) > MAX_CELLULES
        for tableau in tableaux
    ):
        limites.append(f"cellules_tronquees_a_{MAX_CELLULES}")
    return limites


def _emu_vers_mm(valeur: Any) -> float | None:
    """EMU → millimètres (``None`` si la valeur est absente)."""
    return round(float(valeur) / EMU_PAR_MM, 2) if valeur is not None else None


def _pt(valeur: Any) -> float | None:
    """Taille python-docx → points (``None`` si laissée au style)."""
    return round(float(valeur.pt), 2) if valeur is not None else None


# --- PDF ----------------------------------------------------------------------


def inspecter_pdf(chemin: Path, *, niveau: str = "resume") -> dict[str, Any]:
    """Structure d'un PDF : pages, typographie, tableaux, zones répétées.

    ``pdfplumber`` fournit la typographie (tailles/polices) et les tableaux ;
    s'il est absent, l'inspection reste valide (pypdf seul) et le signale.

    Raises:
        ValidationError: PDF illisible ou chiffré.
    """
    detail = _niveau(niveau)
    pypdf = require_library("pypdf")
    try:
        lecteur = pypdf.PdfReader(str(chemin))
    except Exception as exc:
        raise ValidationError("PDF illisible") from exc
    if lecteur.is_encrypted:
        raise ValidationError("PDF chiffré : inspection impossible")

    meta = lecteur.metadata
    premiere = lecteur.pages[0] if lecteur.pages else None
    largeur = float(premiere.mediabox.width) if premiere else None
    hauteur = float(premiere.mediabox.height) if premiere else None
    resultat: dict[str, Any] = {
        "format": "pdf",
        "nb_pages": len(lecteur.pages),
        "page_width": largeur,
        "page_height": hauteur,
        "orientation": (
            "paysage"
            if largeur and hauteur and largeur > hauteur
            else "portrait"
        ),
        "metadata": {
            "title": getattr(meta, "title", None) if meta else None,
            "author": getattr(meta, "author", None) if meta else None,
        },
        "nb_signets": len(lecteur.outline or []) if hasattr(lecteur, "outline") else 0,
        "limites": [],
    }
    analyse = _analyser_pdf(chemin, detail, resultat)
    resultat.update(analyse)
    return resultat


def _analyser_pdf(chemin: Path, niveau: str, resultat: dict[str, Any]) -> dict[str, Any]:
    """Analyse pdfplumber : typographie, pages, tableaux, zones d'en-tête/pied."""
    try:
        pdfplumber = require_library("pdfplumber")
    except Exception:
        resultat["limites"] = ["analyse_avancee_indisponible_pdfplumber_absent"]
        return {"pages": [], "typographie": {}, "zones_entete_pied": {}}

    pages: list[dict[str, Any]] = []
    tailles: dict[str, int] = {}
    polices: dict[str, int] = {}
    entetes: dict[str, int] = {}
    pieds: dict[str, int] = {}
    limites: list[str] = list(resultat.get("limites", []))
    try:
        with pdfplumber.open(str(chemin)) as document:
            for index, page in enumerate(document.pages, start=1):
                caracteres = page.chars or []
                lignes = _lignes_pdf(page)
                description: dict[str, Any] = {
                    "numero": index,
                    "largeur_pt": round(float(page.width), 2),
                    "hauteur_pt": round(float(page.height), 2),
                    "nb_caracteres": len(caracteres),
                    "nb_lignes": len(lignes),
                    "titres_probables": _titres_probables(lignes),
                }
                if niveau == "complet":
                    description["texte"] = page.extract_text() or ""
                if index <= ANALYSE_MAX_PAGES:
                    description["nb_tableaux"] = len(page.find_tables())
                    description["nb_images"] = len(page.images or [])
                    for caractere in caracteres:
                        taille = str(round(float(caractere.get("size") or 0), 1))
                        tailles[taille] = tailles.get(taille, 0) + 1
                        police = str(caractere.get("fontname") or "inconnue")
                        polices[police] = polices.get(police, 0) + 1
                    if lignes:
                        entetes[lignes[0][1]] = entetes.get(lignes[0][1], 0) + 1
                        pieds[lignes[-1][1]] = pieds.get(lignes[-1][1], 0) + 1
                pages.append(description)
    except Exception as exc:  # pragma: no cover - PDF exotique
        raise ValidationError("Analyse PDF impossible (fichier illisible)") from exc

    if len(pages) > ANALYSE_MAX_PAGES:
        limites.append(f"analyse_detaillee_limitee_a_{ANALYSE_MAX_PAGES}_pages")
    resultat["limites"] = limites
    return {
        "pages": pages,
        "typographie": _typographie(tailles, polices),
        "zones_entete_pied": {
            "entete": [texte for texte, n in entetes.items() if n > 1],
            "pied": [texte for texte, n in pieds.items() if n > 1],
        },
    }


def _lignes_pdf(page: Any) -> list[tuple[float, str]]:
    """Lignes de texte d'une page PDF : ``(taille max, texte)`` dans l'ordre."""
    mots = page.extract_words(extra_attrs=["size"], use_text_flow=True) or []
    lignes: dict[float, list[dict[str, Any]]] = {}
    for mot in mots:
        ligne = round(float(mot.get("top") or 0), 1)
        lignes.setdefault(ligne, []).append(mot)
    resultat: list[tuple[float, str]] = []
    for ligne in sorted(lignes):
        mots_ligne = sorted(lignes[ligne], key=lambda mot: float(mot.get("x0") or 0))
        texte = " ".join(str(mot.get("text", "")) for mot in mots_ligne).strip()
        taille = max((float(mot.get("size") or 0) for mot in mots_ligne), default=0.0)
        if texte:
            resultat.append((taille, texte))
    return resultat


def _titres_probables(lignes: list[tuple[float, str]]) -> list[str]:
    """Lignes dont la police dépasse nettement la taille dominante du document."""
    if not lignes:
        return []
    tailles = sorted(taille for taille, _ in lignes if taille > 0)
    if not tailles:
        return []
    mediane = tailles[len(tailles) // 2]
    return [texte for taille, texte in lignes if taille >= mediane * 1.25][:10]


def _typographie(tailles: dict[str, int], polices: dict[str, int]) -> dict[str, Any]:
    """Typographie dominante : tailles et polices par fréquence de caractères."""
    if not tailles:
        return {}
    ordonnees = sorted(tailles.items(), key=lambda item: (-item[1], item[0]))
    tailles_triees = sorted(float(taille) for taille in tailles)
    return {
        "tailles": tailles,
        "polices": polices,
        "taille_corps_probable": float(ordonnees[0][0]),
        "taille_titre_probable": tailles_triees[-1] if tailles_triees else None,
    }


# --- XLSX ---------------------------------------------------------------------


def inspecter_xlsx(chemin: Path, *, niveau: str = "resume") -> dict[str, Any]:
    """Structure d'un XLSX : feuilles, colonnes, fusions, formules, styles.

    Le classeur est lu **avec** ses formules (``data_only=False``) : c'est ce qui
    décrit la feuille. Les valeurs calculées sont accessibles par la lecture de
    plage (``lire_xlsx_plage``, qui lit les valeurs mises en cache).

    Raises:
        ValidationError: classeur macro ou illisible.
    """
    detail = _niveau(niveau)
    if chemin.suffix.lower() == ".xlsm":
        raise ValidationError("Classeur macro (.xlsm) refusé")
    openpyxl = require_library("openpyxl")
    try:
        classeur = openpyxl.load_workbook(str(chemin), read_only=False, data_only=False)
    except Exception as exc:
        raise ValidationError("XLSX illisible") from exc

    try:
        feuilles: list[dict[str, Any]] = []
        for feuille in classeur.worksheets:
            dimensions = None
            if feuille.calculate_dimension() != "A1:A1" or feuille["A1"].value is not None:
                dimensions = feuille.calculate_dimension()
            fusions = [str(plage) for plage in feuille.merged_cells.ranges]
            formules = {
                cellule.coordinate: str(cellule.value)
                for ligne in feuille.iter_rows()
                for cellule in ligne
                if isinstance(cellule.value, str) and cellule.value.startswith("=")
            }
            entetes = [
                "" if cellule.value is None else str(cellule.value)
                for cellule in next(feuille.iter_rows(min_row=1, max_row=1), [])
            ]
            feuilles.append(
                {
                    # Clés historiques (Lot T1).
                    "nom": feuille.title,
                    "nb_lignes": feuille.max_row or 0,
                    "nb_colonnes": feuille.max_column or 0,
                    # Structure (Lot T2).
                    "dimensions": dimensions,
                    "entete": entetes,
                    "colonnes": [
                        {"lettre": lettre, "largeur": dimension.width}
                        for lettre, dimension in (feuille.column_dimensions or {}).items()
                        if dimension.width
                    ],
                    "fusions": fusions,
                    "nb_fusions": len(fusions),
                    "nb_formules": len(formules),
                    "formules": (
                        formules if detail == "complet" else dict(list(formules.items())[:20])
                    ),
                    "styles": _styles_xlsx(feuille, detail),
                }
            )
            if detail == "complet":
                feuilles[-1]["apercu"] = [
                    ["" if cellule.value is None else str(cellule.value) for cellule in ligne]
                    for ligne in feuille.iter_rows(
                        min_row=1, max_row=min(10, feuille.max_row or 1)
                    )
                ]
        return {"format": "xlsx", "nb_feuilles": len(feuilles), "feuilles": feuilles}
    finally:
        classeur.close()


def _styles_xlsx(feuille: Any, niveau: str) -> dict[str, Any]:
    """Comptage des mises en forme utiles (gras, remplissage, format de nombre)."""
    gras = 0
    remplissages = 0
    formats_nombre = 0
    for ligne in feuille.iter_rows():
        for cellule in ligne:
            if cellule.font is not None and cellule.font.bold:
                gras += 1
            if cellule.fill is not None and cellule.fill.fgColor is not None:
                if (cellule.fill.patternType or "none") != "none":
                    remplissages += 1
            if cellule.number_format not in (None, "General"):
                formats_nombre += 1
    return {
        "cellules_gras": gras,
        "cellules_remplies": remplissages,
        "cellules_formattees": formats_nombre,
        "detail": niveau == "complet",
    }


# --- Lecture de plages (Lot T1, inchangée) -----------------------------------


def _borne(debut: int | None, fin: int | None, total: int) -> tuple[int, int]:
    """Normalise une plage 1-indexée inclusive dans ``[1, total]``."""
    if total <= 0:
        return (1, 0)
    debut_n = 1 if debut is None else max(1, debut)
    fin_n = total if fin is None else min(total, fin)
    if debut_n > fin_n:
        raise ValidationError(
            "Plage invalide",
            details={"from": debut_n, "to": fin_n, "total": total},
        )
    return debut_n, fin_n


def lire_docx_plage(
    chemin: Path, *, from_page: int | None = None, to_page: int | None = None
) -> dict[str, Any]:
    """Lit les paragraphes d'un DOCX (plage = index de paragraphes, 1-indexé).

    Un DOCX n'a pas de pages physiques fiables sans moteur de rendu : la
    « page » ici désigne un paragraphe. Documenté explicitement.
    """
    docx = require_library("docx")
    document = docx.Document(str(chemin))
    paragraphes = [(p.text or "").strip() for p in document.paragraphs]
    debut, fin = _borne(from_page, to_page, len(paragraphes))
    extraits = paragraphes[debut - 1 : fin]
    return {
        "format": "docx",
        # Vocabulaire commun aux trois formats (l'appelant n'a pas à savoir
        # lequel il interroge) : ``unite`` + ``debut``/``fin``/``total``.
        "unite": "paragraphes",
        "debut": debut,
        "fin": fin,
        "total": len(paragraphes),
        "from_paragraphe": debut,
        "to_paragraphe": fin,
        "total_paragraphes": len(paragraphes),
        "texte": "\n".join(p for p in extraits if p),
    }


def lire_pdf_plage(
    chemin: Path, *, from_page: int | None = None, to_page: int | None = None
) -> dict[str, Any]:
    """Lit le texte d'un PDF entre deux pages (1-indexées, inclusives)."""
    pypdf = require_library("pypdf")
    lecteur = pypdf.PdfReader(str(chemin))
    if lecteur.is_encrypted:
        raise ValidationError("PDF chiffré")
    debut, fin = _borne(from_page, to_page, len(lecteur.pages))
    pages = []
    for index in range(debut - 1, fin):
        try:
            pages.append(str(lecteur.pages[index].extract_text() or ""))
        except Exception:
            pages.append("")
    return {
        "format": "pdf",
        "unite": "pages",
        "debut": debut,
        "fin": fin,
        "total": len(lecteur.pages),
        "from_page": debut,
        "to_page": fin,
        "total_pages": len(lecteur.pages),
        "texte": "\n\n".join(p.strip() for p in pages if p.strip()),
    }


def lire_xlsx_plage(
    chemin: Path,
    *,
    sheet: str | None = None,
    from_row: int | None = None,
    to_row: int | None = None,
) -> dict[str, Any]:
    """Lit une feuille XLSX (lignes 1-indexées, inclusives, valeurs mises en cache)."""
    if chemin.suffix.lower() == ".xlsm":
        raise ValidationError("Classeur macro (.xlsm) refusé")
    openpyxl = require_library("openpyxl")
    classeur = openpyxl.load_workbook(str(chemin), read_only=True, data_only=True)
    try:
        feuille = classeur[sheet] if sheet else classeur.worksheets[0]
        total = feuille.max_row or 0
        debut, fin = _borne(from_row, to_row, total)
        lignes: list[list[Any]] = []
        for index, row in enumerate(feuille.iter_rows(values_only=True), start=1):
            if index < debut:
                continue
            if index > fin:
                break
            lignes.append(["" if c is None else c for c in row])
        return {
            "format": "xlsx",
            "unite": "lignes",
            "debut": debut,
            "fin": fin,
            "total": total,
            "feuille": feuille.title,
            "from_row": debut,
            "to_row": fin,
            "lignes": lignes,
            # Texte tabulé : le format XLSX n'a pas de prose, mais un appelant
            # uniforme doit pouvoir lire la plage sans connaître le format.
            "texte": "\n".join("\t".join(str(c) for c in ligne) for ligne in lignes),
        }
    finally:
        classeur.close()
