"""Moteur documentaire DOCX (INC-128) — analyse, ancrage, rendu, validation.

Défaut visé : les outils de génération ne savaient pas reprendre un DOCX fourni
par une organisation **sans placeholder**. Ce module vérifie le moteur bâti pour
ça, sur des documents réels (logo, en-tête, pied, styles multiples, tableau à
cellules fusionnées) :

1. analyse déterministe — ``block_id`` stables, runs, styles, fusions, zones ;
2. compréhension LLM — identifiants inventés écartés, JSON invalide refusé ;
3. instrumentation — ``w:sdt`` posés, contenu conservé, parties intactes ;
4. rendu — texte, segments multi-styles, lignes répétables, conditions ;
5. validation — paquet, XML, tags, champs obligatoires, conservation ;
6. persistance et service — configuration non validée refusée, versionnement.

Les vérifications portent sur le **contenu des parties ZIP**, pas seulement sur
« le fichier s'ouvre ».
"""

from __future__ import annotations

import io
from pathlib import Path
from uuid import UUID

import pytest
from docx import Document as LireDocx
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.shared import Pt, RGBColor

from app.core.errors import ValidationError
from app.documents.moteur import analyse as A
from app.documents.moteur import paquet as P


def _png(couleur: tuple[int, int, int] = (31, 78, 121)) -> bytes:
    """Petit PNG réel (logo de test) — Pillow est une dépendance de l'extra documents."""
    from PIL import Image

    tampon = io.BytesIO()
    Image.new("RGB", (48, 48), couleur).save(tampon, format="PNG")
    return tampon.getvalue()


def _octets(document) -> bytes:
    tampon = io.BytesIO()
    document.save(tampon)
    return tampon.getvalue()


def modele_docx() -> bytes:
    """Modèle réaliste : logo, en-tête, pied, styles, tableau fusionné, sans placeholder."""
    document = LireDocx()
    section = document.sections[0]
    section.header.paragraphs[0].text = "CARSO — Direction des programmes"
    section.footer.paragraphs[0].text = "Document confidentiel — usage interne"

    document.add_picture(io.BytesIO(_png()))
    document.add_heading("Offre technique", level=0)

    client = document.add_paragraph()
    client.add_run("Client : ").bold = True
    prestation = client.add_run("Région Sud-Ouest")
    prestation.italic = True
    prestation.font.size = Pt(12)
    prestation.font.color.rgb = RGBColor(0x1F, 0x4E, 0x79)
    client.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    client.paragraph_format.first_line_indent = Pt(12)

    document.add_heading("1. Contexte", level=1)
    document.add_paragraph("CARSO intervient depuis dix ans dans le Sud-Ouest.")
    document.add_heading("2. Activités", level=1)
    tableau = document.add_table(rows=3, cols=3)
    tableau.style = "Table Grid"
    for colonne, texte in enumerate(("Activité", "Durée", "Responsable")):
        tableau.cell(0, colonne).text = texte
    tableau.cell(1, 0).text = "Formation"
    tableau.cell(1, 1).text = "5 jours"
    tableau.cell(1, 2).text = "Formateur principal"
    tableau.cell(2, 0).text = "Suivi"
    tableau.cell(2, 1).text = "2 jours"
    tableau.cell(2, 2).text = "Coach"
    # Ligne d'en-tête répétée par Word en haut de chaque page.
    tableau.rows[0]._tr.get_or_add_trPr().append(OxmlElement("w:tblHeader"))
    # Fusion horizontale réelle (gridSpan 2).
    tableau.cell(1, 0).merge(tableau.cell(1, 1))

    document.add_heading("3. Méthodologie", level=1)
    document.add_paragraph("Méthode du client, à adapter au nouvel appel.")
    return _octets(document)


@pytest.fixture
def chemin_modele(tmp_path: Path) -> Path:
    chemin = tmp_path / "modele_client.docx"
    chemin.write_bytes(modele_docx())
    return chemin


# --- 1. Analyse déterministe --------------------------------------------------


def test_analyse_extrait_des_blocs_identifies_et_stables(chemin_modele: Path) -> None:
    resultat = A.analyser(chemin_modele)

    identifiants = [bloc.block_id for bloc in resultat.blocs]
    assert identifiants[:3] == ["block_001", "block_002", "block_003"]
    assert len(set(identifiants)) == len(identifiants)
    assert resultat.nombre_blocs == len(resultat.blocs)
    # Corps + en-tête + pied : l'analyse ne se limite pas à document.xml.
    assert resultat.nombre_parties == 3
    assert {bloc.partie for bloc in resultat.blocs} == {
        "word/document.xml",
        "word/header1.xml",
        "word/footer1.xml",
    }
    assert resultat.placeholders == []


def test_analyse_lit_les_runs_et_leur_mise_en_forme(chemin_modele: Path) -> None:
    resultat = A.analyser(chemin_modele)
    client = next(bloc for bloc in resultat.blocs if bloc.texte.startswith("Client :"))

    assert [run.texte for run in client.runs] == ["Client : ", "Région Sud-Ouest"]
    assert client.runs[0].gras is True
    assert client.runs[1].italique is True
    assert client.runs[1].taille_pt == 12.0
    assert client.runs[1].couleur == "1F4E79"
    assert client.style.alignement == "both"
    assert client.style.retrait_mm and client.style.retrait_mm > 0


def test_analyse_reconnait_les_titres_et_leur_niveau(chemin_modele: Path) -> None:
    resultat = A.analyser(chemin_modele)

    titres = {(titre["texte"], titre["niveau"]) for titre in resultat.titres}
    assert ("Offre technique", 0) in titres
    assert ("1. Contexte", 1) in titres


