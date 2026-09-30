"""Validation documentaire — structure, contenu, style (ADR 0005, Phase 10).

Un document généré est **vérifié avant d'être présenté**. Trois portées, trois
familles de contrôles déterministes (aucun LLM, aucune invention de contenu) :

- **structure** : sections attendues du profil, sections vides, titres, tableaux
  sans en-tête, images sans référence, marqueurs ``(à compléter)`` restants ;
- **contenu** : complétude réelle (plancher de mots), cohérence d'un total
  budgétaire avec la somme de ses lignes, métadonnées obligatoires, répétitions,
  hypothèse présentée comme un fait ;
- **style** : niveaux de titre sautés, largeurs de tableau incohérentes ou
  débordantes, tableau ou image plus large que la page, pagination estimée.

Chaque constat est un ``Finding`` (code stable, gravité, emplacement, conseil) :
la sortie est exploitable par un agent — il peut réparer précisément ce qui est
signalé (``app.documents.repair``) puis revalider.

Les estimations sont annoncées comme telles : la pagination est **approximée**
par mesure de texte (pas de moteur de rendu ici) ; c'est le PDF dérivé
(``render_document``) qui donne la pagination réelle.
"""

from __future__ import annotations

import math
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.documents.profiles import (
    ProfilDocument,
    normaliser_titre,
    profil_par_nom,
    profil_probable,
    sections_reconnues,
)
from app.documents.spec import (
    MARQUEUR_RE,
    Citation,
    DocumentSpec,
    Encadre,
    Image,
    Liste,
    Paires,
    Paragraphe,
    Tableau,
    Titre,
)
from app.documents.styles import POINTS_PAR_POUCE

__all__ = [
    "Finding",
    "RapportValidation",
    "estimer_pages",
    "valider_document",
]

Gravite = Literal["erreur", "avertissement", "information"]
Portee = Literal["structure", "contenu", "style"]

#: Nombre de colonnes au-delà duquel un tableau devient difficilement lisible.
MAX_COLONNES_LISIBLES = 8

#: Longueur minimale d'un paragraphe pour juger d'une répétition.
_LONGUEUR_REPETITION = 40

_NOMBRE = re.compile(r"-?\d[\d\s\u00a0.,]*")
_PT_MM = POINTS_PAR_POUCE / 25.4


class Finding(BaseModel):
    """Constat de validation : quoi, où, et ce qu'il faut faire."""

    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=3, max_length=40)
    gravite: Gravite
    portee: Portee
    message: str = Field(min_length=1, max_length=500)
    emplacement: dict[str, Any] = Field(default_factory=dict)
    conseil: str | None = Field(default=None, max_length=500)


class RapportValidation(BaseModel):
    """Rapport de validation d'un document."""

    model_config = ConfigDict(extra="forbid")

    ok: bool
    par_portee: dict[str, str]
    findings: list[Finding] = Field(default_factory=list)
    resume: dict[str, Any] = Field(default_factory=dict)

    def messages(self, gravite: Gravite | None = None) -> list[str]:
        """Messages lisibles, filtrés par gravité si demandé."""
        return [
            f"[{finding.code}] {finding.message}"
            for finding in self.findings
            if gravite is None or finding.gravite == gravite
        ]


def valider_document(
    spec: DocumentSpec,
    *,
    profil: str | ProfilDocument | None = None,
    portees: tuple[str, ...] | None = None,
) -> RapportValidation:
    """Valide un document structuré et renvoie un rapport exploitable.

    Args:
        spec: document à valider.
        profil: profil imposé (nom ou objet) ; à défaut, il est déduit du
            document — et si rien ne correspond, seuls les contrôles génériques
            s'appliquent.
        portees: portées à exécuter (défaut : les trois).

    Returns:
        ``RapportValidation`` — ``ok`` vaut ``False`` dès qu'un constat est une
        erreur.
    """
    profil_effectif = _resoudre_profil(spec, profil)
    actives = tuple(portees or ("structure", "contenu", "style"))
    findings: list[Finding] = []
    if "structure" in actives:
        findings.extend(_valider_structure(spec, profil_effectif))
    if "contenu" in actives:
        findings.extend(_valider_contenu(spec, profil_effectif))
    if "style" in actives:
        findings.extend(_valider_style(spec))

    par_portee: dict[str, str] = {}
    for portee in actives:
        constats = [finding for finding in findings if finding.portee == portee]
        if any(finding.gravite == "erreur" for finding in constats):
            par_portee[portee] = "failed"
        elif constats:
            par_portee[portee] = "warning"
        else:
            par_portee[portee] = "passed"

    return RapportValidation(
        ok=not any(finding.gravite == "erreur" for finding in findings),
        par_portee=par_portee,
        findings=findings,
        resume={
            "profil": profil_effectif.nom if profil_effectif else None,
            "profil_demande": profil if isinstance(profil, str) else None,
            "portees": list(actives),
            "nb_findings": len(findings),
            "nb_erreurs": sum(1 for f in findings if f.gravite == "erreur"),
            "nb_avertissements": sum(1 for f in findings if f.gravite == "avertissement"),
            "pages_estimees": estimer_pages(spec),
            "nb_mots": spec.nb_mots(),
        },
    )


