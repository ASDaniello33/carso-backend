"""Tests — skills d'organisation : écriture bornée, prompt global, surface HTTP.

Ce que ces tests verrouillent :

1. **le nom est canonique** — ``/Facturation Client`` et
   ``/facturation-client`` désignent le même skill, et un nom inutilisable est
   refusé avec sa forme attendue ;
2. **le prompt reçoit le texte** — le bloc est ce que le modèle lit : sans lui,
   la fonctionnalité n'existe pas ; vide sans skill ;
3. **l'empreinte change avec le contenu** — c'est elle qui reconstruit les
   graphes quand l'administration modifie un guide ;
4. **l'écriture est réservée à l'administrateur** — un formateur lit la liste
   (menu `/`) mais ne peut ni créer, ni modifier, ni supprimer : sinon il
   changerait le fonctionnement des agents ;
5. **la modification est partielle** — corriger une phrase ne réécrit pas le
   nom ni le reste du contenu.

Aucun réseau, aucune base externe : SQLite mémoire, comme le reste de la suite.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.agents.manager import _effectif_pour
from app.agents.skills_organisation import (
    INSTRUCTIONS_MAX,
    MAX_SKILLS,
    bloc_skills,
    signature_skills,
)
from app.application.services.skills_service import SkillsService, normaliser_nom
from app.core.errors import NotFoundError, ValidationError
from app.domain.document import AuditEvent
from app.domain.enums import ActionAudit
from app.domain.identity import Utilisateur
from app.domain.skill import Skill

PRÉFIXE = "/api/v1"


def _compte(role: str = "administrateur") -> Utilisateur:
    return Utilisateur(
        email=f"skill-{uuid4().hex[:8]}@carso.test",
        nom="Personne",
        prenom="Skill",
        password_hash="x",
        role=role,
        statut="actif",
    )


def _persister(api_session_factory: sessionmaker[Session], compte: Utilisateur) -> None:
    session = api_session_factory()
    try:
        session.add(compte)
        session.commit()
    finally:
        session.close()


def _brancher(api_client: TestClient, utilisateur: Utilisateur) -> None:
    from app.api.deps import get_current_user

    api_client.app.dependency_overrides[get_current_user] = lambda: utilisateur


def _skill(nom: str = "facturation", texte: str = "Toujours citer le lot.") -> Skill:
    return Skill(
        nom=nom,
        description="Pour les offres financières",
        instructions=texte,
    )


def _vider_skills(api_session_factory: sessionmaker[Session]) -> None:
    """Le repository est global : chaque test part d'une table vide."""
    session = api_session_factory()
    try:
        for ligne in session.scalars(select(Skill)):
            session.delete(ligne)
        session.commit()
    finally:
        session.close()


@pytest.fixture(autouse=True)
def _table_propre(api_session_factory: sessionmaker[Session]) -> None:
    _vider_skills(api_session_factory)


# --- 1. Nom canonique ---------------------------------------------------------


@pytest.mark.parametrize(
    ("saisi", "attendu"),
    [
        ("Facturation Client", "facturation-client"),
        ("  Été 2026  ", "ete-2026"),
        ("devis//rapide", "devis-rapide"),
        ("---", ""),
    ],
)
def test_nom_normalise(saisi: str, attendu: str) -> None:
    assert normaliser_nom(saisi) == attendu


# --- 2. Bloc de prompt --------------------------------------------------------


def test_bloc_vide_sans_skill() -> None:
    assert bloc_skills([]) == ""


def test_bloc_porte_nom_description_et_texte() -> None:
    bloc = bloc_skills([_skill()])
    assert "## Skills de l'organisation" in bloc
    assert "### /facturation — Pour les offres financières" in bloc
    assert "Toujours citer le lot." in bloc
    # Le bloc est replié par le prompt : la phrase peut être coupée en fin de
    # ligne, elle doit rester lisible une fois les sauts normalisés.
    assert "jamais un droit" in " ".join(bloc.split())


def test_bloc_coupe_un_texte_herite_trop_long() -> None:
    bloc = bloc_skills([_skill(texte="x" * (INSTRUCTIONS_MAX + 500))])
    assert "x" * INSTRUCTIONS_MAX in bloc
    assert "x" * (INSTRUCTIONS_MAX + 1) not in bloc


def test_bloc_ignore_un_skill_sans_texte() -> None:
    assert bloc_skills([_skill(texte="   ")]) == ""


# --- 3. Empreinte de graphe ---------------------------------------------------


def test_empreinte_change_avec_le_contenu() -> None:
    premier = _skill(texte="Version A")
    second = _skill(texte="Version B")
    assert signature_skills([premier]) != signature_skills([second])
    assert signature_skills([premier]) == signature_skills([premier])


