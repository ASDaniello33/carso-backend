"""La session mène à sa mission et à son organisation (constat 16/11/2026).

**Symptôme.** Sur une fiche de présence demandée depuis une session,
``agent_assistant_formateur`` a bouclé : « je n'ai pas d'outil pour obtenir la
mission d'une session… je dois identifier l'organisation de la mission… », en
répétant le même raisonnement sans jamais appeler d'outil.

**Cause.** Son prompt imposait de lister les modèles de l'organisation avant de
copier un gabarit, or ``lister_modeles_documents`` exige un ``organisation_id``
et l'agent, muni d'un ``session_id``, n'avait aucun chemin pour l'obtenir :
``get_mission`` prend un ``mission_id``, ``list_mission_sessions`` va dans le
sens inverse, et aucun outil ne lisait une session seule.

**Correctif.** Un outil de lecture ``get_session`` (session → mission →
organisation, même périmètre que ``list_mission_sessions``), et un prompt qui
tente la **copie ancrée** avant tout catalogue : le moteur résout lui-même
l'organisation du sujet. Ces tests verrouillent les deux moitiés.
"""

from __future__ import annotations

from collections.abc import Iterator
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.agents.assistant_formateur import DEFINITION
from app.agents.prompt import ASSISTANT_FORMATEUR_SYSTEM_PROMPT
from app.agents.tools.agent_assistant_formateur_tools import (
    NOMS_TOOLS,
    _get_session,
    construire_tools_assistant_formateur,
)
from app.core.errors import NotFoundError
from app.domain.base import Base
from app.domain.enums import StatutMission
from app.domain.execution import Mission, SessionFormation
from app.domain.organization import Organisation


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as ouvert:
        yield ouvert
    engine.dispose()


@pytest.fixture
def mission_session(
    session: Session,
) -> tuple[Organisation, Mission, SessionFormation]:
    """Une organisation cliente, sa mission, et la session de cette mission."""
    organisation = Organisation(nom="Client Présence")
    session.add(organisation)
    session.flush()
    mission = Mission(
        organisation_id=organisation.id,
        reference="M-PRESENCE-01",
        titre="Mission présence",
        statut=StatutMission.PLANIFIE.value,
    )
    session.add(mission)
    session.flush()
    session_formation = SessionFormation(mission_id=mission.id, theme="Jour 1")
    session.add(session_formation)
    session.flush()
    return organisation, mission, session_formation


def test_le_contrat_declare_get_session() -> None:
    """Un nom non déclaré n'est pas monté par le harnais (allow-list)."""
    assert "get_session" in NOMS_TOOLS
    assert "get_session" in DEFINITION.tools


def test_l_outil_est_reellement_monte() -> None:
    """Déclaré mais absent du graphe ne servirait à rien."""
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    try:
        outils = construire_tools_assistant_formateur(DEFINITION, sessionmaker(bind=engine))
        assert "get_session" in {tool.name for tool in outils}
    finally:
        engine.dispose()


def test_la_session_rend_sa_mission_et_l_organisation(
    session: Session,
    mission_session: tuple[Organisation, Mission, SessionFormation],
) -> None:
    """Le scénario du bug : l'agent n'a que le ``session_id``."""
    organisation, mission, session_formation = mission_session

    resultat = _get_session(session, {"session_id": str(session_formation.id)})

    # Les champs de la session (dont ``mission_id``) restent ceux du contrat.
    assert resultat["id"] == str(session_formation.id)
    assert resultat["mission_id"] == str(mission.id)
    assert resultat["theme"] == "Jour 1"
    # …et la chaîne s'arrête à l'organisation, exactement comme le moteur
    # documentaire la résout pour une copie ancrée sur la session.
    assert resultat["mission"]["id"] == str(mission.id)
    assert resultat["mission"]["reference"] == "M-PRESENCE-01"
    assert resultat["organisation"] == {
        "id": str(organisation.id),
        "nom": "Client Présence",
    }


def test_une_session_inconnue_est_signalee(session: Session) -> None:
    """Pas de fiche inventée : l'agent doit lire l'erreur, pas deviner."""
    with pytest.raises(NotFoundError, match="introuvable"):
        _get_session(session, {"session_id": str(uuid4())})


def test_le_prompt_copie_avant_de_lister_les_modeles() -> None:
    """Ordre imposé : copie ancrée d'abord, catalogue seulement pour choisir.

    Sans cette règle, l'agent redemande l'organisation avant de copier et
    boucle dès qu'il n'a qu'un ``session_id``.
    """
    texte = ASSISTANT_FORMATEUR_SYSTEM_PROMPT

    debut = texte.index("Ne cherche pas l'organisation avant de copier")
    fin = texte.index("\n- ", debut + 1)
    paragraphe = texte[debut:fin]

    assert paragraphe.index("copier_modele_document") < paragraphe.index(
        "lister_modeles_documents"
    )
    # La sortie de secours est nommée, et c'est la génération professionnelle.
    assert "generer_document_professionnel" in paragraphe

    # La chaîne « modèle imposé » part de la session, pas du catalogue.
    chaine = texte[texte.index("part de la **copie**") :]
    assert chaine.index("get_session") < chaine.index("copier_modele_document")
    assert chaine.index("copier_modele_document") < chaine.index(
        "lister_modeles_documents"
    )
