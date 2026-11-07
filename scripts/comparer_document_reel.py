"""Reproduit une pièce CARSO réelle (rapport budgétaire v5) avec le DocumentSpec riche.

Même contenu lisible, même découpage de titres, mêmes tableaux, mêmes images ;
la mise en forme passe par les tokens du spec, pas par le fichier d'origine.
Sortie : reproduction DOCX/PDF + mesures de fidélité, pour décider ce qui manque.
"""

from __future__ import annotations

import json
import re
import unicodedata
import zipfile
from pathlib import Path

from docx import Document
from docx.table import Table
from docx.text.paragraph import Paragraph

from app.documents.assets import ImagesMemoire
from app.documents.renderers import rendre
from app.documents.spec import (
    Cellule,
    Couverture,
    DocumentSpec,
    Encadre,
    Graphique,
    Image,
    Liste,
    Metadonnees,
    MiseEnPage,
    Paragraphe,
    Segment,
    SerieGraphique,
    Sommaire,
    Tableau,
    Titre,
)

SOURCE = Path(
    "var/storage/carso/generated/500759bd-608d-4127-a0b2-04ddf6768b44"
    "/rapport_budgetaire_mission_ami_fid_59_v5.docx"
)
SORTIE = Path("var/comparaison")
SORTIE.mkdir(parents=True, exist_ok=True)

doc = Document(SOURCE)
images = ImagesMemoire()
SOURCE_JSON = Path(
    "var/storage/carso/_sources/500759bd-608d-4127-a0b2-04ddf6768b44.json"
)
GRAPHIQUES = json.loads(SOURCE_JSON.read_text(encoding="utf-8"))["graphiques"]


def _norm(texte: str) -> str:
    sans_accent = "".join(
        c for c in unicodedata.normalize("NFD", texte) if unicodedata.category(c) != "Mn"
    )
    return re.sub(r"\s+", " ", sans_accent.lower()).strip()


def _segments(paragraphe: Paragraph) -> list[Segment] | None:
    segments: list[Segment] = []
    for run in paragraphe.runs:
        if not run.text:
            continue
        couleur = None
        if run.font.color is not None and run.font.color.rgb is not None:
            couleur = f"#{run.font.color.rgb}"
        segments.append(
            Segment(
                texte=run.text,
                gras=True if run.font.bold else None,
                italique=True if run.font.italic else None,
                souligne=True if run.font.underline else None,
                couleur=couleur,
                taille_pt=run.font.size.pt if run.font.size else None,
            )
        )
    return segments or None


def _cellule(cellule) -> Cellule:
    texte = "\n".join(p.text for p in cellule.paragraphs).strip()
    gras = all(r.font.bold for p in cellule.paragraphs for r in p.runs if r.text) or None
    return Cellule(texte=texte[:5000], gras=gras)


def _tableau(table: Table) -> list[object]:
    largeurs = [
        round(colonne.width.mm, 2) if colonne.width else None
        for colonne in table.columns
    ]
    largeurs_propres = [largeur for largeur in largeurs if largeur]
    lignes = [[_cellule(cellule) for cellule in ligne.cells] for ligne in table.rows]
    if len(table.rows) == 1 and len(table.columns) == 1:
        texte = lignes[0][0].texte_complet().strip()
        minuscule = _norm(texte)
        if minuscule.startswith("total"):
            return [
                Paragraphe(
                    runs=[Segment(texte=texte, gras=True)],
                    alignement="centre",
                )
            ]
        if "information manquante" in minuscule:
            return [Encadre(genre="avertissement", titre="Information manquante", texte=texte)]
        if minuscule.startswith("note"):
            return [Encadre(genre="note", texte=texte)]
        if re.match(r"^\d+\.", minuscule):
            return [Encadre(genre="info", texte=texte)]
        return [Paragraphe(texte=texte)]
    return [
        Tableau(
            entete=[cellule.texte_complet() for cellule in lignes[0]],
            lignes=[[cellule for cellule in ligne] for ligne in lignes[1:]],
            largeurs_mm=largeurs_propres if len(largeurs_propres) == len(table.columns) else None,
        )
    ]


