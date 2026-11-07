"""Réduction automatique des images inline avant insertion (INC-134).

Une image collée en base64 (spec) ou en data-URI (corps HTML professionnel)
arrive à la résolution de l'appareil : insérée telle quelle, elle alourdit le
DOCX/PDF sans gagner de détail visible. Ces tests vérifient trois choses :

1. la borne réelle (côté ≤ 1600 px, poids ≤ 1 Mio) et l'absence de dégradation
   inutile (petite image intacte, animation préservée, réduction idempotente) ;
2. le livrable déposé contient bien la **version réduite** (média DOCX mesuré) ;
3. la transformation est **signalée** dans le retour des deux tools.
"""

from __future__ import annotations

import base64
import io
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from PIL import Image as PILImage
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.application.services import DocumentService
from app.documents.images_inline import (
    COTE_MAX_INLINE,
    OCTETS_CIBLE,
    Reduction,
    reduire,
    reduire_blocs,
    reduire_data_uri,
)
from app.documents.storage import LocalDocumentStorage
from app.domain.base import Base
from app.domain.organization import Organisation
from app.tools.documents import build_document_tools
from app.tools.permissions import AgentIdentity, PermissionPolicy

IDENTITE = AgentIdentity(agent_id="agent_generateur_offre")
POLICY = PermissionPolicy(
    allowed_capabilities=frozenset({"document_read", "document_propose"})
)


# --- Images de test -----------------------------------------------------------


def _octets(image: PILImage.Image, format: str = "PNG", **options: Any) -> bytes:
    tampon = io.BytesIO()
    image.save(tampon, format=format, **options)
    return tampon.getvalue()


def _png_blanc(largeur: int, hauteur: int) -> bytes:
    """PNG très compressible mais **large** : seule la dimension déclenche."""
    return _octets(PILImage.new("RGB", (largeur, hauteur), "white"))


def _png_bruite(largeur: int, hauteur: int) -> bytes:
    """PNG incompressible : le poids déclenche, même sous 1600 px."""
    return _octets(PILImage.effect_noise((largeur, hauteur), 120).convert("RGB"))


def _dimensions(octets: bytes) -> tuple[int, int]:
    with PILImage.open(io.BytesIO(octets)) as image:
        return image.size


# --- Borne et fidélité --------------------------------------------------------


def test_petite_image_reste_intacte() -> None:
    """Sous les deux bornes, aucune réécriture : les octets d'origine font foi."""
    source = _octets(PILImage.new("RGB", (320, 200), "white"))

    reduction = reduire(source)

    assert reduction.modifiee is False
    assert reduction.octets is source
    assert reduction.phrase() == ""


def test_image_large_reduite_au_cote_maximal() -> None:
    source = _png_blanc(2_000, 1_500)

    reduction = reduire(source)

    assert reduction.modifiee is True
    assert max(_dimensions(reduction.octets)) == COTE_MAX_INLINE
    assert (reduction.largeur, reduction.hauteur) == (1_600, 1_200)
    assert len(reduction.octets) < len(source)
    assert "2000×1500" in reduction.phrase().replace(" ", "")


def test_image_lourde_allagee_sous_la_cible() -> None:
    """Une image incompressible sous 1600 px est allégée sans dépasser la cible."""
    source = _png_bruite(1_200, 900)
    assert len(source) > OCTETS_CIBLE  # sinon le test ne prouve rien

    reduction = reduire(source)

    assert reduction.modifiee is True
    assert len(reduction.octets) < len(source)
    assert max(_dimensions(reduction.octets)) <= COTE_MAX_INLINE


def test_reduction_idempotente() -> None:
    """Revalider un spec ne doit pas raboter l'image une deuxième fois."""
    premier = reduire(_png_bruite(1_200, 900))

    second = reduire(premier.octets)

    assert second.modifiee is False
    assert second.octets == premier.octets


def test_gif_anime_jamais_aplati() -> None:
    """Réduire une image d'une animation détruirait les suivantes : on n'y touche pas."""
    images = [
        PILImage.effect_noise((1_800, 1_200), 80 + index).convert("P")
        for index in range(3)
    ]
    tampon = io.BytesIO()
    images[0].save(
        tampon, format="GIF", save_all=True, append_images=images[1:], duration=120
    )
    source = tampon.getvalue()

    reduction = reduire(source)

    assert reduction.modifiee is False
    with PILImage.open(io.BytesIO(reduction.octets)) as image:
        assert image.n_frames == 3


def test_phrase_de_recompression_sans_changement_de_dimensions() -> None:
    reduction = Reduction(
        octets=b"x" * 500_000,
        format="jpg",
        largeur=1_400,
        hauteur=1_000,
        octets_source=2_000_000,
        largeur_source=1_400,
        hauteur_source=1_000,
        modifiee=True,
    )

    assert "recompressée" in reduction.phrase("Figure 2")
    assert "dimensions conservées" in reduction.phrase("Figure 2")


# --- Blocs bruts (entrée des tools) -------------------------------------------


