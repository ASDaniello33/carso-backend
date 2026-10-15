"""Journal d'audit — intégration API (« qui a fait quoi »).

Décision validée : le journal est **lisible** par l'administrateur et l'équipe
interne, **jamais** par un formateur ; il est insert-only et sa lecture ne
modifie rien. Ces tests couvrent la route, ses filtres, sa pagination déclarée
(``total``) et la résolution des libellés d'acteurs (nom humain, nom d'agent).
"""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.api.deps import get_current_user
from app.api.routes.audit import TYPES_ENTITE_MAX
from app.domain.document import AuditEvent
from app.domain.enums import ActionAudit, ActorType, RoleUtilisateur, StatutUtilisateur
from app.domain.identity import Utilisateur

PRÉFIXE = "/api/v1"
DÉBUT = datetime(2026, 10, 1, 8, 0, tzinfo=UTC)


def _utilisateur(role: str, nom: str = "Humain") -> Utilisateur:
    return Utilisateur(
        email=f"{nom.lower()}-{uuid4().hex[:6]}@carso.test",
        nom=nom,
        prenom="Test",
        password_hash="x",
        role=role,
        statut=StatutUtilisateur.ACTIF.value,
    )


@pytest.fixture
def journal(api_session_factory: sessionmaker[Session]) -> Iterator[SimpleNamespace]:
    """Trois événements : deux humains (dont un avec ``demande_par``) et un agent."""
    session = api_session_factory()
    try:
        actrice = _utilisateur(RoleUtilisateur.COLLABORATEUR.value, nom="Rasoa")
        demandeuse = _utilisateur(RoleUtilisateur.COLLABORATEUR.value, nom="Andria")
        session.add_all([actrice, demandeuse])
        session.flush()
        mission_id = uuid4()
        evenements = [
            AuditEvent(
                actor_type=ActorType.HUMAIN.value,
                actor_id=str(actrice.id),
                action=ActionAudit.MISSION_CREEE.value,
                entity_type="mission",
                entity_id=str(mission_id),
                after={"reference": "M-1"},
                created_at=DÉBUT,
            ),
            AuditEvent(
                actor_type=ActorType.AGENT.value,
                actor_id="agent_rh",
                action=ActionAudit.AFFECTATION_PROPOSEE.value,
                entity_type="affectation",
                entity_id=str(uuid4()),
                event_metadata={"demande_par": str(demandeuse.id)},
                created_at=DÉBUT + timedelta(minutes=5),
            ),
            AuditEvent(
                actor_type=ActorType.SYSTEME.value,
                actor_id=None,
                action=ActionAudit.DOCUMENT_PURGE.value,
                entity_type="document",
                entity_id=None,
                created_at=DÉBUT + timedelta(minutes=10),
            ),
        ]
        session.add_all(evenements)
        session.commit()
        actrice_id, demandeuse_id = actrice.id, demandeuse.id
    finally:
        session.close()
    yield SimpleNamespace(
        actrice_id=actrice_id,
        demandeuse_id=demandeuse_id,
        mission_id=mission_id,
        actrice=actrice,
        demandeuse=demandeuse,
    )


def _brancher(client: TestClient, utilisateur: Utilisateur) -> None:
    client.app.dependency_overrides[get_current_user] = lambda: utilisateur


def test_equipe_interne_lit_le_journal_du_plus_recent_au_plus_ancien(
    api_client: TestClient, journal: SimpleNamespace
) -> None:
    _brancher(api_client, journal.actrice)
    reponse = api_client.get(f"{PRÉFIXE}/audit")
    assert reponse.status_code == 200, reponse.text
    corps = reponse.json()
    assert corps["total"] == 3
    assert corps["limit"] == 50
    assert corps["offset"] == 0
    assert [e["action"] for e in corps["elements"]] == [
        ActionAudit.DOCUMENT_PURGE.value,
        ActionAudit.AFFECTATION_PROPOSEE.value,
        ActionAudit.MISSION_CREEE.value,
    ]


def test_libelles_resolus_pour_l_affichage(
    api_client: TestClient, journal: SimpleNamespace
) -> None:
    """Un humain est nommé, un agent est nommé, un demandeur est nommé."""
    _brancher(api_client, journal.actrice)
    elements = api_client.get(f"{PRÉFIXE}/audit").json()["elements"]
    humain = next(e for e in elements if e["acteur_type"] == ActorType.HUMAIN.value)
    assert humain["acteur_libelle"] == "Test Rasoa"
    agent = next(e for e in elements if e["acteur_type"] == ActorType.AGENT.value)
    assert agent["acteur_libelle"] == "Agent RH"
    assert agent["demande_par"] == str(journal.demandeuse_id)
    assert agent["demande_par_libelle"] == "Test Andria"
    systeme = next(e for e in elements if e["acteur_type"] == ActorType.SYSTEME.value)
    assert systeme["acteur_libelle"] is None


