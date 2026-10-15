"""Tests — administration : surcharges d'agent, secrets chiffrés, connecteurs MCP.

Trois promesses sont vérifiées ici, parce qu'elles sont faciles à casser :

1. **un réglage n'élargit jamais un droit** — une surcharge ne peut que retirer
   un outil du contrat, et un nom hors contrat est refusé avec la liste des noms
   valides (l'erreur doit expliquer, pas seulement bloquer) ;
2. **un secret ne sort jamais en clair** — la clé du provider et les en-têtes
   d'un connecteur sont chiffrés au repos ; l'API n'expose qu'un booléen ou des
   noms d'en-tête, et la valeur chiffrée ne contient pas le jeton ;
3. **sans clé de chiffrement, l'écriture d'un secret est refusée** — plutôt
   qu'un stockage silencieusement en clair.
"""

from __future__ import annotations

import json
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.agents.catalogue import AGENT_DEFINITIONS
from app.agents.surcharges import definition_effective
from app.application.services.parametres_service import (
    ParametresAgentService,
    ServeurMcpService,
    definition_du_contrat,
)
from app.core import chiffrement
from app.core.config import get_settings
from app.core.errors import NotFoundError
from app.core.errors import ValidationError as BusinessValidationError
from app.domain.agent_runtime import SurchargeAgent
from app.domain.document import AuditEvent
from app.domain.enums import ActionAudit, RoleUtilisateur
from app.domain.identity import Utilisateur

AGENT = "agent_generateur_offre"
AGENT_INCONNU = "agent_qui_n_existe_pas"


# --- Fixtures -------------------------------------------------------------------


@pytest.fixture
def cle_chiffrement(monkeypatch: pytest.MonkeyPatch) -> str:
    """Clé Fernet valide pour la durée du test (le chiffrement est disponible)."""
    from cryptography.fernet import Fernet

    cle = Fernet.generate_key().decode("ascii")
    monkeypatch.setenv("PARAMETRES_CHIFFREMENT_KEY", cle)
    monkeypatch.setenv("AUTH_SECRET", "secret-de-test")
    get_settings.cache_clear()
    chiffrement.effacer_cache()
    yield cle
    get_settings.cache_clear()
    chiffrement.effacer_cache()


@pytest.fixture
def sans_chiffrement(monkeypatch: pytest.MonkeyPatch) -> None:
    """Aucune clé de chiffrement exploitable : écrire un secret doit échouer."""
    monkeypatch.delenv("PARAMETRES_CHIFFREMENT_KEY", raising=False)
    monkeypatch.setenv("AUTH_SECRET", "")
    get_settings.cache_clear()
    chiffrement.effacer_cache()
    yield
    get_settings.cache_clear()
    chiffrement.effacer_cache()


def _admin() -> Utilisateur:
    return Utilisateur(
        email=f"admin-{uuid4().hex[:8]}@carso.test",
        nom="Admin",
        prenom="Carso",
        password_hash="x",
        role=RoleUtilisateur.ADMINISTRATEUR.value,
    )


def _brancher_admin(api_client: TestClient) -> None:
    """Force un administrateur sur les routes d'administration.

    Le porteur est remplacé **aussi** : quand ``AUTH_SECRET`` est défini (fixture
    de chiffrement), la route répondrait 401 avant d'atteindre la logique
    d'administration — un test d'administration doit rester indépendant de la
    façon dont le JWT est présenté.
    """
    from app.api.deps import get_current_user, require_admin

    admin = _admin()
    api_client.app.dependency_overrides[require_admin] = lambda: admin
    api_client.app.dependency_overrides[get_current_user] = lambda: admin


# --- 1. Chiffrement des secrets -------------------------------------------------


def test_chiffrement_round_trip(cle_chiffrement: str) -> None:
    """Une valeur chiffrée n'est pas lisible en clair, mais se déchiffre à l'identique."""
    jeton = chiffrement.chiffrer("sk-jeton-secret-123")

    assert "sk-jeton-secret-123" not in jeton
    assert chiffrement.dechiffrer(jeton) == "sk-jeton-secret-123"
    assert chiffrement.dechiffrer(None) is None