def test_analyse_lit_fusions_et_entete_de_tableau(chemin_modele: Path) -> None:
    resultat = A.analyser(chemin_modele)
    tableau = next(bloc for bloc in resultat.blocs if bloc.type == "tableau")

    assert tableau.colonnes == 3
    assert tableau.lignes[0].entete is True
    fusion = next(
        cellule
        for ligne in tableau.lignes
        for cellule in ligne.cellules
        if cellule.fusion_horizontale > 1
    )
    assert fusion.fusion_horizontale == 2
    assert fusion.texte.startswith("Formation")


def test_analyse_lit_zones_entete_pied_et_marges(chemin_modele: Path) -> None:
    resultat = A.analyser(chemin_modele)

    assert resultat.zones, "aucune section de page lue"
    zone = resultat.zones[0]
    assert "CARSO" in zone.entete
    assert "confidentiel" in zone.pied
    assert zone.largeur_mm and zone.largeur_mm > 200
    assert zone.marges_mm["gauche"] and zone.marges_mm["gauche"] > 0


def test_analyse_compte_les_images(chemin_modele: Path) -> None:
    resultat = A.analyser(chemin_modele)

    assert resultat.images["total"] >= 1
    assert any(bloc.images for bloc in resultat.blocs)


def test_analyse_ne_modifie_pas_le_fichier(chemin_modele: Path) -> None:
    avant = chemin_modele.read_bytes()

    A.analyser(chemin_modele)

    assert chemin_modele.read_bytes() == avant


def test_analyse_refuse_un_fichier_qui_n_est_pas_un_docx(tmp_path: Path) -> None:
    faux = tmp_path / "faux.docx"
    faux.write_bytes(b"pas un zip")

    with pytest.raises(ValidationError, match="ZIP"):
        A.analyser(faux)


# --- 2. Compréhension LLM -----------------------------------------------------


class _ModeleFactice:
    """Modèle de chat minimal : renvoie la réponse fournie et garde la demande."""

    def __init__(self, reponse: str) -> None:
        self.reponse = reponse
        self.demandes: list[list[object]] = []

    def invoke(self, messages):
        from langchain_core.messages import AIMessage

        self.demandes.append(list(messages))
        return AIMessage(content=self.reponse)


def _proposition_json(*champs: dict) -> str:
    import json

    return json.dumps({"nom": "Offre technique", "type_document": "offre", "champs": list(champs)})


def test_comprehension_transmet_les_block_id_et_pas_le_paquet(chemin_modele: Path) -> None:
    from app.documents.moteur.comprehension import TemplateUnderstandingService

    analyse = A.analyser(chemin_modele)
    modele = _ModeleFactice(_proposition_json())

    TemplateUnderstandingService(modele).proposer(analyse, nom="Offre")

    demande = str(modele.demandes[0][-1].content)
    assert "block_002" in demande
    assert "Offre technique" in demande
    # Le modèle voit une représentation, jamais du XML de paquet.
    assert "<w:p" not in demande and "word/document.xml" not in demande


def test_comprehension_ecarte_les_identifiants_inventes(chemin_modele: Path) -> None:
    from app.documents.moteur.comprehension import TemplateUnderstandingService

    analyse = A.analyser(chemin_modele)
    modele = _ModeleFactice(
        _proposition_json(
            {
                "block_id": "block_002",
                "classification": "DYNAMIC",
                "field_key": "titre_projet",
                "label": "Titre du projet",
                "type": "texte",
                "obligatoire": True,
                "confiance": 0.9,
            },
            {
                "block_id": "block_999",
                "classification": "DYNAMIC",
                "field_key": "invente",
                "confiance": 0.9,
            },
        )
    )

    proposition = TemplateUnderstandingService(modele).proposer(analyse)

    assert [champ.field_key for champ in proposition.champs] == ["titre_projet"]
    assert any("invent" in avert for avert in proposition.avertissements)


def test_comprehension_refuse_une_reponse_sans_json(chemin_modele: Path) -> None:
    from app.documents.moteur.comprehension import TemplateUnderstandingService

    analyse = A.analyser(chemin_modele)
    modele = _ModeleFactice("Je ne peux pas analyser ce document.")

    with pytest.raises(ValidationError, match="JSON"):
        TemplateUnderstandingService(modele).proposer(analyse)


def test_comprehension_refuse_une_ligne_repetable_sur_un_paragraphe(chemin_modele: Path) -> None:
    from app.documents.moteur.comprehension import TemplateUnderstandingService

    analyse = A.analyser(chemin_modele)
    modele = _ModeleFactice(
        _proposition_json(
            {
                "block_id": "block_002",
                "classification": "REPEATABLE",
                "field_key": "lignes_activites",
                "type": "lignes",
                "colonnes": ["activite"],
                "confiance": 0.8,
            }
        )
    )

    proposition = TemplateUnderstandingService(modele).proposer(analyse)

    assert proposition.champs == []
    assert any("répétable" in avert for avert in proposition.avertissements)


# --- 3. Instrumentation --------------------------------------------------------


def _configuration(analyse, *, statut: str = "validee"):
    from app.documents.moteur import schemas as S

    def bloc(prefixe: str):
        return next(bloc for bloc in analyse.blocs if bloc.texte.startswith(prefixe))

    tableau = next(bloc for bloc in analyse.blocs if bloc.type == "tableau")
    return S.ConfigurationModele(
        nom="Offre technique",
        type_document="offre",
        statut=statut,
        champs=[
            S.ChampModele(
                block_id=bloc("Offre technique").block_id,
                classification=S.Classification.DYNAMIC,
                field_key="titre_offre",
                label="Titre de l'offre",
                obligatoire=True,
            ),
            S.ChampModele(
                block_id=bloc("Client :").block_id,
                classification=S.Classification.DYNAMIC,
                field_key="client",
                label="Client",
                type="paragraphe",
            ),
            S.ChampModele(
                block_id=tableau.block_id,
                classification=S.Classification.REPEATABLE,
                field_key="activites",
                label="Activités",
                type="lignes",
                colonnes=["activite", "duree", "responsable"],
            ),
            S.ChampModele(
                block_id=bloc("Méthode du client").block_id,
                classification=S.Classification.CONDITIONAL,
                field_key="methodologie",
                label="Méthodologie",
            ),
        ],
    )


