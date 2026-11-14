"""Représentation documentaire structurée—``DocumentSpec`` (ADR 0005, Phase 2).

Le LLM ne produit **jamais** de XML DOCX ni de séquence de formatage—il produit
une structure (titres, sections, tableaux, images) et choisit un style. Le code
décide comment cette structure devient un DOCX, un PDF ou du HTML.

```text
LLM → DocumentSpec → Renderer → DOCX / PDF / HTML
```

Propriétés de ce module :

- **strict**—tout modèle interdit les champs inconnus (``extra="forbid"``)—
  une donnée non prévue est une erreur, pas un champ ignoré ;
- **provenance**—chaque bloc peut désigner ses sources (``Source``) et son
  statut de remplissage (``renseigne``/``inconnu``/``manquant``/``a_confirmer``),
  ce qui rend vérifiable la règle « aucun fait inventé » (Phase 16) ;
- **sans dépendance tierce**—uniquement Pydantic—le spec est constructible,
  sérialisable et testable sans python-docx ni fpdf2 ;
- **formatable et validable**—``placeholders()``, ``texte_brut()`` et
  ``resume()`` alimentent la couche de validation sans re-parser un fichier.
"""

from __future__ import annotations

import base64
import binascii
import re
from collections.abc import Sequence
from datetime import date
from typing import Annotated, Any, Literal, NamedTuple
from unicodedata import category, normalize
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.errors import ValidationError
from app.documents.images import OCTETS_MAX
from app.documents.styles import (
    FORMATS_MM,
    DocumentStyle,
    Marges,
    style_par_nom,
)

__all__ = [
    "MARQUEUR_A_COMPLETER",
    "MARQUEUR_A_CONFIRMER",
    "MARQUEUR_RE",
    "Bloc",
    "Cellule",
    "CelluleRef",
    "Citation",
    "Couverture",
    "DocumentSpec",
    "Encadre",
    "Espacement",
    "Forme",
    "Geometrie",
    "Graphique",
    "Illustration",
    "Image",
    "ItemIllustration",
    "Liste",
    "Metadonnees",
    "MiseEnPage",
    "Paire",
    "Paires",
    "Paragraphe",
    "SautDePage",
    "Segment",
    "SerieGraphique",
    "Sommaire",
    "Source",
    "Tableau",
    "Titre",
    "bloc_par_type",
    "genres_source",
]

#: Statut de renseignement d'un bloc : jamais un trou silencieux.
StatutRenseignement = Literal["renseigne", "inconnu", "manquant", "a_confirmer"]

#: Genres de source (Phase 16) : une information dérivée n'est jamais présentée
#: comme une information explicitement fournie.
GENRES_SOURCE = (
    "document_source",
    "base_de_donnees",
    "saisie_utilisateur",
    "modele_reference",
    "information_derivee",
    "hypothese",
)

#: Couleur hexadécimale acceptée par les cellules et les styles (``#1F4E79``).
_HEX = r"^#[0-9A-Fa-f]{6}$"

#: Marqueur conventionnel d'une donnée absente, visible par l'humain.
MARQUEUR_A_COMPLETER = "(à compléter)"

#: Marqueur conventionnel d'une donnée à faire confirmer par l'utilisateur.
MARQUEUR_A_CONFIRMER = "(à confirmer)"

#: Reconnaît les deux marqueurs (sans accent, casse libre — saisie tolérante).
MARQUEUR_RE = re.compile(r"\([àa]\s*(?:compl[eé]ter|confirmer)\)", re.IGNORECASE)


def genres_source() -> tuple[str, ...]:
    """Genres de source acceptés (exposés aux tools pour un schéma lisible)."""
    return GENRES_SOURCE


class Source(BaseModel):
    """Provenance d'une information du document.

    Une source de genre ``hypothese`` ou ``information_derivee`` ne doit jamais
    être présentée à l'utilisateur comme un fait du document source.
    """

    model_config = ConfigDict(extra="forbid")

    genre: Literal[
        "document_source",
        "base_de_donnees",
        "saisie_utilisateur",
        "modele_reference",
        "information_derivee",
        "hypothese",
    ]
    reference: str | None = Field(default=None, max_length=200)
    precision: str | None = Field(default=None, max_length=500)


class _BlocBase(BaseModel):
    """Base commune : identifiant de bloc stable, sources, statut."""

    model_config = ConfigDict(extra="forbid")

    #: Index (0-based) dans ``DocumentSpec.sources`` — jamais des UUID libres.
    origine: list[int] = Field(default_factory=list)
    statut: StatutRenseignement = "renseigne"

    @field_validator("origine")
    @classmethod
    def _origines_positives(cls, valeur: list[int]) -> list[int]:
        if any(index < 0 for index in valeur):
            raise ValueError("Un index de source ne peut pas être négatif")
        return valeur


class Titre(_BlocBase):
    """Titre de section (niveau 1 à 4)."""

    type: Literal["titre"] = "titre"
    niveau: int = Field(default=1, ge=1, le=4)
    texte: str = Field(min_length=1, max_length=300)


