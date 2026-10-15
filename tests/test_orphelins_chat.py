"""Purge validée des orphelins du scope chat (incrément 34).

Le service est testé avec le vrai ``LocalDocumentStorage`` sur un dossier
temporaire (même contrat que la production) : orphelin détecté (pièce retirée
avant publication, annonce supprimée), fichier référencé **jamais** détecté,
purge validée (motif obligatoire, choix explicite, revalidation à l'exécution,
trace d'audit).
"""

from __future__ import annotations

import io
import uuid as uuid_module
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.application.services import OrphelinsChatService
from app.application.trace import Decision
from app.core.config import get_settings
from app.domain.social import Annonce

#: Vraie signature PNG (le stockage valide le contenu, pas l'extension).
_PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 16


def _televerser(client: TestClient, nom: str, contenu: bytes = _PNG) -> dict[str, object]:
    """Téléverse une pièce d'annonce (chemin non référencé tant que rien n'est publié)."""
    reponse = client.post(
        "/api/v1/social/annonces/pieces",
        files={"file": (nom, io.BytesIO(contenu), "image/png")},
    )
    assert reponse.status_code == 201
    return reponse.json()


def _publier(client: TestClient, piece: dict[str, object], titre: str = "Annonce galerie") -> dict:
    reponse = client.post(
        "/api/v1/social/annonces",
        json={
            "titre": titre,
            "pieces": [
                {
                    "chemin": piece["chemin"],
                    "nom": piece["nom"],
                    "mime": piece["mime"],
                    "taille": piece["taille"],
                }
            ],
        },
    )
    assert reponse.status_code == 201
    return reponse.json()


def test_service_detecte_les_orphelins_seulement(
    api_client: TestClient, api_session_factory: sessionmaker[Session], tmp_path, monkeypatch
) -> None:
    """Un fichier téléversé puis retiré est orphelin ; un fichier publié ne l'est pas."""
    monkeypatch.setattr(get_settings(), "storage_root", str(tmp_path))

    gardee = _televerser(api_client, "gardee.png")
    orpheline = _televerser(api_client, "retiree.png")
    _publier(api_client, gardee)

    service = OrphelinsChatService(api_session_factory())
    chemins = [o.chemin for o in service.lister_orphelins()]

    assert orpheline["chemin"] not in {gardee["chemin"]}
    assert str(orpheline["chemin"]) in chemins
    assert str(gardee["chemin"]) not in chemins


def test_purge_refusee_sans_motif_ou_sans_choix(api_session_factory) -> None:
    """Le motif et la sélection explicite sont obligatoires (jamais « tout »)."""
    service = OrphelinsChatService(api_session_factory())
    with pytest.raises(Exception, match="motif"):
        service.purger_orphelins(Decision(decided_by="x", reason="  "), ["chat/a.png"])
    with pytest.raises(Exception, match="Aucun fichier"):
        service.purger_orphelins(Decision(decided_by="x", reason="Nettoyage"), [])


def test_purge_refuse_chemin_hors_scope(api_session_factory) -> None:
    """Aucun chemin hors ``chat/`` ne peut passer, même via un administrateur."""
    service = OrphelinsChatService(api_session_factory())
    with pytest.raises(Exception, match="hors scope"):
        service.purger_orphelins(
            Decision(decided_by="x", reason="Nettoyage"), ["offres/abc/f.pdf"]
        )


def test_purge_valide_supprime_et_revalide(
    api_client: TestClient, api_session_factory: sessionmaker[Session], tmp_path, monkeypatch
) -> None:
    """Purge du fichier choisi ; un chemin redevient référencé → ignoré, pas détruit."""
    monkeypatch.setattr(get_settings(), "storage_root", str(tmp_path))

    gardee = _televerser(api_client, "gardee.png")
    orpheline = _televerser(api_client, "orpheline.png")
    _publier(api_client, gardee)

    service = OrphelinsChatService(api_session_factory(), actor_id="test-admin")

    # 1. La pièce retirée avant publication part réellement.
    rapport = service.purger_orphelins(
        Decision(decided_by="test-admin", reason="Pièce retirée du formulaire"),
        [str(orpheline["chemin"])],
    )
    assert rapport.purges == 1
    assert rapport.ignores == []
    assert not (tmp_path / str(orpheline["chemin"])).exists()

    # 2. Un fichier publié **après** le listing n'est plus orphelin : ignoré.
    rapport2 = service.purger_orphelins(
        Decision(decided_by="test-admin", reason="Rejeu de la même liste"),
        [str(gardee["chemin"])],
    )
    assert rapport2.purges == 0
    assert rapport2.ignores == [str(gardee["chemin"])]
    assert (tmp_path / str(gardee["chemin"])).exists()


