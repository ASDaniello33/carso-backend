"""Phase 4 (instruction/11) — sécurité des chemins, validation et stockage.

Tests **purs** (aucune base de données) : sanitisation des noms, confinement des
chemins, allowlist d'extensions, signature binaire, écriture atomique, checksum et
refus d'écrasement. Ce fichier est le garde-fou des contrôles de ``instruction/08 §4``.
"""

from __future__ import annotations

import hashlib
import io
import zipfile
from pathlib import Path
from uuid import uuid4

import pytest

from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.documents import (
    LocalDocumentStorage,
    build_logical_path,
    resolve_within_root,
    sanitize_filename,
    versioned_filename,
)
from app.documents.paths import directory_of
from app.documents.storage import UploadTooLargeError
from app.documents.validation import inspect_file

# --- Sanitisation des noms ------------------------------------------------------


def test_nom_valide_conserve() -> None:
    assert sanitize_filename("Fiche technique.pdf") == "Fiche technique.pdf"
    assert sanitize_filename("Rapport n°1 (final).pdf") == "Rapport n°1 (final).pdf"
    assert sanitize_filename("CV_Haddad-Omar.docx") == "CV_Haddad-Omar.docx"


def test_nom_avec_espaces_de_bord_normalise() -> None:
    assert sanitize_filename("  offre.pdf ") == "offre.pdf"


@pytest.mark.parametrize(
    "nom",
    [
        "../../etc/passwd",
        "..\\..\\windows\\system32\\config.pdf",
        "/etc/passwd",
        "C:\\Users\\evil.pdf",
        "dossier/sous.pdf",
        "annuaire\\sous.pdf",
        "..",
        ".",
        "x.pdf.",
        "fichier\x00.pdf",
        "Panneau?de.pdf",
        "Etoile*.pdf",
        "",
        "   ",
        "sans_extension",
        ".pdf",
    ],
)
def test_noms_dangereux_ou_invalides_refuses(nom: str) -> None:
    with pytest.raises(ValidationError):
        sanitize_filename(nom)


@pytest.mark.parametrize("nom", ["CON.pdf", "nul.txt", "COM1.docx", "lpt9.pdf"])
def test_noms_reserves_windows_refuses(nom: str) -> None:
    with pytest.raises(ValidationError):
        sanitize_filename(nom)


def test_nom_trop_long_refuse() -> None:
    with pytest.raises(ValidationError):
        sanitize_filename("a" * 250 + ".pdf")


def test_nom_avec_caractere_de_controle_refuse() -> None:
    with pytest.raises(ValidationError):
        sanitize_filename("fichier\x01.pdf")


# --- Chemin logique -------------------------------------------------------------


def test_chemin_logique_construit_depuis_segments_controles() -> None:
    identifiant = uuid4()
    assert (
        build_logical_path("missions", identifiant, "Fiche technique.pdf")
        == f"missions/{identifiant}/Fiche technique.pdf"
    )
    assert directory_of("offres_formation", identifiant) == f"offres_formation/{identifiant}"
    assert (
        build_logical_path("generated", identifiant, "rapport.docx")
        == f"generated/{identifiant}/rapport.docx"
    )


@pytest.mark.parametrize("scope", ["", "..", "inconnu", "missions/../..", "MISSIONS"])
def test_scope_inconnu_refuse(scope: str) -> None:
    with pytest.raises(ValidationError):
        build_logical_path(scope, uuid4(), "fichier.pdf")
    with pytest.raises(ValidationError):
        directory_of(scope, uuid4())


# --- Résolution confinée --------------------------------------------------------


def test_resolution_conserve_le_chemin_sous_la_racine(tmp_path: Path) -> None:
    identifiant = uuid4()
    racine = tmp_path / "carso"
    chemin = resolve_within_root(
        racine, build_logical_path("missions", identifiant, "fiche.pdf")
    )

    assert chemin.is_relative_to(racine.resolve())
    assert chemin.name == "fiche.pdf"


@pytest.mark.parametrize(
    "chemin",
    [
        "missions/../../etc/passwd",
        "/etc/passwd",
        "inconnu/1234/fichier.pdf",
        "missions",
        "missions/1234",
        "missions\\evil.pdf",
        "missions/1234/../../evil.pdf",
        "",
    ],
)
def test_chemins_hors_racine_refuses(tmp_path: Path, chemin: str) -> None:
    with pytest.raises(ValidationError):
        resolve_within_root(tmp_path, chemin)


def test_lien_symbolique_sortant_refuse(tmp_path: Path) -> None:
    """Un lien créé sous la racine ne doit pas permettre d'en sortir."""
    racine = tmp_path / "carso"
    exterieur = tmp_path / "exterieur"
    racine.mkdir()
    exterieur.mkdir()
    (exterieur / "secret.pdf").write_bytes(b"%PDF-1.4 secret")

    lien = racine / "missions"
    try:
        lien.symlink_to(exterieur, target_is_directory=True)
    except (OSError, NotImplementedError):  # pragma: no cover - droits Windows
        pytest.skip("création de lien symbolique non autorisée sur ce système")

    with pytest.raises(ValidationError):
        resolve_within_root(racine, "missions/1234/secret.pdf")


# --- Stockage -------------------------------------------------------------------


@pytest.fixture
def storage(tmp_path: Path) -> LocalDocumentStorage:
    return LocalDocumentStorage(tmp_path / "carso", max_bytes=4096)


def test_save_calcule_checksum_taille_et_mime(storage: LocalDocumentStorage) -> None:
    contenu = b"%PDF-1.4 contenu de test"
    chemin = build_logical_path("missions", uuid4(), "fiche.pdf")

    stocke = storage.save(chemin, io.BytesIO(contenu))

    assert stocke.checksum_sha256 == hashlib.sha256(contenu).hexdigest()
    assert stocke.taille_octets == len(contenu)
    assert stocke.mime_type == "application/pdf"
    assert stocke.absolute_path.is_relative_to(storage.root.resolve())
    assert storage.read(chemin) == contenu


