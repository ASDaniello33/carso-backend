"""Tests — génération d'image pour tous les agents (INC-135).

Deux chemins, un seul livrable : une **image réelle** fournie en base64
(``generer_image``) ou un **SVG** écrit par le modèle puis rasterisé
(``generer_image_svg``). Ce que ces tests verrouillent :

1. le fichier déposé est bien l'image préparée (format et dimensions mesurés,
   réduction appliquée et **signalée**) ;
2. le type vient de la **signature** : un contenu qui n'est pas une image est
   refusé, jamais deviné ;
3. le SVG d'agent est nettoyé avant rasterisation, et un SVG vide échoue avec un
   diagnostic ;
4. les six agents exposés portent les deux tools et la capacité de dépôt.
"""

from __future__ import annotations

import base64
import io
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from PIL import Image as PILImage
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.application.perimetre import Perimetre
from app.application.services import DocumentService
from app.documents.storage import LocalDocumentStorage
from app.domain.base import Base
from app.domain.organization import Organisation
from app.tools.documents import build_document_tools
from app.tools.permissions import AgentIdentity, PermissionPolicy

IDENTITE = AgentIdentity(agent_id="agent_generateur_offre")
POLICY = PermissionPolicy(
    allowed_capabilities=frozenset({"document_read", "document_propose"})
)
SANS_DEPOT = PermissionPolicy(allowed_capabilities=frozenset({"document_read"}))


# --- Images de test -----------------------------------------------------------


def _png(largeur: int, hauteur: int) -> bytes:
    tampon = io.BytesIO()
    PILImage.new("RGB", (largeur, hauteur), "white").save(tampon, format="PNG")
    return tampon.getvalue()


def _jpeg(largeur: int, hauteur: int) -> bytes:
    tampon = io.BytesIO()
    PILImage.new("RGB", (largeur, hauteur), (200, 120, 40)).save(
        tampon, format="JPEG"
    )
    return tampon.getvalue()


def _charge(octets: bytes) -> str:
    return base64.b64encode(octets).decode("ascii")


def _dimensions(octets: bytes) -> tuple[int, int]:
    with PILImage.open(io.BytesIO(octets)) as image:
        return image.size


SVG_LOGO = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 100">'
    '<rect width="200" height="100" fill="#0a7a5e"/>'
    '<text x="12" y="58" font-size="28" fill="white">CARSO</text>'
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


@pytest.fixture
def organisation(session: Session) -> Organisation:
    entite = Organisation(nom="Client Formation")
    session.add(entite)
    session.flush()
    return entite


def _outils(
    service: DocumentService, *, policy: PermissionPolicy = POLICY
) -> dict[str, Any]:
    construits = build_document_tools(
        identite=IDENTITE, policy=policy, service_factory=lambda: service
    )
    return {outil.name: outil for outil in construits}


def _fichier_depose(service: DocumentService, sortie: dict[str, Any]) -> bytes:
    _nom, chemin = service.telecharger(UUID(str(sortie["document_id"])))
    return Path(chemin).read_bytes()


# --- generer_image : l'image réelle en base64 ---------------------------------


def test_image_reelle_deposee_telle_quelle(service: DocumentService) -> None:
    source = _png(320, 200)

    sortie = _outils(service)["generer_image"].invoke(
        {"nom_fichier": "figure", "base64": _charge(source)}
    )

    assert sortie["ok"] is True
    assert sortie["nom"] == "figure.png"
    assert sortie["format"] == "png"
    assert (sortie["largeur_px"], sortie["hauteur_px"]) == (320, 200)
    # Sous les bornes d'insertion : aucun réencodage, les octets d'origine.
    assert _fichier_depose(service, sortie) == source
    assert "afficher_image" in sortie["message"]
    assert sortie["avertissements"] == []