class Segment(BaseModel):
    """Fragment de texte d'un paragraphe ou d'une cellule (INC-130).

    Sert quand une partie de la phrase change de forme (mot en gras, montant en
    couleur). Les champs laissés à ``None`` gardent le style du bloc.
    """

    model_config = ConfigDict(extra="forbid")

    texte: str = Field(min_length=1, max_length=5000)
    gras: bool | None = None
    italique: bool | None = None
    souligne: bool | None = None
    barre: bool | None = None
    surligne: Literal[
        "jaune", "vert", "cyan", "magenta", "rouge", "bleu", "gris", "noir"
    ] | None = Field(
        default=None,
        description=(
            "Marqueur de texte (surlignage Word). Uniquement ces huit teintes : "
            "ce sont celles que Word affiche de façon fiable."
        ),
    )
    couleur: str | None = Field(default=None, pattern=_HEX)
    police: str | None = Field(default=None, min_length=1, max_length=80)
    taille_pt: float | None = Field(default=None, gt=4, le=72)


class Paragraphe(_BlocBase):
    """Paragraphe de corps, de chapeau (accroche) ou de note.

    Deux formes exclusives : ``texte`` (une seule graphie) ou ``runs`` (fragments
    stylés). Les champs de mise en forme sont des **surcharges** du rôle : ce
    qui n'est pas renseigné garde le style du document.
    """

    type: Literal["paragraphe"] = "paragraphe"
    texte: str = Field(default="", max_length=20000)
    runs: list[Segment] | None = Field(
        default=None,
        max_length=200,
        description=(
            "Fragments stylés, dans l'ordre de lecture : {texte, gras, italique, "
            "souligne, barre, surligne, couleur, police, taille_pt}. Exclusif "
            "avec ``texte``."
        ),
    )
    role: Literal["corps", "chapeau", "note"] = "corps"
    alignement: Literal["gauche", "centre", "droite", "justifie"] | None = None
    espace_avant_pt: float | None = Field(default=None, ge=0, le=120)
    espace_apres_pt: float | None = Field(default=None, ge=0, le=120)
    interligne: float | None = Field(default=None, ge=0.8, le=3.0)
    retrait_mm: float | None = Field(default=None, ge=0, le=100)
    retrait_premiere_ligne_mm: float | None = Field(default=None, ge=-50, le=100)
    police: str | None = Field(default=None, min_length=1, max_length=80)
    taille_pt: float | None = Field(default=None, gt=4, le=72)
    couleur: str | None = Field(default=None, pattern=_HEX)

    @model_validator(mode="after")
    def _contenu_unique(self) -> Paragraphe:
        if self.runs and self.texte.strip():
            raise ValueError("Paragraphe : fournir 'texte' OU 'runs', pas les deux.")
        if not self.runs and not self.texte.strip():
            raise ValueError("Paragraphe : 'texte' ou 'runs' est requis.")
        return self


class Liste(_BlocBase):
    """Liste à puces ou numérotée, éventuellement imbriquée."""

    type: Literal["liste"] = "liste"
    items: list[str] = Field(min_length=1, max_length=200)
    numerotee: bool = False
    profondeur: int = Field(default=0, ge=0, le=3)


class Cellule(BaseModel):
    """Cellule de tableau : contenu et présentation propre à la cellule.

    Une cellule ordinaire s'écrit comme une chaîne. Cette forme sert à fusionner,
    tramer ou mettre en gras. Fusions comme HTML : ``fusion_colonnes`` occupe des
    colonnes de la même ligne, ``fusion_lignes`` réserve les lignes suivantes
    (elles n'écrivent rien à ces colonnes), ``"fin"`` va jusqu'à la dernière.
    """

    model_config = ConfigDict(extra="forbid")

    texte: str = Field(default="", max_length=5000)
    fusion_colonnes: int | Literal["fin"] = Field(
        default=1,
        description="Colonnes occupées (entier ≥ 1) ou 'fin' = jusqu'à la dernière colonne.",
    )
    fusion_lignes: int = Field(default=1, ge=1, le=100)
    fond: str | None = Field(default=None, pattern=_HEX)
    gras: bool | None = None
    alignement: Literal["gauche", "centre", "droite"] | None = None
    couleur_texte: str | None = Field(default=None, pattern=_HEX)
    taille_pt: float | None = Field(default=None, gt=4, le=24)
    italique: bool | None = None
    souligne: bool | None = None
    runs: list[Segment] | None = Field(
        default=None,
        max_length=60,
        description="Fragments stylés de la cellule (exclusif avec ``texte``).",
    )
    marge_interne_mm: float | None = Field(
        default=None,
        ge=0,
        le=10,
        description="Marge intérieure de la cellule (padding), en mm.",
    )
    alignement_vertical: Literal["haut", "centre", "bas"] | None = None

    @model_validator(mode="after")
    def _contenu_cellule(self) -> Cellule:
        if self.runs and self.texte:
            raise ValueError("Cellule : fournir 'texte' OU 'runs', pas les deux.")
        return self

    def texte_complet(self) -> str:
        """Texte de la cellule, fragments assemblés si elle en contient."""
        if self.runs:
            return "".join(segment.texte for segment in self.runs)
        return self.texte

    @field_validator("fusion_colonnes")
    @classmethod
    def _fusion_colonnes_valide(cls, valeur: int | str) -> int | str:
        if isinstance(valeur, int) and valeur < 1:
            raise ValueError("Une cellule occupe au moins une colonne")
        return valeur

    def span(self) -> int | Literal["fin"]:
        """Colonnes occupées (``"fin"`` tant que la largeur de grille est inconnue)."""
        return self.fusion_colonnes


