"""API — archivage, espace session (jour J) et import des bénéficiaires.

Routes ajoutées le 20/09 : ``POST /equipes/{id}/archivage`` et réactivation,
liste globale des sessions, documents d'une session, pointage en lot, fiche de
présence générée, aperçu et confirmation d'import Excel.

Le contrat vérifié ici est surtout le HITL : l'aperçu **n'écrit rien**, seule
la confirmation écrit ; régénérer une fiche de présence produit une nouvelle
version, jamais un 409.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.domain.enums import StatutMission, TypeDocument
from app.domain.execution import Beneficiaire, Mission, Participation, SessionFormation
from app.domain.organization import Organisation
from tests.conftest import seed
from tests.document_fixtures import build_xlsx

PRÉFIXE = "/api/v1"

#: Plage de la session de test : du 01 au 05/09/2026 (exemple CARSO).
JOUR_1 = "2026-09-01"
JOUR_3 = "2026-09-03"


@pytest.fixture
def storage_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Racine documentaire temporaire : aucun dépôt de test n'écrit ailleurs."""
    racine = tmp_path / "carso"
    monkeypatch.setenv("STORAGE_ROOT", str(racine))
    get_settings.cache_clear()
    yield racine
    get_settings.cache_clear()


@pytest.fixture
def donnees(api_session_factory: sessionmaker[Session]) -> dict[str, object]:
    """Une session de formation avec deux participations pointables."""
    organisation = Organisation(nom="Client Espace Session")
    seed(api_session_factory, organisation)

    mission = Mission(
        organisation_id=organisation.id,
        reference="M-JOURJ-01",
        titre="Mission jour J",
        date_debut=date(2026, 9, 1),
        date_fin=date(2026, 9, 5),
        statut=StatutMission.EN_COURS.value,
    )
    seed(api_session_factory, mission)

    session_formation = SessionFormation(
        mission_id=mission.id,
        theme="Jour 1",
        lieu="Salle Dakar",
        date_debut=date(2026, 9, 1),
        date_fin=date(2026, 9, 5),
    )
    sow = Beneficiaire(nom="Sow", prenom="Fatou")
    ba = Beneficiaire(nom="Ba", prenom="Moussa")
    seed(api_session_factory, session_formation, sow, ba)

    p1 = Participation(session_id=session_formation.id, beneficiaire_id=sow.id)
    p2 = Participation(session_id=session_formation.id, beneficiaire_id=ba.id)
    seed(api_session_factory, p1, p2)
    return {
        "session_id": str(session_formation.id),
        "mission_id": str(mission.id),
        "participations": [str(p1.id), str(p2.id)],
        "jours": (JOUR_1, JOUR_3),
    }


