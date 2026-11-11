"""Tests — composition d'image et contrôle de design (INC-136).

Ce que ces tests verrouillent :

1. **la mise en page appartient au serveur** : un gabarit + un contenu donnent
   une image aux bonnes dimensions, reproductible, et qui passe son propre
   contrôle de design ;
2. **les palettes sont lisibles** : les couples fond/texte, fond/surtitre et
   fond/texte doux respectent les seuils WCAG — un bleu institutionnel sur du
   marine ne doit pas se retrouver dans une image livrée ;
3. **le contenu qui ne tient pas est refusé**, jamais empilé sur le pied de page ;
4. **le contrôle mesure des défauts réels** : texte hors cadre, police illisible,
   contraste insuffisant, image uniforme, fond transparent — avec la nuance WCAG
   sur le grand texte (3:1 au lieu de 4,5:1) ;
5. **`verifier_image_svg` ne dépose rien** : c'est une boucle de correction, pas
   une deuxième porte de livraison.
"""

from __future__ import annotations

import io
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from PIL import Image as PILImage
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.application.perimetre import Perimetre
from app.application.services import DocumentService
from app.documents.composition_image import (
    GABARITS,
    PALETTES,
    CompositionRefusee,
    composer_svg,
)
from app.documents.controle_image import diagnostiquer
from app.documents.controle_image import _contraste, _couleur
from app.documents.image_generee import depuis_svg
from app.documents.storage import LocalDocumentStorage
from app.domain.base import Base
from app.domain.document import Document
from app.tools.documents import build_document_tools
from app.tools.permissions import AgentIdentity, PermissionPolicy

IDENTITE = AgentIdentity(agent_id="agent_generateur_offre")
POLICY = PermissionPolicy(
    allowed_capabilities=frozenset({"document_read", "document_propose"})
)

#: Un sous-titre didactique qu'aucun bandeau ne peut absorber : la composition
#: doit refuser plutôt que de laisser le texte marcher sur le pied de page.
SOUS_TITRE_VERBEUX = (
    "Cycle de cinq jours pour les équipes du FID à Toliara, avec accompagnement "
    "terrain, évaluation finale, remise des attestations et suivi post-formation "
    "pendant trois mois."
) * 2

CONTENU = {
    "surtitre": "Formation",
    "titre": "Intelligence artificielle appliquée",
    "sous_titre": "Cycle de cinq jours pour les équipes du FID, Toliara.",
    "points": ["Agents IA et usages métier", "Analyse de données", "Aide à la décision"],
    "pied": "CARSO AI — proposition 2026",
}

#: SVG volontairement fautif : texte qui sort du cadre, gris sur gris, police 9 px.
SVG_FAUTIF = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="200" height="100" '
    'viewBox="0 0 200 100">'
    '<rect width="200" height="100" fill="#808080"/>'
    '<text x="10" y="50" font-size="30" fill="#909090">'
    "Un titre beaucoup trop long pour ce cadre</text>"
    '<text x="10" y="90" font-size="9" fill="#909090">mention</text>'
    "</svg>"
)


# --- Fixtures -----------------------------------------------------------------


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as active:
        yield active
    engine.dispose()


@pytest.fixture
def service(session: Session, tmp_path: Path) -> DocumentService:
    storage = LocalDocumentStorage(tmp_path / "carso", max_bytes=16 * 1024 * 1024)
    return DocumentService(
        session,
        storage=storage,
        actor_id="test",
        perimetre=Perimetre(role="administrateur", utilisateur_id=uuid4(), est_complet=True),
    )


def _outils(service: DocumentService) -> dict[str, Any]:
    construits = build_document_tools(
        identite=IDENTITE, policy=POLICY, service_factory=lambda: service
    )
    return {outil.name: outil for outil in construits}


def _diagnostics(svg: str, **kw: Any) -> list[str]:
    image = depuis_svg(svg, echelle=1.0)
    largeur, hauteur = image.rendu or (image.largeur, image.hauteur)
    return [
        constat.code
        for constat in diagnostiquer(
            image.source_svg, largeur=largeur, hauteur=hauteur, png=image.octets, **kw
        )
    ]


# --- 1. Composition : mise en page côté serveur --------------------------------


def test_composition_reproductible() -> None:
    """Même contenu + même gabarit = même image : la qualité est déterministe."""
    premier = composer_svg(**CONTENU)
    second = composer_svg(**CONTENU)

    assert premier == second
    assert premier.startswith("<svg")
    # Le titre est coupé en lignes : on vérifie le contenu mot par mot.
    assert "Intelligence" in premier and "appliquée" in premier