def test_photo_profil_jamais_orpheline(
    api_client: TestClient, api_session_factory: sessionmaker[Session], tmp_path, monkeypatch
) -> None:
    """L'avatar d'un compte (chat/{id}/profil-*) reste référencé par sa ligne.

    Régression : la première détection listait les photos de profil comme
    orphelines — elles sont servies par /annuaire/{id}/photo-profil (incrément
    26) et référencées par ``utilisateurs.photo_profil_chemin``.
    """
    monkeypatch.setattr(get_settings(), "storage_root", str(tmp_path))

    piece = _televerser(api_client, "profil-avatar.png")

    from app.domain.enums import RoleUtilisateur
    from app.domain.identity import Utilisateur

    session = api_session_factory()
    try:
        session.add(
            Utilisateur(
                email="avatar@carso.test",
                nom="Avatar",
                prenom="Test",
                password_hash="x",
                role=RoleUtilisateur.COLLABORATEUR.value,
                photo_profil_chemin=str(piece["chemin"]),
            )
        )
        session.commit()
    finally:
        session.close()

    service = OrphelinsChatService(api_session_factory())
    assert str(piece["chemin"]) not in [o.chemin for o in service.lister_orphelins()]


def test_annonce_supprimee_ne_devient_pas_orpheline(
    api_client: TestClient, api_session_factory: sessionmaker[Session], tmp_path, monkeypatch
) -> None:
    """Une annonce soft-supprimée garde ses fichiers (ligne toujours là)."""
    monkeypatch.setattr(get_settings(), "storage_root", str(tmp_path))

    piece = _televerser(api_client, "annonce-supprimee.png")
    annonce = _publier(api_client, piece)

    session = api_session_factory()
    try:
        ligne = session.get(Annonce, uuid_module.UUID(str(annonce["id"])))
        assert ligne is not None
        ligne.supprime_at = datetime.now(UTC)
        session.commit()
    finally:
        session.close()

    service = OrphelinsChatService(api_session_factory())
    assert str(piece["chemin"]) not in [o.chemin for o in service.lister_orphelins()]


def test_routes_admin_orphelins(
    api_client: TestClient, api_session_factory: sessionmaker[Session], tmp_path, monkeypatch
) -> None:
    """Listing et purge réservés aux administrateurs (403 sinon), motif exigé."""
    monkeypatch.setattr(get_settings(), "storage_root", str(tmp_path))

    _televerser(api_client, "orpheline-admin.png")

    # Collaborateur (porteur lab par défaut) : 403 sur les deux routes.
    assert api_client.get("/api/v1/social/admin/chat/orphelins").status_code == 403
    assert (
        api_client.post(
            "/api/v1/social/admin/chat/orphelins/purge",
            json={"reason": "x", "chemins": ["chat/a.png"]},
        ).status_code
        == 403
    )

    # Administrateur forcé (même pattern que la purge documentaire).
    from app.api.deps import get_current_user, require_admin
    from app.domain.enums import RoleUtilisateur
    from app.domain.identity import Utilisateur

    admin = Utilisateur(
        email="admin@carso.test",
        nom="Admin",
        prenom="Carso",
        password_hash="x",
        role=RoleUtilisateur.ADMINISTRATEUR.value,
    )
    api_client.app.dependency_overrides[require_admin] = lambda: admin
    api_client.app.dependency_overrides[get_current_user] = lambda: admin
    try:
        listing = api_client.get("/api/v1/social/admin/chat/orphelins")
        assert listing.status_code == 200
        assert len(listing.json()) == 1
        cible = listing.json()[0]["chemin"]

        # Motif vide : 422 (jamais de purge muette).
        assert (
            api_client.post(
                "/api/v1/social/admin/chat/orphelins/purge",
                json={"reason": "", "chemins": [cible]},
            ).status_code
            == 422
        )

        purge = api_client.post(
            "/api/v1/social/admin/chat/orphelins/purge",
            json={"reason": "Nettoyage des pièces retirées", "chemins": [cible]},
        )
        assert purge.status_code == 200
        assert purge.json()["purges"] == 1
        assert api_client.get("/api/v1/social/admin/chat/orphelins").json() == []
    finally:
        api_client.app.dependency_overrides.pop(require_admin, None)
        api_client.app.dependency_overrides.pop(get_current_user, None)
