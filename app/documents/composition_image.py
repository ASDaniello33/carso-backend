"""Composition d'une image CARSO à partir d'un **contenu**, côté serveur (INC-136).

Constat de terrain (INC-135) : un modèle de langage écrit un SVG syntaxiquement
correct et visuellement pauvre — textes qui débordent, tailles incohérentes,
contraste faible, éléments collés aux bords. Lui demander « fais mieux » ne rend
pas la qualité prévisible.

La stratégie inverse est appliquée ici : **le serveur possède la mise en page**
(gabarit, marges, hiérarchie typographique, palette) et le modèle ne fournit que
le **contenu** (surtitre, titre, sous-titre, points, pied). Deux conséquences :

- la qualité ne dépend plus du talent graphique du modèle ;
- le résultat est reproductible : même contenu + même gabarit = même SVG.

Aucune donnée métier n'est inventée : chaque texte vient de l'appelant, et un
champ absent n'est simplement pas dessiné.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from xml.sax.saxutils import escape

from app.documents.metriques_texte import couper_en_lignes

__all__ = [
    "GABARITS",
    "PALETTES",
    "CompositionRefusee",
    "composer_svg",
    "gabarit",
]


class CompositionRefusee(ValueError):
    """Contenu insuffisant ou gabarit inconnu : rien à composer."""


@dataclass(frozen=True, slots=True)
class Gabarit:
    """Format de sortie et échelle typographique d'une image."""

    nom: str
    largeur: int
    hauteur: int
    surtitre: int
    titre: int
    sous_titre: int
    point: int
    pied: int
    #: Nombre maximal de points affichés (au-delà, la composition refuse).
    points_max: int = 6


@dataclass(frozen=True, slots=True)
class Palette:
    """Cinq rôles de couleur, dont deux tenus par le contraste (WCAG).

    ``accent`` sert au décor (filet, puces) et n'a pas à être lisible ;
    ``accent_texte`` est sa déclinaison **lisible sur le fond**, utilisée pour le
    surtitre — un bleu institutionnel sur un fond marine ne donne que 3,4:1 et
    échouait au contrôle de design.
    """

    nom: str
    fond: str
    texte: str
    accent: str
    accent_texte: str
    doux: str


#: Gabarits disponibles : le nom est le vocabulaire exposé à l'agent.
GABARITS: dict[str, Gabarit] = {
    "carte": Gabarit("carte", 1200, 1200, 24, 96, 38, 32, 26),
    "banniere": Gabarit("banniere", 1600, 900, 22, 88, 34, 28, 24),
    "bandeau": Gabarit("bandeau", 1600, 400, 16, 44, 20, 18, 16, points_max=2),
    "affiche": Gabarit("affiche", 1240, 1754, 26, 104, 40, 34, 26),
}

#: Palettes dérivées des jetons de l'application (bleu institutionnel #0070D8).
PALETTES: dict[str, Palette] = {
    "carso": Palette("carso", "#0B1F33", "#FFFFFF", "#0070D8", "#7CB8FF", "#A9BDD1"),
    "clair": Palette("clair", "#F5F7FA", "#12233B", "#0070D8", "#0B57A8", "#54657A"),
    # Le bleu institutionnel ne laisse que 4,87:1 au blanc : les textes
    # secondaires doivent être très clairs pour rester au-dessus de 4,5:1.
    "institutionnel": Palette(
        "institutionnel", "#0070D8", "#FFFFFF", "#FFFFFF", "#F2F8FF", "#F5FAFF"
    ),
    "sable": Palette("sable", "#FFF8EC", "#3A2E1B", "#C97B16", "#8A4F09", "#7A6A53"),
}

#: Police unique : présente sur Windows comme sur les serveurs d'impression.
_POLICE = "Arial, Helvetica, sans-serif"


def gabarit(nom: str | None) -> Gabarit:
    """Gabarit demandé, ``carte`` par défaut."""
    cle = str(nom or "carte").strip().lower()
    if cle not in GABARITS:
        raise CompositionRefusee(
            "Gabarit inconnu "
            f"'{nom}' : choisir parmi {', '.join(sorted(GABARITS))}."
        )
    return GABARITS[cle]


def palette(nom: str | None) -> Palette:
    """Palette demandée, ``carso`` par défaut."""
    cle = str(nom or "carso").strip().lower()
    if cle not in PALETTES:
        raise CompositionRefusee(
            "Palette inconnue "
            f"'{nom}' : choisir parmi {', '.join(sorted(PALETTES))}."
        )
    return PALETTES[cle]


