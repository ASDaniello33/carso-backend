"""AUTH — inscription pending, login refusé, activation admin, JWT, profil."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.sql.ddl import CreateColumn

from app.application.security import hasher_mot_de_passe, verifier_mot_de_passe
from app.application.services import UtilisateurService
from app.core.config import Settings
from app.core.errors import (
    PermissionDeniedError,
    ValidationError,
)
from app.domain.base import Base
from app.domain.document import AuditEvent
from app.domain.enums import ActionAudit, RoleUtilisateur, StatutUtilisateur
from app.domain.execution import Equipe
from app.domain.identity import CodeReinitialisation, Utilisateur


def _equipe(session: Session, email: str, *, role_compte: str | None = None) -> Equipe:
    """Personne du vivier active — prérequis de toute inscription (incrément 24)."""
    nom, _, domaine = email.partition("@")
    personne = Equipe(
        nom=nom.capitalize(),
        prenom="Test",
        email=email,
        statut="actif",
        role_compte=role_compte,
    )
    session.add(personne)
    session.flush()
    return personne


@compiles(JSONB, "sqlite")
def _jsonb_as_json(type_: JSONB, compiler: object, **kw: object) -> str:
    return "JSON"


@compiles(CreateColumn, "sqlite")
def _strip_pg(element: CreateColumn, compiler: object, **kw: object) -> str:
    ddl = compiler.visit_create_column(element)  # type: ignore[attr-defined]
    return re.sub(r"DEFAULT gen_random_uuid\(\)", "", ddl)


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as s:
        yield s
    engine.dispose()


def _settings() -> Settings:
    return Settings(_env_file=None, auth_secret="secret-test-auth-carso-32b")


def test_hash_et_verif() -> None:
    h = hasher_mot_de_passe("motdepasse1")
    assert verifier_mot_de_passe("motdepasse1", h)
    assert not verifier_mot_de_passe("autre", h)
    assert "motdepasse1" not in h


def test_inscription_pending(session: Session) -> None:
    svc = UtilisateurService(session)
    _equipe(session, "a@carso.ma")
    user = svc.inscrire(
        email="a@carso.ma", nom="A", prenom="B", mot_de_passe="motdepasse1"
    )
    assert user.equipe_id is not None
    assert user.nom == "A"  # repris du vivier, pas de la saisie
    assert user.statut == StatutUtilisateur.PENDING.value
    assert user.role == RoleUtilisateur.EN_ATTENTE.value
    with pytest.raises(PermissionDeniedError, match="attente"):
        svc.authentifier(email="a@carso.ma", mot_de_passe="motdepasse1", settings=_settings())


def test_activation_puis_login(session: Session) -> None:
    svc = UtilisateurService(session)
    admin = Utilisateur(
        email="admin@carso.ma",
        nom="Ad",
        prenom="Min",
        password_hash=hasher_mot_de_passe("adminpass1"),
        role=RoleUtilisateur.ADMINISTRATEUR.value,
        statut=StatutUtilisateur.ACTIF.value,
    )
    session.add(admin)
    session.flush()
    _equipe(session, "col@carso.ma")
    user = svc.inscrire(
        email="col@carso.ma", nom="C", prenom="D", mot_de_passe="colpass12"
    )
    svc.activer(user.id, admin_id=admin.id)
    assert user.statut == StatutUtilisateur.ACTIF.value
    assert user.role == RoleUtilisateur.COLLABORATEUR.value
    jeton = svc.authentifier(
        email="col@carso.ma", mot_de_passe="colpass12", settings=_settings()
    )
    assert jeton.count(".") == 2
    relu = svc.depuis_jeton(jeton, _settings())
    assert relu.id == user.id


# --- File de validation des inscriptions (page admin /utilisateurs) -------------


def test_lister_demandes_ne_renvoie_que_le_pending(session: Session) -> None:
    svc = UtilisateurService(session)
    _equipe(session, "d1@carso.ma")
    _equipe(session, "d2@carso.ma")
    svc.inscrire(email="d1@carso.ma", nom="Dem", prenom="Une", mot_de_passe="motdepasse1")
    svc.inscrire(email="d2@carso.ma", nom="Dem", prenom="Deux", mot_de_passe="motdepasse1")
    actif = Utilisateur(
        email="actif@carso.ma",
        nom="Compte",
        prenom="Actif",
        password_hash=hasher_mot_de_passe("motdepasse1"),
        role=RoleUtilisateur.COLLABORATEUR.value,
        statut=StatutUtilisateur.ACTIF.value,
    )
    session.add(actif)
    session.flush()

    demandes = svc.lister_demandes()
    assert [u.email for u in demandes] == ["d1@carso.ma", "d2@carso.ma"]  # ordre d'arrivée


def test_validation_avec_role_formateur(session: Session) -> None:
    """L'admin choisit le rôle à la validation : formateur = accès restreint."""
    svc = UtilisateurService(session)
    admin = Utilisateur(
        email="admin@carso.ma",
        nom="Ad",
        prenom="Min",
        password_hash=hasher_mot_de_passe("adminpass1"),
        role=RoleUtilisateur.ADMINISTRATEUR.value,
        statut=StatutUtilisateur.ACTIF.value,
    )
    session.add(admin)
    session.flush()
    _equipe(session, "f@carso.ma")
    user = svc.inscrire(email="f@carso.ma", nom="For", prenom="Mateur", mot_de_passe="motdepasse1")

    svc.activer(user.id, admin_id=admin.id, role=RoleUtilisateur.FORMATEUR.value)
    assert user.statut == StatutUtilisateur.ACTIF.value
    assert user.role == RoleUtilisateur.FORMATEUR.value
    # Le JWT porte le rôle choisi : le formateur n'obtient pas un accès équipe interne.
    jeton = svc.authentifier(email="f@carso.ma", mot_de_passe="motdepasse1", settings=_settings())
    relu = svc.depuis_jeton(jeton, _settings())
    assert relu.role == RoleUtilisateur.FORMATEUR.value


