"""Lecture d'**éléments** dans un document (incrément 53).

Pourquoi ce module : un DOCX n'a pas de pages physiques, et son contenu ne vit
pas seulement dans ses paragraphes : un tableau, un en-tête ou un pied de page
portent de l'information métier (budgets, lots, référence de l'appel). Lire un
DOCX « par paragraphes » laissait donc deux trous qui faisaient boucler l'agent :

- un hit trouvé dans un **tableau** n'était pas relisible (l'index renvoyé
  pointait un paragraphe voisin) → l'agent relançait la recherche ;
- les **en-têtes/pieds** n'étaient ni cherchés ni lisibles.

L'unité de lecture d'un DOCX est donc l'**élément** du flux
(``document.iter_inner_content()`` : paragraphe ou tableau), 1-indexé : la même
unité pour ``chercher_dans_document``, ``read_document_range`` et
``lire_element_document``. Un hit se relit toujours à l'identique.

Types d'éléments par format (``TYPES_ELEMENTS``) :

- ``docx`` : ``element`` (flux), ``paragraphe``, ``tableau``, ``entete``, ``pied`` ;
- ``pdf``  : ``element`` (= page), ``page`` ;
- ``xlsx`` : ``element`` (= ligne), ``ligne``, ``feuille``.

Tout retour est **borné** (lignes d'un tableau, caractères d'une page) et dit ce
qu'il a borné (``tronque``) : jamais de troncature silencieuse.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.core.errors import ValidationError
from app.documents.extraction.base import require_library

__all__ = [
    "CARACTERES_ELEMENT_DEFAUT",
    "LIGNES_TABLEAU_DEFAUT",
    "MAX_CARACTERES_ELEMENT",
    "MAX_LIGNES_TABLEAU",
    "TYPES_ELEMENTS",
    "decrire_element_docx",
    "elements_docx",
    "lire_docx_elements",
    "lire_element",
    "zones_docx",
]

#: Types d'éléments lisibles, par format.
TYPES_ELEMENTS: dict[str, tuple[str, ...]] = {
    "docx": ("element", "paragraphe", "tableau", "entete", "pied"),
    "pdf": ("element", "page"),
    "xlsx": ("element", "ligne", "feuille"),
}

#: Lignes d'un tableau lues par défaut / au plus (le contexte reste borné).
LIGNES_TABLEAU_DEFAUT = 40
MAX_LIGNES_TABLEAU = 200
#: Caractères lus par défaut / au plus pour une page PDF ou une feuille.
CARACTERES_ELEMENT_DEFAUT = 8_000
MAX_CARACTERES_ELEMENT = 40_000


# --- aides communes -----------------------------------------------------------


def _type_valide(format_doc: str, type_element: Any) -> str:
    """Valide le type d'élément **pour ce format** et le rend canonique."""
    propre = str(type_element or "element").strip().lower()
    valides = TYPES_ELEMENTS[format_doc]
    if propre not in valides:
        raise ValidationError(
            f"Type d'élément inconnu pour un {format_doc.upper()} : {type_element!r}",
            details={"types_elements": list(valides), "format": format_doc},
        )
    return propre


def _index_entier(index: Any, champ: str = "index") -> int:
    """Index 1-indexé : un entier positif, jamais deviné."""
    if index is None:
        raise ValidationError(
            f"{champ} est obligatoire (1-indexé) pour ce type d'élément",
            details={"champ": champ},
        )
    try:
        valeur = int(index)
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"{champ} doit être un entier (1-indexé)") from exc
    if valeur < 1:
        raise ValidationError(
            f"{champ} doit être supérieur ou égal à 1 (1-indexé)", details={"index": valeur}
        )
    return valeur


def _index_dans_total(index: int, total: int, type_element: str) -> int:
    """Refuse un index hors document en **disant le total** (aide au lieu d'un échec muet)."""
    if index > total:
        raise ValidationError(
            f"Index {index} hors document : {total} {type_element}(s) au total",
            details={"index": index, "total": total, "type_element": type_element},
        )
    return index