def test_filtres_et_pagination(
    api_client: TestClient, journal: SimpleNamespace
) -> None:
    _brancher(api_client, journal.actrice)
    par_action = api_client.get(
        f"{PRÉFIXE}/audit", params={"action": ActionAudit.MISSION_CREEE.value}
    ).json()
    assert par_action["total"] == 1
    assert par_action["elements"][0]["entity_id"] == str(journal.mission_id)

    par_entite = api_client.get(
        f"{PRÉFIXE}/audit", params={"entity_type": "affectation"}
    ).json()
    assert par_entite["total"] == 1

    par_acteur = api_client.get(
        f"{PRÉFIXE}/audit",
        params={"acteur_type": ActorType.AGENT.value, "acteur_id": "agent_rh"},
    ).json()
    assert par_acteur["total"] == 1

    page = api_client.get(f"{PRÉFIXE}/audit", params={"limit": 1, "offset": 1}).json()
    assert page["total"] == 3  # le total compte avant pagination
    assert page["offset"] == 1
    assert len(page["elements"]) == 1

    par_date = api_client.get(
        f"{PRÉFIXE}/audit",
        params={
            "date_debut": (DÉBUT + timedelta(minutes=6)).isoformat(),
            "date_fin": (DÉBUT + timedelta(minutes=11)).isoformat(),
        },
    ).json()
    assert par_date["total"] == 1


def test_plusieurs_types_d_entite_pour_une_meme_ressource(
    api_client: TestClient, api_session_factory: sessionmaker[Session]
) -> None:
    """L'histoire d'une entité couvre les noms hérités qu'elle porte au journal.

    Le même objet est tracé ``offre`` (cycle de vie) et ``offre_formation``
    (publication historique) : demander les deux types dans **une** requête
    évite à l'interface de recoller deux réponses — et de rater la moitié de
    l'histoire. Un type hors sujet reste dehors.
    """
    session = api_session_factory()
    try:
        acteur = _utilisateur(RoleUtilisateur.COLLABORATEUR.value)
        session.add(acteur)
        session.flush()
        identifiant = uuid4()
        session.add_all(
            [
                AuditEvent(
                    actor_type=ActorType.HUMAIN.value,
                    actor_id=str(acteur.id),
                    action=ActionAudit.OFFRE_SOUMISE.value,
                    entity_type="offre",
                    entity_id=str(identifiant),
                    created_at=DÉBUT,
                ),
                AuditEvent(
                    actor_type=ActorType.HUMAIN.value,
                    actor_id=str(acteur.id),
                    # Nom hérité (l'action ``publication_offre_formation`` n'est
                    # plus écrite) : l'entité, elle, désigne bien le même objet.
                    action="publication_offre_formation",
                    entity_type="offre_formation",
                    entity_id=str(identifiant),
                    created_at=DÉBUT + timedelta(minutes=1),
                ),
                AuditEvent(
                    actor_type=ActorType.HUMAIN.value,
                    actor_id=str(acteur.id),
                    action=ActionAudit.MISSION_CREEE.value,
                    entity_type="mission",
                    entity_id=str(identifiant),
                    created_at=DÉBUT + timedelta(minutes=2),
                ),
            ]
        )
        session.commit()
    finally:
        session.close()

    _brancher(api_client, acteur)
    reponse = api_client.get(
        f"{PRÉFIXE}/audit",
        params=[
            ("entity_type", "offre"),
            ("entity_type", "offre_formation"),
            ("entity_id", str(identifiant)),
        ],
    )
    assert reponse.status_code == 200, reponse.text
    corps = reponse.json()
    assert corps["total"] == 2
    assert sorted(e["entity_type"] for e in corps["elements"]) == [
        "offre",
        "offre_formation",
    ]
    # Même un nom d'action hérité est rendu lisible, jamais brut.
    assert [e["libelle"] for e in corps["elements"]] == [
        "Offre de formation publiée",
        "Offre soumise à revue",
    ]
    # Deux types vides ou répétés ne changent pas le résultat d'un type simple.
    simple = api_client.get(f"{PRÉFIXE}/audit", params={"entity_type": "mission"}).json()
    assert simple["total"] == 1
    assert api_client.get(f"{PRÉFIXE}/audit", params={"entity_type": ""}).json()["total"] == 3
    trop = api_client.get(
        f"{PRÉFIXE}/audit",
        params=[("entity_type", str(nombre)) for nombre in range(TYPES_ENTITE_MAX + 1)],
    )
    assert trop.status_code == 422, trop.text
    assert trop.json()["error"]["code"] == "business_validation_error"
    assert str(TYPES_ENTITE_MAX) in trop.json()["error"]["message"]


def test_parametres_invalides_refuses(api_client: TestClient, journal: SimpleNamespace) -> None:
    _brancher(api_client, journal.actrice)
    assert api_client.get(f"{PRÉFIXE}/audit", params={"limit": 0}).status_code == 422
    assert api_client.get(f"{PRÉFIXE}/audit", params={"limit": 201}).status_code == 422
    assert api_client.get(f"{PRÉFIXE}/audit", params={"offset": -1}).status_code == 422


