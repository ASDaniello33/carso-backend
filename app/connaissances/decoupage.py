"""Découpage déterministe des ouvrages en passages indexables.

Un découpage de recherche n'a pas besoin d'un modèle : il doit être **stable**
(deux indexations du même fichier produisent les mêmes passages), borné en
longueur, et respecter le sens (paragraphes, puis phrases) plutôt que couper au
caractère 1200.

Règles :

1. un segment (page, section, feuille) est découpé séparément — la provenance
   n'est jamais mélangée ;
2. les paragraphes sont accumulés jusqu'à ``taille`` ; un paragraphe trop long
   est scindé aux phrases, puis aux mots en dernier recours ;
3. chaque passage reprend la fin du précédent (``chevauchement``), pour qu'une
   idée à cheval sur deux passages reste trouvable ;
4. un dernier passage plus court que ``minimum`` rejoint le précédent — jamais
   de miette illisible (et jamais au-delà de sa page).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.connaissances.lecture import Segment

#: Frontière de phrase française (point, point-virgule, ponctuation forte suivie
#: d'une espace). Volontairement simple : un découpage approximatif est sans
#: conséquence, une phrase oubliée non plus — le texte est indexé en entier.
_FIN_PHRASE = re.compile(r"(?<=[.!?;:])\s+(?=[A-ZÀ-ÖØ-Þ«\"(])")


@dataclass(frozen=True, slots=True)
class Passage:
    """Passage prêt à être vectorisé, avec sa position et sa provenance."""

    position: int
    texte: str
    page: int | None = None
    section: str | None = None


def _normaliser(texte: str) -> str:
    """Espaces harmonisés, lignes vides bornées (le sens reste intact)."""
    lignes = [" ".join(ligne.split()) for ligne in texte.replace("\r\n", "\n").split("\n")]
    normalise: list[str] = []
    for ligne in lignes:
        if not ligne and (not normalise or not normalise[-1]):
            continue
        normalise.append(ligne)
    return "\n".join(normalise).strip()


def _scinder_mots(texte: str, taille: int) -> list[str]:
    """Dernier recours : coupe aux espaces, sans dépasser ``taille``."""
    morceaux: list[str] = []
    courant = ""
    for mot in texte.split():
        candidat = f"{courant} {mot}".strip()
        if len(candidat) <= taille:
            courant = candidat
            continue
        if courant:
            morceaux.append(courant)
        courant = mot[:taille]
    if courant:
        morceaux.append(courant)
    return morceaux


def _fragments(texte: str, taille: int) -> list[str]:
    """Découpe un segment en blocs <= ``taille`` (paragraphes → phrases)."""
    fragments: list[str] = []
    for paragraphe in re.split(r"\n{2,}", texte):
        bloc = paragraphe.strip()
        if not bloc:
            continue
        if len(bloc) <= taille:
            fragments.append(bloc)
            continue
        for phrase in _FIN_PHRASE.split(bloc):
            phrase = phrase.strip()
            if not phrase:
                continue
            if len(phrase) <= taille:
                fragments.append(phrase)
            else:
                fragments.extend(_scinder_mots(phrase, taille))
    return fragments


def _queue(texte: str, longueur: int) -> str:
    """Fin du passage précédent, ramenée à une frontière de mot."""
    if longueur <= 0:
        return ""
    queue = texte[-longueur:]
    espace = queue.find(" ")
    return queue[espace + 1 :].strip() if espace != -1 else queue.strip()


def _assembler(fragments: list[str], taille: int, chevauchement: int) -> list[str]:
    passages: list[str] = []
    courant = ""
    for fragment in fragments:
        if not courant:
            courant = fragment
            continue
        candidat = f"{courant}\n\n{fragment}"
        if len(candidat) <= taille:
            courant = candidat
            continue
        passages.append(courant)
        prefixe = _queue(courant, chevauchement)
        candidat = f"{prefixe}\n\n{fragment}" if prefixe else fragment
        courant = candidat if len(candidat) <= taille else fragment
    if courant.strip():
        passages.append(courant)
    return passages


def decouper(
    segments: list[Segment],
    *,
    taille: int = 1200,
    chevauchement: int = 200,
    minimum: int = 200,
) -> list[Passage]:
    """Découpe des segments en passages numérotés (ordre de lecture).

    Args:
        segments: texte extrait, avec sa provenance.
        taille: longueur cible maximale d'un passage (caractères).
        chevauchement: caractères repris du passage précédent.
        minimum: en dessous, un dernier passage rejoint le précédent.

    Returns:
        Les passages, ``position`` croissante à partir de 0.
    """
    passages: list[Passage] = []
    for segment in segments:
        texte = _normaliser(segment.texte)
        if not texte:
            continue
        blocs = _assembler(
            _fragments(texte, taille), max(200, taille), max(0, chevauchement)
        )
        if len(blocs) > 1 and len(blocs[-1].strip()) < minimum:
            blocs[-2] = f"{blocs[-2]}\n\n{blocs[-1]}".strip()
            blocs.pop()
        for bloc in blocs:
            contenu = bloc.strip()
            if not contenu:
                continue
            passages.append(
                Passage(
                    position=len(passages),
                    texte=contenu,
                    page=segment.page,
                    section=segment.section,
                )
            )
    return passages


__all__ = ["Passage", "decouper"]