def _resoudre_profil(
    spec: DocumentSpec, profil: str | ProfilDocument | None
) -> ProfilDocument | None:
    """Profil explicite s'il est fourni et connu, sinon profil probable."""
    if isinstance(profil, ProfilDocument):
        return profil
    if isinstance(profil, str):
        connu = profil_par_nom(profil)
        if connu is not None:
            return connu
    return profil_probable(spec)


# --- Structure ----------------------------------------------------------------


def _valider_structure(spec: DocumentSpec, profil: ProfilDocument | None) -> list[Finding]:
    """Sections attendues, sections vides, tableaux, images, marqueurs."""
    findings: list[Finding] = []
    titres = _titres_avec_contenu(spec)

    if profil is not None:
        reconnues = sections_reconnues(profil, list(titres))
        for canonique, titre_reel in reconnues.items():
            if titre_reel is None:
                findings.append(
                    Finding(
                        code="S001",
                        gravite="erreur",
                        portee="structure",
                        message=(
                            f"Section attendue absente : « {canonique} » "
                            f"(profil {profil.libelle})."
                        ),
                        emplacement={"section": canonique, "profil": profil.nom},
                        conseil=(
                            "Ajouter la section avec du contenu réel issu des sources, "
                            "ou expliciter pourquoi elle ne s'applique pas."
                        ),
                    )
                )
                continue
            contenu = titres[titre_reel]
            if contenu["nb_mots"] < profil.mots_min_par_section:
                findings.append(
                    Finding(
                        code="S002",
                        gravite="erreur",
                        portee="structure",
                        message=(
                            f"Section « {titre_reel} » trop pauvre : "
                            f"{contenu['nb_mots']} mot(s), minimum "
                            f"{profil.mots_min_par_section}."
                        ),
                        emplacement={"section": titre_reel, "nb_mots": contenu["nb_mots"]},
                        conseil="Compléter avec les éléments du document source.",
                    )
                )

    if profil is not None and profil.exige_tableau and not any(
        isinstance(bloc, Tableau) for bloc in spec.blocs
    ):
        findings.append(
            Finding(
                code="S003",
                gravite="avertissement",
                portee="structure",
                message=f"Le profil {profil.libelle} suppose au moins un tableau.",
                emplacement={"profil": profil.nom},
                conseil="Ajouter le tableau attendu (budget, moyens, participants…).",
            )
        )

    if not titres and spec.nb_mots() > 150:
        findings.append(
            Finding(
                code="S004",
                gravite="avertissement",
                portee="structure",
                message="Document long sans aucun titre de section.",
                emplacement={"nb_blocs": len(spec.blocs)},
                conseil="Structurer le contenu en sections titrées.",
            )
        )

    findings.extend(_valider_blocs_structure(spec))
    return findings


