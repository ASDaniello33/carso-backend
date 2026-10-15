"""Recherche ciblée dans un document (incréments 52 → 53).

``chercher_dans_document`` remplace le « grep » (shell / lectures bornées en
boucle)—l'agent localise UN contenu précis et reçoit des **éléments + extraits
bornés** (``[{page, content, relire, ...}]``) au lieu de relire le document en
entier—cause des sorties interminables et des boucles de lecture observées.

Choix assumés :

- recherche **littérale** insensible à la casse (``re`` de la bibliothèque
  standard, motif échappé—pas d'expression régulière à deviner côté modèle) ;
- ``page`` est le repère de l'unité de lecture du format—DOCX—index
  d'**élément** de flux (paragraphe **ou tableau**, 1-indexé), PDF—n° de page
  réel, XLSX—n° de ligne. Un hit se relit donc **toujours** à l'identique
  (incrément 53—lire par paragraphes seulement rendait un hit de tableau
  irrelisible, ce qui faisait reboucler l'agent) ;
- chaque hit porte ``relire``—les arguments **exacts** de
  ``lire_element_document`` pour relire ce hit—l'agent n'a plus à traduire
  lui-même « page » en appel d'outil ;
- les en-têtes et pieds d'un DOCX sont cherchés aussi (mention légale, référence
  de l'appel)—ils se relisent par section ;
- contexte court et plafond de résultats—le retour reste borné quel que soit
  la taille du document.

Le tool reste à la frontière—voir ``app.tools.documents`` (permission
``document_read``, service, aucune manipulation de chemin disque).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from app.core.errors import ValidationError
from app.documents.extraction.base import require_library

__all__ = [
    "chercher_dans_docx",
    "chercher_dans_pdf",
    "chercher_dans_xlsx",
    "CONTEXTE_RECHERCHE",
    "LIMITE_RESULTATS_RECHERCHE",
    "MAX_OCCURRENCES_BLOC",
    "MAX_RESULTATS_RECHERCHE",
    "MOT_CLE_MIN",
]

#: Résultats renvoyés par défaut (bornes du contexte).
MAX_RESULTATS_RECHERCHE = 50
#: Plafond absolu de ``max_resultats`` accepté côté serveur.
LIMITE_RESULTATS_RECHERCHE = 200
#: Caractères conservés de chaque côté d'une correspondance.
CONTEXTE_RECHERCHE = 160
#: Longueur minimale d'un mot-clé (une lettre noierait le résultat).
MOT_CLE_MIN = 2
#: Correspondances comptées au plus dans un même bloc.
MAX_OCCURRENCES_BLOC = 1000

#: Outil de relecture d'un hit (nom du tool exposé aux agents).
OUTIL_RELECTURE = "lire_element_document"


def _motif_recherche(mot_cle: Any) -> tuple[str, re.Pattern[str]]:
    """Mot-clé propre + motif littéral insensible à la casse (regex échappée)."""
    propre = str(mot_cle or "").strip()
    if len(propre) < MOT_CLE_MIN:
        raise ValidationError(
            f"Mot-clé trop court : fournir au moins {MOT_CLE_MIN} caractères",
            details={"mot_cle": propre, "min": MOT_CLE_MIN},
        )
    return propre, re.compile(re.escape(propre), re.IGNORECASE)


def _limite_resultats(max_resultats: Any) -> int:
    """Plafond de résultats, borné côté serveur (``None`` → valeur par défaut)."""
    if max_resultats is None or max_resultats == "":
        return MAX_RESULTATS_RECHERCHE
    try:
        demande = int(max_resultats)
    except (TypeError, ValueError) as exc:
        raise ValidationError("max_resultats doit être un entier") from exc
    return max(1, min(demande, LIMITE_RESULTATS_RECHERCHE))


def _occurrences(texte: str, motif: re.Pattern[str]) -> tuple[int, int]:
    """``(nombre de matches, position du premier)`` ; ``(0, -1)`` si aucun."""
    if not texte:
        return 0, -1
    total = 0
    premiere = -1
    for correspondance in motif.finditer(texte):
        if premiere < 0:
            premiere = correspondance.start()
        total += 1
        if total >= MAX_OCCURRENCES_BLOC:
            break
    return total, premiere


def _extrait_contexte(texte: str, position: int, longueur: int) -> str:
    """Extrait **borné** autour du premier match (le bloc entier n'est jamais renvoyé)."""
    debut = max(0, position - CONTEXTE_RECHERCHE)
    fin = min(len(texte), position + max(longueur, 1) + CONTEXTE_RECHERCHE)
    extrait = " ".join(texte[debut:fin].split())
    prefixe = "…" if debut > 0 else ""
    suffixe = "…" if fin < len(texte) else ""
    return f"{prefixe}{extrait}{suffixe}"


def _relire(arguments: dict[str, Any]) -> dict[str, Any]:
    """Locator de relecture d'un hit : le **nom de l'outil + ses arguments**.

    ``document_id`` est ajouté par le tool (voir ``app.tools.documents``) : la
    recherche ne connaît que le fichier, pas l'identité du document en base.
    """
    return {"tool": OUTIL_RELECTURE, "arguments": arguments}


def _reponse_recherche(
    format_doc: str,
    mot_cle: str,
    unite: str,
    total: int,
    resultats: list[dict[str, Any]],
    correspondances: int,
    tronque: bool,
    *,
    complements: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Contrat de retour commun aux trois formats (liste ``[{…, content, page}]``).

    ``complements`` porte les comptages propres à un format (paragraphes,
    tableaux, sections d'un DOCX) — l'agent sait alors ce que le document
    contient, sans un second appel de structure.
    """
    reponse: dict[str, Any] = {
        "format": format_doc,
        "mot_cle": mot_cle,
        "unite": unite,
        "total": total,
        "nb_resultats": len(resultats),
        "nb_correspondances": correspondances,
        "tronque": tronque,
        "resultats": resultats,
        "message": (
            f"Chaque entrée porte 'page' (repère de l'unité '{unite}'), 'content' "
            "(extrait borné, jamais le bloc entier) et 'relire' : les arguments "
            "exacts de lire_element_document pour relire ce hit à l'identique — "
            "aucune autre lecture n'est nécessaire. Aucun résultat ou "
            "'tronque' = true : affiner le mot-clé plutôt que de relire le "
            "document en entier."
        ),
    }
    if complements:
        reponse.update(complements)
    return reponse


def chercher_dans_docx(chemin: Path, mot_cle: Any, *, max_resultats: Any = None) -> dict[str, Any]:
    """Recherche un mot-clé dans un DOCX : éléments du flux **et** en-têtes/pieds.

    Un DOCX n'a pas de pages : l'unité est l'**élément** du flux
    (``iter_inner_content`` : paragraphe ou tableau). ``page`` = index d'élément,
    donc ``read_document_range(from_page=page, to_page=page)`` et
    ``lire_element_document(type_element='element', index=page)`` relisent le hit.
    Pour un hit dans un tableau, ``relire`` cible directement
    ``type_element='tableau'`` (+ ``sous_index`` = ligne).

    Raises:
        ValidationError: DOCX illisible ou mot-clé trop court.
    """
    from app.documents.elements import cellules_rang, elements_docx, zones_docx

    mot_propre, motif = _motif_recherche(mot_cle)
    limite = _limite_resultats(max_resultats)
    docx = require_library("docx")
    try:
        document = docx.Document(str(chemin))
    except Exception as exc:
        raise ValidationError("DOCX illisible") from exc

    elements = elements_docx(document)
    total = len(elements)
    nb_tableaux_total = sum(1 for bloc in elements if hasattr(bloc, "rows"))
    resultats: list[dict[str, Any]] = []
    correspondances = 0
    tronque = False
    index_tableau = 0
    index_paragraphe = 0

    for position, bloc in enumerate(elements, start=1):
        if hasattr(bloc, "rows"):  # Tableau : une entrée par ligne fautive.
            index_tableau += 1
            for numero, rang in enumerate(bloc.rows, start=1):
                texte = " | ".join(cellules_rang(rang))
                if not texte:
                    continue
                nb, depart = _occurrences(texte, motif)
                if not nb:
                    continue
                correspondances += nb
                resultats.append(
                    {
                        "page": position,
                        "unite": "elements",
                        "type_bloc": "tableau",
                        "element": position,
                        "tableau": index_tableau,
                        "ligne_tableau": numero,
                        "occurrences": nb,
                        "content": _extrait_contexte(texte, depart, len(mot_propre)),
                        "relire": _relire(
                            {
                                "type_element": "tableau",
                                "index": index_tableau,
                                "sous_index": numero,
                            }
                        ),
                    }
                )
                if len(resultats) >= limite:
                    tronque = True
                    break
            if tronque:
                break
            continue

        index_paragraphe += 1
        texte = (getattr(bloc, "text", "") or "").strip()
        nb, depart = _occurrences(texte, motif)
        if not nb:
            continue
        correspondances += nb
        resultats.append(
            {
                "page": position,
                "unite": "elements",
                "type_bloc": "paragraphe",
                "element": position,
                "index_paragraphe": index_paragraphe,
                "occurrences": nb,
                "content": _extrait_contexte(texte, depart, len(mot_propre)),
                "relire": _relire({"type_element": "element", "index": position}),
            }
        )
        if len(resultats) >= limite:
            tronque = True
            break

    if not tronque:
        # En-têtes et pieds : hors flux (aucun « element »), relus par section.
        for zone in zones_docx(document):
            nb, depart = _occurrences(zone["texte"], motif)
            if not nb:
                continue
            correspondances += nb
            resultats.append(
                {
                    "page": None,
                    "unite": "elements",
                    "type_bloc": zone["type_bloc"],
                    "section": zone["section"],
                    "libelle": zone["libelle"],
                    "occurrences": nb,
                    "content": _extrait_contexte(
                        zone["texte"].replace("\n", " "), depart, len(mot_propre)
                    ),
                    "relire": _relire(
                        {"type_element": zone["type_bloc"], "index": zone["section"]}
                    ),
                }
            )
            if len(resultats) >= limite:
                tronque = True
                break

    return _reponse_recherche(
        "docx",
        mot_propre,
        "elements",
        total,
        resultats,
        correspondances,
        tronque,
        complements={
            "nb_elements": total,
            "nb_paragraphes": total - nb_tableaux_total,
            "nb_tableaux": nb_tableaux_total,
            "nb_sections": len(document.sections),
        },
    )


def chercher_dans_pdf(chemin: Path, mot_cle: Any, *, max_resultats: Any = None) -> dict[str, Any]:
    """Recherche un mot-clé dans un PDF, page par page (``page`` = numéro réel).

    Raises:
        ValidationError: PDF illisible ou chiffré, mot-clé trop court.
    """
    mot_propre, motif = _motif_recherche(mot_cle)
    limite = _limite_resultats(max_resultats)
    pypdf = require_library("pypdf")
    try:
        lecteur = pypdf.PdfReader(str(chemin))
    except Exception as exc:
        raise ValidationError("PDF illisible") from exc
    if lecteur.is_encrypted:
        raise ValidationError("PDF chiffré : recherche impossible")

    resultats: list[dict[str, Any]] = []
    correspondances = 0
    tronque = False
    total = len(lecteur.pages)
    for numero, page in enumerate(lecteur.pages, start=1):
        if len(resultats) >= limite:
            tronque = True
            break
        try:
            texte = str(page.extract_text() or "")
        except Exception:
            texte = ""
        nb, position = _occurrences(texte, motif)
        if not nb:
            continue
        correspondances += nb
        resultats.append(
            {
                "page": numero,
                "unite": "pages",
                "type_bloc": "page",
                "nb_caracteres": len(texte),
                "occurrences": nb,
                "content": _extrait_contexte(texte, position, len(mot_propre)),
                "relire": _relire({"type_element": "page", "index": numero}),
            }
        )
    return _reponse_recherche(
        "pdf", mot_propre, "pages", total, resultats, correspondances, tronque
    )


def chercher_dans_xlsx(
    chemin: Path,
    mot_cle: Any,
    *,
    feuille: str | None = None,
    max_resultats: Any = None,
) -> dict[str, Any]:
    """Recherche un mot-clé dans un classeur (``page`` = numéro de ligne).

    Toutes les feuilles sont parcourues sauf si ``feuille`` cible l'une
    d'elles. Les valeurs sont celles **mises en cache**, cohérentes avec
    ``lire_xlsx_plage``.

    Raises:
        ValidationError: classeur macro/illisible, feuille inconnue, mot-clé trop court.
    """
    if chemin.suffix.lower() == ".xlsm":
        raise ValidationError("Classeur macro (.xlsm) refusé")
    mot_propre, motif = _motif_recherche(mot_cle)
    limite = _limite_resultats(max_resultats)
    openpyxl = require_library("openpyxl")
    classeur = openpyxl.load_workbook(str(chemin), read_only=True, data_only=True)
    resultats: list[dict[str, Any]] = []
    correspondances = 0
    tronque = False
    total = 0
    try:
        if feuille:
            noms = list(classeur.sheetnames)
            if feuille not in noms:
                raise ValidationError(
                    f"Feuille inconnue : {feuille!r}",
                    details={"feuilles": noms},
                )
            cibles = [classeur[feuille]]
        else:
            cibles = list(classeur.worksheets)
        total = sum(int(feuil.max_row or 0) for feuil in cibles)
        for feuil in cibles:
            for numero, ligne in enumerate(feuil.iter_rows(values_only=True), start=1):
                if len(resultats) >= limite:
                    tronque = True
                    break
                cellules = [
                    str(valeur)
                    for valeur in ligne
                    if valeur is not None and str(valeur).strip()
                ]
                if not cellules:
                    continue
                texte = " | ".join(cellules)
                nb, position = _occurrences(texte, motif)
                if not nb:
                    continue
                correspondances += nb
                resultats.append(
                    {
                        "page": numero,
                        "unite": "lignes",
                        "type_bloc": "ligne",
                        "feuille": feuil.title,
                        "occurrences": nb,
                        "content": _extrait_contexte(texte, position, len(mot_propre)),
                        "relire": _relire(
                            {
                                "type_element": "ligne",
                                "feuille": feuil.title,
                                "index": numero,
                            }
                        ),
                    }
                )
            if tronque:
                break
    finally:
        classeur.close()
    return _reponse_recherche(
        "xlsx",
        mot_propre,
        "lignes",
        total,
        resultats,
        correspondances,
        tronque,
        complements={"feuilles": [feuil.title for feuil in cibles]},
    )