#: Cellule d'un tableau : texte simple, ou cellule décrite (fusions, trame…).
CelluleRef = Cellule | str


class _CellulePlacée(NamedTuple):
    """Cellule normalisée et sa position dans la **grille** du tableau."""

    cellule: Cellule
    colonne: int
    """Colonne de départ, 0-based, dans la grille (fusions comprises)."""
    colonnes: int
    """Colonnes réellement occupées (``"fin"`` déjà résolu)."""
    ligne: int
    lignes: int


class Tableau(_BlocBase):
    """Tableau : en-tête(s), lignes, fusions, largeurs par colonne.

    En-tête : ``entete`` (une ligne) OU ``entetes`` (plusieurs, avec fusions),
    jamais les deux. Sans en-tête, la première ligne de ``lignes`` est une donnée.
    """

    type: Literal["tableau"] = "tableau"
    titre_tableau: str | None = Field(default=None, max_length=200)
    entete: list[str] | None = Field(
        default=None,
        max_length=60,
        description="Une ligne d'en-tête simple (titres de colonnes).",
    )
    entetes: Sequence[Sequence[CelluleRef]] | None = Field(
        default=None,
        max_length=6,
        description="Plusieurs lignes d'en-tête (en-tête à étages, avec fusions).",
    )
    lignes: Sequence[Sequence[CelluleRef]] = Field(min_length=1, max_length=500)
    largeurs_mm: list[float] | None = Field(default=None, max_length=60)
    alignements: list[Literal["gauche", "centre", "droite"]] | None = Field(
        default=None, max_length=60
    )
    repeter_entete: bool = True
    total_ligne: int | None = Field(
        default=None,
        description="Index (0-based) d'une ligne de total, mise en évidence.",
    )
    marges_cellules_mm: float | None = Field(
        default=None,
        ge=0,
        le=10,
        description="Padding intérieur appliqué à toutes les cellules (mm).",
    )
    bordures: Literal["aucune", "fine", "moyenne", "epaisse"] | None = Field(
        default=None,
        description="Filets du tableau : 'aucune' les supprime, sinon épaisseur.",
    )
    couleur_bordures: str | None = Field(default=None, pattern=_HEX)
    couleur_entete: str | None = Field(
        default=None,
        pattern=_HEX,
        description="Trame des lignes d'en-tête (sauf cellule au fond explicite).",
    )
    bandes_alternees: bool | None = Field(
        default=None, description="Lignes de données alternées, surcharge le style."
    )
    hauteur_lignes_mm: float | None = Field(
        default=None, gt=0, le=100, description="Hauteur minimale des lignes (mm)."
    )

    @field_validator("lignes")
    @classmethod
    def _lignes_non_vides(
        cls, lignes: Sequence[Sequence[CelluleRef]]
    ) -> Sequence[Sequence[CelluleRef]]:
        for index, ligne in enumerate(lignes):
            if not ligne:
                raise ValueError(f"Ligne {index + 1} du tableau vide")
        return lignes

    @model_validator(mode="after")
    def _en_tete_non_ambigue(self) -> Tableau:
        if self.entete is not None and self.entetes is not None:
            raise ValueError(
                "Fournir 'entete' (une ligne) OU 'entetes' (plusieurs lignes), pas les deux."
            )
        return self

    # --- Normalisation -------------------------------------------------------

    def lignes_entete(self) -> list[list[Cellule]]:
        """Lignes d'en-tête normalisées (``[]`` si le tableau n'a pas d'en-tête)."""
        if self.entetes is not None:
            return [_normaliser_cellules(ligne) for ligne in self.entetes]
        if self.entete is not None:
            return [_normaliser_cellules(self.entete)]
        return []

    def lignes_normalisees(self) -> list[list[Cellule]]:
        """Lignes de données normalisées (chaque cellule est un objet ``Cellule``)."""
        return [_normaliser_cellules(ligne) for ligne in self.lignes]

    def lignes_toutes(self) -> list[list[Cellule]]:
        """En-tête(s) puis données — l'ordre réel du rendu."""
        return self.lignes_entete() + self.lignes_normalisees()

    def nb_lignes_entete(self) -> int:
        """Nombre de lignes d'en-tête (0 si aucune)."""
        return len(self.lignes_entete())

    # --- Grille --------------------------------------------------------------

    def largeur_grille(self) -> int:
        """Nombre de colonnes de la grille, fusions comprises.

        Une ligne entièrement en ``"fin"`` ne peut pas définir la largeur : au
        moins une ligne doit déclarer des fusions explicites (ou ``largeurs_mm``
        doit les donner) — sinon l'erreur est explicite plutôt qu'un tableau
        silencieusement tronqué.

        Raises:
            ValidationError: largeur indéterminable.
        """
        candidats: list[int] = []
        for ligne in self.lignes_toutes():
            if any(cellule.span() == "fin" for cellule in ligne):
                continue
            candidats.append(sum(int(cellule.span()) for cellule in ligne))
        if self.largeurs_mm:
            candidats.append(len(self.largeurs_mm))
        if not candidats:
            raise ValidationError(
                "Tableau : impossible de déterminer le nombre de colonnes "
                "(toutes les cellules utilisent 'fin').",
                details={"conseil": "Déclarer au moins une ligne avec des fusions chiffrées."},
            )
        return max(candidats)

    def nb_colonnes(self) -> int:
        """Nombre de colonnes du tableau (grille, fusions comprises)."""
        return self.largeur_grille()

    def grille(self) -> list[list[_CellulePlacée]]:
        """Place chaque cellule sur la grille, ligne par ligne, colonne par colonne.

        Une cellule fusionnée verticalement réserve ses colonnes sur les lignes
        suivantes : celles-ci n'écrivent donc rien à ces positions. Chaque ligne
        doit couvrir exactement la largeur du tableau — un tableau bancal est
        refusé avec un message précis, jamais rendu de travers.

        Raises:
            ValidationError: ligne incomplète ou fusion hors de la grille.
        """
        largeur = self.largeur_grille()
        lignes = self.lignes_toutes()
        occupe: dict[tuple[int, int], int] = {}
        placees: list[list[_CellulePlacée]] = []
        for index_ligne, ligne in enumerate(lignes):
            colonne = 0
            rangees: list[_CellulePlacée] = []
            for position, cellule in enumerate(ligne):
                while (index_ligne, colonne) in occupe:
                    colonne += 1
                span = cellule.span()
                colonnes = largeur - colonne if span == "fin" else int(span)
                if colonne >= largeur or colonne + colonnes > largeur:
                    raise ValidationError(
                        f"Tableau, ligne {index_ligne + 1}, cellule {position + 1} : "
                        f"la cellule occupe {colonnes} colonne(s) à partir de la "
                        f"colonne {colonne + 1}, au-delà des {largeur} colonnes du "
                        "tableau.",
                        details={
                            "colonnes_tableau": largeur,
                            "colonne_depart": colonne + 1,
                            "colonnes_cellule": colonnes,
                        },
                    )
                for ligne_visee in range(index_ligne, index_ligne + cellule.fusion_lignes):
                    for colonne_visee in range(colonne, colonne + colonnes):
                        occupe[(ligne_visee, colonne_visee)] = index_ligne
                rangees.append(
                    _CellulePlacée(
                        cellule=cellule,
                        colonne=colonne,
                        colonnes=colonnes,
                        ligne=index_ligne,
                        lignes=cellule.fusion_lignes,
                    )
                )
                colonne += colonnes
            manquantes = [
                index
                for index in range(largeur)
                if (index_ligne, index) not in occupe
            ]
            if manquantes:
                raise ValidationError(
                    f"Tableau, ligne {index_ligne + 1} : {len(manquantes)} colonne(s) "
                    f"non couverte(s) (colonnes {[index + 1 for index in manquantes]}).",
                    details={"largeur": largeur},
                )
            placees.append(rangees)
        return placees

    def cellules_texte(self) -> list[str]:
        """Tous les textes des cellules, en-tête(s) compris."""
        return [
            cellule.texte_complet()
            for ligne in self.lignes_toutes()
            for cellule in ligne
        ]


