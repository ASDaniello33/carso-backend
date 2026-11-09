"""Tests — skills ``SKILL.md`` écrits par l'administration (second paramètre).

Ce que ces tests verrouillent :

1. **la structure attendue par Deep Agents** — créer un skill produit
   ``<racine>/<nom>/SKILL.md`` avec un frontmatter ``name``/``description``
   exploitable, et le corps n'est **pas** dans ce frontmatter (il est lu à la
   demande, pas injecté dans le prompt) ;
2. **le rattachement fait le chargement automatique** — un skill rattaché à un
   agent entre dans ses ``skills`` effectifs (donc ``bloc_skills`` le liste et
   ``resoudre_skills`` le monte) ; le retrait le retire ;
3. **les skills du paquet sont protégés** — ils n'ont pas le marqueur
   d'origine : ni modification ni suppression depuis l'écran ;
4. **la suppression détache d'abord** — sinon un agent pointerait vers un
   dossier absent et son graphe refuserait de se monter ;
5. **le nom est canonique et immuable** — ``/Mon Guide`` devient ``mon-guide`` ;
   renommer est refusé (une surcharge citerait un dossier absent).

Aucun réseau : SQLite mémoire, racine de skills temporaire.
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.agents.registre_skills import (
    description_skill,
    lister_skills,
    racine_skills,
    resoudre_skills,
)
from app.agents.surcharges import definition_effective
from app.application.services.parametres_service import (
    ParametresAgentService,
    definition_du_contrat,
)
from app.application.services.skills_fichiers_service import SkillsFichiersService
from app.core.errors import NotFoundError, ValidationError
from app.domain.document import AuditEvent
from app.domain.enums import ActionAudit, RoleUtilisateur
from app.domain.identity import Utilisateur

PRÉFIXE = "/api/v1"
AGENT = "agent_generateur_offre"
AUTRE_AGENT = "agent_rh"


# --- Fixtures -------------------------------------------------------------------


@pytest.fixture
def racine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Racine de skills temporaire : la suite n'écrit jamais dans le paquet.

    Le patch porte sur le **module** ``registre_skills`` — y compris pour les
    fonctions importées par nom dans d'autres modules (le service tient ses
    propres références) : sans les deux, un test écrivait réellement dans
    ``backend/app/agents/skills``.
    """
    skills = tmp_path / "skills"
    skills.mkdir()
    monkeypatch.setattr(
        "app.agents.registre_skills.racine_skills", lambda settings=None: skills
    )
    monkeypatch.setattr(
        "app.application.services.skills_fichiers_service.racine_skills",
        lambda settings=None: skills,
    )
    monkeypatch.setattr(
        "app.application.services.skills_fichiers_service.lister_skills",
        lambda settings=None: sorted(
            enfant.name
            for enfant in skills.iterdir()
            if enfant.is_dir() and (enfant / "SKILL.md").is_file()
        ),
    )
    return skills


def _admin() -> Utilisateur:
    return Utilisateur(
        email=f"skillf-{uuid4().hex[:8]}@carso.test",
        nom="Admin",
        prenom="Skill",
        password_hash="x",
        role=RoleUtilisateur.ADMINISTRATEUR.value,
        statut="actif",
    )


def _persister(session_factory: sessionmaker[Session], compte: Utilisateur) -> None:
    session = session_factory()
    try:
        session.add(compte)
        session.commit()
    finally:
        session.close()


@pytest.fixture
def rechargements(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, ...]]:
    """Espionne ``manager.reload`` au lieu de reconstruire tous les graphes.

    Le contrat de la route est *de recharger* le runtime après une écriture ;
    le reconstruire réellement demanderait un modèle et une base complète pour
    ne rien vérifier de plus ici. L'espion garde l'assertion exacte — la route a
    bien invalidé les graphes — sans payer 50 secondes par test.
    """
    from app.agents.manager import get_runtime_manager

    appels: list[tuple[str, ...]] = []
    manager = get_runtime_manager()
    monkeypatch.setattr(
        manager, "reload", lambda: (appels.append(tuple(manager.agent_ids())) or ())
    )
    return appels


def _client(api_client: TestClient, api_session_factory: sessionmaker[Session]) -> TestClient:
    """Client dont l'authentification rend un administrateur.

    Comme le reste de la suite : on remplace la dépendance ``get_current_user``
    plutôt que d'inventer un en-tête que le backend ne lit pas.
    """
    from app.api.deps import get_current_user

    compte = _admin()
    _persister(api_session_factory, compte)
    api_client.app.dependency_overrides[get_current_user] = lambda: compte
    return api_client


