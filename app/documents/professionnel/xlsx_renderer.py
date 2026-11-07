"""Rendu Excel professionnel (``openpyxl``) — les tableaux du corps HTML.

Le classeur est construit **depuis le même corps HTML** que le PDF et le DOCX :
chaque ``table.data`` devient une feuille stylée thème CARSO (en-tête figé,
colonnes dimensionnées, lignes alternées), les ``table.kpi`` alimentent une
feuille « Chiffres clés ». La mise en page est décidée ici, jamais par l'agent ;
sans aucun tableau, le classeur est refusé explicitement (jamais un .xlsx vide
présenté comme un livrable).

INC-116 — le tableau HTML porte désormais la **mise en forme de cellule** et les
**formules**, par attributs explicites (rien n'est deviné) :

```html
<table class="data">
  <thead><tr><th>Poste</th><th class="num">Montant</th></tr></thead>
  <tbody>
    <tr><td>Formation</td>
        <td class="num" data-format="devise:MGA">2 500 000</td></tr>
    <tr class="total"><td>Total</td>
        <td data-formule="=SUM(B3:B3)" data-format="devise:MGA">2 500 000</td></tr>
  </tbody>
</table>
```

Attributs reconnus :

- ``data-formule`` — formule Excel (préfixe ``=``), recalculée par le tableur ;
- ``data-format`` — ``entier``, ``decimal``, ``pourcentage`` (valeur en fraction),
  ``devise`` / ``devise:CODE``, ``date`` (``AAAA-MM-JJ`` ou ``JJ/MM/AAAA``) ;
- ``data-align`` — ``gauche``, ``centre``, ``droite`` ;
- ``data-gras``, ``data-fond`` (``primaire|doux|ok|alerte``), ``data-renvoi`` ;
- ``data-largeur`` — largeur de colonne en caractères (première valeur gagne) ;
- ``colspan`` / ``rowspan`` — fusions Excel réelles.

Classes de ligne : ``total`` / ``sous-total`` (gras, fond doux, filet haut) et
``alerte`` (fond rouge clair). Le style reste celui du thème : l'agent ne choisit
qu'un rôle, jamais une couleur arbitraire.

Une valeur non convertible pour le format demandé **reste du texte** et produit
un avertissement : aucune donnée n'est inventée ni silencieusement perdue.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from bs4 import BeautifulSoup, Tag
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.properties import PageSetupProperties

from app.core.errors import ValidationError

from .themes import BORDURE, MUTED, TEXTE, Theme, get_theme

__all__ = ["rendre_xlsx"]

_TIROIR_MAX = 31  # limite de nom de feuille Excel
_LARGEUR_MIN, _LARGEUR_MAX = 10, 60
_FORMULE_MAX = 500

#: Formats de nombre acceptés → format Excel. ``devise`` est complété par le
#: code fourni (``devise:MGA``) ; sans code, aucun symbole n'est ajouté.
_FORMATS_FIXES = {
    "entier": "#,##0",
    "decimal": "#,##0.00",
    "pourcentage": "0.00%",
    "date": "dd/mm/yyyy",
}

#: Fonctions d'exfiltration : une formule de classeur n'a aucune raison de
#: sortir du fichier. Refus explicite, jamais silencieux.
_FORMULES_INTERDITES = ("WEBSERVICE(", "HYPERLINK(", "DDE", "|", "CMD(")

#: Rôles de fond acceptés (la couleur vient du thème, jamais de l'agent).
_FONDS = frozenset({"primaire", "doux", "ok", "alerte"})

#: Rôles de couleur de TEXTE : l'agent nomme un rôle, jamais un code couleur.
_COULEURS_TEXTE = frozenset(
    {"primaire", "accent", "ok", "alerte", "texte", "gris", "blanc"}
)

#: Épaisseurs de bordure Excel acceptées (``None`` = pas de bordure).
_BORDURES = {"aucune": None, "fine": "thin", "moyenne": "medium", "epaisse": "thick"}

#: Couleurs applicables à une bordure : les rôles de texte et de fond.
_COULEURS_BORDURE = _COULEURS_TEXTE | _FONDS

#: Bornes de taille de police (points).
_TAILLE_MIN, _TAILLE_MAX = 8.0, 20.0

_NOMBRE_NETTOYAGE = re.compile(r"[\s\u00a0\u202f€$]")
_DEVISE_FIN = re.compile(r"(?:ar|mga|eur|usd|chf)\s*$", re.IGNORECASE)


@dataclass(slots=True)
class Cellule:
    """Une cellule du tableau HTML : contenu **et** mise en forme demandée."""

    texte: str
    formule: str | None = None
    format_nombre: str | None = None
    alignement: str | None = None
    gras: bool = False
    fond: str | None = None
    renvoi: bool = False
    num: bool = False
    colspan: int = 1
    rowspan: int = 1
    largeur: float | None = None
    couleur: str | None = None
    taille: float | None = None
    bordure: str | None = None
    bordure_couleur: str | None = None

    @property
    def numerique(self) -> bool:
        """Colonne de chiffres : ``class="num"`` ou format numérique explicite."""
        return self.num or (
            self.format_nombre is not None and self.format_nombre != _FORMATS_FIXES["date"]
        )


@dataclass(slots=True)
class Ligne:
    """Une ligne de données et son rôle visuel (``total``, ``alerte``…)."""

    cellules: list[Cellule]
    style: str | None = None


# --------------------------------------------------------------------------- lecture
def _texte(el: Tag | None) -> str:
    if el is None:
        return ""
    return " ".join(el.get_text(" ", strip=True).split())


def _classes(el: Tag) -> set[str]:
    valeur: object = el.get("class") or []
    if isinstance(valeur, str):
        return set(valeur.split())
    return {str(classe) for classe in valeur}


def _attribut(el: Tag, nom: str) -> str:
    valeur = el.get(nom)
    return str(valeur).strip() if valeur is not None else ""


def _entier_attribut(el: Tag, nom: str) -> int:
    brut = _attribut(el, nom)
    if not brut:
        return 1
    try:
        return max(1, min(20, int(brut)))
    except ValueError:
        return 1


def _booleen(el: Tag, nom: str) -> bool:
    return _attribut(el, nom).lower() in {"1", "true", "oui", "yes"}


def _format_nombre(brut: str, avertissements: list[str]) -> str | None:
    """Traduit ``data-format`` en format Excel (``None`` si absent)."""
    if not brut:
        return None
    valeur = brut.strip().lower()
    if valeur in _FORMATS_FIXES:
        return _FORMATS_FIXES[valeur]
    if valeur == "devise":
        return "#,##0.00"
    if valeur.startswith("devise:"):
        code = valeur.split(":", 1)[1].strip().upper()[:6]
        if not code or not code.isalpha():
            avertissements.append(
                f"data-format={brut!r} : code devise invalide, format numérique simple appliqué."
            )
            return "#,##0.00"
        return f'#,##0.00 "{code}"'
    avertissements.append(
        f"data-format={brut!r} inconnu : formats acceptés entier, decimal, "
        "pourcentage, devise, devise:CODE, date."
    )
    return None


def _formule(brut: str, avertissements: list[str], reference: str) -> str | None:
    """Valide une formule déclarée par l'agent (jamais évaluée ici)."""
    if not brut:
        return None
    formule = brut.strip()
    if not formule.startswith("=") or len(formule) < 2:
        avertissements.append(
            f"{reference} : data-formule doit commencer par « = » — contenu ignoré."
        )
        return None
    if len(formule) > _FORMULE_MAX:
        avertissements.append(
            f"{reference} : formule de plus de {_FORMULE_MAX} caractères ignorée."
        )
        return None
    majuscule = formule.upper().replace(" ", "")
    if any(mot in majuscule for mot in _FORMULES_INTERDITES):
        avertissements.append(
            f"{reference} : formule contenant un appel externe refusée "
            "(WEBSERVICE/HYPERLINK/DDE/CMD)."
        )
        return None
    return formule