def _normaliser_cellules(ligne: Sequence[CelluleRef]) -> list[Cellule]:
    """Convertit une ligne brute en cellules (une chaîne devient un texte)."""
    return [
        cellule if isinstance(cellule, Cellule) else Cellule(texte=cellule)
        for cellule in ligne
    ]


def format_image(octets: bytes) -> str | None:
    """Extension d'après la **signature** des octets (jamais d'après un nom).

    Une image inline est une donnée non fiable : son format se lit dans ses
    premiers octets. Un contenu non reconnu est refusé à la construction du
    spec, pas au moment d'insérer une image cassée dans un livrable.
    """
    if octets.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if octets.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if octets[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if octets[:4] == b"RIFF" and octets[8:12] == b"WEBP":
        return "webp"
    return None


class Image(_BlocBase):
    """Image référencée : document enregistré, chemin logique, SVG ou base64.

    Aucun chemin absolu : la résolution passe par le stockage, donc pas de
    traversée. Une seule source, exclusive : ``document_id`` (document
    enregistré), ``chemin`` (relatif au stockage), ``svg`` (assaini puis rendu,
    en PNG pour les formats sans SVG) ou ``base64`` (réduit avant insertion).
    """

    type: Literal["image"] = "image"
    document_id: UUID | None = None
    chemin: str | None = Field(default=None, max_length=400)
    svg: str | None = Field(
        default=None,
        max_length=200_000,
        description=(
            "SVG inline (schéma, organigramme, illustration dessinée) — assaini "
            "avant rendu. Exclusif avec document_id et chemin."
        ),
    )
    base64: str | None = Field(
        default=None,
        max_length=12_000_000,
        description=(
            "Image inline en base64 (PNG, JPEG, GIF, WebP), avec ou sans préfixe "
            "``data:image/...;base64,`` — le moteur la décode et l'insère. "
            "Exclusif avec document_id, chemin et svg."
        ),
    )
    largeur_mm: float = Field(default=120.0, gt=0, le=500)
    legende: str | None = Field(default=None, max_length=300)
    alt: str | None = Field(default=None, max_length=300)

    @model_validator(mode="after")
    def _source_unique(self) -> Image:
        """Une seule source : deux références feraient douter de la vraie."""
        sources = [
            nom
            for nom, valeur in (
                ("document_id", self.document_id),
                ("chemin", self.chemin),
                ("svg", self.svg),
                ("base64", self.base64),
            )
            if valeur
        ]
        if len(sources) > 1:
            raise ValueError(
                "Image : une seule source acceptée (document_id, chemin, svg ou "
                f"base64), reçu {sources}"
            )
        return self

    @field_validator("base64")
    @classmethod
    def _base64_valide(cls, valeur: str | None) -> str | None:
        """Vérifie le base64 **à la construction** : l'erreur arrive tôt.

        Le préfixe ``data:image/...;base64,`` est retiré : le spec garde la
        charge utile seule, et le format réel est celui des octets décodés —
        jamais celui qu'un nom de fichier prétendrait.
        """
        if valeur is None:
            return None
        propre = "".join(valeur.split())
        if propre[:5].lower() == "data:":
            _, _, propre = propre.partition(",")
            propre = "".join(propre.split())
        if not propre:
            raise ValueError("Image base64 vide")
        try:
            octets = base64.b64decode(propre, validate=True)
        except (binascii.Error, ValueError) as erreur:
            raise ValueError(
                "Image base64 illisible : encodage base64 invalide"
            ) from erreur
        if len(octets) > OCTETS_MAX:
            raise ValueError(
                f"Image base64 trop lourde ({len(octets)} octets) : maximum {OCTETS_MAX}"
            )
        if format_image(octets) is None:
            raise ValueError(
                "Image base64 non reconnue : formats acceptés PNG, JPEG, GIF, WebP"
            )
        return propre

    @field_validator("chemin")
    @classmethod
    def _chemin_relatif(cls, valeur: str | None) -> str | None:
        if valeur is None:
            return None
        propre = valeur.strip().replace("\\", "/")
        if propre.startswith("/") or ".." in propre.split("/") or ":" in propre:
            raise ValueError("Chemin d'image non relatif (traversée refusée)")
        return propre

class Encadre(_BlocBase):
    """Encadré mis en évidence (information, note, avertissement, succès)."""

    type: Literal["encadre"] = "encadre"
    genre: Literal["info", "note", "avertissement", "succes"] = "info"
    titre: str | None = Field(default=None, max_length=200)
    texte: str = Field(min_length=1, max_length=5000)

    def libelle_affiche(self, defaut: str | None = None) -> str | None:
        """Libellé à écrire avant le texte, ``None`` s'il figure déjà dedans.

        Les encadrés réels commencent souvent par leur propre libellé
        (« Note : … », « Information manquante : … ») : le répéter donnerait
        « Note — Note : … ». La comparaison ignore casse, accents et
        ponctuation, mais exige une frontière de mot (« Info » ne masque pas
        « Informations »).
        """
        libelle = (self.titre or defaut or "").strip()
        if not libelle:
            return None
        if _cle_libelle(self.texte).startswith(f"{_cle_libelle(libelle)} "):
            return None
        return libelle


def _cle_libelle(texte: str) -> str:
    """Forme comparable d'un libellé : sans accent, casse, ni ponctuation."""
    sans_accent = "".join(c for c in normalize("NFD", texte) if category(c) != "Mn")
    return re.sub(r"[^a-z0-9]+", " ", sans_accent.casefold()).strip()


class Citation(_BlocBase):
    """Citation, éventuellement attribuée (extrait du document source)."""

    type: Literal["citation"] = "citation"
    texte: str = Field(min_length=1, max_length=5000)
    attribution: str | None = Field(default=None, max_length=200)


class Paire(BaseModel):
    """Couple libellé/valeur (couverture, synthèse)."""

    model_config = ConfigDict(extra="forbid")

    libelle: str = Field(min_length=1, max_length=120)
    valeur: str = Field(default="", max_length=1000)


class Paires(_BlocBase):
    """Bloc de couples libellé/valeur (fiche d'identité, synthèse)."""

    type: Literal["paires"] = "paires"
    paires: list[Paire] = Field(min_length=1, max_length=60)
    colonnes: int = Field(default=1, ge=1, le=3)


class SerieGraphique(BaseModel):
    """Une série nommée d'un graphique à axes (bar/line)."""

    model_config = ConfigDict(extra="forbid")

    nom: str = Field(min_length=1, max_length=60)
    valeurs: list[float] = Field(min_length=1, max_length=60)


class Graphique(_BlocBase):
    """Graphique de données : le serveur dessine, l'agent donne les chiffres.

    SVG construit par ``figures.render_chart`` (palette et graduations fixes,
    libellés échappés). ``bar``/``line`` exigent ``series`` (un nom, une valeur
    par label) ; ``pie``/``donut`` exigent ``valeurs`` (une par label, ≥ 0).
    ``legende`` devient la légende imprimée ; absente, elle n'est pas inventée.
    """

    type: Literal["graphique"] = "graphique"
    variante: Literal["bar", "line", "pie", "donut"] = Field(
        default="bar",
        description=(
            "'bar' : barres groupées (comparer des séries par catégorie) ; "
            "'line' : courbes (évolution) ; 'pie' : camembert ; 'donut' : anneau."
        ),
    )
    titre: str | None = Field(default=None, max_length=200)
    labels: list[str] = Field(min_length=1, max_length=60)
    series: list[SerieGraphique] | None = Field(default=None, max_length=8)
    valeurs: list[float] | None = Field(default=None, max_length=60)
    y_label: str | None = Field(default=None, max_length=40)
    legende: str | None = Field(default=None, max_length=300)
    largeur_px: int = Field(default=640, ge=200, le=2400)
    hauteur_px: int = Field(default=400, ge=150, le=1600)
    largeur_mm: float = Field(default=160.0, gt=0, le=500)
    total_reference: float | None = Field(
        default=None,
        gt=0,
        description=(
            "pie/donut : total de référence des pourcentages, quand le total réel "
            "n'est pas la somme des parts affichées (frais imprévus, autres "
            "postes). Par défaut : la somme des valeurs."
        ),
    )
    afficher_valeurs: bool = Field(
        default=False,
        description=(
            "pie/donut : écrire les montants dans la légende, avant le pourcentage."
        ),
    )

    @model_validator(mode="after")
    def _options_de_camembert(self) -> Graphique:
        """Les options de pourcentage ne valent que pour un camembert/anneau."""
        if self.variante in ("bar", "line") and (
            self.total_reference is not None or self.afficher_valeurs
        ):
            raise ValueError(
                "Graphique bar/line : 'total_reference' et 'afficher_valeurs' ne "
                "concernent que les variantes 'pie' et 'donut'."
            )
        return self

    @model_validator(mode="after")
    def _donnees_de_la_variante(self) -> Graphique:
        """Chaque variante exige sa forme de données — refus explicite sinon."""
        if self.variante in ("bar", "line"):
            if not self.series:
                raise ValueError(
                    f"Graphique {self.variante} : 'series' est requis "
                    "([{nom, valeurs}], une valeur par label)."
                )
            for serie in self.series:
                if len(serie.valeurs) != len(self.labels):
                    raise ValueError(
                        f"Graphique : la série {serie.nom!r} a {len(serie.valeurs)} "
                        f"valeur(s) pour {len(self.labels)} label(s) — il en faut autant."
                    )
            if self.valeurs:
                raise ValueError(
                    "Graphique bar/line : utiliser 'series', pas 'valeurs' "
                    "(réservé à pie/donut)."
                )
        else:
            if not self.valeurs:
                raise ValueError(
                    f"Graphique {self.variante} : 'valeurs' est requis (une par label, ≥ 0)."
                )
            if len(self.valeurs) != len(self.labels):
                raise ValueError(
                    f"Graphique : {len(self.valeurs)} valeur(s) pour "
                    f"{len(self.labels)} label(s) — il en faut autant."
                )
            if self.series:
                raise ValueError(
                    "Graphique pie/donut : utiliser 'valeurs', pas 'series'."
                )
        return self


class ItemIllustration(BaseModel):
    """Un item d'illustration : un titre court, un texte court, une icône nommée."""

    model_config = ConfigDict(extra="forbid")

    titre: str | None = Field(default=None, max_length=40)
    texte: str | None = Field(default=None, max_length=80)
    icone: str | None = Field(default=None, max_length=30)


class Illustration(_BlocBase):
    """Illustration de composition : géométrie et couleurs fixées par le serveur.

    L'agent choisit une composition et fournit des libellés courts ; espacements,
    tailles et couleurs viennent du thème. Évite les schémas dessinés à la main.
    """

    type: Literal["illustration"] = "illustration"
    variante: Literal[
        "icones", "etapes", "cycle", "pyramide", "frise", "comparaison"
    ] = Field(
        default="etapes",
        description=(
            "Composition : 'icones' (cartes d'atouts), 'etapes' (processus "
            "linéaire), 'cycle' (boucle), 'pyramide' (niveaux), 'frise' "
            "(chronologie), 'comparaison' (deux colonnes)."
        ),
    )
    titre: str | None = Field(default=None, max_length=120)
    items: list[ItemIllustration] = Field(min_length=1, max_length=8)
    legende: str | None = Field(default=None, max_length=300)
    largeur_mm: float = Field(default=160.0, gt=0, le=500)


class SautDePage(_BlocBase):
    """Saut de page explicite."""

    type: Literal["saut_de_page"] = "saut_de_page"


class Sommaire(_BlocBase):
    """Table des matières paginée (champ Word ``TOC``), pas une liste recopiée.

    Les numéros de page sont calculés par le lecteur à partir des styles de
    titre : le serveur ne peut pas les écrire avant rendu, il ne les invente pas.
    """

    type: Literal["sommaire"] = "sommaire"
    titre: str | None = Field(default="Sommaire", max_length=120)
    niveaux: int = Field(default=3, ge=1, le=4)
    avec_numeros_de_page: bool = True


class Forme(_BlocBase):
    """Élément graphique de mise en page (bandeau, filet, étiquette).

    Dessiné par le serveur en SVG : l'agent donne une intention et un texte,
    jamais des coordonnées.
    """

    type: Literal["forme"] = "forme"
    variante: Literal["bandeau", "separateur", "etiquette"] = Field(
        default="bandeau",
        description=(
            "'bandeau' : plaque pleine avec un titre ; 'separateur' : filet "
            "horizontal ; 'etiquette' : pastille arrondie courte."
        ),
    )
    texte: str | None = Field(default=None, max_length=200)
    couleur: str = Field(default="#1F4E79", pattern=_HEX)
    fond: str | None = Field(default=None, pattern=_HEX)
    largeur_mm: float | None = Field(default=None, gt=0, le=500)
    hauteur_mm: float | None = Field(default=None, gt=0, le=200)

    @model_validator(mode="after")
    def _texte_du_bandeau(self) -> Forme:
        if self.variante != "separateur" and not (self.texte or "").strip():
            raise ValueError(f"Forme {self.variante} : 'texte' est requis.")
        return self


class Espacement(_BlocBase):
    """Blanc vertical explicite entre deux blocs (respiration de la page)."""

    type: Literal["espacement"] = "espacement"
    hauteur_mm: float = Field(gt=0, le=100)


#: Union discriminée : le champ ``type`` sélectionne le modèle concret.
Bloc = Annotated[
    Titre
    | Paragraphe
    | Liste
    | Tableau
    | Image
    | Graphique
    | Illustration
    | Encadre
    | Citation
    | Paires
    | SautDePage
    | Sommaire
    | Forme
    | Espacement,
    Field(discriminator="type"),
]

_BLOCS_PAR_TYPE: dict[str, type[BaseModel]] = {
    "titre": Titre,
    "paragraphe": Paragraphe,
    "liste": Liste,
    "tableau": Tableau,
    "image": Image,
    "graphique": Graphique,
    "illustration": Illustration,
    "encadre": Encadre,
    "citation": Citation,
    "paires": Paires,
    "saut_de_page": SautDePage,
    "sommaire": Sommaire,
    "forme": Forme,
    "espacement": Espacement,
}


class Metadonnees(BaseModel):
    """Identité du document, réutilisée par la couverture et l'en-tête."""

    model_config = ConfigDict(extra="forbid")

    titre: str = Field(min_length=1, max_length=300)
    sous_titre: str | None = Field(default=None, max_length=300)
    reference: str | None = Field(default=None, max_length=120)
    organisation: str | None = Field(default=None, max_length=200)
    destinataire: str | None = Field(default=None, max_length=200)
    auteur: str | None = Field(default=None, max_length=200)
    date_document: date | None = None
    objet: str | None = Field(default=None, max_length=500)
    type_document: str | None = Field(default=None, max_length=60)
    mots_cles: list[str] = Field(default_factory=list, max_length=20)


class Couverture(BaseModel):
    """Page de couverture : contenu explicite (aucune valeur devinée)."""

    model_config = ConfigDict(extra="forbid")

    active: bool = True
    titre: str | None = Field(default=None, max_length=300)
    sous_titre: str | None = Field(default=None, max_length=300)
    organisation: str | None = Field(default=None, max_length=200)
    logo_document_id: UUID | None = None
    logo_chemin: str | None = Field(default=None, max_length=400)
    champs: list[Paire] = Field(default_factory=list, max_length=20)
    date_ligne: str | None = Field(default=None, max_length=120)


class Geometrie(BaseModel):
    """Géométrie **résolue** d'une page (largeur/hauteur réelles, en mm)."""

    model_config = ConfigDict(extra="forbid")

    format: str
    orientation: Literal["portrait", "paysage"]
    largeur_mm: float
    hauteur_mm: float
    marges: Marges

    def largeur_utile_mm(self) -> float:
        """Largeur imprimable (page − marges gauche/droite)."""
        return max(20.0, self.largeur_mm - self.marges.gauche_mm - self.marges.droite_mm)

    def hauteur_utile_mm(self) -> float:
        """Hauteur imprimable (page − marges haut/bas)."""
        return max(20.0, self.hauteur_mm - self.marges.haut_mm - self.marges.bas_mm)


class MiseEnPage(BaseModel):
    """Surcharges de mise en page propres au document (le style donne le défaut).

    Un champ renseigné ici l'emporte explicitement ; ``None`` laisse le style
    décider — aucune valeur implicite cachée.
    """

    model_config = ConfigDict(extra="forbid")

    format: Literal["A4", "Letter"] | None = None
    orientation: Literal["portrait", "paysage"] | None = None
    marges: Marges | None = None
    couverture: Couverture | None = None
    entete_texte: str | None = Field(default=None, max_length=200)
    pied_texte: str | None = Field(default=None, max_length=200)
    numero_de_page: bool | None = None
    numero_de_page_debut: int | None = Field(
        default=None,
        ge=0,
        le=9999,
        description="Numéro de la première page (la couverture compte comme 1).",
    )
    mention_proposition: str | None = Field(
        default="Document proposé — non officiel avant validation humaine.",
        max_length=300,
    )


class DocumentSpec(BaseModel):
    """Le document, indépendamment de tout format de fichier.

    ``style`` est facultatif : ``None`` signifie « style CARSO par défaut ».
    """

    model_config = ConfigDict(extra="forbid")

    metadata: Metadonnees
    mise_en_page: MiseEnPage = Field(default_factory=MiseEnPage)
    style: DocumentStyle | None = None
    profil: str | None = Field(default=None, max_length=60)
    sources: list[Source] = Field(default_factory=list, max_length=100)
    blocs: list[Bloc] = Field(default_factory=list, max_length=2000)

    # --- Résolutions ---------------------------------------------------------

    def style_effectif(self) -> DocumentStyle:
        """Style du document (``CARSO_DEFAUT`` si aucun n'est défini)."""
        return self.style if self.style is not None else style_par_nom(None)

    def geometrie(self) -> Geometrie:
        """Géométrie résolue : surcharge du document > style, avec dimensions calculées."""
        style = self.style_effectif()
        format_ = self.mise_en_page.format or style.page.format
        orientation = self.mise_en_page.orientation or style.page.orientation
        marges = self.mise_en_page.marges or style.page.marges
        if format_ not in FORMATS_MM:
            raise ValidationError(f"Format de page inconnu : {format_!r}")
        largeur, hauteur = FORMATS_MM[format_]
        if orientation == "paysage":
            largeur, hauteur = hauteur, largeur
        return Geometrie(
            format=format_,
            orientation=orientation,
            largeur_mm=largeur,
            hauteur_mm=hauteur,
            marges=marges,
        )

    def couverture(self) -> Couverture | None:
        """Couverture à rendre, ou ``None`` si le style/le document la désactive."""
        style = self.style_effectif()
        if not style.couverture.active:
            return None
        if self.mise_en_page.couverture is None:
            return Couverture(
                titre=self.metadata.titre,
                sous_titre=self.metadata.sous_titre,
                organisation=self.metadata.organisation,
            )
        couverture = self.mise_en_page.couverture
        return couverture if couverture.active else None

    # --- Lectures pour la validation et les rapports -------------------------

    def placeholders(self) -> list[str]:
        """Occurrences de ``(à compléter)``/``(à confirmer)`` (données non fournies)."""
        trouvees: list[str] = []
        for bloc in self.blocs:
            for texte in _textes_du_bloc(bloc):
                trouvees.extend(MARQUEUR_RE.findall(texte))
        return trouvees

    def blocs_a_confirmer(self) -> list[int]:
        """Index des blocs dont le statut n'est pas ``renseigne``."""
        return [
            index for index, bloc in enumerate(self.blocs) if bloc.statut != "renseigne"
        ]

    def texte_brut(self, *, separateur: str = "\n") -> str:
        """Texte plat du document (recherche, comptage, empreinte)."""
        morceaux: list[str] = []
        for bloc in self.blocs:
            morceaux.extend(_textes_du_bloc(bloc))
        return separateur.join(morceau for morceau in morceaux if morceau.strip())

    def nb_mots(self) -> int:
        """Nombre de mots du document (mesure de complétude, Phase 16)."""
        return len([mot for mot in self.texte_brut().split() if mot.strip()])

    def resume(self) -> dict[str, Any]:
        """Description compacte du document (rapports, logs, sortie de tool)."""
        comptes: dict[str, int] = {}
        for bloc in self.blocs:
            comptes[bloc.type] = comptes.get(bloc.type, 0) + 1
        geometrie = self.geometrie()
        return {
            "titre": self.metadata.titre,
            "reference": self.metadata.reference,
            "profil": self.profil,
            "style": self.style_effectif().nom,
            "format": geometrie.format,
            "orientation": geometrie.orientation,
            "nb_blocs": len(self.blocs),
            "blocs_par_type": comptes,
            "nb_mots": self.nb_mots(),
            "couverture": self.couverture() is not None,
            "sources": len(self.sources),
            "placeholders": len(self.placeholders()),
            "blocs_a_confirmer": self.blocs_a_confirmer(),
        }

    def avec_blocs(self, blocs: list[Bloc]) -> DocumentSpec:
        """Copie du spec avec une nouvelle liste de blocs (jamais de mutation)."""
        return self.model_copy(update={"blocs": blocs})


def _textes_du_bloc(bloc: Bloc) -> list[str]:
    """Tous les textes visibles d'un bloc (pour recherche et comptage)."""
    if isinstance(bloc, (Titre, Citation)):
        return [bloc.texte]
    if isinstance(bloc, Paragraphe):
        # Les fragments d'un même paragraphe forment **un** texte : les séparer
        # ferait croire à deux paragraphes dans ``texte_brut()``.
        if bloc.runs:
            return ["".join(segment.texte for segment in bloc.runs)]
        return [bloc.texte]
    if isinstance(bloc, Encadre):
        return [texte for texte in (bloc.titre, bloc.texte) if texte]
    if isinstance(bloc, Liste):
        return list(bloc.items)
    if isinstance(bloc, Tableau):
        textes = bloc.cellules_texte()
        if bloc.titre_tableau:
            textes.append(bloc.titre_tableau)
        return textes
    if isinstance(bloc, Image):
        return [texte for texte in (bloc.legende, bloc.alt) if texte]
    if isinstance(bloc, Graphique):
        return [texte for texte in (bloc.titre, bloc.legende) if texte]
    if isinstance(bloc, Illustration):
        textes = [texte for texte in (bloc.titre, bloc.legende) if texte]
        for item in bloc.items:
            textes.extend(texte for texte in (item.titre, item.texte) if texte)
        return textes
    if isinstance(bloc, Paires):
        return [f"{paire.libelle} : {paire.valeur}" for paire in bloc.paires]
    if isinstance(bloc, Sommaire):
        return [bloc.titre] if bloc.titre else []
    if isinstance(bloc, Forme):
        return [bloc.texte] if bloc.texte else []
    return []


def bloc_par_type(donnees: dict[str, Any]) -> Bloc:
    """Construit un bloc depuis un dictionnaire brut (entrée d'agent tolérante).

    Utile aux tools : le modèle peut envoyer ``{"type": "tableau", ...}`` sans
    que le tool ait à connaître les neuf classes.

    Raises:
        ValidationError: type de bloc inconnu.
    """
    type_ = str(donnees.get("type", "")).strip().lower()
    classe = _BLOCS_PAR_TYPE.get(type_)
    if classe is None:
        raise ValidationError(
            f"Type de bloc inconnu : {donnees.get('type')!r}",
            details={"types_disponibles": sorted(_BLOCS_PAR_TYPE)},
        )
    return classe.model_validate(donnees)  # type: ignore[return-value]
