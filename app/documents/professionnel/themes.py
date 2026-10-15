"""Palettes de couleurs disponibles pour les documents."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Theme:
    """Une palette : utilisée à la fois par le rendu PDF (CSS) et DOCX (Word)."""

    nom: str
    primary: str  # titres, en-têtes de tableaux, couverture
    accent: str  # filets, puces, chiffres clés, liens
    soft: str  # fonds légers (lignes alternées, KPI)
    callout: str  # fond des encadrés d'information


THEMES: dict[str, Theme] = {
    "corporate": Theme("corporate", "#1E3A5F", "#2563EB", "#F3F4F6", "#EFF6FF"),
    "moderne": Theme("moderne", "#1F1147", "#7C3AED", "#F5F3FF", "#F3E8FF"),
    "nature": Theme("nature", "#14532D", "#16A34A", "#F0FDF4", "#ECFDF5"),
}

TEXTE = "#1F2937"
MUTED = "#6B7280"
BORDURE = "#E5E7EB"

# variante -> (couleur de bordure, couleur de fond)
CALLOUT_VARIANTES: dict[str, tuple[str, str]] = {
    "success": ("#059669", "#ECFDF5"),
    "warning": ("#D97706", "#FFFBEB"),
    "danger": ("#DC2626", "#FEF2F2"),
}


def get_theme(nom: str | Theme | None) -> Theme:
    """Retourne le thème demandé, ou 'corporate' si le nom est inconnu."""
    if isinstance(nom, Theme):
        return nom
    return THEMES.get((nom or "corporate").strip().lower(), THEMES["corporate"])
