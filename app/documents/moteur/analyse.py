"""DocumentAnalyzer — extraction déterministe de la structure d'un DOCX (INC-128).

Ce que l'analyseur produit est la **vérité** sur laquelle tout le reste s'appuie :
la compréhension LLM reçoit ces blocs, l'instrumentation les cible, le rendu les
retrouve. Trois conséquences :

1. chaque bloc reçoit un ``block_id`` **stable pour cette version du fichier** —
   attribué dans l'ordre du document, jamais déduit du texte ;
2. l'extraction lit **toutes** les parties de contenu (``word/document.xml``,
   ``word/header*.xml``, ``word/footer*.xml``), jamais seulement le corps ;
3. ce qui n'est pas lisible de façon fiable n'est pas deviné : les zones de texte
   flottantes, les modifications suivies et les contrôles déjà présents sont
   **signalés** dans ``avertissements``.

Limite assumée : les valeurs de ``style`` sont celles lues sur le paragraphe et
ses ``rPr`` directs. La résolution complète de l'héritage Word (styles liés,
thème) appartient au rendu de Word, pas à l'analyse — prétendre le contraire
donnerait une fausse précision.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from app.documents.moteur import paquet as paquet_docx
from app.documents.moteur import schemas as S

__all__ = [
    "DocumentAnalyzer",
    "analyser",
    "nom_style",
    "texte_element",
    "w_balise",
]

_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_A = "http://schemas.openxmlformats.org/drawingml/2006/main"
_V = "urn:schemas-microsoft-com:vml"

#: 1440 twips = 1 pouce = 25,4 mm.
_TWIPS_PAR_MM = 1440.0 / 25.4

_PLACEHOLDER = re.compile(r"\{\{\s*([a-zA-Z0-9_.-]+)\s*\}\}")
_STYLE_TITRE = re.compile(r"^(?:heading|titre)\s*(\d)$", re.I)

_VALEURS_FAUSSES = {"0", "false", "off", "none"}


def w_balise(nom: str) -> str:
    """Balise WordprocessingML qualifiée (``"p"`` → ``{…}p``)."""
    return f"{{{_W}}}{nom}"


def _attribut(element: Any, nom: str, *, namespace: str = _W) -> str | None:
    if element is None:
        return None
    valeur = element.get(f"{{{namespace}}}{nom}")
    return valeur.strip() if isinstance(valeur, str) else None


def _booleen(element: Any, nom: str) -> bool | None:
    """Propriété OOXML booléenne : présence sans valeur = vrai."""
    if element is None:
        return None
    cible = element.find(w_balise(nom))
    if cible is None:
        return None
    valeur = _attribut(cible, "val")
    if valeur is None:
        return True
    return valeur.casefold() not in _VALEURS_FAUSSES


def _twips_mm(valeur: str | None) -> float | None:
    if not valeur:
        return None
    try:
        return round(float(valeur.replace(",", ".")) / _TWIPS_PAR_MM, 1)
    except ValueError:
        return None


def _demi_points(valeur: str | None) -> float | None:
    """``w:sz`` s'exprime en demi-points ; ``w:szCs`` n'est pas la taille du texte."""
    if not valeur:
        return None
    try:
        return round(float(valeur) / 2, 1)
    except ValueError:
        return None


def texte_element(element: Any) -> str:
    """Texte visible d'un élément : ``w:t``, tabulations et sauts de ligne."""
    if element is None:
        return ""
    morceaux: list[str] = []
    for noeud in element.iter():
        if noeud.tag == w_balise("t"):
            morceaux.append(noeud.text or "")
        elif noeud.tag == w_balise("tab"):
            morceaux.append("\t")
        elif noeud.tag == w_balise("br") or noeud.tag == w_balise("cr"):
            morceaux.append("\n")
    return "".join(morceaux)


def nom_style(styles: dict[str, str], identifiant: str | None) -> str | None:
    """Nom lisible d'un style (``Heading1`` → ``Heading 1``)."""
    if not identifiant:
        return None
    return styles.get(identifiant, identifiant)


def _lire_styles(paquet: paquet_docx.Paquet) -> dict[str, str]:
    """Table ``styleId`` → nom de style, lue sur ``word/styles.xml``."""
    if "word/styles.xml" not in paquet.parties:
        return {}
    racine = paquet_docx.xml(paquet, "word/styles.xml")
    table: dict[str, str] = {}
    for style in racine.findall(w_balise("style")):
        identifiant = _attribut(style, "styleId")
        nom = _attribut(style.find(w_balise("name")), "val") if style.find(
            w_balise("name")
        ) is not None else None
        if identifiant and nom:
            table[identifiant] = nom
    return table


