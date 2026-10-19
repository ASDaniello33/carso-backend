"""Tests AgentRH — proposition HITL, score explicable, pas d'invention."""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy.sql.ddl import CreateColumn

from app.agents.agent_rh import DEFINITION, build_rh
from app.agents.toolkit import KIT_CAPABILITY
from app.agents.tools.agent_rh_tools import (
    CAPACITE_EMAIL,
    MAX_DESTINATAIRES,
    EnvoyerEmailInput,
    construire_tools_rh,
)
from app.core.errors import PermissionDeniedError
from app.domain.base import Base
from app.domain.document import AuditEvent, Document
from app.domain.enums import (
    ActionAudit,
    ActorType,
    StatutAffectation,
    StatutDocument,
    StatutMission,
    TypeDocument,
)
from app.domain.execution import Equipe, Lieu, Mission
from app.domain.organization import Organisation
from app.tools.base import TypedTool
from app.tools.permissions import PermissionPolicy


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


@pytest.fixture
def factory() -> Iterator[sessionmaker[Session]]:
    """Fabrique partagée (StaticPool) : les tools ouvrent leur propre transaction."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    yield sessionmaker(bind=engine)
    engine.dispose()


def _semis_mission(factory: sessionmaker[Session]) -> tuple[Mission, Lieu, Lieu]:
    """Une mission planifiée et deux lieux réels (jamais de texte libre)."""
    with factory() as session:
        org = Organisation(nom="Client RH")
        session.add(org)
        session.flush()
        mission = Mission(
            organisation_id=org.id,
            reference="M-RH-LIEU",
            titre="Mission sans lieu",
            statut=StatutMission.PLANIFIEE.value,
        )
        lyon = Lieu(nom="Lyon — centre", ville="Lyon")
        paris = Lieu(nom="Paris — site client", ville="Paris")
        session.add_all([mission, lyon, paris])
        session.commit()
        session.refresh(mission)
        session.refresh(lyon)
        session.refresh(paris)
        return mission, lyon, paris


def test_contrat_hitl() -> None:
    assert DEFINITION.agent_id == "agent_rh"
    assert "create_assignment_proposal" in DEFINITION.approval_required
    assert DEFINITION.collaboration == ()


def test_proposer_affectation(session: Session) -> None:
    org = Organisation(nom="Client RH")
    session.add(org)
    session.flush()
    mission = Mission(
        organisation_id=org.id,
        reference="M-RH-1",
        titre="Mission RH",
        statut=StatutMission.PLANIFIEE.value,
    )
    equipe = Equipe(nom="Ben", prenom="Ali", profil="formateur senior")
    session.add_all([mission, equipe])
    session.flush()

    agent = build_rh(session)
    reponse = agent.proposer(
        mission.id,
        equipe.id,
        "formateur",
        justification="Profil contient formateur ; aucun diplôme inventé.",
    )
    assert reponse["requires_approval"] is True
    prop = reponse["proposition"]
    assert prop["statut"] == StatutAffectation.PROPOSEE.value
    assert prop["score_max"] == 3
    assert prop["criteres"]["profil_renseigne"] is True
    assert prop["criteres"]["role_dans_profil"] is True
    noms = {t.name for t in agent.deep_tools()}
    # Depuis le 12/10, la surface de recherche externe est la **même pour tous
    # les agents** (recherche fédérée, lecture de pages, recherche média) :
    # l'ancienne absence de recherche web chez RH n'était plus tenable.
    assert {
        "langsearch_web_search",
        "fetch_url",
        "read_urls",
        "rechercher_video",
        "envoyer_email",
    } <= noms
    assert "read_document" in noms
    assert "generer_document" in noms
    assert "propose_equipe_update" in noms
    assert "search_organisations" in noms
    assert "search_documents" in noms


def _outils_rh(factory: sessionmaker[Session]) -> dict[str, TypedTool]:
    agent = build_rh(factory(), session_factory=factory)
    return {tool.name: tool for tool in agent.deep_tools()}


def test_propose_mission_update_par_lieu_id(factory: sessionmaker[Session]) -> None:
    """Le lieu d'exécution est une **référence** : jamais un texte libre (HITL)."""
    mission, lyon, _ = _semis_mission(factory)
    sortie = _outils_rh(factory)["propose_mission_update"].invoke(
        {"mission_id": str(mission.id), "lieu_id": str(lyon.id)}
    )
    assert sortie["ok"] is True
    assert sortie["lieu_id"] == str(lyon.id)
    assert sortie["requires_approval"] is True

    with factory() as session:
        relue = session.get(Mission, mission.id)
        assert relue is not None
        assert relue.lieu_id == lyon.id


