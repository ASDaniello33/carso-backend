"""Tests — image inline fournie en base64 (INC-133).

L'agent ne peut pas déposer de fichier : il fournit donc une image **dans** le
spec. Deux exigences : le format se lit dans les octets (jamais dans un nom
déclaré) et l'erreur arrive à la construction du spec, pas au rendu.
"""

from __future__ import annotations

import base64
import io
import zipfile

import pytest
from pydantic import ValidationError as PydanticValidationError

from app.documents.assets import resoudre_image
from app.documents.professionnel.docx_renderer import rendre_docx as rendre_docx_pro
from app.documents.professionnel.html_builder import preparer
from app.documents.renderers.docx import rendre_docx
from app.documents.renderers.html import rendre_html
from app.documents.renderers.pdf import rendre_pdf
from app.documents.spec import DocumentSpec, Image, format_image

PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGA"
    "hKmMIQAAAABJRU5ErkJggg=="
)
PNG = base64.b64decode(PNG_B64)


def _spec(base64_image: str = PNG_B64, **options: object) -> DocumentSpec:
    return DocumentSpec.model_validate(
        {
            "metadata": {"titre": "Image inline", "type_document": "note"},
            "blocs": [
                {"type": "paragraphe", "texte": "Avant."},
                {
                    "type": "image",
                    "base64": base64_image,
                    "largeur_mm": 40,
                    "legende": "Capture",
                    "alt": "Étapes du processus",
                    **options,
                },
            ],
        }
    )


class TestFormatEtValidation:
    """Le format vient des octets ; une erreur ne survit pas à la construction."""

    def test_signatures(self) -> None:
        assert format_image(PNG) == "png"
        assert format_image(b"\xff\xd8\xff\xe0" + b"0" * 8) == "jpg"
        assert format_image(b"GIF89a" + b"0" * 8) == "gif"
        assert format_image(b"RIFF" + b"\x00" * 4 + b"WEBP" + b"0" * 4) == "webp"
        assert format_image(b"pas une image") is None

    def test_prefixe_data_retire(self) -> None:
        image = Image(base64="data:image/png;base64," + PNG_B64)
        assert "data:" not in (image.base64 or "")

    def test_espaces_toleres(self) -> None:
        coupe = PNG_B64[:20] + "\n" + PNG_B64[20:]
        assert resoudre_image(Image(base64=coupe), None) == (PNG, "image.png")

    def test_base64_invalide(self) -> None:
        with pytest.raises(PydanticValidationError, match="base64"):
            Image(base64="blablabla")

    def test_contenu_non_image(self) -> None:
        texte = base64.b64encode(b"pas une image").decode()
        with pytest.raises(PydanticValidationError, match="non reconnue"):
            Image(base64=texte)

    def test_trop_lourde(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("app.documents.spec.OCTETS_MAX", 4)
        with pytest.raises(PydanticValidationError, match="trop lourde"):
            Image(base64=PNG_B64)

    def test_deux_sources_refusees(self) -> None:
        with pytest.raises(PydanticValidationError, match="une seule source"):
            Image(base64=PNG_B64, chemin="missions/1/logo.png")


class TestRendus:
    """Une image base64 se rend sans fournisseur d'images ni fichier disque."""

    def test_resolution_sans_fournisseur(self) -> None:
        assert resoudre_image(Image(base64=PNG_B64), None) == (PNG, "image.png")

    def test_html_data_uri(self) -> None:
        html = rendre_html(_spec()).decode("utf-8")
        assert "data:image/png;base64," in html
        assert "Capture" in html

    def test_docx_media(self) -> None:
        docx = rendre_docx(_spec())
        with zipfile.ZipFile(io.BytesIO(docx)) as archive:
            medias = [n for n in archive.namelist() if n.startswith("word/media/")]
        assert medias == ["word/media/image1.png"]

    def test_pdf(self) -> None:
        assert rendre_pdf(_spec()).startswith(b"%PDF-")

    def test_document_professionnel(self, tmp_path) -> None:
        corps = (
            "<h1>Essai</h1><p>Texte.</p>"
            f"<img src='data:image/png;base64,{PNG_B64}' alt='Capture'>"
        )
        chemin = rendre_docx_pro(preparer(corps), tmp_path / "essai.docx")
        with zipfile.ZipFile(chemin) as archive:
            medias = [n for n in archive.namelist() if n.startswith("word/media/")]
        assert medias
