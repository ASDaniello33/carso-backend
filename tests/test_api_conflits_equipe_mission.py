"""Tests — alerte de chevauchement de l'équipe d'une mission (ADR 0015).

Le report des dates d'une mission peut croiser une autre mission d'un membre
déjà approuvé. La règle de disponibilité est **calculée** (ADR 0012) ; cette
lecture l'agrège par membre et ne bloque jamais une écriture : elle informe.
"""

from __future__ import annotations

from datetime import date

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.application.services import AffectationService, MissionService
from app.domain.enums import (
    ActorType,
    SourceAffectation,
    StatutAffectation,
    StatutMission,
)
from app.domain.execution import AffectationEquipe, Equipe, Mission
from app.domain.organization import Organisation
from tests.conftest import seed

PRÉFIXE = "/api/v1"


def _org(factory: sessionmaker[Session], nom: str = "Client conflits") -> Organisation:
    org = Organisation(nom=nom)
    seed(factory, org)
    return org


def _mission(
    factory: sessionmaker[Session],
    org: Organisation,
    *,
    reference: str,
    debut: date | None,
    fin: date | None,
    statut: str = StatutMission.PLANIFIE.value,
) -> Mission:
    mission = Mission(
        organisation_id=org.id,
        reference=reference,
        titre=f"Mission {reference}",
        statut=statut,
        date_debut=debut,
        date_fin=fin,
    )
    seed(factory, mission)
    return mission


def _personne(factory: sessionmaker[Session], nom: str, prenom: str) -> Equipe:
    personne = Equipe(nom=nom, prenom=prenom, profil="formateur")
    seed(factory, personne)
    return personne


def _affectation(
    factory: sessionmaker[Session],
    mission: Mission,
    personne: Equipe,
    statut: str = StatutAffectation.APPROUVEE.value,
) -> AffectationEquipe:
    affectation = AffectationEquipe(
        mission_id=mission.id,
        equipe_id=personne.id,
        role_dans_mission="formateur",
        statut=statut,
        source_affectation=SourceAffectation.MANUAL.value,
    )
    seed(factory, affectation)
    return affectation


# --- Règle de lecture --------------------------------------------------------


def test_conflits_mission_signale_la_personne_occupee_ailleurs(
    api_session_factory: sessionmaker[Session],
) -> None:
    """La mission examinée chevauche une autre mission approuvée de son formateur."""
    org = _org(api_session_factory)
    mission = _mission(
        api_session_factory,
        org,
        reference="M-CONF-1",
        debut=date(2026, 12, 1),
        fin=date(2026, 12, 5),
    )
    ailleurs = _mission(
        api_session_factory,
        org,
        reference="M-CONF-AUTRE",
        debut=date(2026, 12, 3),
        fin=date(2026, 12, 9),
    )
    personne = _personne(api_session_factory, "Amrani", "Sara")
    _affectation(api_session_factory, mission, personne)
    _affectation(api_session_factory, ailleurs, personne)

    with api_session_factory() as session:
        conflits = AffectationService(session).conflits_mission(mission.id)

    assert len(conflits) == 1
    assert conflits[0]["nom"] == "Amrani"
    assert conflits[0]["prenom"] == "Sara"
    missions = conflits[0]["missions_en_conflit"]
    assert [m["reference"] for m in missions] == ["M-CONF-AUTRE"]
    assert missions[0]["date_debut"] == "2026-12-03"


def test_aucun_conflit_quand_les_dates_ne_se_touchent_pas(
    api_session_factory: sessionmaker[Session],
) -> None:
    org = _org(api_session_factory, "Client sans conflit")
    mission = _mission(
        api_session_factory,
        org,
        reference="M-CONF-2",
        debut=date(2026, 12, 1),
        fin=date(2026, 12, 2),
    )
    ailleurs = _mission(
        api_session_factory,
        org,
        reference="M-CONF-LOIN",
        debut=date(2026, 12, 3),
        fin=date(2026, 12, 4),
    )
    personne = _personne(api_session_factory, "Benali", "Karim")
    _affectation(api_session_factory, mission, personne)
    _affectation(api_session_factory, ailleurs, personne)

    with api_session_factory() as session:
        assert AffectationService(session).conflits_mission(mission.id) == []


def test_une_affectation_proposee_ne_bloque_personne(
    api_session_factory: sessionmaker[Session],
) -> None:
    """La règle ne regarde que les affectations approuvées (jamais une intention)."""
    org = _org(api_session_factory, "Client propositions")
    mission = _mission(
        api_session_factory,
        org,
        reference="M-CONF-3",
        debut=date(2026, 12, 1),
        fin=date(2026, 12, 5),
    )
    ailleurs = _mission(
        api_session_factory,
        org,
        reference="M-CONF-PROP",
        debut=date(2026, 12, 1),
        fin=date(2026, 12, 5),
    )
    personne = _personne(api_session_factory, "Haddad", "Omar")
    _affectation(api_session_factory, mission, personne)
    _affectation(
        api_session_factory, ailleurs, personne, statut=StatutAffectation.PROPOSEE.value
    )

    with api_session_factory() as session:
        assert AffectationService(session).conflits_mission(mission.id) == []


def test_mission_annulee_n_est_pas_evaluable(
    api_session_factory: sessionmaker[Session],
) -> None:
    org = _org(api_session_factory, "Client annulé")
    mission = _mission(
        api_session_factory,
        org,
        reference="M-CONF-4",
        debut=date(2026, 12, 1),
        fin=date(2026, 12, 5),
        statut=StatutMission.ANNULE.value,
    )
    personne = _personne(api_session_factory, "Solo", "Sam")
    _affectation(api_session_factory, mission, personne)

    with api_session_factory() as session:
        assert AffectationService(session).conflits_mission(mission.id) == []


