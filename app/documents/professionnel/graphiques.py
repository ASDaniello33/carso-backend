"""Génération de graphiques SVG pour un livrable professionnel (incrément 69).

Adaptation CARSO du module ``svg_charts.py`` fourni par l'équipe. Le module
d'origine était autonome et écrivait sur disque ; ici il devient une **brique du
moteur de rendu**, avec les mêmes règles que le reste de
``app.documents.professionnel`` :

- **aucun chemin disque ne sort d'ici** — ``render_chart`` retourne une chaîne
  SVG, rien d'autre (``save_svg``/``svg_to_png`` du module d'origine ne sont pas
  repris : le dépôt documentaire reste la seule écriture, AGENTS.md §2.5 et §7) ;
- **aucune exception avalée** — une spec invalide lève ``ValidationError`` avec
  un message actionnable, comme ``_preparer_document`` dans ``rendu.py`` ; jamais
  un graphique vide ni un dictionnaire ``succes: false`` que l'agent ignorerait ;
- **le SVG est construit par le serveur, jamais fourni par l'agent** — les textes
  traversent tous ``_esc`` (``html.escape``), donc un libellé ne peut ni fermer
  une balise ni injecter de script (AGENTS.md §7 et §9).

Trois familles, quatre types :

- ``bar``   — barres groupées (valeurs négatives acceptées : l'axe zéro est tracé) ;
- ``line``  — courbes avec points ;
- ``pie`` / ``donut`` — camembert et anneau (valeurs négatives refusées).

Le rendu est **déterministe** (palette fixe, graduations « rondes ») : la même
spec produit les mêmes octets, ce qui rend le graphique vérifiable par test.

Utilisation par le moteur : ``rendre_graphique(spec)`` puis insertion du SVG dans
le corps HTML par ``rendu.py``. Pour le DOCX, ``pdf_renderer.rasteriser_svg``
convertit ce SVG en PNG via le Chromium déjà requis par le PDF.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from html import escape
from typing import Any

from app.core.errors import ValidationError

__all__ = [
    "FONT",
    "HAUTEUR_DEFAUT",
    "LARGEUR_DEFAUT",
    "PALETTE",
    "TYPES_GRAPHIQUES",
    "bar_chart",
    "line_chart",
    "pie_chart",
    "render_chart",
]

#: Palette qualitative (mêmes teintes que le module d'origine, accordées aux thèmes).
PALETTE = [
    "#4E79A7",
    "#F28E2B",
    "#E15759",
    "#76B7B2",
    "#59A14F",
    "#EDC948",
    "#B07AA1",
    "#FF9DA7",
    "#9C755F",
    "#BAB0AC",
]

FONT = "Arial, Helvetica, sans-serif"

#: Vocabulaire fermé des types de graphique.
TYPES_GRAPHIQUES = ("bar", "line", "pie", "donut")

#: Taille par défaut (le moteur impose ses propres bornes : voir MIN_*).
LARGEUR_DEFAUT = 640
HAUTEUR_DEFAUT = 400

MIN_LARGEUR = 200
MIN_HAUTEUR = 150
MAX_LARGEUR = 2400
MAX_HAUTEUR = 1600


# --------------------------------------------------------------------------
# Utilitaires
# --------------------------------------------------------------------------
def _esc(texte: Any) -> str:
    """Échappe un texte pour le SVG : aucune injection XML possible."""
    return escape(str(texte), quote=True)


def _num(valeur: Any, nom: str) -> float:
    """Vérifie qu'une valeur est un nombre fini, sinon erreur explicite."""
    est_nombre = isinstance(valeur, (int, float)) and not isinstance(valeur, bool)
    if not est_nombre or not math.isfinite(valeur):
        raise ValidationError(
            f"Série/point {nom} : nombre fini attendu, reçu {valeur!r}."
        )
    return float(valeur)