def _valider_blocs_structure(spec: DocumentSpec) -> list[Finding]:
    """Tableaux sans en-tête, images sans référence, blocs vides, marqueurs."""
    findings: list[Finding] = []
    for index, bloc in enumerate(spec.blocs):
        if isinstance(bloc, Tableau):
            if bloc.nb_colonnes() > 2 and bloc.nb_lignes_entete() == 0:
                findings.append(
                    Finding(
                        code="S005",
                        gravite="avertissement",
                        portee="structure",
                        message=(
                            f"Tableau {index} : {bloc.nb_colonnes()} colonnes sans "
                            "ligne d'en-tête."
                        ),
                        emplacement={"bloc": index, "nb_colonnes": bloc.nb_colonnes()},
                        conseil="Déclarer l'en-tête des colonnes.",
                    )
                )
            if bloc.nb_colonnes() > MAX_COLONNES_LISIBLES:
                findings.append(
                    Finding(
                        code="S006",
                        gravite="information",
                        portee="structure",
                        message=(
                            f"Tableau {index} : {bloc.nb_colonnes()} colonnes "
                            "(lisibilité limitée)."
                        ),
                        emplacement={"bloc": index},
                    )
                )
        if isinstance(bloc, Image) and bloc.document_id is None and not bloc.chemin:
            findings.append(
                Finding(
                    code="S007",
                    gravite="erreur",
                    portee="structure",
                    message=f"Image {index} sans référence (ni document ni chemin).",
                    emplacement={"bloc": index},
                    conseil="Référencer un document existant ou un chemin du stockage.",
                )
            )
        if isinstance(bloc, (Paragraphe, Titre, Encadre)) and not bloc.texte.strip():
            findings.append(
                Finding(
                    code="S008",
                    gravite="avertissement",
                    portee="structure",
                    message=f"Bloc {index} vide.",
                    emplacement={"bloc": index, "type": bloc.type},
                )
            )
        if isinstance(bloc, Liste) and not [item for item in bloc.items if item.strip()]:
            findings.append(
                Finding(
                    code="S009",
                    gravite="avertissement",
                    portee="structure",
                    message=f"Liste {index} sans élément renseigné.",
                    emplacement={"bloc": index},
                )
            )

    marqueurs = spec.placeholders()
    if marqueurs:
        emplacements = [
            index
            for index, bloc in enumerate(spec.blocs)
            if MARQUEUR_RE.search(_texte_du_bloc(bloc))
        ]
        findings.append(
            Finding(
                code="S010",
                gravite="avertissement",
                portee="structure",
                message=(
                    f"{len(marqueurs)} donnée(s) non fournie(s) restent marquées "
                    "dans le document."
                ),
                emplacement={"blocs": emplacements},
                conseil=(
                    "Renseigner depuis les sources, ou confirmer explicitement que "
                    "l'information reste à obtenir."
                ),
            )
        )
    return findings


def _titres_avec_contenu(spec: DocumentSpec) -> dict[str, dict[str, Any]]:
    """Contenu (mots, tableaux, listes) de chaque section titrée du document."""
    sections: dict[str, dict[str, Any]] = {}
    courant: dict[str, Any] | None = None
    for bloc in spec.blocs:
        if isinstance(bloc, Titre):
            courant = {"nb_mots": 0, "nb_tableaux": 0, "nb_listes": 0, "niveau": bloc.niveau}
            sections[bloc.texte] = courant
            continue
        if courant is None:
            continue
        if isinstance(bloc, Paragraphe):
            courant["nb_mots"] += len(bloc.texte.split())
        elif isinstance(bloc, Encadre):
            courant["nb_mots"] += len(bloc.texte.split())
        elif isinstance(bloc, Liste):
            courant["nb_listes"] += 1
            courant["nb_mots"] += sum(len(item.split()) for item in bloc.items)
        elif isinstance(bloc, Tableau):
            courant["nb_tableaux"] += 1
    return sections


# --- Contenu ------------------------------------------------------------------


def _valider_contenu(spec: DocumentSpec, profil: ProfilDocument | None) -> list[Finding]:
    """Complétude réelle, cohérence budgétaire, métadonnées, répétitions, provenance."""
    findings: list[Finding] = []
    nb_mots = spec.nb_mots()

    if profil is not None and nb_mots < profil.nb_mots_min:
        findings.append(
            Finding(
                code="C001",
                gravite="erreur",
                portee="contenu",
                message=(
                    f"Contenu insuffisant pour un livrable « {profil.libelle} » : "
                    f"{nb_mots} mot(s), minimum {profil.nb_mots_min}."
                ),
                emplacement={"nb_mots": nb_mots, "minimum": profil.nb_mots_min},
                conseil=(
                    "Développer chaque section à partir du document source : un "
                    "livrable court et vide n'est pas une proposition."
                ),
            )
        )

    findings.extend(_valider_budget(spec))
    findings.extend(_valider_metadonnees(spec, profil))
    findings.extend(_valider_repetitions(spec))
    findings.extend(_valider_provenance(spec))
    return findings


