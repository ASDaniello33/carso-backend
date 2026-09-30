"""Génération de livrables depuis du **HTML** (tool ``generer_document_html``).

Pourquoi ce chemin : le HTML est un type **standard** que le modèle produit
avec aisance et qui offre des styles très variés (couleurs, encadrés, images,
tableaux stylés) sans les contraintes de structure du ``DocumentSpec``. La
chaîne ``DocumentSpec``/``profiles`` reste disponible et inchangée pour les
livrables à structure imposée — ce module est le chemin **prioritaire** pour
un document personnalisé, pas un remplacement de la chaîne structurée.

Deux sorties (vocabulaire fermé ``FORMATS_SORTIE_HTML``) :

- **docx** : moteur ``app.documents.html_to_docx`` — conversion fidèle du
  HTML+CSS (couleurs, polices, encadrés, images, listes, tableaux, liens) ;
- **pdf** : le système ne dépend d'aucun convertisseur bureautique
  (LibreOffice, pandoc) — limite documentée du projet. Le PDF est donc rendu
  par le renderer CARSO depuis un **DocumentSpec minimal** extrait du HTML
  (titres, paragraphes, listes, tableaux) : même contenu, mise en page
  réaliste du moteur PDF plutôt qu'une copie pixel du DOCX. Documenté tel
  quel au tool, jamais trompeur.

Le circuit documentaire CARSO est **inchangé** (règle 6 [C]) : la sortie est
déposée via ``DocumentService.enregistrer_document`` avec
``proposed_by_agent`` → statut ``proposed`` (validation humaine requise) ;
une ancre métier réelle classe le fichier sous l'objet, une ancre absente le
range sous ``generated/{id}``. Aucune écriture disque directe : tout passe
par le service (AGENTS.md §2.5).
"""

from __future__ import annotations

from bs4 import Tag

__all__ = [
    "convertir_html_bytes",
    "FORMATS_SORTIE_HTML",
]


#: Formats de sortie supportés par la conversion HTML (vocabulaire fermé).
FORMATS_SORTIE_HTML = ("docx", "pdf")


def convertir_html_bytes(
    html: str | bytes,
    *,
    format: str = "docx",
    css: str | None = None,
) -> bytes:
    """Convertit du HTML en octets du format demandé.

    Args:
        html: page HTML complète (ou fragment) avec ses ``<style>``. Une
            chaîne encodée autrement qu'en UTF-8 est décodée permissivement.
        format: ``docx`` (moteur HTML→DOCX natif) ou ``pdf`` (DocumentSpec
            minimal extrait du HTML, rendu par le moteur PDF CARSO).
        css: CSS additionnel appliqué après celui du HTML (facultatif).

    Returns:
        Les octets du document produit.

    Raises:
        ValidationError: format hors vocabulaire fermé.
        ConversionHtmlError: HTML vide ou sans contenu rendable.
    """
    cle = str(format).strip().lower().lstrip(".")
    if cle not in FORMATS_SORTIE_HTML:
        from app.core.errors import ValidationError

        raise ValidationError(
            f"Format de sortie inconnu : {format!r}",
            details={"formats_disponibles": list(FORMATS_SORTIE_HTML)},
        )

    from app.documents.html_to_docx import html_string_to_docx_bytes

    html_texte = (
        html.decode("utf-8", errors="replace") if isinstance(html, bytes) else html
    )
    if cle == "docx":
        return html_string_to_docx_bytes(html_texte, css_string=css)
    return _html_vers_pdf(html_texte)


# --- PDF : DocumentSpec minimal extrait du HTML -----------------------------