def test_reduire_blocs_ne_touche_qu_aux_images_inline() -> None:
    source = _png_blanc(2_000, 1_500)
    charge = base64.b64encode(source).decode("ascii")
    blocs = [
        {"type": "titre", "texte": "Contexte"},
        {"type": "image", "base64": charge, "legende": "Schéma de test"},
        {"type": "image", "document_id": "00000000-0000-0000-0000-000000000001"},
    ]

    reduits, phrases = reduire_blocs(blocs)

    assert len(phrases) == 1
    assert "Schéma de test" in phrases[0] and "réduite" in phrases[0]
    assert blocs[1]["base64"] == charge  # l'entrée n'est jamais mutée
    assert len(reduits[1]["base64"]) < len(charge)
    assert reduits[0] == blocs[0] and reduits[2] == blocs[2]


def test_reduire_blocs_accepte_le_prefixe_data_uri() -> None:
    charge = "data:image/png;base64," + base64.b64encode(_png_blanc(2_000, 1_500)).decode(
        "ascii"
    )

    reduits, phrases = reduire_blocs(
        [{"type": "image", "base64": charge, "alt": "Étapes du processus"}]
    )

    assert len(phrases) == 1 and "Étapes du processus" in phrases[0]
    assert "data:" not in reduits[0]["base64"]


# --- Corps HTML professionnel -------------------------------------------------


def _html_avec_image(octets: bytes) -> str:
    charge = base64.b64encode(octets).decode("ascii")
    return (
        "<h1>Rapport de mission</h1>"
        "<p class='lead'>Un paragraphe réel, inchangé.</p>"
        f"<figure><img src='data:image/png;base64,{charge}' alt='Capture'></figure>"
    )


def test_data_uri_reecrite_sans_toucher_au_reste_du_html() -> None:
    source = _png_blanc(2_000, 1_500)

    html, phrases = reduire_data_uri(_html_avec_image(source))

    assert len(phrases) == 1 and "réduite" in phrases[0]
    assert "<h1>Rapport de mission</h1>" in html
    charge = html.split("base64,", 1)[1].split("'", 1)[0]
    assert max(_dimensions(base64.b64decode(charge))) == COTE_MAX_INLINE


def test_data_uri_petite_laissee_intacte() -> None:
    html = _html_avec_image(_octets(PILImage.new("RGB", (64, 64), "white")))

    reecrit, phrases = reduire_data_uri(html)

    assert phrases == []
    assert reecrit == html


# --- Retour des tools ---------------------------------------------------------


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as active:
        yield active
    engine.dispose()


@pytest.fixture
def service(session: Session, tmp_path: Path) -> DocumentService:
    storage = LocalDocumentStorage(tmp_path / "carso", max_bytes=8 * 1024 * 1024)
    return DocumentService(session, storage=storage, actor_id="test")


@pytest.fixture
def organisation(session: Session) -> Organisation:
    entite = Organisation(nom="Client Formation")
    session.add(entite)
    session.flush()
    return entite


def _outils(service: DocumentService) -> dict[str, Any]:
    construits = build_document_tools(
        identite=IDENTITE, policy=POLICY, service_factory=lambda: service
    )
    return {outil.name: outil for outil in construits}


def test_generer_document_reduit_et_signale(
    service: DocumentService, organisation: Organisation
) -> None:
    source = _png_blanc(2_400, 1_800)
    sortie = _outils(service)["generer_document"].invoke(
        {
            "format": "docx",
            "nom_fichier": "note_capture",
            "type_document": "rapport",
            "ancre": "organisation",
            "entity_id": str(organisation.id),
            "propose_par": IDENTITE.agent_id,
            "valider": False,
            "document_spec": {
                "metadata": {"titre": "Note avec capture"},
                "blocs": [
                    {"type": "paragraphe", "texte": "Un paragraphe réel."},
                    {
                        "type": "image",
                        "base64": base64.b64encode(source).decode("ascii"),
                        "largeur_mm": 80,
                        "legende": "Capture",
                    },
                ],
            },
        }
    )

    assert any("réduite" in message for message in sortie["warnings"])
    assert "base64" not in str(sortie["document_spec"])

    _nom, chemin = service.telecharger(UUID(str(sortie["documents"]["principal"])))
    with zipfile.ZipFile(chemin) as archive:
        medias = [nom for nom in archive.namelist() if nom.startswith("word/media/")]
        assert medias
        media = archive.read(medias[0])
    assert max(_dimensions(media)) == COTE_MAX_INLINE
    assert len(media) < len(source)


def test_generer_document_professionnel_signale_la_reduction(
    service: DocumentService, organisation: Organisation
) -> None:
    sortie = _outils(service)["generer_document_professionnel"].invoke(
        {
            "format": "docx",
            "titre": "Rapport de mission",
            "nom_fichier": "rapport_mission",
            "type_document": "rapport",
            "ancre": "organisation",
            "entity_id": str(organisation.id),
            "propose_par": IDENTITE.agent_id,
            "contenu_html": _html_avec_image(_png_blanc(2_000, 1_500)),
        }
    )

    assert any("réduite" in message for message in sortie["avertissements"])
    # Réduire une image n'est pas un défaut de conformité.
    assert "PAS conforme" not in sortie["message"]
