"""Illustrations vectorielles **dessinées par le serveur** (incrément 73).

Pourquoi ce module : jusqu'ici l'agent devait écrire lui-même des `<svg>` pour
illustrer un document. C'est la source d'erreurs de design la plus coûteuse —
coordonnées qui se chevauchent, texte qui déborde, couleurs hors thème. Ici,
l'agent choisit une **composition** et fournit des **libellés courts** ; la
géométrie, les espacements, les tailles de police et les couleurs du thème sont
fixés par le serveur.

Mêmes règles que ``graphiques.py`` :

- aucun chemin disque, aucune écriture : ``render_illustration`` retourne du SVG ;
- aucune exception avalée : une spec invalide lève ``ValidationError`` avec un
  message actionnable (nombre d'items, longueur de libellé, icône inconnue) ;
- tout texte traverse ``html.escape`` : un libellé ne peut pas fermer une balise.

Vocabulaire fermé : ``icones``, ``etapes``, ``cycle``, ``pyramide``, ``frise``,
``comparaison``. Le rendu est **déterministe** : même spec, mêmes octets.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from html import escape
from typing import Any

from app.core.errors import ValidationError

from .themes import BORDURE, MUTED, TEXTE, Theme, get_theme

__all__ = [
    "ICONES",
    "TYPES_ILLUSTRATIONS",
    "render_illustration",
]

#: Vocabulaire fermé des compositions.
TYPES_ILLUSTRATIONS = ("icones", "etapes", "cycle", "pyramide", "frise", "comparaison")

#: Largeur logique de toutes les illustrations (le HTML les met à l'échelle).
LARGEUR = 640

#: Icônes au trait, grille 24 × 24, dessinées par primitives (aucune dépendance,
#: aucune ressource distante). Le nom est le seul contrat exposé à l'agent.
ICONES: dict[str, str] = {
    "personne": (
        '<circle cx="12" cy="8" r="3.4"/>'
        '<path d="M5 20c0-3.9 3.1-7 7-7s7 3.1 7 7"/>'
    ),
    "equipe": (
        '<circle cx="9" cy="8" r="3.2"/>'
        '<path d="M3 20c0-3.3 2.7-6 6-6s6 2.7 6 6"/>'
        '<circle cx="17" cy="9" r="2.4"/>'
        '<path d="M15.5 14.3c2.9.3 5.5 2.6 5.5 5.7"/>'
    ),
    "cible": (
        '<circle cx="12" cy="12" r="8"/>'
        '<circle cx="12" cy="12" r="4"/>'
        '<circle cx="12" cy="12" r="0.9" fill="currentColor"/>'
    ),
    "calendrier": (
        '<rect x="3.5" y="5" width="17" height="15" rx="2"/>'
        '<path d="M3.5 10h17M8 3v4M16 3v4"/>'
    ),
    "graphique": (
        '<path d="M4 20V4M4 20h16"/>'
        '<rect x="7" y="12" width="3" height="5"/>'
        '<rect x="12" y="8" width="3" height="9"/>'
        '<rect x="17" y="5" width="3" height="12"/>'
    ),
    "check": '<path d="M4 12.5l5 5 11-11"/>',
    "croix": '<path d="M6 6l12 12M18 6L6 18"/>',
    "livre": (
        '<path d="M4 5.5c3-1.2 5.5-1.2 8 0 2.5-1.2 5-1.2 8 0v13c-3-1.2-5.5-1.2-8 0'
        '-2.5-1.2-5-1.2-8 0z"/>'
        '<path d="M12 5.5v13"/>'
    ),
    "horloge": '<circle cx="12" cy="12" r="8.5"/><path d="M12 7v5l3.5 2"/>',
    "carte": (
        '<path d="M3.5 6.5l5-2 7 2 5-2v13l-5 2-7-2-5 2z"/>'
        '<path d="M8.5 4.5v13M15.5 6.5v13"/>'
    ),
    "prix": '<circle cx="12" cy="9" r="5"/><path d="M9 13.5l-2 7 5-2.5 5 2.5-2-7"/>',
    "bouclier": (
        '<path d="M12 3l7 3v6c0 4.5-3 7.5-7 9-4-1.5-7-4.5-7-9V6z"/>'
        '<path d="M9 12l2 2 4-4"/>'
    ),
    "ampoule": (
        '<path d="M9 17h6M10 20h4"/>'
        '<path d="M12 3a6 6 0 0 0-3.5 10.9c.6.5 1 1.2 1 2.1h5c0-.9.4-1.6 1-2.1'
        'A6 6 0 0 0 12 3z"/>'
    ),
    "lien": (
        '<rect x="2.5" y="9" width="10" height="6" rx="3"/>'
        '<rect x="11.5" y="9" width="10" height="6" rx="3"/>'
        '<path d="M8 12h8"/>'
    ),
    "dossier": '<path d="M3.5 6.5h6l2 2h9v11h-17z"/>',
    "mail": '<rect x="3" y="5.5" width="18" height="13" rx="2"/><path d="M3.5 7l8.5 6 8.5-6"/>',
    "telephone": (
        '<path d="M7 3.5l3 4-2 2c1 2.5 3 4.5 5.5 5.5l2-2 4 3-1.5 2.5'
        'c-.8 1.3-2.3 1.8-3.7 1.4C8.6 18.5 5.5 15.4 4.1 9.7 3.7 8.3 4.2 6.8 5.5 6z"/>'
    ),
    "globe": (
        '<circle cx="12" cy="12" r="8.5"/>'
        '<path d="M3.5 12h17"/>'
        '<path d="M12 3.5c2.5 2.3 4 5.2 4 8.5s-1.5 6.2-4 8.5c-2.5-2.3-4-5.2-4-8.5'
        's1.5-6.2 4-8.5z"/>'
    ),
    "fleche": '<path d="M4 12h15"/><path d="M14 7l5 5-5 5"/>',
    "etoile": (
        '<path d="M12 3.5l2.6 5.3 5.9.9-4.3 4.1 1 5.8-5.2-2.7-5.2 2.7 1-5.8'
        'L3.5 9.7l5.9-.9z"/>'
    ),
    "alerte": (
        '<path d="M12 3.5L21 19H3z"/><path d="M12 9v4"/>'
        '<circle cx="12" cy="16.4" r="0.9" fill="currentColor"/>'
    ),
}

#: Préfixe des marqueurs : une seule définition par document, même forme.
_ID_FLECHE = "ill-fleche"

_TITRE_MAX = 60
_TEXTE_MAX = 90
_ITEMS: dict[str, tuple[int, int]] = {
    "icones": (2, 4),
    "etapes": (2, 5),
    "cycle": (2, 5),
    "pyramide": (2, 4),
    "frise": (2, 6),
    "comparaison": (2, 2),
}


@dataclass(frozen=True, slots=True)
class _Style:
    """Teintes du thème, calculées une fois par illustration."""

    primaire: str
    accent: str
    doux: str
    encadre: str


def _style(theme: str | Theme | None) -> _Style:
    t = get_theme(theme)
    return _Style(t.primary, t.accent, t.soft, t.callout)


def _esc(valeur: Any) -> str:
    return escape(str(valeur), quote=True)


def _texte(
    x: float,
    y: float,
    valeur: str,
    *,
    taille: float = 12,
    couleur: str = TEXTE,
    gras: bool = False,
    ancre: str = "middle",
) -> str:
    poids = ' font-weight="bold"' if gras else ""
    return (
        f'<text x="{x:.1f}" y="{y:.1f}" font-family="Arial, Helvetica, sans-serif" '
        f'font-size="{taille}" fill="{couleur}" text-anchor="{ancre}"{poids}>'
        f"{_esc(valeur)}</text>"
    )


def _lignes(texte: str, max_caracteres: int) -> list[str]:
    """Découpe un libellé en lignes courtes, sans couper un mot si possible."""
    mots = str(texte).split()
    lignes: list[str] = []
    courante = ""
    for mot in mots:
        candidat = f"{courante} {mot}".strip()
        if courante and len(candidat) > max_caracteres:
            lignes.append(courante)
            courante = mot
        else:
            courante = candidat
    if courante:
        lignes.append(courante)
    return lignes or [""]


def _texte_multi(
    x: float,
    y: float,
    valeur: str,
    *,
    max_caracteres: int,
    taille: float = 10,
    couleur: str = MUTED,
    gras: bool = False,
    interligne: float = 13,
    max_lignes: int = 2,
    ancre: str = "middle",
) -> str:
    toutes = _lignes(valeur, max_caracteres)
    lignes = toutes[:max_lignes]
    if len(toutes) > max_lignes and lignes:
        # Repli explicite : le texte reste lisible, l'ellipse dit qu'il est coupé.
        # Les bornes de ``_libelle`` évitent normalement ce cas.
        lignes[-1] = lignes[-1][: max(1, max_caracteres - 1)].rstrip() + "…"
    poids = ' font-weight="bold"' if gras else ""
    tspans = "".join(
        f'<tspan x="{x:.1f}" dy="{0 if index == 0 else interligne}">{_esc(ligne)}</tspan>'
        for index, ligne in enumerate(lignes)
    )
    return (
        f'<text x="{x:.1f}" y="{y:.1f}" font-family="Arial, Helvetica, sans-serif" '
        f'font-size="{taille}" fill="{couleur}" text-anchor="{ancre}"{poids}>'
        f"{tspans}</text>"
    )


def _carte(x: float, y: float, largeur: float, hauteur: float, s: _Style) -> str:
    return (
        f'<rect x="{x:.1f}" y="{y:.1f}" width="{largeur:.1f}" height="{hauteur:.1f}" '
        f'rx="10" fill="{s.doux}" stroke="{BORDURE}" stroke-width="1"/>'
    )


def _icone(nom: str, x: float, y: float, taille: float, couleur: str) -> str:
    chemin = ICONES[nom]
    echelle = taille / 24
    # L'épaisseur est pré-découpée : après mise à l'échelle elle vaut 1,8 px.
    epaisseur = 1.8 / echelle
    return (
        f'<g transform="translate({x:.1f} {y:.1f}) scale({echelle:.4f})" fill="none" '
        f'stroke="{couleur}" stroke-width="{epaisseur:.3f}" stroke-linecap="round" '
        f'stroke-linejoin="round" color="{couleur}">{chemin}</g>'
    )


def _svg(hauteur: int, contenu: str, titre: str, accent: str) -> str:
    # ``context-stroke`` (SVG 2) n'est pas garanti par resvg : la couleur du
    # marqueur est explicite, donc identique dans Chromium et dans le PNG.
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {LARGEUR} {hauteur}" '
        f'width="{LARGEUR}" height="{hauteur}" role="img">'
        f"<title>{_esc(titre)}</title>"
        "<defs>"
        f'<marker id="{_ID_FLECHE}" viewBox="0 0 10 10" refX="9" refY="5" '
        f'markerWidth="6" markerHeight="6" orient="auto-start-reverse">'
        f'<path d="M0 0L10 5L0 10z" fill="{accent}"/>'
        "</marker>"
        "</defs>"
        f"{contenu}</svg>"
    )


# --------------------------------------------------------------------------- spec
def _spec(spec: Mapping[str, Any]) -> tuple[str, list[Mapping[str, Any]], str]:
    if not isinstance(spec, Mapping):
        raise ValidationError("Illustration : spec attendue (objet).")
    type_ = str(spec.get("type") or "").strip().lower()
    if type_ not in TYPES_ILLUSTRATIONS:
        raise ValidationError(
            f"Type d'illustration inconnu : {type_!r}",
            details={"types_disponibles": list(TYPES_ILLUSTRATIONS)},
        )
    brut = spec.get("items")
    if not isinstance(brut, Sequence) or isinstance(brut, (str, bytes)):
        raise ValidationError(f"Illustration {type_} : 'items' doit être une liste.")
    minimum, maximum = _ITEMS[type_]
    if not minimum <= len(brut) <= maximum:
        raise ValidationError(
            f"Illustration {type_} : {minimum} à {maximum} items attendus, reçu {len(brut)}.",
            details={"minimum": minimum, "maximum": maximum, "recu": len(brut)},
        )
    items = [item for item in brut if isinstance(item, Mapping)]
    if len(items) != len(brut):
        raise ValidationError(f"Illustration {type_} : chaque item doit être un objet.")
    titre = str(spec.get("titre") or spec.get("title") or "Illustration")
    return type_, items, titre[:_TITRE_MAX]


def _libelle(
    item: Mapping[str, Any],
    cle: str,
    *,
    maximum: int,
    defaut: str | None = None,
) -> str:
    valeur = str(item.get(cle) or defaut or "").strip()
    if len(valeur) > maximum:
        raise ValidationError(
            f"Libellé trop long ({cle} : {len(valeur)} caractères, maximum {maximum}) : "
            f"« {valeur[: maximum]}… » — raccourcis-le, la mise en page est fixe.",
            details={"champ": cle, "maximum": maximum, "recu": len(valeur)},
        )
    return valeur


def _icone_item(item: Mapping[str, Any], defaut: str = "check") -> str:
    nom = str(item.get("icone") or item.get("icon") or defaut).strip().lower()
    if nom not in ICONES:
        raise ValidationError(
            f"Icône inconnue : {nom!r}",
            details={"icones_disponibles": sorted(ICONES)},
        )
    return nom


# --------------------------------------------------------------------------- compositions
def _icones(items: list[Mapping[str, Any]], s: _Style) -> str:
    marge, ecart, hauteur = 8, 16, 152
    largeur = (LARGEUR - 2 * marge - ecart * (len(items) - 1)) / len(items)
    morceaux: list[str] = []
    for index, item in enumerate(items):
        x = marge + index * (largeur + ecart)
        titre = _libelle(item, "titre", maximum=28, defaut="")
        texte = _libelle(item, "texte", maximum=52)
        morceaux.append(_carte(x, 6, largeur, hauteur - 12, s))
        morceaux.append(f'<circle cx="{x + largeur / 2:.1f}" cy="40" r="22" fill="{s.encadre}"/>')
        morceaux.append(
            _icone(_icone_item(item), x + largeur / 2 - 12, 28, 24, s.accent)
        )
        morceaux.append(
            _texte_multi(
                x + largeur / 2,
                82,
                titre,
                max_caracteres=20,
                taille=13,
                couleur=s.primaire,
                gras=True,
                interligne=15,
            )
        )
        morceaux.append(
            _texte_multi(
                x + largeur / 2,
                108,
                texte,
                max_caracteres=26,
                taille=10,
                interligne=12,
                max_lignes=2,
            )
        )
    return _svg(hauteur, "".join(morceaux), "Illustration", s.accent)


def _etapes(items: list[Mapping[str, Any]], s: _Style, *, boucle: bool = False) -> str:
    marge, hauteur = 12, 196 if boucle else 168
    largeur_utile = LARGEUR - 2 * marge
    pas = largeur_utile / max(1, len(items) - 1) if len(items) > 1 else 0
    xs = [marge + index * pas for index in range(len(items))]
    y_noeud = 40
    morceaux: list[str] = []

    # Ligne de liaison (ou boucle de retour pour un cycle).
    if len(xs) > 1:
        morceaux.append(
            f'<line x1="{xs[0]:.1f}" y1="{y_noeud}" x2="{xs[-1]:.1f}" y2="{y_noeud}" '
            f'stroke="{BORDURE}" stroke-width="2"/>'
        )
    if boucle:
        retour = f"M{xs[-1]:.1f} {y_noeud + 20}V{y_noeud + 62}H{xs[0]:.1f}V{y_noeud + 22}"
        morceaux.append(
            f'<path d="{retour}" fill="none" stroke="{s.accent}" stroke-width="2" '
            f'stroke-dasharray="6 5" marker-end="url(#{_ID_FLECHE})"/>'
        )

    for index, (item, x) in enumerate(zip(items, xs, strict=True)):
        titre = _libelle(item, "titre", maximum=34)
        texte = _libelle(item, "texte", maximum=64)
        morceaux.append(f'<circle cx="{x:.1f}" cy="{y_noeud}" r="18" fill="{s.accent}"/>')
        morceaux.append(
            _texte(x, y_noeud + 4.5, str(index + 1), taille=13, couleur="#FFFFFF", gras=True)
        )
        morceaux.append(
            _texte_multi(
                x,
                84,
                titre,
                max_caracteres=20,
                taille=12.5,
                couleur=s.primaire,
                gras=True,
                interligne=15,
            )
        )
        morceaux.append(
            _texte_multi(
                x,
                112,
                texte,
                max_caracteres=24,
                taille=10,
                interligne=12,
                max_lignes=3,
            )
        )
    return _svg(hauteur, "".join(morceaux), "Illustration", s.accent)


def _pyramide(items: list[Mapping[str, Any]], s: _Style) -> str:
    hauteur, largeur_max = 258, LARGEUR - 40
    niveaux = len(items)
    hauteur_niveau, ecart = 58, 5
    morceaux: list[str] = []
    centre = LARGEUR / 2
    for index, item in enumerate(items):
        titre = _libelle(item, "titre", maximum=30)
        texte = _libelle(item, "texte", maximum=44)
        # Le sommet est le plus étroit : la largeur croît vers le bas.
        largeur = largeur_max * (0.4 + 0.6 * index / max(1, niveaux - 1))
        y = 8 + index * (hauteur_niveau + ecart)
        x1, x2 = centre - largeur / 2, centre + largeur / 2
        retrait = largeur * 0.08
        opacity = 1 - index * 0.16
        morceaux.append(
            f'<polygon points="{x1 + retrait:.1f},{y:.1f} {x2 - retrait:.1f},{y:.1f} '
            f'{x2:.1f},{y + hauteur_niveau:.1f} {x1:.1f},{y + hauteur_niveau:.1f}" '
            f'fill="{s.primaire}" fill-opacity="{opacity:.2f}"/>'
        )
        morceaux.append(
            _texte(
                centre,
                y + (24 if texte else hauteur_niveau / 2 + 4),
                titre,
                taille=13,
                couleur="#FFFFFF",
                gras=True,
            )
        )
        if texte:
            morceaux.append(
                _texte_multi(
                    centre,
                    y + 41,
                    texte,
                    max_caracteres=46,
                    taille=9,
                    couleur="#FFFFFF",
                    max_lignes=1,
                )
            )
    return _svg(hauteur, "".join(morceaux), "Illustration", s.accent)


def _frise(items: list[Mapping[str, Any]], s: _Style) -> str:
    hauteur, marge = 190, 34
    largeur_utile = LARGEUR - 2 * marge
    pas = largeur_utile / max(1, len(items) - 1)
    y_ligne = 96
    morceaux: list[str] = [
        f'<line x1="{marge}" y1="{y_ligne}" x2="{LARGEUR - marge}" y2="{y_ligne}" '
        f'stroke="{BORDURE}" stroke-width="3" stroke-linecap="round"/>'
    ]
    for index, item in enumerate(items):
        x = marge + index * pas
        date = _libelle(item, "date", maximum=18)
        titre = _libelle(item, "titre", maximum=26)
        texte = _libelle(item, "texte", maximum=60)
        morceaux.append(
            f'<circle cx="{x:.1f}" cy="{y_ligne}" r="9" fill="{s.accent}" '
            f'stroke="#FFFFFF" stroke-width="3"/>'
        )
        if date:
            morceaux.append(
                _texte(x, y_ligne - 22, date, taille=11, couleur=s.accent, gras=True)
            )
        morceaux.append(
            _texte_multi(
                x,
                y_ligne + 32,
                titre,
                max_caracteres=16,
                taille=12,
                couleur=s.primaire,
                gras=True,
                interligne=14,
            )
        )
        morceaux.append(
            _texte_multi(
                x,
                y_ligne + 52,
                texte,
                max_caracteres=20,
                taille=9.5,
                interligne=11,
                max_lignes=3,
            )
        )
    return _svg(hauteur, "".join(morceaux), "Illustration", s.accent)


def _comparaison(items: list[Mapping[str, Any]], s: _Style) -> str:
    hauteur, marge, ecart = 236, 8, 20
    largeur = (LARGEUR - 2 * marge - ecart) / 2
    morceaux: list[str] = []
    for index, item in enumerate(items):
        x = marge + index * (largeur + ecart)
        titre = _libelle(item, "titre", maximum=30)
        coche = _icone_item(item, defaut="check")
        points = item.get("points") or []
        if not isinstance(points, Sequence) or isinstance(points, (str, bytes)):
            raise ValidationError("Illustration comparaison : 'points' doit être une liste.")
        if len(points) > 5:
            raise ValidationError(
                "Illustration comparaison : 5 points maximum par colonne "
                f"({len(points)} reçus).",
                details={"maximum": 5, "recu": len(points)},
            )
        if any(not isinstance(point, str) for point in points):
            raise ValidationError(
                "Illustration comparaison : chaque point doit être un texte court."
            )
        morceaux.append(_carte(x, 6, largeur, hauteur - 12, s))
        morceaux.append(
            f'<rect x="{x:.1f}" y="6" width="{largeur:.1f}" height="44" rx="10" '
            f'fill="{s.primaire if index == 0 else s.accent}"/>'
        )
        morceaux.append(
            _texte(x + largeur / 2, 34, titre, taille=13, couleur="#FFFFFF", gras=True)
        )
        for rang, point in enumerate(points):
            texte = _libelle({"point": point}, "point", maximum=44)
            y = 74 + rang * 30
            morceaux.append(_icone(coche, x + 22, y - 11, 16, s.accent))
            morceaux.append(
                _texte_multi(
                    x + 48,
                    y,
                    texte,
                    max_caracteres=42,
                    taille=10.5,
                    couleur=TEXTE,
                    ancre="start",
                    max_lignes=1,
                )
            )
    return _svg(hauteur, "".join(morceaux), "Illustration", s.accent)


def render_illustration(spec: Mapping[str, Any], theme: str | Theme | None = None) -> str:
    """Construit le SVG d'une illustration à partir de sa spec.

    Args:
        spec: ``{"type", "titre"?, "legende"?, "items": [...]}``. Chaque item
            porte ``titre``, ``texte`` et, selon la composition, ``icone``,
            ``date`` ou ``points``.
        theme: nom du thème ou ``Theme`` — les couleurs en viennent.

    Returns:
        Le SVG, ``viewBox`` 0 0 640 h, prêt à être inséré dans le corps.

    Raises:
        ValidationError: type inconnu, nombre d'items hors bornes, libellé trop
            long ou icône inconnue. Le message dit quoi corriger.
    """
    type_, items, titre = _spec(spec)
    s = _style(theme)
    if type_ == "icones":
        contenu = _icones(items, s)
    elif type_ == "etapes":
        contenu = _etapes(items, s)
    elif type_ == "cycle":
        contenu = _etapes(items, s, boucle=True)
    elif type_ == "pyramide":
        contenu = _pyramide(items, s)
    elif type_ == "frise":
        contenu = _frise(items, s)
    else:
        contenu = _comparaison(items, s)
    # Le <title> accessible porte le titre déclaré (l'agent en fournit un).
    return contenu.replace("<title>Illustration</title>", f"<title>{_esc(titre)}</title>", 1)