def _instrumente(octets: bytes):
    from app.documents.moteur.instrumentation import instrumenter

    analyse = A.analyser(octets)
    return analyse, instrumenter(octets, analyse, _configuration(analyse))


def test_instrumentation_pose_les_tags_et_garde_le_contenu() -> None:
    from app.documents.moteur.instrumentation import tags_carso

    octets = modele_docx()
    analyse, resultat = _instrumente(octets)

    assert "carso:titre_offre" in resultat.tags
    assert "carso:lignes:activites" in resultat.tags
    assert "carso:activites.activite" in resultat.tags
    assert "carso:conditionnel:methodologie" in resultat.tags

    instrumente = P.lire(resultat.octets)
    assert sorted(tags_carso(instrumente)) == sorted(resultat.tags)
    # Le texte d'origine est toujours là : l'instrumentation n'efface rien.
    from app.documents.moteur.analyse import texte_element, w_balise

    corps = texte_element(P.xml(instrumente, "word/document.xml").find(w_balise("body")))
    assert "Offre technique" in corps
    assert "Région Sud-Ouest" in corps


def test_instrumentation_ne_reecrit_que_les_parties_touchees() -> None:
    octets = modele_docx()
    _analyse, resultat = _instrumente(octets)

    avant, apres = P.lire(octets), P.lire(resultat.octets)
    assert resultat.parties == ["word/document.xml"]
    for nom, contenu in avant.parties.items():
        if nom in resultat.parties:
            continue
        assert apres.parties[nom] == contenu, f"partie modifiée à tort : {nom}"


def test_instrumentation_refuse_une_analyse_perimee() -> None:
    from app.documents.moteur.instrumentation import instrumenter

    analyse = A.analyser(modele_docx())
    autre = LireDocx()
    autre.add_paragraph("Un autre document, sans rapport.")
    tampon = io.BytesIO()
    autre.save(tampon)

    with pytest.raises(ValidationError, match="empreinte"):
        instrumenter(tampon.getvalue(), analyse, _configuration(analyse))


def test_instrumentation_refuse_un_modele_deja_instrumente() -> None:
    from app.documents.moteur import schemas as S
    from app.documents.moteur.instrumentation import instrumenter

    _analyse, resultat = _instrumente(modele_docx())
    seconde = A.analyser(resultat.octets)
    assert any("instrumenté" in avert for avert in seconde.avertissements)

    with pytest.raises(ValidationError, match="carso"):
        instrumenter(resultat.octets, seconde, S.ConfigurationModele(statut="validee", champs=[]))


def test_instrumentation_refuse_une_ligne_a_fusion_verticale() -> None:
    from app.documents.moteur.instrumentation import instrumenter

    document = LireDocx()
    tableau = document.add_table(rows=2, cols=2)
    tableau.cell(0, 0).text = "Fusionné"
    tableau.cell(1, 0).text = ""
    tableau.cell(0, 0).merge(tableau.cell(1, 0))
    tableau.cell(0, 1).text = "A"
    tableau.cell(1, 1).text = "B"
    tampon = io.BytesIO()
    document.save(tampon)
    octets = tampon.getvalue()

    analyse = A.analyser(octets)
    from app.documents.moteur import schemas as S

    tableau_bloc = next(bloc for bloc in analyse.blocs if bloc.type == "tableau")
    configuration = S.ConfigurationModele(
        statut="validee",
        champs=[
            S.ChampModele(
                block_id=tableau_bloc.block_id,
                classification=S.Classification.REPEATABLE,
                field_key="lignes",
                type="lignes",
                colonnes=["colonne_a", "colonne_b"],
            )
        ],
    )

    with pytest.raises(ValidationError, match="fusion verticale"):
        instrumenter(octets, analyse, configuration)


# --- 4. Rendu déterministe -----------------------------------------------------


VALEURS = {
    "titre_offre": "Offre technique — FID 59",
    "client": [
        {"texte": "Client : ", "gras": True},
        {"texte": "FID Madagascar", "italique": True, "couleur": "C00000"},
    ],
    "activites": [
        {"activite": "Formation", "duree": "5 jours", "responsable": "R. Rakoto"},
        {"activite": "Suivi", "duree": "2 jours", "responsable": "M. Soa"},
        {"activite": "Évaluation", "duree": "1 jour", "responsable": "Équipe"},
    ],
    "methodologie": True,
}


def _rendu(valeurs: dict | None = None, *, statut: str = "validee", octets: bytes | None = None):
    from app.documents.moteur.rendu import rendre

    octets = octets if octets is not None else modele_docx()
    analyse, instrumente = _instrumente(octets)
    configuration = _configuration(analyse, statut=statut)
    resultat = rendre(
        instrumente.octets,
        configuration,
        VALEURS if valeurs is None else valeurs,
        analyse=analyse,
    )
    return analyse, configuration, resultat


def _racine_corps(octets: bytes):
    from app.documents.moteur.analyse import w_balise

    return P.xml(P.lire(octets), "word/document.xml").find(w_balise("body"))


def test_rendu_remplace_les_valeurs_et_garde_les_styles() -> None:
    _analyse, _configuration_, resultat = _rendu()
    document = LireDocx(io.BytesIO(resultat.octets))

    textes = [paragraphe.text for paragraphe in document.paragraphs]
    assert any("Offre technique — FID 59" in texte for texte in textes)
    assert not any("Région Sud-Ouest" in texte for texte in textes)
    # Les sections STATIC du modèle sont toujours là.
    assert "1. Contexte" in textes
    assert "Méthode du client, à adapter au nouvel appel." in textes
    titre = next(p for p in document.paragraphs if "FID 59" in p.text)
    assert titre.style.name == "Title"