def test_propose_mission_update_lieu_inconnu_refuse(
    factory: sessionmaker[Session],
) -> None:
    """Un lieu inexistant arrête la mise à jour : aucune valeur inventée."""
    mission, _, _ = _semis_mission(factory)
    sortie = _outils_rh(factory)["propose_mission_update"].invoke(
        {"mission_id": str(mission.id), "lieu_id": str(uuid4())}
    )
    assert sortie["ok"] is False
    assert sortie["error"]["code"] == "not_found"

    with factory() as session:
        relue = session.get(Mission, mission.id)
        assert relue is not None
        assert relue.lieu_id is None


def test_propose_mission_update_sans_changement_refuse(
    factory: sessionmaker[Session],
) -> None:
    """Aucune valeur fournie ⇒ refus explicite, pas de trace vide."""
    mission, _, _ = _semis_mission(factory)
    sortie = _outils_rh(factory)["propose_mission_update"].invoke(
        {"mission_id": str(mission.id)}
    )
    assert sortie["ok"] is False
    assert sortie["error"]["code"] == "business_validation_error"


# --- Envoi d'email (12/10) -----------------------------------------------------


class _EnvoyeurEspion:
    """Transport factice : aucun réseau, les envois sont enregistrés."""

    envois: list[dict[str, Any]] = []

    def __init__(self, settings: object) -> None:
        self._settings = settings

    def envoyer(
        self,
        *,
        destinataires: list[str] | str,
        sujet: str,
        corps: str,
        corps_html: str | None = None,
        pieces_jointes: tuple[Any, ...] = (),
    ) -> None:
        adresses = [destinataires] if isinstance(destinataires, str) else list(destinataires)
        type(self).envois.append(
            {
                "destinataires": adresses,
                "sujet": sujet,
                "corps": corps,
                "corps_html": corps_html,
                "pieces_jointes": list(pieces_jointes),
            }
        )


@pytest.fixture
def envois(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[dict[str, Any]]]:
    """Remplace le transport email du tool : le test n'envoie rien réellement."""
    from app.agents.tools import agent_rh_tools

    _EnvoyeurEspion.envois = []
    monkeypatch.setattr(agent_rh_tools, "EnvoyeurEmail", _EnvoyeurEspion)
    yield _EnvoyeurEspion.envois
    _EnvoyeurEspion.envois = []