def test_empreinte_vide_sans_skill() -> None:
    assert signature_skills([]) == "-"


# --- 4. Service ----------------------------------------------------------------


def test_enregistrer_normalise_et_trace(
    api_session_factory: sessionmaker[Session],
) -> None:
    session = api_session_factory()
    try:
        compte = _compte()
        session.add(compte)
        session.commit()
        service = SkillsService(session, actor_id=str(compte.id))

        skill = service.enregistrer(
            nom="Facturation Client",
            description="  Pour les offres  financières ",
            instructions="Toujours citer le lot et le montant.",
        )
        session.commit()

        assert skill.nom == "facturation-client"
        assert skill.description == "Pour les offres financières"
        evenement = session.scalars(
            select(AuditEvent).where(AuditEvent.action == ActionAudit.SKILL_CREE.value)
        ).first()
        assert evenement is not None
        assert evenement.after["nom"] == "facturation-client"
    finally:
        session.close()


def test_nom_deja_pris_refuse(api_session_factory: sessionmaker[Session]) -> None:
    session = api_session_factory()
    try:
        service = SkillsService(session)
        service.enregistrer(
            nom="devis", description="d", instructions="Contenu valide."
        )
        with pytest.raises(ValidationError) as erreur:
            service.enregistrer(
                nom="Devis", description="d", instructions="Autre texte."
            )
        assert "existe déjà" in str(erreur.value)
    finally:
        session.close()


def test_contenu_trop_long_refuse(api_session_factory: sessionmaker[Session]) -> None:
    session = api_session_factory()
    try:
        with pytest.raises(ValidationError) as erreur:
            SkillsService(session).enregistrer(
                nom="long",
                description="d",
                instructions="x" * (INSTRUCTIONS_MAX + 1),
            )
        assert str(INSTRUCTIONS_MAX) in str(erreur.value)
    finally:
        session.close()


def test_nombre_maximal_refuse(api_session_factory: sessionmaker[Session]) -> None:
    session = api_session_factory()
    try:
        service = SkillsService(session)
        for index in range(MAX_SKILLS):
            service.enregistrer(
                nom=f"skill-{index}",
                description="d",
                instructions="Contenu valide.",
            )
        with pytest.raises(ValidationError) as erreur:
            service.enregistrer(
                nom="trop", description="d", instructions="Contenu valide."
            )
        assert "Maximum" in str(erreur.value)
    finally:
        session.close()


def test_modifier_ne_touche_que_les_champs_fournis(
    api_session_factory: sessionmaker[Session],
) -> None:
    session = api_session_factory()
    try:
        compte = _compte()
        session.add(compte)
        session.commit()
        service = SkillsService(session, actor_id=str(compte.id))
        skill = service.enregistrer(
            nom="ton-carso",
            description="Quand rédiger",
            instructions="Vouvoie et reste concis.",
        )
        session.commit()

        corrige = service.modifier(
            skill.id, instructions="Vouvoie, reste concis, cite le lot."
        )
        session.commit()

        assert corrige.nom == "ton-carso"
        assert corrige.description == "Quand rédiger"
        assert corrige.instructions == "Vouvoie, reste concis, cite le lot."
        modifie = session.scalars(
            select(AuditEvent).where(AuditEvent.action == ActionAudit.SKILL_MODIFIE.value)
        ).first()
        assert modifie is not None
        assert modifie.before["instructions"] == "Vouvoie et reste concis."
        assert modifie.after["instructions"] == "Vouvoie, reste concis, cite le lot."
    finally:
        session.close()


def test_renommer_vers_un_nom_pris_est_refuse(
    api_session_factory: sessionmaker[Session],
) -> None:
    session = api_session_factory()
    try:
        service = SkillsService(session)
        service.enregistrer(nom="un", description="d", instructions="Premier texte.")
        second = service.enregistrer(
            nom="deux", description="d", instructions="Second texte."
        )
        with pytest.raises(ValidationError):
            service.modifier(second.id, nom="un")
        assert service.skills.get(second.id).nom == "deux"  # type: ignore[union-attr]
    finally:
        session.close()


def test_supprimer_un_skill_inconnu_est_un_404(
    api_session_factory: sessionmaker[Session],
) -> None:
    session = api_session_factory()
    try:
        with pytest.raises(NotFoundError):
            SkillsService(session).supprimer(uuid4())
        with pytest.raises(NotFoundError):
            SkillsService(session).modifier(uuid4(), description="d")
    finally:
        session.close()


# --- 5. Prompt effectif du runtime ---------------------------------------------