def _fmt(valeur: float) -> str:
    """Formate un nombre court : 1,2 k, 3,4 M, sinon décimal à la française."""
    if abs(valeur) >= 1e9:
        return f"{valeur / 1e9:.1f}G"
    if abs(valeur) >= 1e6:
        return f"{valeur / 1e6:.1f}M"
    if abs(valeur) >= 1e4:
        return f"{valeur / 1e3:.1f}k"
    return f"{valeur:,.2f}".rstrip("0").rstrip(".").replace(",", " ")


def _tronquer(texte: str, max_caracteres: int) -> str:
    """Tronque un libellé trop long (avec ellipse explicite)."""
    max_caracteres = max(3, max_caracteres)
    return texte if len(texte) <= max_caracteres else texte[: max_caracteres - 1] + "…"


def _graduations(lo: float, hi: float, nombre: int = 5) -> list[float]:
    """Graduations « rondes » (1, 2, 2.5, 5 × 10^n) couvrant [lo, hi]."""
    if lo == hi:
        lo, hi = (lo - 1, hi + 1) if lo else (0.0, 1.0)
    brut = (hi - lo) / nombre
    magnitude = 10 ** math.floor(math.log10(brut))
    pas = next(m * magnitude for m in (1, 2, 2.5, 5, 10) if m * magnitude >= brut)
    debut = math.floor(lo / pas) * pas
    fin = math.ceil(hi / pas) * pas
    valeurs: list[float] = []
    courant = debut
    while courant <= fin + pas * 1e-9:
        valeurs.append(round(courant, 10))
        courant += pas
    return valeurs


def _dimensions(spec: Mapping[str, Any]) -> tuple[int, int]:
    """Lit et borne la taille demandée (bornes serveur, jamais un refus muet)."""

    def _entier(cle: str, defaut: int) -> int:
        valeur = spec.get(cle, defaut)
        try:
            return int(valeur)
        except (TypeError, ValueError) as exc:
            raise ValidationError(f"{cle} doit être un entier (reçu {valeur!r}).") from exc

    largeur = _entier("width", LARGEUR_DEFAUT)
    hauteur = _entier("height", HAUTEUR_DEFAUT)
    if not MIN_LARGEUR <= largeur <= MAX_LARGEUR:
        raise ValidationError(
            f"largeur hors bornes : {largeur} (attendu {MIN_LARGEUR}–{MAX_LARGEUR})."
        )
    if not MIN_HAUTEUR <= hauteur <= MAX_HAUTEUR:
        raise ValidationError(
            f"hauteur hors bornes : {hauteur} (attendu {MIN_HAUTEUR}–{MAX_HAUTEUR})."
        )
    return largeur, hauteur


def _open_svg(largeur: int, hauteur: int, titre: str, description: str) -> list[str]:
    """Ouvre le document SVG : fond blanc, titre accessible, rôle image."""
    libelle = description or titre or "Graphique"
    parties = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{largeur}" '
        f'height="{hauteur}" viewBox="0 0 {largeur} {hauteur}" role="img" '
        f'aria-label="{_esc(libelle)}" font-family="{FONT}">',
        f"<title>{_esc(titre or description or 'Graphique')}</title>",
        f'<rect width="{largeur}" height="{hauteur}" fill="#ffffff"/>',
    ]
    if titre:
        parties.append(
            f'<text x="{largeur / 2}" y="28" text-anchor="middle" '
            f'font-size="16" font-weight="bold" fill="#222">{_esc(titre)}</text>'
        )
    return parties


def _legendes(noms: Sequence[str], x: float, y: float, largeur: float) -> list[str]:
    """Légende horizontale centrée sous le graphique."""
    largeurs = [22 + len(_tronquer(n, 20)) * 7 + 16 for n in noms]
    cx = x + max(0.0, (largeur - sum(largeurs)) / 2)
    parties: list[str] = []
    for i, nom in enumerate(noms):
        couleur = PALETTE[i % len(PALETTE)]
        parties.append(
            f'<rect x="{cx:.1f}" y="{y - 10:.1f}" width="12" height="12" rx="2" '
            f'fill="{couleur}"/>'
        )
        parties.append(
            f'<text x="{cx + 18:.1f}" y="{y:.1f}" font-size="12" fill="#333">'
            f"{_esc(_tronquer(nom, 20))}</text>"
        )
        cx += largeurs[i]
    return parties


