"""Tests — agent personnel : réglages du compte et surface HTTP (10/10).

Ce que ces tests verrouillent, et qui n'était prouvé nulle part :

1. **le contrat proposé est le contrat effectif** — surcharge administrée
   comprise : ce que l'écran affiche est exactement ce que le backend acceptera ;
2. **un compte ne peut que retirer** — un nom d'outil hors contrat est refusé
   avec la liste valide, jamais absorbé en silence ;
3. **le périmètre est le compte** — les routes ne prennent aucun identifiant de
   propriétaire : la configuration d'un compte n'est ni lisible ni modifiable
   par un autre ;
4. **l'agent retrouve son contrat** — la réinitialisation désactive la ligne
   sans la détruire (la contrainte d'unicité porte sur ``utilisateur_id``,
   lignes désactivées comprises).

Aucun accès réseau, aucune base externe : SQLite mémoire + dépendances
surchargées, comme le reste de la suite.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.agents.agent_personnel import AGENT_ID, DEFINITION
from app.application.services.agent_personnel_service import (
    INSTRUCTIONS_MAX,
    AgentPersonnelService,
)
from app.core.errors import ValidationError
from app.domain.document import AuditEvent
from app.domain.enums import ActionAudit
from app.domain.identity import Utilisateur
from app.infrastructure.repositories import ConfigurationAgentPersonnelRepository

PRÉFIXE = "/api/v1"


def _compte(role: str = "formateur") -> Utilisateur:
    """Compte actif : la route personnelle n'exige aucune garde d'administration."""
    return Utilisateur(
        email=f"perso-{uuid4().hex[:8]}@carso.test",
        nom="Personne",
        prenom="Perso",
        password_hash="x",
        role=role,
        statut="actif",
    )


def _brancher(api_client: TestClient, utilisateur: Utilisateur) -> None:
    """Force le porteur du jeton, comme le fait la session réelle."""
    from app.api.deps import get_current_user

    api_client.app.dependency_overrides[get_current_user] = lambda: utilisateur


def _persister(api_session_factory: sessionmaker[Session], compte: Utilisateur) -> None:
    session = api_session_factory()
    try:
        session.add(compte)
        session.commit()
    finally:
        session.close()


def _service(api_session_factory: sessionmaker[Session]) -> AgentPersonnelService:
    return AgentPersonnelService(api_session_factory())


# --- 1. Contrat proposé = contrat effectif ---------------------------------------


def test_le_contrat_propose_est_celui_de_l_agent_personnel(
    api_session_factory: sessionmaker[Session],
) -> None:
    service = _service(api_session_factory)
    attendus = sorted(DEFINITION.tools)
    assert service.outils_du_contrat() == attendus
    assert attendus, "un agent sans aucun outil ne serait pas un agent"
    # Le contrat est une surface de **lecture** : aucun outil de mutation.
    for nom in attendus:
        assert not nom.startswith(("create_", "update_", "delete_", "approve_"))


def test_la_surcharge_administree_ferme_l_outil_pour_le_compte(
    api_session_factory: sessionmaker[Session],
) -> None:
    """Ce que l'administration a fermé n'est pas proposé au compte."""
    from app.domain.agent_runtime import SurchargeAgent
    from app.infrastructure.repositories import SurchargeAgentRepository

    session = api_session_factory()
    try:
        retiré = DEFINITION.tools[0]
        SurchargeAgentRepository(session).add(
            SurchargeAgent(
                agent_id=AGENT_ID,
                outils_desactives=[retiré],
                instructions_supplementaires=None,
                skills_ajoutes=[],
                actif=True,
            )
        )
        session.commit()
        service = AgentPersonnelService(api_session_factory())
        assert retiré not in service.outils_du_contrat()
    finally:
        session.close()


# --- 2. Un compte ne peut que retirer -------------------------------------------


def test_un_outil_hors_contrat_est_refuse_avec_la_liste_valide(
    api_session_factory: sessionmaker[Session],
) -> None:
    session = api_session_factory()
    try:
        compte = _compte()
        session.add(compte)
        session.commit()
        service = AgentPersonnelService(session, actor_id=str(compte.id))
        with pytest.raises(ValidationError) as erreur:
            service.enregistrer(
                compte.id,
                instructions=None,
                outils_desactives=["outil_inexistant"],
            )
        details = erreur.value.details or {}
        assert details["outils_hors_contrat"] == ["outil_inexistant"]
        assert details["outils_du_contrat"] == sorted(DEFINITION.tools)
        assert service.configuration(compte.id) is None, "rien n'a été écrit"
    finally:
        session.close()


def test_les_instructions_sont_nettoyees_et_bornees(
    api_session_factory: sessionmaker[Session],
) -> None:
    session = api_session_factory()
    try:
        compte = _compte()
        session.add(compte)
        session.commit()
        service = AgentPersonnelService(session)

        ligne = service.enregistrer(
            compte.id, instructions="  Sois bref.  ", outils_desactives=[]
        )
        assert ligne.instructions == "Sois bref."

        ligne = service.enregistrer(compte.id, instructions="   ", outils_desactives=[])
        assert ligne.instructions is None, "des espaces ne sont pas des instructions"

        with pytest.raises(ValidationError):
            service.enregistrer(
                compte.id, instructions="x" * (INSTRUCTIONS_MAX + 1), outils_desactives=[]
            )
    finally:
        session.close()


# --- 3. Périmètre : le compte, jamais un autre -----------------------------------


def test_la_configuration_est_écrite_pour_le_compte_et_auditée(
    api_session_factory: sessionmaker[Session],
) -> None:
    session = api_session_factory()
    try:
        alice, bob = _compte(), _compte("collaborateur")
        session.add_all([alice, bob])
        session.commit()
        service = AgentPersonnelService(session, actor_id=str(alice.id))
        retiré = DEFINITION.tools[0]
        ligne = service.enregistrer(
            alice.id, instructions="Ton direct.", outils_desactives=[retiré]
        )
        session.commit()

        assert ligne.utilisateur_id == alice.id
        assert service.configuration(bob.id) is None, "Bob n'hérite de rien"
        assert service.configuration(alice.id) is not None

        # L'audit conserve l'identifiant en texte (jamais un objet) : on relit
        # l'événement tel qu'il est écrit, pas tel qu'on l'a passé.
        actions = [
            e.action
            for e in session.scalars(
                select(AuditEvent).where(AuditEvent.entity_id == str(ligne.id))
            ).all()
        ]
        assert ActionAudit.CONFIGURATION_AGENT_PERSONNEL_MODIFIEE.value in actions
    finally:
        session.close()


def test_le_contrat_du_compte_perd_uniquement_l_outil_retire(
    api_session_factory: sessionmaker[Session],
) -> None:
    """Ce que le compte a retiré disparaît de **son** contrat, pas des autres."""
    from app.agents.surcharges import definition_avec_configuration_personnelle

    session = api_session_factory()
    try:
        compte = _compte()
        session.add(compte)
        session.commit()
        service = AgentPersonnelService(session)
        retiré = DEFINITION.tools[0]
        service.enregistrer(compte.id, instructions=None, outils_desactives=[retiré])
        session.commit()

        effectif = definition_avec_configuration_personnelle(
            DEFINITION, service.configuration(compte.id)
        )
        assert retiré not in effectif.tools
        assert set(effectif.tools) == set(DEFINITION.tools) - {retiré}
        # Contrat nu : rien n'est perdu pour autant.
        assert set(DEFINITION.tools) == set(DEFINITION.allowed_tools())
    finally:
        session.close()


def test_reenregistrer_reutilise_la_ligne_existante(
    api_session_factory: sessionmaker[Session],
) -> None:
    """Après une réinitialisation, réécrire ne viole pas l'unicité par compte."""
    session = api_session_factory()
    try:
        compte = _compte()
        session.add(compte)
        session.commit()
        service = AgentPersonnelService(session)
        premiere = service.enregistrer(compte.id, instructions="A", outils_desactives=[])
        assert service.supprimer(compte.id) is True
        assert service.configuration(compte.id) is None
        seconde = service.enregistrer(compte.id, instructions="B", outils_desactives=[])
        assert seconde.id == premiere.id
        assert service.configuration(compte.id).instructions == "B"
        assert ConfigurationAgentPersonnelRepository(session).get_toute_ligne(compte.id).actif
    finally:
        session.close()