def _actes(session: Session) -> list[str]:
    from sqlalchemy import select

    return [
        ligne.action
        for ligne in session.scalars(select(AuditEvent).order_by(AuditEvent.created_at))
    ]


# --- Service : structure et contenu ---------------------------------------------


def test_creer_ecrit_un_skill_md_lisible_par_deep_agents(
    api_session_factory: sessionmaker[Session], racine: Path
) -> None:
    """Le fichier produit doit être exactement ce que le registre sait lire."""
    session = api_session_factory()
    try:
        service = SkillsFichiersService(session, actor_id="test")
        skill = service.creer(
            nom="Mon Guide",
            description="À charger pour rédiger une offre financière",
            corps="# Méthode\n\nCommencer par le lot, puis le montant.",
        )
        session.flush()
    finally:
        session.close()

    # Le nom saisi est normalisé : c'est aussi le nom du dossier.
    assert skill["nom"] == "mon-guide"
    manifeste = racine / "mon-guide" / "SKILL.md"
    assert manifeste.is_file()

    texte = manifeste.read_text(encoding="utf-8")
    assert texte.startswith("---\n")
    assert "name: mon-guide" in texte
    # La description est un scalaire plié : elle survit aux deux-points.
    assert "description: >" in texte
    assert description_skill(manifeste) == "À charger pour rédiger une offre financière"
    # Le corps n'est pas dans le frontmatter : l'agent le lit à la demande.
    assert "Commencer par le lot" in texte.split("---", 2)[2]

    # Le registre résout le dossier et le skill est listé.
    assert "mon-guide" in lister_skills()
    assert resoudre_skills(["mon-guide"])


def test_l_apercu_est_le_texte_exact_du_fichier(
    api_session_factory: sessionmaker[Session], racine: Path
) -> None:
    """L'aperçu ne recompose rien : il rend le fichier tel que l'agent le lit.

    Un rendu fabriqué depuis les champs du formulaire pourrait diverger du
    fichier (ordre des clés, pliage de la description) — et l'aperçu perdrait
    son seul intérêt : vérifier avant d'enregistrer.
    """
    session = api_session_factory()
    try:
        service = SkillsFichiersService(session)
        service.creer(
            nom="guide-offre",
            description="À charger pour rédiger une offre",
            corps="# Méthode\n\nCommencer par le lot.",
        )
        session.flush()
        detail = service.lire("guide-offre")
    finally:
        session.close()

    fichier = (racine / "guide-offre" / "SKILL.md").read_text(encoding="utf-8")
    assert detail["apercu"] == fichier
    # Le frontmatter est bien dans l'aperçu : c'est ce que voit l'agent au montage.
    assert detail["apercu"].startswith("---\nname: guide-offre")
    assert "carso_origine: admin" in str(detail["apercu"])


def test_creer_refuse_un_nom_deja_pris_et_un_contenu_vide(
    api_session_factory: sessionmaker[Session], racine: Path
) -> None:
    """Nom déjà pris et corps trop court sont refusés avec un message utile."""
    session = api_session_factory()
    try:
        service = SkillsFichiersService(session)
        service.creer(
            nom="guide-offre",
            description="Quand rédiger une offre",
            corps="Contenu suffisamment long pour être accepté.",
        )
        session.flush()
        with pytest.raises(ValidationError) as erreur:
            service.creer(
                nom="Guide Offre",
                description="Doublon",
                corps="Contenu suffisamment long pour être accepté.",
            )
        assert "existe déjà" in erreur.value.message

        with pytest.raises(ValidationError):
            service.creer(nom="court", description="x", corps="trop court")
    finally:
        session.close()

    # Aucun dossier partiel n'est resté derrière.
    assert sorted(p.name for p in racine.iterdir()) == ["guide-offre"]


def test_modifier_conserve_le_nom_et_refuse_un_skill_du_paquet(
    api_session_factory: sessionmaker[Session], racine: Path
) -> None:
    """Le nom est immuable ; un skill livré avec le paquet reste en lecture seule."""
    paquet = racine / "docx-officiels"
    paquet.mkdir()
    (paquet / "SKILL.md").write_text(
        "---\nname: docx-officiels\ndescription: Modèle DOCX\n---\n\n# Corps\n",
        encoding="utf-8",
    )

    session = api_session_factory()
    try:
        service = SkillsFichiersService(session)
        service.creer(
            nom="guide-offre",
            description="Première formulation",
            corps="Contenu suffisamment long pour être accepté.",
        )
        session.flush()

        corrige = service.modifier(
            "guide-offre",
            description="Deuxième formulation",
            corps="Nouveau corps, lui aussi suffisamment long.",
        )
        assert corrige["nom"] == "guide-offre"
        assert corrige["description"] == "Deuxième formulation"
        assert "Nouveau corps" in str(corrige["corps"])

        with pytest.raises(ValidationError) as erreur:
            service.modifier("docx-officiels", description="Tentative")
        assert "paquet" in erreur.value.message

        with pytest.raises(ValidationError):
            service.supprimer("docx-officiels")
        assert (paquet / "SKILL.md").is_file()
    finally:
        session.close()