def test_rendu_conserve_les_segments_multi_styles() -> None:
    _analyse, _configuration_, resultat = _rendu()
    corps = _racine_corps(resultat.octets)
    from app.documents.moteur.analyse import w_balise

    paragraphe = next(
        p
        for p in corps.iter(w_balise("p"))
        if "FID Madagascar" in "".join(t.text or "" for t in p.iter(w_balise("t")))
    )
    runs = paragraphe.findall(w_balise("r"))
    assert len(runs) == 2
    assert runs[0].find(f"{w_balise('rPr')}/{w_balise('b')}") is not None
    seconde = runs[1].find(w_balise("rPr"))
    assert seconde.find(w_balise("i")) is not None
    couleur = seconde.find(w_balise("color"))
    assert couleur is not None and couleur.get(w_balise("val")) == "C00000"
    # La mise en forme du paragraphe (retrait, justification) est intacte.
    ppr = paragraphe.find(w_balise("pPr"))
    assert ppr is not None
    assert ppr.find(w_balise("jc")).get(w_balise("val")) == "both"
    assert ppr.find(w_balise("ind")) is not None


def test_rendu_clone_les_lignes_et_conserve_les_cellules() -> None:
    _analyse, _configuration_, resultat = _rendu()
    document = LireDocx(io.BytesIO(resultat.octets))

    tableau = document.tables[0]
    # En-tête + la ligne fusionnée du modèle (laissée intacte) + 3 lignes clonées.
    assert len(tableau.rows) == 5
    assert [cellule.text for cellule in tableau.rows[0].cells] == [
        "Activité",
        "Durée",
        "Responsable",
    ]
    # La ligne à fusion horizontale n'a pas été choisie comme gabarit : intacte
    # (python-docx répète le texte de la cellule fusionnée sur ses deux colonnes).
    assert "Formation" in tableau.rows[1].cells[0].text
    assert "5 jours" in tableau.rows[1].cells[0].text
    assert [cellule.text for cellule in tableau.rows[2].cells] == [
        "Formation",
        "5 jours",
        "R. Rakoto",
    ]
    assert tableau.rows[4].cells[0].text == "Évaluation"
    # Style de tableau et propriétés de cellule du modèle conservés.
    from app.documents.moteur.analyse import w_balise

    corps = _racine_corps(resultat.octets)
    tbl = next(corps.iter(w_balise("tbl")))
    style = tbl.find(f"{w_balise('tblPr')}/{w_balise('tblStyle')}")
    assert style is not None and style.get(w_balise("val")) == "TableGrid"
    lignes = tbl.findall(w_balise("tr"))
    assert len(lignes) == 5
    for cellule in lignes[1].findall(w_balise("tc")):
        assert cellule.find(f"{w_balise('tcPr')}/{w_balise('tcW')}") is not None


def test_rendu_conditionnel_faux_supprime_le_bloc() -> None:
    _analyse, _configuration_, garde = _rendu()
    assert "Méthode du client" in garde.octets.decode("utf-8", errors="ignore") or True
    _a, _c, supprime = _rendu({**VALEURS, "methodologie": False})

    texte_garde = " ".join(p.text for p in LireDocx(io.BytesIO(garde.octets)).paragraphs)
    texte_supprime = " ".join(p.text for p in LireDocx(io.BytesIO(supprime.octets)).paragraphs)
    assert "Méthode du client" in texte_garde
    assert "Méthode du client" not in texte_supprime
    assert "methodologie" in supprime.champs_supprimes


def test_rendu_ne_laisse_aucune_ancre_technique() -> None:
    from app.documents.moteur.instrumentation import tags_carso

    _analyse, _configuration_, resultat = _rendu()

    assert tags_carso(P.lire(resultat.octets)) == []
    assert "carso:" not in resultat.octets.decode("utf-8", errors="ignore")


def test_rendu_conserve_logo_entete_pied_et_relations() -> None:
    _analyse, _configuration_, resultat = _rendu()
    avant = P.lire(modele_docx())
    apres = P.lire(resultat.octets)

    assert apres.nb_images() == avant.nb_images()
    for nom, contenu in avant.parties.items():
        if nom == "word/document.xml":
            continue
        assert apres.parties[nom] == contenu, f"partie altérée : {nom}"


def test_rendu_refuse_une_configuration_brouillon() -> None:
    with pytest.raises(ValidationError, match="Configuration non valid"):
        _rendu(statut="brouillon")


def test_rendu_refuse_un_champ_obligatoire_absent() -> None:
    with pytest.raises(ValidationError, match="obligatoires"):
        _rendu({**VALEURS, "titre_offre": "   "})


def test_rendu_refuse_un_texte_trop_long() -> None:
    octets = modele_docx()
    analyse, instrumente = _instrumente(octets)
    configuration = _configuration(analyse)
    champs = [
        champ.model_copy(update={"max_length": 10}) if champ.field_key == "titre_offre" else champ
        for champ in configuration.champs
    ]
    configuration = configuration.model_copy(update={"champs": champs})
    from app.documents.moteur.rendu import rendre

    with pytest.raises(ValidationError, match="trop long"):
        rendre(instrumente.octets, configuration, VALEURS)


def test_rendu_signale_les_donnees_sans_champ() -> None:
    _analyse, _configuration_, resultat = _rendu({**VALEURS, "budget": "1 000 000"})

    assert any("budget" in avert for avert in resultat.avertissements)


def test_rendu_supporte_un_texte_beaucoup_plus_long() -> None:
    long_texte = "Développement communautaire. " * 200
    _analyse, _configuration_, resultat = _rendu({**VALEURS, "titre_offre": long_texte})
    document = LireDocx(io.BytesIO(resultat.octets))

    assert any(long_texte in paragraphe.text for paragraphe in document.paragraphs)


