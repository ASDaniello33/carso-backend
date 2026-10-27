"""Matrice agent × règle — phase B du plan de validation (INCREMENT-94).

Chaque règle validée le 26/10 est vérifiée **au niveau où l'agent agit** :
le tool lui-même, appelé avec un payload interdit — comme un modèle dévoyé
l'appellerait. Le refus doit venir du service, jamais du seul prompt.

Deux volets :

1. **Structurel** — les six contrats ne portent **aucun** tool de cycle de vie
   mission/session (créer, planifier, annuler, rattacher, confirmer) : ces
   gestes sont humains (routes UI). Les agents lecture seule (généraliste,
   statistique, personnel) n'ont aucun tool qui mute une donnée métier. Les
   tools mutants des agents actifs sont tous déclarés ``approval_required``
   (HITL) — l'incrément 93 n'a ajouté aucun geste d'agent non couvert.
2. **Comportemental** — les tools d'écriture refusent les états interdits :
   type d'offre selon le type d'appel (une AMI ne reçoit jamais d'offre
   technique), second brouillon du même type, appel rejeté, affectation en
   chevauchement, mission clôturée non staffable, et le type réel de l'appel
   exposé en lecture (jamais une AMI présentée comme appel à proposition).

Les cases « document déposé par un agent = proposition, jamais officiel » et
« propriété documentaire » sont déjà verrouillées par
``test_document_service.py`` (dépôt d'agent → ``proposed``) et
``test_documents_propriete.py`` — référencées dans la matrice de l'incrément.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from datetime import date
from uuid import UUID

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.sql.ddl import CreateColumn

from app.agents.agent_personnel import DEFINITION as PERSONNEL_DEF
from app.agents.agent_rh import DEFINITION as RH_DEF
from app.agents.agent_statistique import DEFINITION as STAT_DEF
from app.agents.assistant_formateur import DEFINITION as ASSISTANT_DEF
from app.agents.catalogue import AGENT_DEFINITIONS
from app.agents.generaliste_readonly import DEFINITION as GENERALISTE_DEF
from app.agents.generateur_offre import DEFINITION as GENERATEUR_DEF
from app.agents.tools.agent_generateur_offre_tools import (
    _create_offer_draft,
    _read_appel,
)
from app.agents.tools.agent_rh_tools import _create_proposal
from app.application.dto import AffectationProposee, AppelInput
from app.application.services import (
    AffectationService,
    AppelService,
    MissionService,
)
from app.application.services.cycle_de_vie_service import CycleDeVieService
from app.application.trace import Decision
from app.core.errors import ConflictError, ValidationError
from app.domain.base import Base
from app.domain.enums import (
    RoleMission,
    SourceAffectation,
    StatutMission,
    StatutSessionFormation,
    TypeAppel,
    TypeOffre,
)
from app.domain.execution import Equipe, Mission
from app.domain.organization import Organisation


@compiles(JSONB, "sqlite")
def _jsonb_as_json(type_: JSONB, compiler: object, **kw: object) -> str:
    return "JSON"


@compiles(CreateColumn, "sqlite")
def _strip_pg_server_defaults(element: CreateColumn, compiler: object, **kw: object) -> str:
    ddl = compiler.visit_create_column(element)  # type: ignore[arg-defined]
    return re.sub("DEFAULT gen_random_uuid\\(\\)", "", ddl)


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as s:
        yield s
    engine.dispose()


# ---------------------------------------------------------------------------
# 1. Structurel : aucun agent ne porte un tool de cycle de vie mission/session
# ---------------------------------------------------------------------------

#: Gestes du cycle de vie validés le 26/10 : **humains**, jamais des tools.
#: « rattacher_document_appel » attache une pièce à un appel — le rattachement
#: d'**offres** à une mission (« rattacher_offres ») reste un geste humain.
_GESTES_HUMAINS = re.compile(
    r"create_mission|creer_mission|depuis_appel|depuis_offres|rattacher_offres"
    r"|planifier|annuler|annulation|confirmer|create_session|creer_session"
    r"|supprimer|purger|corbeille"
)


def test_aucun_agent_ne_porte_un_tool_de_cycle_de_vie() -> None:
    """Créer/planifier/annuler une mission ou une session n'est pas un tool."""
    for definition in AGENT_DEFINITIONS:
        for nom in definition.tools:
            assert not _GESTES_HUMAINS.search(nom), (
                f"{definition.agent_id}: le tool {nom!r} touche un geste "
                "réservé à la décision humaine (cycle de vie mission/session)"
            )