def test_chiffrement_disponible_sans_cle() -> None:
    """Sans matériel de clé, le chiffrement se déclare indisponible (pas de repli en clair)."""
    from app.core.config import Settings

    reglages = Settings(
        auth_secret=None, parametres_chiffrement_key=None  # type: ignore[arg-type]
    )
    assert chiffrement.chiffrement_disponible(reglages) is False


# --- 2. Surcharges d'agent ------------------------------------------------------


def test_surcharge_refuse_un_outil_hors_contrat(
    api_session_factory: sessionmaker[Session],
) -> None:
    """Une désactivation fantaisiste est refusée, avec la liste des outils valides."""
    session = api_session_factory()
    try:
        service = ParametresAgentService(session, actor_id="admin-1")
        with pytest.raises(BusinessValidationError) as capture:
            service.enregistrer(
                AGENT,
                outils_desactives=["outil_qui_n_existe_pas"],
                instructions_supplementaires=None,
                skills_ajoutes=[],
            )
        details = capture.value.details or {}
        assert details["outils_hors_contrat"] == ["outil_qui_n_existe_pas"]
        assert set(details["outils_du_contrat"]) == set(
            definition_du_contrat(AGENT).allowed_tools()
        )
    finally:
        session.close()


def test_surcharge_retire_un_outil_du_contrat(
    api_session_factory: sessionmaker[Session],
) -> None:
    """Le contrat effectif perd l'outil retiré, gagne les consignes, rien de plus."""
    contrat = definition_du_contrat(AGENT)
    outil = sorted(contrat.allowed_tools())[0]

    session = api_session_factory()
    try:
        service = ParametresAgentService(session, actor_id="admin-1")
        ligne = service.enregistrer(
            AGENT,
            outils_desactives=[outil],
            instructions_supplementaires="  Toujours nommer CARSO en entier.  ",
            skills_ajoutes=[],
            modifie_par="admin-1",
        )
        session.commit()

        effective = definition_effective(contrat, ligne)
        assert outil not in effective.tools
        assert set(effective.tools) == set(contrat.tools) - {outil}
        assert effective.prompt.startswith(contrat.prompt.rstrip())
        assert "Toujours nommer CARSO en entier." in effective.prompt
        # Le contrat déclaré n'a pas bougé : la surcharge est une surcouche.
        assert outil in contrat.tools

        audits = list(session.scalars(select(AuditEvent)).all())
        assert [e.action for e in audits] == [ActionAudit.SURCHARGE_AGENT_MODIFIEE.value]
    finally:
        session.close()


def test_surcharge_est_remplacee_puis_rendue_au_contrat(
    api_session_factory: sessionmaker[Session],
) -> None:
    """Une seule ligne par agent : ré-enregistrer remplace, supprimer rend le contrat."""
    session = api_session_factory()
    try:
        service = ParametresAgentService(session, actor_id="admin-1")
        service.enregistrer(
            AGENT,
            outils_desactives=[],
            instructions_supplementaires="Première consigne.",
            skills_ajoutes=[],
        )
        service.enregistrer(
            AGENT,
            outils_desactives=[],
            instructions_supplementaires="Seconde consigne.",
            skills_ajoutes=[],
        )
        session.commit()

        lignes = list(
            session.scalars(
                select(SurchargeAgent).where(SurchargeAgent.agent_id == AGENT)
            ).all()
        )
        assert len(lignes) == 1
        assert lignes[0].instructions_supplementaires == "Seconde consigne."

        assert service.supprimer(AGENT) is True
        session.commit()
        assert service.surcharge(AGENT) is None
    finally:
        session.close()