def _nombre(texte: str) -> float:
    """Convertit un nombre affiché (fr ou en) en flottant.

    ``1 250 000``, ``1.250.000`` (milliers), ``12,5`` et ``12.5`` sont
    acceptés ; ``1,250.50`` et ``1 250,50`` aussi. Un texte non numérique lève
    ``ValueError`` : l'appelant le conserve alors tel quel.
    """
    brut = _DEVISE_FIN.sub("", texte.strip()).rstrip("%").strip()
    brut = _NOMBRE_NETTOYAGE.sub("", brut)
    if not brut:
        raise ValueError(texte)
    if "," in brut and "." in brut:
        dernier = max(brut.rfind(","), brut.rfind("."))
        separateur = brut[dernier]
        milliers = "," if separateur == "." else "."
        brut = brut.replace(milliers, "").replace(separateur, ".")
    elif "," in brut:
        gauche, _, droite = brut.rpartition(",")
        if droite.isdigit() and len(droite) == 3 and gauche.isdigit() and len(gauche) > 3:
            brut = gauche + droite  # 1,250 → 1250 (séparateur de milliers)
        else:
            brut = gauche + "." + droite
    elif brut.count(".") > 1:
        brut = brut.replace(".", "")
    return float(brut)


def _valeur(cellule: Cellule, reference: str, avertissements: list[str]) -> object:
    """Valeur écrite dans la cellule : formule, nombre, date ou texte."""
    if cellule.formule:
        return cellule.formule
    if cellule.format_nombre is None or cellule.texte == "":
        return cellule.texte
    if cellule.format_nombre == _FORMATS_FIXES["date"]:
        for motif in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d"):
            try:
                return datetime.strptime(cellule.texte, motif).date()
            except ValueError:
                continue
        avertissements.append(
            f"{reference} : « {cellule.texte} » n'est pas une date reconnue "
            "(AAAA-MM-JJ ou JJ/MM/AAAA) — cellule laissée en texte."
        )
        return cellule.texte
    try:
        nombre = _nombre(cellule.texte)
    except ValueError:
        avertissements.append(
            f"{reference} : « {cellule.texte} » n'est pas un nombre pour "
            f"data-format={cellule.format_nombre!r} — cellule laissée en texte."
        )
        return cellule.texte
    if cellule.format_nombre == _FORMATS_FIXES["entier"]:
        return int(round(nombre))
    return nombre