def _format_run(run: Any) -> S.RunBloc:
    """Mise en forme directe d'un run (aucune extrapolation de style)."""
    rpr = run.find(w_balise("rPr"))
    couleur = _attribut(rpr.find(w_balise("color")), "val") if rpr is not None else None
    if couleur and couleur.casefold() == "auto":
        couleur = None
    return S.RunBloc(
        texte=texte_element(run),
        gras=_booleen(rpr, "b"),
        italique=_booleen(rpr, "i"),
        souligne=_booleen(rpr, "u"),
        taille_pt=_demi_points(_attribut(rpr.find(w_balise("sz")), "val"))
        if rpr is not None
        else None,
        couleur=couleur,
        police=_attribut(rpr.find(w_balise("rFonts")), "ascii") if rpr is not None else None,
    )


def _runs(paragraphe: Any) -> list[S.RunBloc]:
    """Runs du paragraphe, dans l'ordre (y compris ceux des liens)."""
    return [_format_run(run) for run in paragraphe.iter(w_balise("r"))]


def _niveau_titre(paragraphe: Any, style: str | None) -> int | None:
    """Niveau de titre : par le style (``Heading 2``) ou la structure (``outlineLvl``)."""
    if style:
        normalise = style.strip()
        # « Title »/« Titre » est le titre du document (niveau 0) : le traiter
        # comme un titre de niveau 1 masquerait la page de garde du plan.
        if normalise.casefold() in ("title", "titre"):
            return 0
        correspondance = _STYLE_TITRE.match(normalise)
        if correspondance:
            return int(correspondance.group(1))
    ppr = paragraphe.find(w_balise("pPr"))
    outline = ppr.find(w_balise("outlineLvl")) if ppr is not None else None
    valeur = _attribut(outline, "val")
    if valeur and valeur.isdigit():
        return int(valeur) + 1
    return None


def _style_paragraphe(paragraphe: Any, styles: dict[str, str]) -> S.StyleBloc:
    """Style nommé + mise en forme directe lue sur le paragraphe et son premier run."""
    ppr = paragraphe.find(w_balise("pPr"))
    premier = next(iter(_runs(paragraphe)), None)
    identifiant = _attribut(ppr.find(w_balise("pStyle")), "val") if ppr is not None else None

    retrait: float | None = None
    interligne: float | None = None
    numerotation: str | None = None
    if ppr is not None:
        ind = ppr.find(w_balise("ind"))
        if ind is not None:
            retrait = _twips_mm(_attribut(ind, "firstLine")) or _twips_mm(_attribut(ind, "left"))
        espacement = ppr.find(w_balise("spacing"))
        if espacement is not None:
            ligne = _attribut(espacement, "line")
            regle = _attribut(espacement, "lineRule")
            if ligne and (regle is None or regle == "auto"):
                try:
                    interligne = round(float(ligne) / 240, 2)
                except ValueError:
                    interligne = None
        numpr = ppr.find(w_balise("numPr"))
        if numpr is not None:
            num_id = _attribut(numpr.find(w_balise("numId")), "val")
            ilvl = _attribut(numpr.find(w_balise("ilvl")), "val")
            numerotation = f"numId={num_id};ilvl={ilvl}"

    return S.StyleBloc(
        nom=nom_style(styles, identifiant),
        alignement=_attribut(ppr.find(w_balise("jc")), "val") if ppr is not None else None,
        gras=premier.gras if premier else None,
        italique=premier.italique if premier else None,
        taille_pt=premier.taille_pt if premier else None,
        couleur=premier.couleur if premier else None,
        police=premier.police if premier else None,
        numerotation=numerotation,
        retrait_mm=retrait,
        interligne=interligne,
        bordures="oui" if ppr is not None and ppr.find(w_balise("pBdr")) is not None else None,
    )


def _images(element: Any) -> int:
    """Nombre de références graphiques (DrawingML + VML)."""
    return sum(1 for _ in element.iter(f"{{{_A}}}blip")) + sum(
        1 for _ in element.iter(f"{{{_V}}}imagedata")
    )


def _saut_page(paragraphe: Any) -> bool:
    ppr = paragraphe.find(w_balise("pPr"))
    if ppr is not None and ppr.find(w_balise("pageBreakBefore")) is not None:
        return True
    return any(
        _attribut(noeud, "type") == "page" for noeud in paragraphe.iter(w_balise("br"))
    )


