"""Extraction de texte **avec provenance** pour la base de connaissance.

Les extracteurs de ``app.documents.extraction`` renvoient un texte aplati : ils
servent la lecture d'un document métier, pas une citation vérifiable. Ici, un
segment porte sa page (PDF) ou sa section (titre DOCX, feuille XLSX) — c'est ce
qui permet à un agent d'écrire « (p. 42) » sans deviner.

Sémantique des échecs, identique à l'extraction documentaire : type non
supporté → ``ValidationError`` ; librairie absente → ``CarsoError`` ; contenu
illisible ou vide → ``ValidationError`` (l'OCR est hors périmètre : on ne
fabrique pas de texte).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.core.errors import CarsoError, ValidationError
from app.documents.paths import extension_of

#: Formats indexables. Le PDF, le DOCX et le XLSX sont lus avec leurs
#: librairies déjà déclarées par l'extra ``documents`` ; le texte brut ne
#: demande rien.
EXTENSIONS_CONNAISSANCE: frozenset[str] = frozenset(
    {"pdf", "docx", "xlsx", "txt", "md", "csv"}
)

#: Garde-fou de lecture d'une feuille de calcul volumineuse (lignes).
MAX_LIGNES_FEUILLE = 20_000


@dataclass(frozen=True, slots=True)
class Segment:
    """Bloc de texte homogène : une page, une section, une feuille."""

    texte: str
    page: int | None = None
    section: str | None = None


def _exiger(module: str) -> object:
    """Importe une librairie de lecture ou échoue explicitement."""
    import importlib

    try:
        return importlib.import_module(module)
    except ImportError as exc:
        msg = (
            f"Lecture de la base de connaissance indisponible : la librairie "
            f"'{module}' n'est pas installée (pip install -e '.[documents]')"
        )
        raise CarsoError(msg, details={"librairie": module}) from exc


def lire_segments(path: Path, *, nom_fichier: str) -> list[Segment]:
    """Extrait les segments d'un fichier selon son extension.

    Args:
        path: fichier physique, sous la racine gérée.
        nom_fichier: nom logique portant l'extension.

    Raises:
        ValidationError: extension non indexable, PDF chiffré, texte vide.
        CarsoError: librairie de lecture absente.
    """
    extension = extension_of(nom_fichier)
    if extension not in EXTENSIONS_CONNAISSANCE:
        msg = f"Format non indexable pour la base de connaissance : .{extension or '?'}"
        raise ValidationError(msg, details={"formats": sorted(EXTENSIONS_CONNAISSANCE)})

    if extension == "pdf":
        segments = _lire_pdf(path)
    elif extension == "docx":
        segments = _lire_docx(path)
    elif extension == "xlsx":
        segments = _lire_xlsx(path)
    else:
        segments = _lire_texte(path)

    utiles = [segment for segment in segments if segment.texte.strip()]
    if not utiles:
        msg = (
            "Aucun texte extractible de ce fichier. S'il s'agit d'un PDF scanné, "
            "l'OCR est hors périmètre : fournir une version texte."
        )
        raise ValidationError(msg, details={"nom_fichier": nom_fichier})
    return utiles


def _lire_pdf(path: Path) -> list[Segment]:
    module = _exiger("pypdf")
    lecteur_cls = module.PdfReader  # type: ignore[attr-defined]
    try:
        lecteur = lecteur_cls(str(path))
        if getattr(lecteur, "is_encrypted", False):
            raise ValidationError(
                "PDF chiffré : la base de connaissance n'indexe pas de contenu protégé",
                details={"nom_fichier": path.name},
            )
        pages: list[Segment] = []
        for numero, page in enumerate(lecteur.pages, start=1):
            texte = page.extract_text() or ""
            if texte.strip():
                pages.append(Segment(texte=texte, page=numero))
        return pages
    except ValidationError:
        raise
    except Exception as exc:  # noqa: BLE001 — un PDF corrompu doit être signalé, pas deviné
        msg = f"Lecture du PDF impossible : {type(exc).__name__}"
        raise ValidationError(msg, details={"nom_fichier": path.name}) from exc


def _lire_docx(path: Path) -> list[Segment]:
    module = _exiger("docx")
    try:
        document = module.Document(str(path))  # type: ignore[attr-defined]
    except Exception as exc:  # noqa: BLE001
        msg = f"Lecture du DOCX impossible : {type(exc).__name__}"
        raise ValidationError(msg, details={"nom_fichier": path.name}) from exc

    segments: list[Segment] = []
    section: str | None = None
    tampon: list[str] = []

    def _vider() -> None:
        if tampon:
            segments.append(Segment(texte="\n\n".join(tampon), section=section))
            tampon.clear()

    for paragraphe in document.paragraphs:
        texte = (paragraphe.text or "").strip()
        style = (getattr(getattr(paragraphe, "style", None), "name", "") or "").lower()
        est_titre = style.startswith("heading") or style.startswith("titre")
        if est_titre and texte:
            _vider()
            section = texte[:255]
            continue
        if texte:
            tampon.append(texte)
    _vider()
    return segments


def _lire_xlsx(path: Path) -> list[Segment]:
    module = _exiger("openpyxl")
    try:
        classeur = module.load_workbook(  # type: ignore[attr-defined]
            filename=str(path), read_only=True, data_only=True
        )
    except Exception as exc:  # noqa: BLE001
        msg = f"Lecture du classeur impossible : {type(exc).__name__}"
        raise ValidationError(msg, details={"nom_fichier": path.name}) from exc

    segments: list[Segment] = []
    try:
        for feuille in classeur.worksheets:
            lignes: list[str] = []
            for index, ligne in enumerate(feuille.iter_rows(values_only=True), start=1):
                if index > MAX_LIGNES_FEUILLE:
                    break
                cellules = [
                    " ".join(str(valeur).split())
                    for valeur in ligne
                    if valeur is not None and str(valeur).strip()
                ]
                if cellules:
                    lignes.append(" | ".join(cellules))
            if lignes:
                segments.append(Segment(texte="\n".join(lignes), section=feuille.title))
    finally:
        classeur.close()
    return segments


def _lire_texte(path: Path) -> list[Segment]:
    brut = path.read_bytes()
    for encodage in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return [Segment(texte=brut.decode(encodage))]
        except UnicodeDecodeError:
            continue
    raise ValidationError(
        "Encodage du fichier texte non reconnu",
        details={"nom_fichier": path.name},
    )


__all__ = ["EXTENSIONS_CONNAISSANCE", "MAX_LIGNES_FEUILLE", "Segment", "lire_segments"]
