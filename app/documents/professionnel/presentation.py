"""Modèle de présentation : HTML de diapositives → diapositives interprétées.

Contrat exposé à l'agent (même outil, même paramètre ``contenu_html``) :

- ``<section class="cover">`` : diapositive de titre (composant existant) ;
- ``<section class="slide">`` : une diapositive de contenu, avec ``h1``/``h2``
  pour le titre, ``p.lead`` pour le message clé, ``ul``/``ol``, ``table.kpi``,
  ``table.data``, ``figure`` (ancre de graphique/illustration, SVG ou image),
  ``div.callout`` et ``aside.notes`` pour les notes présentateur.

Ce module est le **seul propriétaire** de la lecture : le rendu PPTX et le PDF
jumeau consomment le même modèle. Un élément non reconnu n'est jamais deviné —
il est signalé par ``verification.py`` ou refusé explicitement.
"""

from __future__ import annotations

import html
from dataclasses import dataclass, field
from string import Template
from typing import Any, Literal

from bs4 import BeautifulSoup, Tag

from app.core.errors import ValidationError

from .themes import BORDURE, CALLOUT_VARIANTES, MUTED, TEXTE, Theme, get_theme

__all__ = [
    "Bloc",
    "Diapositive",
    "Figure",
    "MAX_DIAPOSITIVES",
    "Tableau",
    "construire_html_diapositives",
    "lire_presentation",
    "verifier_presentation",
]

#: Bornes de travail : au-delà, la présentation est refusée (pas de deck de 200
#: diapositives qui ferait expirer le rendu).
MAX_DIAPOSITIVES = 40

#: Limites de contenu (contrôlées par ``verification`` et appliquées au rendu).
TITRE_MAX = 90
LEAD_MAX = 220
PUCES_MAX = 7
PUCE_MAX = 180
KPIS_MAX = 4

GenreBloc = Literal["sous_titre", "paragraphe", "puces", "kpi", "tableau", "encadre"]


@dataclass(frozen=True, slots=True)
class Tableau:
    """Tableau de données : en-têtes + lignes, colonnes numériques alignées."""

    entetes: tuple[str, ...]
    lignes: tuple[tuple[str, ...], ...]
    colonnes_num: tuple[int, ...] = ()


@dataclass(frozen=True, slots=True)
class Figure:
    """Visuel d'une diapositive (image déjà intégrée, PNG ou data URI)."""

    src: str
    libelle: str = ""


@dataclass(frozen=True, slots=True)
class Bloc:
    """Bloc de contenu d'une diapositive, dans l'ordre du document."""

    genre: GenreBloc
    texte: str = ""
    variante: str = ""
    items: tuple[str, ...] = ()
    kpis: tuple[tuple[str, str], ...] = ()
    tableau: Tableau | None = None


@dataclass(frozen=True, slots=True)
class Diapositive:
    """Une diapositive interprétée."""

    titre: str = ""
    surtitre: str = ""
    lead: str = ""
    blocs: tuple[Bloc, ...] = field(default_factory=tuple)
    figure: Figure | None = None
    notes: str = ""
    est_titre: bool = False
    sous_titre: str = ""
    meta: str = ""


# --------------------------------------------------------------------------- lecture
def _classes(el: Tag) -> set[str]:
    valeur: Any = el.get("class") or []
    if isinstance(valeur, str):
        return set(valeur.split())
    return {str(classe) for classe in valeur}


def _texte(el: Tag | None) -> str:
    if el is None:
        return ""
    return " ".join(el.get_text(" ", strip=True).split())