def _cellule(cellule: Any, ligne: int, colonne: int) -> S.CelluleBloc:
    """Cellule avec ses fusions réelles (``gridSpan``, ``vMerge``)."""
    tcpr = cellule.find(w_balise("tcPr"))
    span = 1
    vertical: str | None = None
    largeur: float | None = None
    if tcpr is not None:
        brut = _attribut(tcpr.find(w_balise("gridSpan")), "val")
        if brut and brut.isdigit() and int(brut) > 0:
            span = int(brut)
        fusion = tcpr.find(w_balise("vMerge"))
        if fusion is not None:
            vertical = "debut" if (_attribut(fusion, "val") or "continue") == "restart" else "suite"
        largeur = _twips_mm(_attribut(tcpr.find(w_balise("tcW")), "w"))
    textes = [
        texte_element(paragraphe).strip()
        for paragraphe in cellule.findall(w_balise("p"))
    ]
    return S.CelluleBloc(
        ligne=ligne,
        colonne=colonne,
        texte="\n".join(texte for texte in textes if texte),
        fusion_horizontale=span,
        fusion_verticale=vertical,
        largeur_mm=largeur,
    )


def _ligne(tableau_ligne: Any, index: int) -> S.LigneBloc:
    trpr = tableau_ligne.find(w_balise("trPr"))
    entete = trpr is not None and trpr.find(w_balise("tblHeader")) is not None
    cellules: list[S.CelluleBloc] = []
    colonne = 0
    for brute in tableau_ligne.findall(w_balise("tc")):
        cellule = _cellule(brute, index, colonne)
        cellules.append(cellule)
        colonne += cellule.fusion_horizontale
    return S.LigneBloc(index=index, entete=entete, cellules=cellules)


class _Compteur:
    """Compteur d'identifiants de blocs, global au document."""

    def __init__(self) -> None:
        self._valeur = 0

    def suivant(self) -> str:
        self._valeur += 1
        return f"block_{self._valeur:03d}"


def _bloc_paragraphe(
    paragraphe: Any, *, partie: str, compteur: _Compteur, position: dict[str, int],
    styles: dict[str, str],
) -> S.Bloc:
    style = _style_paragraphe(paragraphe, styles)
    return S.Bloc(
        block_id=compteur.suivant(),
        partie=partie,
        type="paragraphe",
        position=position,
        texte=texte_element(paragraphe).strip(),
        style=style,
        niveau_titre=_niveau_titre(paragraphe, style.nom),
        runs=_runs(paragraphe),
        images=_images(paragraphe),
        saut_page=_saut_page(paragraphe),
    )


def _bloc_tableau(
    tableau: Any, *, partie: str, compteur: _Compteur, position: dict[str, int],
    styles: dict[str, str],
) -> S.Bloc:
    grille = tableau.find(w_balise("tblGrid"))
    largeurs = []
    if grille is not None:
        for colonne in grille.findall(w_balise("gridCol")):
            largeur = _twips_mm(_attribut(colonne, "w"))
            if largeur is not None:
                largeurs.append(largeur)
    lignes = [
        _ligne(tr, index)
        for index, tr in enumerate(tableau.findall(w_balise("tr")))
    ]
    tblpr = tableau.find(w_balise("tblPr"))
    identifiant = _attribut(tblpr.find(w_balise("tblStyle")), "val") if tblpr is not None else None
    imbriques = max(0, sum(1 for _ in tableau.iter(w_balise("tbl"))) - 1)
    resume = " | ".join(
        cellule.texte.replace("\n", " ")
        for ligne in lignes[:2]
        for cellule in ligne.cellules
        if cellule.texte
    )
    return S.Bloc(
        block_id=compteur.suivant(),
        partie=partie,
        type="tableau",
        position={**position, "tableaux_imbriques": imbriques},
        texte=resume,
        style=S.StyleBloc(nom=nom_style(styles, identifiant)),
        lignes=lignes,
        colonnes=len(largeurs) or max((len(ligne.cellules) for ligne in lignes), default=0),
        largeurs_mm=largeurs,
        images=_images(tableau),
    )