def _plafond(valeur: Any, defaut: int, maximum: int, champ: str) -> int:
    """Plafond borné côté serveur (``None`` → valeur par défaut)."""
    if valeur in (None, ""):
        return defaut
    try:
        demande = int(valeur)
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"{champ} doit être un entier") from exc
    return max(1, min(demande, maximum))


def _tronquer_texte(texte: str, plafond: int) -> tuple[str, bool]:
    """Tronque au plafond de caractères (le drapeau dit la troncature)."""
    if len(texte) <= plafond:
        return texte, False
    return texte[:plafond], True


# --- DOCX ---------------------------------------------------------------------


def elements_docx(document: Any) -> list[Any]:
    """Éléments du corps DOCX dans l'ordre **réel** (paragraphes et tableaux).

    ``iter_inner_content`` est la seule source qui conserve la position des
    tableaux au milieu du texte : parcourir ``document.paragraphs`` puis
    ``document.tables`` séparément invente un ordre.
    """
    return list(document.iter_inner_content())


def _est_tableau(bloc: Any) -> bool:
    return hasattr(bloc, "rows")


def cellules_rang(rang: Any) -> list[str]:
    """Cellules visibles d'un rang : une fusion ne duplique pas son texte.

    Définition **unique** partagée par la lecture d'élément et la recherche
    (``app.documents.recherche``) : deux rendus divergents d'un même tableau
    seraient deux vérités.
    """
    vus: set[int] = set()
    cellules: list[str] = []
    for cellule in rang.cells:
        identite = id(cellule._tc)
        if identite in vus:
            continue
        vus.add(identite)
        texte = " ".join((cellule.text or "").split())
        if texte:
            cellules.append(texte)
    return cellules


def _texte_rang(rang: Any) -> str:
    return " | ".join(cellules_rang(rang))


def decrire_element_docx(
    bloc: Any,
    index: int,
    *,
    index_paragraphe: int | None = None,
    index_tableau: int | None = None,
    sous_index: int | None = None,
    max_lignes: int = LIGNES_TABLEAU_DEFAUT,
    ligne_debut: int | None = None,
    relire_type: str | None = None,
    relire_index: int | None = None,
) -> dict[str, Any]:
    """Décrit un élément du flux DOCX : paragraphe ou tableau (borné).

    Args:
        bloc: paragraphe ou tableau issu de ``iter_inner_content``.
        index: position 1-indexée de l'élément dans le flux (unité de lecture).
        index_paragraphe: rang du paragraphe (``document.paragraphs``), si pertinent.
        index_tableau: rang du tableau (``document.tables``), si pertinent.
        sous_index: ligne de tableau ciblée (1-indexée) — une seule ligne lue.
        max_lignes: plafond de lignes d'un tableau.
        ligne_debut: première ligne d'une **fenêtre** de tableau (1-indexée) :
            c'est ce qui permet de lire la suite d'un grand tableau en un appel
            (`ligne_debut = lignes_lues.fin + 1`), au lieu d'un appel par ligne.
        relire_type: type d'élément utilisé par l'appelant (``tableau``,
            ``element``…) — recopié dans ``relire_suite`` pour que la suite se
            demande avec le **même** repère.
        relire_index: index utilisé par l'appelant (rang de tableau ou position
            de flux) — recopié dans ``relire_suite``.
    """
    if not _est_tableau(bloc):
        texte = (getattr(bloc, "text", "") or "").strip()
        return {
            "element": index,
            "type": "paragraphe",
            "index_paragraphe": index_paragraphe,
            "style": getattr(getattr(bloc, "style", None), "name", None),
            "texte": texte,
        }

    lignes = list(bloc.rows)
    total_lignes = len(lignes)
    if total_lignes == 0:
        debut, fin = 1, 0
    elif sous_index is not None:
        debut = _index_dans_total(sous_index, total_lignes, "ligne")
        fin = debut
    else:
        debut = 1 if ligne_debut is None else _index_dans_total(ligne_debut, total_lignes, "ligne")
        fin = min(total_lignes, debut + max_lignes - 1)
    rendues = [
        {"ligne": numero, "texte": _texte_rang(rang)}
        for numero, rang in enumerate(lignes, start=1)
        if debut <= numero <= fin
    ]
    entete = cellules_rang(lignes[0]) if lignes else []
    prochaine = fin + 1 if total_lignes > 0 and fin < total_lignes else None
    suite = (
        {
            "type_element": relire_type,
            "index": relire_index if relire_index is not None else index,
            "ligne_debut": prochaine,
            "max_lignes": max_lignes,
        }
        if prochaine is not None and relire_type is not None
        else None
    )
    return {
        "element": index,
        "type": "ligne_tableau" if sous_index is not None else "tableau",
        "index_tableau": index_tableau,
        "tableau": index_tableau,
        "ligne_tableau": sous_index,
        "nb_lignes": total_lignes,
        "nb_colonnes": len(bloc.columns),
        "entete": entete,
        "lignes_lues": {"debut": debut, "fin": fin},
        "tronque": fin < total_lignes,
        "ligne_debut": debut,
        "prochaine_ligne": prochaine,
        # Prêt à recopier : la suite du tableau en **un** appel, pas une
        # lecture par ligne (cause réelle des boucles de l'agent sur un DOCX).
        "relire_suite": suite,
        "lignes": rendues,
        "texte": "\n".join(ligne["texte"] for ligne in rendues),
    }


