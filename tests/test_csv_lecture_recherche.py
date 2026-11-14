"""Lecture et recherche dans un CSV (INC-168).

Ce que ces tests verrouillent — la raison d'être du module
``app.documents.csv_lecture`` :

1. **un CSV se lit par colonnes**, pas comme un bloc de texte : noms d'en-tête,
   lignes en dictionnaires, séparateur et encodage retenus ;
2. **une ligne se cite** : son repère est son numéro de ligne dans le fichier
   (en-tête = ligne 1), et ``relire`` rend la même ligne à l'identique ;
3. **une valeur se cherche** : ``chercher_dans_document`` fouille le CSV,
   ``colonne`` restreint la fouille, et une colonne inconnue est **refusée avec
   la liste réelle** (sinon zéro résultat se lirait « la donnée n'existe pas ») ;
4. **une plage se lit** : ``read_document_range`` rend des lignes de CSV ;
5. **les défauts de forme sont dits** : une ligne de largeur inégale (séparateur
   mal détecté) apparaît dans ``problemes`` ;
6. **les options CSV ne sont jamais des no-op silencieux** : les passer sur un
   autre format est refusé.

SQLite mémoire + stockage local temporaire (mêmes fixtures que
``test_tools_documents.py``).
"""

from __future__ import annotations

import io
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.application.dto import DocumentUploadInput
from app.application.perimetre import Perimetre
from app.application.services import DocumentService
from app.documents.csv_lecture import (
    lire_csv,
    lire_csv_plage,
    lire_element_csv,
    structure_csv,
)
from app.documents.recherche import chercher_dans_csv
from app.documents.storage import LocalDocumentStorage
from app.domain.base import Base
from app.tools.documents import build_document_tools
from app.tools.permissions import AgentIdentity, PermissionPolicy

IDENTITE = AgentIdentity(agent_id="agent_generaliste")
POLICY = PermissionPolicy(allowed_capabilities=frozenset({"document_read"}))

#: Export « Excel français » : séparateur ``;``, accents en cp1252, et une ligne
#: de largeur inégale (le symptôme réel d'un séparateur mal détecté).
CSV_FR = (
    "Nom;Bénéficiaires;Zone\r\n"
    "Atelier A;12;Toliara\r\n"
    "Atelier B;8;Tuléar\r\n"
    "Atelier C;5\r\n"
    "Atelier D;20;Toliara\r\n"
)


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as active:
        yield active
    engine.dispose()


@pytest.fixture
def service(session: Session, tmp_path: Path) -> DocumentService:
    storage = LocalDocumentStorage(tmp_path / "carso", max_bytes=2 * 1024 * 1024)
    return DocumentService(
        session,
        storage=storage,
        actor_id="test",
        perimetre=Perimetre(role="administrateur", utilisateur_id=uuid4(), est_complet=True),
    )


def _deposer(service: DocumentService, nom: str, contenu: bytes) -> Any:
    return service.enregistrer_document(
        DocumentUploadInput(
            type_document="autre",
            nom=nom,
            stream=io.BytesIO(contenu),
            created_by="test",
        )
    )


@pytest.fixture
def csv_document(service: DocumentService) -> Any:
    return _deposer(service, "ateliers.csv", CSV_FR.encode("cp1252"))


def _outils(service: DocumentService) -> dict[str, Any]:
    construits = build_document_tools(
        identite=IDENTITE, policy=POLICY, service_factory=lambda: service
    )
    return {outil.name: outil for outil in construits}


# --- 1. Lecture par colonnes --------------------------------------------------


def test_lire_document_rend_les_colonnes_et_les_lignes(
    service: DocumentService, csv_document: Any
) -> None:
    """Le CSV arrive en colonnes nommées, pas en 4 000 caractères de ``;``."""
    sortie = _outils(service)["lire_document"].invoke({"document_id": str(csv_document.id)})

    assert sortie["ok"] is True
    assert sortie["separateur"] == ";"
    assert sortie["encodage"] == "cp1252"
    assert sortie["colonnes"] == ["Nom", "Bénéficiaires", "Zone"]
    assert sortie["nb_colonnes"] == 3
    # En-tête comprise : c'est le numéro de ligne du fichier.
    assert sortie["nb_lignes"] == 5
    assert sortie["nb_donnees"] == 4
    # Aucun bloc de texte brut : la lecture est structurée.
    assert sortie["apercu_texte"] is None

    premiere = sortie["apercu_lignes"][0]
    assert premiere["ligne"] == 2
    assert premiere["valeurs"] == {"Nom": "Atelier A", "Bénéficiaires": "12", "Zone": "Toliara"}