def test_un_skill_sans_marqueur_reste_du_paquet(
    api_session_factory: sessionmaker[Session], racine: Path
) -> None:
    """Sans ``carso_origine``, un dossier n'est jamais modifiable depuis l'écran."""
    dossier = racine / "depose-a-la-main"
    dossier.mkdir()
    (dossier / "SKILL.md").write_text(
        "---\nname: depose-a-la-main\ndescription: Déposé à la main\n---\n\n# Corps\n",
        encoding="utf-8",
    )

    session = api_session_factory()
    try:
        service = SkillsFichiersService(session)
        assert service.lire("depose-a-la-main")["origine"] == "paquet"
        with pytest.raises(ValidationError):
            service.supprimer("depose-a-la-main")
    finally:
        session.close()


# --- Rattachement : c'est lui qui rend le skill automatique ----------------------


def test_le_rattachement_entre_dans_les_skills_effectifs(
    api_session_factory: sessionmaker[Session], racine: Path
) -> None:
    """Un skill rattaché est chargé par l'agent, sans que l'utilisateur tape `/`."""
    session = api_session_factory()
    try:
        service = SkillsFichiersService(session, actor_id="test")
        service.creer(
            nom="guide-offre",
            description="Charger pour rédiger une offre",
            corps="Contenu suffisamment long pour être accepté.",
            agents=[AGENT],
        )
        session.flush()

        surcharge = ParametresAgentService(session).surcharge(AGENT)
        assert surcharge is not None
        assert "guide-offre" in surcharge.skills_ajoutes
        definition = definition_effective(definition_du_contrat(AGENT), surcharge)
        assert "guide-offre" in definition.skills
        # L'agent qui ne le reçoit pas n'a pas de surcharge créée pour rien.
        assert ParametresAgentService(session).surcharge(AUTRE_AGENT) is None

        # Décocher retire le skill, et la surcharge — devenue vide — est désactivée.
        corrige = service.modifier("guide-offre", agents=[])
        session.flush()
        assert corrige["agents"] == []
        assert ParametresAgentService(session).surcharge(AGENT) is None
    finally:
        session.close()


def test_le_prompt_de_lagent_liste_nom_et_description(
    api_session_factory: sessionmaker[Session], racine: Path
) -> None:
    """Au démarrage du runtime, l'agent reçoit le nom **et** la description.

    C'est la moitié « automatique » du contrat : l'agent sait quelles méthodes
    existent et quand les charger, sans qu'on tape `/nom` dans le message.
    """
    from app.agents.harness import build_system_prompt

    session = api_session_factory()
    try:
        SkillsFichiersService(session, actor_id="test").creer(
            nom="analyse-offre",
            description="Pour extraire les lots d'un appel à proposition",
            corps="Contenu suffisamment long pour être accepté.",
            agents=[AGENT],
        )
        session.flush()
        surcharge = ParametresAgentService(session).surcharge(AGENT)
        assert surcharge is not None
        definition = definition_effective(definition_du_contrat(AGENT), surcharge)
        prompt = build_system_prompt(definition)
    finally:
        session.close()

    assert "## Skills disponibles" in prompt
    assert "- analyse-offre : Pour extraire les lots d'un appel à proposition" in prompt
    # Le corps n'est **pas** dans le prompt : il est lu à la demande.
    assert "Contenu suffisamment long" not in prompt


def test_supprimer_detache_avant_effacer(
    api_session_factory: sessionmaker[Session], racine: Path
) -> None:
    """L'ordre compte : sinon l'agent pointerait vers un skill absent."""
    session = api_session_factory()
    try:
        service = SkillsFichiersService(session, actor_id="test")
        service.creer(
            nom="guide-offre",
            description="Charger pour rédiger une offre",
            corps="Contenu suffisamment long pour être accepté.",
            agents=[AGENT],
        )
        session.flush()

        service.supprimer("guide-offre")
        session.flush()

        assert not (racine / "guide-offre").exists()
        assert "guide-offre" not in lister_skills()
        # Plus aucune surcharge ne cite le skill : le montage des agents reste sain.
        assert ParametresAgentService(session).surcharge(AGENT) is None

        with pytest.raises(NotFoundError):
            service.supprimer("guide-offre")
    finally:
        session.close()