def test_validation_role_invalide_refuse(session: Session) -> None:
    svc = UtilisateurService(session)
    admin = Utilisateur(
        email="admin@carso.ma",
        nom="Ad",
        prenom="Min",
        password_hash=hasher_mot_de_passe("adminpass1"),
        role=RoleUtilisateur.ADMINISTRATEUR.value,
        statut=StatutUtilisateur.ACTIF.value,
    )
    session.add(admin)
    session.flush()
    _equipe(session, "x@carso.ma")
    user = svc.inscrire(email="x@carso.ma", nom="X", prenom="Y", mot_de_passe="motdepasse1")

    with pytest.raises(ValidationError, match="invalide"):
        svc.activer(user.id, admin_id=admin.id, role=RoleUtilisateur.ADMINISTRATEUR.value)


def test_refus_retire_la_demande_et_trace(session: Session) -> None:
    """Le refus supprime le compte pending (aucune FK métier) et trace l'email."""
    svc = UtilisateurService(session)
    admin = Utilisateur(
        email="admin@carso.ma",
        nom="Ad",
        prenom="Min",
        password_hash=hasher_mot_de_passe("adminpass1"),
        role=RoleUtilisateur.ADMINISTRATEUR.value,
        statut=StatutUtilisateur.ACTIF.value,
    )
    session.add(admin)
    session.flush()
    _equipe(session, "refuse@carso.ma")
    user = svc.inscrire(
        email="refuse@carso.ma", nom="Dem", prenom="Refuse", mot_de_passe="motdepasse1"
    )

    svc.refuser(user.id)
    session.flush()

    assert session.get(Utilisateur, user.id) is None
    assert svc.lister_demandes() == []
    # L'email redevient inscriptible (correction et nouvelle tentative).
    recreated = svc.inscrire(
        email="refuse@carso.ma", nom="Dem", prenom="Refuse", mot_de_passe="motdepasse2"
    )
    assert recreated.id != user.id
    # Traçabilité : l'événement de refus porte l'identité de la demande.
    evenements = list(
        session.scalars(
            select(AuditEvent).where(AuditEvent.action == ActionAudit.UTILISATEUR_REFUSE.value)
        )
    )
    assert len(evenements) == 1
    assert evenements[0].after["email"] == "refuse@carso.ma"


def test_refus_impossible_sur_compte_actif(session: Session) -> None:
    svc = UtilisateurService(session)
    actif = Utilisateur(
        email="actif2@carso.ma",
        nom="Compte",
        prenom="Actif",
        password_hash=hasher_mot_de_passe("motdepasse1"),
        role=RoleUtilisateur.COLLABORATEUR.value,
        statut=StatutUtilisateur.ACTIF.value,
    )
    session.add(actif)
    session.flush()
    with pytest.raises(ValidationError, match="en attente"):
        svc.refuser(actif.id)


# --- Gestion des comptes existants (page Utilisateurs, admin) -----------------


def test_lister_comptes_exclut_les_demandes_et_filtre(session: Session) -> None:
    svc = UtilisateurService(session)
    _equipe(session, "file@carso.ma")
    svc.inscrire(email="file@carso.ma", nom="Demande", prenom="En File", mot_de_passe="motdepasse1")
    _actif(session, nom="Daho", prenom="Amine")
    _actif(session, nom="Alaoui", prenom="Sara")

    comptes = svc.lister_comptes()
    emails = [u.email for u in comptes]
    assert "file@carso.ma" not in emails
    assert len(comptes) == 2

    filtrés = svc.lister_comptes(recherche="DAHO")
    assert [u.nom for u in filtrés] == ["Daho"]
    # La recherche porte aussi sur l'email et le prénom.
    assert len(svc.lister_comptes(recherche="sara")) == 1
    assert len(svc.lister_comptes(recherche="carso.ma")) == 2
    # Recherche blanche = tous les comptes.
    assert len(svc.lister_comptes(recherche="   ")) == 2


