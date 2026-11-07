"""Métriques de texte SVG, partagées par la composition et le contrôle (INC-136).

Un SVG n'a pas de moteur de mise en page : la largeur d'un texte n'existe nulle
part dans le fichier. Or composer une image lisible — et **vérifier** qu'un SVG
d'agent ne déborde pas — demande la même estimation.

Ce module fournit donc une seule table de largeurs (fractions de la taille de
police, métriques Arial/Helvetica) utilisée par les deux côtés :

- ``composition_image`` s'en sert pour couper les lignes et dimensionner les
  titres ;
- ``controle_image`` s'en sert pour signaler un texte qui sort du cadre.

C'est une **estimation**, pas une mesure : elle est volontairement prudente
(marges de coupe à 92 % de la largeur utile) et le contrôle le dit à l'agent.
"""

from __future__ import annotations

import unicodedata

__all__ = ["couper_en_lignes", "largeur_estimee"]

#: Largeurs Arial/Helvetica en fractions de la taille de police.
_LARGEURS: dict[str, float] = {
    " ": 0.278,
    "!": 0.278,
    '"': 0.355,
    "#": 0.556,
    "$": 0.556,
    "%": 0.889,
    "&": 0.667,
    "'": 0.191,
    "(": 0.333,
    ")": 0.333,
    "*": 0.389,
    "+": 0.584,
    ",": 0.278,
    "-": 0.333,
    ".": 0.278,
    "/": 0.278,
    ":": 0.278,
    ";": 0.278,
    "<": 0.584,
    "=": 0.584,
    ">": 0.584,
    "?": 0.556,
    "@": 1.015,
    "[": 0.278,
    "\\": 0.278,
    "]": 0.278,
    "^": 0.469,
    "_": 0.556,
    "`": 0.333,
    "{": 0.334,
    "|": 0.26,
    "}": 0.334,
    "~": 0.584,
    "0": 0.556,
    "1": 0.556,
    "2": 0.556,
    "3": 0.556,
    "4": 0.556,
    "5": 0.556,
    "6": 0.556,
    "7": 0.556,
    "8": 0.556,
    "9": 0.556,
    "A": 0.667,
    "B": 0.667,
    "C": 0.722,
    "D": 0.722,
    "E": 0.667,
    "F": 0.611,
    "G": 0.778,
    "H": 0.722,
    "I": 0.278,
    "J": 0.5,
    "K": 0.667,
    "L": 0.556,
    "M": 0.833,
    "N": 0.722,
    "O": 0.778,
    "P": 0.667,
    "Q": 0.778,
    "R": 0.722,
    "S": 0.667,
    "T": 0.611,
    "U": 0.722,
    "V": 0.667,
    "W": 0.944,
    "X": 0.667,
    "Y": 0.667,
    "Z": 0.611,
    "a": 0.556,
    "b": 0.556,
    "c": 0.5,
    "d": 0.556,
    "e": 0.556,
    "f": 0.278,
    "g": 0.556,
    "h": 0.556,
    "i": 0.222,
    "j": 0.222,
    "k": 0.5,
    "l": 0.222,
    "m": 0.833,
    "n": 0.556,
    "o": 0.556,
    "p": 0.556,
    "q": 0.556,
    "r": 0.333,
    "s": 0.5,
    "t": 0.278,
    "u": 0.556,
    "v": 0.5,
    "w": 0.722,
    "x": 0.5,
    "y": 0.5,
    "z": 0.5,
    "«": 0.556,
    "»": 0.556,
    "—": 1.0,
    "–": 0.556,
    "’": 0.191,
    "€": 0.556,
}

#: Facteur des graisses fortes (``font-weight="bold"``).
_GRAS = 1.06

#: Caractère absent de la table : largeur moyenne d'une lettre bas de casse.
_DEFAUT = 0.52


def largeur_estimee(texte: str, taille: float, *, gras: bool = False) -> float:
    """Largeur approximative d'un texte en pixels (métriques Arial).

    Les lettres accentuées sont ramenées à leur lettre de base (``É`` → ``E``) :
    leur chasse est identique dans les polices courantes.
    """
    if not texte or taille <= 0:
        return 0.0
    total = 0.0
    for caractere in texte:
        base = unicodedata.normalize("NFD", caractere)
        base = base[0] if base else caractere
        total += _LARGEURS.get(base, _LARGEURS.get(caractere, _DEFAUT))
    return total * taille * (_GRAS if gras else 1.0)


def couper_en_lignes(
    texte: str,
    taille: float,
    largeur_max: float,
    *,
    gras: bool = False,
    marge_securite: float = 0.92,
) -> list[str]:
    """Coupe un texte en lignes qui tiennent dans ``largeur_max``.

    La coupe est faite sur les espaces (jamais au milieu d'un mot) ; un mot seul
    plus large que la place disponible est laissé tel quel — c'est au contrôle
    de design de le signaler, pas à la composition de mutiler le texte.
    """
    mots = str(texte or "").split()
    if not mots:
        return []
    plafond = max(1.0, largeur_max * marge_securite)
    lignes: list[str] = []
    courante = mots[0]
    for mot in mots[1:]:
        essai = f"{courante} {mot}"
        if largeur_estimee(essai, taille, gras=gras) <= plafond:
            courante = essai
        else:
            lignes.append(courante)
            courante = mot
    lignes.append(courante)
    return lignes
