"""Extraction de fichiers texte : ``.txt`` et ``.csv`` (bibliothèque standard).

Aucune dépendance tierce ici. Les exports Excel français sont souvent encodés en
cp1252 : le décodage tente UTF-8 puis cp1252 (même politique que la validation
de contenu), sans jamais inventer de contenu en remplacement.

La lecture est **bornée** : on ne charge pas un fichier entier pour n'en
retourner que les premiers caractères.
"""

from __future__ import annotations

from pathlib import Path

from app.core.errors import ValidationError
from app.documents.extraction.base import ExtractedContent

_ENCODAGES = ("utf-8", "cp1252")
# Marge pour les caractères multi-octets et l'éclatement des lignes.
_OCTETS_PAR_CARACTERE_MAX = 4


class TextFileExtractor:
    """Extracteur texte/csv (aucune librairie externe)."""

    librairie: str = "stdlib"

    def __init__(self, extension: str) -> None:
        self.extension = extension
        self.adaptateur = f"stdlib[{extension}]"

    def extract(self, path: Path, *, max_chars: int) -> ExtractedContent:
        limite = max_chars * _OCTETS_PAR_CARACTERE_MAX
        with path.open("rb") as fichier:
            octets = fichier.read(limite + 1)

        tronque = len(octets) > limite
        echantillon = octets[:limite]

        texte = self._decode(echantillon)
        if tronque:
            texte = texte[:max_chars]

        return ExtractedContent(
            texte=texte.strip(),
            adaptateur=self.adaptateur,
            nb_pages=None,
            tronque=tronque,
        )

    def _decode(self, octets: bytes) -> str:
        for encodage in _ENCODAGES:
            try:
                return octets.decode(encodage)
            except UnicodeDecodeError:
                continue

        msg = "Fichier texte non décodable (ni UTF-8 ni cp1252)"
        raise ValidationError(msg)