def _deposer_classeur(
    client: TestClient, session_id: str, lignes: list[list[object]]
) -> dict[str, object]:
    reponse = client.post(
        f"{PRÉFIXE}/documents",
        files={
            "file": (
                "liste.xlsx",
                build_xlsx(feuille="Liste", lignes=lignes),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
        data={
            "type_document": TypeDocument.LISTE_BENEFICIAIRES.value,
            "session_id": session_id,
        },
    )
    assert reponse.status_code == 201, reponse.text
    return reponse.json()


# --- Archivage (équipes et bénéficiaires) ----------------------------------------


def test_archivage_et_reactivation_d_une_equipe(api_client: TestClient) -> None:
    cree = api_client.post(
        f"{PRÉFIXE}/equipes/", json={"nom": "Diallo", "prenom": "Awa"}
    )
    assert cree.status_code == 201
    equipe_id = cree.json()["id"]
    # Une personne créée sans statut est active : elle ne peut pas disparaître
    # des listes actives par surprise.
    assert cree.json()["statut"] == "actif"

    archivee = api_client.post(f"{PRÉFIXE}/equipes/{equipe_id}/archivage")
    assert archivee.status_code == 200
    assert archivee.json()["statut"] == "archive"

    active = api_client.get(f"{PRÉFIXE}/equipes/?statut=actif")
    assert active.status_code == 200
    assert active.json() == []
    archivees = api_client.get(f"{PRÉFIXE}/equipes/?statut=archive")
    assert [e["id"] for e in archivees.json()] == [equipe_id]

    reactivee = api_client.post(f"{PRÉFIXE}/equipes/{equipe_id}/reactivation")
    assert reactivee.json()["statut"] == "actif"


def test_archiver_deux_fois_422(api_client: TestClient) -> None:
    cree = api_client.post(f"{PRÉFIXE}/equipes/", json={"nom": "Diop", "prenom": "Ali"})
    equipe_id = cree.json()["id"]
    api_client.post(f"{PRÉFIXE}/equipes/{equipe_id}/archivage")

    second = api_client.post(f"{PRÉFIXE}/equipes/{equipe_id}/archivage")
    assert second.status_code == 422


def test_archivage_d_un_beneficiaire(api_client: TestClient, donnees) -> None:
    cree = api_client.post(f"{PRÉFIXE}/beneficiaires/", json={"nom": "Fall", "prenom": "Awa"})
    beneficiaire_id = cree.json()["id"]

    archive = api_client.post(f"{PRÉFIXE}/beneficiaires/{beneficiaire_id}/archivage")
    assert archive.json()["statut"] == "archive"
    actifs = api_client.get(f"{PRÉFIXE}/beneficiaires/?statut=actif").json()
    assert beneficiaire_id not in [b["id"] for b in actifs]

    reactivation = api_client.post(
        f"{PRÉFIXE}/beneficiaires/{beneficiaire_id}/reactivation"
    )
    assert reactivation.json()["statut"] == "actif"


def test_archiver_une_personne_inconnue_404(api_client: TestClient) -> None:
    from uuid import uuid4

    inexistant = uuid4()
    assert api_client.post(f"{PRÉFIXE}/equipes/{inexistant}/archivage").status_code == 404
    assert api_client.post(f"{PRÉFIXE}/beneficiaires/{inexistant}/archivage").status_code == 404


# --- Sessions : liste globale et documents ---------------------------------------


def test_liste_globale_des_sessions(api_client: TestClient, donnees) -> None:
    toutes = api_client.get(f"{PRÉFIXE}/sessions/")
    assert toutes.status_code == 200
    assert [s["id"] for s in toutes.json()] == [donnees["session_id"]]
    # Sans slash final : pas de 307 qui ferait perdre le Bearer navigateur.
    sans_slash = api_client.get(f"{PRÉFIXE}/sessions", follow_redirects=False)
    assert sans_slash.status_code == 200

    par_mission = api_client.get(f"{PRÉFIXE}/sessions/?mission_id={donnees['mission_id']}")
    assert [s["id"] for s in par_mission.json()] == [donnees["session_id"]]

    vide = api_client.get(f"{PRÉFIXE}/sessions/?statut=realisee")
    assert vide.json() == []


def test_documents_d_une_session(
    api_client: TestClient, donnees, storage_root: Path
) -> None:
    session_id = donnees["session_id"]
    document = _deposer_classeur(
        api_client, session_id, [["nom", "prenom"], ["Sow", "Fatou"]]
    )

    assert document["session_id"] == session_id
    liste = api_client.get(f"{PRÉFIXE}/sessions/{session_id}/documents")
    assert [d["id"] for d in liste.json()] == [document["id"]]


def test_documents_d_une_session_inexistante_404(api_client: TestClient) -> None:
    from uuid import uuid4

    reponse = api_client.get(f"{PRÉFIXE}/sessions/{uuid4()}/documents")
    assert reponse.status_code == 404


# --- Pointage en lot -------------------------------------------------------------


def test_pointage_en_lot_d_une_fiche_complete(api_client: TestClient, donnees) -> None:
    """Le pointage porte une **date** et ne touche plus l'inscription."""
    session_id = donnees["session_id"]
    p1, p2 = donnees["participations"]  # type: ignore[misc]

    reponse = api_client.patch(
        f"{PRÉFIXE}/sessions/{session_id}/presences",
        json={
            "date": JOUR_1,
            "lignes": [
                {"participation_id": p1, "presence": "present", "heure_arrivee": "09:05"},
                {"participation_id": p2, "presence": "retard", "heure_arrivee": "10:12"},
            ],
        },
    )
    assert reponse.status_code == 200, reponse.text
    pointees = {p["participation_id"]: (p["presence"], p["date"]) for p in reponse.json()}
    assert pointees == {p1: ("present", JOUR_1), p2: ("retard", JOUR_1)}

    # La fiche d'un jour se relit par date ; les autres jours restent vides.
    du_jour = api_client.get(
        f"{PRÉFIXE}/sessions/{session_id}/presences", params={"date": JOUR_1}
    ).json()
    assert {p["participation_id"] for p in du_jour} == {p1, p2}
    assert api_client.get(
        f"{PRÉFIXE}/sessions/{session_id}/presences", params={"date": JOUR_3}
    ).json() == []

    # L'inscription ne porte plus de présence : une seule vérité pour le fait.
    lues = api_client.get(f"{PRÉFIXE}/sessions/{session_id}/participations").json()
    assert all("presence" not in inscription for inscription in lues)


def test_pointage_hors_vocabulaire_422(api_client: TestClient, donnees) -> None:
    session_id = donnees["session_id"]
    p1 = donnees["participations"][0]  # type: ignore[index]

    reponse = api_client.patch(
        f"{PRÉFIXE}/sessions/{session_id}/presences",
        json={"date": JOUR_1, "lignes": [{"participation_id": p1, "presence": "peut-etre"}]},
    )
    assert reponse.status_code == 422
    assert reponse.json()["error"]["code"] == "business_validation_error"


def test_pointage_sans_date_422(api_client: TestClient, donnees) -> None:
    """Une présence sans jour ne dit rien : la date est obligatoire."""
    session_id = donnees["session_id"]
    p1 = donnees["participations"][0]  # type: ignore[index]

    reponse = api_client.patch(
        f"{PRÉFIXE}/sessions/{session_id}/presences",
        json={"lignes": [{"participation_id": p1, "presence": "present"}]},
    )
    assert reponse.status_code == 422


def test_pointage_hors_plage_422(api_client: TestClient, donnees) -> None:
    """La session du 01 au 05/09 ne se pointe pas le 06."""
    session_id = donnees["session_id"]
    p1 = donnees["participations"][0]  # type: ignore[index]

    reponse = api_client.patch(
        f"{PRÉFIXE}/sessions/{session_id}/presences",
        json={
            "date": "2026-09-06",
            "lignes": [{"participation_id": p1, "presence": "present"}],
        },
    )
    assert reponse.status_code == 422
    assert reponse.json()["error"]["code"] == "business_validation_error"


def test_pointage_unitaire_d_une_date(api_client: TestClient, donnees) -> None:
    """Route unitaire : elle pointe une personne pour une date, et corrige."""
    p1 = donnees["participations"][0]  # type: ignore[index]

    premiere = api_client.post(
        f"{PRÉFIXE}/participations/{p1}/presence",
        json={"date": JOUR_3, "presence": "absent"},
    )
    assert premiere.status_code == 200, premiere.text
    assert (premiere.json()["date"], premiere.json()["presence"]) == (JOUR_3, "absent")

    correction = api_client.post(
        f"{PRÉFIXE}/participations/{p1}/presence",
        json={"date": JOUR_3, "presence": "present"},
    )
    assert correction.status_code == 200
    assert correction.json()["id"] == premiere.json()["id"]
    assert correction.json()["presence"] == "present"


def test_pointage_d_une_participation_etrangere_422(
    api_client: TestClient, donnees, api_session_factory: sessionmaker[Session]
) -> None:
    """La fiche d'une session ne pointe pas la participation d'une autre."""
    organisation = Organisation(nom="Client Autre Session")
    seed(api_session_factory, organisation)
    mission = Mission(
        organisation_id=organisation.id,
        reference="M-JOURJ-02",
        titre="Autre mission",
        statut=StatutMission.EN_COURS.value,
    )
    seed(api_session_factory, mission)
    autre = SessionFormation(mission_id=mission.id, theme="Jour 1")
    beneficiaire = Beneficiaire(nom="Ndoye", prenom="Awa")
    seed(api_session_factory, autre, beneficiaire)
    participation_etrangere = Participation(
        session_id=autre.id, beneficiaire_id=beneficiaire.id
    )
    seed(api_session_factory, participation_etrangere)

    reponse = api_client.patch(
        f"{PRÉFIXE}/sessions/{donnees['session_id']}/presences",
        json={
            "date": JOUR_1,
            "lignes": [
                {
                    "participation_id": str(participation_etrangere.id),
                    "presence": "present",
                }
            ],
        },
    )
    assert reponse.status_code == 422


# --- Import Excel : aperçu puis confirmation -------------------------------------


def test_apercu_n_ecrit_rien(api_client: TestClient, donnees) -> None:
    """L'aperçu classe, n'écrit rien — et repère la personne déjà inscrite."""
    session_id = donnees["session_id"]
    document = _deposer_classeur(
        api_client,
        session_id,
        [
            ["nom", "prenom", "contact"],
            ["Fall", "Awa", "awa@example.org"],
            ["Sow", "Fatou", "fatou@example.org"],
            ["", "Ndiaye"],
        ],
    )

    reponse = api_client.post(
        f"{PRÉFIXE}/sessions/{session_id}/imports/beneficiaires/apercu",
        json={"document_id": document["id"]},
    )
    assert reponse.status_code == 200, reponse.text
    apercu = reponse.json()
    assert apercu["nb_lignes"] == 3
    assert apercu["nb_valides"] == 1
    statuts = {ligne["statut"] for ligne in apercu["lignes"]}
    assert statuts == {"valide", "deja_dans_session", "incomplete"}

    # Rien n'a été écrit : ni bénéficiaire, ni participation.
    assert len(api_client.get(f"{PRÉFIXE}/beneficiaires/").json()) == 2
    assert len(api_client.get(f"{PRÉFIXE}/sessions/{session_id}/participations").json()) == 2


def test_apercu_colonnes_absentes_422(api_client: TestClient, donnees) -> None:
    session_id = donnees["session_id"]
    document = _deposer_classeur(api_client, session_id, [["ville"], ["Dakar"]])

    reponse = api_client.post(
        f"{PRÉFIXE}/sessions/{session_id}/imports/beneficiaires/apercu",
        json={"document_id": document["id"]},
    )
    assert reponse.status_code == 422
    details = reponse.json()["error"]["details"]
    assert set(details["colonnes_requises"]) == {"nom", "prenom"}


def test_confirmation_cree_et_inscrit(api_client: TestClient, donnees) -> None:
    session_id = donnees["session_id"]
    reponse = api_client.post(
        f"{PRÉFIXE}/sessions/{session_id}/imports/beneficiaires",
        json={
            "lignes": [
                {"nom": "Fall", "prenom": "Awa", "contact": "awa@example.org"},
                {"nom": "", "prenom": "Ndiaye"},
            ]
        },
    )
    assert reponse.status_code == 201, reponse.text
    corps = reponse.json()
    assert (corps["crees"], corps["inscrits"], corps["ignores"]) == (1, 1, 1)

    inscrits = api_client.get(f"{PRÉFIXE}/sessions/{session_id}/participations").json()
    assert len(inscrits) == 3  # deux participations de départ + Awa Fall


def test_confirmation_signale_le_doublon_sans_fusionner(
    api_client: TestClient, donnees
) -> None:
    """La bénéficiaire déjà en base est inscrite, jamais dupliquée."""
    session_id = donnees["session_id"]
    avant = api_client.get(f"{PRÉFIXE}/beneficiaires/").json()
    assert len(avant) == 2

    reponse = api_client.post(
        f"{PRÉFIXE}/sessions/{session_id}/imports/beneficiaires",
        json={"lignes": [{"nom": "BA", "prenom": "Moussa"}]},
    )
    corps = reponse.json()
    assert (corps["crees"], corps["doublons_signales"]) == (0, 1)
    assert len(api_client.get(f"{PRÉFIXE}/beneficiaires/").json()) == 2


def test_confirmation_sans_lignes_422(api_client: TestClient, donnees) -> None:
    reponse = api_client.post(
        f"{PRÉFIXE}/sessions/{donnees['session_id']}/imports/beneficiaires",
        json={"lignes": []},
    )
    assert reponse.status_code == 422


# --- Fiche de présence générée ---------------------------------------------------


def test_fiche_de_presence_generee(
    api_client: TestClient, donnees, storage_root: Path
) -> None:
    session_id = donnees["session_id"]
    p1, p2 = donnees["participations"]  # type: ignore[misc]
    api_client.patch(
        f"{PRÉFIXE}/sessions/{session_id}/presences",
        json={
            "date": JOUR_1,
            "lignes": [
                {"participation_id": p1, "presence": "present", "heure_arrivee": "09:05"},
                {"participation_id": p2, "presence": "absent"},
            ],
        },
    )

    reponse = api_client.post(
        f"{PRÉFIXE}/sessions/{session_id}/fiche-presence", json={"date": JOUR_1}
    )
    assert reponse.status_code == 201, reponse.text
    fiche = reponse.json()
    assert fiche["type_document"] == TypeDocument.FICHE_PRESENCE.value
    assert fiche["session_id"] == session_id
    assert fiche["version"] == 1
    # Une fiche par jour, jamais une fiche de session entière.
    assert JOUR_1 in fiche["nom"]
    assert (storage_root / fiche["storage_path"]).is_file()

    # Une seconde génération du même jour produit une nouvelle version (pas de 409).
    seconde = api_client.post(
        f"{PRÉFIXE}/sessions/{session_id}/fiche-presence", json={"date": JOUR_1}
    )
    assert seconde.status_code == 201, seconde.text
    assert seconde.json()["version"] == 2
    assert seconde.json()["nom"] == fiche["nom"]


def test_fiche_de_presence_ne_contient_que_le_jour_pointe(
    api_client: TestClient, donnees
) -> None:
    """Le 03/09, la feuille du 01/09 ne montre personne : elle est vide, pas fausse."""
    session_id = donnees["session_id"]
    p1 = donnees["participations"][0]  # type: ignore[index]
    api_client.patch(
        f"{PRÉFIXE}/sessions/{session_id}/presences",
        json={"date": JOUR_1, "lignes": [{"participation_id": p1, "presence": "present"}]},
    )

    reponse = api_client.post(
        f"{PRÉFIXE}/sessions/{session_id}/fiche-presence", json={"date": JOUR_3}
    )
    assert reponse.status_code == 201, reponse.text
    assert JOUR_3 in reponse.json()["nom"]


def test_fiche_de_presence_sans_date_422(api_client: TestClient, donnees) -> None:
    reponse = api_client.post(f"{PRÉFIXE}/sessions/{donnees['session_id']}/fiche-presence")
    assert reponse.status_code == 422