@dataclass(slots=True)
class _Bloc:
    """Un morceau de contenu : sa hauteur, et comment le dessiner à une ordonnée.

    Mesurer **avant** de dessiner est ce qui permet de répartir le contenu dans
    la page au lieu de l'empiler en haut en laissant un vide en bas.
    """

    hauteur: float
    rendre: Callable[[float], list[str]]


def composer_svg(
    *,
    titre: str,
    sous_titre: str | None = None,
    surtitre: str | None = None,
    points: list[str] | None = None,
    pied: str | None = None,
    gabarit_nom: str | None = None,
    palette_nom: str | None = None,
) -> str:
    """Compose le SVG d'une image CARSO à partir d'un contenu structuré.

    Args:
        titre: texte principal (obligatoire : c'est le sujet de l'image).
        sous_titre: précision d'une ligne ou deux, sous le titre.
        surtitre: étiquette courte en capitales, au-dessus du titre.
        points: puces (au plus ``gabarit.points_max``), une idée par puce.
        pied: mention de bas de page (source, date, contact).
        gabarit_nom: ``carte``, ``banniere``, ``bandeau`` ou ``affiche``.
        palette_nom: ``carso``, ``clair``, ``institutionnel`` ou ``sable``.

    Returns:
        Le SVG complet, prêt à être assaini puis rasterisé.

    Raises:
        CompositionRefusee: titre vide, gabarit ou palette inconnus, ou trop de
            points pour le gabarit choisi.
    """
    gab = gabarit(gabarit_nom)
    pal = palette(palette_nom)
    texte_titre = " ".join(str(titre or "").split())
    if not texte_titre:
        raise CompositionRefusee("Un titre est nécessaire : c'est le sujet de l'image.")

    puces = [" ".join(str(p).split()) for p in (points or []) if str(p).strip()]
    if len(puces) > gab.points_max:
        raise CompositionRefusee(
            f"Le gabarit '{gab.nom}' accepte au plus {gab.points_max} points "
            f"({len(puces)} fournis) : garder l'essentiel."
        )

    marge = round(min(gab.largeur, gab.hauteur) * 0.078)
    largeur_utile = gab.largeur - 2 * marge
    reserve_pied = (gab.pied * 3.4) if pied and str(pied).strip() else 0.0
    bas_disponible = gab.hauteur - marge - reserve_pied

    # --- Mesure : chaque bloc connaît sa hauteur avant d'être placé ----------
    blocs: list[_Bloc] = []
    epaisseur = max(6, round(marge * 0.09))
    blocs.append(
        _Bloc(
            epaisseur + gab.surtitre * 1.1,
            lambda y, _e=epaisseur: [
                _rect(marge, y, round(gab.largeur * 0.11), _e, pal.accent)
            ],
        )
    )

    if surtitre and str(surtitre).strip():
        texte_surtitre = " ".join(str(surtitre).split()).upper()
        blocs.append(
            _Bloc(
                gab.surtitre * 2.0,
                lambda y, _t=texte_surtitre: [
                    _texte(
                        marge,
                        y + gab.surtitre,
                        _t,
                        gab.surtitre,
                        pal.accent_texte,
                        gras=True,
                        letter_spacing=round(gab.surtitre * 0.14),
                    )
                ],
            )
        )

    # Le sous-titre et les puces sont mesurés d'abord : c'est ce qui reste
    # après eux qui décide de la taille du titre (sinon un titre plein cadre
    # chasse les puces sur le pied de page).
    apres_titre: list[_Bloc] = []

    if sous_titre and str(sous_titre).strip():
        lignes_sous = couper_en_lignes(
            " ".join(str(sous_titre).split()), gab.sous_titre, largeur_utile
        )

        def _rendre_sous(y: float, _lignes=lignes_sous) -> list[str]:
            fragments = []
            courant = y + gab.sous_titre * 0.6
            for ligne in _lignes:
                courant += gab.sous_titre * 1.28
                fragments.append(_texte(marge, courant, ligne, gab.sous_titre, pal.doux))
            return fragments

        # Un vrai blanc après le sous-titre : collé aux puces, il donne
        # l'impression d'un texte qui se chevauche.
        apres_titre.append(
            _Bloc(
                len(lignes_sous) * gab.sous_titre * 1.28 + gab.sous_titre * 0.95,
                _rendre_sous,
            )
        )

    if puces:
        decoupees = [
            couper_en_lignes(puce, gab.point, largeur_utile - gab.point * 1.4)
            for puce in puces
        ]

        def _rendre_puces(y: float, _puces=decoupees) -> list[str]:
            fragments: list[str] = []
            courant = y + gab.point * 1.0
            for lignes in _puces:
                courant += gab.point * 0.95
                fragments.append(
                    f'<circle cx="{marge + gab.point * 0.34:.0f}" '
                    f'cy="{courant - gab.point * 0.3:.0f}" '
                    f'r="{max(3, round(gab.point * 0.16))}" fill="{pal.accent}"/>'
                )
                for index, ligne in enumerate(lignes):
                    if index:
                        courant += gab.point * 1.34
                    fragments.append(
                        _texte(
                            marge + gab.point * 1.2,
                            courant,
                            ligne,
                            gab.point,
                            pal.texte,
                        )
                    )
                courant += gab.point * 0.45
            return fragments

        hauteur_puces = sum(
            len(lignes) * gab.point * 1.34 + gab.point * 1.9 for lignes in decoupees
        )
        apres_titre.append(_Bloc(hauteur_puces, _rendre_puces))

    hauteur_fixe = sum(bloc.hauteur for bloc in blocs) + sum(
        bloc.hauteur for bloc in apres_titre
    )
    taille_titre = float(gab.titre)
    lignes_titre: list[str] = []
    while True:
        lignes_titre = couper_en_lignes(texte_titre, taille_titre, largeur_utile, gras=True)
        hauteur_titre = len(lignes_titre) * taille_titre * 1.12
        if marge + hauteur_fixe + hauteur_titre <= bas_disponible:
            break
        if taille_titre <= gab.titre * 0.62:
            break
        taille_titre -= 4

    def _rendre_titre(y: float, _lignes=lignes_titre, _taille=taille_titre) -> list[str]:
        fragments = []
        courant = y
        for ligne in _lignes:
            courant += _taille * 1.06
            fragments.append(_texte(marge, courant, ligne, _taille, pal.texte, gras=True))
        return fragments

    hauteur_titre_bloc = len(lignes_titre) * taille_titre * 1.12 + taille_titre * 0.42
    blocs.append(_Bloc(hauteur_titre_bloc, _rendre_titre))
    blocs.extend(apres_titre)

    # --- Placement : le contenu se répartit, il ne reste pas collé en haut ----
    hauteur_contenu = sum(bloc.hauteur for bloc in blocs)
    place = bas_disponible - marge
    if hauteur_contenu > place + 1:
        # Mieux vaut refuser que livrer un texte qui marche sur le pied de page.
        raise CompositionRefusee(
            f"Contenu trop long pour le gabarit '{gab.nom}' : "
            f"{hauteur_contenu - place:.0f} px de trop. Raccourcir le titre, le "
            "sous-titre ou les points — ou choisir un gabarit plus haut "
            "(`banniere`, `affiche`)."
        )
    # Biais vers le haut (0,42) : un bloc optiquement centré, pas un vide unique
    # en bas de l'image quand le texte est court.
    curseur = marge + max(0.0, (place - hauteur_contenu) * 0.46)

    fragments: list[str] = []
    for bloc in blocs:
        fragments.extend(bloc.rendre(curseur))
        curseur += bloc.hauteur

    if pied and str(pied).strip():
        y_filet = gab.hauteur - marge - gab.pied * 1.9
        fragments.append(
            f'<rect x="{marge}" y="{y_filet:.0f}" '
            f'width="{largeur_utile:.0f}" height="2" '
            f'fill="{pal.doux}" opacity="0.55"/>'
        )
        texte_pied = " ".join(str(pied).split())
        if len(texte_pied) > 110:
            texte_pied = texte_pied[:107].rstrip() + "…"
        fragments.append(
            _texte(marge, gab.hauteur - marge, texte_pied, gab.pied, pal.doux)
        )

    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{gab.largeur}" '
        f'height="{gab.hauteur}" viewBox="0 0 {gab.largeur} {gab.hauteur}">'
        f'<rect width="{gab.largeur}" height="{gab.hauteur}" fill="{pal.fond}"/>'
        + "".join(fragments)
        + "</svg>"
    )


def _rect(x: float, y: float, largeur: float, hauteur: float, couleur: str) -> str:
    return (
        f'<rect x="{x:.0f}" y="{y:.0f}" width="{largeur:.0f}" '
        f'height="{hauteur:.0f}" fill="{couleur}"/>'
    )


def _texte(
    x: float,
    y: float,
    contenu: str,
    taille: float,
    couleur: str,
    *,
    gras: bool = False,
    letter_spacing: float = 0.0,
) -> str:
    """Un ``<text>`` ancré à gauche, ligne de base en ``y``."""
    attributs = [
        f'x="{x:.0f}"',
        f'y="{y:.0f}"',
        f'font-family="{_POLICE}"',
        f'font-size="{taille:.0f}"',
        f'fill="{couleur}"',
    ]
    if gras:
        attributs.append('font-weight="bold"')
    if letter_spacing:
        attributs.append(f'letter-spacing="{letter_spacing:.1f}"')
    return f"<text {' '.join(attributs)}>{escape(contenu)}</text>"