# --- 4. Surface HTTP --------------------------------------------------------------


def test_get_sans_reglage_rend_le_contrat_intact(
    api_client: TestClient, api_session_factory: sessionmaker[Session]
) -> None:
    compte = _compte()
    _persister(api_session_factory, compte)
    _brancher(api_client, compte)

    reponse = api_client.get(f"{PRÉFIXE}/agents/personnel")
    assert reponse.status_code == 200, reponse.text
    corps = reponse.json()
    assert corps["agent_id"] == AGENT_ID
    assert corps["actif"] is False, "aucun réglage : l'agent suit son contrat"
    assert corps["instructions"] is None
    assert corps["outils_desactives"] == []
    assert corps["outils_effectifs"] == sorted(DEFINITION.tools)


def test_put_enregistre_et_rend_les_outils_effectifs(
    api_client: TestClient, api_session_factory: sessionmaker[Session]
) -> None:
    compte = _compte()
    _persister(api_session_factory, compte)
    _brancher(api_client, compte)
    retiré = DEFINITION.tools[0]

    ecrit = api_client.put(
        f"{PRÉFIXE}/agents/personnel",
        json={"instructions": "Réponds court.", "outils_desactives": [retiré]},
    )
    assert ecrit.status_code == 200, ecrit.text
    corps = ecrit.json()
    assert corps["actif"] is True
    assert corps["instructions"] == "Réponds court."
    assert corps["outils_desactives"] == [retiré]
    assert retiré not in corps["outils_effectifs"]
    assert sorted(DEFINITION.tools) == sorted(
        [*corps["outils_effectifs"], retiré]
    ), "aucun outil du contrat n'est perdu au passage"