def _compte_paragraphes(elements: list[Any]) -> int:
    return sum(1 for element in elements if not _est_tableau(element))


def _compte_tableaux(elements: list[Any]) -> int:
    return sum(1 for element in elements if _est_tableau(element))


def lire_docx_elements(
    chemin: Path,
    *,
    from_element: int | None = None,
    to_element: int | None = None,
    max_lignes: int = LIGNES_TABLEAU_DEFAUT,
) -> dict[str, Any]:
    """Lit une plage d'**éléments** d'un DOCX (1-indexée, inclusives).

    L'unité est ``elements`` : paragraphes **et** tableaux, dans l'ordre du
    document. Le retour porte la plage exacte (``debut``/``fin``/``total``), la
    liste ``elements`` (type, texte borné) et ``texte`` — la version rendue, celle
    que le modèle lit.

    Raises:
        ValidationError: DOCX illisible ou plage inversée.
    """
    docx = require_library("docx")
    try:
        document = docx.Document(str(chemin))
    except Exception as exc:
        raise ValidationError("DOCX illisible") from exc

    elements = elements_docx(document)
    total = len(elements)
    if total <= 0:
        return {
            "format": "docx",
            "unite": "elements",
            "debut": 1,
            "fin": 0,
            "total": 0,
            "nb_elements": 0,
            "nb_paragraphes": 0,
            "nb_tableaux": 0,
            "elements": [],
            "texte": "",
        }
    debut = 1 if from_element is None else max(1, int(from_element))
    fin = total if to_element is None else min(total, int(to_element))
    if debut > fin:
        raise ValidationError(
            "Plage invalide",
            details={"from": debut, "to": fin, "total": total, "unite": "elements"},
        )

    numeros_paragraphes = _rangs_paragraphes(elements)
    numeros_tableaux = _rangs_tableaux(elements)
    lus: list[dict[str, Any]] = []
    for position in range(debut, fin + 1):
        lus.append(
            decrire_element_docx(
                elements[position - 1],
                position,
                index_paragraphe=numeros_paragraphes.get(position),
                index_tableau=numeros_tableaux.get(position),
                max_lignes=max_lignes,
            )
        )
    return {
        "format": "docx",
        "unite": "elements",
        "debut": debut,
        "fin": fin,
        "total": total,
        "nb_elements": total,
        "nb_paragraphes": _compte_paragraphes(elements),
        "nb_tableaux": _compte_tableaux(elements),
        "elements": lus,
        "texte": "\n".join(element["texte"] for element in lus if element["texte"]),
    }


def _rangs_paragraphes(elements: list[Any]) -> dict[int, int]:
    """Position d'élément → rang de paragraphe (``document.paragraphs``)."""
    rangs: dict[int, int] = {}
    rang = 0
    for position, element in enumerate(elements, start=1):
        if _est_tableau(element):
            continue
        rang += 1
        rangs[position] = rang
    return rangs