@pytest.fixture
def storage_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Racine documentaire temporaire : une pièce jointe se lit ici, jamais au réel."""
    from app.core.config import get_settings

    racine = tmp_path / "carso"
    monkeypatch.setenv("STORAGE_ROOT", str(racine))
    get_settings.cache_clear()
    yield racine
    get_settings.cache_clear()


def _document_avec_fichier(
    factory: sessionmaker[Session],
    racine: Path,
    *,
    nom: str = "Offre technique.pdf",
    contenu: bytes = b"%PDF-1.4 contenu factice",
    mime_type: str = "application/pdf",
    statut: str = StatutDocument.DRAFT.value,
) -> UUID:
    """Un document **enregistré** et son fichier sur disque (chemin logique contrôlé)."""
    with factory() as session:
        document = Document(
            nom=nom,
            type_document=TypeDocument.AUTRE.value,
            mime_type=mime_type,
            taille_octets=len(contenu),
            storage_path=f"generated/{uuid4()}/{nom}",
            statut=statut,
        )
        session.add(document)
        session.commit()
        document_id = document.id
        chemin_logique = document.storage_path
    cible = racine / chemin_logique
    cible.parent.mkdir(parents=True, exist_ok=True)
    cible.write_bytes(contenu)
    return document_id


def _personne_avec_email(
    factory: sessionmaker[Session], email: str = "voahangy@carso.test"
) -> tuple[Equipe, UUID]:
    with factory() as session:
        personne = Equipe(nom="Rakotomalala", prenom="Voahangy", email=email)
        session.add(personne)
        session.commit()
        session.refresh(personne)
        return personne, personne.id


def test_envoyer_email_exige_approbation_et_capacite() -> None:
    """Un email ne se rappelle pas : approbation humaine **et** capacité dédiée."""
    assert "envoyer_email" in DEFINITION.tools
    assert "envoyer_email" in DEFINITION.approval_required
    assert DEFINITION.policy.allows(CAPACITE_EMAIL)


def test_envoyer_email_est_sous_interruption_du_harnais(
    factory: sessionmaker[Session],
) -> None:
    """Le harnais interrompt bien **avant** l'exécution du tool (HITL réel)."""
    from app.agents.harness import AgentRuntimeConfig, _politique_approbation

    agent = build_rh(factory(), session_factory=factory)
    outils = agent.deep_tools()
    politique = _politique_approbation(
        DEFINITION, outils, AgentRuntimeConfig(interactive=True)
    )
    assert politique["envoyer_email"] is True
    # Les autres écritures sensibles de l'agent restent couvertes.
    assert politique["create_assignment_proposal"] is True
    assert politique["generer_document"] is True


def test_envoyer_email_capacite_verifiee_a_la_construction(
    factory: sessionmaker[Session],
) -> None:
    """Un contrat sans la capacité ``email_send`` échoue au montage, pas à l'appel."""
    sans_capacite = replace(
        DEFINITION,
        policy=PermissionPolicy(allowed_capabilities=frozenset({KIT_CAPABILITY})),
    )
    with pytest.raises(PermissionDeniedError):
        construire_tools_rh(sans_capacite, factory)


def test_envoyer_email_intervenant_envoye_et_trace(
    factory: sessionmaker[Session], envois: list[dict[str, str]]
) -> None:
    """Contact du vivier : envoi normalisé, tracé — jamais le corps du message."""
    _personne, personne_id = _personne_avec_email(factory)
    sortie = _outils_rh(factory)["envoyer_email"].invoke(
        {
            "destinataires": ["  Voahangy@Carso.Test "],
            "sujet": "Disponibilité pour la mission M-12",
            "corps": "Bonjour, pouvez-vous confirmer votre disponibilité ?",
            "motif": "Vérifier la disponibilité déclarée",
        }
    )
    assert sortie["ok"] is True
    assert sortie["nb_destinataires"] == 1
    assert sortie["destinataires"] == [
        {
            "email": "voahangy@carso.test",
            "nom": "Voahangy Rakotomalala",
            "type": "equipes",
            "entity_id": str(personne_id),
        }
    ]
    assert len(envois) == 1
    assert envois[0]["destinataires"] == ["voahangy@carso.test"]
    assert envois[0]["sujet"] == "Disponibilité pour la mission M-12"

    with factory() as session:
        evenements = list(session.scalars(select(AuditEvent)).all())
    assert len(evenements) == 1
    evenement = evenements[0]
    assert evenement.action == ActionAudit.EMAIL_ENVOYE.value
    assert evenement.actor_type == ActorType.AGENT.value
    assert evenement.actor_id == "agent_rh"
    assert evenement.entity_type == "equipes"
    assert evenement.entity_id == str(personne_id)
    assert (evenement.after or {})["sujet"] == "Disponibilité pour la mission M-12"
    assert (evenement.event_metadata or {})["motif"] == "Vérifier la disponibilité déclarée"
    # Le corps ne quitte jamais la requête : le journal porte la taille, pas le
    # texte (un email peut porter des données personnelles, AGENTS.md §9).
    assert "confirmer votre disponibilité" not in json.dumps(evenement.after)


