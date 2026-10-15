"""ADR 0005 — représentation structurée ``DocumentSpec`` (Phases 2 et 5).

Le spec est le contrat entre le LLM et les renderers : il doit être **strict**
(champ inconnu refusé), **sûr** (aucun chemin disque hors stockage) et
**auto-descriptif** (marqueurs, sources, résumé) pour alimenter la validation.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import ValidationError as PydanticValidationError

from app.core.errors import ValidationError
from app.documents.spec import (
    MARQUEUR_A_COMPLETER,
    Couverture,
    DocumentSpec,
    Encadre,
    Image,
    Liste,
    Metadonnees,
    MiseEnPage,
    Paire,
    Paires,
    Paragraphe,
    Tableau,
    Titre,
    bloc_par_type,
)
from app.documents.styles import (
    CARSO_DEFAUT,
    CARSO_INSTITUTIONNEL,
    CARSO_SOBRE,
    STYLES_DISPONIBLES,
    Marges,
    StyleTexte,
    couleur_rgb,
    mm_vers_emu,
    style_par_nom,
)


def _spec(**kwargs: object) -> DocumentSpec:
    """Spec minimal : titre + un paragraphe."""
    return DocumentSpec(
        metadata=Metadonnees(titre="Offre technique"),
        blocs=[Paragraphe(texte="Contenu")],
        **kwargs,
    )


class TestSpec:
    def test_bloc_inconnu_refuse(self) -> None:
        """Un champ non prévu est une erreur, jamais un champ ignoré."""
        with pytest.raises(PydanticValidationError):
            Titre(niveau=1, texte="T", inconnu=True)  # type: ignore[call-arg]

    def test_bloc_par_type(self) -> None:
        bloc = bloc_par_type({"type": "tableau", "entete": ["A"], "lignes": [["1"]]})
        assert isinstance(bloc, Tableau)
        with pytest.raises(ValidationError, match="inconnu"):
            bloc_par_type({"type": "graphique"})

    def test_niveau_titre_borne(self) -> None:
        with pytest.raises(PydanticValidationError):
            Titre(niveau=6, texte="Trop profond")

    def test_chemin_image_relatif_obligatoire(self) -> None:
        """Aucun chemin absolu ni traversée : le rendu ne lit que le stockage."""
        with pytest.raises(PydanticValidationError):
            Image(chemin="../../etc/passwd", largeur_mm=10)
        with pytest.raises(PydanticValidationError):
            Image(chemin="C:/Windows/system.ini", largeur_mm=10)
        assert Image(chemin="missions/1/logo.png", largeur_mm=10).chemin == "missions/1/logo.png"

    def test_tableau_nb_colonnes_et_lignes_vides(self) -> None:
        with pytest.raises(PydanticValidationError):
            Tableau(entete=["A", "B"], lignes=[[]])
        assert Tableau(entete=["A", "B"], lignes=[["1", "2"]]).nb_colonnes() == 2
        assert Tableau(lignes=[["1", "2", "3"]]).nb_colonnes() == 3

    def test_placeholders_et_statuts(self) -> None:
        spec = DocumentSpec(
            metadata=Metadonnees(titre="Offre"),
            blocs=[
                Paragraphe(texte="Budget : (à compléter)"),
                Paragraphe(texte="Durée : 5 jours", statut="a_confirmer"),
                Liste(items=["Lieu", "Effectif"], statut="manquant"),
            ],
        )
        assert len(spec.placeholders()) == 1
        assert spec.blocs_a_confirmer() == [1, 2]

    def test_resume(self) -> None:
        resume = _spec().resume()
        assert resume["titre"] == "Offre technique"
        assert resume["nb_blocs"] == 1
        assert resume["blocs_par_type"] == {"paragraphe": 1}
        assert resume["couverture"] is True
        assert resume["placeholders"] == 0

    def test_texte_brut_et_nb_mots(self) -> None:
        spec = DocumentSpec(
            metadata=Metadonnees(titre="Offre"),
            blocs=[
                Titre(niveau=1, texte="Contexte"),
                Paires(paires=[Paire(libelle="Lieu", valeur="Rabat")]),
                Tableau(entete=["Poste"], lignes=[["Formateur"]]),
            ],
        )
        texte = spec.texte_brut()
        assert "Contexte" in texte and "Rabat" in texte and "Formateur" in texte
        assert spec.nb_mots() >= 5

    def test_avec_blocs_sans_mutation(self) -> None:
        spec = _spec()
        nouveau = spec.avec_blocs([Paragraphe(texte="Autre")])
        assert len(spec.blocs) == 1 and len(nouveau.blocs) == 1
        assert spec.blocs[0].texte != nouveau.blocs[0].texte


class TestGeometrie:
    def test_defaut_style_a4_portrait(self) -> None:
        geometrie = _spec(style=CARSO_DEFAUT).geometrie()
        assert (geometrie.format, geometrie.orientation) == ("A4", "portrait")
        assert geometrie.largeur_mm == 210.0 and geometrie.hauteur_mm == 297.0
        assert geometrie.largeur_utile_mm() == pytest.approx(172.0)

    def test_surcharge_document_prioritaire(self) -> None:
        """Le document peut l'emporter sur le style (paysage, marges, format)."""
        spec = _spec(
            mise_en_page=MiseEnPage(
                format="Letter", orientation="paysage", marges=Marges(haut_mm=10, bas_mm=10)
            )
        )
        geometrie = spec.geometrie()
        assert geometrie.orientation == "paysage"
        assert geometrie.largeur_mm == pytest.approx(279.4)
        assert geometrie.hauteur_mm == pytest.approx(215.9)
        assert geometrie.marges.haut_mm == 10

    def test_format_inconnu_refuse(self) -> None:
        with pytest.raises(PydanticValidationError):
            MiseEnPage(format="A3")  # type: ignore[arg-type]

    def test_couverture_desactivable(self) -> None:
        assert _spec().couverture() is not None
        sans = _spec(mise_en_page=MiseEnPage(couverture=Couverture(active=False)))
        assert sans.couverture() is None


