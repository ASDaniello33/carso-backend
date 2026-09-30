"""Design system documentaire — tokens de style réutilisables (ADR 0005, Phase 5).

Un document CARSO n'est jamais formaté « en dur » dans le code de rendu : tous
les choix visuels (polices, tailles, couleurs, marges, bordures, espacements)
vivent ici, dans des modèles Pydantic **validés**. Les renderers (DOCX, PDF,
HTML) ne font que consommer ces tokens — une seule source de vérité visuelle,
donc une identité cohérente d'un format à l'autre.

Trois styles fournis :

- ``CARSO_DEFAUT`` : sobre et professionnel, adapté aux offres et rapports ;
- ``CARSO_SOBRE`` : dense, noir et blanc — documents de travail internes ;
- ``CARSO_INSTITUTIONNEL`` : accent bleu profond et couverture marquée.

Un ``DocumentStyle`` peut aussi être **extrait d'un document de référence**
(``app.documents.reference``), puis réutilisé tel quel : c'est ce qui permet à
l'agent de dire « utilise le style de ce document » **sans copier son contenu**.

Aucune librairie tierce n'est importée ici : les couleurs sont exposées en
hexadécimal et en triplet RGB, chaque renderer choisit sa représentation.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.core.errors import ValidationError

__all__ = [
    "Alignement",
    "DocumentStyle",
    "FORMATS_MM",
    "Marges",
    "STYLES_DISPONIBLES",
    "StyleCouverture",
    "StyleEncadre",
    "StyleEntetePied",
    "StylePage",
    "StyleTableau",
    "StyleTexte",
    "couleur_rgb",
    "mm_vers_emu",
    "mm_vers_pt",
    "style_par_nom",
]

#: Un alignement, dans le vocabulaire du domaine (jamais « LEFT »/« center »).
Alignement = Literal["gauche", "centre", "droite", "justifie"]

#: Formats de page supportés : (largeur mm, hauteur mm) en **portrait**.
FORMATS_MM: dict[str, tuple[float, float]] = {
    "A4": (210.0, 297.0),
    "Letter": (215.9, 279.4),
}

_HEX = r"^#[0-9A-Fa-f]{6}$"

#: Conversions d'unités partagées par les renderers.
MM_PAR_POUCE = 25.4
EMU_PAR_MM = 36000.0
POINTS_PAR_POUCE = 72.0


def mm_vers_emu(millimetres: float) -> int:
    """Millimètres → EMU (unité interne DOCX/OOXML)."""
    return int(round(millimetres * EMU_PAR_MM))


def mm_vers_pt(millimetres: float) -> float:
    """Millimètres → points typographiques (unité PDF)."""
    return millimetres * POINTS_PAR_POUCE / MM_PAR_POUCE


def couleur_rgb(couleur: str) -> tuple[int, int, int]:
    """``#1F4E79`` → ``(31, 78, 121)`` (python-docx et fpdf2 partagent ce besoin)."""
    valeur = couleur.lstrip("#")
    if len(valeur) != 6:
        raise ValidationError(f"Couleur invalide : {couleur!r}")
    return (int(valeur[0:2], 16), int(valeur[2:4], 16), int(valeur[4:6], 16))


class StyleTexte(BaseModel):
    """Un rôle typographique : police, taille, graisse, couleur, espacements."""

    model_config = {"extra": "forbid"}

    police: str = Field(default="Calibri", min_length=1, max_length=80)
    taille_pt: float = Field(default=11.0, gt=4, le=72)
    gras: bool = False
    italique: bool = False
    couleur: str = Field(default="#1F2937", pattern=_HEX)
    alignement: Alignement = "gauche"
    espace_avant_pt: float = Field(default=0.0, ge=0, le=120)
    espace_apres_pt: float = Field(default=6.0, ge=0, le=120)
    interligne: float = Field(default=1.15, ge=0.8, le=3.0)