def _valider_budget(spec: DocumentSpec) -> list[Finding]:
    """Un total déclaré doit être la somme de ses lignes (aucun chiffre inventé)."""
    findings: list[Finding] = []
    for index, bloc in enumerate(spec.blocs):
        if not isinstance(bloc, Tableau) or bloc.total_ligne is None:
            continue
        if bloc.total_ligne >= len(bloc.lignes_normalisees()):
            findings.append(
                Finding(
                    code="C002",
                    gravite="erreur",
                    portee="contenu",
                    message=f"Tableau {index} : ligne de total inexistante.",
                    emplacement={"bloc": index, "total_ligne": bloc.total_ligne},
                )
            )
            continue
        lignes_donnees = bloc.lignes_normalisees()
        for colonne in range(bloc.nb_colonnes()):
            valeurs = [
                _montant(ligne[colonne].texte) if colonne < len(ligne) else None
                for ligne in lignes_donnees
            ]
            lignes_hors_total = [
                valeur
                for position, valeur in enumerate(valeurs)
                if position != bloc.total_ligne
            ]
            total = valeurs[bloc.total_ligne]
            if total is None or any(valeur is None for valeur in lignes_hors_total):
                continue
            somme = sum(valeur for valeur in lignes_hors_total if valeur is not None)
            if abs(somme - total) > 0.01:
                findings.append(
                    Finding(
                        code="C003",
                        gravite="erreur",
                        portee="contenu",
                        message=(
                            f"Tableau {index}, colonne {colonne + 1} : le total "
                            f"({_montant_texte(total)}) diffère de la somme des lignes "
                            f"({_montant_texte(somme)})."
                        ),
                        emplacement={"bloc": index, "colonne": colonne},
                        conseil="Corriger le total ou les lignes : aucun chiffre n'est inventé.",
                    )
                )
    return findings


def _valider_metadonnees(
    spec: DocumentSpec, profil: ProfilDocument | None
) -> list[Finding]:
    """Métadonnées attendues sur un livrable transmis à un tiers."""
    findings: list[Finding] = []
    exige = profil is not None and profil.nom in (
        "offre_technique",
        "offre_financiere",
        "offre_autre",
        "courrier",
        "rapport",
    )
    manquants = [
        nom
        for nom, valeur in (
            ("reference", spec.metadata.reference),
            ("organisation", spec.metadata.organisation),
            ("date_document", spec.metadata.date_document),
        )
        if not valeur
    ]
    if manquants:
        findings.append(
            Finding(
                code="C004",
                gravite="erreur" if exige else "avertissement",
                portee="contenu",
                message=f"Métadonnées manquantes : {', '.join(manquants)}.",
                emplacement={"manquants": manquants},
                conseil="Renseigner ces valeurs depuis les sources (jamais devinées).",
            )
        )
    return findings


def _valider_repetitions(spec: DocumentSpec) -> list[Finding]:
    """Un même paragraphe répété signale un remplissage artificiel."""
    vus: dict[str, list[int]] = {}
    for index, bloc in enumerate(spec.blocs):
        if isinstance(bloc, Paragraphe) and len(bloc.texte) >= _LONGUEUR_REPETITION:
            cle = normaliser_titre(bloc.texte)
            vus.setdefault(cle, []).append(index)
    findings: list[Finding] = []
    for positions in vus.values():
        if len(positions) > 1:
            findings.append(
                Finding(
                    code="C005",
                    gravite="avertissement",
                    portee="contenu",
                    message=f"Paragraphe répété {len(positions)} fois aux blocs {positions}.",
                    emplacement={"blocs": positions},
                    conseil="Supprimer la répétition ou expliquer sa fonction.",
                )
            )
    return findings


def _valider_provenance(spec: DocumentSpec) -> list[Finding]:
    """Une hypothèse ne doit jamais être présentée comme un fait établi."""
    findings: list[Finding] = []
    indecises = spec.blocs_a_confirmer()
    if indecises:
        findings.append(
            Finding(
                code="C006",
                gravite="information",
                portee="contenu",
                message=(
                    f"{len(indecises)} bloc(s) dont le contenu n'est pas confirmé "
                    "(statut déclaré)."
                ),
                emplacement={"blocs": indecises},
            )
        )
    for index, bloc in enumerate(spec.blocs):
        for position in bloc.origine:
            if position >= len(spec.sources):
                findings.append(
                    Finding(
                        code="C007",
                        gravite="erreur",
                        portee="contenu",
                        message=(
                            f"Bloc {index} : source {position} inexistante "
                            f"({len(spec.sources)} source(s) déclarée(s))."
                        ),
                        emplacement={"bloc": index, "source": position},
                    )
                )
                continue
            source = spec.sources[position]
            if source.genre in ("hypothese", "information_derivee") and bloc.statut == "renseigne":
                findings.append(
                    Finding(
                        code="C008",
                        gravite="avertissement",
                        portee="contenu",
                        message=(
                            f"Bloc {index} : information {source.genre.replace('_', ' ')} "
                            "présentée comme un fait."
                        ),
                        emplacement={"bloc": index, "source": position},
                        conseil=(
                            "Marquer la donnée comme à confirmer, ou l'appuyer sur le "
                            "document source."
                        ),
                    )
                )
    return findings