@pytest.mark.parametrize("nom_gabarit", sorted(GABARITS))
def test_gabarit_donne_les_bonnes_dimensions(nom_gabarit: str) -> None:
    gabarit = GABARITS[nom_gabarit]
    points = CONTENU["points"][: gabarit.points_max]

    image = depuis_svg(
        composer_svg(**{**CONTENU, "points": points, "gabarit_nom": nom_gabarit}),
        echelle=1.0,
    )

    assert (image.largeur, image.hauteur) == (gabarit.largeur, gabarit.hauteur)


@pytest.mark.parametrize("nom_gabarit", sorted(GABARITS))
@pytest.mark.parametrize("nom_palette", sorted(PALETTES))
def test_composition_passe_son_propre_controle(nom_gabarit: str, nom_palette: str) -> None:
    """Aucun défaut mesurable sur une composition par défaut — sinon la garde
    signalerait nos propres images."""
    gabarit = GABARITS[nom_gabarit]
    svg = composer_svg(
        **{
            **CONTENU,
            "points": CONTENU["points"][: gabarit.points_max],
            "gabarit_nom": nom_gabarit,
            "palette_nom": nom_palette,
        }
    )

    assert _diagnostics(svg) == []


@pytest.mark.parametrize("nom_palette", sorted(PALETTES))
def test_palettes_respectent_les_seuils_de_contraste(nom_palette: str) -> None:
    palette = PALETTES[nom_palette]
    fond = _couleur(palette.fond)

    assert _contraste(_couleur(palette.texte), fond) >= 4.5
    # Surtitre et textes secondaires : seuil du grand texte (3:1).
    assert _contraste(_couleur(palette.accent_texte), fond) >= 3.0
    assert _contraste(_couleur(palette.doux), fond) >= 3.0


def test_titre_obligatoire() -> None:
    with pytest.raises(CompositionRefusee, match="titre"):
        composer_svg(titre="   ")


def test_gabarit_inconnu_refuse() -> None:
    with pytest.raises(CompositionRefusee, match="Gabarit inconnu"):
        composer_svg(titre="Titre", gabarit_nom="poster-geant")


def test_trop_de_points_refuse() -> None:
    with pytest.raises(CompositionRefusee, match="accepte au plus"):
        composer_svg(titre="Titre", gabarit_nom="bandeau", points=["a", "b", "c"])


def test_contenu_trop_long_refuse_au_lieu_de_deborder() -> None:
    """Un bandeau ne doit jamais laisser un texte marcher sur le pied de page."""
    with pytest.raises(CompositionRefusee, match="trop long"):
        composer_svg(
            surtitre="Appel à proposition — programme FID",
            titre="Renforcer les capacités des formateurs du Sud-Ouest",
            sous_titre=SOUS_TITRE_VERBEUX,
            points=["Formation certifiante", "Accompagnement terrain"],
            pied="CARSO AI — proposition 2026",
            gabarit_nom="bandeau",
        )


# --- 2. Contrôle de design ----------------------------------------------------


def test_svg_propre_ne_produit_aucun_constat() -> None:
    assert _diagnostics(composer_svg(**CONTENU)) == []


def test_texte_hors_cadre_est_bloquant() -> None:
    constats = diagnostiquer(
        SVG_FAUTIF,
        largeur=200,
        hauteur=100,
        png=depuis_svg(SVG_FAUTIF).octets,
    )

    bloquants = [constat for constat in constats if constat.niveau == "bloquant"]
    assert any(constat.code == "texte_hors_cadre" for constat in bloquants)


def test_contraste_gris_sur_gris_est_bloquant() -> None:
    codes = {constat.code for constat in diagnostiquer(SVG_FAUTIF, largeur=200, hauteur=100)}

    assert "contraste_insuffisant" in codes


def test_grand_texte_tolere_le_seuil_wcag_de_3() -> None:
    """Un titre de 26 px en gras sur bleu institutionnel (≈ 4,3:1) est conforme."""
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="600" height="200" '
        'viewBox="0 0 600 200">'
        '<rect width="600" height="200" fill="#0070D8"/>'
        '<text x="30" y="110" font-size="26" font-weight="bold" fill="#EAF3FD">'
        "Renforcer les capacités</text>"
        "</svg>"
    )

    assert "contraste_insuffisant" not in _diagnostics(svg)


def test_petit_texte_en_contraste_insuffisant_reste_signale() -> None:
    """Le même couple de couleurs en 14 px passe sous le seuil du texte courant."""
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="600" height="200" '
        'viewBox="0 0 600 200">'
        '<rect width="600" height="200" fill="#0070D8"/>'
        '<text x="30" y="110" font-size="14" fill="#E6F1FC">mention légale</text>'
        "</svg>"
    )

    codes = {constat.code for constat in _constats(svg)}

    assert "contraste_insuffisant" in codes