class Marges(BaseModel):
    """Marges de page en millimètres."""

    model_config = {"extra": "forbid"}

    haut_mm: float = Field(default=20.0, ge=5, le=80)
    bas_mm: float = Field(default=18.0, ge=5, le=80)
    gauche_mm: float = Field(default=20.0, ge=5, le=80)
    droite_mm: float = Field(default=18.0, ge=5, le=80)


class StylePage(BaseModel):
    """Géométrie **par défaut** du style (peut être surchargée par document)."""

    model_config = {"extra": "forbid"}

    format: Literal["A4", "Letter"] = "A4"
    orientation: Literal["portrait", "paysage"] = "portrait"
    marges: Marges = Field(default_factory=Marges)

    @property
    def format_invalide(self) -> bool:  # pragma: no cover - garde interne
        return self.format not in FORMATS_MM


class StyleTableau(BaseModel):
    """Apparence des tableaux : filets, en-tête, bandes alternées."""

    model_config = {"extra": "forbid"}

    bordures: bool = True
    bordure_couleur: str = Field(default="#B4C6E7", pattern=_HEX)
    bordure_taille_pt: float = Field(default=0.75, gt=0, le=6)
    entete_fond: str = Field(default="#1F4E79", pattern=_HEX)
    entete_couleur: str = Field(default="#FFFFFF", pattern=_HEX)
    entete_gras: bool = True
    bandes_alternees: bool = True
    bande_fond: str = Field(default="#F2F6FC", pattern=_HEX)
    taille_pt: float = Field(default=9.5, gt=4, le=24)
    repeter_entete: bool = True
    marge_interne_pt: float = Field(default=3.0, ge=0, le=20)


class StyleEntetePied(BaseModel):
    """En-tête ou pied de page (texte fixe + numéro de page optionnel)."""

    model_config = {"extra": "forbid"}

    texte: str | None = Field(default=None, max_length=200)
    alignement: Alignement = "gauche"
    taille_pt: float = Field(default=8.5, gt=4, le=24)
    couleur: str = Field(default="#6B7280", pattern=_HEX)
    italique: bool = False
    numero_page: bool = False
    filet: bool = False

    def comme_style_texte(self) -> StyleTexte:
        """Vue ``StyleTexte`` d'une zone (les renderers n'ont qu'un seul chemin)."""
        return StyleTexte(
            taille_pt=self.taille_pt,
            couleur=self.couleur,
            italique=self.italique,
            alignement=self.alignement,
            espace_apres_pt=0.0,
        )


class StyleCouverture(BaseModel):
    """Page de couverture (première page du livrable)."""

    model_config = {"extra": "forbid"}

    active: bool = True
    alignement: Alignement = "centre"
    titre_taille_pt: float = Field(default=26.0, gt=8, le=72)
    sous_titre_taille_pt: float = Field(default=14.0, gt=6, le=48)
    couleur_accent: str = Field(default="#1F4E79", pattern=_HEX)
    espace_haut_mm: float = Field(default=45.0, ge=0, le=200)
    bandeau: bool = True
    logo_largeur_mm: float = Field(default=40.0, gt=0, le=200)


class StyleEncadre(BaseModel):
    """Encadré (information, avertissement, note, succès)."""

    model_config = {"extra": "forbid"}

    fond: str = Field(default="#EFF6FF", pattern=_HEX)
    bordure: str = Field(default="#1F4E79", pattern=_HEX)
    couleur_texte: str = Field(default="#1F2937", pattern=_HEX)
    couleur_titre: str = Field(default="#1F4E79", pattern=_HEX)
    libelle: str | None = Field(default=None, max_length=40)


#: Encadrés standard : un genre, un libellé affiché, des couleurs.
_ENCADRES_DEFAUT: dict[str, StyleEncadre] = {
    "info": StyleEncadre(libelle="Information"),
    "note": StyleEncadre(
        fond="#F9FAFB",
        bordure="#9CA3AF",
        couleur_titre="#4B5563",
        libelle="Note",
    ),
    "avertissement": StyleEncadre(
        fond="#FEF3C7",
        bordure="#B45309",
        couleur_titre="#92400E",
        libelle="Attention",
    ),
    "succes": StyleEncadre(
        fond="#ECFDF5",
        bordure="#047857",
        couleur_titre="#065F46",
        libelle="Validé",
    ),
}