#: Préfixes des tools **de lecture** (aucune écriture de donnée métier).
#: ``generer_document_professionnel`` dépose un livrable en **proposition**
#: (``document_propose``) : un document proposé n'est jamais une donnée
#: officielle — la validation documentaire reste humaine.
_LECTURE_SEULE = (
    "search_",
    "read_",
    "lire_",
    "get_",
    "list_",
    "query_",
    "rechercher_",
    "calculer_",
    "fetch_",
    "langsearch_",
    "chercher_",
    "run_python_script",
)


def test_agents_lecture_seule_n_ont_aucun_outil_de_mutation() -> None:
    """Généraliste, statistique et personnel : lecture, rien d'autre."""
    for definition in (GENERALISTE_DEF, STAT_DEF, PERSONNEL_DEF):
        for nom in definition.tools:
            autorise = nom.startswith(_LECTURE_SEULE) or nom in (
                "generer_document_professionnel",
                "importer_tableau_xlsx",
            )
            assert autorise, (
                f"{definition.agent_id}: le tool {nom!r} n'est pas de la "
                "lecture — un agent lecture seule ne doit pas l'avoir"
            )
        # Le livrable reste une proposition : la capacité est ``propose``,
        # jamais une approbation.
        assert "document_propose" in definition.capabilities
        assert not any(c.startswith("document_approve") for c in definition.capabilities)


def test_outils_mutants_des_agents_actifs_sont_sous_hitl() -> None:
    """Tout tool qui écrit une donnée métier est déclaré ``approval_required``.

    Les corrections de documents proposés (``corriger_document``…) ne figurent
    pas ici : elles retouchent un brouillon **non officiel** — le document
    obtenu reste en ``proposed`` et traverse la validation humaine.
    """
    attendus = {
        GENERATEUR_DEF.agent_id: (
            "enregistrer_appel",
            "situer_zone_lot",
            "rattacher_document_appel",
            "submit_rfp_extraction",
            "create_offer_draft",
            "propose_lot_update",
            "propose_appel_update",
        ),
        RH_DEF.agent_id: (
            "create_assignment_proposal",
            "propose_equipe_update",
            "propose_mission_update",
            "envoyer_email",
        ),
        ASSISTANT_DEF.agent_id: ("import_beneficiaires",),
    }
    par_agent = {d.agent_id: d for d in AGENT_DEFINITIONS}
    for agent_id, tools in attendus.items():
        definition = par_agent[agent_id]
        manquants = [t for t in tools if t not in definition.approval_required]
        assert not manquants, (
            f"{agent_id}: tools mutants hors HITL : {manquants} — chaque "
            "écriture doit exiger une décision humaine explicite"
        )


# ---------------------------------------------------------------------------
# 2. Comportemental : un payload interdit est refusé par le service
# ---------------------------------------------------------------------------


def _appel_avec_lot_de_repli(session: Session, *, type_appel: str, reference: str):
    """Appel sans lot → validation → lot unique « 1 » (règle du 26/10)."""
    org = Organisation(nom=f"Émetteur {reference}")
    session.add(org)
    session.flush()
    appels = AppelService(session)
    appel = appels.enregistrer(
        AppelInput(
            organisation_id=org.id,
            reference=reference,
            titre=f"Appel {reference}",
            type=type_appel,
        )
    )
    appels.enregistrer_extraction(
        appel.id, {"organisation": {"nom": org.nom}, "lots": []}, proposed_by_agent="agent-ao"
    )
    appels.approuver_extraction(appel.id, Decision(decided_by="chef.carso"))
    from app.domain.organization import Lot

    lot = session.scalars(select(Lot)).one()
    return appel, lot


