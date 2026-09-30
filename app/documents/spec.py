"""Représentation documentaire structurée — ``DocumentSpec`` (ADR 0005, Phase 2).

Le LLM ne produit **jamais** de XML DOCX ni de séquence de formatage : il produit
une structure (titres, sections, tableaux, images) et choisit un style. Le code
décide comment cette structure devient un DOCX, un PDF ou du HTML.

```text
LLM → DocumentSpec → Renderer → DOCX / PDF / HTML
```

Propriétés de ce module :

- **strict** : tout modèle interdit les champs inconnus (``extra="forbid"``) —
  une donnée non prévue est une erreur, pas un champ ignoré ;
- **provenance** : chaque bloc peut désigner ses sources (``Source``) et son
  statut de remplissage (``renseigne``/``inconnu``/``manquant``/``a_confirmer``),
  ce qui rend vérifiable la règle « aucun fait inventé » (Phase 16) ;
- **sans dépendance tierce** : uniquement Pydantic — le spec est constructible,
  sérialisable et testable sans python-docx ni fpdf2 ;
- **formatable et validable** : ``placeholders()``, ``texte_brut()`` et
  ``resume()`` alimentent la couche de validation sans re-parser un fichier.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import date
from typing import Annotated, Any, Literal, NamedTuple
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.errors import ValidationError
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
    "Geometrie",
    "Image",
    "Liste",
    "Metadonnees",
    "MiseEnPage",
    "Paire",
    "Paires",
    "Paragraphe",
    "SautDePage",
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


class Paragraphe(_BlocBase):
    """Paragraphe de corps, de chapeau (accroche) ou de note."""

    type: Literal["paragraphe"] = "paragraphe"
    texte: str = Field(min_length=1, max_length=20000)
    role: Literal["corps", "chapeau", "note"] = "corps"


class Liste(_BlocBase):
    """Liste à puces ou numérotée, éventuellement imbriquée."""

    type: Literal["liste"] = "liste"
    items: list[str] = Field(min_length=1, max_length=200)
    numerotee: bool = False
    profondeur: int = Field(default=0, ge=0, le=3)


class Cellule(BaseModel):
    """Cellule de tableau : contenu + présentation **propre à la cellule**.

    Une cellule ordinaire s'écrit comme une simple chaîne (``"Mois 1"``). Cette
    forme explicite n'est utile que pour fusionner, tramer ou mettre en gras.

    Règles de fusion (comme HTML ``colspan``/``rowspan``) :

    - ``fusion_colonnes`` occupe plusieurs colonnes de la **même** ligne ;
    - ``fusion_lignes`` occupe plusieurs lignes : les lignes suivantes
      n'écrivent alors **rien** pour ces colonnes (elles n'ont pas de cellule) ;
    - ``"fin"`` étend la cellule jusqu'à la dernière colonne — pas besoin de
      compter les colonnes soi-même (source d'erreur fréquente).
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
    """Tableau : en-tête(s) optionnel(s), lignes, fusions, largeurs par colonne.

    Deux façons d'écrire l'en-tête, jamais les deux ensemble :

    - ``entete`` : une ligne d'en-tête simple (``["Période", "Activité"]``) ;
    - ``entetes`` : plusieurs lignes d'en-tête (en-tête à deux niveaux, avec
      fusions) ; les deux dernières colonnes d'un planning en sont l'exemple.

    Sans ``entete`` **ni** ``entetes``, aucune ligne d'en-tête n'est produite
    (jamais de bandeau vide) : la première ligne de ``lignes`` reste une donnée.
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
            cellule.texte
            for ligne in self.lignes_toutes()
            for cellule in ligne
        ]


def _normaliser_cellules(ligne: Sequence[CelluleRef]) -> list[Cellule]:
    """Convertit une ligne brute en cellules (une chaîne devient un texte)."""
    return [
        cellule if isinstance(cellule, Cellule) else Cellule(texte=cellule)
        for cellule in ligne
    ]


class Image(_BlocBase):
    """Image référencée : par document enregistré **ou** par chemin logique confiné.

    Aucun chemin disque absolu n'est accepté dans un spec : la résolution passe
    par le stockage (``app.documents.assets``), donc pas de traversée possible.
    """

    type: Literal["image"] = "image"
    document_id: UUID | None = None
    chemin: str | None = Field(default=None, max_length=400)
    largeur_mm: float = Field(default=120.0, gt=0, le=500)
    legende: str | None = Field(default=None, max_length=300)
    alt: str | None = Field(default=None, max_length=300)

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


class SautDePage(_BlocBase):
    """Saut de page explicite."""

    type: Literal["saut_de_page"] = "saut_de_page"


#: Union discriminée : le champ ``type`` sélectionne le modèle concret.
Bloc = Annotated[
    Titre | Paragraphe | Liste | Tableau | Image | Encadre | Citation | Paires | SautDePage,
    Field(discriminator="type"),
]

_BLOCS_PAR_TYPE: dict[str, type[BaseModel]] = {
    "titre": Titre,
    "paragraphe": Paragraphe,
    "liste": Liste,
    "tableau": Tableau,
    "image": Image,
    "encadre": Encadre,
    "citation": Citation,
    "paires": Paires,
    "saut_de_page": SautDePage,
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
    """Surcharges de mise en page **propres au document** (le style donne le défaut).

    ``format``/``orientation``/``marges`` valent ``None`` par défaut : le style
    décide. Renseigner un champ ici l'emporte explicitement — jamais de valeur
    implicite cachée.
    """

    model_config = ConfigDict(extra="forbid")

    format: Literal["A4", "Letter"] | None = None
    orientation: Literal["portrait", "paysage"] | None = None
    marges: Marges | None = None
    couverture: Couverture | None = None
    entete_texte: str | None = Field(default=None, max_length=200)
    pied_texte: str | None = Field(default=None, max_length=200)
    numero_de_page: bool | None = None
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
    if isinstance(bloc, (Titre, Paragraphe, Citation)):
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
    if isinstance(bloc, Paires):
        return [f"{paire.libelle} : {paire.valeur}" for paire in bloc.paires]
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