def _tableau(el: Tag) -> Tableau | None:
    lignes: list[tuple[str, ...]] = []
    colonnes_num: list[int] = []
    entetes: tuple[str, ...] = ()
    for tr in el.find_all("tr"):
        if tr.find_parent("table") is not el:
            continue
        cellules = tr.find_all(["td", "th"], recursive=False)
        if not cellules:
            continue
        parent = tr.parent.name if tr.parent is not None else ""
        valeurs = tuple(_texte(cellule) for cellule in cellules)
        if parent == "thead" or (not entetes and cellules and cellules[0].name == "th"):
            entetes = valeurs
            continue
        if parent == "tfoot":
            continue  # un total de page n'a pas de sens sur une diapositive
        if not colonnes_num:
            colonnes_num = [
                index for index, cellule in enumerate(cellules) if "num" in _classes(cellule)
            ]
        lignes.append(valeurs)
    if not lignes and not entetes:
        return None
    if not entetes:
        entetes = tuple(f"Colonne {index + 1}" for index in range(len(lignes[0])))
    largeur = len(entetes)
    normalisees = tuple(
        tuple((list(ligne) + [""] * largeur)[:largeur]) for ligne in lignes
    )
    return Tableau(entetes=entetes, lignes=normalisees, colonnes_num=tuple(colonnes_num))


def _kpis(el: Tag) -> tuple[tuple[str, str], ...]:
    kpis: list[tuple[str, str]] = []
    for cellule in el.find_all(["td", "th"]):
        valeur = cellule.find(class_="kpi-value")
        label = cellule.find(class_="kpi-label")
        if valeur is not None:
            kpis.append((_texte(valeur), _texte(label)))
        else:
            texte = _texte(cellule)
            if texte:
                kpis.append((texte, ""))
    return tuple(kpis)


def _figure(el: Tag) -> Figure | None:
    image = el.find("img")
    if image is None:
        return None
    legende = el.find("figcaption")
    return Figure(src=str(image.get("src") or "").strip(), libelle=_texte(legende))


def _parcourir(el: Tag, blocs: list[Bloc], diapositive: dict[str, Any]) -> None:
    """Parcourt les enfants d'une section et remplit blocs + champs simples."""
    for enfant in el.children:
        if not isinstance(enfant, Tag):
            continue
        classes = _classes(enfant)
        nom = enfant.name
        if nom in ("h1", "h2"):
            if not diapositive["titre"]:
                diapositive["titre"] = _texte(enfant)
            else:
                blocs.append(Bloc("sous_titre", texte=_texte(enfant)))
        elif nom == "h3":
            blocs.append(Bloc("sous_titre", texte=_texte(enfant)))
        elif nom == "p":
            if "lead" in classes and not diapositive["lead"]:
                diapositive["lead"] = _texte(enfant)
            elif "notes" in classes:
                diapositive["notes"] = (diapositive["notes"] + " " + _texte(enfant)).strip()
            else:
                texte = _texte(enfant)
                if texte:
                    blocs.append(Bloc("paragraphe", texte=texte))
        elif nom in ("ul", "ol"):
            items = tuple(
                _texte(li)
                for li in enfant.find_all("li", recursive=False)
                if _texte(li)
            )
            if items:
                blocs.append(Bloc("puces", items=items))
        elif nom == "table":
            if "kpi" in classes:
                kpis = _kpis(enfant)
                if kpis:
                    blocs.append(Bloc("kpi", kpis=kpis))
            elif "layout" in classes:
                # Une table de mise en page n'a pas de sens ici : son texte
                # compte quand même, en paragraphes, plutôt que de disparaître.
                texte = _texte(enfant)
                if texte:
                    blocs.append(Bloc("paragraphe", texte=texte))
            else:
                tableau = _tableau(enfant)
                if tableau is not None:
                    blocs.append(Bloc("tableau", tableau=tableau))
        elif nom == "figure":
            figure = _figure(enfant)
            if figure is not None and diapositive["figure"] is None:
                diapositive["figure"] = figure
        elif nom == "aside" and "notes" in classes:
            diapositive["notes"] = (diapositive["notes"] + " " + _texte(enfant)).strip()
        elif "callout" in classes or nom == "blockquote":
            variante = next((v for v in CALLOUT_VARIANTES if v in classes), "")
            blocs.append(Bloc("encadre", texte=_texte(enfant), variante=variante))
        elif nom in ("div", "section", "article", "main", "header", "footer"):
            _parcourir(enfant, blocs, diapositive)