def test_image_large_reduite_et_signalee(service: DocumentService) -> None:
    sortie = _outils(service)["generer_image"].invoke(
        {"nom_fichier": "capture", "base64": _charge(_png(2400, 1800))}
    )

    assert sortie["format"] == "png"
    assert max(_dimensions(_fichier_depose(service, sortie))) == 1600
    assert any("réduite" in phrase for phrase in sortie["avertissements"])


def test_prefixe_data_uri_accepte(service: DocumentService) -> None:
    charge = "data:image/png;base64," + _charge(_png(120, 80))

    sortie = _outils(service)["generer_image"].invoke(
        {"nom_fichier": "icone", "base64": charge}
    )

    assert sortie["ok"] is True
    assert sortie["format"] == "png"


def test_jpeg_reste_un_jpeg(service: DocumentService) -> None:
    """Le format déposé suit la signature : un JPEG reste un JPEG."""
    sortie = _outils(service)["generer_image"].invoke(
        {"nom_fichier": "photo", "base64": _charge(_jpeg(400, 300))}
    )

    assert sortie["nom"] == "photo.jpg"
    assert sortie["format"] == "jpg"


def test_base64_invalide_refuse(service: DocumentService) -> None:
    sortie = _outils(service)["generer_image"].invoke(
        {"nom_fichier": "x", "base64": "ceci-n'est-pas-du-base64!!"}
    )

    assert sortie["ok"] is False
    assert "base64" in sortie["error"]["message"]


def test_contenu_non_image_refuse(service: DocumentService) -> None:
    """Un PDF (ou une vidéo) n'est pas une image : la signature décide."""
    sortie = _outils(service)["generer_image"].invoke(
        {"nom_fichier": "faux", "base64": _charge(b"%PDF-1.7 pas une image")}
    )

    assert sortie["ok"] is False
    assert "image reconnue" in sortie["error"]["message"]


def test_ancre_range_l_image_sous_l_objet(
    service: DocumentService, organisation: Organisation
) -> None:
    sortie = _outils(service)["generer_image"].invoke(
        {
            "nom_fichier": "logo_client",
            "base64": _charge(_png(200, 100)),
            "type_document": "justificatif",
            "ancre": "organisation",
            "entity_id": str(organisation.id),
        }
    )

    assert sortie["ok"] is True
    assert "non_classe" not in sortie
    document = service.obtenir(UUID(str(sortie["document_id"])))
    assert document.organisation_id == organisation.id


# --- generer_image_svg : dessin du modèle -------------------------------------


def test_svg_rasterise_en_png(service: DocumentService) -> None:
    sortie = _outils(service)["generer_image_svg"].invoke(
        {"nom_fichier": "logo.svg", "svg": SVG_LOGO}
    )

    assert sortie["ok"] is True
    # L'extension annoncée (SVG) est remplacée par le format réellement produit.
    assert sortie["nom"] == "logo.png"
    assert sortie["format"] == "png"
    assert (sortie["largeur_px"], sortie["hauteur_px"]) == (400, 200)
    assert _dimensions(_fichier_depose(service, sortie)) == (400, 200)
    assert any("rasterisée" in phrase for phrase in sortie["avertissements"])


def test_svg_est_nettoye_avant_rasterisation(service: DocumentService) -> None:
    """Script et référence distante ne survivent pas au nettoyage."""
    from app.documents.professionnel.html_builder import assainir_svg

    malveillant = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 50">'
        "<script>alert(1)</script>"
        '<image href="https://exemple.test/pixel.png"/>'
        '<rect width="100" height="50" fill="#123"/>'
        "</svg>"
    )
    propre = assainir_svg(malveillant)
    assert "<script" not in propre
    assert "https://" not in propre

    sortie = _outils(service)["generer_image_svg"].invoke(
        {"nom_fichier": "nettoye", "svg": malveillant}
    )
    assert sortie["ok"] is True