def test_surcharge_re_enregistree_apres_retour_au_contrat(
    api_session_factory: sessionmaker[Session],
) -> None:
    """Régression (bug 500 « Enregistrement impossible ») : ré-enregistrer après
    un retour au contrat réutilise la ligne désactivée au lieu d'en insérer une
    seconde — la contrainte unique porte sur agent_id, lignes inactives comprises."""
    session = api_session_factory()
    try:
        service = ParametresAgentService(session, actor_id="admin-1")
        contrat = definition_du_contrat(AGENT)
        outil = sorted(contrat.allowed_tools())[0]
        service.enregistrer(
            AGENT,
            outils_desactives=[outil],
            instructions_supplementaires=None,
            skills_ajoutes=[],
        )
        session.commit()
        # Retour au contrat : la ligne reste physiquement en base (actif=False).
        assert service.supprimer(AGENT) is True
        session.commit()

        # Ré-enregistrement : l'ancien code insérait une seconde ligne →
        # UniqueViolation (uq_surcharges_agent_agent_id) au flush.
        ligne = service.enregistrer(
            AGENT,
            outils_desactives=[],
            instructions_supplementaires="Nouvelle consigne après réactivation.",
            skills_ajoutes=[],
            modifie_par="admin-1",
        )
        session.commit()

        lignes = list(
            session.scalars(
                select(SurchargeAgent).where(SurchargeAgent.agent_id == AGENT)
            ).all()
        )
        assert len(lignes) == 1
        assert lignes[0].id == ligne.id
        assert lignes[0].actif is True
        assert lignes[0].instructions_supplementaires == (
            "Nouvelle consigne après réactivation."
        )
        assert lignes[0].outils_desactives == []
        assert lignes[0].modifie_par == "admin-1"
    finally:
        session.close()


def test_surcharge_refuse_un_skill_inconnu(
    api_session_factory: sessionmaker[Session],
) -> None:
    session = api_session_factory()
    try:
        service = ParametresAgentService(session, actor_id="admin-1")
        with pytest.raises(BusinessValidationError):
            service.enregistrer(
                AGENT,
                outils_desactives=[],
                instructions_supplementaires=None,
                skills_ajoutes=["skill_fantome"],
            )
    finally:
        session.close()


def test_agent_inconnu_refuse(api_session_factory: sessionmaker[Session]) -> None:
    session = api_session_factory()
    try:
        with pytest.raises(NotFoundError):
            definition_du_contrat(AGENT_INCONNU)
    finally:
        session.close()


# --- 3. Connecteurs MCP ---------------------------------------------------------


def test_mcp_transport_inconnu_refuse(api_session_factory: sessionmaker[Session]) -> None:
    session = api_session_factory()
    try:
        with pytest.raises(BusinessValidationError):
            ServeurMcpService(session, actor_id="admin-1").enregistrer(
                nom="x",
                transport="pigeon",
                url="https://exemple.test/mcp",
                commande=None,
                arguments=[],
                agents=[],
            )
    finally:
        session.close()


def test_mcp_exige_url_ou_commande(api_session_factory: sessionmaker[Session]) -> None:
    """Un stdio sans commande et un connecteur réseau sans URL sont refusés."""
    session = api_session_factory()
    try:
        service = ServeurMcpService(session, actor_id="admin-1")
        with pytest.raises(BusinessValidationError):
            service.enregistrer(
                nom="local",
                transport="stdio",
                url=None,
                commande=None,
                arguments=[],
                agents=[],
            )
        with pytest.raises(BusinessValidationError):
            service.enregistrer(
                nom="distant",
                transport="streamable_http",
                url=None,
                commande=None,
                arguments=[],
                agents=[],
            )
    finally:
        session.close()


def test_mcp_agent_inconnu_refuse(api_session_factory: sessionmaker[Session]) -> None:
    session = api_session_factory()
    try:
        with pytest.raises(NotFoundError):
            ServeurMcpService(session, actor_id="admin-1").enregistrer(
                nom="x",
                transport="streamable_http",
                url="https://exemple.test/mcp",
                commande=None,
                arguments=[],
                agents=[AGENT_INCONNU],
            )
    finally:
        session.close()


def test_mcp_en_tetes_chiffres_jamais_exposes(
    api_session_factory: sessionmaker[Session], cle_chiffrement: str
) -> None:
    """Le jeton est chiffré au repos ; l'API n'en connaît que le nom de l'en-tête."""
    session = api_session_factory()
    try:
        service = ServeurMcpService(session, actor_id="admin-1")
        ligne = service.enregistrer(
            nom="notion-carso",
            transport="streamable_http",
            url="https://mcp.exemple.test/mcp",
            commande=None,
            arguments=[],
            agents=[AGENT],
            en_tetes={"Authorization": "Bearer jeton-tres-secret"},
            modifie_par="admin-1",
        )
        session.commit()

        assert ligne.en_tetes_chiffres is not None
        assert "jeton-tres-secret" not in ligne.en_tetes_chiffres
        # La projection et les noms ne laissent rien filtrer.
        assert service.noms_en_tetes(ligne) == ["Authorization"]
        assert "jeton-tres-secret" not in json.dumps(ligne.resume(noms_en_tetes=["Authorization"]))
        # En revanche, la construction du client MCP reçoit bien la valeur.
        assert service.en_tetes(ligne) == {"Authorization": "Bearer jeton-tres-secret"}

        # Le journal d'audit ne doit pas contenir le jeton non plus.
        audits = list(session.scalars(select(AuditEvent)).all())
        assert audits
        assert "jeton-tres-secret" not in json.dumps(
            [e.after for e in audits], default=str
        )
    finally:
        session.close()