# --- Style --------------------------------------------------------------------


def _valider_style(spec: DocumentSpec) -> list[Finding]:
    """Niveaux sautés, largeurs incohérentes, débordements, pagination."""
    findings: list[Finding] = []
    geometrie = spec.geometrie()
    largeur_utile = geometrie.largeur_utile_mm()

    precedent: int | None = None
    for index, bloc in enumerate(spec.blocs):
        if isinstance(bloc, Titre):
            if precedent is not None and bloc.niveau > precedent + 1:
                findings.append(
                    Finding(
                        code="Y001",
                        gravite="avertissement",
                        portee="style",
                        message=(
                            f"Bloc {index} : niveau de titre {bloc.niveau} après un "
                            f"niveau {precedent} (niveau sauté)."
                        ),
                        emplacement={"bloc": index, "niveau": bloc.niveau},
                        conseil="Utiliser le niveau intermédiaire ou remonter le titre.",
                    )
                )
            precedent = bloc.niveau

    for index, bloc in enumerate(spec.blocs):
        if isinstance(bloc, Tableau):
            nb_colonnes = bloc.nb_colonnes()
            if bloc.largeurs_mm and len(bloc.largeurs_mm) != nb_colonnes:
                findings.append(
                    Finding(
                        code="Y002",
                        gravite="erreur",
                        portee="style",
                        message=(
                            f"Tableau {index} : {len(bloc.largeurs_mm)} largeur(s) pour "
                            f"{nb_colonnes} colonne(s)."
                        ),
                        emplacement={"bloc": index},
                    )
                )
            elif bloc.largeurs_mm:
                total = sum(bloc.largeurs_mm)
                if total > largeur_utile + 0.5:
                    findings.append(
                        Finding(
                            code="Y003",
                            gravite="erreur",
                            portee="style",
                            message=(
                                f"Tableau {index} : largeur totale {round(total, 1)} mm "
                                f"supérieure à la largeur imprimable {round(largeur_utile, 1)} mm."
                            ),
                            emplacement={"bloc": index, "largeur_mm": round(total, 1)},
                            conseil="Réduire les colonnes : le tableau ne doit pas déborder.",
                        )
                    )
                elif abs(total - largeur_utile) > 10.0:
                    findings.append(
                        Finding(
                            code="Y004",
                            gravite="information",
                            portee="style",
                            message=(
                                f"Tableau {index} : {round(total, 1)} mm utilisés sur "
                                f"{round(largeur_utile, 1)} mm disponibles."
                            ),
                            emplacement={"bloc": index},
                        )
                    )
        if isinstance(bloc, Image) and bloc.largeur_mm > largeur_utile + 0.5:
            findings.append(
                Finding(
                    code="Y005",
                    gravite="erreur",
                    portee="style",
                    message=(
                        f"Image {index} : {bloc.largeur_mm} mm pour "
                        f"{round(largeur_utile, 1)} mm imprimables."
                    ),
                    emplacement={"bloc": index},
                    conseil="Réduire la largeur de l'image.",
                )
            )

    pages = estimer_pages(spec)
    if pages > 1 and spec.couverture() is None and pages > 3:
        findings.append(
            Finding(
                code="Y006",
                gravite="information",
                portee="style",
                message=f"Document estimé à {pages} pages sans page de couverture.",
                emplacement={"pages_estimees": pages},
            )
        )
    numero = (
        spec.mise_en_page.numero_de_page
        if spec.mise_en_page.numero_de_page is not None
        else spec.style_effectif().pied.numero_page
    )
    if pages > 1 and not numero:
        findings.append(
            Finding(
                code="Y007",
                gravite="avertissement",
                portee="style",
                message=f"Document multi-pages ({pages} pages) sans numérotation.",
                emplacement={"pages_estimees": pages},
                conseil="Activer la numérotation de page pour un livrable paginé.",
            )
        )
    return findings