class DocumentStyle(BaseModel):
    """Design system complet d'un document : le seul endroit où le visuel est décidé."""

    model_config = {"extra": "forbid"}

    nom: str = Field(default="carso_defaut", min_length=1, max_length=60)
    page: StylePage = Field(default_factory=StylePage)
    titre: StyleTexte = Field(
        default_factory=lambda: StyleTexte(
            taille_pt=20.0,
            gras=True,
            couleur="#1F4E79",
            espace_avant_pt=0.0,
            espace_apres_pt=12.0,
        )
    )
    titres: dict[int, StyleTexte] = Field(default_factory=dict)
    corps: StyleTexte = Field(default_factory=StyleTexte)
    liste: StyleTexte = Field(default_factory=StyleTexte)
    tableau: StyleTableau = Field(default_factory=StyleTableau)
    citation: StyleTexte = Field(
        default_factory=lambda: StyleTexte(
            italique=True,
            couleur="#4B5563",
            espace_avant_pt=6.0,
            espace_apres_pt=6.0,
        )
    )
    encadres: dict[str, StyleEncadre] = Field(default_factory=dict)
    entete: StyleEntetePied = Field(default_factory=StyleEntetePied)
    pied: StyleEntetePied = Field(default_factory=StyleEntetePied)
    couverture: StyleCouverture = Field(default_factory=StyleCouverture)

    @field_validator("titres")
    @classmethod
    def _verifier_niveaux(cls, valeur: dict[int, StyleTexte]) -> dict[int, StyleTexte]:
        """Seuls les niveaux 1 à 4 existent (au-delà, le document est à revoir)."""
        for niveau in valeur:
            if niveau not in (1, 2, 3, 4):
                raise ValueError(f"Niveau de titre inconnu : {niveau} (attendu 1 à 4)")
        return valeur

    def titre_niveau(self, niveau: int) -> StyleTexte:
        """Style du titre ``niveau``, avec repli déterministe sur les niveaux voisins.

        Un document qui saute un niveau (H1 → H3) doit rester rendable : le
        renderer ne plante pas, la validation le signale (Phase 10).
        """
        if niveau in self.titres:
            return self.titres[niveau]
        superieurs = [n for n in self.titres if n < niveau]
        if superieurs:
            return self.titres[max(superieurs)]
        return self.titre

    def encadre(self, genre: str) -> StyleEncadre:
        """Style d'encadré par genre, avec repli sur ``info``."""
        return (
            self.encadres.get(genre)
            or self.encadres.get("info")
            or _ENCADRES_DEFAUT["info"]
        )

    def to_css(
        self,
        *,
        largeur_mm: float | None = None,
        hauteur_mm: float | None = None,
        marges: Marges | None = None,
    ) -> str:
        """CSS du document, dérivé des mêmes tokens (renderer HTML, aperçu).

        Les paramètres optionnels permettent au renderer de passer la **géométrie
        résolue** du document (surcharges de mise en page comprises) : l'aperçu
        HTML respecte donc exactement le format, l'orientation et les marges du
        document, et non les seuls défauts du style.
        """
        largeur, hauteur = FORMATS_MM[self.page.format]
        if self.page.orientation == "paysage":
            largeur, hauteur = hauteur, largeur
        largeur = largeur if largeur_mm is None else largeur_mm
        hauteur = hauteur if hauteur_mm is None else hauteur_mm
        marges = marges or self.page.marges
        lignes: list[str] = [
            "@page {",
            f"  size: {largeur}mm {hauteur}mm;",
            (
                f"  margin: {marges.haut_mm}mm {marges.droite_mm}mm "
                f"{marges.bas_mm}mm {marges.gauche_mm}mm;"
            ),
            "}",
            "",
            "body {",
            f"  font-family: '{self.corps.police}', 'Segoe UI', Arial, sans-serif;",
            f"  font-size: {self.corps.taille_pt}pt;",
            f"  color: {self.corps.couleur};",
            f"  line-height: {self.corps.interligne};",
            "  margin: 0;",
            "}",
            "",
            *self._css_texte("h1", self.titre),
            *self._css_texte("h2", self.titre_niveau(1)),
            *self._css_texte("h3", self.titre_niveau(2)),
            *self._css_texte("h4", self.titre_niveau(3)),
            *self._css_texte("p", self.corps),
            *self._css_texte("li", self.liste),
            *self._css_texte("blockquote", self.citation),
            "",
            "table {",
            "  border-collapse: collapse;",
            "  width: 100%;",
            f"  font-size: {self.tableau.taille_pt}pt;",
            "}",
            "th, td {",
            (
                f"  border: {self.tableau.bordure_taille_pt}pt solid "
                f"{self.tableau.bordure_couleur};"
                if self.tableau.bordures
                else "  border: none;"
            ),
            f"  padding: {self.tableau.marge_interne_pt}pt;",
            "  text-align: left;",
            "}",
            "th {",
            f"  background: {self.tableau.entete_fond};",
            f"  color: {self.tableau.entete_couleur};",
            f"  font-weight: {'bold' if self.tableau.entete_gras else 'normal'};",
            "}",
            (
                f"tbody tr:nth-child(even) {{ background: {self.tableau.bande_fond}; }}"
                if self.tableau.bandes_alternees
                else ""
            ),
            "",
            ".encadre {",
            "  border-left: 3pt solid;",
            "  padding: 6pt 9pt;",
            "  margin: 8pt 0;",
            "}",
            ".couverture { text-align: center; padding-top: 45mm; }",
            ".couverture .titre {",
            f"  font-size: {self.couverture.titre_taille_pt}pt;",
            f"  color: {self.couverture.couleur_accent};",
            "  font-weight: bold;",
            "}",
            ".saut-de-page { page-break-after: always; }",
        ]
        return "\n".join(ligne for ligne in lignes if ligne is not None)

    @staticmethod
    def _css_texte(selecteur: str, style: StyleTexte) -> list[str]:
        """Règles CSS d'un rôle typographique."""
        alignements = {
            "gauche": "left",
            "centre": "center",
            "droite": "right",
            "justifie": "justify",
        }
        return [
            f"{selecteur} {{",
            f"  font-size: {style.taille_pt}pt;",
            f"  color: {style.couleur};",
            f"  font-weight: {'bold' if style.gras else 'normal'};",
            f"  font-style: {'italic' if style.italique else 'normal'};",
            f"  text-align: {alignements[style.alignement]};",
            f"  margin: {style.espace_avant_pt}pt 0 {style.espace_apres_pt}pt 0;",
            "}",
            "",
        ]