class TestStyles:
    def test_styles_disponibles_et_inconnu_refuse(self) -> None:
        assert set(STYLES_DISPONIBLES) == {
            "carso_defaut",
            "carso_sobre",
            "carso_institutionnel",
        }
        assert style_par_nom(None).nom == "carso_defaut"
        assert style_par_nom("CARSO_SOBRE").nom == "carso_sobre"
        with pytest.raises(ValidationError, match="inconnu"):
            style_par_nom("style_maison")

    def test_titre_niveau_repli_deterministe(self) -> None:
        """Un document qui saute un niveau reste rendable (la validation le signale)."""
        assert CARSO_DEFAUT.titre_niveau(2).taille_pt == 12.5
        assert CARSO_DEFAUT.titre_niveau(3).taille_pt == 11.5
        sans_trois = CARSO_DEFAUT.model_copy(update={"titres": {1: CARSO_DEFAUT.titres[1]}})
        assert sans_trois.titre_niveau(2).taille_pt == CARSO_DEFAUT.titres[1].taille_pt

    def test_encadre_repli(self) -> None:
        assert CARSO_DEFAUT.encadre("avertissement").libelle == "Attention"

    def test_css_contient_les_tokens(self) -> None:
        css = CARSO_INSTITUTIONNEL.to_css()
        assert "@page" in css and "size: 210.0mm 297.0mm" in css
        assert CARSO_INSTITUTIONNEL.couverture.couleur_accent in css
        assert "font-family: 'Calibri'" in css

    def test_couleurs_et_unites(self) -> None:
        assert couleur_rgb("#1F4E79") == (31, 78, 121)
        with pytest.raises(ValidationError):
            couleur_rgb("bleu")
        assert mm_vers_emu(10) == 360000
        assert CARSO_SOBRE.tableau.bordures is True

    def test_style_texte_borne(self) -> None:
        with pytest.raises(PydanticValidationError):
            StyleTexte(taille_pt=200)
        with pytest.raises(PydanticValidationError):
            StyleTexte(couleur="rouge")  # type: ignore[arg-type]

    def test_document_id_image_accepte(self) -> None:
        """Une image peut être désignée par document enregistré (jamais un chemin libre)."""
        identifiant = uuid4()
        bloc = Image(document_id=identifiant, largeur_mm=80)
        assert bloc.document_id == identifiant
        assert bloc.chemin is None
        assert MARQUEUR_A_COMPLETER in Encadre(texte=MARQUEUR_A_COMPLETER).texte