def _cellule(el: Tag, avertissements: list[str], reference: str) -> Cellule:
    formule = _formule(_attribut(el, "data-formule"), avertissements, reference)
    largeur_brute = _attribut(el, "data-largeur")
    largeur: float | None = None
    if largeur_brute:
        try:
            largeur = max(_LARGEUR_MIN, min(_LARGEUR_MAX, float(largeur_brute)))
        except ValueError:
            avertissements.append(
                f"{reference} : data-largeur={largeur_brute!r} n'est pas un nombre — ignoré."
            )
    couleur = _role(el, "data-couleur", _COULEURS_TEXTE, avertissements, reference)
    taille = _taille(_attribut(el, "data-taille"), avertissements, reference)
    bordure = _role(el, "data-bordure", set(_BORDURES), avertissements, reference)
    bordure_couleur = _role(
        el, "data-bordure-couleur", _COULEURS_BORDURE, avertissements, reference
    )
    return Cellule(
        texte=_texte(el),
        formule=formule,
        format_nombre=_format_nombre(_attribut(el, "data-format"), avertissements),
        alignement=_attribut(el, "data-align").lower() or None,
        gras=_booleen(el, "data-gras") or "gras" in _classes(el),
        fond=_attribut(el, "data-fond").lower() or None,
        renvoi=_booleen(el, "data-renvoi"),
        num="num" in _classes(el),
        colspan=_entier_attribut(el, "colspan"),
        rowspan=_entier_attribut(el, "rowspan"),
        largeur=largeur,
        couleur=couleur,
        taille=taille,
        bordure=bordure,
        bordure_couleur=bordure_couleur,
    )


