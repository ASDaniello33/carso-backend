"""Import d'une image du web comme document CARSO.

Deux niveaux vérifiés, du plus fin au plus exposé :

1. **service** — ``ImportMediaService`` : du rapatriement contrôlé à la fiche
   documentaire (checksum SHA-256, taille, MIME réel, chemin logique, statut,
   provenance) ;
2. **route** — ``POST /documents/import-media`` : ``201`` et document ``draft``,
   refus ``422`` d'une adresse invalide, d'un contenu non image ou d'une vidéo,
   ``401`` hors mode lab — et **aucune officialisation** par un import.

Aucun appel réseau : la résolution DNS est remplacée et le transport ``httpx``
est simulé. Les signatures binaires réelles sont empruntées à
``tests.test_documents_telechargement`` — une seule définition d'un PNG, d'un
MP4 et d'une page HTML dans la suite.
"""

from __future__ import annotations

import hashlib
import socket
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.application.dto import ImportMediaInput
from app.application.services import import_media_service as module_import
from app.application.services.import_media_service import ImportMediaService
from app.core.config import get_settings
from app.core.errors import ConflictError, ValidationError
from app.documents.storage import LocalDocumentStorage
from app.domain.base import Base
from app.domain.document import AuditEvent, Document
from app.domain.enums import ActionAudit, StatutDocument, TypeDocument
from tests.test_documents_telechargement import (
    MP4,
    PAGE_HTML,
    PNG,
    PUBLIQUE,
    _resoudre,
    _transport,
)

PRÉFIXE = "/api/v1"
DOMAINE = "exemple.org"
ADRESSE = f"https://{DOMAINE}/photos/affiche.php"


# --- Fixtures -------------------------------------------------------------------


@pytest.fixture
def session() -> Iterator[Session]:
    """Session SQLite mémoire (aucun commit : la transaction est au test)."""
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as active:
        yield active
    engine.dispose()


@pytest.fixture
def storage(tmp_path: Path) -> LocalDocumentStorage:
    return LocalDocumentStorage(tmp_path / "carso", max_bytes=2 * 1024 * 1024)


@pytest.fixture
def hote_public(monkeypatch: pytest.MonkeyPatch) -> None:
    """Hôte résolu vers une adresse publique : le test ne dépend pas du DNS."""
    monkeypatch.setattr(socket, "getaddrinfo", _resoudre(PUBLIQUE))


def _service(
    session: Session,
    storage: LocalDocumentStorage,
    reponses: Any,
    *,
    actor_id: str | None = "importeur",
) -> ImportMediaService:
    """Service dont le client HTTP ne sort jamais du processus."""
    client = httpx.Client(transport=_transport(reponses), follow_redirects=False)
    return ImportMediaService(
        session, storage=storage, actor_id=actor_id, client=client
    )


def _reponse(contenu: bytes, *, mime: str = "application/octet-stream") -> Any:
    return {ADRESSE: (200, contenu, {"content-type": mime})}


# --- Service : fiche documentaire complète ---------------------------------------


def test_import_image_produit_une_fiche_complete(
    session: Session, storage: LocalDocumentStorage, hote_public: None
) -> None:
    """Un média importé est un document à part entière, pas un fichier posé là."""
    service = _service(session, storage, _reponse(PNG, mime="image/png"))

    document = service.importer(ImportMediaInput(url=ADRESSE, nom="affiche"))

    assert document.nom == "affiche.png"
    assert document.type_document == TypeDocument.NON_CLASSE.value
    assert document.mime_type == "image/png"
    assert document.taille_octets == len(PNG)
    assert document.checksum_sha256 == hashlib.sha256(PNG).hexdigest()
    assert document.version == 1
    assert document.statut == StatutDocument.DRAFT.value
    assert document.created_by == "importeur"
    assert document.storage_path.startswith("generated/")
    assert document.storage_disk
    # Le fichier est bien sur le disque, à l'endroit annoncé.
    assert (storage.root / document.storage_path).read_bytes() == PNG


def test_import_video_est_refuse_avant_tout_enregistrement(
    session: Session, storage: LocalDocumentStorage, hote_public: None
) -> None:
    """Une vidéo n'entre jamais dans CARSO : refus, aucune ligne, aucun fichier."""
    service = _service(session, storage, _reponse(MP4, mime="video/mp4"))

    with pytest.raises(ValidationError) as erreur:
        service.importer(ImportMediaInput(url=ADRESSE))

    assert "vidéo" in str(erreur.value)
    assert session.query(Document).count() == 0
    assert list(storage.root.rglob("*")) == []