def test_changer_role_change_et_trace(session: Session) -> None:
    user = _actif(session)

    UtilisateurService(session, actor_id="admin-1").changer_role(
        user.id, role=RoleUtilisateur.FORMATEUR.value, admin_id=uuid4()
    )

    assert user.role == RoleUtilisateur.FORMATEUR.value
    evenement = session.scalars(
        select(AuditEvent).where(
            AuditEvent.action == ActionAudit.UTILISATEUR_MODIFIE.value,
            AuditEvent.entity_id == str(user.id),
        )
    ).one()
    assert evenement.event_metadata["avant"] == {"role": RoleUtilisateur.COLLABORATEUR.value}
    assert evenement.after == {"role": RoleUtilisateur.FORMATEUR.value}


def test_changer_role_refuse_hors_choix_valide(session: Session) -> None:
    user = _actif(session)

    for role_interdit in (RoleUtilisateur.ADMINISTRATEUR.value, RoleUtilisateur.EN_ATTENTE.value):
        with pytest.raises(ValidationError, match="invalide"):
            UtilisateurService(session).changer_role(
                user.id, role=role_interdit, admin_id=uuid4()
            )


def test_changer_role_refuse_sur_demande_en_attente(session: Session) -> None:
    svc = UtilisateurService(session)
    _equipe(session, "attente@carso.ma")
    demande = svc.inscrire(
        email="attente@carso.ma", nom="X", prenom="Y", mot_de_passe="motdepasse1"
    )

    with pytest.raises(ValidationError, match="en attente"):
        svc.changer_role(
            demande.id, role=RoleUtilisateur.COLLABORATEUR.value, admin_id=uuid4()
        )


def test_changer_role_refuse_son_propre_compte(session: Session) -> None:
    admin = Utilisateur(
        email="admin@carso.ma",
        nom="Ad",
        prenom="Min",
        password_hash=hasher_mot_de_passe("adminpass1"),
        role=RoleUtilisateur.ADMINISTRATEUR.value,
        statut=StatutUtilisateur.ACTIF.value,
    )
    session.add(admin)
    session.flush()

    with pytest.raises(ValidationError, match="propre compte"):
        UtilisateurService(session).changer_role(
            admin.id,
            role=RoleUtilisateur.FORMATEUR.value,
            admin_id=admin.id,
        )


def test_supprimer_compte_retire_et_trace(session: Session) -> None:
    admin = Utilisateur(
        email="admin@carso.ma",
        nom="Ad",
        prenom="Min",
        password_hash=hasher_mot_de_passe("adminpass1"),
        role=RoleUtilisateur.ADMINISTRATEUR.value,
        statut=StatutUtilisateur.ACTIF.value,
    )
    session.add(admin)
    session.flush()
    user = _actif(session, nom="Cible", prenom="Supprime")

    UtilisateurService(session, actor_id=str(admin.id)).supprimer(
        user.id, admin_id=admin.id
    )
    session.flush()

    assert session.get(Utilisateur, user.id) is None
    evenement = session.scalars(
        select(AuditEvent).where(
            AuditEvent.action == ActionAudit.UTILISATEUR_SUPPRIME.value
        )
    ).one()
    assert evenement.after["email"] == "cible-supprime@carso.ma"
    assert evenement.after["decision"] == "supprime"


def test_supprimer_refuse_son_propre_compte(session: Session) -> None:
    admin = Utilisateur(
        email="admin@carso.ma",
        nom="Ad",
        prenom="Min",
        password_hash=hasher_mot_de_passe("adminpass1"),
        role=RoleUtilisateur.ADMINISTRATEUR.value,
        statut=StatutUtilisateur.ACTIF.value,
    )
    session.add(admin)
    session.flush()

    with pytest.raises(ValidationError, match="propre compte"):
        UtilisateurService(session, actor_id=str(admin.id)).supprimer(
            admin.id, admin_id=admin.id
        )