def test_mcp_en_tetes_sans_chiffrement_refuse(
    api_session_factory: sessionmaker[Session], sans_chiffrement: None
) -> None:
    """Pas de clé de chiffrement : écrire un en-tête est refusé, jamais stocké en clair."""
    session = api_session_factory()
    try:
        with pytest.raises(BusinessValidationError) as capture:
            ServeurMcpService(session, actor_id="admin-1").enregistrer(
                nom="x",
                transport="streamable_http",
                url="https://exemple.test/mcp",
                commande=None,
                arguments=[],
                agents=[],
                en_tetes={"Authorization": "Bearer clair"},
            )
        assert "chiffrement" in capture.value.message.lower()
    finally:
        session.close()


def test_mcp_lister_pour_agent_ne_rend_que_ce_qui_le_nomme(
    api_session_factory: sessionmaker[Session],
) -> None:
    session = api_session_factory()
    try:
        service = ServeurMcpService(session, actor_id="admin-1")
        service.enregistrer(
            nom="pour-generateur",
            transport="streamable_http",
            url="https://a.test/mcp",
            commande=None,
            arguments=[],
            agents=[AGENT],
        )
        service.enregistrer(
            nom="inactif",
            transport="streamable_http",
            url="https://b.test/mcp",
            commande=None,
            arguments=[],
            agents=[AGENT],
            actif=False,
        )
        session.commit()

        noms = [s.nom for s in service.lister_pour_agent(AGENT)]
        assert noms == ["pour-generateur"]
        assert [s.nom for s in service.lister_pour_agent("agent_rh")] == []
    finally:
        session.close()


def test_mcp_suppression(api_session_factory: sessionmaker[Session]) -> None:
    session = api_session_factory()
    try:
        service = ServeurMcpService(session, actor_id="admin-1")
        service.enregistrer(
            nom="jetable",
            transport="stdio",
            url=None,
            commande="npx",
            arguments=["-y", "serveur"],
            agents=[],
        )
        session.commit()

        assert service.supprimer("jetable") is True
        session.commit()
        assert service.lister() == []
        assert service.supprimer("jetable") is False
    finally:
        session.close()


def test_mcp_catalogue_declare_le_support() -> None:
    """Le catalogue expose un état de support MCP honnête (installé ou non)."""
    from app.agents import mcp as module_mcp

    assert isinstance(module_mcp.paquet_disponible(), bool)


# --- 4. Routes d'administration -------------------------------------------------


def test_api_etat_reserve_a_l_admin(api_client: TestClient) -> None:
    """Sans administrateur, l'écran d'administration est refusé."""
    assert api_client.get("/api/v1/parametres/etat").status_code == 403


def test_api_etat_expose_agents_et_jamais_de_secret(
    api_client: TestClient,
) -> None:
    _brancher_admin(api_client)

    resp = api_client.get("/api/v1/parametres/etat")
    assert resp.status_code == 200, resp.text
    corps = resp.json()

    par_id = {item["agent_id"]: item for item in corps["agents"]}
    assert set(par_id) == {d.agent_id for d in AGENT_DEFINITIONS}
    assert par_id[AGENT]["outils_du_contrat"] == sorted(
        definition_du_contrat(AGENT).allowed_tools()
    )
    assert "api_key" not in json.dumps(corps)
    assert corps["connecteurs_mcp"] == []


def test_api_surcharge_refuse_outil_hors_contrat(api_client: TestClient) -> None:
    _brancher_admin(api_client)

    resp = api_client.put(
        f"/api/v1/parametres/agents/{AGENT}",
        json={
            "outils_desactives": ["outil_qui_n_existe_pas"],
            "instructions_supplementaires": None,
            "skills_ajoutes": [],
        },
    )
    assert resp.status_code == 422, resp.text
    corps = resp.json()
    details = corps["error"]["details"]
    assert details["outils_hors_contrat"] == ["outil_qui_n_existe_pas"]
    assert details["outils_du_contrat"]


