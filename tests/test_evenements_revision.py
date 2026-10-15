"""Tests — empreinte de révision (rafraîchissement automatique de l'interface).

L'interface recharge ses listes quand l'empreinte change : ces tests vérifient
que l'empreinte est **stable** sans écriture et qu'elle change dès qu'une ligne
métier est créée — c'est le comportement dont dépend le rafraîchissement après
une action d'agent.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.domain.enums import StatutAppelAProposition
from app.domain.organization import AppelAProposition, Lot, Organisation
from tests.conftest import seed


def _revision(client: TestClient) -> str:
    reponse = client.get("/api/v1/evenements/revision")
    assert reponse.status_code == 200
    return str(reponse.json()["revision"])


def test_revision_stable_sans_ecriture(api_client: TestClient) -> None:
    premiere = _revision(api_client)
    seconde = _revision(api_client)

    assert premiere == seconde
    assert len(premiere) == 16


def test_revision_change_apres_creation(
    api_client: TestClient, api_session_factory: sessionmaker[Session]
) -> None:
    avant = _revision(api_client)
    organisation = Organisation(nom="Client révision")
    seed(api_session_factory, organisation)
    appel = AppelAProposition(
        organisation_id=organisation.id,
        reference="AO-REV-01",
        titre="Appel révision",
        statut=StatutAppelAProposition.RECU,
    )
    seed(api_session_factory, appel)

    assert _revision(api_client) != avant


def test_revision_change_apres_modification(
    api_client: TestClient, api_session_factory: sessionmaker[Session]
) -> None:
    organisation = Organisation(nom="Client modification")
    seed(api_session_factory, organisation)
    appel = AppelAProposition(
        organisation_id=organisation.id,
        reference="AO-REV-02",
        titre="Appel à modifier",
        statut=StatutAppelAProposition.RECU,
    )
    seed(api_session_factory, appel)
    avant = _revision(api_client)

    # SQLite (base des tests) horodate à la seconde : on pose une modification
    # postérieure pour que le test vérifie le mécanisme, pas la résolution
    # d'horloge (PostgreSQL, la cible, conserve les microsecondes).
    with api_session_factory() as session:
        objet = session.get(AppelAProposition, appel.id)
        assert objet is not None
        objet.titre = "Appel modifié"
        objet.updated_at = datetime.now(UTC).replace(tzinfo=None) + timedelta(seconds=30)
        session.commit()

    assert _revision(api_client) != avant


def test_revision_ignore_les_lignes_non_metier(
    api_client: TestClient, api_session_factory: sessionmaker[Session]
) -> None:
    """Un lot sans horodatage métier créé puis supprimé ne fige pas l'empreinte."""
    organisation = Organisation(nom="Client lot")
    seed(api_session_factory, organisation)
    appel = AppelAProposition(
        organisation_id=organisation.id,
        reference="AO-REV-03",
        titre="Appel lot",
    )
    seed(api_session_factory, appel)
    lot = Lot(appel_a_proposition_id=appel.id, numero="1", titre="Lot 1")
    seed(api_session_factory, lot)

    apres_creation = _revision(api_client)
    with api_session_factory() as session:
        objet = session.get(Lot, lot.id)
        assert objet is not None
        session.delete(objet)
        session.commit()

    assert _revision(api_client) != apres_creation
