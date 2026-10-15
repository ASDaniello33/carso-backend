"""Tests de l'agent généraliste en lecture seule (clinrules 06 + 10)."""

import re
from collections.abc import Iterator
from datetime import date
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy.sql.ddl import CreateColumn

from app.agents.base import AgentDefinition
from app.agents.generaliste_readonly import (
    AGENT_ID,
    DEFINITION,
    AgentGeneralisteReadOnly,
    build_generaliste,
)
from app.core.errors import PermissionDeniedError
from app.core.errors import ValidationError as BusinessValidationError
from app.domain.base import Base
from app.domain.document import AgentTask
from app.domain.enums import StatutAffectation, StatutMission, StatutSessionFormation
from app.domain.execution import (
    AffectationEquipe,
    Beneficiaire,
    Equipe,
    Mission,
    Participation,
    Presence,
    SessionFormation,
)
from app.domain.organization import AppelAProposition, Lot, Organisation
from app.tools.base import TypedTool


@compiles(JSONB, "sqlite")
def _jsonb_as_json(type_: JSONB, compiler: object, **kw: object) -> str:
    return "JSON"


@compiles(CreateColumn, "sqlite")
def _strip_pg(element: CreateColumn, compiler: object, **kw: object) -> str:
    ddl = compiler.visit_create_column(element)  # type: ignore[attr-defined]
    return re.sub(r"DEFAULT gen_random_uuid\(\)", "", ddl)