def test_api_surcharge_puis_retour_au_contrat(api_client: TestClient) -> None:
    """Un réglage enregistré apparaît dans l'état, et disparaît quand on le retire."""
    _brancher_admin(api_client)
    outil = sorted(definition_du_contrat(AGENT).allowed_tools())[0]

    resp = api_client.put(
        f"/api/v1/parametres/agents/{AGENT}",
        json={
            "outils_desactives": [outil],
            "instructions_supplementaires": "Consigne d'administration.",
            "skills_ajoutes": [],
        },
    )
    assert resp.status_code == 200, resp.text

    etat = api_client.get("/api/v1/parametres/etat").json()
    agent = next(a for a in etat["agents"] if a["agent_id"] == AGENT)
    assert agent["surcharge_active"] is True
    assert outil not in agent["outils_effectifs"]
    assert agent["instructions_supplementaires"] == "Consigne d'administration."

    assert api_client.delete(f"/api/v1/parametres/agents/{AGENT}").status_code == 200

    etat = api_client.get("/api/v1/parametres/etat").json()
    agent = next(a for a in etat["agents"] if a["agent_id"] == AGENT)
    assert agent["surcharge_active"] is False
    assert set(agent["outils_effectifs"]) == set(agent["outils_du_contrat"])


def test_api_connecteur_mcp_cycle_complet(
    api_client: TestClient, cle_chiffrement: str
) -> None:
    """Déclarer, voir dans l'état (jamais la valeur), supprimer."""
    _brancher_admin(api_client)

    resp = api_client.put(
        "/api/v1/parametres/mcp/notion-carso",
        json={
            "transport": "streamable_http",
            "url": "https://mcp.exemple.test/mcp",
            "commande": None,
            "arguments": [],
            "agents": [AGENT],
            "en_tetes": {"Authorization": "Bearer jeton-secret"},
            "actif": True,
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["en_tetes"] == ["Authorization"]
    assert "jeton-secret" not in resp.text

    etat = api_client.get("/api/v1/parametres/etat").json()
    assert ("jeton-secret" not in json.dumps(etat))
    assert [c["nom"] for c in etat["connecteurs_mcp"]] == ["notion-carso"]
    agent = next(a for a in etat["agents"] if a["agent_id"] == AGENT)
    assert agent["connecteurs_mcp"] == ["notion-carso"]

    assert api_client.delete("/api/v1/parametres/mcp/notion-carso").status_code == 204
    assert api_client.get("/api/v1/parametres/etat").json()["connecteurs_mcp"] == []


def test_api_connecteur_mcp_transport_invalide(api_client: TestClient) -> None:
    _brancher_admin(api_client)

    resp = api_client.put(
        "/api/v1/parametres/mcp/x",
        json={
            "transport": "pigeon",
            "url": "https://exemple.test/mcp",
            "commande": None,
            "arguments": [],
            "agents": [],
            "en_tetes": None,
            "actif": True,
        },
    )
    assert resp.status_code == 422, resp.text


def test_api_connecteur_mcp_sans_chiffrement(
    api_client: TestClient, sans_chiffrement: None
) -> None:
    """Sans clé de chiffrement, l'état l'annonce et l'en-tête est refusé."""
    _brancher_admin(api_client)

    etat = api_client.get("/api/v1/parametres/etat").json()
    assert etat["chiffrement_disponible"] is False

    resp = api_client.put(
        "/api/v1/parametres/mcp/x",
        json={
            "transport": "streamable_http",
            "url": "https://exemple.test/mcp",
            "commande": None,
            "arguments": [],
            "agents": [],
            "en_tetes": {"Authorization": "Bearer clair"},
            "actif": True,
        },
    )
    assert resp.status_code == 422, resp.text


def test_api_skills_liste_la_racine(api_client: TestClient) -> None:
    _brancher_admin(api_client)

    resp = api_client.get("/api/v1/parametres/skills")
    assert resp.status_code == 200, resp.text
    assert "racine" in resp.json()
    assert isinstance(resp.json()["disponibles"], list)