def test_vocabulaire_des_actions_est_lisible(
    api_client: TestClient, journal: SimpleNamespace
) -> None:
    """Le filtre parle français : aucun nom technique (constat 06/10)."""
    _brancher(api_client, journal.actrice)
    reponse = api_client.get(f"{PRÉFIXE}/audit/actions")
    assert reponse.status_code == 200, reponse.text
    entrees = reponse.json()
    simples = {
        item["action"]: item["libelle"]
        for item in entrees
        if item["entity_type"] is None
    }
    assert ActionAudit.MISSION_CREEE.value in simples
    assert simples[ActionAudit.MISSION_CREEE.value] == "Mission créée"
    assert simples[ActionAudit.DOCUMENT_PURGE.value] == "Document purgé définitivement"
    # La clé technique ne fuit dans aucun libellé rendu à l'écran.
    assert all("." not in item["libelle"] and "_" not in item["libelle"] for item in entrees)


def test_suppression_par_entite_dans_le_filtre(
    api_client: TestClient, api_session_factory: sessionmaker[Session]
) -> None:
    """Une même clé, plusieurs libellés selon l'entité réellement supprimée.

    ``suppression.effectuee`` couvre les neuf ressources supprimables : le filtre
    doit dire « Suppression d'une offre » et non « Suppression ». Le motif du
    geste, lui, s'affiche dans le journal.
    """
    session = api_session_factory()
    try:
        session.add(
            AuditEvent(
                actor_type=ActorType.HUMAIN.value,
                actor_id=str(uuid4()),
                action=ActionAudit.SUPPRESSION_EFFECTUEE.value,
                entity_type="offres",
                entity_id=str(uuid4()),
                event_metadata={"reason": "offre émise en double"},
                created_at=DÉBUT,
            )
        )
        session.commit()
    finally:
        session.close()

    _brancher(api_client, _utilisateur(RoleUtilisateur.COLLABORATEUR.value))
    entrees = api_client.get(f"{PRÉFIXE}/audit/actions").json()
    suppressions = [
        item for item in entrees if item["action"] == ActionAudit.SUPPRESSION_EFFECTUEE.value
    ]
    assert suppressions == [
        {
            "action": ActionAudit.SUPPRESSION_EFFECTUEE.value,
            "entity_type": "offres",
            "libelle": "Suppression d'une offre de formation",
        }
    ]
    # Aucune entrée pour une entité jamais supprimée : le filtre ne propose que
    # ce que le journal contient.
    assert all(item["entity_type"] == "offres" for item in suppressions)

    element = api_client.get(f"{PRÉFIXE}/audit", params={"action": "suppression.effectuee"}).json()[
        "elements"
    ][0]
    assert element["libelle"] == "Suppression d'une offre de formation"
    assert element["motif"] == "offre émise en double"


def test_formateur_refuse_avec_403(
    api_client: TestClient, journal: SimpleNamespace
) -> None:
    formateur = _utilisateur(RoleUtilisateur.FORMATEUR.value, nom="Formatrice")
    _brancher(api_client, formateur)
    assert api_client.get(f"{PRÉFIXE}/audit").status_code == 403
    assert api_client.get(f"{PRÉFIXE}/audit/actions").status_code == 403


def test_administrateur_lit_le_journal(
    api_client: TestClient, journal: SimpleNamespace
) -> None:
    """L'administrateur est un role interne : le journal lui est ouvert aussi."""
    admin = _utilisateur(RoleUtilisateur.ADMINISTRATEUR.value, nom="Admin")
    _brancher(api_client, admin)
    assert api_client.get(f"{PRÉFIXE}/audit").status_code == 200


def test_lecture_ne_modifie_pas_le_journal(
    api_client: TestClient, journal: SimpleNamespace, api_session_factory: sessionmaker[Session]
) -> None:
    """Insert-only : lire n'écrit ni ne purge rien (rétention = décision CARSO)."""
    _brancher(api_client, journal.actrice)
    api_client.get(f"{PRÉFIXE}/audit")
    session = api_session_factory()
    try:
        assert session.query(AuditEvent).count() == 3
    finally:
        session.close()


def test_identifiant_inconnu_ne_casse_pas_la_resolution(
    api_client: TestClient, api_session_factory: sessionmaker[Session]
) -> None:
    """Un acteur disparu reste lisible : on retombe sur l'identifiant, jamais une erreur."""
    identifiant = str(uuid4())
    session = api_session_factory()
    try:
        session.add(
            AuditEvent(
                actor_type=ActorType.HUMAIN.value,
                actor_id=identifiant,
                action=ActionAudit.DOCUMENT_SUPPRIME.value,
                entity_type="document",
                entity_id=identifiant,
                created_at=DÉBUT,
            )
        )
        session.commit()
    finally:
        session.close()
    acteur = _utilisateur(RoleUtilisateur.COLLABORATEUR.value)
    _brancher(api_client, acteur)
    element = api_client.get(f"{PRÉFIXE}/audit").json()["elements"][0]
    assert element["acteur_libelle"] == identifiant
