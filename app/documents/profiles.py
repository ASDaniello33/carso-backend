"""Profils documentaires CARSO (ADR 0005, Phases 10 et 15).

Un profil dit, pour un type de livrable : **quelles sections sont attendues**, ce
qui constitue un contenu suffisant, et à partir de quand un document est
manifestement vide. Il n'invente aucune règle métier : il traduit en attentes
vérifiables les livrables déjà décrits par les règles confirmées CARSO (offre
technique, offre financière, offre autre, fiche technique, fiche de présence,
checklist, rapport, courrier).

Trois usages, un seul endroit à modifier :

- guider le **plan de document** de l'agent (les sections attendues) ;
- alimenter la **validation de structure** (section absente, section vide) ;
- refuser un document **court et vide** — un livrable sans contenu réel est un
  échec, pas un brouillon acceptable (exigence de complétude).

Un document sans profil reste validable : les contrôles génériques s'appliquent.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "PROFILS",
    "ProfilDocument",
    "libelles_sections",
    "normaliser_titre",
    "profil_par_nom",
    "profil_probable",
    "sections_reconnues",
]


@dataclass(frozen=True, slots=True)
class ProfilDocument:
    """Attentes vérifiables d'un type de livrable.

    Attributes:
        nom: identifiant stable (``offre_technique``).
        libelle: intitulé lisible affiché à l'utilisateur.
        sections: sections attendues, dans l'ordre, avec leurs variantes de titre
            reconnues (un document réel n'écrit pas toujours « Méthodologie »).
        nb_mots_min: plancher de contenu réel du document entier.
        mots_min_par_section: plancher par section attendue.
        exige_tableau: vrai si le livrable suppose au moins un tableau.
        type_document: vocabulaire ``TypeDocument`` associé (facultatif).
    """

    nom: str
    libelle: str
    sections: tuple[tuple[str, tuple[str, ...]], ...]
    nb_mots_min: int
    mots_min_par_section: int
    exige_tableau: bool = False
    type_document: str | None = None
    mots_cles: tuple[str, ...] = field(default=())

    def titres_attendus(self) -> tuple[str, ...]:
        """Intitulés canoniques des sections attendues, dans l'ordre."""
        return tuple(titre for titre, _ in self.sections)

    def variantes(self) -> dict[str, tuple[str, ...]]:
        """Table canonique → variantes reconnues (titre canonique inclus)."""
        return {titre: variantes for titre, variantes in self.sections}


def _section(titre: str, *variantes: str) -> tuple[str, tuple[str, ...]]:
    """Déclare une section attendue et ses variantes de titre."""
    return (titre, (titre, *variantes))


PROFILS: dict[str, ProfilDocument] = {
    "offre_technique": ProfilDocument(
        nom="offre_technique",
        libelle="Offre technique",
        sections=(
            _section("Contexte", "Présentation", "Presentation", "Compréhension"),
            _section("Objectifs", "Objectif", "Finalités", "Finalites"),
            _section("Démarche", "Méthodologie", "Methodologie", "Approche"),
            _section("Moyens", "Ressources", "Équipe", "Equipe"),
            _section("Planning", "Calendrier", "Durée", "Duree"),
            _section("Résultats attendus", "Resultats attendus", "Livrables"),
            _section("Conclusion", "Synthèse", "Synthese"),
        ),
        nb_mots_min=350,
        mots_min_par_section=25,
        exige_tableau=True,
        type_document="offre_technique",
        mots_cles=("méthodologie", "formateurs", "durée"),
    ),
    "offre_financiere": ProfilDocument(
        nom="offre_financiere",
        libelle="Offre financière",
        sections=(
            _section("Contexte", "Présentation", "Objet"),
            _section("Base de chiffrage", "Hypothèses", "Hypotheses", "Détail"),
            _section("Budget", "Coût", "Cout", "Tarification", "Prix"),
            _section("Conditions", "Modalités", "Modalites", "Paiement"),
            _section("Conclusion", "Récapitulatif", "Recapitulatif"),
        ),
        nb_mots_min=200,
        mots_min_par_section=20,
        exige_tableau=True,
        type_document="offre_financiere",
        mots_cles=("budget", "coût", "quantité"),
    ),
    "offre_autre": ProfilDocument(
        nom="offre_autre",
        libelle="Offre (autre nature)",
        sections=(
            _section("Contexte", "Présentation", "Objet"),
            _section("Proposition", "Contenu", "Offre"),
            _section("Mise en œuvre", "Mise en oeuvre", "Organisation"),
            _section("Conclusion", "Synthèse", "Synthese"),
        ),
        nb_mots_min=200,
        mots_min_par_section=20,
        type_document="autre",
    ),
    "fiche_technique": ProfilDocument(
        nom="fiche_technique",
        libelle="Fiche technique",
        sections=(
            _section("Identification", "Objet", "Référence", "Reference"),
            _section("Contenu", "Description", "Détail", "Detail"),
            _section("Moyens", "Ressources", "Logistique"),
            _section("Contraintes", "Sécurité", "Securite", "Prérequis", "Prerequis"),
        ),
        nb_mots_min=120,
        mots_min_par_section=15,
        type_document="fiche_technique",
    ),
    "fiche_presence": ProfilDocument(
        nom="fiche_presence",
        libelle="Fiche de présence",
        sections=(
            _section("Identification", "Session", "Objet"),
            _section("Bénéficiaires", "Beneficiaires", "Participants", "Présences"),
        ),
        nb_mots_min=40,
        mots_min_par_section=10,
        exige_tableau=True,
        type_document="fiche_presence",
    ),
    "checklist": ProfilDocument(
        nom="checklist",
        libelle="Checklist",
        sections=(
            _section("Objet", "Contexte", "Identification"),
            _section("Points de contrôle", "Points de controle", "Vérifications", "Verifications"),
        ),
        nb_mots_min=40,
        mots_min_par_section=10,
        type_document="checklist",
    ),
    "rapport": ProfilDocument(
        nom="rapport",
        libelle="Rapport",
        sections=(
            _section("Contexte", "Objet", "Introduction"),
            _section("Déroulement", "Deroulement", "Activités", "Activites", "Réalisations"),
            _section("Résultats", "Resultats", "Constats", "Analyse"),
            _section("Difficultés", "Difficultes", "Points de vigilance", "Limites"),
            _section("Recommandations", "Perspectives", "Conclusion"),
        ),
        nb_mots_min=300,
        mots_min_par_section=25,
        type_document="rapport",
    ),
    "courrier": ProfilDocument(
        nom="courrier",
        libelle="Courrier",
        sections=(
            _section("Objet", "Référence", "Reference"),
            _section("Corps", "Contenu", "Message"),
        ),
        nb_mots_min=60,
        mots_min_par_section=20,
        type_document="courrier",
    ),
}


def profil_par_nom(nom: str | None) -> ProfilDocument | None:
    """Profil demandé, ou ``None`` (jamais de repli silencieux sur un autre profil)."""
    if not nom:
        return None
    return PROFILS.get(str(nom).strip().lower())


def profil_probable(spec: Any) -> ProfilDocument | None:
    """Profil le plus probable d'un ``DocumentSpec``, d'après son plan et ses métadonnées.

    L'indice le plus fiable est le type de document déclaré ; à défaut, on
    compare les intitulés de sections du document aux sections attendues.

    Args:
        spec: ``DocumentSpec`` à analyser (import paresseux pour éviter un cycle).
    """
    type_document = (spec.metadata.type_document or "").strip().lower()
    if type_document:
        direct = profil_par_nom(type_document)
        if direct is not None:
            return direct
    if spec.profil:
        direct = profil_par_nom(spec.profil)
        if direct is not None:
            return direct

    titres = {normaliser_titre(bloc.texte) for bloc in spec.blocs if bloc.type == "titre"}
    if not titres:
        return None
    meilleur: tuple[int, ProfilDocument | None] = (0, None)
    for profil in PROFILS.values():
        attendus = set()
        for canonique, variantes in profil.sections:
            attendus.add(normaliser_titre(canonique))
            attendus.update(normaliser_titre(variante) for variante in variantes)
        score = len(titres & attendus)
        if score > meilleur[0]:
            meilleur = (score, profil)
    return meilleur[1] if meilleur[0] >= 2 else None


def libelles_sections(profil: ProfilDocument) -> list[str]:
    """Intitulés canoniques d'un profil (pour les instructions d'agent)."""
    return [titre for titre, _ in profil.sections]


def normaliser_titre(titre: str | None) -> str:
    """Normalise un intitulé de section pour la comparaison.

    Retire la numérotation (« 2. Contexte », « II — Budget ») et les accents :
    un document réel n'écrit pas ses titres exactement comme un gabarit.
    """
    if not titre:
        return ""
    texte = unicodedata.normalize("NFKD", str(titre))
    texte = "".join(caractere for caractere in texte if not unicodedata.combining(caractere))
    texte = texte.lower().strip()
    for separateur in (".", ")", ":", "—", "-"):
        if separateur in texte[:6]:
            texte = texte.split(separateur, 1)[-1].strip()
    return " ".join(texte.split())


def sections_reconnues(profil: ProfilDocument, titres: list[str]) -> dict[str, str | None]:
    """Associe chaque section attendue au titre réel qui la porte (``None`` si absente)."""
    normalises = {normaliser_titre(titre): titre for titre in titres}
    trouvees: dict[str, str | None] = {}
    for canonique, variantes in profil.sections:
        titre_reel = None
        for candidat in (canonique, *variantes):
            titre_reel = normalises.get(normaliser_titre(candidat))
            if titre_reel is not None:
                break
        trouvees[canonique] = titre_reel
    return trouvees