def _rangs_tableaux(elements: list[Any]) -> dict[int, int]:
    """Position d'élément → rang de tableau (``document.tables``)."""
    rangs: dict[int, int] = {}
    rang = 0
    for position, element in enumerate(elements, start=1):
        if not _est_tableau(element):
            continue
        rang += 1
        rangs[position] = rang
    return rangs


def _texte_conteneur(conteneur: Any) -> str:
    """Texte d'un en-tête/pied : paragraphes **et** tableaux, dans l'ordre."""
    lignes: list[str] = []
    paragraphes = list(getattr(conteneur, "paragraphs", []) or [])
    tableaux = list(getattr(conteneur, "tables", []) or [])
    if not tableaux:
        lignes.extend(p.text for p in paragraphes if (p.text or "").strip())
        return "\n".join(lignes)
    for bloc in conteneur.iter_inner_content():
        if _est_tableau(bloc):
            lignes.extend(_texte_rang(rang) for rang in bloc.rows if _texte_rang(rang))
            continue
        texte = (getattr(bloc, "text", "") or "").strip()
        if texte:
            lignes.append(texte)
    return "\n".join(lignes)


def zones_docx(document: Any) -> list[dict[str, Any]]:
    """En-têtes et pieds de page par section (hors flux : ``element`` est ``None``).

    Un DOCX d'appel porte souvent la référence de l'appel ou le nom
    de l'organisation émettrice en en-tête : c'est un élément documentaire comme
    un autre, donc cherchable et relisible.
    """
    zones: list[dict[str, Any]] = []
    for numero, section in enumerate(document.sections, start=1):
        for type_bloc, conteneur, libelle in (
            ("entete", section.header, "En-tête"),
            ("pied", section.footer, "Pied de page"),
        ):
            texte = _texte_conteneur(conteneur)
            if texte.strip():
                zones.append(
                    {
                        "type_bloc": type_bloc,
                        "section": numero,
                        "libelle": f"{libelle} — section {numero}",
                        "texte": texte,
                    }
                )
    return zones


def lire_element_docx(
    chemin: Path,
    *,
    type_element: str,
    index: int | None = None,
    sous_index: int | None = None,
    max_lignes: Any = None,
    ligne_debut: Any = None,
) -> dict[str, Any]:
    """Lit **un** élément d'un DOCX : flux, paragraphe, tableau, en-tête, pied.

    ``ligne_debut`` ouvre une **fenêtre** dans un tableau : le tableau est lu
    de cette ligne à ``ligne_debut + max_lignes - 1``. Sans lui, la lecture
    repart de la ligne 1 — c'était la seule façon de lire un grand tableau, et
    l'agent multipliait les appels de une ligne (boucle constatée).

    Raises:
        ValidationError: DOCX illisible, type inconnu, index hors document.
    """
    type_propre = _type_valide("docx", type_element)
    plafond_lignes = _plafond(max_lignes, LIGNES_TABLEAU_DEFAUT, MAX_LIGNES_TABLEAU, "max_lignes")
    debut_lignes = None if ligne_debut in (None, "") else _index_entier(ligne_debut, "ligne_debut")
    docx = require_library("docx")
    try:
        document = docx.Document(str(chemin))
    except Exception as exc:
        raise ValidationError("DOCX illisible") from exc

    if type_propre in ("entete", "pied"):
        sections = list(document.sections)
        emplacement = _index_dans_total(
            _index_entier(index), len(sections), "section"
        )
        conteneur = sections[emplacement - 1]
        texte = _texte_conteneur(conteneur.header if type_propre == "entete" else conteneur.footer)
        return {
            "format": "docx",
            "type_element": type_propre,
            "index": emplacement,
            "unite": "sections",
            "total": len(sections),
            "texte": texte,
        }

    elements = elements_docx(document)
    total = len(elements)
    if type_propre == "element":
        position = _index_dans_total(_index_entier(index), total, "élément")
        return {
            "format": "docx",
            "type_element": "element",
            "index": position,
            "unite": "elements",
            "total": total,
            "nb_paragraphes": _compte_paragraphes(elements),
            "nb_tableaux": _compte_tableaux(elements),
            **decrire_element_docx(
                elements[position - 1],
                position,
                index_paragraphe=_rangs_paragraphes(elements).get(position),
                index_tableau=_rangs_tableaux(elements).get(position),
                sous_index=sous_index if _est_tableau(elements[position - 1]) else None,
                max_lignes=plafond_lignes,
                ligne_debut=debut_lignes,
                relire_type="element",
                relire_index=position,
            ),
        }

    if type_propre == "tableau":
        tableaux = [element for element in elements if _est_tableau(element)]
        rang = _index_dans_total(_index_entier(index), len(tableaux), "tableau")
        position = next(
            index_element
            for index_element, element in enumerate(elements, start=1)
            if element is tableaux[rang - 1]
        )
        return {
            "format": "docx",
            "type_element": "tableau",
            "index": rang,
            "unite": "tableaux",
            "total": len(tableaux),
            **decrire_element_docx(
                tableaux[rang - 1],
                position,
                index_tableau=rang,
                sous_index=sous_index,
                max_lignes=plafond_lignes,
                ligne_debut=debut_lignes,
                relire_type="tableau",
                relire_index=rang,
            ),
        }

    paragraphes = [element for element in elements if not _est_tableau(element)]
    rang = _index_dans_total(_index_entier(index), len(paragraphes), "paragraphe")
    return {
        "format": "docx",
        "type_element": "paragraphe",
        "index": rang,
        "unite": "paragraphes",
        "total": len(paragraphes),
        **decrire_element_docx(
            paragraphes[rang - 1],
            rang,
            index_paragraphe=rang,
            max_lignes=plafond_lignes,
        ),
    }