def _role(
    el: Tag, attribut: str, acceptes: set[str], avertissements: list[str], reference: str
) -> str | None:
    """Littéral d'attribut contrôlé (rôle nommé) ; inconnu = ignoré + averti."""
    valeur = _attribut(el, attribut).lower()
    if not valeur:
        return None
    if valeur not in acceptes:
        avertissements.append(
            f"{reference} : {attribut}={valeur!r} inconnu — valeurs acceptées "
            f"{', '.join(sorted(acceptes))} ; attribut ignoré."
        )
        return None
    return valeur


def _taille(brut: str, avertissements: list[str], reference: str) -> float | None:
    """Taille de police en points (accepte ``14`` et ``14pt``), bornée."""
    if not brut:
        return None
    valeur = brut.strip().lower().removesuffix("pt").strip()
    try:
        taille = float(valeur.replace(",", "."))
    except ValueError:
        avertissements.append(
            f"{reference} : data-taille={brut!r} n'est pas un nombre en points — ignoré."
        )
        return None
    bornee = max(_TAILLE_MIN, min(_TAILLE_MAX, taille))
    if bornee != taille:
        avertissements.append(
            f"{reference} : data-taille={brut!r} ramenée à {bornee:g} pt "
            f"(bornes {_TAILLE_MIN:g}–{_TAILLE_MAX:g})."
        )
    return bornee


def _colonnes_numeriques(entetes: list[Cellule]) -> set[int]:
    """Colonnes déclarées numériques (format explicite ou classe ``num``)."""
    return {
        index
        for index, cellule in enumerate(entetes)
        if cellule.numerique
    }


def _lignes_tableau(
    table: Tag, avertissements: list[str], reference: str
) -> tuple[list[Cellule], list[Ligne]]:
    entetes: list[Cellule] = []
    lignes: list[Ligne] = []
    for tr in table.find_all("tr"):
        if tr.find_parent("table") is not table:
            continue
        cellules = tr.find_all(["td", "th"], recursive=False)
        if not cellules:
            continue
        parent = tr.parent.name if tr.parent is not None else ""
        est_entete = parent == "thead" or (not entetes and cellules[0].name == "th")
        if est_entete:
            entetes = [
                _cellule(c, avertissements, f"{reference} en-tête")
                for c in cellules
            ]
            continue
        if parent == "tfoot":
            continue  # un total déjà présent dans les lignes reste lisible
        classes = _classes(tr)
        style = next(
            (nom for nom in ("total", "sous-total", "alerte") if nom in classes),
            None,
        )
        lignes.append(
            Ligne(
                cellules=[
                    _cellule(c, avertissements, f"{reference} ligne {len(lignes) + 1}")
                    for c in cellules
                ],
                style=style,
            )
        )
    if not entetes and lignes:
        entetes = [
            Cellule(texte=f"Colonne {i + 1}")
            for i in range(len(lignes[0].cellules))
        ]
    return entetes, lignes


def _nom_feuille(racine: Tag, table: Tag, deja_pris: set[str], rang: int) -> str:
    """Nom de feuille : le titre le plus proche AVANT le tableau, sinon Tableau n."""
    titre = ""
    for frere in racine.descendants:
        if frere is table:
            break
        if isinstance(frere, Tag) and frere.name in ("h1", "h2", "h3"):
            texte = _texte(frere)
            if texte:
                titre = texte
    brut = titre or f"Tableau {rang + 1}"
    nom = re.sub(r"[\[\]:*?/\\]", " ", brut).strip()[:_TIROIR_MAX] or f"Tableau {rang + 1}"
    base = nom
    suffixe = 2
    while nom.lower() in deja_pris:
        nom = f"{base[:_TIROIR_MAX - 3]} ({suffixe})"
        suffixe += 1
    deja_pris.add(nom.lower())
    return nom


def _kpis_classeur(racine: Tag) -> list[tuple[str, str]]:
    kpis: list[tuple[str, str]] = []
    for table in racine.find_all("table"):
        if "kpi" not in _classes(table):
            continue
        for cellule in table.find_all(["td", "th"]):
            valeur = cellule.find(class_="kpi-value")
            label = cellule.find(class_="kpi-label")
            if valeur is not None:
                kpis.append((_texte(label), _texte(valeur)))
            else:
                texte = _texte(cellule)
                if texte:
                    kpis.append(("", texte))
    return kpis