def _sections_racines(soup: BeautifulSoup) -> list[Tag]:
    sections = [
        section
        for section in soup.find_all("section")
        if _classes(section) & {"slide", "cover"}
    ]
    # Une section imbriquée dans une autre section de diapositive n'est pas une
    # diapositive : elle appartient à celle qui la porte.
    return [
        section
        for section in sections
        if not any(
            _classes(parent) & {"slide", "cover"}
            for parent in section.parents
            if isinstance(parent, Tag)
        )
    ]


def lire_presentation(corps: str) -> tuple[Diapositive, ...]:
    """Interprète le corps HTML en diapositives.

    Raises:
        ValidationError: aucune section ``.slide``/``.cover``, ou plus de
            ``MAX_DIAPOSITIVES`` diapositives. Le message donne le contrat attendu.
    """
    soup = BeautifulSoup(corps or "", "html.parser")
    sections = _sections_racines(soup)
    if not sections:
        raise ValidationError(
            "Une présentation attend des sections <section class=\"cover\"> "
            "(diapositive de titre) et <section class=\"slide\"> (une par "
            "diapositive) — aucune n'a été trouvée. Réécris contenu_html avec "
            "ces sections.",
            details={"sections_attendues": ["cover", "slide"]},
        )
    if len(sections) > MAX_DIAPOSITIVES:
        raise ValidationError(
            f"Présentation trop longue : {len(sections)} diapositives "
            f"(maximum {MAX_DIAPOSITIVES}).",
            details={"maximum": MAX_DIAPOSITIVES, "recu": len(sections)},
        )

    diapositives: list[Diapositive] = []
    for section in sections:
        classes = _classes(section)
        if "cover" in classes:
            titre_el = section.find(class_="cover-title") or section.find("h1")
            diapositives.append(
                Diapositive(
                    titre=_texte(titre_el),
                    surtitre=_texte(section.find(class_="cover-label")),
                    sous_titre=_texte(section.find(class_="cover-subtitle")),
                    meta=_texte(section.find(class_="cover-meta")),
                    est_titre=True,
                )
            )
            continue
        champs: dict[str, Any] = {"titre": "", "lead": "", "notes": "", "figure": None}
        blocs: list[Bloc] = []
        _parcourir(section, blocs, champs)
        diapositives.append(
            Diapositive(
                titre=str(champs["titre"]),
                lead=str(champs["lead"]),
                notes=str(champs["notes"]),
                blocs=tuple(blocs),
                figure=champs["figure"],
            )
        )
    return tuple(diapositives)