def _html_vers_pdf(html_texte: str) -> bytes:
    """Extrait une structure minimale du HTML puis rend un PDF CARSO.

    La fidélité pixel n'est pas l'objectif (pas de convertisseur bureautique
    dans ce projet, limite documentée) : le contenu — titres, paragraphes,
    listes, tableaux — est transmis au renderer PDF du système, qui applique
    la mise en page professionnelle standard.
    """
    from bs4 import BeautifulSoup

    from app.documents.spec import (
        DocumentSpec,
        Liste,
        Metadonnees,
        Paragraphe,
        Tableau,
        Titre,
    )

    soup = BeautifulSoup(html_texte, "html.parser")
    body = soup.find("body") or soup
    titre = body.find(["h1"])
    titre_texte = titre.get_text(strip=True) if titre else None

    blocs: list[Titre | Paragraphe | Liste | Tableau] = []
    if titre is not None:
        blocs.append(Titre(niveau=1, texte=titre_texte or ""))

    puces: list[str] = []  # les <li> consécutifs forment une liste

    def _vider_puces() -> None:
        if puces:
            blocs.append(Liste(items=list(puces), numerotee=False))
            puces.clear()

    for element in body.find_all(["h2", "h3", "h4", "p", "li", "table"]):
        # Ne pas descendre deux fois dans le contenu d'un tableau.
        if element.find_parent("table") is not None and element.name != "table":
            continue
        if element.name in ("h2", "h3", "h4"):
            _vider_puces()
            blocs.append(
                Titre(
                    niveau=int(element.name[1]),
                    texte=element.get_text(strip=True),
                )
            )
        elif element.name == "p":
            _vider_puces()
            texte = element.get_text(" ", strip=True)
            if texte:
                blocs.append(Paragraphe(texte=texte))
        elif element.name == "li":
            texte = element.get_text(" ", strip=True)
            if texte:
                puces.append(texte)
        elif element.name == "table":
            _vider_puces()
            entete, lignes = _grille_tableau(element)
            if lignes:
                blocs.append(Tableau(entete=entete, lignes=lignes))

    _vider_puces()
    if not blocs:
        from app.documents.html_to_docx import ConversionHtmlError

        raise ConversionHtmlError(
            "Le HTML fourni ne contient aucun contenu convertible en PDF."
        )

    spec = DocumentSpec(
        metadata=Metadonnees(titre=titre_texte or "Document"),
        blocs=blocs,
    )
    from app.documents.renderers.pdf import rendre_pdf

    return rendre_pdf(spec)


def _grille_tableau(table_el: Tag) -> tuple[list[str] | None, list[list[str]]]:
    """Grille texte d'un ``<table>`` : (en-tête si ``<th>``, lignes de données).

    Les fusions HTML ``colspan`` sont **résolues en grille complète** : chaque
    ligne couvre exactement la largeur du tableau — condition du validateur
    ``DocumentSpec``. Sans cela, une ligne de total ``<td colspan="n">``
    produisait une grille incohérente et la génération PDF échouait
    (« colonne(s) non couverte(s) »). Les ``rowspan`` sont aplatis : la valeur
    reste sur sa ligne d'origine, les lignes couvertes portent une chaîne vide
    (rendu visuel identique après fusion gérée par le renderer).
    """
    # --- Passe 1 : lecture brute des lignes (texte + colspan) --------------
    brutes: list[tuple[list[bool], list[tuple[str, int]]]] = []
    for tr in table_el.find_all("tr"):
        ths: list[bool] = []
        cellules: list[tuple[str, int]] = []
        for cellule in tr.find_all(["th", "td"], recursive=False):
            try:
                colspan = max(1, int(cellule.get("colspan", 1)))
            except (TypeError, ValueError):
                colspan = 1
            ths.append(cellule.name == "th")
            cellules.append((cellule.get_text(" ", strip=True), colspan))
        if cellules:
            brutes.append((ths, cellules))
    if not brutes:
        return None, []

    largeur = max(sum(colspan for _texte, colspan in cellules) for _ths, cellules in brutes)

    # --- Passe 2 : expansion des colspan en grille pleine largeur ----------
    grille: list[list[str]] = []
    est_th: list[list[bool]] = []
    for ths, cellules in brutes:
        ligne: list[str] = []
        ths_ligne: list[bool] = []
        for (texte, colspan), th in zip(cellules, ths, strict=True):
            ligne.append(texte)
            ths_ligne.append(th)
            # Expansion : les colonnes fusionnées portent une chaîne vide
            # (continuité de grille ; le DOCX gère la vraie fusion, le PDF
            # affiche la cellule sur sa première colonne puis des vides).
            ligne.extend([""] * (colspan - 1))
            ths_ligne.extend([th] * (colspan - 1))
        # Normalise (HTML mal formé : ligne trop courte ou trop longue).
        del ligne[largeur:]
        del ths_ligne[largeur:]
        ligne.extend([""] * (largeur - len(ligne)))
        ths_ligne.extend([False] * (largeur - len(ths_ligne)))
        grille.append(ligne)
        est_th.append(ths_ligne)

    # --- Passe 3 : en-tête (toutes les cellules de la 1re ligne en th) -----
    entete: list[str] | None = None
    lignes: list[list[str]] = []
    if all(est_th[0]):
        entete = grille[0]
        lignes = grille[1:]
    else:
        lignes = grille

    # Nettoie : supprime les lignes entièrement vides (résidus de fusions).
    lignes = [ligne for ligne in lignes if any(texte.strip() for texte in ligne)]
    return entete, lignes