def _tables_donnees(
    racine: Tag, avertissements: list[str]
) -> list[tuple[str, list[Cellule], list[Ligne]]]:
    tables: list[tuple[str, list[Cellule], list[Ligne]]] = []
    deja_pris: set[str] = set()
    for rang, table in enumerate(racine.find_all("table")):
        if "data" not in _classes(table):
            continue
        nom = _nom_feuille(racine, table, deja_pris, rang)
        entetes, lignes = _lignes_tableau(table, avertissements, f"Tableau {rang + 1}")
        if not entetes and not lignes:
            continue
        tables.append((nom, entetes, lignes))
    return tables


# --------------------------------------------------------------------------- style
def _hex(couleur: str) -> str:
    return couleur.lstrip("#").upper()


def _bord_basse(couleur: str) -> Border:
    cote = Side(style="thin", color=_hex(couleur))
    return Border(bottom=cote)


def _couleur_fond(role: str, theme: Theme) -> str | None:
    """Couleur de fond d'un rôle (le thème décide, jamais l'agent)."""
    return {
        "primaire": theme.primary,
        "doux": theme.soft,
        "ok": "#ECFDF5",
        "alerte": "#FEF2F2",
    }.get(role)


def _couleur_texte(role: str, theme: Theme) -> str | None:
    """Couleur de texte d'un rôle nommé (le thème décide, jamais l'agent)."""
    return {
        "primaire": theme.primary,
        "accent": theme.accent,
        "ok": "#059669",
        "alerte": "#DC2626",
        "texte": TEXTE,
        "gris": MUTED,
        "blanc": "#FFFFFF",
    }.get(role)


def _couleur_bordure(role: str, theme: Theme) -> str:
    """Couleur de bordure : rôles de texte, puis fonds, puis le filet standard."""
    return _couleur_texte(role, theme) or _couleur_fond(role, theme) or BORDURE


def _bordure_cellule(
    cellule: Cellule, theme: Theme, role_ligne: str | None
) -> Border | None:
    """Bordure explicitement demandée sur les quatre côtés (``None`` sinon).

    ``None`` laisse la bordure posée par le rôle de la ligne : une cellule
    sans ``data-bordure`` ne change donc rien au style du tableau.
    """
    if not cellule.bordure:
        return None
    if cellule.bordure == "aucune":
        return Border()
    style = _BORDURES[cellule.bordure]
    if cellule.bordure_couleur:
        couleur = _couleur_bordure(cellule.bordure_couleur, theme)
    elif role_ligne in ("total", "sous-total"):
        couleur = theme.primary
    else:
        couleur = BORDURE
    cote = Side(style=style, color=_hex(couleur))
    return Border(left=cote, right=cote, top=cote, bottom=cote)


def _alignement(cellule: Cellule, defaut: str) -> str:
    table = {"gauche": "left", "centre": "center", "droite": "right"}
    if cellule.alignement in table:
        return table[cellule.alignement]
    return defaut