def test_supprimer_refuse_le_dernier_admin(session: Session) -> None:
    admin = Utilisateur(
        email="admin@carso.ma",
        nom="Ad",
        prenom="Min",
        password_hash=hasher_mot_de_passe("adminpass1"),
        role=RoleUtilisateur.ADMINISTRATEUR.value,
        statut=StatutUtilisateur.ACTIF.value,
    )
    session.add(admin)
    session.flush()
    cible = Utilisateur(
        email="admin2@carso.ma",
        nom="Deux",
        prenom="Admin",
        password_hash=hasher_mot_de_passe("adminpass1"),
        role=RoleUtilisateur.ADMINISTRATEUR.value,
        statut=StatutUtilisateur.SUSPENDU.value,
    )
    session.add(cible)
    session.flush()

    # Le second admin est suspendu : il n'y a qu'un administrateur actif.
    # (L'auto-suppression, elle, est refusée en premier garde — voir test dédié.)
    with pytest.raises(ValidationError, match="dernier compte administrateur"):
        UtilisateurService(session).supprimer(admin.id, admin_id=uuid4())

    # Avec un second admin actif, la suppression passe.
    troisieme = Utilisateur(
        email="admin3@carso.ma",
        nom="Trois",
        prenom="Admin",
        password_hash=hasher_mot_de_passe("adminpass1"),
        role=RoleUtilisateur.ADMINISTRATEUR.value,
        statut=StatutUtilisateur.ACTIF.value,
    )
    session.add(troisieme)
    session.flush()
    UtilisateurService(session).supprimer(admin.id, admin_id=troisieme.id)
    session.flush()
    assert session.get(Utilisateur, admin.id) is None


def test_reinitialisation_mot_de_passe_par_admin(session: Session) -> None:
    user = _actif(session)
    ancien_hash = user.password_hash

    UtilisateurService(session, actor_id="admin-1").reinitialiser_mot_de_passe(
        user.id, admin_id=uuid4(), nouveau_mot_de_passe="nouveaupass1"
    )

    assert verifier_mot_de_passe("nouveaupass1", user.password_hash)
    assert not verifier_mot_de_passe("motdepasse1", user.password_hash)
    assert user.password_hash != ancien_hash
    evenement = session.scalars(
        select(AuditEvent).where(
            AuditEvent.action
            == ActionAudit.UTILISATEUR_MOT_DE_PASSE_REINITIALISE.value
        )
    ).one()
    # Ni mot de passe, ni hash dans la trace (AGENTS.md §9).
    trace = f"{evenement.after} {evenement.event_metadata}"
    assert "nouveaupass1" not in trace
    assert ancien_hash not in trace


def test_reinitialisation_refuse_trop_court_ou_identique(session: Session) -> None:
    user = _actif(session)
    svc = UtilisateurService(session)

    with pytest.raises(ValidationError, match="8 caractères"):
        svc.reinitialiser_mot_de_passe(user.id, admin_id=uuid4(), nouveau_mot_de_passe="court")

    with pytest.raises(ValidationError, match="différent"):
        svc.reinitialiser_mot_de_passe(
            user.id, admin_id=uuid4(), nouveau_mot_de_passe="motdepasse1"
        )


def test_api_gestion_comptes_bout_en_bout(
    api_client: TestClient, api_session_factory: sessionmaker[Session]
) -> None:
    """Routes admin : liste, recherche, rôle, suspension+réactivation, reset mdp."""
    from app.api.deps import get_current_user

    with api_session_factory() as preparation:
        admin = Utilisateur(
            email="admin-bt@carso.ma",
            nom="Ad",
            prenom="Min",
            password_hash=hasher_mot_de_passe("adminpass1"),
            role=RoleUtilisateur.ADMINISTRATEUR.value,
            statut=StatutUtilisateur.ACTIF.value,
        )
        preparation.add(admin)
        preparation.flush()
        admin_ref = str(admin.id)
        cible = Utilisateur(
            email="cible@carso.ma",
            nom="Cible",
            prenom="Compte",
            password_hash=hasher_mot_de_passe("motdepasse1"),
            role=RoleUtilisateur.COLLABORATEUR.value,
            statut=StatutUtilisateur.ACTIF.value,
        )
        preparation.add(cible)
        preparation.commit()
        cible_id = cible.id

    api_client.app.dependency_overrides[get_current_user] = lambda: admin

    # Liste + recherche : le tri est par création décroissante, la cible
    # (créée en second) figure seule pour ce filtre.
    reponse = api_client.get("/api/v1/auth/utilisateurs", params={"recherche": "cible"})
    assert reponse.status_code == 200, reponse.text
    corps = reponse.json()
    assert [u["email"] for u in corps] == ["cible@carso.ma"]

    # Changement de rôle.
    role = api_client.patch(
        f"/api/v1/auth/utilisateurs/{cible_id}/role", json={"role": "formateur"}
    )
    assert role.status_code == 200, role.text
    assert role.json()["role"] == "formateur"

    # Réinitialisation de mot de passe.
    reset = api_client.post(
        f"/api/v1/auth/utilisateurs/{cible_id}/reinitialisation-mot-de-passe",
        json={"nouveau_mot_de_passe": "nouveaupass1"},
    )
    assert reset.status_code == 200, reset.text
    with api_session_factory() as verification:
        rangee = verification.get(Utilisateur, cible_id)
        assert rangee is not None
        assert verifier_mot_de_passe("nouveaupass1", rangee.password_hash)

    # Suspension puis réactivation (l'activation réactive un compte suspendu).
    suspension = api_client.post(f"/api/v1/auth/utilisateurs/{cible_id}/suspension")
    assert suspension.status_code == 200, suspension.text
    assert suspension.json()["statut"] == "suspendu"
    reactiver = api_client.post(
        f"/api/v1/auth/utilisateurs/{cible_id}/activation", json={"role": "formateur"}
    )
    assert reactiver.status_code == 200, reactiver.text
    assert reactiver.json()["statut"] == "actif"

    # Suppression : l'admin ne peut pas se supprimer lui-même, la cible oui.
    # La ValidationError applicative est traduite en 422 par le gestionnaire
    # d'erreurs global (voir app.core.errors) : message affiché tel quel.
    soi = api_client.delete(f"/api/v1/auth/utilisateurs/{admin_ref}")
    assert soi.status_code == 422, soi.text
    assert "propre compte" in soi.json()["error"]["message"]
    suppression = api_client.delete(f"/api/v1/auth/utilisateurs/{cible_id}")
    assert suppression.status_code == 204
    avec = api_session_factory()
    try:
        assert avec.get(Utilisateur, cible_id) is None
    finally:
        avec.close()