# --------------------------------------------------------------------------- contrôle qualité
def verifier_presentation(diapositives: tuple[Diapositive, ...]) -> list[str]:
    """Constats lisibles sur un deck interprété — l'agent corrige avant de livrer.

    Ces avertissements ne bloquent pas le rendu (sauf ce que ``rendre_pptx``
    refuse : trop de texte pour la zone) : ils disent ce qui rendra mal à
    l'écran — une diapositive sans titre, sept puces de 30 lignes, un tableau à
    neuf colonnes, un deck sans page de titre.
    """
    constats: list[str] = []
    if not diapositives:
        return ["Présentation vide : aucune diapositive."]
    if not diapositives[0].est_titre:
        constats.append(
            "Aucune diapositive de titre : commence par "
            "<section class='cover'> pour ouvrir la présentation."
        )
    if len(diapositives) > 25:
        constats.append(
            f"{len(diapositives)} diapositives : au-delà de 25, un deck se lit mal "
            "— regroupe ou supprime les diapositives faibles."
        )
    sans_notes: list[int] = []
    for index, diapositive in enumerate(diapositives, start=1):
        nom = f"Diapositive {index}" + (f" ({diapositive.titre})" if diapositive.titre else "")
        if diapositive.est_titre:
            if not diapositive.titre:
                constats.append(f"{nom} : titre de couverture vide.")
            continue
        if not diapositive.titre:
            constats.append(f"{nom} : aucun titre (h1/h2) — la diapositive est muette.")
        elif len(diapositive.titre) > TITRE_MAX:
            constats.append(
                f"{nom} : titre de {len(diapositive.titre)} caractères "
                f"(maximum conseillé {TITRE_MAX}) — raccourcis-le."
            )
        if len(diapositive.lead) > LEAD_MAX:
            constats.append(
                f"{nom} : message clé de {len(diapositive.lead)} caractères "
                f"(maximum conseillé {LEAD_MAX})."
            )
        puces = [item for bloc in diapositive.blocs if bloc.genre == "puces" for item in bloc.items]
        if len(puces) > PUCES_MAX:
            constats.append(
                f"{nom} : {len(puces)} puces (maximum {PUCES_MAX}) — une "
                "diapositive porte une idée, pas un document."
            )
        longues = [item for item in puces if len(item) > PUCE_MAX]
        if longues:
            constats.append(
                f"{nom} : {len(longues)} puce(s) de plus de {PUCE_MAX} caractères "
                "— coupe en deux puces ou résume."
            )
        for bloc in diapositive.blocs:
            if bloc.genre == "kpi" and len(bloc.kpis) > KPIS_MAX:
                constats.append(
                    f"{nom} : {len(bloc.kpis)} chiffres clés (maximum {KPIS_MAX}) "
                    "— garde les plus parlants."
                )
            if bloc.genre == "tableau" and bloc.tableau is not None:
                colonnes = len(bloc.tableau.entetes)
                if colonnes > 6:
                    constats.append(
                        f"{nom} : tableau de {colonnes} colonnes — illisible "
                        "projeté ; garde l'essentiel ou scinde."
                    )
                if len(bloc.tableau.lignes) > 10:
                    constats.append(
                        f"{nom} : tableau de {len(bloc.tableau.lignes)} lignes "
                        "— au-delà de 10, seules les premières sont lues."
                    )
        if not diapositive.blocs and diapositive.figure is None:
            constats.append(f"{nom} : aucun contenu (ni texte, ni figure).")
        if not diapositive.notes:
            sans_notes.append(index)
    if sans_notes:
        constats.append(
            f"{len(sans_notes)} diapositive(s) sans notes présentateur "
            "(<aside class='notes'>) — facultatif, mais utile pour l'oral."
        )
    return constats