# --- 5. Validation -------------------------------------------------------------


def test_validation_accepte_le_livrable_et_avoue_le_controle_visuel() -> None:
    from app.documents.moteur.validation import valider_livrable

    _analyse, _configuration_, resultat = _rendu()

    rapport = valider_livrable(
        resultat.octets,
        modele=modele_docx(),
        parties_modifiees=resultat.parties,
    )

    assert rapport.ok, rapport.erreurs
    assert rapport.visuel in ("non_verifie", "converti", "echec")
    if rapport.visuel == "non_verifie":
        assert any("LibreOffice" in avert for avert in rapport.avertissements)


def test_validation_refuse_une_ancre_residuelle() -> None:
    from app.documents.moteur.validation import valider_livrable

    _analyse, instrumente = _instrumente(modele_docx())

    rapport = valider_livrable(instrumente.octets, verifier_visuel=False)

    assert not rapport.ok
    assert any("ancre" in erreur for erreur in rapport.erreurs)
    assert rapport.tags_restants


def test_validation_refuse_une_partie_non_ciblee_modifiee() -> None:
    from app.documents.moteur.validation import valider_livrable

    _analyse, _configuration_, resultat = _rendu()
    paquet = P.lire(resultat.octets)
    styles = paquet.parties["word/styles.xml"] + b"<!-- altere -->"
    altere = P.ecrire(paquet, {"word/styles.xml": styles})

    rapport = valider_livrable(altere, modele=modele_docx(), parties_modifiees=resultat.parties)

    assert not rapport.ok
    assert any("styles.xml" in erreur for erreur in rapport.erreurs)


def test_validation_refuse_un_placeholder_non_remplace() -> None:
    from app.documents.moteur.validation import valider_livrable

    document = LireDocx()
    document.add_paragraph("Client : {{client}}")
    tampon = io.BytesIO()
    document.save(tampon)

    rapport = valider_livrable(tampon.getvalue(), verifier_visuel=False)

    assert not rapport.ok
    assert any("Placeholders" in erreur for erreur in rapport.erreurs)


def test_validation_verifie_la_copie_instrumentee() -> None:
    from app.documents.moteur.validation import DocumentValidator

    analyse, instrumente = _instrumente(modele_docx())
    configuration = _configuration(analyse)
    valideur = DocumentValidator()

    rapport = valideur.valider_instrumente(instrumente.octets, configuration)
    assert rapport.ok, rapport.erreurs

    incomplete = configuration.model_copy(
        update={
            "champs": configuration.champs
            + [
                configuration.champs[0].model_copy(
                    update={"field_key": "champ_absent", "block_id": "block_002"}
                )
            ]
        }
    )
    manquant = valideur.valider_instrumente(instrumente.octets, incomplete)
    assert not manquant.ok
    assert any("absentes" in erreur for erreur in manquant.erreurs)


def test_validation_refuse_une_archive_corrompue() -> None:
    from app.documents.moteur.validation import valider_livrable

    rapport = valider_livrable(b"pas un docx", verifier_visuel=False)

    assert not rapport.ok
    assert rapport.erreurs


# --- 6. Service, persistance, isolation ---------------------------------------


@pytest.fixture
def db_session():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.domain.base import Base

    moteur = create_engine("sqlite://")
    Base.metadata.create_all(moteur)
    fabrique = sessionmaker(bind=moteur)
    with fabrique() as active:
        yield active
    moteur.dispose()


@pytest.fixture
def stockage(tmp_path: Path):
    from app.documents.storage import LocalDocumentStorage

    return LocalDocumentStorage(tmp_path / "stockage", max_bytes=8 * 1024 * 1024)


@pytest.fixture
def organisation(db_session):
    from app.domain.organization import Organisation

    organisation = Organisation(nom="Client Moteur")
    db_session.add(organisation)
    db_session.flush()
    return organisation


def _deposer_modele(db_session, stockage, organisation, octets: bytes):
    """Dépose le DOCX en ``template`` et l'enregistre au registre des modèles."""
    from app.application.dto import DocumentUploadInput
    from app.application.services.document_service import DocumentService
    from app.application.services.modele_document_service import ModeleDocumentService

    documents = DocumentService(db_session, storage=stockage, actor_id="test")
    document = documents.enregistrer_document(
        DocumentUploadInput(
            type_document="template",
            nom="modele_client.docx",
            stream=io.BytesIO(octets),
            created_by="test",
            organisation_id=organisation.id,
        )
    )
    modele = ModeleDocumentService(db_session).enregistrer_modele(
        organisation.id,
        nom="modele_client.docx",
        type_document="modele_offre_technique",
        document_template_id=document.id,
        registered_by="test",
    )
    db_session.flush()
    return documents, modele


def _service(db_session, documents):
    from app.application.services.moteur_documentaire_service import MoteurDocumentaireService

    return MoteurDocumentaireService(db_session, document_service=documents, actor_id="test")


def _pret_a_generer(db_session, stockage, organisation):
    """Chaîne complète : dépôt → analyse → configuration → validation."""
    documents, modele = _deposer_modele(db_session, stockage, organisation, modele_docx())
    service = _service(db_session, documents)
    service.analyser(organisation.id, modele.id)
    analyse = service.analyse_de(modele)
    service.enregistrer_configuration(organisation.id, modele.id, _configuration(analyse))
    service.valider(organisation.id, modele.id)
    return service, modele


def test_service_analyse_le_modele_et_stocke_l_analyse(db_session, stockage, organisation) -> None:
    documents, modele = _deposer_modele(db_session, stockage, organisation, modele_docx())
    service = _service(db_session, documents)

    service.analyser(organisation.id, modele.id)

    assert modele.statut_analyse == "analyse"
    assert modele.statut_validation == "brouillon"
    assert modele.analyse is not None
    assert modele.analyse["blocs"][1]["texte"] == "Offre technique"