def test_report_des_dates_fait_apparaitre_le_conflit(
    api_session_factory: sessionmaker[Session],
) -> None:
    """Le geste même de la demande : déplacer les dates croise une autre mission."""
    org = _org(api_session_factory, "Client report")
    mission = _mission(
        api_session_factory,
        org,
        reference="M-CONF-5",
        debut=date(2026, 12, 1),
        fin=date(2026, 12, 5),
    )
    ailleurs = _mission(
        api_session_factory,
        org,
        reference="M-CONF-CROISEE",
        debut=date(2026, 12, 20),
        fin=date(2026, 12, 25),
    )
    personne = _personne(api_session_factory, "Fofana", "Léa")
    _affectation(api_session_factory, mission, personne)
    _affectation(api_session_factory, ailleurs, personne)

    with api_session_factory() as session:
        service = AffectationService(session)
        assert service.conflits_mission(mission.id) == []

        MissionService(session).modifier(
            mission.id,
            date_debut=date(2026, 12, 22),
            date_fin=date(2026, 12, 28),
            acteur=ActorType.HUMAIN.value,
        )

        conflits = service.conflits_mission(mission.id)

    assert [c["nom"] for c in conflits] == ["Fofana"]
    assert conflits[0]["missions_en_conflit"][0]["reference"] == "M-CONF-CROISEE"


# --- Route de lecture --------------------------------------------------------


def test_route_conflits_equipe_expose_la_lecture(
    api_client: TestClient, api_session_factory: sessionmaker[Session]
) -> None:
    org = _org(api_session_factory, "Client route conflits")
    mission = _mission(
        api_session_factory,
        org,
        reference="M-CONF-API",
        debut=date(2026, 11, 2),
        fin=date(2026, 11, 6),
    )
    ailleurs = _mission(
        api_session_factory,
        org,
        reference="M-CONF-API-BLOC",
        debut=date(2026, 11, 4),
        fin=date(2026, 11, 9),
    )
    prise = _personne(api_session_factory, "Amrani", "Sara")
    libre = _personne(api_session_factory, "Benali", "Karim")
    _affectation(api_session_factory, mission, prise)
    _affectation(api_session_factory, mission, libre)
    _affectation(api_session_factory, ailleurs, prise)

    reponse = api_client.get(f"{PRÉFIXE}/missions/{mission.id}/conflits-equipe")

    assert reponse.status_code == 200, reponse.text
    corps = reponse.json()
    assert corps["evaluable"] is True
    assert corps["date_debut"] == "2026-11-02"
    assert len(corps["membres"]) == 1
    membre = corps["membres"][0]
    assert membre["prenom"] == "Sara"
    assert membre["equipe_id"] == str(prise.id)
    assert membre["missions_en_conflit"][0]["reference"] == "M-CONF-API-BLOC"


def test_route_conflits_equipe_sans_dates_non_evaluable(
    api_client: TestClient, api_session_factory: sessionmaker[Session]
) -> None:
    org = _org(api_session_factory, "Client route sans dates")
    mission = _mission(api_session_factory, org, reference="M-CONF-API-VIDE", debut=None, fin=None)

    reponse = api_client.get(f"{PRÉFIXE}/missions/{mission.id}/conflits-equipe")

    assert reponse.status_code == 200, reponse.text
    corps = reponse.json()
    assert corps["evaluable"] is False
    assert corps["membres"] == []


def test_route_evalue_une_periode_hypothethetique(
    api_client: TestClient, api_session_factory: sessionmaker[Session]
) -> None:
    """Le formulaire évalue les dates saisies **avant** d'enregistrer le report."""
    org = _org(api_session_factory, "Client période hypothétique")
    mission = _mission(
        api_session_factory,
        org,
        reference="M-CONF-HYP",
        debut=date(2027, 3, 1),
        fin=date(2027, 3, 5),
    )
    ailleurs = _mission(
        api_session_factory,
        org,
        reference="M-CONF-HYP-B",
        debut=date(2027, 3, 20),
        fin=date(2027, 3, 25),
    )
    personne = _personne(api_session_factory, "Rakoto", "Nina")
    _affectation(api_session_factory, mission, personne)
    _affectation(api_session_factory, ailleurs, personne)

    # Dates enregistrées : aucun conflit.
    avant = api_client.get(f"{PRÉFIXE}/missions/{mission.id}/conflits-equipe")
    assert avant.json()["membres"] == []

    # Période saisie (report à venir) : le conflit apparaît, rien n'est écrit.
    apres = api_client.get(
        f"{PRÉFIXE}/missions/{mission.id}/conflits-equipe",
        params={"date_debut": "2027-03-22", "date_fin": "2027-03-26"},
    )
    corps = apres.json()
    assert corps["evaluable"] is True
    assert corps["date_debut"] == "2027-03-22"
    assert [m["prenom"] for m in corps["membres"]] == ["Nina"]
    assert corps["membres"][0]["missions_en_conflit"][0]["reference"] == "M-CONF-HYP-B"

    # La mission n'a pas bougé : la lecture n'écrit rien.
    inchangee = api_client.get(f"{PRÉFIXE}/missions/{mission.id}").json()
    assert inchangee["date_debut"] == "2027-03-01"
    assert inchangee["date_fin"] == "2027-03-05"


def test_route_conflits_equipe_mission_inconnue_404(api_client: TestClient) -> None:
    from uuid import uuid4

    reponse = api_client.get(f"{PRÉFIXE}/missions/{uuid4()}/conflits-equipe")
    assert reponse.status_code == 404