def _constats(svg: str) -> list[Any]:
    image = depuis_svg(svg, echelle=1.0)
    largeur, hauteur = image.rendu or (image.largeur, image.hauteur)
    return diagnostiquer(image.source_svg, largeur=largeur, hauteur=hauteur, png=image.octets)


def test_texte_trop_petit_signale() -> None:
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="400" height="200" '
        'viewBox="0 0 400 200"><rect width="400" height="200" fill="white"/>'
        '<text x="20" y="100" font-size="9" fill="black">minuscule</text></svg>'
    )

    assert "texte_trop_petit" in {constat.code for constat in _constats(svg)}


def test_image_uniforme_signalee() -> None:
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="300" height="200" '
        'viewBox="0 0 300 200"><rect width="300" height="200" fill="#123456"/></svg>'
    )

    codes = {constat.code for constat in _constats(svg)}

    assert "image_vide" in codes
    assert "aucun_texte" in codes


def test_fond_transparent_signale() -> None:
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="300" height="200" '
        'viewBox="0 0 300 200"><circle cx="150" cy="100" r="60" fill="#0070D8"/></svg>'
    )

    assert "fond_transparent" in {constat.code for constat in _constats(svg)}


def test_sans_png_le_controle_reste_utilisable() -> None:
    """Le contrôle des textes ne dépend pas du rendu : il marche sur le SVG seul."""
    constats = diagnostiquer(SVG_FAUTIF, largeur=200, hauteur=100)

    assert any(constat.code == "texte_hors_cadre" for constat in constats)


# --- 3. Les tools -------------------------------------------------------------


def test_composer_image_depose_une_image_propre(service: DocumentService) -> None:
    sortie = _outils(service)["composer_image"].invoke(
        {**CONTENU, "nom_fichier": "carte-formation", "gabarit": "carte"}
    )

    assert sortie["ok"] is True
    assert sortie["nom"] == "carte-formation.png"
    assert (sortie["largeur_px"], sortie["hauteur_px"]) == (1200, 1200)
    assert sortie["diagnostics"] == []
    assert sortie["gabarit"] == "carte"
    assert "afficher_image" in sortie["message"]

    _nom, chemin = service.telecharger(UUID(str(sortie["document_id"])))
    with PILImage.open(io.BytesIO(Path(chemin).read_bytes())) as rendu:
        assert rendu.format == "PNG"
        assert rendu.size == (1200, 1200)


def test_composer_image_refuse_un_contenu_trop_long(service: DocumentService) -> None:
    sortie = _outils(service)["composer_image"].invoke(
        {
            "nom_fichier": "bandeau",
            "gabarit": "bandeau",
            "surtitre": "Appel à proposition — programme FID",
            "titre": "Renforcer les capacités des formateurs du Sud-Ouest",
            "sous_titre": SOUS_TITRE_VERBEUX,
            "points": ["Formation certifiante", "Accompagnement terrain"],
            "pied": "CARSO AI — proposition 2026",
        }
    )

    assert sortie["ok"] is False
    assert "trop long" in sortie["error"]["message"]


def test_verifier_image_svg_ne_depose_rien(
    service: DocumentService, session: Session
) -> None:
    sortie = _outils(service)["verifier_image_svg"].invoke({"svg": SVG_FAUTIF})

    assert sortie["ok"] is True
    assert sortie["pret_a_deposer"] is False
    assert any(diag["niveau"] == "bloquant" for diag in sortie["diagnostics"])
    assert session.scalar(select(func.count()).select_from(Document)) == 0


def test_verifier_image_svg_accepte_un_svg_propre(service: DocumentService) -> None:
    sortie = _outils(service)["verifier_image_svg"].invoke(
        {"svg": composer_svg(**CONTENU)}
    )

    assert sortie["ok"] is True
    assert sortie["diagnostics"] == []
    assert sortie["pret_a_deposer"] is True


def test_generer_image_svg_remonte_les_defauts(service: DocumentService) -> None:
    """Une image fautive est déposée mais **signalée** : l'agent doit corriger
    avant de l'afficher."""
    sortie = _outils(service)["generer_image_svg"].invoke(
        {"nom_fichier": "brouillon", "svg": SVG_FAUTIF}
    )

    assert sortie["ok"] is True
    assert sortie["pret_a_afficher"] is False
    assert sortie["diagnostics"]
    assert "corriger" in sortie["message"]


def test_les_six_agents_portent_la_composition_et_le_controle() -> None:
    from app.agents.catalogue import AGENT_RUNTIME

    for definition in AGENT_RUNTIME:
        noms = set(definition.tools)
        assert "composer_image" in noms, definition.agent_id
        assert "verifier_image_svg" in noms, definition.agent_id