def test_offre_d_une_ami_refuse_les_types_de_proposition(session: Session) -> None:
    """AMI + offre technique : refusé — seule l'offre « autre » y répond."""
    appel, lot = _appel_avec_lot_de_repli(
        session, type_appel=TypeAppel.APPEL_A_MANIFESTATION_INTERET.value, reference="AMI-2026-01"
    )
    assert appel.type == TypeAppel.APPEL_A_MANIFESTATION_INTERET.value

    with pytest.raises(ValidationError, match="nature de l'appel"):
        _create_offer_draft(
            session,
            {
                "lot_id": str(lot.id),
                "reference": "OFFRE-TECH-AMI",
                "titre": "Offre technique",
                "type": TypeOffre.OFFRE_TECHNIQUE.value,
            },
        )
    with pytest.raises(ValidationError, match="nature de l'appel"):
        _create_offer_draft(
            session,
            {
                "lot_id": str(lot.id),
                "reference": "OFFRE-FIN-AMI",
                "titre": "Offre financière",
                "type": TypeOffre.OFFRE_FINANCIERE.value,
            },
        )

    # La voie attendue d'une AMI : une offre « autre », en brouillon HITL.
    sortie = _create_offer_draft(
        session,
        {
            "lot_id": str(lot.id),
            "reference": "OFFRE-AUTRE-AMI",
            "titre": "Note d'intérêt",
            "type": TypeOffre.AUTRE.value,
        },
    )
    assert sortie["requires_approval"] is True
    assert sortie["statut"] == "brouillon"


def test_offre_d_un_appel_a_proposition_refuse_le_type_autre(session: Session) -> None:
    """Un appel à proposition se répond en technique + financière, jamais autre."""
    _, lot = _appel_avec_lot_de_repli(
        session, type_appel=TypeAppel.APPEL_A_PROPOSITION.value, reference="AP-2026-01"
    )
    with pytest.raises(ValidationError, match="nature de l'appel"):
        _create_offer_draft(
            session,
            {
                "lot_id": str(lot.id),
                "reference": "OFFRE-AUTRE-AP",
                "titre": "Autre",
                "type": TypeOffre.AUTRE.value,
            },
        )


def test_second_brouillon_du_meme_type_refuse(session: Session) -> None:
    """1 lot → 1 ou 2 offres, jamais deux du même type (uq_offres_lot_id_type)."""
    _, lot = _appel_avec_lot_de_repli(
        session, type_appel=TypeAppel.APPEL_A_PROPOSITION.value, reference="AP-2026-02"
    )
    _create_offer_draft(
        session,
        {
            "lot_id": str(lot.id),
            "reference": "OFFRE-TECH-1",
            "titre": "Technique",
            "type": TypeOffre.OFFRE_TECHNIQUE.value,
        },
    )
    with pytest.raises(ConflictError, match="existe déjà"):
        _create_offer_draft(
            session,
            {
                "lot_id": str(lot.id),
                "reference": "OFFRE-TECH-2",
                "titre": "Technique bis",
                "type": TypeOffre.OFFRE_TECHNIQUE.value,
            },
        )


def test_appel_rejete_bloque_les_offres(session: Session) -> None:
    appel, lot = _appel_avec_lot_de_repli(
        session, type_appel=TypeAppel.APPEL_A_PROPOSITION.value, reference="AP-2026-03"
    )
    appel.statut = "rejete"
    with pytest.raises(ValidationError, match="appel rejeté"):
        _create_offer_draft(
            session,
            {
                "lot_id": str(lot.id),
                "reference": "OFFRE-TECH-REJ",
                "titre": "Technique",
                "type": TypeOffre.OFFRE_TECHNIQUE.value,
            },
        )


def _mission_planifiee(session: Session, org: Organisation, *, reference: str, debut: date,
                       fin: date) -> Mission:
    mission = Mission(
        organisation_id=org.id,
        reference=reference,
        titre=f"Mission {reference}",
        date_debut=debut,
        date_fin=fin,
        statut=StatutMission.EN_PLANIFICATION.value,
    )
    session.add(mission)
    session.flush()
    mission = MissionService(session).planifier(mission.id)
    assert mission.session is not None
    assert mission.session.statut == StatutSessionFormation.PLANIFIE.value
    return mission