def test_api_gestion_comptes_reservee_aux_admins(
    api_client: TestClient, api_session_factory: sessionmaker[Session]
) -> None:
    """Un compte non-admin est refusé (403) sur les routes de gestion."""
    from app.api.deps import get_current_user

    with api_session_factory() as preparation:
        collaborateur = Utilisateur(
            email="collab-bt@carso.ma",
            nom="Collab",
            prenom="Bt",
            password_hash=hasher_mot_de_passe("motdepasse1"),
            role=RoleUtilisateur.COLLABORATEUR.value,
            statut=StatutUtilisateur.ACTIF.value,
        )
        preparation.add(collaborateur)
        preparation.commit()
        collab_id = collaborateur.id

    api_client.app.dependency_overrides[get_current_user] = lambda: collaborateur

    liste = api_client.get("/api/v1/auth/utilisateurs")
    assert liste.status_code == 403
    role = api_client.patch(
        f"/api/v1/auth/utilisateurs/{collab_id}/role", json={"role": "formateur"}
    )
    assert role.status_code == 403
    suppression = api_client.delete(f"/api/v1/auth/utilisateurs/{collab_id}")
    assert suppression.status_code == 403


# --- Profil personnel (page Profil) ---------------------------------------------


def _actif(session: Session, *, nom: str = "Avant", prenom: str = "Nom") -> Utilisateur:
    user = Utilisateur(
        email=f"{nom.lower()}-{prenom.lower()}@carso.ma",
        nom=nom,
        prenom=prenom,
        password_hash=hasher_mot_de_passe("motdepasse1"),
        role=RoleUtilisateur.COLLABORATEUR.value,
        statut=StatutUtilisateur.ACTIF.value,
    )
    session.add(user)
    session.flush()
    return user


def test_modifier_profil_nettoie_et_trace(session: Session) -> None:
    """Le titulaire change son identité ; l'audit garde l'avant et l'après."""
    user = _actif(session)

    modifie = UtilisateurService(session, actor_id=str(user.id)).modifier_profil(
        user.id, nom="  Daho  ", prenom=" Amine "
    )

    assert (modifie.nom, modifie.prenom) == ("Daho", "Amine")
    evenement = session.scalars(
        select(AuditEvent).where(AuditEvent.action == ActionAudit.UTILISATEUR_MODIFIE.value)
    ).one()
    assert evenement.actor_id == str(user.id)
    assert evenement.event_metadata["avant"] == {"nom": "Avant", "prenom": "Nom"}
    assert evenement.after == {"nom": "Daho", "prenom": "Amine"}


def test_modifier_profil_refuse_un_nom_vide(session: Session) -> None:
    user = _actif(session)

    with pytest.raises(ValidationError, match="obligatoires"):
        UtilisateurService(session).modifier_profil(user.id, nom="   ", prenom="Amine")


def test_changer_mot_de_passe_remplace_le_hash_sans_le_tracer(session: Session) -> None:
    user = _actif(session)
    ancien_hash = user.password_hash

    UtilisateurService(session, actor_id=str(user.id)).changer_mot_de_passe(
        user.id,
        ancien_mot_de_passe="motdepasse1",
        nouveau_mot_de_passe="motdepasse2",
    )

    assert verifier_mot_de_passe("motdepasse2", user.password_hash)
    assert not verifier_mot_de_passe("motdepasse1", user.password_hash)
    assert user.password_hash != ancien_hash

    evenement = session.scalars(
        select(AuditEvent).where(
            AuditEvent.action == ActionAudit.UTILISATEUR_MOT_DE_PASSE_CHANGE.value
        )
    ).one()
    # Ni mot de passe, ni hash dans l'événement (AGENTS.md §9).
    trace = f"{evenement.after} {evenement.event_metadata}"
    assert "motdepasse2" not in trace
    assert ancien_hash not in trace
    assert user.password_hash not in trace


