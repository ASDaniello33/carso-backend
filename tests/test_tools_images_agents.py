"""Tests — lire une image, et la génération documentaire ouverte à tous.

Deux demandes, deux verrous :

1. **une image se voit** — ``lire_image`` renvoie un contenu multimodal (bloc
   ``image_url``), pas un JSON où l'image serait invisible. Le pont LangChain est
   donc testé, pas seulement le handler : un handler correct branché sur une
   sortie texte ne servirait à rien ;
2. **tous les agents** portent ``lire_image`` et
   ``generer_document_professionnel`` — y compris les agents de consultation,
   dont la génération reste une **proposition** (``document_propose``), jamais
   une modification de donnée métier.

Aucune base n'est nécessaire pour l'outil : le service documentaire est un
double (l'accès réel est déjà verrouillé par les tests de ``DocumentService``).
"""

from __future__ import annotations

import io
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from PIL import Image

from app.core.errors import ValidationError
from app.documents.images import COTE_MAX, preparer_image
from app.tools.base import to_langchain_tool
from app.tools.documents import build_document_tools
from app.tools.permissions import AgentIdentity, PermissionPolicy


def _image_png(cote: int = 64) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (cote, cote), (30, 90, 160)).save(buffer, format="PNG")
    return buffer.getvalue()


def _image_jpeg(cote: int = 64) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (cote, cote), (200, 120, 40)).save(buffer, format="JPEG")
    return buffer.getvalue()


# --- 1. Préparation de l'image ------------------------------------------------


def test_petite_image_png_reste_intacte(tmp_path: Path) -> None:
    chemin = tmp_path / "capture.png"
    octets = _image_png()
    chemin.write_bytes(octets)

    preparee = preparer_image(chemin, mime_type="image/png")

    assert preparee.mime_type == "image/png"
    assert preparee.transformee is False
    # Aucun réencodage : les octets d'origine sont la meilleure version.
    import base64

    assert base64.b64decode(preparee.base64) == octets
    assert (preparee.largeur, preparee.hauteur) == (64, 64)


def test_grande_image_est_reduite(tmp_path: Path) -> None:
    chemin = tmp_path / "photo.png"
    chemin.write_bytes(_image_png(COTE_MAX + 400))

    preparee = preparer_image(chemin)

    assert max(preparee.largeur, preparee.hauteur) == COTE_MAX
    assert preparee.transformee is True


def test_jpeg_reste_un_jpeg(tmp_path: Path) -> None:
    chemin = tmp_path / "photo.jpg"
    chemin.write_bytes(_image_jpeg())

    preparee = preparer_image(chemin, mime_type="image/jpeg")

    assert preparee.mime_type == "image/jpeg"
    assert preparee.transformee is False


def test_fichier_non_image_refuse(tmp_path: Path) -> None:
    chemin = tmp_path / "pas-une-image.png"
    chemin.write_bytes(b"%PDF-1.7 contenu qui n'est pas une image")

    with pytest.raises(ValidationError):
        preparer_image(chemin)


# --- 2. L'outil et son pont multimodal ----------------------------------------


class _ServiceDouble:
    """Service documentaire minimal : un document image déjà déposé."""

    def __init__(self, chemin: Path, mime_type: str) -> None:
        self._chemin = chemin
        self._document = SimpleNamespace(
            id=uuid4(),
            nom="capture.png",
            statut="proposed",
            type_document="non_classe",
            mime_type=mime_type,
        )

    def obtenir(self, document_id):
        return self._document

    def telecharger(self, document_id):
        return self._document.nom, self._chemin


def _outil_lire_image(chemin: Path, mime_type: str):
    service = _ServiceDouble(chemin, mime_type)
    tools = build_document_tools(
        identite=AgentIdentity(agent_id="agent_generaliste_readonly"),
        policy=PermissionPolicy(
            allowed_capabilities=frozenset({"document_read", "document_propose"})
        ),
        service_factory=lambda: service,
    )
    return next(tool for tool in tools if tool.name == "lire_image"), service


def test_lire_image_porte_l_image_au_modele(tmp_path: Path) -> None:
    chemin = tmp_path / "capture.png"
    chemin.write_bytes(_image_png())
    tool, service = _outil_lire_image(chemin, "image/png")

    sortie = tool.invoke({"document_id": str(service._document.id)})

    assert sortie["ok"] is True, sortie
    assert sortie["nom"] == "capture.png"
    assert sortie["images"][0]["mime"] == "image/png"
    assert sortie["images"][0]["base64"]

    # Le pont ne doit pas sérialiser l'image dans un JSON : le modèle doit la
    # recevoir comme contenu, sinon il ne la voit pas.
    contenu = to_langchain_tool(tool).invoke({"document_id": str(service._document.id)})
    assert isinstance(contenu, list), contenu
    types = [bloc["type"] for bloc in contenu]
    assert types[0] == "text"
    assert "image_url" in types
    url = next(b["image_url"]["url"] for b in contenu if b["type"] == "image_url")
    assert url.startswith("data:image/png;base64,")


def test_lire_image_refuse_un_document_qui_n_est_pas_une_image(tmp_path: Path) -> None:
    chemin = tmp_path / "facture.pdf"
    chemin.write_bytes(b"%PDF-1.7")
    tool, service = _outil_lire_image(chemin, "application/pdf")

    sortie = tool.invoke({"document_id": str(service._document.id)})

    assert sortie["ok"] is False
    assert "n'est pas une image" in sortie["error"]["message"]


# --- 3. Périmètre documentaire de tous les agents -----------------------------


def test_tous_les_agents_portent_la_generation_et_la_vision() -> None:
    from app.agents.agent_personnel import DEFINITION as PERSONNEL
    from app.agents.agent_rh import DEFINITION as RH
    from app.agents.agent_statistique import DEFINITION as STAT
    from app.agents.assistant_formateur import DEFINITION as ASSISTANT
    from app.agents.generaliste_readonly import DEFINITION as GENERALISTE
    from app.agents.generateur_offre import DEFINITION as GENERATEUR

    for definition in (GENERALISTE, GENERATEUR, RH, ASSISTANT, STAT, PERSONNEL):
        assert "lire_image" in definition.tools, definition.agent_id
        assert "generer_document_professionnel" in definition.tools, definition.agent_id
        # La génération dépose une proposition : la capacité doit suivre, sinon
        # le tool est refusé à l'appel (policy.require).
        assert "document_propose" in definition.capabilities, definition.agent_id
        assert "document_read" in definition.capabilities, definition.agent_id
