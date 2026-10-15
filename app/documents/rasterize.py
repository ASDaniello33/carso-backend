"""Rasterisation PDF → images de page (ADR 0005, Phase 9).

Un agent peut **inspecter le rendu réel** d'un document : le PDF de rendu est
converti page par page en images PNG, que l'utilisateur voit dans le fil de
conversation et que l'agent peut analyser s'il dispose de la vision.

Choix techniques :

- **pdftoppm (poppler)** est l'outil de rasterisation : aucune dépendance
  Python suplémentaire, et un rendu fidèle au PDF réellement produit. Il est
  détecté dans le ``PATH`` ou désigné par configuration ; **son absence est une
  erreur explicite** (``GenerationError``), jamais un aperçu vide silencieux.
- **Aucune dépendance pour lire les dimensions** : les dimensions PNG sont lues
  dans l'en-tête IHDR (octets 16..24), pas besoin de Pillow.
- **Le nombre de pages est borné** (``max_pages``) : un document de 300 pages
  n'immobilise pas le serveur, et le résultat dit explicitement qu'il est
  tronqué.
"""

from __future__ import annotations

import importlib
import shutil
import struct
import subprocess
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.core.errors import GenerationError

__all__ = [
    "PageRasterisee",
    "outil_pdftoppm",
    "pages_pdf",
    "rasteriser_pdf",
]

#: Délai maximal d'une conversion PDF → images (secondes).
_TIMEOUT_SECONDES = 120

#: Signature PNG : les images produites sont vérifiées avant d'être publiées.
_SIGNATURE_PNG = b"\x89PNG\r\n\x1a\n"


@dataclass(frozen=True, slots=True)
class PageRasterisee:
    """Une page du PDF convertie en image."""

    numero: int
    octets: bytes
    largeur_px: int
    hauteur_px: int


def outil_pdftoppm(configure: str | None = None) -> str:
    """Chemin de l'exécutable ``pdftoppm``, ou erreur explicite.

    Args:
        configure: chemin fourni par la configuration (``pdftoppm_path``).

    Raises:
        GenerationError: poppler absent (ni configuré, ni dans le ``PATH``).
    """
    if configure and configure.strip():
        chemin = Path(configure.strip())
        if chemin.is_file():
            return str(chemin)
        msg = (
            f"L'outil de rasterisation configuré est introuvable : {configure!r} "
            "(vérifier PDFTOPPM_PATH)"
        )
        raise GenerationError(msg, details={"outil": "pdftoppm", "chemin": configure})

    trouve = shutil.which("pdftoppm")
    if trouve is None:
        msg = (
            "Aperçu visuel indisponible : l'outil de rasterisation PDF (pdftoppm, "
            "paquet poppler) n'est pas installé. Le document et sa validation "
            "restent disponibles ; seul l'aperçu image est refusé."
        )
        raise GenerationError(
            msg,
            details={"outil": "pdftoppm", "remonter": "installer poppler-utils"},
        )
    return trouve


def pages_pdf(source: bytes | Path) -> int | None:
    """Nombre de pages d'un PDF, ou ``None`` si indéterminable.

    Le comptage est **facultatif** : il borne le travail de rasterisation. S'il
    échoue (pypdf absent, PDF atypique), la conversion a lieu sans borne haute et
    l'appelant coupe lui-même au plafond de pages.
    """
    pypdf = _module_pypdf()
    if pypdf is None:
        return None
    if isinstance(source, Path):
        try:
            return len(pypdf.PdfReader(str(source)).pages)
        except Exception:  # pragma: no cover - PDF atypique
            return None
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as temporaire:
        temporaire.write(source)
        chemin = Path(temporaire.name)
    try:
        return pages_pdf(chemin)
    finally:
        chemin.unlink(missing_ok=True)