@pytest.fixture
def usine_sessions() -> Iterator[sessionmaker]:
    """Fabrique SQLite mémoire partagée (tools) + seed déterministe."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    _seed(factory)
    yield factory
    engine.dispose()


def _seed(factory: sessionmaker) -> None:
    """Jeu déterministe : 1 org, 1 appel + lot, 2 missions, 1 équipe +
    affectation approuvée, 1 session + 2 participations (1 présent)."""
    session = factory()
    try:
        org = Organisation(nom="Client Généraliste")
        session.add(org)
        session.flush()
        appel = AppelAProposition(
            organisation_id=org.id, reference="AO-GEN-01", titre="Appel générique"
        )
        session.add(appel)
        session.flush()
        session.add(Lot(appel_a_proposition_id=appel.id, numero="1", titre="Lot unique"))
        mission_a = Mission(
            organisation_id=org.id,
            reference="M-GEN-A",
            titre="Mission Alpha",
            statut=StatutMission.EN_COURS.value,
        )
        mission_b = Mission(
            organisation_id=org.id,
            reference="M-GEN-B",
            titre="Mission Bêta",
            statut=StatutMission.PLANIFIEE.value,
        )
        session.add_all([mission_a, mission_b])
        session.flush()
        equipe = Equipe(nom="Ben", prenom="Ali", profil="Formateur")
        session.add(equipe)
        session.flush()
        session.add(
            AffectationEquipe(
                mission_id=mission_a.id,
                equipe_id=equipe.id,
                role_dans_mission="formateur",
                statut=StatutAffectation.APPROUVEE.value,
            )
        )
        formation = SessionFormation(
            mission_id=mission_a.id,
            theme="Atelier",
            statut=StatutSessionFormation.CONFIRMEE.value,
        )
        session.add(formation)
        session.flush()
        ben_a = Beneficiaire(nom="Tazi", prenom="Sara")
        ben_b = Beneficiaire(nom="Idrissi", prenom="Omar")
        session.add_all([ben_a, ben_b])
        session.flush()
        premiere = Participation(session_id=formation.id, beneficiaire_id=ben_a.id)
        session.add_all(
            [
                premiere,
                Participation(session_id=formation.id, beneficiaire_id=ben_b.id),
            ]
        )
        session.flush()
        # Le pointage est daté (règle 24/09) : une ligne par (inscription, date).
        session.add(
            Presence(
                participation_id=premiere.id,
                date=date(2026, 9, 1),
                presence="present",
            )
        )
        session.commit()
    finally:
        session.close()


def _agent(factory: sessionmaker) -> AgentGeneralisteReadOnly:
    """Agent câblé sur la fabrique de test (trace + tools)."""
    trace = factory()
    return build_generaliste(trace, session_factory=factory)


# --- Consultation déterministe (Phase 6 : preuve d'intégration) --------------


def test_consulter_agrege_recherches(usine_sessions: sessionmaker) -> None:
    agent = _agent(usine_sessions)
    reponse = agent.consulter("Alpha")
    assert reponse["status"] == "completed"
    assert reponse["requires_approval"] is False  # consultation : rien à décider
    consultation = reponse["consultation"]
    assert consultation["question"] == "Alpha"
    titres = [m["titre"] for m in consultation["missions"]]
    assert "Mission Alpha" in titres
    assert consultation["missions_par_statut"] == {
        "en_cours": 1,
        "planifiee": 1,
    }
    assert consultation["affectations_approuvees_par_role"] == {"formateur": 1}


def test_consulter_trace_la_tache(usine_sessions: sessionmaker) -> None:
    """La trace vit dans la transaction de l'appelant (comme l'analyseur) :
    le test committe explicitement, comme une route le ferait."""
    trace = usine_sessions()
    try:
        agent = build_generaliste(trace, session_factory=usine_sessions)
        reponse = agent.consulter("Client", correlation_id="corr-gen-1")
        trace.commit()  # transaction de l'appelant
        assert reponse["task_id"]
        taches = trace.query(AgentTask).all()
        assert len(taches) == 1
        assert taches[0].correlation_id == "corr-gen-1"
        assert taches[0].from_agent == AGENT_ID
        assert taches[0].task_type == "consultation_generale"
    finally:
        trace.close()


def test_consulter_necrit_aucune_donnee_officielle(
    usine_sessions: sessionmaker,
) -> None:
    """Une consultation ne crée ni organisation, ni mission, ni document."""
    from sqlalchemy import func, select

    trace = usine_sessions()
    try:
        agent = build_generaliste(trace, session_factory=usine_sessions)
        agent.consulter("tout")
        trace.commit()
        assert trace.scalar(select(func.count(Organisation.id))) == 1
        assert trace.scalar(select(func.count(Mission.id))) == 2
        assert trace.scalar(select(func.count(Beneficiaire.id))) == 2
    finally:
        trace.close()


# --- Agrégations nommées (instruction/10 §9 : le service calcule) ------------


def test_analytique_missions_par_statut(usine_sessions: sessionmaker) -> None:
    agent = _agent(usine_sessions)
    assert agent.executer_analytique("missions_par_statut") == {
        "missions_par_statut": {"en_cours": 1, "planifiee": 1},
        "total": 2,
    }


def test_analytique_beneficiaires_par_session(
    usine_sessions: sessionmaker,
) -> None:
    from sqlalchemy import select

    agent = _agent(usine_sessions)
    session = usine_sessions()
    try:
        formation = session.scalars(select(SessionFormation)).first()
        assert formation is not None
        session_id = formation.id
    finally:
        session.close()
    resultat = agent.executer_analytique("beneficiaires_par_session", session_id=session_id)
    assert resultat["inscrits"] == 2
    # Le comptage porte sur les **pointages** (1 seul jour pointé ici) ;
    # « non pointé » compte les inscrits sans aucun pointage.
    assert resultat["pointages"] == 1
    assert resultat["par_presence"] == {"present": 1}
    assert resultat["non_pointe"] == 1
    assert resultat["date"] is None

    du_jour = agent.executer_analytique(
        "beneficiaires_par_session", session_id=session_id, date=date(2026, 9, 1)
    )
    assert du_jour["date"] == "2026-09-01"
    assert du_jour["par_presence"] == {"present": 1}

    autre_jour = agent.executer_analytique(
        "beneficiaires_par_session", session_id=session_id, date=date(2026, 9, 2)
    )
    assert autre_jour["pointages"] == 0
    assert autre_jour["non_pointe"] == 2


def test_analytique_session_absente_404(usine_sessions: sessionmaker) -> None:
    agent = _agent(usine_sessions)
    with pytest.raises(BusinessValidationError):
        agent.executer_analytique("beneficiaires_par_session", session_id=None)
    from app.core.errors import NotFoundError

    with pytest.raises(NotFoundError):
        agent.executer_analytique("beneficiaires_par_session", session_id=uuid4())


def test_analytique_inconnue_refusee(usine_sessions: sessionmaker) -> None:
    """Clinrules 10 : métrique inconnue refusée techniquement (pas d'invention)."""
    agent = _agent(usine_sessions)
    with pytest.raises(BusinessValidationError):
        agent.executer_analytique("chiffre_affaires_par_agent")


def test_analytique_affectations_par_role(usine_sessions: sessionmaker) -> None:
    """« Combien de missions ont eu un assistant logistique ? » → comptage réel."""
    agent = _agent(usine_sessions)
    assert agent.executer_analytique("affectations_par_role") == {
        "affectations_approuvees_par_role": {"formateur": 1},
        "total": 1,
    }


def test_analytique_missions_par_organisation(usine_sessions: sessionmaker) -> None:
    """« Quelles organisations génèrent le plus de missions ? » → jointure nommée."""
    agent = _agent(usine_sessions)
    assert agent.executer_analytique("missions_par_organisation") == {
        "missions_par_organisation": {"Client Généraliste": 2},
        "total": 2,
    }


def test_analytique_sessions_par_mois(usine_sessions: sessionmaker) -> None:
    """Fenêtre glissante : mois courant compté, hors fenêtre isolé, dates
    absentes exclues (la session semée sans ``date_debut``)."""
    from sqlalchemy import select

    agent = _agent(usine_sessions)
    session = usine_sessions()
    try:
        mission = session.scalars(select(Mission)).first()
        assert mission is not None
        aujourd_hui = date.today()
        if aujourd_hui.month == 1:
            annee, mois = aujourd_hui.year - 1, 12
        else:
            annee, mois = aujourd_hui.year, aujourd_hui.month - 1
        session.add_all(
            [
                SessionFormation(
                    mission_id=mission.id,
                    theme="Mois courant",
                    date_debut=date(aujourd_hui.year, aujourd_hui.month, 10),
                ),
                SessionFormation(
                    mission_id=mission.id,
                    theme="Mois précédent",
                    date_debut=date(annee, mois, 20),
                ),
                SessionFormation(
                    mission_id=mission.id,
                    theme="Hors fenêtre",
                    date_debut=date(aujourd_hui.year - 2, 1, 5),
                ),
            ]
        )
        session.commit()
    finally:
        session.close()

    resultat = agent.executer_analytique("sessions_par_mois", mois=6)
    assert len(resultat["sessions_par_mois"]) == 6
    assert resultat["sessions_par_mois"][f"{aujourd_hui.year:04d}-{aujourd_hui.month:02d}"] == 1
    assert resultat["sessions_par_mois"][f"{annee:04d}-{mois:02d}"] == 1
    # Trois sessions datées au total, une seule hors des 6 derniers mois.
    assert resultat["total_sessions_datees"] == 3
    assert resultat["hors_fenetre"] == 1


def test_analytique_beneficiaires_par_mois(usine_sessions: sessionmaker) -> None:
    """Bénéficiaires **distincts** par mois : un inscrit à deux sessions du
    même mois n'est compté qu'une fois ; il compte dans chacun des mois."""
    from sqlalchemy import select

    agent = _agent(usine_sessions)
    session = usine_sessions()
    try:
        formation = session.scalars(select(SessionFormation)).first()
        assert formation is not None
        ben_a = session.scalar(select(Beneficiaire).where(Beneficiaire.nom == "Tazi"))
        assert ben_a is not None
        ben_c = Beneficiaire(nom="Nadir", prenom="Lina")
        session.add(ben_c)
        session.flush()
        aujourd_hui = date.today()
        session_courante = SessionFormation(
            mission_id=formation.mission_id,
            theme="Atelier du mois",
            date_debut=date(aujourd_hui.year, aujourd_hui.month, 12),
        )
        session.add(session_courante)
        session.flush()
        # ben_a et ben_c inscrits à la session du mois : 2 bénéficiaires
        # distincts pour ce mois (ben_a existe déjà dans le vivier).
        session.add_all(
            [
                Participation(session_id=session_courante.id, beneficiaire_id=ben_a.id),
                Participation(session_id=session_courante.id, beneficiaire_id=ben_c.id),
            ]
        )
        session.commit()
    finally:
        session.close()

    resultat = agent.executer_analytique("beneficiaires_par_mois", mois=12)
    mois_cle = f"{date.today().year:04d}-{date.today().month:02d}"
    assert resultat["beneficiaires_par_mois"][mois_cle] == 2
    assert resultat["total_beneficiaires_formes"] == 2


# --- Sécurité read-only (clinrules 06 §Sécurité + 10 §GeneralistReadOnly) ----


def test_contrat_sans_mutation_ni_collaboration() -> None:
    """Le contrat ne déclare aucun tool de mutation ni aucune délégation."""
    assert DEFINITION.collaboration == ()
    for nom in DEFINITION.tools:
        assert not nom.startswith(("create_", "update_", "delete_", "approve_"))


def test_kit_readonly_bloque_toute_mutation() -> None:
    """Même sous injection, aucune opération de mutation ne peut être
    enregistrée : la barrière est technique (construction), pas un prompt."""
    from pydantic import BaseModel

    from app.agents.toolkit import AgentTools, OperationSpec

    class _In(BaseModel):
        x: str = "y"

    kit = AgentTools(DEFINITION, readonly=True)
    for nom_mutation in (
        "create_appel_a_proposition_draft",
        "update_draft",
        "approve_appel_a_proposition",
        "delete_document",
        "propose_team_assignment",
    ):
        with pytest.raises(PermissionDeniedError):
            kit.enregistrer(
                OperationSpec(
                    name=nom_mutation,
                    description="tentative de contournement",
                    input_schema=_In,
                    mutation=True,
                    run=lambda session, payload: {},
                )
            )
    assert kit.noms() == []


def test_agent_sans_capacite_kit_refuse() -> None:
    """Un contrat sans la capacité ``agent_toolkit`` ne peut pas outiller."""
    from app.agents.toolkit import AgentTools
    from app.tools.permissions import PermissionPolicy

    definition = AgentDefinition(
        agent_id="agent_sans_kit",
        display_name="Sans kit",
        description="Contrat sans capacité.",
        policy=PermissionPolicy(allowed_capabilities=frozenset()),
        capabilities=(),
    )
    with pytest.raises(PermissionDeniedError):
        AgentTools(definition, readonly=True)


def test_deep_tools_allow_list_du_contrat(
    usine_sessions: sessionmaker,
) -> None:
    """Les tools exposés au modèle sont déclarés au contrat (harnais)."""
    from app.agents.harness import allowed_tools_only

    agent = _agent(usine_sessions)
    outils = agent.deep_tools()
    # harnais : tout tool fourni doit être déclaré au contrat
    retenus = allowed_tools_only(DEFINITION, list(outils))
    noms = {tool.name for tool in retenus}
    assert {
        "search_organisations",
        "search_appels_a_proposition",
        "search_missions",
        "search_equipes",
        "search_documents",
        "query_analytics",
    } <= noms


def test_deep_tools_avec_lecture_document(
    usine_sessions: sessionmaker,
) -> None:
    """``read_document`` est injecté par l'appelant, jamais construit par
    l'agent : l'agent reste indépendant du câblage documentaire."""
    from pydantic import BaseModel

    class _In(BaseModel):
        document_id: str

    faux_tool = TypedTool(
        name="read_document",
        description="Lecture documentaire (câblage appelant).",
        input_schema=_In,
        handler=lambda payload: {"texte": "extrait"},
    )
    trace = usine_sessions()
    try:
        from app.agents.generaliste_readonly import build_generaliste

        agent = build_generaliste(
            trace, session_factory=usine_sessions, read_document_tool=faux_tool
        )
        noms = {tool.name for tool in agent.deep_tools()}
    finally:
        trace.close()
    assert "read_document" in noms


def test_recherche_documents_exige_une_seule_ancre(
    usine_sessions: sessionmaker,
) -> None:
    """Deux ancres métier simultanées sont refusées (règle API reprise)."""
    from app.agents.consultation import rechercher_documents

    session = usine_sessions()
    try:
        with pytest.raises(BusinessValidationError):
            rechercher_documents(
                session,
                {"mission_id": str(uuid4()), "equipe_id": str(uuid4())},
            )
    finally:
        session.close()


def test_build_agent_accepte_system_prompt_extra(usine_sessions: sessionmaker) -> None:
    """Signature uniforme de ``build_agent`` : le manager appelle tous les
    agents avec ``system_prompt_extra`` (instructions administrées).

    Régression (incrément 13) : le généraliste exposait le paramètre sous le
    nom ``extra_prompt`` — chaque montage AG-UI échouait en ``TypeError``
    (``unexpected keyword argument``) et l'agent restait indisponible du chat.
    """
    from app.agents.harness import AgentRuntimeConfig

    trace = usine_sessions()
    try:
        agent = build_generaliste(trace, session_factory=usine_sessions)
        # Lève ``TypeError`` avant correctif ; le graphe est construit ici
        # sans modèle configuré (harnais le résout à l'invocation).
        graphe = agent.build_agent(
            runtime=AgentRuntimeConfig(interactive=False),
            system_prompt_extra="Toujours citer la source consultée.",
        )
        assert graphe is not None
    finally:
        trace.close()