def test_changer_mot_de_passe_exige_l_ancien(session: Session) -> None:
    user = _actif(session)

    with pytest.raises(PermissionDeniedError, match="incorrect"):
        UtilisateurService(session).changer_mot_de_passe(
            user.id,
            ancien_mot_de_passe="mauvais-mot-de-passe",
            nouveau_mot_de_passe="motdepasse2",
        )

    # Le mot de passe n'a pas bougé.
    assert verifier_mot_de_passe("motdepasse1", user.password_hash)


def test_changer_mot_de_passe_identique_refuse(session: Session) -> None:
    user = _actif(session)

    with pytest.raises(ValidationError, match="différent"):
        UtilisateurService(session).changer_mot_de_passe(
            user.id,
            ancien_mot_de_passe="motdepasse1",
            nouveau_mot_de_passe="motdepasse1",
        )


def test_api_profil_modification_et_mot_de_passe(
    api_client: TestClient, api_session_factory: sessionmaker[Session]
) -> None:
    """``PATCH /auth/me`` et changement de mot de passe sur **son** compte."""
    from app.api.deps import get_current_user

    with api_session_factory() as preparation:
        user = Utilisateur(
            email="profil@carso.ma",
            nom="Avant",
            prenom="Nom",
            password_hash=hasher_mot_de_passe("motdepasse1"),
            role=RoleUtilisateur.COLLABORATEUR.value,
            statut=StatutUtilisateur.ACTIF.value,
        )
        preparation.add(user)
        preparation.commit()
        identifiant = user.id

    def _courant() -> Utilisateur:
        with api_session_factory() as lecture:
            return lecture.get(Utilisateur, identifiant)  # type: ignore[return-value]

    api_client.app.dependency_overrides[get_current_user] = _courant

    reponse = api_client.patch(
        "/api/v1/auth/me", json={"nom": "Daho", "prenom": "Amine"}
    )
    assert reponse.status_code == 200, reponse.text
    corps = reponse.json()
    assert (corps["nom"], corps["prenom"]) == ("Daho", "Amine")
    assert corps["created_at"]

    # L'ancien mot de passe est exigé : un faux est refusé sans rien changer.
    faux = api_client.post(
        "/api/v1/auth/me/mot-de-passe",
        json={"ancien_mot_de_passe": "mauvais-mot", "nouveau_mot_de_passe": "motdepasse2"},
    )
    assert faux.status_code == 403

    trop_court = api_client.post(
        "/api/v1/auth/me/mot-de-passe",
        json={"ancien_mot_de_passe": "motdepasse1", "nouveau_mot_de_passe": "court"},
    )
    assert trop_court.status_code == 422

    ok = api_client.post(
        "/api/v1/auth/me/mot-de-passe",
        json={"ancien_mot_de_passe": "motdepasse1", "nouveau_mot_de_passe": "motdepasse2"},
    )
    assert ok.status_code == 200, ok.text

    with api_session_factory() as verification:
        rangee = verification.get(Utilisateur, identifiant)
        assert rangee is not None
        assert verifier_mot_de_passe("motdepasse2", rangee.password_hash)


def test_api_profil_identite_incomplete_refusee(
    api_client: TestClient, api_session_factory: sessionmaker[Session]
) -> None:
    """Un nom vide est refusé par la validation de la requête (422)."""
    from app.api.deps import get_current_user

    with api_session_factory() as preparation:
        user = Utilisateur(
            email="vide@carso.ma",
            nom="Avant",
            prenom="Nom",
            password_hash=hasher_mot_de_passe("motdepasse1"),
            role=RoleUtilisateur.COLLABORATEUR.value,
            statut=StatutUtilisateur.ACTIF.value,
        )
        preparation.add(user)
        preparation.commit()

    api_client.app.dependency_overrides[get_current_user] = lambda: user

    reponse = api_client.patch("/api/v1/auth/me", json={"nom": "", "prenom": "Nom"})

    assert reponse.status_code == 422


def test_crud_exige_bearer_si_auth_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    """Décision (a) : AUTH_SECRET actif ⇒ CRUD / AG-UI protégés ; /health public."""
    from fastapi.testclient import TestClient

    from app.core.config import get_settings
    from app.main import app

    monkeypatch.setenv("AUTH_SECRET", "secret-test-auth-carso-32b")
    get_settings.cache_clear()
    try:
        with TestClient(app) as client:
            assert client.get("/api/v1/health").status_code == 200
            interdit = client.get("/api/v1/organisations/")
            assert interdit.status_code == 401
            assert interdit.json()["error"]["code"] == "unauthenticated"
            assert client.get("/api/v1/agui/").status_code == 401
            assert client.get("/api/v1/documents").status_code == 401
    finally:
        get_settings.cache_clear()


