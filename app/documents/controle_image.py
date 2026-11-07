"""Contrôle de design d'une image SVG, avant livraison à l'utilisateur (INC-136).

Un modèle qui dessine à la main produit souvent une image « techniquement
valide » mais illisible : un texte qui sort du cadre, une police de 9 px, un
gris sur gris, un fond uni sans contenu. L'agent ne voit pas ce qu'il vient de
produire : ce module lui rend des **constats vérifiables** pour qu'il corrige
avant de déposer, au lieu de découvrir le problème par l'utilisateur.

Les constats sont calculés, jamais devinés :

- métriques de texte partagées avec la composition (``metriques_texte``) pour
  estimer les débordements ;
- contraste WCAG (rapport de luminance) entre la couleur d'un texte et la
  couleur dominante du rendu ;
- analyse du PNG rendu (couleur dominante, part du fond, nombre de teintes).

Ce sont des **heuristiques** : chaque constat dit ce qu'il mesure, et le retour
d'outil précise que l'agent reste juge de la correction à apporter.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass

from app.documents.metriques_texte import largeur_estimee

__all__ = ["Diagnostic", "diagnostiquer"]

#: Marge de sécurité sous laquelle un texte est considéré comme hors cadre.
_MARGE_SECURITE = 6.0

#: En dessous de cette taille (px), un texte devient illisible à l'impression.
_TAILLE_MIN = 14.0

#: Rapport de contraste WCAG AA pour du texte courant.
_CONTRASTE_AA = 4.5
#: WCAG AA pour du **grand texte** (≥ 24 px, ou ≥ 18,5 px en gras).
_CONTRASTE_AA_GRAND = 3.0
#: En dessous de ce rapport, le texte est illisible quel que soit le corps.
_CONTRASTE_BLOQUANT = 3.0

_RE_TEXTE = re.compile(r"<text\b([^>]*)>(.*?)</text>", re.DOTALL)
_RE_RECT = re.compile(r"<rect\b([^>]*?)/?>", re.DOTALL)
_RE_ATTRIBUT = re.compile(r"([\w:-]+)\s*=\s*[\"']([^\"']*)[\"']")

#: Quelques noms de couleurs CSS courants (le reste est ignoré, sans erreur).
_NOMS_COULEURS = {
    "black": (0, 0, 0),
    "white": (255, 255, 255),
    "red": (255, 0, 0),
    "green": (0, 128, 0),
    "blue": (0, 0, 255),
    "gray": (128, 128, 128),
    "grey": (128, 128, 128),
    "yellow": (255, 255, 0),
    "orange": (255, 165, 0),
    "navy": (0, 0, 128),
    "silver": (192, 192, 192),
    "transparent": None,
}


@dataclass(frozen=True, slots=True)
class Diagnostic:
    """Un constat de design, avec sa portée et le correctif attendu."""

    code: str
    #: ``bloquant`` (ne pas livrer), ``avertissement`` (à corriger si possible),
    #: ``conseil`` (qualité).
    niveau: str
    message: str
    correctif: str

    def en_dict(self) -> dict[str, str]:
        return {
            "code": self.code,
            "niveau": self.niveau,
            "message": self.message,
            "correctif": self.correctif,
        }


def diagnostiquer(
    svg: str,
    *,
    largeur: int,
    hauteur: int,
    png: bytes | None = None,
) -> list[Diagnostic]:
    """Analyse un SVG rendu et rend la liste des défauts de design détectés.

    Args:
        svg: balisage déjà assaini (tel qu'il sera rasterisé).
        largeur: largeur du rendu en pixels.
        hauteur: hauteur du rendu en pixels.
        png: rendu PNG, si disponible — c'est lui qui permet de mesurer le fond,
            la part de vide et le nombre de teintes réelles.

    Returns:
        Les constats, du plus grave au plus anodin. Liste vide = rien de
        détecté (ce qui ne garantit pas le goût, seulement l'absence de défaut
        mesurable).
    """
    fond = _couleur_dominante(png) if png else None
    if fond is None:
        fond = _fond_declare(svg, largeur, hauteur)
    diagnostics: list[Diagnostic] = []
    textes = _textes(svg)

    if _fond_declare(svg, largeur, hauteur) is None:
        # La rasterisation compose toujours sur blanc : la transparence ne se
        # voit donc pas dans le PNG. Le vrai risque est un fond **absent** — un
        # texte blanc sur blanc dans un document imprimé.
        diagnostics.append(
            Diagnostic(
                "fond_transparent",
                "conseil",
                "Aucun rectangle de fond ne couvre l'image : le rendu sera "
                "transparent, puis aplati en blanc dans un document.",
                "Dessiner un <rect width=… height=… fill=…> de la taille du "
                "viewBox avant les autres éléments.",
            )
        )

    if not textes:
        diagnostics.append(
            Diagnostic(
                "aucun_texte",
                "conseil",
                "L'image ne contient aucun texte : elle n'explique rien d'elle-même.",
                "Ajouter au moins un titre lisible sur l'image elle-même.",
            )
        )

    for texte in textes:
        diagnostics.extend(_controler_texte(texte, fond, largeur, hauteur))

    if png:
        diagnostics.extend(_controler_rendu(png))

    ordre = {"bloquant": 0, "avertissement": 1, "conseil": 2}
    diagnostics.sort(key=lambda diag: ordre.get(diag.niveau, 3))
    return diagnostics


# --- Textes ------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Texte:
    contenu: str
    x: float
    y: float
    taille: float
    couleur: tuple[int, int, int] | None
    ancre: str
    gras: bool


def _textes(svg: str) -> list[_Texte]:
    """Extrait les ``<text>`` du SVG avec leurs attributs utiles."""
    trouves: list[_Texte] = []
    for attributs, contenu in _RE_TEXTE.findall(svg or ""):
        valeurs = {cle.lower(): val for cle, val in _RE_ATTRIBUT.findall(attributs)}
        texte = re.sub(r"<[^>]+>", "", contenu).strip()
        if not texte:
            continue
        try:
            x = float(valeurs.get("x", "0").strip().rstrip("px") or 0)
            y = float(valeurs.get("y", "0").strip().rstrip("px") or 0)
            taille = float(valeurs.get("font-size", "16").strip().rstrip("px") or 16)
        except ValueError:
            continue
        graisse = (valeurs.get("font-weight") or "").strip().lower()
        trouves.append(
            _Texte(
                contenu=texte,
                x=x,
                y=y,
                taille=taille,
                couleur=_couleur(valeurs.get("fill")),
                ancre=(valeurs.get("text-anchor") or "start").strip().lower(),
                gras=graisse in {"bold", "bolder", "600", "700", "800", "900"},
            )
        )
    return trouves


def _controler_texte(
    texte: _Texte, fond: tuple[int, int, int] | None, largeur: int, hauteur: int
) -> list[Diagnostic]:
    """Débordement, taille illisible et contraste d'un seul texte."""
    constats: list[Diagnostic] = []
    largeur_texte = largeur_estimee(texte.contenu, texte.taille, gras=texte.gras)

    if texte.ancre == "middle":
        gauche, droite = texte.x - largeur_texte / 2, texte.x + largeur_texte / 2
    elif texte.ancre == "end":
        gauche, droite = texte.x - largeur_texte, texte.x
    else:
        gauche, droite = texte.x, texte.x + largeur_texte

    if gauche < -_MARGE_SECURITE or droite > largeur + _MARGE_SECURITE:
        constats.append(
            Diagnostic(
                "texte_hors_cadre",
                "bloquant",
                f"« {_extrait(texte.contenu)} » dépasse le cadre "
                f"({droite:.0f} px pour une image de {largeur} px).",
                "Raccourcir le texte, réduire la police ou élargir le gabarit.",
            )
        )
    if texte.y > hauteur + _MARGE_SECURITE:
        constats.append(
            Diagnostic(
                "texte_hors_cadre",
                "bloquant",
                f"« {_extrait(texte.contenu)} » est placé sous le bas de l'image "
                f"(y={texte.y:.0f} pour {hauteur} px de haut).",
                "Remonter le texte dans la zone utile.",
            )
        )
    if texte.taille < _TAILLE_MIN:
        constats.append(
            Diagnostic(
                "texte_trop_petit",
                "avertissement",
                f"« {_extrait(texte.contenu)} » fait {texte.taille:.0f} px de haut "
                f"(minimum conseillé {_TAILLE_MIN:.0f} px).",
                "Augmenter la taille ou retirer ce texte.",
            )
        )
    if texte.couleur and fond:
        ratio = _contraste(texte.couleur, fond)
        # WCAG distingue le grand texte (3:1) du texte courant (4,5:1) : un
        # titre de 26 px en gras n'a pas besoin du même seuil qu'une légende.
        seuil = (
            _CONTRASTE_AA_GRAND
            if texte.taille >= 24 or (texte.gras and texte.taille >= 18.5)
            else _CONTRASTE_AA
        )
        if ratio < _CONTRASTE_BLOQUANT:
            constats.append(
                Diagnostic(
                    "contraste_insuffisant",
                    "bloquant",
                    f"« {_extrait(texte.contenu)} » : contraste "
                    f"{ratio:.1f}:1 avec le fond (illisible).",
                    "Choisir une couleur de texte claire sur fond sombre, ou "
                    "l'inverse.",
                )
            )
        elif ratio < seuil:
            constats.append(
                Diagnostic(
                    "contraste_insuffisant",
                    "avertissement",
                    f"« {_extrait(texte.contenu)} » : contraste {ratio:.1f}:1 "
                    f"(minimum AA : {seuil}).",
                    "Renforcer le contraste du texte sur son fond.",
                )
            )
    return constats


# --- Rendu -------------------------------------------------------------------


def _controler_rendu(png: bytes) -> list[Diagnostic]:
    """Part du fond et richesse de palette du PNG rendu."""
    try:
        from PIL import Image
    except ImportError:  # pragma: no cover - Pillow fait partie du socle documents
        return []

    try:
        with Image.open(io.BytesIO(png)) as image:
            rgba = image.convert("RGBA")
            total = max(1, rgba.width * rgba.height)
            reduite = rgba.convert("RGB").quantize(colors=8)
            couleurs = reduite.getcolors(maxcolors=1 << 16) or []
            part_dominante = max((nombre for nombre, _ in couleurs), default=total) / total
            # Compter les **aplats** (≥ 2 % de l'image), pas les nuances
            # d'antialiasing : un titre net génère des dizaines de pixels
            # intermédiaires qui ne disent rien de la palette réelle.
            teintes = sum(1 for nombre, _ in couleurs if nombre / total >= 0.02)
    except Exception:  # noqa: BLE001 - un PNG illisible est déjà signalé ailleurs
        return []


    constats: list[Diagnostic] = []
    if part_dominante > 0.995:
        constats.append(
            Diagnostic(
                "image_vide",
                "bloquant",
                "L'image rendue est uniforme : rien n'y est visible.",
                "Vérifier que le contenu est dessiné (coordonnées, couleurs, "
                "taille du viewBox).",
            )
        )
    if teintes > 6:
        constats.append(
            Diagnostic(
                "palette_trop_riche",
                "conseil",
                f"{teintes} aplats de couleur : l'image paraît chargée.",
                "Garder un fond, une couleur de texte, une couleur d'accent.",
            )
        )
    return constats


# --- Couleurs ----------------------------------------------------------------


def _fond_declare(svg: str, largeur: int, hauteur: int) -> tuple[int, int, int] | None:
    """Couleur du premier rectangle qui couvre tout le cadre (fond réel).

    Un ``width="100%"`` est accepté : c'est la façon la plus courante d'écrire
    un fond en SVG.
    """
    for attributs in _RE_RECT.findall(svg or ""):
        valeurs = {cle.lower(): val for cle, val in _RE_ATTRIBUT.findall(attributs)}
        if not _couvre(valeurs.get("width"), largeur):
            continue
        if not _couvre(valeurs.get("height"), hauteur):
            continue
        couleur = _couleur(valeurs.get("fill"))
        if couleur is not None:
            return couleur
    return None


def _couvre(valeur: object, dimension: int) -> bool:
    """Cette dimension d'attribut couvre-t-elle le cadre ?"""
    brut = str(valeur or "").strip().lower()
    if brut.endswith("%"):
        try:
            return float(brut[:-1]) >= 99.0
        except ValueError:
            return False
    try:
        return float(brut.removesuffix("px")) >= dimension * 0.98
    except ValueError:
        return False


def _couleur(valeur: object) -> tuple[int, int, int] | None:
    """Couleur CSS → RGB, ou ``None`` si elle n'est pas exploitable."""
    brut = str(valeur or "").strip().lower()
    if not brut:
        return None
    if brut in _NOMS_COULEURS:
        return _NOMS_COULEURS[brut]
    if brut.startswith("#"):
        hexa = brut[1:]
        if len(hexa) == 3:
            hexa = "".join(c * 2 for c in hexa)
        if len(hexa) in (6, 8):
            try:
                return (int(hexa[0:2], 16), int(hexa[2:4], 16), int(hexa[4:6], 16))
            except ValueError:
                return None
    correspondance = re.match(
        r"rgba?\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})", brut
    )
    if correspondance:
        return tuple(int(g) for g in correspondance.groups())  # type: ignore[return-value]
    return None


def _luminance(couleur: tuple[int, int, int]) -> float:
    """Luminance relative WCAG d'une couleur RGB."""
    composantes = []
    for canal in couleur:
        valeur = canal / 255
        composantes.append(
            valeur / 12.92 if valeur <= 0.03928 else ((valeur + 0.055) / 1.055) ** 2.4
        )
    r, g, b = composantes
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contraste(a: tuple[int, int, int], b: tuple[int, int, int]) -> float:
    """Rapport de contraste WCAG entre deux couleurs (1:1 à 21:1)."""
    la, lb = _luminance(a), _luminance(b)
    clair, sombre = max(la, lb), min(la, lb)
    return (clair + 0.05) / (sombre + 0.05)


def _couleur_dominante(png: bytes) -> tuple[int, int, int] | None:
    """Couleur la plus fréquente du rendu (le fond, dans une image soignée)."""
    try:
        from PIL import Image
    except ImportError:  # pragma: no cover
        return None
    try:
        with Image.open(io.BytesIO(png)) as image:
            reduite = image.convert("RGB").quantize(colors=8)
            couleurs = reduite.getcolors(maxcolors=1 << 16) or []
            if not couleurs:
                return None
            _, index = max(couleurs, key=lambda paire: paire[0])
            rgb = reduite.getpalette() or []
            return (rgb[index * 3], rgb[index * 3 + 1], rgb[index * 3 + 2])
    except Exception:  # noqa: BLE001
        return None


def _extrait(texte: str, longueur: int = 40) -> str:
    """Début du texte cité dans un constat (les messages restent courts)."""
    propre = " ".join(texte.split())
    return propre if len(propre) <= longueur else propre[: longueur - 1] + "…"