def test_rh_ne_propose_pas_un_chevauchement(session: Session) -> None:
    """Disponibilité calculée au tool : une personne approuvée ailleurs, refusée."""
    org = Organisation(nom="Émetteur RH-01")
    session.add(org)
    session.flush()
    occupe = _mission_planifiee(
        session, org, reference="M-RH-01", debut=date(2026, 11, 10), fin=date(2026, 11, 12)
    )
    voisine = _mission_planifiee(
        session, org, reference="M-RH-02", debut=date(2026, 11, 11), fin=date(2026, 11, 13)
    )
    rasoa = Equipe(nom="Rasoa", prenom="Mino")
    session.add(rasoa)
    session.flush()

    affectations = AffectationService(session)
    premiere = affectations.proposer(
        AffectationProposee(
            mission_id=occupe.id,
            equipe_id=rasoa.id,
            role_dans_mission=RoleMission.FORMATEUR.value,
            source=SourceAffectation.MANUAL.value,
            proposed_by="test",
        )
    )
    affectations.approuver(premiere.id, Decision(decided_by="chef"))

    # Le tool RH — pas le service appelé directement — refuse le chevauchement.
    with pytest.raises(ConflictError, match="pas disponible"):
        _create_proposal(
            session,
            {
                "mission_id": str(voisine.id),
                "equipe_id": str(rasoa.id),
                "role_dans_mission": RoleMission.FORMATEUR.value,
                "justification": "Proposition de test en chevauchement",
            },
        )


def test_rh_ne_propose_pas_sur_une_mission_cloturee(session: Session) -> None:
    """Une mission clôturée n'est plus staffable — même via le tool."""
    org = Organisation(nom="Émetteur RH-02")
    session.add(org)
    session.flush()
    terminee = _mission_planifiee(
        session, org, reference="M-RH-03", debut=date(2026, 11, 1), fin=date(2026, 11, 2)
    )
    # Balayage réel : la mission passe en `cloture` après sa fin (ADR 0012).
    CycleDeVieService(session).appliquer_transitions_dues(date(2026, 11, 4))
    assert terminee.statut == StatutMission.CLOTURE.value

    rakoto = Equipe(nom="Rakoto", prenom="Be")
    session.add(rakoto)
    session.flush()

    with pytest.raises(ConflictError, match="non ouverte au staffing"):
        _create_proposal(
            session,
            {
                "mission_id": str(terminee.id),
                "equipe_id": str(rakoto.id),
                "role_dans_mission": RoleMission.FORMATEUR.value,
                "justification": "Proposition sur une mission terminée",
            },
        )


def test_read_appel_expose_le_type_reel(session: Session) -> None:
    """Lecture honnête : une AMI est lue comme une AMI, jamais comme une
    « appel à proposition » — le type est une donnée, pas un préfixe."""
    appel, _ = _appel_avec_lot_de_repli(
        session, type_appel=TypeAppel.APPEL_A_MANIFESTATION_INTERET.value, reference="AMI-2026-02"
    )
    sortie = _read_appel(session, {"appel_id": str(appel.id)})
    assert sortie["type"] == TypeAppel.APPEL_A_MANIFESTATION_INTERET.value
    assert sortie["statut"] == "valide"
    assert sortie["reference"] == "AMI-2026-02"
    # L'état des lots guide l'agent sans invention.
    assert sortie["nb_lots_officiels"] == 1
    assert sortie["proposition_sans_lot"] is False


def test_la_proposition_du_rh_ne_devient_pas_officielle(session: Session) -> None:
    """Une proposition du tool RH naît ``proposee`` : l'approbation est humaine."""
    org = Organisation(nom="Émetteur RH-03")
    session.add(org)
    session.flush()
    mission = _mission_planifiee(
        session, org, reference="M-RH-04", debut=date(2026, 12, 1), fin=date(2026, 12, 3)
    )
    soa = Equipe(nom="Soa", prenom="Fanja")
    session.add(soa)
    session.flush()

    sortie = _create_proposal(
        session,
        {
            "mission_id": str(mission.id),
            "equipe_id": str(soa.id),
            "role_dans_mission": RoleMission.FORMATEUR.value,
            "justification": "Disponible sur ces dates",
        },
    )
    assert sortie["statut"] == "proposee"
    assert sortie["requires_approval"] is True

    # L'affectation proposee n'équipe pas la mission : pas de mission
    # « pourvue » tant que personne n'a approuvé.
    affectations = AffectationService(session)
    from app.domain.enums import StatutAffectation

    tracees = affectations.lister_pour_mission(mission.id)
    assert all(
        a.statut == StatutAffectation.PROPOSEE.value for a in tracees
    ), "une proposition d'agent ne vaut jamais affectation officielle"
