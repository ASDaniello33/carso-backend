"""INC-130 — le document complexe : spec riche rendu réellement (DOCX, PDF, HTML).

Chaque test lit le fichier produit (XML OOXML, balisage HTML) : une option de mise
en forme n'est acquise que si elle est **dans** le document, pas seulement dans le
modèle Pydantic.
"""

from __future__ import annotations

import io
import re
import zipfile

import pytest
from pydantic import ValidationError as PydanticValidationError

from app.documents.renderers import rendre
from app.documents.spec import (
    Cellule,
    DocumentSpec,
    Espacement,
    Forme,
    Metadonnees,
    MiseEnPage,
    Paragraphe,
    Segment,
    Sommaire,
    Tableau,
    Titre,
    bloc_par_type,
)


def _partie(octets: bytes, nom: str = "word/document.xml") -> str:
    with zipfile.ZipFile(io.BytesIO(octets)) as archive:
        return archive.read(nom).decode("utf-8")


def _spec(blocs: list[object], **mise_en_page: object) -> DocumentSpec:
    return DocumentSpec(
        metadata=Metadonnees(titre="Document riche"),
        mise_en_page=MiseEnPage(**mise_en_page),
        blocs=blocs,  # type: ignore[arg-type]
    )


# --- Fragments de texte ------------------------------------------------------


def test_fragments_gras_italique_souligne_surligne_couleur_taille() -> None:
    spec = _spec(
        [
            Paragraphe(
                runs=[
                    Segment(texte="Important", gras=True),
                    Segment(texte=" nuance", italique=True),
                    Segment(texte=" souligne", souligne=True),
                    Segment(texte=" marque", surligne="jaune"),
                    Segment(texte=" rouge", couleur="#C00000"),
                    Segment(texte=" grand", taille_pt=16.0),
                ]
            )
        ]
    )
    xml = _partie(rendre(spec, format="docx"))
    assert "<w:b/>" in xml
    assert "<w:i/>" in xml
    assert 'w:u w:val="single"' in xml
    assert 'w:highlight w:val="yellow"' in xml
    assert 'w:color w:val="C00000"' in xml
    assert 'w:sz w:val="32"' in xml  # 16 pt = 32 demi-points
    for texte in ("Important", "nuance", "souligne", "marque", "rouge", "grand"):
        assert texte in xml


def test_fragment_barre_et_police_propre() -> None:
    xml = _partie(
        rendre(
            _spec([Paragraphe(runs=[Segment(texte="annule", barre=True, police="Georgia")])]),
            format="docx",
        )
    )
    assert "<w:strike/>" in xml or 'w:strike w:val="true"' in xml
    assert "Georgia" in xml


def test_surcharges_typographiques_du_paragraphe() -> None:
    xml = _partie(
        rendre(
            _spec(
                [
                    Paragraphe(
                        texte="Texte justifie",
                        alignement="justifie",
                        espace_avant_pt=24.0,
                        espace_apres_pt=12.0,
                        interligne=1.5,
                        retrait_mm=10.0,
                        retrait_premiere_ligne_mm=5.0,
                    )
                ]
            ),
            format="docx",
        )
    )
    assert 'w:jc w:val="both"' in xml
    assert 'w:before="480"' in xml
    assert 'w:after="240"' in xml
    assert 'w:line="360"' in xml
    assert re.search(r'w:ind[^>]*w:left="567"', xml)
    assert re.search(r'w:ind[^>]*w:firstLine="283"', xml)


def test_espacement_vertical_exact() -> None:
    xml = _partie(rendre(_spec([Espacement(hauteur_mm=10.0)]), format="docx"))
    assert 'w:lineRule="exact"' in xml
    assert 'w:line="567"' in xml  # 10 mm = 28,35 pt = 567 twips


# --- Tableaux ---------------------------------------------------------------


def test_tableau_padding_bordures_trame_entete_hauteur_alignment_vertical() -> None:
    bloc = Tableau(
        entete=["Periode", "Montant"],
        lignes=[
            [
                Cellule(texte="2026", alignement_vertical="centre"),
                Cellule(
                    runs=[Segment(texte="1 200", gras=True), Segment(texte=" EUR")],
                    fond="#EAF1F8",
                ),
            ]
        ],
        marges_cellules_mm=1.5,
        bordures="aucune",
        couleur_entete="#1F4E79",
        hauteur_lignes_mm=9.0,
    )
    xml = _partie(rendre(_spec([bloc]), format="docx"))
    assert "w:tcMar" in xml
    assert 'w:tcMar' in xml and 'w:w="85"' in xml  # 1,5 mm = 85 twips
    assert 'w:tblBorders' in xml and 'w:val="nil"' in xml
    assert 'w:shd' in xml and 'w:fill="1F4E79"' in xml
    assert 'w:trHeight' in xml and 'w:hRule="atLeast"' in xml
    assert 'w:vAlign w:val="center"' in xml
    assert 'w:fill="EAF1F8"' in xml
    assert "1 200" in xml


def test_tableau_filets_epais_et_couleur() -> None:
    bloc = Tableau(
        entete=["A"],
        lignes=[["1"]],
        bordures="epaisse",
        couleur_bordures="#C00000",
    )
    xml = _partie(rendre(_spec([bloc]), format="docx"))
    assert 'w:tblBorders' in xml
    assert 'w:color="C00000"' in xml
    assert 'w:sz="12"' in xml  # 1,5 pt = 12 huitiemes de point