# --------------------------------------------------------------------------- HTML du PDF jumeau
_CSS = Template(
    """
@page { size: 13.333in 7.5in; margin: 0; }
* { box-sizing: border-box; }
html, body { margin: 0; padding: 0; }
body { font-family: 'Segoe UI', Calibri, Arial, sans-serif; color: $texte;
       -webkit-print-color-adjust: exact; print-color-adjust: exact; }
.slide { width: 13.333in; height: 7.5in; padding: .5in .75in .55in;
         background: #FFFFFF; position: relative; overflow: hidden;
         break-after: page; page-break-after: always; display: flex;
         flex-direction: column; }
.slide:last-child { break-after: auto; page-break-after: auto; }
.slide::before { content: ''; position: absolute; top: 0; left: 0;
                 width: 100%; height: .09in; background: $primaire; }
.slide.titre { background: $primaire; padding: 1.6in .9in .8in; }
.slide.titre::before { background: $accent; width: 1.6in; height: .07in; top: 1.25in; }
.surtitre { font-size: 12pt; letter-spacing: .08em; text-transform: uppercase;
            font-weight: 600; color: $accent; margin-bottom: .18in; }
.titre { font-size: 30pt; line-height: 1.12; font-weight: 700; color: $primaire;
         margin: 0; max-width: 11.2in; }
.slide.titre .surtitre { color: rgba(255,255,255,.85); }
.slide.titre .titre { color: #FFFFFF; font-size: 40pt; max-width: 11in; }
.slide.titre .soustitre { color: rgba(255,255,255,.92); font-size: 18pt;
                          margin-top: .3in; max-width: 10in; }
.slide.titre .meta { position: absolute; left: .9in; right: .9in; bottom: .85in;
                     border-top: 1px solid rgba(255,255,255,.45); padding-top: .14in;
                     color: rgba(255,255,255,.88); font-size: 11pt; }
.filet { width: 1.1in; height: .045in; background: $accent; margin: .16in 0 .22in; }
.lead { font-size: 15pt; color: $muted; margin: 0 0 .26in; max-width: 11.2in; }
.corps { flex: 1; display: flex; gap: .45in; min-height: 0; }
.colonne-texte { flex: 1; min-width: 0; }
.avec-figure .colonne-texte { flex: 0 0 5.05in; }
.colonne-figure { flex: 1; display: flex; flex-direction: column;
                  align-items: center; justify-content: center; min-width: 0; }
.colonne-figure img { max-width: 100%; max-height: 4.75in; }
.colonne-figure figcaption { font-size: 9pt; color: $muted; margin-top: .08in;
                             text-align: center; }
.corps.figure-seule { display: flex; flex-direction: column; align-items: center;
                      justify-content: center; width: 100%; }
.figure-seule img { max-width: 100%; max-height: 4.5in; }
.figure-seule figcaption { font-size: 9pt; color: $muted; margin-top: .08in;
                           text-align: center; }
.corps h3 { font-size: 14pt; color: $accent; margin: .12in 0 .06in; }
.corps p { font-size: 16pt; line-height: 1.42; margin: 0 0 .14in; }
.corps ul, .corps ol { margin: 0 0 .12in; padding-left: .28in; }
.corps li { font-size: 16pt; line-height: 1.38; margin-bottom: .08in; }
.callout { border-left: 4px solid $accent; background: $encadre; padding: .14in .18in;
           font-size: 14pt; color: $texte; margin-top: .16in; }
.callout.success { border-color: #059669; background: #ECFDF5; }
.callout.warning { border-color: #D97706; background: #FFFBEB; }
.callout.danger { border-color: #DC2626; background: #FEF2F2; }
.kpis { display: flex; gap: .18in; margin: .2in 0; }
.kpi { flex: 1; background: $doux; border-top: 3px solid $accent; padding: .16in .12in; }
.kpi b { display: block; font-size: 26pt; color: $primaire; line-height: 1.1; }
.kpi span { display: block; font-size: 9pt; letter-spacing: .06em;
            text-transform: uppercase; color: $muted; margin-top: .05in; }
.tableau { width: 100%; border-collapse: collapse; margin: .16in 0; }
.tableau th { background: $primaire; color: #FFFFFF; font-size: 10.5pt;
              text-align: left; padding: .08in .1in; }
.tableau td { font-size: 10.5pt; padding: .07in .1in; border-bottom: 1px solid $bordure; }
.tableau tr:nth-child(even) td { background: $doux; }
.tableau .num { text-align: right; }
.pied { position: absolute; left: .75in; right: .75in; bottom: .3in;
        border-top: 1px solid $bordure; padding-top: .07in;
        display: flex; justify-content: space-between; font-size: 8.5pt; color: $muted; }
"""
)


def _echapper(valeur: str) -> str:
    return html.escape(valeur, quote=False)


def _bloc_html(bloc: Bloc) -> str:
    if bloc.genre == "sous_titre":
        return f"<h3>{_echapper(bloc.texte)}</h3>"
    if bloc.genre == "paragraphe":
        return f"<p>{_echapper(bloc.texte)}</p>"
    if bloc.genre == "puces":
        items = "".join(f"<li>{_echapper(item)}</li>" for item in bloc.items)
        return f"<ul>{items}</ul>"
    if bloc.genre == "encadre":
        classe = f"callout {bloc.variante}".strip()
        return f'<div class="{classe}">{_echapper(bloc.texte)}</div>'
    if bloc.genre == "kpi":
        cartes = "".join(
            f'<div class="kpi"><b>{_echapper(valeur)}</b>'
            f"<span>{_echapper(label)}</span></div>"
            for valeur, label in bloc.kpis
        )
        return f'<div class="kpis">{cartes}</div>'
    tableau = bloc.tableau
    if tableau is None:
        return ""
    entetes = "".join(f'<th class="{"num" if i in tableau.colonnes_num else ""}">'
                      f"{_echapper(texte)}</th>"
                      for i, texte in enumerate(tableau.entetes))
    lignes = "".join(
        "<tr>"
        + "".join(
            f'<td class="{"num" if i in tableau.colonnes_num else ""}">'
            f"{_echapper(valeur)}</td>"
            for i, valeur in enumerate(ligne)
        )
        + "</tr>"
        for ligne in tableau.lignes
    )
    return (
        '<table class="tableau"><thead><tr>'
        f"{entetes}</tr></thead><tbody>{lignes}</tbody></table>"
    )