def _blocs_partie(
    partie: str, racine: Any, styles: dict[str, str], compteur: _Compteur
) -> list[S.Bloc]:
    """Blocs d'une partie de contenu, dans l'ordre du document."""
    if partie == paquet_docx.PARTIE_DOCUMENT:
        corps = racine.find(w_balise("body"))
        enfants = list(corps) if corps is not None else []
    else:
        enfants = list(racine)

    blocs: list[S.Bloc] = []
    section = 0
    paragraphes = 0
    tableaux = 0
    for enfant in enfants:
        if enfant.tag == w_balise("p"):
            blocs.append(
                _bloc_paragraphe(
                    enfant,
                    partie=partie,
                    compteur=compteur,
                    position={"section": section, "paragraphe": paragraphes},
                    styles=styles,
                )
            )
            paragraphes += 1
            ppr = enfant.find(w_balise("pPr"))
            if ppr is not None and ppr.find(w_balise("sectPr")) is not None:
                section += 1
        elif enfant.tag == w_balise("tbl"):
            blocs.append(
                _bloc_tableau(
                    enfant,
                    partie=partie,
                    compteur=compteur,
                    position={"section": section, "tableau": tableaux},
                    styles=styles,
                )
            )
            tableaux += 1
    return blocs


def _relations(paquet: paquet_docx.Paquet, partie: str) -> dict[str, str]:
    """Relations d'une partie (``rId`` → partie cible), pour les en-têtes/pieds."""
    dossier = str(Path(partie).parent)
    if dossier == ".":
        nom = f"_rels/{Path(partie).name}.rels"
    else:
        nom = f"{dossier}/_rels/{Path(partie).name}.rels"
    if nom not in paquet.parties:
        return {}
    racine = paquet_docx.xml(paquet, nom)
    ns = "http://schemas.openxmlformats.org/package/2006/relationships"
    table: dict[str, str] = {}
    for relation in racine.findall(f"{{{ns}}}Relationship"):
        identifiant = relation.get("Id")
        cible = relation.get("Target")
        if identifiant and cible and relation.get("TargetMode") != "External":
            absolu = cible if cible.startswith("word/") else f"word/{cible.lstrip('./')}"
            table[identifiant] = absolu
    return table


def _texte_partie(paquet: paquet_docx.Paquet, nom: str) -> str:
    """Texte d'une partie (en-tête/pied), sur une seule ligne."""
    if nom not in paquet.parties:
        return ""
    racine = paquet_docx.xml(paquet, nom)
    morceaux = [
        texte_element(paragraphe).strip()
        for paragraphe in racine.iter(w_balise("p"))
    ]
    return " ".join(morceau for morceau in morceaux if morceau)


def _zones(paquet: paquet_docx.Paquet, racine: Any) -> list[S.ZoneModele]:
    """Sections de page : format, orientation, marges, en-tête et pied réels."""
    corps = racine.find(w_balise("body"))
    if corps is None:
        return []
    relations = _relations(paquet, paquet_docx.PARTIE_DOCUMENT)
    zones: list[S.ZoneModele] = []
    for section in corps.iter(w_balise("sectPr")):
        pg_sz = section.find(w_balise("pgSz"))
        pg_mar = section.find(w_balise("pgMar"))
        entete = ""
        pied = ""
        for reference in section.findall(w_balise("headerReference")):
            cible = relations.get(_attribut(reference, "id", namespace=_R) or "")
            entete = entete or _texte_partie(paquet, cible or "")
        for reference in section.findall(w_balise("footerReference")):
            cible = relations.get(_attribut(reference, "id", namespace=_R) or "")
            pied = pied or _texte_partie(paquet, cible or "")
        zones.append(
            S.ZoneModele(
                partie=paquet_docx.PARTIE_DOCUMENT,
                largeur_mm=_twips_mm(_attribut(pg_sz, "w")),
                hauteur_mm=_twips_mm(_attribut(pg_sz, "h")),
                orientation=_attribut(pg_sz, "orient"),
                marges_mm={
                    "haut": _twips_mm(_attribut(pg_mar, "top")),
                    "bas": _twips_mm(_attribut(pg_mar, "bottom")),
                    "gauche": _twips_mm(_attribut(pg_mar, "left")),
                    "droite": _twips_mm(_attribut(pg_mar, "right")),
                },
                entete=entete,
                pied=pied,
            )
        )
    return zones


def _empreinte(paquet: paquet_docx.Paquet) -> str:
    """Empreinte du **contenu** (indépendante des métadonnées ZIP)."""
    condensat = hashlib.sha256()
    for nom in sorted(paquet.parties):
        condensat.update(nom.encode("utf-8"))
        condensat.update(b"\0")
        condensat.update(paquet.parties[nom])
    return condensat.hexdigest()