def estimer_pages(spec: DocumentSpec) -> int:
    """Estimation **approximative** du nombre de pages du document rendu.

    Somme des hauteurs de texte (caractères → lignes → millimètres), des tableaux
    et des images. Une image est comptée à sa largeur (borne haute pour une image
    paysage ou carrée) : c'est une estimation, pas une pagination — le PDF dérivé
    donne la pagination réelle.
    """
    geometrie = spec.geometrie()
    utile = geometrie.largeur_utile_mm()
    hauteur_utile = geometrie.hauteur_utile_mm()
    if hauteur_utile <= 0:
        return 1

    hauteur = 0.0
    if spec.couverture() is not None:
        hauteur += hauteur_utile
    style = spec.style_effectif()
    for bloc in spec.blocs:
        if isinstance(bloc, Titre):
            tokens = style.titre_niveau(bloc.niveau)
            hauteur += _hauteur_texte(bloc.texte, tokens.taille_pt, tokens.interligne, utile)
            hauteur += (tokens.espace_avant_pt + tokens.espace_apres_pt) / _PT_MM
        elif isinstance(bloc, (Paragraphe, Encadre, Citation)):
            tokens = style.corps
            hauteur += _hauteur_texte(bloc.texte, tokens.taille_pt, tokens.interligne, utile)
            hauteur += (tokens.espace_avant_pt + tokens.espace_apres_pt) / _PT_MM
        elif isinstance(bloc, Liste):
            for item in bloc.items:
                hauteur += _hauteur_texte(
                    item, style.liste.taille_pt, style.liste.interligne, utile
                )
            hauteur += 4.0
        elif isinstance(bloc, Tableau):
            lignes_estimees = len(bloc.lignes) + bloc.nb_lignes_entete()
            hauteur += lignes_estimees * (style.tableau.taille_pt * 0.55 + 2.0)
        elif isinstance(bloc, Image):
            hauteur += bloc.largeur_mm
        elif isinstance(bloc, Paires):
            hauteur += len(bloc.paires) * 6.0
    return max(1, math.ceil(hauteur / hauteur_utile))


def _hauteur_texte(texte: str, taille_pt: float, interligne: float, largeur_mm: float) -> float:
    """Hauteur d'un texte sur une largeur donnée (estimation déterministe)."""
    if not texte.strip():
        return 0.0
    largeur_moyenne_caractere = max(0.6, taille_pt * 0.19)
    caracteres_par_ligne = max(10.0, largeur_mm / largeur_moyenne_caractere)
    lignes = math.ceil(len(texte) / caracteres_par_ligne)
    return lignes * taille_pt * interligne / _PT_MM


def _montant(valeur: str) -> float | None:
    """Montant d'une cellule (« 12 000,00 € » → ``12000.0``), sinon ``None``.

    Un texte sans chiffre exploitable renvoie ``None`` : la cohérence budgétaire
    n'est vérifiée que sur des colonnes réellement numériques.
    """
    if valeur is None:
        return None
    texte = str(valeur)
    correspondance = _NOMBRE.search(texte)
    if correspondance is None:
        return None
    brut = correspondance.group(0).strip().replace("\u00a0", "").replace(" ", "")
    if not brut or brut in ("-", ".", ","):
        return None
    if "," in brut and "." in brut:
        # Un seul séparateur décimal : celui le plus à droite (« 1.250,50 »).
        separateur = "," if brut.rindex(",") > brut.rindex(".") else "."
        milliers = "." if separateur == "," else ","
        brut = brut.replace(milliers, "").replace(separateur, ".")
    else:
        brut = brut.replace(",", ".")
    try:
        return float(brut)
    except ValueError:
        return None


def _montant_texte(valeur: float) -> str:
    """Formate un montant pour un message de validation."""
    return f"{valeur:,.2f}".replace(",", " ")


def _texte_du_bloc(bloc: Any) -> str:
    """Tous les textes d'un bloc, concaténés (recherche de marqueurs)."""
    morceaux: list[str] = []

    def _collecter(valeur: Any) -> None:
        if isinstance(valeur, str):
            morceaux.append(valeur)
        elif isinstance(valeur, dict):
            for item in valeur.values():
                _collecter(item)
        elif isinstance(valeur, (list, tuple)):
            for item in valeur:
                _collecter(item)

    _collecter(bloc.model_dump())
    return " ".join(morceaux)