# --- PDF ----------------------------------------------------------------------


def lire_element_pdf(
    chemin: Path, *, type_element: str, index: int | None = None, max_caracteres: Any = None
) -> dict[str, Any]:
    """Lit une page d'un PDF (``element`` et ``page`` sont le même repère).

    Raises:
        ValidationError: PDF illisible/chiffré, type inconnu, page hors document.
    """
    _type_valide("pdf", type_element)
    plafond = _plafond(
        max_caracteres, CARACTERES_ELEMENT_DEFAUT, MAX_CARACTERES_ELEMENT, "max_caracteres"
    )
    pypdf = require_library("pypdf")
    try:
        lecteur = pypdf.PdfReader(str(chemin))
    except Exception as exc:
        raise ValidationError("PDF illisible") from exc
    if lecteur.is_encrypted:
        raise ValidationError("PDF chiffré : lecture impossible")

    total = len(lecteur.pages)
    numero = _index_dans_total(_index_entier(index), total, "page")
    try:
        texte = str(lecteur.pages[numero - 1].extract_text() or "")
    except Exception:
        texte = ""
    texte, tronque = _tronquer_texte(texte.strip(), plafond)
    return {
        "format": "pdf",
        "type_element": "page",
        "index": numero,
        "unite": "pages",
        "total": total,
        "tronque": tronque,
        "texte": texte,
        "note": (
            "Aucune couche texte sur cette page : le PDF est peut-être un scan "
            "(OCR hors périmètre)."
            if not texte
            else None
        ),
    }


# --- XLSX ---------------------------------------------------------------------