#: Style par défaut des livrables CARSO (offres, rapports, fiches).
CARSO_DEFAUT = DocumentStyle(
    nom="carso_defaut",
    titres={
        1: StyleTexte(
            taille_pt=15.0,
            gras=True,
            couleur="#1F4E79",
            espace_avant_pt=14.0,
            espace_apres_pt=6.0,
        ),
        2: StyleTexte(
            taille_pt=12.5,
            gras=True,
            couleur="#2E5E8C",
            espace_avant_pt=10.0,
            espace_apres_pt=4.0,
        ),
        3: StyleTexte(
            taille_pt=11.5,
            gras=True,
            couleur="#374151",
            espace_avant_pt=8.0,
            espace_apres_pt=3.0,
        ),
        4: StyleTexte(
            taille_pt=11.0,
            gras=False,
            italique=True,
            couleur="#374151",
            espace_avant_pt=6.0,
            espace_apres_pt=3.0,
        ),
    },
    corps=StyleTexte(taille_pt=11.0, espace_apres_pt=6.0),
    liste=StyleTexte(taille_pt=11.0, espace_apres_pt=3.0),
    encadres=dict(_ENCADRES_DEFAUT),
    entete=StyleEntetePied(texte="CARSO — Document proposé", taille_pt=8.0),
    pied=StyleEntetePied(alignement="centre", numero_page=True, filet=True),
)