def _avertissements(
    paquet: paquet_docx.Paquet, blocs: list[S.Bloc]
) -> list[str]:
    """Tout ce que l'analyse ne peut pas traiter de façon fiable, dit à l'avance."""
    avertissements: list[str] = []
    controles: list[str] = []
    modifies: list[str] = []
    zones_texte: list[str] = []
    for nom in paquet_docx.zones_texte(paquet):
        racine = paquet_docx.xml(paquet, nom)
        for controle in racine.iter(w_balise("sdt")):
            balise = controle.find(f"{w_balise('sdtPr')}/{w_balise('tag')}")
            etiquette = _attribut(balise, "val") if balise is not None else None
            controles.append(f"{nom}:{etiquette or '(sans tag)'}")
        if racine.find(f".//{w_balise('ins')}") is not None or racine.find(
            f".//{w_balise('del')}"
        ) is not None:
            modifies.append(nom)
        if racine.find(f".//{w_balise('txbxContent')}") is not None:
            zones_texte.append(nom)

    if controles:
        deja = sorted({tag for tag in controles if tag.split(":", 1)[1].startswith("carso:")})
        if deja:
            avertissements.append(
                "Ce modèle est DÉJÀ instrumenté par le moteur ("
                + ", ".join(deja[:5])
                + ") : ré-analyser le fichier d'origine, jamais la copie technique."
            )
        else:
            avertissements.append(
                f"{len(controles)} contrôle(s) de contenu (w:sdt) déjà présents : "
                "l'instrumentation s'ajoute à eux, elle ne les remplace pas."
            )
    if modifies:
        avertissements.append(
            "Modifications suivies présentes (w:ins/w:del) : leur contenu n'est pas "
            "traité comme du texte définitif par le rendu."
        )
    if zones_texte:
        avertissements.append(
            "Zones de texte flottantes présentes : elles ne sont pas instrumentées "
            "par ce moteur (le contenu du corps, lui, l'est)."
        )
    imbriques = sum(bloc.position.get("tableaux_imbriques", 0) for bloc in blocs)
    if imbriques:
        avertissements.append(
            f"{imbriques} tableau(x) imbriqué(s) détecté(s) : la ligne répétable se "
            "choisit sur le tableau de premier niveau."
        )
    if not any(bloc.niveau_titre for bloc in blocs):
        avertissements.append(
            "Aucun titre identifié : le plan du document ne peut pas être déduit des "
            "styles. Les blocs restent exploitables un par un."
        )
    return avertissements


class DocumentAnalyzer:
    """Analyse structurelle déterministe d'un DOCX."""

    def analyser(self, source: Path | bytes) -> S.AnalyseModele:
        """Analyse un DOCX (chemin ou octets) sans le modifier.

        Raises:
            ValidationError: fichier illisible, non DOCX, ou XML mal formé.
        """
        return analyser_paquet(paquet_docx.lire(source))


def analyser_paquet(paquet: paquet_docx.Paquet) -> S.AnalyseModele:
    """Analyse un paquet déjà lu (évite une double lecture)."""
    styles = _lire_styles(paquet)
    compteur = _Compteur()
    blocs: list[S.Bloc] = []
    for nom in paquet_docx.zones_texte(paquet):
        racine = paquet_docx.xml(paquet, nom)
        blocs.extend(_blocs_partie(nom, racine, styles, compteur))

    document = paquet_docx.xml(paquet, paquet_docx.PARTIE_DOCUMENT)
    zones = _zones(paquet, document)
    placeholders = sorted(
        {correspondance.group(1) for bloc in blocs
         for correspondance in _PLACEHOLDER.finditer(bloc.texte)}
    )
    titres = [
        {"block_id": bloc.block_id, "texte": bloc.texte, "niveau": bloc.niveau_titre}
        for bloc in blocs
        # ``0`` est un niveau valide (titre du document) : ``if bloc.niveau_titre``
        # l'aurait écarté et la page de garde aurait disparu du plan.
        if bloc.niveau_titre is not None
    ]
    corps = [bloc for bloc in blocs if bloc.partie == paquet_docx.PARTIE_DOCUMENT]
    return S.AnalyseModele(
        empreinte=_empreinte(paquet),
        nombre_blocs=len(blocs),
        blocs=blocs,
        zones=zones,
        images={
            "total": paquet.nb_images(),
            "references_corps": sum(bloc.images for bloc in corps),
            "references_zones": sum(
                bloc.images for bloc in blocs if bloc.partie != paquet_docx.PARTIE_DOCUMENT
            ),
        },
        placeholders=placeholders,
        titres=titres,
        nombre_parties=len(paquet_docx.zones_texte(paquet)),
        avertissements=_avertissements(paquet, blocs),
    )


def analyser(source: Path | bytes) -> S.AnalyseModele:
    """Raccourci : analyse structurelle d'un DOCX."""
    return DocumentAnalyzer().analyser(source)