def test_ligne_de_largeur_inegale_est_signalee(
    service: DocumentService, csv_document: Any
) -> None:
    """Un défaut de forme se dit : il indique un séparateur mal détecté."""
    sortie = _outils(service)["lire_document"].invoke({"document_id": str(csv_document.id)})

    assert any("ligne 4" in probleme for probleme in sortie["problemes"])

    # La ligne courte ne décale pas les valeurs : ``Zone`` reste vide.
    ligne_courte = next(
        entree for entree in sortie["apercu_lignes"] if entree["ligne"] == 4
    )
    assert ligne_courte["valeurs"] == {
        "Nom": "Atelier C",
        "Bénéficiaires": "5",
        "Zone": "",
    }


# --- 2. Une ligne se cite -----------------------------------------------------


def test_chercher_puis_relire_la_meme_ligne(
    service: DocumentService, csv_document: Any
) -> None:
    """``hit['relire']`` se recopie tel quel et rend la ligne trouvée."""
    outils = _outils(service)
    recherche = outils["chercher_dans_document"].invoke(
        {"document_id": str(csv_document.id), "mot_cle": "Toliara"}
    )

    assert recherche["ok"] is True
    assert recherche["nb_resultats"] == 2
    hit = recherche["resultats"][0]
    assert hit["page"] == 2
    assert hit["type_bloc"] == "ligne"
    assert hit["colonnes_correspondantes"] == ["Zone"]
    assert hit["valeurs"]["Nom"] == "Atelier A"
    assert hit["relire"]["tool"] == "lire_element_document"

    relu = outils["lire_element_document"].invoke(hit["relire"]["arguments"])

    assert relu["ok"] is True
    assert relu["format"] == "csv"
    assert relu["index"] == 2
    assert relu["valeurs"] == hit["valeurs"]
    assert relu["est_entete"] is False


# --- 3. Une valeur se cherche, dans une colonne ou partout -------------------


def test_colonne_restreint_la_recherche(
    service: DocumentService, csv_document: Any
) -> None:
    """``colonne`` accepte le nom d'en-tête sans accent et sans casse."""
    recherche = _outils(service)["chercher_dans_document"].invoke(
        {
            "document_id": str(csv_document.id),
            "mot_cle": "Atelier",
            "colonne": "nom",
        }
    )

    assert recherche["ok"] is True
    assert recherche["colonne"] == "Nom"
    assert recherche["nb_resultats"] == 4
    # « Atelier » ne vit que dans la colonne Nom.
    assert all(hit["colonnes_correspondantes"] == ["Nom"] for hit in recherche["resultats"])


def test_colonne_inconnue_est_refusee_avec_la_liste(
    service: DocumentService, csv_document: Any
) -> None:
    """Une colonne mal nommée est un refus explicite, jamais zéro résultat."""
    refus = _outils(service)["chercher_dans_document"].invoke(
        {
            "document_id": str(csv_document.id),
            "mot_cle": "Atelier",
            "colonne": "Montant",
        }
    )

    assert refus["ok"] is False
    assert "Colonne inconnue" in refus["error"]["message"]
    assert refus["error"]["details"]["colonnes"] == ["Nom", "Bénéficiaires", "Zone"]


# --- 4. Une plage de lignes ---------------------------------------------------


def test_read_document_range_lit_des_lignes_de_csv(
    service: DocumentService, csv_document: Any
) -> None:
    plage = _outils(service)["read_document_range"].invoke(
        {"document_id": str(csv_document.id), "from_page": 3, "to_page": 4}
    )

    assert plage["ok"] is True
    assert plage["unite"] == "lignes"
    assert [entree["ligne"] for entree in plage["lignes"]] == [3, 4]
    assert plage["lignes"][0]["valeurs"]["Nom"] == "Atelier B"
    assert plage["tronque"] is True  # reste la ligne 5


# --- 5. Un type d'élément qui n'existe pas dans un CSV -----------------------


def test_type_feuille_refuse_sur_un_csv(
    service: DocumentService, csv_document: Any
) -> None:
    """Un CSV n'a qu'une table : le refus dit les types valides."""
    refus = _outils(service)["lire_element_document"].invoke(
        {"document_id": str(csv_document.id), "type_element": "feuille"}
    )

    assert refus["ok"] is False
    assert refus["error"]["details"]["types_elements"] == ["element", "ligne"]


# --- 6. Les options CSV ne sont pas des no-op silencieux ---------------------


def test_option_csv_refusee_sur_un_autre_format(service: DocumentService) -> None:
    """``separateur`` sur un TXT : refusé, pas ignoré en silence."""
    document = _deposer(service, "notes.txt", b"Un texte sans colonnes.\n")

    refus = _outils(service)["lire_document"].invoke(
        {"document_id": str(document.id), "separateur": ";"}
    )

    assert refus["ok"] is False
    assert "ne s'appliquent qu'à un CSV" in refus["error"]["message"]
    assert refus["error"]["details"]["format"] == "txt"


# --- 7. Le cœur du lecteur, sans passer par un tool -------------------------