def test_svg_demesure_refuse_avant_rasterisation(service: DocumentService) -> None:
    """Un SVG au-delà du plafond est refusé : la rasterisation ne doit pas l'essayer.

    Régression INC-135 (suivi) : un SVG déclarant ``width='44000' height='51200'``
    entre guillemets **simples** échappait à la garde de taille, resvg rendait
    88 000 × 102 400 px et demandait 36 Go pour son pixmap ; l'échec d'allocation
    tuait le processus backend (abort Rust, pas une exception Python).
    """
    geant = (
        "<svg xmlns='http://www.w3.org/2000/svg' width='44000' height='51200'>"
        "<rect width='100%' height='100%' fill='red'/></svg>"
    )

    sortie = _outils(service)["generer_image_svg"].invoke(
        {"nom_fichier": "geant.svg", "svg": geant}
    )

    assert sortie["ok"] is False
    assert "trop grand" in sortie["error"]["message"]


def test_svg_sans_balise_refuse(service: DocumentService) -> None:
    sortie = _outils(service)["generer_image_svg"].invoke(
        {"nom_fichier": "vide", "svg": "un simple texte, pas un dessin"}
    )

    assert sortie["ok"] is False
    assert "SVG" in sortie["error"]["message"]


# --- Permissions et périmètre des agents --------------------------------------


def test_depot_refuse_sans_capacite(service: DocumentService) -> None:
    outils = _outils(service, policy=SANS_DEPOT)

    sortie = outils["generer_image"].invoke(
        {"nom_fichier": "figure", "base64": _charge(_png(80, 60))}
    )

    assert sortie["ok"] is False
    assert "document_propose" in sortie["error"]["message"]


def test_tous_les_agents_portent_la_generation_d_image() -> None:
    from app.agents.agent_personnel import DEFINITION as PERSONNEL
    from app.agents.agent_rh import DEFINITION as RH
    from app.agents.agent_statistique import DEFINITION as STAT
    from app.agents.assistant_formateur import DEFINITION as ASSISTANT
    from app.agents.generaliste_readonly import DEFINITION as GENERALISTE
    from app.agents.generateur_offre import DEFINITION as GENERATEUR

    for definition in (GENERALISTE, GENERATEUR, RH, ASSISTANT, STAT, PERSONNEL):
        assert "generer_image" in definition.tools, definition.agent_id
        assert "generer_image_svg" in definition.tools, definition.agent_id
        # Le dépôt d'une image est une proposition : la capacité doit suivre.
        assert "document_propose" in definition.capabilities, definition.agent_id


def test_chaque_agent_monte_les_tools_de_son_contrat(session: Session) -> None:
    """Déclarer un tool ne suffit pas : il doit être **monté** pour le modèle.

    Constat à l'écran (INC-135) : le généraliste répondait « l'outil n'est pas
    disponible dans mes outils » alors que ``generer_image_svg`` figurait à son
    contrat — son assemblage ne branchait pas les tools documentaires. Ce test
    couvre les six agents : aucun tool déclaré ne peut manquer au montage.
    """
    import importlib

    cas: tuple[tuple[str, str], ...] = (
        ("app.agents.generaliste_readonly", "build_generaliste"),
        ("app.agents.generateur_offre", "build_generateur"),
        ("app.agents.agent_rh", "build_rh"),
        ("app.agents.assistant_formateur", "build_assistant"),
        ("app.agents.agent_statistique", "build_statistique"),
        ("app.agents.agent_personnel", "build_agent_personnel"),
    )
    def fabrique() -> Session:
        return session
    for nom_module, nom_builder in cas:
        module = importlib.import_module(nom_module)
        agent = getattr(module, nom_builder)(session, session_factory=fabrique)
        montes = {outil.name for outil in agent.deep_tools()}
        manquants = sorted(set(module.DEFINITION.tools) - montes)
        assert manquants == [], (module.DEFINITION.agent_id, manquants)


def test_outil_d_affichage_declare_pour_tous_les_agents_ui() -> None:
    from app.agents.frontend_tools_catalogue import OUTILS_PAR_NOM, TOUS_LES_AGENTS_UI

    outil = OUTILS_PAR_NOM["afficher_image"]
    assert outil.agents == TOUS_LES_AGENTS_UI