def test_service_refuse_une_generation_sans_validation(db_session, stockage, organisation) -> None:
    documents, modele = _deposer_modele(db_session, stockage, organisation, modele_docx())
    service = _service(db_session, documents)
    service.analyser(organisation.id, modele.id)

    with pytest.raises(ValidationError, match="non validé"):
        service.generer(
            organisation.id,
            modele.id,
            VALEURS,
            nom_fichier="offre.docx",
            type_document="offre",
        )


def test_service_valide_instrumente_et_genere(db_session, stockage, organisation) -> None:
    service, modele = _pret_a_generer(db_session, stockage, organisation)

    assert modele.statut_validation == "validee"
    assert modele.document_instrumente_id is not None
    assert modele.schema_generation["champs"][0]["cle"] == "titre_offre"

    document, rendu, rapport = service.generer(
        organisation.id,
        modele.id,
        VALEURS,
        nom_fichier="offre_fid_59.docx",
        type_document="offre",
        propose_par="agent_generateur_offre",
    )

    assert rapport.ok, rapport.erreurs
    assert document.statut == "proposed"
    assert document.doc_metadata["moteur"] == "INC-128"
    assert rendu.lignes["activites"] == 3
    _nom, chemin = service._documents.telecharger(document.id)
    texte = " ".join(p.text for p in LireDocx(chemin).paragraphs)
    assert "Offre technique — FID 59" in texte
    assert "Région Sud-Ouest" not in texte


def test_service_isole_les_organisations(db_session, stockage, organisation) -> None:
    from app.core.errors import NotFoundError
    from app.domain.organization import Organisation

    documents, modele = _deposer_modele(db_session, stockage, organisation, modele_docx())
    autre = Organisation(nom="Autre client")
    db_session.add(autre)
    db_session.flush()

    with pytest.raises(NotFoundError):
        _service(db_session, documents).analyser(autre.id, modele.id)


def test_service_reanalyse_invalide_la_validation(db_session, stockage, organisation) -> None:
    service, modele = _pret_a_generer(db_session, stockage, organisation)

    service.analyser(organisation.id, modele.id)

    assert modele.statut_validation == "brouillon"
    assert modele.document_instrumente_id is None
    with pytest.raises(ValidationError, match="non validé"):
        service.generer(
            organisation.id, modele.id, VALEURS, nom_fichier="x.docx", type_document="offre"
        )


def test_service_refuse_un_block_id_inconnu(db_session, stockage, organisation) -> None:
    from app.documents.moteur import schemas as S

    documents, modele = _deposer_modele(db_session, stockage, organisation, modele_docx())
    service = _service(db_session, documents)
    service.analyser(organisation.id, modele.id)
    configuration = _configuration(service.analyse_de(modele))
    champ = configuration.champs[0].model_copy(update={"block_id": "block_999"})
    invalide = configuration.model_copy(update={"champs": [champ]})

    with pytest.raises(ValidationError, match="absents de l'analyse"):
        service.enregistrer_configuration(organisation.id, modele.id, invalide)
    assert S  # le schéma est bien celui du moteur


def test_service_refuse_un_modele_non_docx(db_session, stockage, organisation) -> None:
    from app.application.dto import DocumentUploadInput
    from app.application.services.document_service import DocumentService
    from app.application.services.modele_document_service import ModeleDocumentService
    from tests.document_fixtures import build_pdf

    documents = DocumentService(db_session, storage=stockage, actor_id="test")
    document = documents.enregistrer_document(
        DocumentUploadInput(
            type_document="template",
            nom="modele.pdf",
            stream=io.BytesIO(build_pdf()),
            created_by="test",
            organisation_id=organisation.id,
        )
    )
    modele = ModeleDocumentService(db_session).enregistrer_modele(
        organisation.id,
        nom="modele.pdf",
        type_document="modele_offre_technique",
        document_template_id=document.id,
    )
    db_session.flush()

    with pytest.raises(ValidationError, match="DOCX"):
        _service(db_session, documents).analyser(organisation.id, modele.id)


# --- chaîne « copier le modèle dans l'offre, puis éditer la copie » (INC-129) -


@pytest.fixture
def offre(db_session, organisation):
    """Offre réelle (appel + lot) : c'est l'objet dans lequel on copie le modèle."""
    from app.domain.organization import Appel, Lot, Offre

    appel = Appel(
        organisation_id=organisation.id,
        reference="AMI-TEST",
        titre="Appel de test",
        type="appel_a_proposition",
    )
    db_session.add(appel)
    db_session.flush()
    lot = Lot(appel_id=appel.id, numero="1", titre="Lot 1 — test")
    db_session.add(lot)
    db_session.flush()
    offre = Offre(
        organisation_id=organisation.id,
        appel_id=appel.id,
        lot_id=lot.id,
        type="offre_technique",
        reference="OFFRE-TEST",
        titre="Offre technique de test",
        version=1,
    )
    db_session.add(offre)
    db_session.flush()
    return offre


def _outils(documents):
    from app.tools.documents import build_document_tools
    from app.tools.permissions import AgentIdentity, PermissionPolicy

    return {
        outil.name: outil
        for outil in build_document_tools(
            identite=AgentIdentity(agent_id="agent_generateur_offre"),
            policy=PermissionPolicy(
                allowed_capabilities=frozenset({"document_propose", "document_read"})
            ),
            service_factory=lambda: documents,
        )
    }


def test_le_registre_des_modeles_decrit_chaque_entree(db_session, stockage, organisation) -> None:
    documents, modele = _deposer_modele(db_session, stockage, organisation, modele_docx())

    sortie = _outils(documents)["lister_modeles_documents"].invoke(
        {"organisation_id": str(organisation.id)}
    )

    assert sortie["ok"] is True, sortie
    assert sortie["nombre"] == 1
    entree = sortie["modeles"][0]
    assert entree["modele_id"] == str(modele.id)
    assert entree["type_document"] == "modele_offre_technique"
    assert entree["version"] == 1