def test_champ_entre_guillemets_garde_son_saut_de_ligne(tmp_path: Path) -> None:
    """Un champ multiligne reste **une** ligne CSV (sinon les repères glissent)."""
    chemin = tmp_path / "notes.csv"
    chemin.write_text(
        'Nom;Commentaire\nA;"Première ligne\nDeuxième ligne"\nB;Simple\n',
        encoding="utf-8",
    )

    table = lire_csv(chemin)

    assert table["nb_lignes"] == 3
    assert table["apercu_lignes"][0]["valeurs"]["Commentaire"] == (
        "Première ligne Deuxième ligne"
    )
    assert lire_element_csv(chemin, type_element="ligne", index=3)["valeurs"]["Nom"] == "B"


# --- 8. Un export volumineux se lit en flux (constat du 13/11) ----------------


@pytest.fixture(scope="module")
def grand_csv(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """CSV de plus de 8 Mio, marqueur sur la dernière ligne.

    C'est la taille qui était refusée en bloc avant ce correctif : l'agent
    statistique ne pouvait ni l'apercevoir, ni le fouiller.
    """
    chemin = tmp_path_factory.mktemp("grand-csv") / "export.csv"
    with chemin.open("w", encoding="utf-8", newline="") as flux:
        flux.write("id;nom;montant;zone\n")
        for numero in range(250_000):
            nom = "MARQUEUR_FIN" if numero == 249_999 else f"Beneficiaire {numero:06d}"
            flux.write(f"{numero};{nom};{numero % 100000};Toliara\n")
    assert chemin.stat().st_size > 8 * 1024 * 1024
    return chemin


def test_csv_volumineux_est_parcouru_sans_refus(grand_csv: Path) -> None:
    """Plus de refus à 8 Mio : l'aperçu est borné, le total est exact."""
    sortie = lire_csv(grand_csv)

    assert sortie["nb_lignes"] == 250_001
    assert sortie["nb_donnees"] == 250_000
    assert len(sortie["apercu_lignes"]) == 40
    assert sortie["tronque"] is True
    assert sortie["lecture_en_flux"] is True
    assert sortie["apercu_lignes"][0]["ligne"] == 2


def test_csv_volumineux_plage_et_ligne(grand_csv: Path) -> None:
    """La dernière ligne reste citable sans charger le fichier entier."""
    plage = lire_csv_plage(grand_csv, from_row=250_000, to_row=250_001)

    assert plage["total"] == 250_001
    assert [entree["ligne"] for entree in plage["lignes"]] == [250_000, 250_001]
    assert plage["lignes"][-1]["valeurs"]["nom"] == "MARQUEUR_FIN"
    assert plage["lecture_en_flux"] is True

    ligne = lire_element_csv(grand_csv, type_element="ligne", index=250_001)
    assert ligne["valeurs"]["nom"] == "MARQUEUR_FIN"
    assert ligne["total"] == 250_001


def test_csv_volumineux_recherche_atteint_la_fin(grand_csv: Path) -> None:
    """La recherche parcourt tout le fichier, pas seulement l'aperçu."""
    resultat = chercher_dans_csv(grand_csv, "MARQUEUR_FIN")

    assert resultat["total"] == 250_001
    assert resultat["nb_resultats"] == 1
    assert resultat["resultats"][0]["page"] == 250_001
    assert resultat["resultats"][0]["valeurs"]["nom"] == "MARQUEUR_FIN"
    assert resultat["lecture_en_flux"] is True


def test_structure_csv_donne_colonnes_et_exemples(grand_csv: Path) -> None:
    """La structure d'un CSV décrit ses colonnes réelles, sur un échantillon."""
    structure = structure_csv(grand_csv, max_lignes=50)

    assert structure["format"] == "csv"
    assert [colonne["nom"] for colonne in structure["colonnes"]] == [
        "id",
        "nom",
        "montant",
        "zone",
    ]
    assert structure["colonnes"][1]["exemple"].startswith("Beneficiaire")
    assert structure["nb_lignes"] == 250_001
    assert structure["echantillon_tronque"] is True


# --- 9. Le tool de structure couvre désormais le CSV --------------------------


def test_get_document_structure_inspecte_un_csv(
    service: DocumentService, csv_document: Any
) -> None:
    """« Inspection non supportée » ne doit plus être la réponse à un CSV."""
    structure = _outils(service)["get_document_structure"].invoke(
        {"document_id": str(csv_document.id)}
    )

    assert structure["ok"] is True
    assert structure["format"] == "csv"
    assert structure["unite_pagination"] == "lignes"
    assert [colonne["nom"] for colonne in structure["colonnes"]] == [
        "Nom",
        "Bénéficiaires",
        "Zone",
    ]
    assert structure["colonnes"][0]["exemple"] == "Atelier A"