def _feuille_tableau(
    wb: Workbook,
    nom: str,
    t: Theme,
    entetes: list[Cellule],
    lignes: list[Ligne],
    titre_document: str,
    avertissements: list[str],
) -> None:
    ws = wb.create_sheet(title=nom[:_TIROIR_MAX])
    largeur = max(sum(c.colspan for c in entetes), 1)
    # Titre du document, fusionné sur la largeur du tableau.
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=largeur)
    cellule_titre = ws.cell(row=1, column=1, value=titre_document)
    cellule_titre.font = Font(bold=True, size=13, color=_hex(t.primary))
    cellule_titre.alignment = Alignment(horizontal="left", vertical="center")
    ws.row_dimensions[1].height = 26

    # En-têtes : fond primaire, blanc, gras (fusions HTML respectées).
    remplir_en_tete = PatternFill("solid", start_color=_hex(t.primary))
    # Largeurs déclarées : lues sur les en-têtes comme sur les cellules.
    largeurs: dict[int, float] = {}
    colonne = 1
    for entete in entetes:
        cellule = ws.cell(row=2, column=colonne, value=entete.texte)
        # Le rôle de texte de l'en-tête, s'il est déclaré, l'emporte sur le blanc
        # par défaut ; la taille déclarée s'ajoute au gras de l'en-tête.
        couleur_entete = (
            _couleur_texte(entete.couleur, t) if entete.couleur else None
        ) or "#FFFFFF"
        cellule.font = Font(bold=True, color=_hex(couleur_entete), size=entete.taille)
        cellule.fill = remplir_en_tete
        cellule.alignment = Alignment(
            horizontal=_alignement(entete, "right" if entete.numerique else "left"),
            vertical="center",
            wrap_text=entete.renvoi,
        )
        cellule.border = _bord_basse(t.primary)
        if entete.largeur:
            largeurs[colonne] = entete.largeur
        if entete.colspan > 1 or entete.rowspan > 1:
            ws.merge_cells(
                start_row=2,
                start_column=colonne,
                end_row=1 + entete.rowspan,
                end_column=colonne + entete.colspan - 1,
            )
        colonne += entete.colspan

    numeriques = _colonnes_numeriques(entetes)
    alternance = PatternFill("solid", start_color=_hex(t.soft))

    for rang, ligne in enumerate(lignes, start=3):
        pair = (rang - 3) % 2 == 1
        colonne = 1
        for index, cellule in enumerate(ligne.cellules):
            reference = f"« {nom} » ligne {rang - 2}"
            valeur = _valeur(cellule, reference, avertissements)
            c = ws.cell(row=rang, column=colonne, value=valeur)
            if cellule.format_nombre is not None:
                c.number_format = cellule.format_nombre

            total = ligne.style in ("total", "sous-total")
            gras = cellule.gras or total
            if cellule.couleur:
                couleur = _couleur_texte(cellule.couleur, t) or TEXTE
            elif cellule.fond == "primaire":
                couleur = "#FFFFFF"
            else:
                couleur = TEXTE
            c.font = Font(
                bold=gras,
                color=_hex(couleur),
                size=cellule.taille or 11,
            )
            defaut = "right" if index in numeriques else "left"
            c.alignment = Alignment(
                horizontal=_alignement(cellule, defaut),
                vertical="center",
                wrap_text=cellule.renvoi or len(cellule.texte) > 40,
            )
            if ligne.style == "total":
                c.border = Border(
                    bottom=_bord_basse(t.primary).bottom,
                    top=Side(style="thin", color=_hex(t.primary)),
                )
            else:
                c.border = _bord_basse(BORDURE)
            bordure_demandee = _bordure_cellule(cellule, t, ligne.style)
            if bordure_demandee is not None:
                c.border = bordure_demandee

            fond_brut = cellule.fond or (
                "doux" if total else ("alerte" if ligne.style == "alerte" else None)
            )
            if fond_brut in _FONDS:
                couleur_fond = _couleur_fond(fond_brut, t)
                if couleur_fond:
                    c.fill = PatternFill("solid", start_color=_hex(couleur_fond))
            elif pair:
                c.fill = alternance

            if cellule.colspan > 1 or cellule.rowspan > 1:
                ws.merge_cells(
                    start_row=rang,
                    start_column=colonne,
                    end_row=rang + cellule.rowspan - 1,
                    end_column=colonne + cellule.colspan - 1,
                )
            if cellule.largeur and colonne not in largeurs:
                largeurs[colonne] = cellule.largeur
            colonne += cellule.colspan

    # Largeurs de colonnes : largeur déclarée, sinon le plus long contenu, borné.
    for index in range(1, largeur + 1):
        if index in largeurs:
            ws.column_dimensions[get_column_letter(index)].width = largeurs[index]
            continue
        contenus: list[int] = []
        position = 1
        for cellule in entetes:
            if position == index:
                contenus.append(len(cellule.texte))
            position += cellule.colspan
        for ligne in lignes:
            position = 1
            for cellule in ligne.cellules:
                if position == index:
                    contenus.append(len(cellule.texte))
                position += cellule.colspan
        ws.column_dimensions[get_column_letter(index)].width = max(
            _LARGEUR_MIN, min(_LARGEUR_MAX, max(contenus, default=10) + 2)
        )

    ws.freeze_panes = "A3"  # titre + en-têtes restent visibles
    ws.auto_filter.ref = f"A2:{get_column_letter(largeur)}{max(2, 2 + len(lignes))}"
    # Impression : en-tête répété, ajustement à la largeur, paysage si large.
    ws.print_title_rows = "2:2"
    ws.page_setup.orientation = "landscape" if largeur > 6 else "portrait"
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr = PageSetupProperties(fitToPage=True)