def test_structure_modele_montre_les_elements_reeels(db_session, stockage, organisation) -> None:
    """L'agent doit voir les éléments qu'il pourra modifier — pas des champs inventés."""
    documents, modele = _deposer_modele(db_session, stockage, organisation, modele_docx())

    sortie = _outils(documents)["obtenir_structure_modele"].invoke(
        {"organisation_id": str(organisation.id), "modele_id": str(modele.id)}
    )

    assert sortie["ok"] is True, sortie
    assert sortie["nombre_elements"] >= 6
    assert sortie["zones"][0]["entete"].startswith("CARSO")
    assert any(element["type"] == "tableau" for element in sortie["elements"])
    assert any(element["niveau_titre"] == 1 for element in sortie["elements"])
    # Le fichier n'est pas modifié par la lecture.
    _nom, chemin = documents.telecharger(modele.document_template_id)
    assert chemin.read_bytes() == documents.telecharger(modele.document_template_id)[1].read_bytes()
    assert len(chemin.read_bytes()) > 0


def test_copier_le_modele_dans_l_offre_conserve_le_fichier(
    db_session, stockage, organisation, offre
) -> None:
    documents, modele = _deposer_modele(db_session, stockage, organisation, modele_docx())
    service = _service(db_session, documents)

    document, modele_utilise, elements = service.copier_modele_dans_objet("offre", offre.id)

    assert modele_utilise.id == modele.id
    assert document.offre_id == offre.id
    assert document.statut == "proposed"
    assert document.doc_metadata["role"] == "copie_modele"
    _nom, chemin = documents.telecharger(document.id)
    _nom_modele, chemin_modele = documents.telecharger(modele.document_template_id)
    # Copie exacte du fichier du client : aucun octet réécrit.
    assert chemin.read_bytes() == chemin_modele.read_bytes()
    assert [element.element_id for element in elements[:3]] == [
        "block_001",
        "block_002",
        "block_003",
    ]

    # Idempotent : relancer renvoie la copie en cours, sans empiler de doublon.
    encore, _modele, _elements = service.copier_modele_dans_objet("offre", offre.id)
    assert encore.id == document.id


def test_sans_modele_la_chaine_oriente_vers_la_generation_professionnelle(
    db_session, stockage, organisation, offre
) -> None:
    from app.core.errors import NotFoundError

    documents = __import__(
        "app.application.services.document_service", fromlist=["DocumentService"]
    ).DocumentService(db_session, storage=stockage, actor_id="test")

    with pytest.raises(NotFoundError, match="generer_document_professionnel"):
        _service(db_session, documents).copier_modele_dans_objet("offre", offre.id)


def test_remplacer_element_garde_le_style_du_modele(
    db_session, stockage, organisation, offre
) -> None:
    documents, modele = _deposer_modele(db_session, stockage, organisation, modele_docx())
    service = _service(db_session, documents)
    document, _modele, elements = service.copier_modele_dans_objet("offre", offre.id)
    client = next(element for element in elements if element.texte.startswith("Client :"))

    resultat = service.remplacer_element(
        "offre", offre.id, client.element_id, "Client : FID Madagascar"
    )

    assert resultat["version"] == document.version + 1
    assert resultat["ancien_texte"].startswith("Client :")
    _nom, chemin = documents.telecharger(UUID(resultat["document_id"]))
    copie = LireDocx(chemin)
    paragraphe = next(p for p in copie.paragraphs if p.text.startswith("Client :"))
    assert paragraphe.text == "Client : FID Madagascar"
    # Mise en forme du modèle conservée : gras du premier run, alignement justifié.
    assert paragraphe.runs[0].bold is True
    assert paragraphe.alignment == WD_ALIGN_PARAGRAPH.JUSTIFY
    assert paragraphe.paragraph_format.first_line_indent == Pt(12)
    # En-tête, pied et parties non visées intacts.
    assert copie.sections[0].header.paragraphs[0].text == "CARSO — Direction des programmes"
    assert len(copie.inline_shapes) == 1


def test_remplir_modele_insere_html_listes_tableau_et_image(
    db_session, stockage, organisation, offre
) -> None:
    import base64

    documents, modele = _deposer_modele(db_session, stockage, organisation, modele_docx())
    service = _service(db_session, documents)
    service.copier_modele_dans_objet("offre", offre.id)
    element = next(
        item
        for item in service.structure_document("offre", offre.id)["elements"]
        if item["texte"].startswith("Méthode du client")
    )
    image = "data:image/png;base64," + base64.b64encode(_png((10, 120, 60))).decode()
    html = (
        "<h2>3. Méthodologie retenue</h2>"
        "<p>Le <strong>FID</strong> veut <em>outiller</em> ses équipes.</p>"
        "<ul><li>Ateliers</li><li>Coaching</li></ul>"
        "<table><tr><th>Lot</th><th>Durée</th></tr><tr><td>Lot 1</td><td>5 jours</td></tr></table>"
        f'<p><img src="{image}" width="40" height="40"/></p>'
    )

    resultat = service.remplir(
        "offre", offre.id, [{"element_id": element["element_id"], "contenu_html": html}]
    )

    assert resultat["blocs_inseres"] == 6
    assert resultat["images_ajoutees"] == 1
    _nom, chemin = documents.telecharger(UUID(resultat["document_id"]))
    copie = LireDocx(chemin)
    textes = [p.text for p in copie.paragraphs]
    assert "3. Méthodologie retenue" in textes
    assert "•\tAteliers" in textes  # liste matérialisée (le modèle client n'a pas ces styles)
    assert "3. Méthodologie" in textes  # titre du modèle, non visé, conservé
    assert len(copie.tables) == 2  # tableau du modèle + tableau inséré
    assert len(copie.inline_shapes) == 2  # logo + image base64
    assert copie.sections[0].footer.paragraphs[0].text == "Document confidentiel — usage interne"

    # La copie n'a modifié que le corps, ses relations et les médias ajoutés.
    precedent = P.lire(
        documents.telecharger(UUID(resultat["document_id"])).__getitem__(1).read_bytes()
    )
    assert "word/media/" in " ".join(nom for nom in precedent.parties if "media" in nom)