def test_put_refuse_un_outil_hors_contrat(
    api_client: TestClient, api_session_factory: sessionmaker[Session]
) -> None:
    compte = _compte()
    _persister(api_session_factory, compte)
    _brancher(api_client, compte)

    refus = api_client.put(
        f"{PRÉFIXE}/agents/personnel",
        json={"instructions": None, "outils_desactives": ["create_mission"]},
    )
    assert refus.status_code == 422, refus.text
    assert "create_mission" in refus.json()["error"]["message"]


def test_delete_rend_l_agent_a_son_contrat(
    api_client: TestClient, api_session_factory: sessionmaker[Session]
) -> None:
    compte = _compte()
    _persister(api_session_factory, compte)
    _brancher(api_client, compte)
    api_client.put(
        f"{PRÉFIXE}/agents/personnel",
        json={"instructions": "Test", "outils_desactives": [DEFINITION.tools[0]]},
    )

    rendu = api_client.delete(f"{PRÉFIXE}/agents/personnel")
    assert rendu.status_code == 200, rendu.text
    corps = rendu.json()
    assert corps["actif"] is False
    assert corps["instructions"] is None
    assert corps["outils_desactives"] == []
    assert corps["outils_effectifs"] == sorted(DEFINITION.tools)


def test_un_compte_ne_voit_pas_les_reglages_d_un_autre(
    api_client: TestClient, api_session_factory: sessionmaker[Session]
) -> None:
    """Les routes ne portent aucun identifiant : le propriétaire est le jeton."""
    alice, bob = _compte(), _compte("collaborateur")
    _persister(api_session_factory, alice)
    _persister(api_session_factory, bob)

    _brancher(api_client, alice)
    assert (
        api_client.put(
            f"{PRÉFIXE}/agents/personnel",
            json={"instructions": "Réglage d'Alice", "outils_desactives": []},
        ).status_code
        == 200
    )

    _brancher(api_client, bob)
    chez_bob = api_client.get(f"{PRÉFIXE}/agents/personnel").json()
    assert chez_bob["actif"] is False
    assert chez_bob["instructions"] is None

    _brancher(api_client, alice)
    assert (
        api_client.get(f"{PRÉFIXE}/agents/personnel").json()["instructions"]
        == "Réglage d'Alice"
    )