# --- Couverture : la 1re table 1x1 du rapport porte l'identité de la pièce -----
cover = doc.tables[0]
lignes_couverture = [ligne.strip() for ligne in cover.cell(0, 0).text.split("\n") if ligne.strip()]
titre_couverture = lignes_couverture[0] if lignes_couverture else "Rapport budgétaire"
sous_titre = lignes_couverture[1] if len(lignes_couverture) > 1 else None
COUVERTURE = {_norm(ligne) for ligne in lignes_couverture}

# --- Images du rapport -------------------------------------------------------
medias: dict[str, bytes] = {}
with zipfile.ZipFile(SOURCE) as archive:
    for nom in archive.namelist():
        if nom.startswith("word/media/"):
            cle = f"rapport/{Path(nom).name}"
            medias[cle] = archive.read(nom)
            images.ajouter(cle, medias[cle])
noms_medias = sorted(medias)

# --- Corps du document, dans l'ordre ----------------------------------------
blocs: list[object] = [Sommaire()]
puces: list[str] = []
index_media = 0
index_table = 0


def _vider_puces() -> None:
    if puces:
        blocs.append(Liste(items=list(puces)))
        puces.clear()


for element in doc.element.body.iterchildren():
    if element.tag.endswith("}tbl"):
        _vider_puces()
        table = Table(element, doc)
        if index_table == 0:
            index_table += 1
            continue
        index_table += 1
        blocs.extend(_tableau(table))
        continue
    if not element.tag.endswith("}p"):
        continue
    paragraphe = Paragraph(element, doc)
    style = paragraphe.style.name
    texte = paragraphe.text.strip()
    if "<w:drawing" in element.xml and index_media < len(noms_medias):
        _vider_puces()
        if index_media < len(GRAPHIQUES):
            donnees = GRAPHIQUES[index_media]
            if donnees.get("type") in ("pie", "donut"):
                blocs.append(
                    Graphique(
                        variante=donnees["type"],
                        titre=donnees.get("titre"),
                        labels=donnees["labels"],
                        valeurs=donnees["valeurs"],
                        legende=donnees.get("legende"),
                        largeur_mm=150.0,
                    )
                )
            else:
                blocs.append(
                    Graphique(
                        variante=donnees.get("type", "bar"),
                        titre=donnees.get("titre"),
                        labels=donnees["labels"],
                        series=[
                            SerieGraphique(nom=serie["nom"], valeurs=serie["valeurs"])
                            for serie in donnees.get("series") or []
                        ],
                        y_label=donnees.get("y_label"),
                        legende=donnees.get("legende"),
                        largeur_mm=150.0,
                    )
                )
        else:
            blocs.append(
                Image(chemin=noms_medias[index_media], largeur_mm=150.0, alt="Figure du rapport")
            )
        index_media += 1
        continue
    if "TOC" in element.xml or "Mettre à jour les champs" in texte:
        continue
    if _norm(texte) in COUVERTURE or _norm(texte) == "sommaire":
        continue
    if style.startswith("Heading"):
        _vider_puces()
        niveau = int(style.split()[-1])
        blocs.append(Titre(niveau=min(4, niveau), texte=texte))
        continue
    if style == "List Bullet" and texte:
        puces.append(texte)
        continue
    _vider_puces()
    if not texte:
        continue
    if style == "Lead":
        blocs.append(Paragraphe(texte=texte, role="chapeau"))
    elif style == "Legende":
        # La légende de figure est déjà portée par le bloc ``graphique``.
        if not texte.lower().startswith("figure"):
            blocs.append(Paragraphe(texte=texte, role="note"))
    else:
        segments = _segments(paragraphe)
        if segments and len(segments) > 1:
            blocs.append(Paragraphe(runs=segments))
        else:
            blocs.append(Paragraphe(texte=texte))
_vider_puces()