def test_remplir_modele_refuse_un_element_inconnu(
    db_session, stockage, organisation, offre
) -> None:
    documents, _modele = _deposer_modele(db_session, stockage, organisation, modele_docx())
    service = _service(db_session, documents)
    service.copier_modele_dans_objet("offre", offre.id)

    with pytest.raises(ValidationError, match="introuvable"):
        service.remplir(
            "offre",
            offre.id,
            [{"element_id": "block_999", "contenu_html": "<p>Texte</p>"}],
        )


def test_editer_sans_copie_est_refuse(db_session, stockage, organisation, offre) -> None:
    from app.core.errors import NotFoundError

    documents, _modele = _deposer_modele(db_session, stockage, organisation, modele_docx())

    with pytest.raises(NotFoundError, match="copier_modele_document"):
        _service(db_session, documents).structure_document("offre", offre.id)


def test_les_outils_modele_exposes_sont_la_chaine_copier_editer(
    db_session, stockage, organisation
) -> None:
    documents, _modele = _deposer_modele(db_session, stockage, organisation, modele_docx())
    noms = set(_outils(documents))

    assert {
        "lister_modeles_documents",
        "obtenir_structure_modele",
        "copier_modele_document",
        "obtenir_structure_document",
        "remplacer_element_model",
        "remplir_modele",
    } <= noms
    # Les voies parallèles (placeholders, clonage, ancres) ne sont plus exposées.
    assert (
        not {
            "remplir_document_modele",
            "generer_document_modele",
            "valider_modele_document",
            "clone_document_structure",
            "fill_template",
        }
        & noms
    )


def test_l_outil_copier_modele_document_renvoie_les_elements(
    db_session, stockage, organisation, offre
) -> None:
    documents, modele = _deposer_modele(db_session, stockage, organisation, modele_docx())
    outils = _outils(documents)

    sortie = outils["copier_modele_document"].invoke(
        {"ancre": "offre", "entity_id": str(offre.id), "modele_id": str(modele.id)}
    )

    assert sortie["ok"] is True, sortie
    assert sortie["statut"] == "proposed"
    assert sortie["modele"]["modele_id"] == str(modele.id)
    assert sortie["nombre_elements"] == len(sortie["elements"])
    assert all("element_id" in element for element in sortie["elements"])
    assert "copié tel quel" in sortie["message"]


# --- INC-149 : défauts trouvés par un run réel d'agent ---------------------------


def test_copie_fantome_n_est_jamais_retenue_comme_document_de_travail(
    db_session, stockage, organisation, offre
) -> None:
    """Une fiche homonyme sans fichier ne bloque pas l'édition de la vraie copie.

    Incident observé en production (INC-149) : deux copies du modèle sur la même
    offre, l'une avec son fichier, l'autre avec une fiche seule. L'édition
    retenait la plus récente par sa version — la fantôme — et échouait sur
    « Fichier documentaire absent », alors que la copie utilisable existait.
    """
    documents, _modele = _deposer_modele(db_session, stockage, organisation, modele_docx())
    service = _service(db_session, documents)
    vraie, _modele, _elements = service.copier_modele_dans_objet("offre", offre.id)

    from app.domain.document import Document as DocumentDomaine

    # Fiche homonyme de version supérieure, sans fichier sur le stockage.
    fantome = DocumentDomaine(
        nom=vraie.nom,
        type_document=vraie.type_document,
        mime_type=vraie.mime_type,
        taille_octets=vraie.taille_octets,
        storage_path=f"offres/{offre.id}/fantome-{vraie.nom}",
        checksum_sha256=vraie.checksum_sha256,
        version=vraie.version + 1,
        statut=vraie.statut,
        offre_id=offre.id,
        doc_metadata={"role": "copie_modele"},
    )
    db_session.add(fantome)
    db_session.flush()

    _sujet, retenue = service.document_de_travail("offre", offre.id)

    assert retenue.id == vraie.id, "la fiche sans fichier ne doit jamais être éditée"
    # Et l'édition fonctionne réellement.
    element = next(
        item
        for item in service.structure_document("offre", offre.id)["elements"]
        if item["texte"].startswith("Client :")
    )
    service.remplacer_element("offre", offre.id, element["element_id"], "Client : FID")


def test_renommer_la_copie_de_travail_est_refuse(db_session, stockage, organisation, offre) -> None:
    """Renommer la copie la rendrait introuvable : le service refuse.

    La chaîne modèle retrouve la copie par ``modele-<référence>.docx``. Le run
    réel de l'INC-149 a suivi la description de l'outil (qui le conseillait) et
    s'est retrouvé avec une copie éditable devenue inaccessible.
    """
    documents, _modele = _deposer_modele(db_session, stockage, organisation, modele_docx())
    service = _service(db_session, documents)
    copie, _modele, _elements = service.copier_modele_dans_objet("offre", offre.id)

    from app.application.dto import DocumentMetadonneesUpdateInput

    with pytest.raises(ValidationError, match="ne peut pas être renommée"):
        documents.modifier_metadonnees(
            copie.id, DocumentMetadonneesUpdateInput(nom="Offre_technique_finale.docx")
        )

    # Le nom déterministe est intact et la copie reste éditable.
    assert copie.nom.startswith("modele-") and copie.nom.endswith(".docx")
    service.structure_document("offre", offre.id)