def test_rattacher_un_agent_inconnu_est_refuse(
    api_session_factory: sessionmaker[Session], racine: Path
) -> None:
    """Un identifiant fantôme laisserait croire que le skill est chargé."""
    session = api_session_factory()
    try:
        with pytest.raises(NotFoundError):
            SkillsFichiersService(session).creer(
                nom="guide-offre",
                description="Charger pour rédiger une offre",
                corps="Contenu suffisamment long pour être accepté.",
                agents=["agent_qui_n_existe_pas"],
            )
    finally:
        session.close()


# --- Audit -----------------------------------------------------------------------


def test_les_ecritures_sont_tracees(
    api_session_factory: sessionmaker[Session], racine: Path
) -> None:
    """Créer puis supprimer laisse deux événements d'audit lisibles."""
    session = api_session_factory()
    try:
        service = SkillsFichiersService(session, actor_id="test")
        service.creer(
            nom="guide-offre",
            description="Charger pour rédiger une offre",
            corps="Contenu suffisamment long pour être accepté.",
            agents=[AGENT],
        )
        service.supprimer("guide-offre")
        session.flush()
        actes = _actes(session)
    finally:
        session.close()

    assert ActionAudit.SKILL_AGENT_ENREGISTRE.value in actes
    assert ActionAudit.SKILL_SUPPRIME.value in actes


# --- Surface HTTP -----------------------------------------------------------------


def test_api_lister_creer_corriger_supprimer(
    api_client: TestClient,
    api_session_factory: sessionmaker[Session],
    racine: Path,
    rechargements: list[tuple[str, ...]],
) -> None:
    """Le parcours complet de l'écran, du vide à la suppression."""
    client = _client(api_client, api_session_factory)

    assert client.get(f"{PRÉFIXE}/parametres/skills/fichiers").json() == []

    creation = client.post(
        f"{PRÉFIXE}/parametres/skills/fichiers",
        json={
            "nom": "guide-offre",
            "description": "À charger pour rédiger une offre",
            "corps": "# Méthode\n\nCommencer par le lot.",
            "agents": [AGENT],
        },
    )
    assert creation.status_code == 201, creation.text
    assert creation.json()["origine"] == "admin"
    assert creation.json()["agents"] == [AGENT]
    assert (racine / "guide-offre" / "SKILL.md").is_file()

    liste = client.get(f"{PRÉFIXE}/parametres/skills/fichiers").json()
    assert [element["nom"] for element in liste] == ["guide-offre"]
    assert liste[0]["description"] == "À charger pour rédiger une offre"

    detail = client.get(f"{PRÉFIXE}/parametres/skills/fichiers/guide-offre")
    assert detail.status_code == 200
    assert "Commencer par le lot" in detail.json()["corps"]
    # L'aperçu est servi par l'API : c'est le texte du fichier, frontmatter inclus.
    assert detail.json()["apercu"].startswith("---\nname: guide-offre")

    correction = client.patch(
        f"{PRÉFIXE}/parametres/skills/fichiers/guide-offre",
        json={"corps": "# Méthode\n\nCommencer par le montant."},
    )
    assert correction.status_code == 200, correction.text
    # La description absente est conservée : corriger le corps ne l'efface pas.
    assert correction.json()["description"] == "À charger pour rédiger une offre"

    suppression = client.delete(f"{PRÉFIXE}/parametres/skills/fichiers/guide-offre")
    assert suppression.status_code == 204
    assert client.get(f"{PRÉFIXE}/parametres/skills/fichiers").json() == []

    # Trois écritures, trois rechargements : un skill changé ne doit jamais
    # rester servi par un graphe mémorisé.
    assert len(rechargements) == 3


def test_api_refuse_un_skill_du_paquet(
    api_client: TestClient,
    api_session_factory: sessionmaker[Session],
    racine: Path,
    rechargements: list[tuple[str, ...]],
) -> None:
    """Un skill livré avec le paquet ne se modifie pas depuis l'écran."""
    dossier = racine / "docx-officiels"
    dossier.mkdir()
    (dossier / "SKILL.md").write_text(
        "---\nname: docx-officiels\ndescription: Modèle DOCX\n---\n\n# Corps\n",
        encoding="utf-8",
    )
    client = _client(api_client, api_session_factory)

    reponse = client.patch(
        f"{PRÉFIXE}/parametres/skills/fichiers/docx-officiels",
        json={"description": "Tentative"},
    )
    assert reponse.status_code == 422
    assert reponse.json()["error"]["code"] == "business_validation_error"
    assert client.delete(f"{PRÉFIXE}/parametres/skills/fichiers/docx-officiels").status_code == 422