# --- Sommaire et pagination -------------------------------------------------


def test_sommaire_est_un_champ_toc_recalcule() -> None:
    octets = rendre(
        _spec([Sommaire(), Titre(niveau=1, texte="Contexte"), Paragraphe(texte="Corps")]),
        format="docx",
    )
    corps = _partie(octets)
    assert "fldSimple" in corps
    assert "TOC" in corps
    assert 'w:instr=" TOC \\o &quot;1-3&quot;' in corps or "TOC \\o" in corps
    assert "updateFields" in _partie(octets, "word/settings.xml")


def test_sommaire_sans_numeros_de_page() -> None:
    xml = _partie(
        rendre(_spec([Sommaire(avec_numeros_de_page=False)]), format="docx")
    )
    assert "TOC" in xml
    assert "\\n" in xml


def test_debut_de_numerotation_de_page() -> None:
    xml = _partie(rendre(_spec([Paragraphe(texte="a")], numero_de_page_debut=3), format="docx"))
    assert 'w:pgNumType' in xml
    assert 'w:start="3"' in xml


# --- Formes -----------------------------------------------------------------


def test_forme_est_rendue_en_image_dans_le_docx() -> None:
    octets = rendre(
        _spec([Forme(variante="bandeau", texte="Partie 1", couleur="#1F4E79")]),
        format="docx",
    )
    with zipfile.ZipFile(io.BytesIO(octets)) as archive:
        medias = [nom for nom in archive.namelist() if nom.startswith("word/media/")]
    assert medias, "une forme doit produire une image dans le DOCX"


def test_forme_separateur_sans_texte_et_etiquette() -> None:
    rendre(_spec([Forme(variante="separateur")]), format="docx")
    octets = rendre(
        _spec([Forme(variante="etiquette", texte="Priorite haute", couleur="#C00000")]),
        format="html",
    )
    assert b"Priorite haute" in octets
    assert b"<svg" in octets


# --- PDF et HTML ------------------------------------------------------------


def test_pdf_accepte_le_spec_riche() -> None:
    octets = rendre(
        _spec(
            [
                Sommaire(),
                Titre(niveau=1, texte="Contexte"),
                Paragraphe(
                    runs=[
                        Segment(texte="Un mot "),
                        Segment(texte="en gras", gras=True),
                        Segment(texte=" et ", italique=True),
                        Segment(texte="souligne", souligne=True),
                    ],
                    alignement="justifie",
                ),
                Espacement(hauteur_mm=4.0),
                Tableau(
                    entete=["A", "B"],
                    lignes=[["1", "2"]],
                    marges_cellules_mm=1.0,
                    bordures="aucune",
                    couleur_entete="#1F4E79",
                ),
            ]
        ),
        format="pdf",
    )
    assert octets.startswith(b"%PDF")
    assert len(octets) > 2000


def test_html_fragments_sommaire_et_espacement() -> None:
    html = rendre(
        _spec(
            [
                Sommaire(titre="Sommaire", niveaux=2),
                Titre(niveau=1, texte="Contexte"),
                Paragraphe(
                    runs=[
                        Segment(texte="Important", gras=True, surligne="jaune"),
                        Segment(texte=" discret", italique=True),
                    ]
                ),
                Espacement(hauteur_mm=6.0),
                Tableau(entete=["A"], lignes=[["1"]], bordures="aucune"),
            ]
        ),
        format="html",
    ).decode("utf-8")
    assert "font-weight:bold" in html
    assert "background:#FFFF00" in html
    assert "font-style:italic" in html
    assert '<nav class="sommaire">' in html
    assert "<li" in html and "Contexte" in html
    assert "height:6.0mm" in html or "height:6mm" in html
    assert "border:0" in html


def test_html_surcharges_de_paragraphe() -> None:
    html = rendre(
        _spec([Paragraphe(texte="Centre", alignement="centre", taille_pt=14.0, retrait_mm=5.0)]),
        format="html",
    ).decode("utf-8")
    assert "text-align:center" in html
    assert "font-size:14.0pt" in html
    assert "margin-left:5.0mm" in html


# --- Modèle ----------------------------------------------------------------


def test_texte_et_runs_sont_exclusifs() -> None:
    with pytest.raises(PydanticValidationError):
        Paragraphe(texte="texte", runs=[Segment(texte="fragment")])


def test_paragraphe_sans_contenu_refuse() -> None:
    with pytest.raises(PydanticValidationError):
        Paragraphe()


def test_forme_bandeau_sans_texte_refusee() -> None:
    with pytest.raises(PydanticValidationError):
        Forme(variante="bandeau")


def test_bloc_par_type_connait_les_nouveaux_blocs() -> None:
    assert isinstance(bloc_par_type({"type": "sommaire"}), Sommaire)
    assert isinstance(bloc_par_type({"type": "espacement", "hauteur_mm": 4}), Espacement)
    assert isinstance(
        bloc_par_type({"type": "forme", "variante": "bandeau", "texte": "Titre"}), Forme
    )
    with pytest.raises(Exception) as erreur:
        bloc_par_type({"type": "inconnu"})
    assert "sommaire" in str(getattr(erreur.value, "details", {}))


def test_texte_brut_inclut_les_fragments() -> None:
    spec = _spec(
        [Paragraphe(runs=[Segment(texte="Un mot "), Segment(texte="en gras", gras=True)])]
    )
    assert "Un mot en gras" in spec.texte_brut()