# --- Équipe ↔ comptes (incrément 24) ---------------------------------------------


def test_inscription_refusee_hors_equipe(session: Session) -> None:
    """Décision validée : l'email doit exister dans le vivier Équipes actif."""
    svc = UtilisateurService(session)
    with pytest.raises(ValidationError, match="équipe"):
        svc.inscrire(
            email="inconnu@carso.ma", nom="N", prenom="P", mot_de_passe="motdepasse1"
        )


def test_inscription_refusee_si_equipe_archivee(session: Session) -> None:
    svc = UtilisateurService(session)
    _equipe(session, "sorti@carso.ma").statut = "archive"
    with pytest.raises(ValidationError, match="plus active"):
        svc.inscrire(
            email="sorti@carso.ma", nom="N", prenom="P", mot_de_passe="motdepasse1"
        )


def test_role_equipe_attribue_a_l_activation(session: Session) -> None:
    """« Rôle équipe = rôle utilisateur » : formateur porté par la fiche vivier."""
    svc = UtilisateurService(session)
    admin = Utilisateur(
        email="admin2@carso.ma",
        nom="Ad",
        prenom="Min",
        password_hash=hasher_mot_de_passe("adminpass1"),
        role=RoleUtilisateur.ADMINISTRATEUR.value,
        statut=StatutUtilisateur.ACTIF.value,
    )
    session.add(admin)
    session.flush()
    _equipe(session, "form@carso.ma", role_compte=RoleUtilisateur.FORMATEUR.value)
    user = svc.inscrire(
        email="form@carso.ma", nom="N", prenom="P", mot_de_passe="motdepasse1"
    )
    svc.activer(user.id, admin_id=admin.id)
    assert user.role == RoleUtilisateur.FORMATEUR.value


# --- Mot de passe oublié : code à usage unique envoyé par email ---------------


class _EnvoyeurFactice:
    """Transport email de test : capture les messages, n'envoie rien."""

    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []

    def envoyer(
        self,
        *,
        destinataires: list[str] | str,
        sujet: str,
        corps: str,
        corps_html: str | None = None,
        pieces_jointes: tuple[object, ...] = (),
    ) -> None:
        adresses = [destinataires] if isinstance(destinataires, str) else list(destinataires)
        self.messages.append(
            {
                "destinataires": adresses,
                "sujet": sujet,
                "corps": corps,
                "corps_html": corps_html,
                "pieces_jointes": list(pieces_jointes),
            }
        )


def _compte_actif(session: Session, email: str) -> Utilisateur:
    user = Utilisateur(
        email=email,
        nom="Compte",
        prenom="Actif",
        password_hash=hasher_mot_de_passe("ancienpass1"),
        role=RoleUtilisateur.COLLABORATEUR.value,
        statut=StatutUtilisateur.ACTIF.value,
    )
    session.add(user)
    session.flush()
    return user


def _code_recu(envoyeur: _EnvoyeurFactice) -> str:
    match = re.search(r"\b(\d{6})\b", envoyeur.messages[-1]["corps"])
    assert match is not None
    return match.group(1)


def test_reinitialisation_flux_complet(session: Session) -> None:
    """Demande → code reçu → vérification → confirmation → nouveau mot de passe."""
    user = _compte_actif(session, "reset@carso.ma")
    envoyeur = _EnvoyeurFactice()
    svc = UtilisateurService(session)

    svc.demander_reinitialisation(email=user.email, settings=_settings(), envoyeur=envoyeur)
    assert len(envoyeur.messages) == 1
    assert envoyeur.messages[0]["destinataires"] == [user.email]
    code = _code_recu(envoyeur)

    # Vérification sans consommation : le code reste valable pour la suite.
    svc.verifier_code_reinitialisation(email=user.email, code=code, settings=_settings())

    svc.definir_nouveau_mot_de_passe(
        email=user.email, code=code, nouveau_mot_de_passe="nouveaupass1", settings=_settings()
    )
    assert verifier_mot_de_passe("nouveaupass1", user.password_hash)
    assert not verifier_mot_de_passe("ancienpass1", user.password_hash)

    # Le code ne sert qu'UNE fois : la confirmation rejouée est refusée.
    with pytest.raises(ValidationError, match="invalide ou expir"):
        svc.definir_nouveau_mot_de_passe(
            email=user.email, code=code, nouveau_mot_de_passe="encorepass1", settings=_settings()
        )