#: Style dense noir et blanc (documents de travail).
CARSO_SOBRE = DocumentStyle(
    nom="carso_sobre",
    titre=StyleTexte(
        taille_pt=16.0, gras=True, couleur="#111111", espace_apres_pt=10.0
    ),
    titres={
        1: StyleTexte(taille_pt=13.5, gras=True, couleur="#111111", espace_avant_pt=12.0),
        2: StyleTexte(taille_pt=12.0, gras=True, couleur="#111111", espace_avant_pt=9.0),
        3: StyleTexte(taille_pt=11.0, gras=True, couleur="#222222", espace_avant_pt=7.0),
        4: StyleTexte(taille_pt=11.0, italique=True, couleur="#222222", espace_avant_pt=6.0),
    },
    corps=StyleTexte(taille_pt=10.5, couleur="#111111", espace_apres_pt=5.0),
    liste=StyleTexte(taille_pt=10.5, couleur="#111111", espace_apres_pt=2.0),
    tableau=StyleTableau(
        bordure_couleur="#111111",
        entete_fond="#E5E7EB",
        entete_couleur="#111111",
        bandes_alternees=False,
        taille_pt=9.0,
    ),
    encadres={
        genre: StyleEncadre(
            fond="#FFFFFF",
            bordure="#111111",
            couleur_titre="#111111",
            libelle=style.libelle,
        )
        for genre, style in _ENCADRES_DEFAUT.items()
    },
    entete=StyleEntetePied(taille_pt=8.0, couleur="#444444"),
    pied=StyleEntetePied(alignement="centre", numero_page=True, filet=True),
    couverture=StyleCouverture(
        titre_taille_pt=22.0,
        couleur_accent="#111111",
        espace_haut_mm=40.0,
    ),
)

#: Style institutionnel : accent profond, bandeau de couverture.
CARSO_INSTITUTIONNEL = CARSO_DEFAUT.model_copy(
    update={
        "nom": "carso_institutionnel",
        "couverture": StyleCouverture(
            titre_taille_pt=30.0,
            sous_titre_taille_pt=15.0,
            couleur_accent="#0B2B4A",
            espace_haut_mm=55.0,
            bandeau=True,
        ),
        "titre": StyleTexte(
            taille_pt=22.0,
            gras=True,
            couleur="#0B2B4A",
            espace_apres_pt=14.0,
        ),
    }
)

#: Catalogue des styles nommés (choix explicite de l'agent ou de l'utilisateur).
STYLES_DISPONIBLES: dict[str, DocumentStyle] = {
    CARSO_DEFAUT.nom: CARSO_DEFAUT,
    CARSO_SOBRE.nom: CARSO_SOBRE,
    CARSO_INSTITUTIONNEL.nom: CARSO_INSTITUTIONNEL,
}


def style_par_nom(nom: str | None) -> DocumentStyle:
    """Résout un style nommé ; ``None`` → ``CARSO_DEFAUT``.

    Raises:
        ValidationError: nom inconnu (jamais de repli silencieux sur un autre
            style : l'agent doit savoir que son choix n'existe pas).
    """
    if nom is None or not str(nom).strip():
        return CARSO_DEFAUT
    cle = str(nom).strip().lower()
    style = STYLES_DISPONIBLES.get(cle)
    if style is None:
        raise ValidationError(
            f"Style documentaire inconnu : {nom!r}",
            details={"styles_disponibles": sorted(STYLES_DISPONIBLES)},
        )
    return style