def test_save_refuse_l_ecrasement(storage: LocalDocumentStorage) -> None:
    chemin = build_logical_path("missions", uuid4(), "fiche.pdf")
    storage.save(chemin, io.BytesIO(b"%PDF-1.4 premier"))

    with pytest.raises(ConflictError):
        storage.save(chemin, io.BytesIO(b"%PDF-1.4 second"))


def test_save_ne_laisse_aucun_fichier_temporaire(storage: LocalDocumentStorage) -> None:
    chemin = build_logical_path("missions", uuid4(), "fiche.pdf")
    stocke = storage.save(chemin, io.BytesIO(b"%PDF-1.4 ok"))

    restes = list(storage.root.rglob(".part-*"))
    assert restes == []
    assert stocke.absolute_path.read_bytes() == b"%PDF-1.4 ok"


def test_save_nettoie_le_temporaire_si_contenu_invalide(storage: LocalDocumentStorage) -> None:
    """Un contenu qui n'est pas un PDF ne doit rien laisser derrière lui."""
    chemin = build_logical_path("missions", uuid4(), "faux.pdf")

    with pytest.raises(ValidationError):
        storage.save(chemin, io.BytesIO(b"ceci n'est pas un pdf"))

    assert list(storage.root.rglob(".part-*")) == []
    assert list(storage.root.rglob("*.pdf")) == []


def test_save_nettoie_le_temporaire_si_fichier_trop_gros(
    tmp_path: Path,
) -> None:
    petit = LocalDocumentStorage(tmp_path / "carso", max_bytes=16)
    chemin = build_logical_path("missions", uuid4(), "gros.pdf")

    with pytest.raises(UploadTooLargeError):
        petit.save(chemin, io.BytesIO(b"%PDF-1.4" + b"x" * 100))

    assert list(petit.root.rglob(".part-*")) == []
    assert list(petit.root.rglob("*.pdf")) == []


def test_read_fichier_absent_leve_not_found(storage: LocalDocumentStorage) -> None:
    chemin = build_logical_path("missions", uuid4(), "absent.pdf")
    with pytest.raises(NotFoundError):
        storage.read(chemin)


def test_remove_usage_interne(storage: LocalDocumentStorage) -> None:
    chemin = build_logical_path("missions", uuid4(), "fiche.pdf")
    storage.save(chemin, io.BytesIO(b"%PDF-1.4 ok"))

    storage.remove(chemin)

    assert storage.exists(chemin) is False


# --- Validation de contenu ------------------------------------------------------


def _zip(entrees: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for nom, contenu in entrees.items():
            archive.writestr(nom, contenu)
    return buffer.getvalue()


def test_extension_non_autorisee_refusee(tmp_path: Path) -> None:
    script = tmp_path / "executable.exe"
    script.write_bytes(b"MZ\x90\x00")

    with pytest.raises(ValidationError) as erreur:
        inspect_file(script, nom_fichier="executable.exe")

    assert "extensions_autorisees" in erreur.value.details


def test_signature_incoherente_refusee(tmp_path: Path) -> None:
    """Une extension trompeuse (.pdf) ne suffit pas : la signature fait foi."""
    faux = tmp_path / "faux.pdf"
    faux.write_bytes(b"PK\x03\x04 pas un pdf")

    with pytest.raises(ValidationError):
        inspect_file(faux, nom_fichier="faux.pdf")


def test_docx_renomme_en_xlsx_refuse(tmp_path: Path) -> None:
    """Un vrai ZIP, mais pas un classeur : les parties OOXML sont vérifiées."""
    trompeur = tmp_path / "trompeur.xlsx"
    trompeur.write_bytes(_zip({"word/document.xml": b"<w:document/>"}))

    with pytest.raises(ValidationError):
        inspect_file(trompeur, nom_fichier="trompeur.xlsx")


def test_xlsx_valide_accepte(tmp_path: Path) -> None:
    valide = tmp_path / "valide.xlsx"
    valide.write_bytes(_zip({"xl/workbook.xml": b"<workbook/>"}))

    regle = inspect_file(valide, nom_fichier="valide.xlsx")

    assert regle.extension == "xlsx"
    assert regle.mime_type.endswith("spreadsheetml.sheet")


def test_texte_avec_octets_nuls_refuse(tmp_path: Path) -> None:
    binaire = tmp_path / "binaire.csv"
    binaire.write_bytes(b"colonne1,colonne2\x00")

    with pytest.raises(ValidationError):
        inspect_file(binaire, nom_fichier="binaire.csv")


def test_csv_cp1252_accepte(tmp_path: Path) -> None:
    export = tmp_path / "beneficiaires.csv"
    export.write_bytes("Nom,Prénom\nDiallo,Awa\nTémoin,François".encode("cp1252"))

    regle = inspect_file(export, nom_fichier="beneficiaires.csv")

    assert regle.is_text is True


# --- Nommage des versions -------------------------------------------------------


def test_nom_de_version_ajoute_le_numero() -> None:
    assert versioned_filename("offre.pdf", 1) == "offre.pdf"
    assert versioned_filename("offre.pdf", 2) == "offre_v2.pdf"
    assert versioned_filename("offre v1.pdf", 10) == "offre v1_v10.pdf"


def test_nom_de_version_borne_la_longueur() -> None:
    long = "a" * 190 + ".pdf"
    resultat = versioned_filename(long, 12)

    assert resultat.endswith("_v12.pdf")
    assert len(resultat) <= 200