def _valider_series(
    labels: Sequence[Any], series: Mapping[str, Sequence[Any]]
) -> list[str]:
    """Valide labels/séries et retourne les noms de séries (ordre conservé)."""
    if not labels:
        raise ValidationError("Un graphique exige au moins un libellé (labels vide).")
    if not series:
        raise ValidationError("Un graphique à axes exige au moins une série (series vide).")
    for nom, valeurs in series.items():
        if len(valeurs) != len(labels):
            raise ValidationError(
                f"Série {nom!r} : {len(valeurs)} valeurs pour {len(labels)} labels."
            )
        for valeur in valeurs:
            _num(valeur, f"{nom!r}")
    return list(series)


# --------------------------------------------------------------------------
# Graphiques à axes (barres groupées / courbes)
# --------------------------------------------------------------------------
def _graphique_axes(
    type_graphique: str,
    labels: Sequence[Any],
    series: Mapping[str, Sequence[Any]],
    titre: str,
    largeur: int,
    hauteur: int,
    y_label: str,
    description: str,
) -> str:
    noms = _valider_series(labels, series)
    avec_legende = len(noms) > 1

    m_gauche, m_droite = 64, 20
    m_haut = 48 if titre else 20
    m_bas = 50 + (26 if avec_legende else 0)
    pw, ph = largeur - m_gauche - m_droite, hauteur - m_haut - m_bas

    valeurs = [float(v) for vs in series.values() for v in vs]
    ticks = _graduations(min(0.0, min(valeurs)), max(0.0, max(valeurs)))
    lo, hi = ticks[0], ticks[-1]

    def y(valeur: float) -> float:
        return m_haut + ph - (valeur - lo) / (hi - lo) * ph

    parties = _open_svg(largeur, hauteur, titre, description)

    # Grille et graduations de l'axe Y
    for tick in ticks:
        yy = y(tick)
        parties.append(
            f'<line x1="{m_gauche}" y1="{yy:.1f}" x2="{m_gauche + pw}" y2="{yy:.1f}" '
            f'stroke="#e6e6e6"/>'
        )
        parties.append(
            f'<text x="{m_gauche - 8}" y="{yy + 4:.1f}" text-anchor="end" '
            f'font-size="11" fill="#555">{_fmt(tick)}</text>'
        )

    if y_label:
        cy = m_haut + ph / 2
        parties.append(
            f'<text x="16" y="{cy:.1f}" font-size="12" fill="#555" text-anchor="middle" '
            f'transform="rotate(-90 16 {cy:.1f})">{_esc(y_label)}</text>'
        )

    # Libellés de l'axe X (pas de chevauchement : on saute des libellés si besoin)
    total = len(labels)
    creneau = pw / total
    max_caracteres = int(creneau / 7)
    pas = max(1, math.ceil(total / max(1, pw // 40)))
    for i, label in enumerate(labels):
        if i % pas:
            continue
        cx = m_gauche + creneau * (i + 0.5)
        parties.append(
            f'<text x="{cx:.1f}" y="{m_haut + ph + 18:.1f}" text-anchor="middle" '
            f'font-size="11" fill="#333">'
            f"{_esc(_tronquer(str(label), max_caracteres * pas))}</text>"
        )

    # Séries
    if type_graphique == "bar":
        groupe = creneau * 0.8
        largeur_barre = groupe / len(noms)
        for si, nom in enumerate(noms):
            couleur = PALETTE[si % len(PALETTE)]
            for i, valeur in enumerate(series[nom]):
                v = float(valeur)
                x = m_gauche + creneau * i + creneau * 0.1 + largeur_barre * si
                haut, h = min(y(0), y(v)), abs(y(v) - y(0))
                parties.append(
                    f'<rect x="{x:.1f}" y="{haut:.1f}" width="{largeur_barre * 0.9:.1f}" '
                    f'height="{max(h, 0.5):.1f}" fill="{couleur}">'
                    f"<title>{_esc(nom)} — {_esc(labels[i])} : {_fmt(v)}</title></rect>"
                )
    else:  # line
        for si, nom in enumerate(noms):
            couleur = PALETTE[si % len(PALETTE)]
            points = [
                (m_gauche + creneau * (i + 0.5), y(float(v)))
                for i, v in enumerate(series[nom])
            ]
            parties.append(
                f'<polyline points="{" ".join(f"{a:.1f},{b:.1f}" for a, b in points)}" '
                f'fill="none" stroke="{couleur}" stroke-width="2.5" '
                f'stroke-linejoin="round" stroke-linecap="round"/>'
            )
            for i, (px, py) in enumerate(points):
                parties.append(
                    f'<circle cx="{px:.1f}" cy="{py:.1f}" r="3.5" fill="{couleur}">'
                    f"<title>{_esc(nom)} — {_esc(labels[i])} : "
                    f"{_fmt(float(series[nom][i]))}</title></circle>"
                )

    # Axe zéro (visible même avec des valeurs négatives)
    parties.append(
        f'<line x1="{m_gauche}" y1="{y(0):.1f}" x2="{m_gauche + pw}" y2="{y(0):.1f}" '
        f'stroke="#333"/>'
    )

    if avec_legende:
        parties += _legendes(noms, m_gauche, hauteur - 14, pw)

    parties.append("</svg>")
    return "\n".join(parties)


def bar_chart(
    labels: Sequence[Any],
    series: Mapping[str, Sequence[Any]],
    titre: str = "",
    largeur: int = LARGEUR_DEFAUT,
    hauteur: int = HAUTEUR_DEFAUT,
    y_label: str = "",
    description: str = "",
) -> str:
    """Barres groupées (une barre par série et par libellé)."""
    return _graphique_axes("bar", labels, series, titre, largeur, hauteur, y_label, description)


def line_chart(
    labels: Sequence[Any],
    series: Mapping[str, Sequence[Any]],
    titre: str = "",
    largeur: int = LARGEUR_DEFAUT,
    hauteur: int = HAUTEUR_DEFAUT,
    y_label: str = "",
    description: str = "",
) -> str:
    """Courbes (une polyligne et des points par série)."""
    return _graphique_axes("line", labels, series, titre, largeur, hauteur, y_label, description)


# --------------------------------------------------------------------------
# Camembert / anneau
# --------------------------------------------------------------------------
def pie_chart(
    labels: Sequence[Any],
    valeurs: Sequence[Any],
    titre: str = "",
    largeur: int = 560,
    hauteur: int = 360,
    donut: bool = False,
    description: str = "",
) -> str:
    """Camembert (ou anneau) avec légende et pourcentages."""
    if not labels or len(labels) != len(valeurs):
        raise ValidationError(
            "Camembert : labels et values doivent avoir la même taille (non nulle), "
            f"reçu {len(labels)} labels et {len(valeurs)} values."
        )
    vals = [_num(v, str(libelle)) for v, libelle in zip(valeurs, labels, strict=True)]
    if any(v < 0 for v in vals):
        raise ValidationError("Un camembert n'accepte pas de valeurs négatives.")
    total = sum(vals)
    if total <= 0:
        raise ValidationError("La somme des valeurs du camembert doit être > 0.")

    parties = _open_svg(largeur, hauteur, titre, description)
    haut = 48 if titre else 16
    rayon = min((largeur * 0.55) / 2, (hauteur - haut - 16) / 2)
    cx, cy = 16 + rayon, haut + (hauteur - haut) / 2 - 4

    angle = -math.pi / 2  # départ à midi
    for i, (label, v) in enumerate(zip(labels, vals, strict=True)):
        if v == 0:
            continue
        couleur = PALETTE[i % len(PALETTE)]
        pourcentage = v / total * 100
        bulle = f"<title>{_esc(label)} : {_fmt(v)} ({pourcentage:.1f} %)</title>"
        if v == total:  # une seule part : cercle complet
            parties.append(
                f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{rayon:.1f}" fill="{couleur}">'
                f"{bulle}</circle>"
            )
            break
        fin = angle + v / total * 2 * math.pi
        x1, y1 = cx + rayon * math.cos(angle), cy + rayon * math.sin(angle)
        x2, y2 = cx + rayon * math.cos(fin), cy + rayon * math.sin(fin)
        grand = 1 if fin - angle > math.pi else 0
        parties.append(
            f'<path d="M{cx:.1f},{cy:.1f} L{x1:.1f},{y1:.1f} '
            f'A{rayon:.1f},{rayon:.1f} 0 {grand} 1 {x2:.1f},{y2:.1f} Z" '
            f'fill="{couleur}" stroke="#fff" stroke-width="1.5">{bulle}</path>'
        )
        angle = fin

    if donut:
        parties.append(
            f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{rayon * 0.55:.1f}" fill="#fff"/>'
        )
        parties.append(
            f'<text x="{cx:.1f}" y="{cy + 6:.1f}" text-anchor="middle" font-size="16" '
            f'font-weight="bold" fill="#333">{_fmt(total)}</text>'
        )

    # Légende verticale à droite, avec pourcentages
    lx = cx + rayon + 30
    ly = cy - (len(labels) * 22) / 2 + 12
    for i, (label, v) in enumerate(zip(labels, vals, strict=True)):
        couleur = PALETTE[i % len(PALETTE)]
        yy = ly + i * 22
        parties.append(
            f'<rect x="{lx:.1f}" y="{yy - 10:.1f}" width="12" height="12" rx="2" '
            f'fill="{couleur}"/>'
        )
        parties.append(
            f'<text x="{lx + 18:.1f}" y="{yy:.1f}" font-size="12" fill="#333">'
            f"{_esc(_tronquer(str(label), 22))} — {v / total * 100:.1f} %</text>"
        )

    parties.append("</svg>")
    return "\n".join(parties)


# --------------------------------------------------------------------------
# Point d'entrée unique (spec vient du tool, jamais de l'agent directement)
# --------------------------------------------------------------------------
def render_chart(spec: Mapping[str, Any]) -> str:
    """Génère le SVG d'une spec de graphique.

    ``spec`` = {
        "type": "bar" | "line" | "pie" | "donut",
        "title": "...", "labels": [...],
        "series": {"Nom": [...]},   # bar / line
        "values": [...],            # pie / donut
        "width": 640, "height": 400, "y_label": "..."
    }

    Raises:
        ValidationError: type inconnu, données désalignées, valeurs non finies
            ou négatives dans un camembert, taille hors bornes.
    """
    type_graphique = str(spec.get("type", "")).strip().lower()
    if type_graphique not in TYPES_GRAPHIQUES:
        raise ValidationError(
            f"Type de graphique inconnu : {spec.get('type')!r}.",
            details={"types_disponibles": list(TYPES_GRAPHIQUES)},
        )
    largeur, hauteur = _dimensions(spec)
    titre = str(spec.get("title") or "")
    description = str(spec.get("description") or "")
    labels = spec.get("labels") or []

    if type_graphique in ("bar", "line"):
        fonction = bar_chart if type_graphique == "bar" else line_chart
        return fonction(
            labels,
            spec.get("series") or {},
            titre=titre,
            largeur=largeur,
            hauteur=hauteur,
            y_label=str(spec.get("y_label") or ""),
            description=description,
        )
    return pie_chart(
        labels,
        spec.get("values") or [],
        titre=titre,
        largeur=largeur,
        hauteur=hauteur,
        donut=type_graphique == "donut",
        description=description,
    )