def test_content_type_menteur_ne_fait_pas_le_type(
    session: Session, storage: LocalDocumentStorage, hote_public: None
) -> None:
    """Le site annonce du HTML, les octets sont un PNG : le PNG gagne."""
    service = _service(session, storage, _reponse(PNG, mime="text/html"))

    document = service.importer(ImportMediaInput(url=ADRESSE))

    assert document.mime_type == "image/png"
    assert document.nom.endswith(".png")


def test_aucune_ancre_metier_inventee(
    session: Session, storage: LocalDocumentStorage, hote_public: None
) -> None:
    """L'import n'invente aucun rattachement : il naît ``non_classe``."""
    service = _service(session, storage, _reponse(PNG, mime="image/png"))

    document = service.importer(ImportMediaInput(url=ADRESSE))

    assert document.organisation_id is None
    assert document.appel_a_proposition_id is None
    assert document.offre_id is None
    assert document.mission_id is None
    assert document.equipe_id is None
    assert document.session_id is None


def test_provenance_conservee_avec_url_source_et_finale(
    session: Session, storage: LocalDocumentStorage, hote_public: None
) -> None:
    """Un média sans sa source est un média invérifiable : la provenance reste."""
    depart = f"https://{DOMAINE}/photos/affiche.php"
    arrivee = f"https://cdn.{DOMAINE}/cache/affiche.png"
    service = _service(
        session,
        storage,
        {
            depart: (302, b"", {"location": arrivee}),
            arrivee: (200, PNG, {"content-type": "image/png"}),
        },
    )

    document = service.importer(ImportMediaInput(url=depart))

    provenance = document.doc_metadata["provenance"]  # type: ignore[index]
    assert provenance["type"] == "web"
    assert provenance["url_source"] == depart
    assert provenance["url_finale"] == arrivee
    assert provenance["hote"] == f"cdn.{DOMAINE}"
    assert provenance["importe_le"]
    assert document.storage_path.startswith("generated/")


def test_audit_trace_le_depot_humain(
    session: Session, storage: LocalDocumentStorage, hote_public: None
) -> None:
    service = _service(session, storage, _reponse(PNG, mime="image/png"))

    document = service.importer(ImportMediaInput(url=ADRESSE))

    evenement, = session.scalars(
        select(AuditEvent).where(AuditEvent.entity_id == str(document.id))
    ).all()
    assert evenement.action == ActionAudit.DOCUMENT_ENREGISTRE.value
    assert evenement.actor_type == "humain"
    assert evenement.after["statut"] == StatutDocument.DRAFT.value


def test_second_import_du_meme_nom_est_un_conflit(
    session: Session, storage: LocalDocumentStorage, hote_public: None
) -> None:
    """Deux fois le même nom et le même type : refus explicite, pas d'écrasement."""
    premier = _service(session, storage, _reponse(PNG, mime="image/png"))
    premier.importer(ImportMediaInput(url=ADRESSE, nom="affiche"))

    second = _service(session, storage, _reponse(PNG, mime="image/png"))
    with pytest.raises(ConflictError):
        second.importer(ImportMediaInput(url=ADRESSE, nom="affiche"))


# --- Service : refus --------------------------------------------------------------


def test_page_html_servie_en_200_refusee(
    session: Session, storage: LocalDocumentStorage, hote_public: None
) -> None:
    """Erreur 404 déguisée en 200 : rien n'est enregistré."""
    service = _service(session, storage, _reponse(PAGE_HTML, mime="text/html"))

    with pytest.raises(ValidationError):
        service.importer(ImportMediaInput(url=ADRESSE))

    assert session.scalars(select(Document)).all() == []


def test_contenu_non_media_refuse(
    session: Session, storage: LocalDocumentStorage, hote_public: None
) -> None:
    """Un PDF n'est pas un média : l'import média ne détourne pas le dépôt."""
    pdf = b"%PDF-1.7\n" + b"\x00" * 32
    service = _service(session, storage, _reponse(pdf, mime="application/pdf"))

    with pytest.raises(ValidationError):
        service.importer(ImportMediaInput(url=ADRESSE))

    assert session.scalars(select(Document)).all() == []