def construire_html_diapositives(
    diapositives: tuple[Diapositive, ...],
    *,
    theme: str | Theme = "corporate",
    titre: str = "",
    auteur: str = "",
) -> str:
    """Assemble le HTML 16:9 du PDF jumeau — même contenu que le PPTX.

    Le HTML est **construit par le serveur** (le CSS ci-dessus n'est jamais écrit
    par l'agent) : il sert uniquement d'aperçu fidèle et d'impression.
    """
    t = get_theme(theme)
    total = len(diapositives)
    pages: list[str] = []
    for index, diapositive in enumerate(diapositives, start=1):
        if diapositive.est_titre:
            meta = (
                f'<div class="meta">{_echapper(diapositive.meta)}</div>'
                if diapositive.meta
                else ""
            )
            sous = (
                f'<div class="soustitre">{_echapper(diapositive.sous_titre)}</div>'
                if diapositive.sous_titre
                else ""
            )
            sur = (
                f'<div class="surtitre">{_echapper(diapositive.surtitre)}</div>'
                if diapositive.surtitre
                else ""
            )
            pages.append(
                f'<section class="slide titre">{sur}'
                f'<h1 class="titre">{_echapper(diapositive.titre)}</h1>{sous}{meta}</section>'
            )
            continue
        sur = (
            f'<div class="surtitre">{_echapper(diapositive.surtitre)}</div>'
            if diapositive.surtitre
            else ""
        )
        lead = f'<p class="lead">{_echapper(diapositive.lead)}</p>' if diapositive.lead else ""
        texte_blocs = "".join(
            _bloc_html(bloc) for bloc in diapositive.blocs
        )
        if diapositive.figure is not None:
            figure = diapositive.figure
            fig_html = (
                "<figure>"
                f'<img src="{html.escape(figure.src, quote=True)}" '
                f'alt="{html.escape(figure.libelle, quote=True)}">'
                + (
                    f"<figcaption>{_echapper(figure.libelle)}</figcaption>"
                    if figure.libelle
                    else ""
                )
                + "</figure>"
            )
            if texte_blocs:
                corps = (
                    '<div class="corps avec-figure">'
                    f'<div class="colonne-texte">{texte_blocs}</div>'
                    f'<div class="colonne-figure">{fig_html}</div></div>'
                )
            else:
                # Figure seule : pleine largeur, même règle que le PPTX.
                corps = f'<div class="corps figure-seule">{fig_html}</div>'
        else:
            corps = f'<div class="corps"><div class="colonne-texte">{texte_blocs}</div></div>'
        pages.append(
            f'<section class="slide">{sur}'
            f'<h1 class="titre">{_echapper(diapositive.titre)}</h1>'
            '<div class="filet"></div>'
            f'{lead}{corps}'
            f'<div class="pied"><span>{_echapper(titre)}</span>'
            f"<span>{index} / {total}</span></div></section>"
        )

    css = _CSS.substitute(
        texte=TEXTE,
        primaire=t.primary,
        accent=t.accent,
        doux=t.soft,
        encadre=t.callout,
        muted=MUTED,
        bordure=BORDURE,
    )
    return (
        '<!DOCTYPE html><html lang="fr"><head><meta charset="utf-8">'
        f"<title>{html.escape(titre)}</title>"
        f'<meta name="author" content="{html.escape(auteur)}">'
        f"<style>{css}</style></head><body>{''.join(pages)}</body></html>"
    )