def test_reinitialisation_le_code_en_clair_ne_vit_pas_en_base(
    session: Session,
) -> None:
    """Seul le hash HMAC est stocké — jamais les 6 chiffres (AGENTS.md §9)."""
    user = _compte_actif(session, "hash@carso.ma")
    envoyeur = _EnvoyeurFactice()
    svc = UtilisateurService(session)
    svc.demander_reinitialisation(email=user.email, settings=_settings(), envoyeur=envoyeur)
    code = _code_recu(envoyeur)

    enregistres = session.scalars(select(CodeReinitialisation)).all()
    assert len(enregistres) == 1
    assert code not in enregistres[0].code_hash
    assert len(enregistres[0].code_hash) >= 32


def test_reinitialisation_reponse_neutre_compte_inconnu(session: Session) -> None:
    """Aucun message ne dit si un compte existe (pas d'énumération d'adresses)."""
    envoyeur = _EnvoyeurFactice()
    svc = UtilisateurService(session)
    message = svc.demander_reinitialisation(
        email="inconnu@carso.ma", settings=_settings(), envoyeur=envoyeur
    )
    assert envoyeur.messages == []
    assert "Si un compte actif existe" in message


def test_reinitialisation_compte_en_attente_ne_recoit_rien(session: Session) -> None:
    """Un compte pending (ou suspendu) n'a rien à réinitialiser : même réponse neutre."""
    _equipe(session, "pending@carso.ma")
    svc = UtilisateurService(session)
    svc.inscrire(email="pending@carso.ma", nom="N", prenom="P", mot_de_passe="motdepasse1")
    envoyeur = _EnvoyeurFactice()

    message = svc.demander_reinitialisation(
        email="pending@carso.ma", settings=_settings(), envoyeur=envoyeur
    )
    assert envoyeur.messages == []
    assert "Si un compte actif existe" in message


def test_reinitialisation_code_expire(session: Session) -> None:
    """Un code périmé est refusé : redemande explicite, jamais une validation."""
    user = _compte_actif(session, "expire@carso.ma")
    envoyeur = _EnvoyeurFactice()
    svc = UtilisateurService(session)
    svc.demander_reinitialisation(email=user.email, settings=_settings(), envoyeur=envoyeur)
    code = _code_recu(envoyeur)

    enregistre = session.scalars(select(CodeReinitialisation)).one()
    enregistre.expire_at = datetime.now(UTC) - timedelta(seconds=1)

    with pytest.raises(ValidationError, match="invalide ou expir"):
        svc.verifier_code_reinitialisation(email=user.email, code=code, settings=_settings())


def test_reinitialisation_trop_d_essais(session: Session) -> None:
    """Chaque saisie fausse consomme un essai ; code mort au plafond."""
    user = _compte_actif(session, "essais@carso.ma")
    envoyeur = _EnvoyeurFactice()
    svc = UtilisateurService(session)
    svc.demander_reinitialisation(email=user.email, settings=_settings(), envoyeur=envoyeur)
    code = _code_recu(envoyeur)
    reglages = _settings()

    for _ in range(reglages.mot_de_passe_oublie_tentatives_max):
        with pytest.raises(ValidationError, match="Code invalide"):
            svc.verifier_code_reinitialisation(email=user.email, code="000000", settings=reglages)
    # Le bon code est mort lui aussi : le compteur a épuisé le code.
    with pytest.raises(ValidationError, match="redemandez"):
        svc.verifier_code_reinitialisation(email=user.email, code=code, settings=reglages)


def test_reinitialisation_delai_anti_spam(session: Session) -> None:
    """Deux demandes trop rapprochées : refus explicite, pas de second email."""
    user = _compte_actif(session, "spam@carso.ma")
    envoyeur = _EnvoyeurFactice()
    svc = UtilisateurService(session)
    reglages = _settings()
    svc.demander_reinitialisation(email=user.email, settings=reglages, envoyeur=envoyeur)

    with pytest.raises(ValidationError, match="patientez"):
        svc.demander_reinitialisation(email=user.email, settings=reglages, envoyeur=envoyeur)
    assert len(envoyeur.messages) == 1


def test_reinitialisation_nouveau_mot_de_passe_identique_refuse(session: Session) -> None:
    user = _compte_actif(session, "identique@carso.ma")
    envoyeur = _EnvoyeurFactice()
    svc = UtilisateurService(session)
    svc.demander_reinitialisation(email=user.email, settings=_settings(), envoyeur=envoyeur)
    code = _code_recu(envoyeur)

    with pytest.raises(ValidationError, match="différent"):
        svc.definir_nouveau_mot_de_passe(
            email=user.email, code=code, nouveau_mot_de_passe="ancienpass1", settings=_settings()
        )
    # La pose a échoué : le code n'est PAS consommé — un vrai choix reste possible.
    svc.verifier_code_reinitialisation(email=user.email, code=code, settings=_settings())