def test_le_prompt_effectif_porte_les_skills(
    api_session_factory: sessionmaker[Session],
) -> None:
    session = api_session_factory()
    try:
        compte = _compte()
        session.add(compte)
        session.commit()
        SkillsService(session).enregistrer(
            nom="ton-carso",
            description="Quand rédiger une réponse",
            instructions="Vouvoie et reste concis.",
        )
        session.commit()

        _, _, instructions = _effectif_pour(
            session, "agent_generaliste_readonly", compte.id
        )
        assert instructions is not None
        assert "### /ton-carso" in instructions
        assert "Vouvoie et reste concis." in instructions
    finally:
        session.close()


def test_sans_skill_aucun_bloc(api_session_factory: sessionmaker[Session]) -> None:
    session = api_session_factory()
    try:
        compte = _compte()
        session.add(compte)
        session.commit()
        _, _, instructions = _effectif_pour(
            session, "agent_generaliste_readonly", compte.id
        )
        assert instructions is None
    finally:
        session.close()


# --- 6. Surface HTTP -----------------------------------------------------------


def test_cycle_creation_modification_suppression(
    api_client: TestClient, api_session_factory: sessionmaker[Session]
) -> None:
    admin = _compte()
    _persister(api_session_factory, admin)
    _brancher(api_client, admin)

    creation = api_client.post(
        f"{PRÉFIXE}/agents/skills",
        json={
            "nom": "Ton Carso",
            "description": "Quand rédiger une réponse",
            "instructions": "Vouvoie et reste concis.",
        },
    )
    assert creation.status_code == 201, creation.text
    skill = creation.json()
    assert skill["nom"] == "ton-carso"

    correction = api_client.patch(
        f"{PRÉFIXE}/agents/skills/{skill['id']}",
        json={"instructions": "Vouvoie, reste concis, cite toujours le lot."},
    )
    assert correction.status_code == 200, correction.text
    assert correction.json()["nom"] == "ton-carso"
    assert "cite toujours le lot" in correction.json()["instructions"]

    liste = api_client.get(f"{PRÉFIXE}/agents/skills")
    assert liste.status_code == 200, liste.text
    assert [item["nom"] for item in liste.json()] == ["ton-carso"]

    supprime = api_client.delete(f"{PRÉFIXE}/agents/skills/{skill['id']}")
    assert supprime.status_code == 204, supprime.text

    vide = api_client.get(f"{PRÉFIXE}/agents/skills")
    assert vide.json() == []


def test_un_formateur_lit_mais_n_ecrit_pas(
    api_client: TestClient, api_session_factory: sessionmaker[Session]
) -> None:
    admin, formateur = _compte(), _compte("formateur")
    _persister(api_session_factory, admin)
    _persister(api_session_factory, formateur)

    session = api_session_factory()
    try:
        skill = SkillsService(session).enregistrer(
            nom="commun", description="d", instructions="Guide commun à tous."
        )
        session.commit()
        identifiant = skill.id
    finally:
        session.close()

    _brancher(api_client, formateur)
    # La lecture reste ouverte : c'est elle qui alimente le menu `/` du chat.
    assert api_client.get(f"{PRÉFIXE}/agents/skills").status_code == 200
    refus = api_client.post(
        f"{PRÉFIXE}/agents/skills",
        json={"nom": "perso", "description": "d", "instructions": "Contenu valide."},
    )
    assert refus.status_code == 403, refus.text
    assert api_client.patch(
        f"{PRÉFIXE}/agents/skills/{identifiant}", json={"description": "contourné"}
    ).status_code == 403
    assert api_client.delete(f"{PRÉFIXE}/agents/skills/{identifiant}").status_code == 403


def test_nom_invalide_rend_un_message_actionnable(
    api_client: TestClient, api_session_factory: sessionmaker[Session]
) -> None:
    admin = _compte()
    _persister(api_session_factory, admin)
    _brancher(api_client, admin)

    refus = api_client.post(
        f"{PRÉFIXE}/agents/skills",
        json={"nom": "!!!", "description": "d", "instructions": "Contenu valide."},
    )
    assert refus.status_code == 422, refus.text
    assert "Nom de skill" in refus.json()["error"]["message"]


def test_modifier_un_skill_inconnu_rend_un_404(
    api_client: TestClient, api_session_factory: sessionmaker[Session]
) -> None:
    admin = _compte()
    _persister(api_session_factory, admin)
    _brancher(api_client, admin)

    refus = api_client.patch(
        f"{PRÉFIXE}/agents/skills/{uuid4()}", json={"description": "d"}
    )
    assert refus.status_code == 404, refus.text
