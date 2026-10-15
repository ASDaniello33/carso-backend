"""Notifications in-app — émission transactionnelle et lecture.

Les notifications naissent dans les services métier, dans la transaction qui
porte l'écriture. Ici : émission (dédoublonnage), lecture (ordre, badge),
marquage (idempotent, propriété vérifiée), et l'intégration bout-en-bout
inscription → notification des administrateurs.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.application.services.notification_service import NotificationService
from app.application.services.utilisateur_service import UtilisateurService
from app.domain.enums import RoleUtilisateur, StatutUtilisateur
from app.domain.identity import Utilisateur
from app.domain.notification import Notification


def _compte(role: str, statut: str = StatutUtilisateur.ACTIF.value) -> Utilisateur:
    return Utilisateur(
        email=f"{uuid4().hex[:8]}@carso.mg",
        nom="Test",
        prenom="User",
        password_hash="x",
        role=role,
        statut=statut,
    )


def test_emettre_cree_une_notification_par_destinataire(api_session_factory) -> None:
    session: Session = api_session_factory()
    try:
        service = NotificationService(session)
        creees = service.emettre(
            destinataires=[uuid4(), uuid4()],
            type_notification="test",
            titre="Bonjour",
        )
        session.flush()
    finally:
        session.close()

    assert len(creees) == 2
    assert all(n.titre == "Bonjour" for n in creees)
    assert all(n.lue_at is None for n in creees)


def test_emettre_ignore_les_destinataires_dedoubles(api_session_factory) -> None:
    session: Session = api_session_factory()
    try:
        destinataire = uuid4()
        creees = NotificationService(session).emettre(
            destinataires=[destinataire, destinataire],
            type_notification="test",
            titre="une seule",
        )
    finally:
        session.close()
    assert len(creees) == 1


def test_lister_non_lues_dabord_puis_par_date(api_session_factory) -> None:
    session: Session = api_session_factory()
    try:
        utilisateur = _compte(RoleUtilisateur.COLLABORATEUR.value)
        session.add(utilisateur)
        session.commit()

        service = NotificationService(session)
        service.emettre(
            destinataires=[utilisateur.id], type_notification="t", titre="ancienne"
        )
        recente = service.emettre(
            destinataires=[utilisateur.id], type_notification="t", titre="récente"
        )
        # On marque la plus récente lue : elle doit passer après les non-lues.
        service.marquer_lue(recente[0].id, utilisateur.id)
        liste = service.lister(utilisateur.id)
    finally:
        session.close()

    titres = [n.titre for n in liste]
    assert titres == ["ancienne", "récente"]


def test_compter_non_lues_et_marquer_tout_lu(api_session_factory) -> None:
    session: Session = api_session_factory()
    try:
        utilisateur = _compte(RoleUtilisateur.COLLABORATEUR.value)
        session.add(utilisateur)
        session.commit()

        service = NotificationService(session)
        service.emettre(
            destinataires=[utilisateur.id],
            type_notification="t",
            titre="a",
        )
        service.emettre(destinataires=[utilisateur.id], type_notification="t", titre="b")
        assert service.compter_non_lues(utilisateur.id) == 2

        traitées = service.marquer_tout_lu(utilisateur.id)
        assert traitées == 2
        assert service.compter_non_lues(utilisateur.id) == 0
        # Idempotent : repasser tout-lu ne traite rien.
        assert service.marquer_tout_lu(utilisateur.id) == 0
    finally:
        session.close()


def test_marquer_lue_refuse_la_notification_dun_autre(api_session_factory) -> None:
    from app.core.errors import NotFoundError

    session: Session = api_session_factory()
    try:
        proprietaire = _compte(RoleUtilisateur.COLLABORATEUR.value)
        autre = _compte(RoleUtilisateur.COLLABORATEUR.value)
        session.add_all([proprietaire, autre])
        session.commit()

        service = NotificationService(session)
        (creee,) = service.emettre(
            destinataires=[proprietaire.id], type_notification="t", titre="privée"
        )
        session.commit()
        with pytest.raises(NotFoundError):
            service.marquer_lue(creee.id, autre.id)
    finally:
        session.close()


def test_inscription_emet_une_notification_aux_admins(api_session_factory) -> None:
    """Bout-en-bout : l'inscription d'un compte notifie les administrateurs actifs."""
    session: Session = api_session_factory()
    try:
        admin = _compte(RoleUtilisateur.ADMINISTRATEUR.value)
        session.add(admin)
        session.commit()

        service = UtilisateurService(session)
        # L'inscription exige désormais une personne active du vivier (incr. 24).
        from app.domain.execution import Equipe

        email = f"candidat-{uuid4().hex[:6]}@carso.mg"
        session.add(Equipe(nom="Rakoto", prenom="Jean", email=email, statut="actif"))
        session.flush()
        service.inscrire(
            email=email,
            nom="Rakoto",
            prenom="Jean",
            mot_de_passe="mot-de-passe-sûr",
        )
        notifications_admin = NotificationService(session).lister(admin.id)
        session.commit()
    finally:
        session.close()

    assert len(notifications_admin) == 1
    assert notifications_admin[0].type == "inscription_recue"
    assert "Rakoto" in (notifications_admin[0].corps or "")


# --- API -------------------------------------------------------------------------


def test_routes_notifications_en_mode_lab(
    api_session_factory, api_client: TestClient
) -> None:
    """Mode lab : l'utilisateur technique (id ad hoc) voit une liste vide."""
    reponse = api_client.get("/api/v1/notifications/")
    assert reponse.status_code == 200
    corps = reponse.json()
    assert {"notifications", "non_lues"} <= set(corps)


# --- Suppression (demande 04/10) -------------------------------------------------


def test_supprimer_une_notification_verifie_son_destinataire(api_session_factory) -> None:
    """Une notification s'efface — et seulement par son destinataire."""
    from app.core.errors import NotFoundError

    session: Session = api_session_factory()
    try:
        service = NotificationService(session)
        destinataire = uuid4()
        creee = service.emettre(
            destinataires=[destinataire], type_notification="test", titre="à supprimer"
        )[0]
        session.flush()

        with pytest.raises(NotFoundError):
            service.supprimer(creee.id, uuid4())

        service.supprimer(creee.id, destinataire)
        session.flush()
        assert session.get(Notification, creee.id) is None
        assert service.lister(destinataire) == []
    finally:
        session.close()


def test_supprimer_toutes_ne_touche_que_ses_notifications(api_session_factory) -> None:
    """Vider sa liste n'efface jamais celle d'un autre compte."""
    session: Session = api_session_factory()
    try:
        service = NotificationService(session)
        destinataire, autre = uuid4(), uuid4()
        service.emettre(destinataires=[destinataire], type_notification="test", titre="une")
        service.emettre(destinataires=[destinataire], type_notification="test", titre="deux")
        service.emettre(destinataires=[autre], type_notification="test", titre="trois")
        session.flush()

        effacees = service.supprimer_toutes(destinataire)
        session.flush()

        assert effacees == 2
        assert service.lister(destinataire) == []
        assert len(service.lister(autre)) == 1
    finally:
        session.close()


def test_routes_suppression_notifications(api_client: TestClient) -> None:
    """Les deux routes de suppression existent (204 / 404 sur inconnue)."""
    assert api_client.delete("/api/v1/notifications/").status_code == 204
    assert api_client.delete(f"/api/v1/notifications/{uuid4()}").status_code == 404