def lire_element_xlsx(
    chemin: Path,
    *,
    type_element: str,
    index: int | None = None,
    feuille: str | None = None,
    max_lignes: Any = None,
) -> dict[str, Any]:
    """Lit une **ligne** ou décrit une **feuille** d'un classeur.

    ``element`` et ``ligne`` désignent le même repère ; ``feuille`` s'écrit avec
    son nom (jamais un index : les feuilles se nomment).

    Raises:
        ValidationError: classeur macro/illisible, feuille ou ligne inconnue.
    """
    type_propre = _type_valide("xlsx", type_element)
    if chemin.suffix.lower() == ".xlsm":
        raise ValidationError("Classeur macro (.xlsm) refusé")
    plafond = _plafond(max_lignes, LIGNES_TABLEAU_DEFAUT, MAX_LIGNES_TABLEAU, "max_lignes")
    openpyxl = require_library("openpyxl")
    classeur = openpyxl.load_workbook(str(chemin), read_only=True, data_only=True)
    try:
        noms = list(classeur.sheetnames)
        if feuille:
            if feuille not in noms:
                raise ValidationError(f"Feuille inconnue : {feuille!r}", details={"feuilles": noms})
            feuillet = classeur[feuille]
        else:
            # Sans nom de feuille, la première est lue — et la réponse dit
            # lesquelles existent, pour que l'appelant n'ait pas à deviner.
            feuillet = classeur.worksheets[0]

        if type_propre == "feuille":
            entete = [
                "" if cellule.value is None else str(cellule.value)
                for cellule in next(
                    feuillet.iter_rows(min_row=1, max_row=1, values_only=False), []
                )
            ]
            apercu: list[list[str]] = []
            for numero, ligne in enumerate(
                feuillet.iter_rows(values_only=True), start=1
            ):
                if numero > plafond:
                    break
                apercu.append(["" if valeur is None else str(valeur) for valeur in ligne])
            return {
                "format": "xlsx",
                "type_element": "feuille",
                "feuille": feuillet.title,
                "feuilles": noms,
                "unite": "lignes",
                "total": int(feuillet.max_row or 0),
                "nb_colonnes": int(feuillet.max_column or 0),
                "entete": entete,
                "tronque": int(feuillet.max_row or 0) > plafond,
                "lignes": apercu,
                "texte": "\n".join(" | ".join(ligne) for ligne in apercu),
            }

        total = int(feuillet.max_row or 0)
        numero = _index_dans_total(_index_entier(index), total, "ligne")
        ligne = next(
            (
                valeurs
                for position, valeurs in enumerate(
                    feuillet.iter_rows(values_only=True), start=1
                )
                if position == numero
            ),
            None,
        )
        cellules = ["" if valeur is None else str(valeur) for valeur in (ligne or ())]
        return {
            "format": "xlsx",
            "type_element": "ligne",
            "feuille": feuillet.title,
            "feuilles": noms,
            "index": numero,
            "unite": "lignes",
            "total": total,
            "cellules": cellules,
            "texte": " | ".join(cellules),
        }
    finally:
        classeur.close()


# --- point d'entrée commun ----------------------------------------------------


def lire_element(
    chemin: Path,
    *,
    type_element: Any,
    index: Any = None,
    sous_index: Any = None,
    feuille: Any = None,
    max_lignes: Any = None,
    max_caracteres: Any = None,
    ligne_debut: Any = None,
) -> dict[str, Any]:
    """Lit un élément d'un document, quel que soit son format (docx, pdf, xlsx).

    ``type_element='element'`` est le repère **commun** : index de flux DOCX,
    page PDF, ligne XLSX — celui que rend ``chercher_dans_document``.

    Raises:
        ValidationError: format sans lecture d'élément, type ou index invalide,
            ou ``ligne_debut`` demandé sur un format sans tableau.
    """
    suffixe = chemin.suffix.lower()
    if suffixe == ".docx":
        return lire_element_docx(
            chemin,
            type_element=type_element,
            index=index,
            sous_index=sous_index,
            max_lignes=max_lignes,
            ligne_debut=ligne_debut,
        )
    if ligne_debut not in (None, ""):
        raise ValidationError(
            "ligne_debut s'applique aux tableaux d'un DOCX : ce document n'en a pas "
            "(utiliser max_caracteres pour une page PDF, max_lignes pour une feuille XLSX)",
            details={"format": suffixe.lstrip(".")},
        )
    if suffixe == ".pdf":
        return lire_element_pdf(
            chemin,
            type_element=type_element,
            index=index,
            max_caracteres=max_caracteres,
        )
    if suffixe == ".xlsx":
        return lire_element_xlsx(
            chemin,
            type_element=type_element,
            index=index,
            feuille=feuille,
            max_lignes=max_lignes,
        )
    raise ValidationError(
        f"Lecture d'élément non supportée pour {suffixe or 'ce fichier'} "
        "(formats : docx, pdf, xlsx)",
        details={"extension": suffixe},
    )