def _feuille_kpis(wb: Workbook, t: Theme, kpis: list[tuple[str, str]], titre_document: str) -> None:
    ws = wb.create_sheet(title="Chiffres clés")
    ws.merge_cells("A1:B1")
    cellule_titre = ws.cell(row=1, column=1, value=titre_document)
    cellule_titre.font = Font(bold=True, size=13, color=_hex(t.primary))
    ws.row_dimensions[1].height = 26
    for rang, (label, valeur) in enumerate(kpis, start=3):
        cellule_label = ws.cell(row=rang, column=1, value=(label or "Indicateur").upper())
        cellule_label.font = Font(size=9, color=_hex(MUTED))
        cellule_valeur = ws.cell(row=rang, column=2, value=valeur)
        cellule_valeur.font = Font(bold=True, size=12, color=_hex(t.primary))
        cellule_valeur.alignment = Alignment(horizontal="right")
    ws.column_dimensions["A"].width = 28
    ws.column_dimensions["B"].width = 18


# --------------------------------------------------------------------------- point d'entrée
def rendre_xlsx(
    corps_html: str,
    chemin: str | Path,
    *,
    theme: str | Theme = "corporate",
    titre: str = "",
    auteur: str = "",
    avertissements: list[str] | None = None,
) -> Path:
    """Écrit le classeur et retourne son chemin.

    Args:
        corps_html: corps du document (les ``table.data`` deviennent des
            feuilles) ; la mise en forme vient des attributs ``data-*``.
        chemin: fichier .xlsx à écrire.
        theme: palette CARSO.
        titre: titre du document (ligne 1 de chaque feuille).
        auteur: métadonnée du classeur.
        avertissements: liste optionnelle où sont ajoutés les constats
            (formule refusée, valeur non convertible…).

    Raises:
        ValidationError: corps vide, thème inconnu, ou aucun tableau
            (``table.data`` / ``table.kpi``) : un .xlsx sans données n'est pas
            un livrable.
    """
    if not corps_html or not corps_html.strip():
        raise ValidationError("contenu_html est vide : rien à mettre en classeur.")
    t = get_theme(theme)
    constats: list[str] = [] if avertissements is None else avertissements

    racine = BeautifulSoup(corps_html, "html.parser")
    tables = _tables_donnees(racine, constats)
    kpis = _kpis_classeur(racine)
    if not tables and not kpis:
        raise ValidationError(
            "Un classeur Excel attend des tableaux : aucun table.data (ni "
            "table.kpi) dans le contenu. Écris au moins un tableau de données "
            "avec <thead>, ou choisis le format pdf/docx pour un document texte.",
            details={"attendus": ["table.data", "table.kpi"]},
        )

    wb = Workbook()
    wb.remove(wb.active)  # la feuille par défaut : on ne garde que les nôtres
    for nom, entetes, lignes in tables:
        _feuille_tableau(wb, nom, t, entetes, lignes, titre, constats)
    if kpis:
        _feuille_kpis(wb, t, kpis, titre)

    wb.properties.title = titre or "Classeur"
    wb.properties.creator = auteur

    chemin = Path(chemin)
    chemin.parent.mkdir(parents=True, exist_ok=True)
    wb.save(str(chemin))
    return chemin