def test_hote_non_public_refuse_avant_toute_requete(
    session: Session, storage: LocalDocumentStorage, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SSRF : la résolution interne est refusée, et le site n'est jamais appelé."""
    monkeypatch.setattr(socket, "getaddrinfo", _resoudre("127.0.0.1"))

    def interdit(requete: httpx.Request) -> httpx.Response:  # pragma: no cover - garde
        message = f"aucune requête ne devait partir ({requete.url})"
        raise AssertionError(message)

    service = _service(session, storage, interdit)

    with pytest.raises(ValidationError):
        service.importer(ImportMediaInput(url=ADRESSE))


def test_plafond_du_reglage_respecte(
    session: Session,
    storage: LocalDocumentStorage,
    hote_public: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """La borne de taille est celle des réglages, pas une constante oubliée."""
    monkeypatch.setenv("MEDIA_IMPORT_MAX_BYTES", "8")
    get_settings.cache_clear()
    try:
        service = _service(session, storage, _reponse(PNG, mime="image/png"))
        with pytest.raises(ValidationError) as refus:
            service.importer(ImportMediaInput(url=ADRESSE))
    finally:
        get_settings.cache_clear()

    assert refus.value.details["max_octets"] == 8
    assert session.scalars(select(Document)).all() == []


# --- Route HTTP -------------------------------------------------------------------


@pytest.fixture
def storage_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Redirige la racine documentaire : la suite n'écrit jamais dans ``/storage``."""
    racine = tmp_path / "carso"
    monkeypatch.setenv("STORAGE_ROOT", str(racine))
    get_settings.cache_clear()
    yield racine
    get_settings.cache_clear()


@pytest.fixture
def reseau(
    monkeypatch: pytest.MonkeyPatch, hote_public: None
) -> Callable[[Any], None]:
    """Rend le rapatriement de la route simulable **sans** court-circuiter ses gardes.

    La route construit elle-même son service : on ne remplace que le client HTTP
    utilisé par ``recuperer_media`` — la validation d'URL, l'anti-SSRF et la
    détection par signature restent donc exécutées pour de vrai.
    """

    def brancher(reponses: Any) -> None:
        transport = _transport(reponses)
        reel = module_import.recuperer_media

        def rapatrier(url: str, **kwargs: Any) -> Any:
            # Le service transmet ``client`` (``None`` en production) : on impose
            # le nôtre sans court-circuiter les gardes du rapatriement.
            kwargs.pop("client", None)
            client = httpx.Client(transport=transport, follow_redirects=False)
            try:
                return reel(url, client=client, **kwargs)
            finally:
                client.close()

        monkeypatch.setattr(module_import, "recuperer_media", rapatrier)

    return brancher


def test_route_import_media_cree_un_document_draft(
    api_client: TestClient, storage_root: Path, reseau: Callable[[Any], None]
) -> None:
    reseau(_reponse(PNG, mime="image/png"))

    reponse = api_client.post(
        f"{PRÉFIXE}/documents/import-media",
        json={"url": ADRESSE, "nom": "affiche"},
    )

    assert reponse.status_code == 201
    fiche = reponse.json()
    assert fiche["nom"] == "affiche.png"
    assert fiche["type_document"] == TypeDocument.NON_CLASSE.value
    assert fiche["mime_type"] == "image/png"
    assert fiche["taille_octets"] == len(PNG)
    assert fiche["checksum_sha256"] == hashlib.sha256(PNG).hexdigest()
    assert fiche["version"] == 1
    assert fiche["statut"] == StatutDocument.DRAFT.value
    assert fiche["storage_path"].startswith("generated/")
    assert fiche["created_by"]
    assert (storage_root / fiche["storage_path"]).read_bytes() == PNG

    # Un import n'officialise rien : le document reste hors des validés.
    assert api_client.get(f"{PRÉFIXE}/documents/{fiche['id']}").status_code == 200
    valides = api_client.get(f"{PRÉFIXE}/documents/all", params={"statut": "approved"})
    assert valides.status_code == 200
    assert valides.json() == []


def test_route_import_media_refuse_une_adresse_non_http(
    api_client: TestClient, storage_root: Path
) -> None:
    """Refus avant tout réseau : le schéma est vérifié en premier."""
    reponse = api_client.post(
        f"{PRÉFIXE}/documents/import-media",
        json={"url": "file:///etc/passwd"},
    )

    assert reponse.status_code == 422
    assert reponse.json()["error"]["code"] == "business_validation_error"
    assert api_client.get(f"{PRÉFIXE}/documents/all").json() == []


def test_route_import_media_refuse_une_page_html(
    api_client: TestClient, storage_root: Path, reseau: Callable[[Any], None]
) -> None:
    reseau(_reponse(PAGE_HTML, mime="text/html"))

    reponse = api_client.post(
        f"{PRÉFIXE}/documents/import-media", json={"url": ADRESSE}
    )

    assert reponse.status_code == 422
    assert api_client.get(f"{PRÉFIXE}/documents/all").json() == []


def test_route_import_media_reservee_a_lequipe_interne(
    api_client: TestClient, storage_root: Path, reseau: Callable[[Any], None]
) -> None:
    """Un document sans ancre n'est visible d'aucun compte restreint.

    Un formateur ne verrait donc pas ce qu'il vient d'importer : le geste
    appartient à la gestion documentaire (équipe interne), sinon l'écran
    annoncerait un document que son auteur ne pourrait plus retrouver.
    """
    from app.api.deps import get_current_user
    from app.domain.enums import RoleUtilisateur
    from app.domain.identity import Utilisateur

    reseau(_reponse(PNG, mime="image/png"))
    formateur = Utilisateur(
        email="formateur@carso.test",
        nom="Formateur",
        prenom="Test",
        password_hash="x",
        role=RoleUtilisateur.FORMATEUR.value,
    )
    api_client.app.dependency_overrides[get_current_user] = lambda: formateur
    try:
        reponse = api_client.post(
            f"{PRÉFIXE}/documents/import-media", json={"url": ADRESSE}
        )
    finally:
        api_client.app.dependency_overrides.pop(get_current_user, None)

    assert reponse.status_code == 403
    assert reponse.json()["error"]["code"] == "permission_denied"
    assert api_client.get(f"{PRÉFIXE}/documents/all").json() == []


def test_route_import_media_exige_un_jeton_hors_mode_lab(
    storage_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``AUTH_SECRET`` actif ⇒ l'import exige un porteur (aucun import anonyme)."""
    from app.main import app

    monkeypatch.setenv("AUTH_SECRET", "secret-test-import-media-carso")
    get_settings.cache_clear()
    try:
        with TestClient(app) as client:
            reponse = client.post(
                f"{PRÉFIXE}/documents/import-media", json={"url": ADRESSE}
            )
    finally:
        get_settings.cache_clear()

    assert reponse.status_code == 401
    assert reponse.json()["error"]["code"] == "unauthenticated"


# --- Route HTTP : rattachement ----------------------------------------------------


def test_route_import_media_rattache_a_une_ancre(
    api_client: TestClient, storage_root: Path, reseau: Callable[[Any], None]
) -> None:
    """Une ancre fournie rattache le média et le range dans le dossier métier.

    C'est la différence entre « un média sur le disque » et « un document de
    CARSO » : la fiche porte l'ancre, et le fichier vit sous le dossier de
    l'objet au lieu de ``generated/{id}/``.
    """
    reseau(_reponse(PNG, mime="image/png"))
    organisation = api_client.post(
        f"{PRÉFIXE}/organisations/", json={"nom": "Client média"}
    ).json()

    reponse = api_client.post(
        f"{PRÉFIXE}/documents/import-media",
        json={
            "url": ADRESSE,
            "nom": "logo client",
            "organisation_id": organisation["id"],
        },
    )

    assert reponse.status_code == 201
    fiche = reponse.json()
    assert fiche["organisation_id"] == organisation["id"]
    assert fiche["type_document"] == TypeDocument.AUTRE.value
    assert fiche["statut"] == StatutDocument.DRAFT.value
    assert fiche["storage_path"].startswith("organisations/")
    assert (storage_root / fiche["storage_path"]).read_bytes() == PNG


def test_route_import_media_refuse_deux_ancres(
    api_client: TestClient, storage_root: Path, reseau: Callable[[Any], None]
) -> None:
    """Un document ne se rattache qu'à **une** ancre métier (règle du service)."""
    reseau(_reponse(PNG, mime="image/png"))
    organisation = api_client.post(
        f"{PRÉFIXE}/organisations/", json={"nom": "Client média 2"}
    ).json()

    reponse = api_client.post(
        f"{PRÉFIXE}/documents/import-media",
        json={
            "url": ADRESSE,
            "organisation_id": organisation["id"],
            "mission_id": str(uuid4()),
        },
    )

    assert reponse.status_code == 422
    assert reponse.json()["error"]["code"] == "business_validation_error"
    assert api_client.get(f"{PRÉFIXE}/documents/all").json() == []