def test_envoyer_email_organisation_resolue(
    factory: sessionmaker[Session], envois: list[dict[str, str]]
) -> None:
    """Une organisation cliente est un contact légitime, tracé comme tel."""
    with factory() as session:
        organisation = Organisation(nom="Client Sud", email="contact@sud.test")
        session.add(organisation)
        session.commit()
        session.refresh(organisation)
        organisation_id = organisation.id

    sortie = _outils_rh(factory)["envoyer_email"].invoke(
        {
            "destinataires": ["contact@sud.test"],
            "sujet": "Convention de formation",
            "corps": "Bonjour, la convention est jointe au dossier.",
        }
    )
    assert sortie["ok"] is True
    assert sortie["destinataires"][0]["type"] == "organisations"
    assert sortie["destinataires"][0]["nom"] == "Client Sud"
    assert len(envois) == 1

    with factory() as session:
        evenements = list(session.scalars(select(AuditEvent)).all())
    assert [e.entity_type for e in evenements] == ["organisations"]
    assert evenements[0].entity_id == str(organisation_id)


def test_envoyer_email_adresse_inconnue_refusee(
    factory: sessionmaker[Session], envois: list[dict[str, str]]
) -> None:
    """Adresse hors base : refus explicite, aucun envoi, aucun événement."""
    _personne_avec_email(factory)  # une personne existe, pas à cette adresse
    sortie = _outils_rh(factory)["envoyer_email"].invoke(
        {
            "destinataires": ["inconnu@ailleurs.test"],
            "sujet": "Bonjour",
            "corps": "Message qui ne doit jamais partir.",
        }
    )
    assert sortie["ok"] is False
    assert sortie["error"]["code"] == "business_validation_error"
    assert "contact" in sortie["error"]["message"]
    assert envois == []
    with factory() as session:
        assert list(session.scalars(select(AuditEvent)).all()) == []


def test_envoyer_email_adresse_invalide_refusee(
    factory: sessionmaker[Session], envois: list[dict[str, str]]
) -> None:
    """Une adresse qui n'a pas la forme d'un email est refusée avant toute chose."""
    sortie = _outils_rh(factory)["envoyer_email"].invoke(
        {"destinataires": ["pas-une-adresse"], "sujet": "Bonjour", "corps": "Message"}
    )
    assert sortie["ok"] is False
    assert sortie["error"]["code"] == "business_validation_error"
    assert envois == []