section = doc.sections[0]
spec = DocumentSpec(
    metadata=Metadonnees(
        titre=f"{titre_couverture} — MISSION-AMI-FID-59",
        sous_titre=sous_titre,
        reference="MISSION-AMI-FID-59",
        organisation="CARSO — Centre d'Affaires de la Région Sud-Ouest",
        type_document="rapport",
    ),
    mise_en_page=MiseEnPage(
        format="A4",
        orientation="portrait",
        numero_de_page=True,
        entete_texte=doc.sections[0].header.paragraphs[0].text.strip() or None,
        couverture=Couverture(
            active=True,
            titre=titre_couverture,
            sous_titre=sous_titre,
            organisation="CARSO — Centre d'Affaires de la Région Sud-Ouest",
            champs=[],
        ),
    ),
    blocs=blocs,
)

docx_bytes = rendre(spec, format="docx", images=images)
pdf_bytes = rendre(spec, format="pdf", images=images)
(SORTIE / "rapport_graphiques.docx").write_bytes(docx_bytes)
(SORTIE / "rapport_graphiques.pdf").write_bytes(pdf_bytes)

# --- Mesures de fidélité ------------------------------------------------------
texte_original = "\n".join(
    [p.text for p in doc.paragraphs]
    + [c.text for t in doc.tables for row in t.rows for c in row.cells]
)
mots_original = set(_norm(texte_original).split())
mots_reproduction = set(_norm(spec.texte_brut()).split())
couverture_mots = len(mots_original & mots_reproduction) / max(1, len(mots_original))

titres_origine = [
    p.text.strip()
    for p in doc.paragraphs
    if p.style.name.startswith("Heading") and p.text.strip()
]
titres_reproduction = [bloc.texte for bloc in blocs if isinstance(bloc, Titre)]

tableaux_origine = len(doc.tables)
tableaux_reproduction = sum(1 for bloc in blocs if isinstance(bloc, Tableau))

entetes_origine = [
    [cellule.text.strip() for cellule in table.rows[0].cells] for table in doc.tables
]
entetes_reproduction = [
    tuple(_norm(texte) for texte in (bloc.entete or []))
    for bloc in blocs
    if isinstance(bloc, Tableau)
]
entetes_origine_normalisees = {
    tuple(_norm(cellule) for cellule in ligne) for ligne in entetes_origine if ligne
}
entetes_identiques = sum(
    1 for entete in entetes_reproduction if entete in entetes_origine_normalisees
)

mesures = {
    "source": str(SOURCE),
    "mots_origine": len(mots_original),
    "mots_reproduction": len(mots_reproduction),
    "couverture_lexicale": round(couverture_mots, 3),
    "titres_origine": len(titres_origine),
    "titres_reproduction": len(titres_reproduction),
    "titres_identiques": sum(
        1 for a, b in zip(titres_origine, titres_reproduction, strict=False) if _norm(a) == _norm(b)
    ),
    "tableaux_origine": tableaux_origine,
    "tableaux_reproduction": tableaux_reproduction,
    "images_origine": len(noms_medias),
    "images_reproduction": sum(1 for bloc in blocs if isinstance(bloc, Image)),
    "graphiques_regeneres": sum(1 for bloc in blocs if isinstance(bloc, Graphique)),
    "puces_origine": sum(1 for p in doc.paragraphs if p.style.name == "List Bullet"),
    "puces_reproduction": sum(len(bloc.items) for bloc in blocs if isinstance(bloc, Liste)),
    "blocs_par_type": {
        type(bloc).__name__: sum(1 for b in blocs if type(b) is type(bloc))
        for bloc in blocs
    },
    "entetes_de_tableau_identiques": entetes_identiques,
    "champs_du_texte_manquants": sorted(mots_original - mots_reproduction)[:20],
}
(SORTIE / "mesures.json").write_text(
    __import__("json").dumps(mesures, ensure_ascii=False, indent=2), encoding="utf-8"
)
print(__import__("json").dumps(mesures, ensure_ascii=False, indent=2))