def rasteriser_pdf(
    source: bytes | Path,
    *,
    dpi: int = 110,
    pages: Sequence[int] | None = None,
    max_pages: int = 40,
    outil: str | None = None,
) -> list[PageRasterisee]:
    """Convertit un PDF en images PNG, une par page.

    Args:
        source: octets du PDF (rendu à la volée) ou chemin d'un PDF existant.
        dpi: résolution des images.
        pages: numéros de pages à rendre (1-based) ; ``None`` = toutes les
            premières pages jusqu'au plafond.
        max_pages: nombre maximal de pages rendues.
        outil: chemin de ``pdftoppm`` (détecté si absent).

    Returns:
        Les pages effectivement rendues, dans l'ordre du document.

    Raises:
        GenerationError: outil absent, PDF illisible, ou échec de conversion.
    """
    executable = outil_pdftoppm(outil)
    with tempfile.TemporaryDirectory(prefix=f"carso-apercu-{uuid4().hex}") as travail:
        dossier = Path(travail)
        cible = _materialiser(source, dossier)
        prefixe = dossier / "page"
        commande = [executable, "-png", "-r", str(int(dpi))]
        premier, dernier = _bornes(pages)
        if premier is not None and dernier is not None:
            commande += ["-f", str(premier), "-l", str(dernier)]
        commande += [str(cible), str(prefixe)]
        _lancer(commande, dossier)

        images = sorted(dossier.glob("page-*.png"), key=_numero_du_fichier)
        resultat = [page for page in (_lire_page(chemin) for chemin in images) if page]
        if not resultat:
            msg = "La conversion PDF → images n'a produit aucune page"
            raise GenerationError(msg, details={"outil": "pdftoppm"})
        return resultat[:max_pages]


def _module_pypdf() -> Any | None:
    """Module ``pypdf`` s'il est disponible (import paresseux, usage facultatif)."""
    try:
        return importlib.import_module("pypdf")
    except ImportError:
        return None


def _materialiser(source: bytes | Path, dossier: Path) -> Path:
    """Écrit le PDF dans le dossier de travail (ou valide le chemin fourni)."""
    if isinstance(source, Path):
        if not source.is_file():
            msg = f"PDF introuvable pour l'aperçu : {source.name}"
            raise GenerationError(msg, details={"fichier": source.name})
        return source
    chemin = dossier / "source.pdf"
    chemin.write_bytes(source)
    return chemin


def _bornes(pages: Sequence[int] | None) -> tuple[int | None, int | None]:
    """Première et dernière page demandées (``pdftoppm`` travaille par intervalle)."""
    if not pages:
        return None, None
    propres = sorted({int(page) for page in pages if int(page) >= 1})
    if not propres:
        return None, None
    return propres[0], propres[-1]


def _lancer(commande: list[str], dossier: Path) -> None:
    """Exécute ``pdftoppm`` et traduit ses échecs en erreur métier explicite."""
    try:
        resultat = subprocess.run(  # noqa: S603 - commande construite, sans shell
            commande,
            cwd=dossier,
            capture_output=True,
            timeout=_TIMEOUT_SECONDES,
            check=False,
        )
    except FileNotFoundError as exc:  # pragma: no cover - dépend de l'hôte
        msg = "L'outil de rasterisation PDF est introuvable à l'exécution"
        raise GenerationError(msg, details={"outil": "pdftoppm"}) from exc
    except subprocess.TimeoutExpired as exc:
        msg = f"Conversion PDF → images trop longue (>{_TIMEOUT_SECONDES} s)"
        raise GenerationError(msg, details={"outil": "pdftoppm"}) from exc

    if resultat.returncode != 0:
        detail = (resultat.stderr or b"").decode("utf-8", "replace").strip()
        msg = "PDF illisible : la conversion en images a échoué (fichier corrompu ?)"
        raise GenerationError(
            msg,
            details={"outil": "pdftoppm", "code": resultat.returncode, "detail": detail[:300]},
        )


def _numero_du_fichier(chemin: Path) -> int:
    """Numéro de page porté par ``page-<n>.png`` (0 si illisible)."""
    try:
        return int(chemin.stem.rsplit("-", 1)[-1])
    except (ValueError, IndexError):  # pragma: no cover - nom inattendu
        return 0


def _lire_page(chemin: Path) -> PageRasterisee | None:
    """Lit une image produite ; une page illisible n'interrompt pas l'aperçu."""
    octets = chemin.read_bytes()
    if not octets.startswith(_SIGNATURE_PNG):
        return None
    largeur, hauteur = _dimensions_png(octets)
    return PageRasterisee(
        numero=_numero_du_fichier(chemin),
        octets=octets,
        largeur_px=largeur,
        hauteur_px=hauteur,
    )


def _dimensions_png(octets: bytes) -> tuple[int, int]:
    """Dimensions d'un PNG, lues dans l'en-tête IHDR (aucune dépendance)."""
    if len(octets) < 24 or octets[12:16] != b"IHDR":
        return 0, 0
    largeur, hauteur = struct.unpack(">II", octets[16:24])
    return int(largeur), int(hauteur)