def test_envoyer_email_production_sans_smtp_refuse(
    factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    """En production sans SMTP, l'envoi refuse explicitement (jamais silencieux)."""
    from app.core.config import get_settings

    _personne_avec_email(factory, email="prod@carso.test")
    reglages = get_settings()
    monkeypatch.setattr(reglages, "environment", "production", raising=False)
    monkeypatch.setattr(reglages, "smtp_host", None, raising=False)
    # Le transport réel est exercé : SMTP absent + production ⇒ refus explicite.
    sortie = _outils_rh(factory)["envoyer_email"].invoke(
        {"destinataires": ["prod@carso.test"], "sujet": "Bonjour", "corps": "Message"}
    )
    assert sortie["ok"] is False
    assert sortie["error"]["code"] == "business_validation_error"
    assert "SMTP_HOST" in sortie["error"]["message"]
    with factory() as session:
        assert list(session.scalars(select(AuditEvent)).all()) == []


# --- Envoi collectif, variante HTML et pièces jointes (13/10) ------------------


def test_entree_envoyer_email_normalise_et_refuse_une_forme_invalide() -> None:
    """Une adresse invalide arrête la construction ; un doublon de casse disparaît."""
    entree = EnvoyerEmailInput(
        destinataires=["  Voahangy@Carso.Test ", "voahangy@carso.test"],
        sujet="Sujet",
        corps="Corps",
    )
    assert entree.destinataires == ["voahangy@carso.test"]
    with pytest.raises(PydanticValidationError):
        EnvoyerEmailInput(destinataires=["pas-une-adresse"], sujet="Sujet", corps="Corps")
    with pytest.raises(PydanticValidationError):
        EnvoyerEmailInput(
            destinataires=[f"contact{i}@carso.test" for i in range(MAX_DESTINATAIRES + 1)],
            sujet="Sujet",
            corps="Corps",
        )


def test_envoyer_email_plusieurs_destinataires_un_seul_message(
    factory: sessionmaker[Session], envois: list[dict[str, Any]]
) -> None:
    """Un même message à plusieurs contacts : un envoi, une ligne d'audit par destinataire."""
    _personne, personne_id = _personne_avec_email(factory)
    with factory() as session:
        organisation = Organisation(nom="Client Sud", email="contact@sud.test")
        session.add(organisation)
        session.commit()
        organisation_id = organisation.id

    sortie = _outils_rh(factory)["envoyer_email"].invoke(
        {
            "destinataires": [
                "voahangy@carso.test",
                "Contact@Sud.Test",
                "contact@sud.test",  # doublon de casse : une seule adresse
            ],
            "sujet": "Réunion de préparation",
            "corps": "Bonjour, la réunion de préparation est fixée à lundi.",
        }
    )
    assert sortie["ok"] is True
    assert sortie["nb_destinataires"] == 2
    assert [d["email"] for d in sortie["destinataires"]] == [
        "voahangy@carso.test",
        "contact@sud.test",
    ]
    # Un seul départ : le transport reçoit la liste complète, pas deux messages.
    assert len(envois) == 1
    assert envois[0]["destinataires"] == ["voahangy@carso.test", "contact@sud.test"]

    with factory() as session:
        evenements = list(session.scalars(select(AuditEvent)).all())
    par_entite = {evenement.entity_id: evenement for evenement in evenements}
    assert set(par_entite) == {str(personne_id), str(organisation_id)}
    assert par_entite[str(personne_id)].entity_type == "equipes"
    assert par_entite[str(organisation_id)].entity_type == "organisations"
    for evenement in evenements:
        assert evenement.action == ActionAudit.EMAIL_ENVOYE.value
        assert (evenement.after or {})["destinataires_total"] == 2


def test_envoyer_email_une_adresse_inconnue_annule_tout(
    factory: sessionmaker[Session], envois: list[dict[str, Any]]
) -> None:
    """Tout ou rien : une adresse hors base ⇒ aucun départ, aucune trace."""
    _personne_avec_email(factory)
    sortie = _outils_rh(factory)["envoyer_email"].invoke(
        {
            "destinataires": ["voahangy@carso.test", "inconnu@ailleurs.test"],
            "sujet": "Message",
            "corps": "Message qui ne doit partir à personne.",
        }
    )
    assert sortie["ok"] is False
    assert sortie["error"]["code"] == "business_validation_error"
    assert sortie["error"]["details"]["destinataires_inconnus"] == ["inconnu@ailleurs.test"]
    assert envois == []
    with factory() as session:
        assert list(session.scalars(select(AuditEvent)).all()) == []


def test_envoyer_email_piece_jointe_document(
    factory: sessionmaker[Session],
    storage_root: Path,
    envois: list[dict[str, Any]],
) -> None:
    """Une pièce jointe est un document enregistré, lu par la couche de stockage."""
    _personne_avec_email(factory)
    contenu = b"%PDF-1.4 offre technique"
    document_id = _document_avec_fichier(factory, storage_root, contenu=contenu)

    sortie = _outils_rh(factory)["envoyer_email"].invoke(
        {
            "destinataires": ["voahangy@carso.test"],
            "sujet": "Offre technique en pièce jointe",
            "corps": "Bonjour, vous trouverez l'offre en pièce jointe.",
            "pieces_jointes": [str(document_id)],
        }
    )
    assert sortie["ok"] is True
    assert sortie["pieces_jointes"] == [
        {"nom": "Offre technique.pdf", "mime_type": "application/pdf", "octets": len(contenu)}
    ]
    assert len(envois) == 1
    piece = envois[0]["pieces_jointes"][0]
    assert piece.contenu == contenu
    assert piece.mime_type == "application/pdf"

    with factory() as session:
        evenement = session.scalars(select(AuditEvent)).one()
    journal = (evenement.after or {})["pieces_jointes"]
    assert journal == [
        {"nom": "Offre technique.pdf", "mime_type": "application/pdf", "octets": len(contenu)}
    ]
    # Le contenu binaire ne quitte jamais la requête : le journal porte le poids.
    assert contenu.decode("ascii", errors="ignore") not in json.dumps(evenement.after)


def test_envoyer_email_piece_jointe_image_et_variante_html(
    factory: sessionmaker[Session],
    storage_root: Path,
    envois: list[dict[str, Any]],
) -> None:
    """Image jointe et corps HTML : les deux voyagent dans le même message."""
    _personne_avec_email(factory)
    image = b"\x89PNG\r\n\x1a\n image factice"
    document_id = _document_avec_fichier(
        factory,
        storage_root,
        nom="Logo CARSO.png",
        contenu=image,
        mime_type="image/png",
    )
    html = "<p>Bonjour <strong>CARSO</strong>, voici le logo.</p>"

    sortie = _outils_rh(factory)["envoyer_email"].invoke(
        {
            "destinataires": ["voahangy@carso.test"],
            "sujet": "Logo de la formation",
            "corps": "Bonjour, le logo est en pièce jointe.",
            "corps_html": html,
            "pieces_jointes": [str(document_id)],
        }
    )
    assert sortie["ok"] is True
    assert sortie["corps_html"] is True
    assert envois[0]["corps_html"] == html
    assert envois[0]["pieces_jointes"][0].mime_type == "image/png"

    with factory() as session:
        evenement = session.scalars(select(AuditEvent)).one()
    assert (evenement.after or {})["corps_html"] is True


def test_envoyer_email_piece_jointe_inconnue_refusee(
    factory: sessionmaker[Session],
    storage_root: Path,
    envois: list[dict[str, Any]],
) -> None:
    """Un identifiant de document inventé arrête l'envoi : jamais un chemin libre."""
    del storage_root
    _personne_avec_email(factory)
    sortie = _outils_rh(factory)["envoyer_email"].invoke(
        {
            "destinataires": ["voahangy@carso.test"],
            "sujet": "Pièce jointe inventée",
            "corps": "Ne doit jamais partir.",
            "pieces_jointes": [str(uuid4())],
        }
    )
    assert sortie["ok"] is False
    assert sortie["error"]["code"] == "not_found"
    assert envois == []
    with factory() as session:
        assert list(session.scalars(select(AuditEvent)).all()) == []


def test_envoyer_email_piece_jointe_supprimee_refusee(
    factory: sessionmaker[Session],
    storage_root: Path,
    envois: list[dict[str, Any]],
) -> None:
    """Un document supprimé (fichier retiré) ne peut plus partir par email."""
    _personne_avec_email(factory)
    document_id = _document_avec_fichier(
        factory, storage_root, statut=StatutDocument.SUPPRIME.value
    )
    sortie = _outils_rh(factory)["envoyer_email"].invoke(
        {
            "destinataires": ["voahangy@carso.test"],
            "sujet": "Document supprimé",
            "corps": "Ne doit jamais partir.",
            "pieces_jointes": [str(document_id)],
        }
    )
    assert sortie["ok"] is False
    assert sortie["error"]["code"] == "not_found"
    assert envois == []


def test_envoyer_email_pieces_jointes_trop_lourdes_refusees(
    factory: sessionmaker[Session],
    storage_root: Path,
    envois: list[dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Plafond de poids : refus explicite, jamais une pièce jointe tronquée."""
    from app.core.config import get_settings

    _personne_avec_email(factory)
    document_id = _document_avec_fichier(
        factory, storage_root, nom="Gros rapport.pdf", contenu=b"0123456789"
    )
    monkeypatch.setattr(get_settings(), "email_pieces_jointes_max_octets", 4, raising=False)
    sortie = _outils_rh(factory)["envoyer_email"].invoke(
        {
            "destinataires": ["voahangy@carso.test"],
            "sujet": "Rapport volumineux",
            "corps": "Ne doit jamais partir.",
            "pieces_jointes": [str(document_id)],
        }
    )
    assert sortie["ok"] is False
    assert sortie["error"]["code"] == "business_validation_error"
    assert sortie["error"]["details"]["plafond_octets"] == 4
    assert envois == []
