"""Phase 4 — services applicatifs : human-in-the-loop, zones de données, règles.

Tests purs sur SQLite mémoire : les hooks de compilation adaptent les types
PG (JSONB, gen_random_uuid) ; les sessions vérifient la logique applicative
sans commit — le commit appartient à l'appelant.
"""

import re
from collections.abc import Iterator
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.sql.ddl import CreateColumn

from app.application.dto import AffectationProposee, AppelInput, LigneBudgetInput
from app.application.services import (
    AffectationService,
    AppelService,
    OffreService,
)
from app.application.trace import Decision
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.domain.base import Base
from app.domain.document import Approbation, AuditEvent
from app.domain.enums import (
    DecisionApprobation,
    StatutAffectation,
    StatutAppel,
    StatutBudget,
    StatutMission,
    StatutOffre,
    TypeAppel,
    TypeOffre,
)
from app.domain.execution import Equipe, Mission
from app.domain.organization import Appel, Budget, Lot, Offre, Organisation
from app.domain.state_machines import validate_transition


@compiles(JSONB, "sqlite")
def _jsonb_as_json(type_: JSONB, compiler: object, **kw: object) -> str:
    return "JSON"


@compiles(CreateColumn, "sqlite")
def _strip_pg_server_defaults(element: CreateColumn, compiler: object, **kw: object) -> str:
    ddl = compiler.visit_create_column(element)  # type: ignore[attr-defined]
    return re.sub(r"DEFAULT gen_random_uuid\(\)", "", ddl)


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as s:
        yield s
    engine.dispose()


# --- Fixtures métier ----------------------------------------------------------


def _make_appel_en_analyse(session: Session) -> Appel:
    org = Organisation(nom="Client SARL")
    session.add(org)
    session.flush()  # id disponible avant référence
    appel = Appel(organisation_id=org.id, reference="AO-2026-01", titre="Formation")
    session.add(appel)
    session.flush()
    return appel


def _make_lot(session: Session, appel: Appel) -> Lot:
    lot = Lot(appel_id=appel.id, numero="1", titre="Lot 1")
    session.add(lot)
    session.flush()
    return lot


def _make_mission_planifiee(session: Session) -> Mission:
    org = Organisation(nom="Client Mission")
    session.add(org)
    session.flush()
    mission = Mission(
        organisation_id=org.id,
        reference="M-2026-01",
        titre="Formation trésorerie",
        statut=StatutMission.PLANIFIE.value,
    )
    session.add(mission)
    session.flush()
    return mission


def _make_equipe(session: Session) -> Equipe:
    membre = Equipe(nom="Alaoui", prenom="Sara", email="salaoui@example.com")
    session.add(membre)
    session.flush()
    return membre


# --- Machines à états ---------------------------------------------------------


class TestStateMachines:
    def test_transition_valide_passe(self) -> None:
        validate_transition(
            "offre",
            StatutOffre.BROUILLON.value,
            StatutOffre.EN_REVUE.value,
        )

    def test_transition_interdite_leve_conflict(self) -> None:
        with pytest.raises(ConflictError):
            validate_transition(
                "offre",
                StatutOffre.BROUILLON.value,
                StatutOffre.APPROUVE.value,
            )

    def test_budget_approuve_impossible_directement(self) -> None:
        """Human-in-the-loop : pas d'approbation sans passage par ``propose``."""
        with pytest.raises(ConflictError):
            validate_transition(
                "budget",
                StatutBudget.BROUILLON.value,
                StatutBudget.APPROUVE.value,
            )


# --- Appel à proposition : zone proposal / validation --------------------------------


class TestAppelService:
    def test_extraction_reste_en_zone_proposal(self, session: Session) -> None:
        appel = _make_appel_en_analyse(session)
        service = AppelService(session, actor_id="agent-ao")

        service.enregistrer_extraction(
            appel.id,
            {"lots": [{"numero": "1", "titre": "Formation"}]},
            proposed_by_agent="AgentAnalyseurAppel",
        )

        # Aucune table officielle écrite : seulement la zone proposal + statut.
        # L'enregistrement soumet la proposition : l'appel arrive en 'propose',
        # prêt pour la décision humaine (Phase 2 §3).
        assert appel.donnees_extraites["_meta"]["proposed_by_agent"] == (
            "AgentAnalyseurAppel"
        )
        assert appel.statut == StatutAppel.PROPOSE.value
        assert session.scalars(select(Lot)).all() == []

    def test_approbation_officialise_et_trace(self, session: Session) -> None:
        appel = _make_appel_en_analyse(session)
        service = AppelService(session)
        service.enregistrer_extraction(
            appel.id,
            {"lots": [{"numero": "1", "titre": "Lot Formation"}]},
            proposed_by_agent="agent-ao",
        )

        service.approuver_extraction(
            appel.id, Decision(decided_by="chef.carso", reason="conforme")
        )

        assert appel.statut == StatutAppel.VALIDE.value
        assert [lot.numero for lot in session.scalars(select(Lot)).all()] == ["1"]
        approbation = session.scalars(select(Approbation)).one()
        assert approbation.decided_by == "chef.carso"
        assert approbation.proposal_type == "extraction_appel_a_proposition"
        assert approbation.proposed_by_agent == "agent-ao"
        events = session.scalars(select(AuditEvent)).all()
        assert len(events) >= 2  # proposition (agent) + décision (humain)

    def test_refus_renvoie_en_correction_sans_effacer(self, session: Session) -> None:
        appel = _make_appel_en_analyse(session)
        service = AppelService(session)
        service.enregistrer_extraction(appel.id, {"lots": []}, proposed_by_agent="agent-ao")

        service.refuser_extraction(appel.id, Decision(decided_by="chef.carso"))

        assert appel.statut == StatutAppel.CORRIGE.value
        assert appel.donnees_extraites is not None  # conservé pour audit
        decision = session.scalars(select(Approbation)).one()
        assert decision.decision == DecisionApprobation.REJETE.value

    def test_validation_sans_proposition_impossible(self, session: Session) -> None:
        appel = _make_appel_en_analyse(session)
        service = AppelService(session)

        with pytest.raises(ConflictError):
            service.approuver_extraction(appel.id, Decision(decided_by="chef.carso"))

    def test_entite_absente_leve_not_found(self, session: Session) -> None:
        service = AppelService(session)
        with pytest.raises(NotFoundError):
            service.approuver_extraction(uuid4(), Decision(decided_by="x"))

    def test_enregistrer_appel_recu(self, session: Session) -> None:
        org = Organisation(nom="Client Enregistrement")
        session.add(org)
        session.flush()
        service = AppelService(session, actor_id="collab")

        appel = service.enregistrer(
            AppelInput(
                organisation_id=org.id,
                reference="ULA-097",
                titre="Incubation TEFIALA",
            )
        )

        assert appel.statut == StatutAppel.RECU.value
        assert appel.reference == "ULA-097"
        events = session.scalars(select(AuditEvent)).all()
        assert any(e.action == "appel_a_proposition.enregistre" for e in events)

    def test_enregistrer_reference_doublon(self, session: Session) -> None:
        appel = _make_appel_en_analyse(session)
        service = AppelService(session)
        with pytest.raises(ConflictError):
            service.enregistrer(
                AppelInput(
                    organisation_id=appel.organisation_id,
                    reference=appel.reference,
                    titre="Doublon",
                )
            )

    def test_approbation_materialise_lots_officiels(self, session: Session) -> None:
        """Validation humaine → lots officiels (1 lot = 1 ligne). Zone proposal intacte."""
        appel = _make_appel_en_analyse(session)
        service = AppelService(session)
        service.enregistrer_extraction(
            appel.id,
            {
                "lots": [
                    {
                        "numero": "1",
                        "titre": "Lot 1 PBM Ankatsakantsa",
                        "zone": "Atsimo Andrefana",
                    },
                    {
                        "numero": "2",
                        "titre": "Lot 2 PBM Ambahikily",
                        "zone": "Atsimo Andrefana",
                    },
                    {"numero": "3"},  # incomplet : ignoré, pas inventé
                ]
            },
            proposed_by_agent="agent-ao",
        )

        service.approuver_extraction(
            appel.id, Decision(decided_by="chef.carso", reason="4 lots RFP")
        )

        lots = session.scalars(select(Lot).order_by(Lot.numero)).all()
        assert [lot.numero for lot in lots] == ["1", "2"]
        assert lots[0].titre == "Lot 1 PBM Ankatsakantsa"
        assert lots[0].zone == "Atsimo Andrefana"
        assert appel.donnees_extraites is not None  # proposal conservée

    def test_approbation_ne_ecrase_pas_lot_manuel(self, session: Session) -> None:
        appel = _make_appel_en_analyse(session)
        existant = Lot(
            appel_id=appel.id, numero="1", titre="Lot saisi à la main"
        )
        session.add(existant)
        session.flush()
        service = AppelService(session)
        service.enregistrer_extraction(
            appel.id,
            {"lots": [{"numero": "1", "titre": "Lot extrait IA"}]},
            proposed_by_agent="agent-ao",
        )

        service.approuver_extraction(appel.id, Decision(decided_by="chef.carso"))

        lots = session.scalars(select(Lot)).all()
        assert len(lots) == 1
        assert lots[0].titre == "Lot saisi à la main"

    def test_validation_sans_lot_cree_le_lot_unique(self, session: Session) -> None:
        """ADR 0012 (règle 26/10) : un appel sans lot reçoit un lot unique « 1 ».

        Le lot porte le **titre de l'appel** et le numéro ``1`` : les offres de
        cet appel y sont rattachées, aucune n'est orpheline. L'audit de la
        décision dit que le lot vient du repli, pas d'une extraction.
        """
        appel = _make_appel_en_analyse(session)
        service = AppelService(session)
        service.enregistrer_extraction(
            appel.id, {"lots": []}, proposed_by_agent="agent-ao"
        )

        service.approuver_extraction(appel.id, Decision(decided_by="chef.carso"))

        assert appel.statut == StatutAppel.VALIDE.value
        lots = session.scalars(select(Lot)).all()
        assert len(lots) == 1
        assert lots[0].numero == "1"
        assert lots[0].titre == appel.titre

    def test_validation_avec_lots_inexploitables_cree_aussi_le_lot_unique(
        self, session: Session
    ) -> None:
        """Des lots sans numéro ni titre n'officialisent rien **d'eux-mêmes**
        (aucun lot inventé depuis un champ absent) : le repli s'applique."""
        appel = _make_appel_en_analyse(session)
        service = AppelService(session)
        service.enregistrer_extraction(
            appel.id,
            {"lots": [{"titre": "Titre sans numéro"}, {"numero": "2"}]},
            proposed_by_agent="agent-ao",
        )

        service.approuver_extraction(appel.id, Decision(decided_by="chef.carso"))

        assert appel.statut == StatutAppel.VALIDE.value
        lots = session.scalars(select(Lot)).all()
        assert len(lots) == 1
        assert lots[0].numero == "1"

    def test_validation_acceptee_si_lot_manuel_deja_present(self, session: Session) -> None:
        """Cas légitime conservé : un lot saisi à la main suffit à officialiser."""
        appel = _make_appel_en_analyse(session)
        _make_lot(session, appel)
        service = AppelService(session)
        service.enregistrer_extraction(
            appel.id, {"lots": []}, proposed_by_agent="agent-ao"
        )

        service.approuver_extraction(appel.id, Decision(decided_by="chef.carso"))

        assert appel.statut == StatutAppel.VALIDE.value
        assert len(session.scalars(select(Lot)).all()) == 1

    def test_proposition_corrigee_remet_en_propose(self, session: Session) -> None:
        """L'appel validé sans lot redevient réparable par une nouvelle proposition."""
        appel = _make_appel_en_analyse(session)
        service = AppelService(session)
        service.enregistrer_extraction(
            appel.id, {"lots": []}, proposed_by_agent="agent-ao"
        )
        # On force l'état figeant observé en base (validation historique sans garde).
        appel.statut = StatutAppel.VALIDE

        service.enregistrer_proposition_corrigee(
            appel.id,
            {"lots": [{"numero": "1", "titre": "Lot retrouvé"}]},
            proposed_by_agent="agent-ao",
        )

        assert appel.statut == StatutAppel.PROPOSE.value
        assert session.scalars(select(Lot)).all() == []  # rien d'officiel avant approbation

        service.approuver_extraction(appel.id, Decision(decided_by="chef.carso"))
        lots = session.scalars(select(Lot)).all()
        assert [(lot.numero, lot.titre) for lot in lots] == [("1", "Lot retrouvé")]

    def test_proposition_corrigee_refusee_si_offre_existe(self, session: Session) -> None:
        """La proposition d'un appel qui a produit du concret n'est plus modifiable."""
        appel = _make_appel_en_analyse(session)
        lot = _make_lot(session, appel)
        OffreService(session).creer_brouillon(
            lot.id, "OF-1", "Offre Lot 1", type=TypeOffre.OFFRE_TECHNIQUE.value
        )
        service = AppelService(session)
        service.enregistrer_extraction(
            appel.id, {"lots": []}, proposed_by_agent="agent-ao"
        )
        appel.statut = StatutAppel.VALIDE

        with pytest.raises(ConflictError):
            service.enregistrer_proposition_corrigee(
                appel.id,
                {"lots": [{"numero": "1", "titre": "Lot réécrit"}]},
                proposed_by_agent="agent-ao",
            )

        assert appel.statut == StatutAppel.VALIDE.value
        assert appel.donnees_extraites["lots"] == []  # proposition intacte

    def test_proposition_corrigee_vide_acceptee(self, session: Session) -> None:
        """Une re-soumission sans lot est **légitime** depuis le 26/10.

        La validation crée alors le lot unique « 1 » : refuser la proposition
        ici empêcherait le seul chemin qui produit un appel sans lot.
        """
        appel = _make_appel_en_analyse(session)
        service = AppelService(session)
        service.enregistrer_extraction(
            appel.id,
            {"lots": [{"numero": "1", "titre": "Lot d'origine"}]},
            proposed_by_agent="agent-ao",
        )
        appel.statut = StatutAppel.VALIDE

        service.enregistrer_proposition_corrigee(
            appel.id, {"lots": []}, proposed_by_agent="agent-ao"
        )

        assert appel.statut == StatutAppel.PROPOSE.value
        assert appel.donnees_extraites["lots"] == []


# --- Offre de formation : cardinalité configurable + budget ---------------------


class TestOffreService:
    def test_cree_brouillon_depuis_lot(self, session: Session) -> None:
        appel = _make_appel_en_analyse(session)
        lot = _make_lot(session, appel)
        service = OffreService(session)

        offre = service.creer_brouillon(
            lot.id, "OF-2026-01", "Offre Lot 1", type=TypeOffre.OFFRE_TECHNIQUE.value
        )

        assert offre.statut == StatutOffre.BROUILLON.value
        assert offre.type == TypeOffre.OFFRE_TECHNIQUE.value
        assert offre.organisation_id == appel.organisation_id
        assert offre.appel_id == appel.id
        assert offre.lot_id == lot.id

    def test_le_type_d_offre_est_controlé_par_le_type_d_appel(
        self, session: Session
    ) -> None:
        """Règle 26/10 : un appel à proposition se répond par une offre technique
        et/ou financière ; une manifestation d'intérêt par une offre « autre ».
        """
        appel = _make_appel_en_analyse(session)  # appel à proposition (défaut)
        lot = _make_lot(session, appel)
        service = OffreService(session)

        with pytest.raises(ValidationError, match="nature de l'appel"):
            service.creer_brouillon(
                lot.id, "OF-AUTRE-INTERDIT", "Offre", type=TypeOffre.AUTRE.value
            )

        # La même offre est légitime sur une manifestation d'intérêt.
        appel.type = TypeAppel.APPEL_A_MANIFESTATION_INTERET.value
        session.flush()
        offre = service.creer_brouillon(
            lot.id, "OF-AUTRE-AMI", "Offre AMI", type=TypeOffre.AUTRE.value
        )
        assert offre.type == TypeOffre.AUTRE.value

        # Et réciproquement : une offre technique n'y a pas sa place.
        lot_ami = Lot(appel_id=appel.id, numero="2", titre="Lot AMI")
        session.add(lot_ami)
        session.flush()
        with pytest.raises(ValidationError, match="nature de l'appel"):
            service.creer_brouillon(
                lot_ami.id, "OF-TECH-INTERDIT", "Offre", type=TypeOffre.OFFRE_TECHNIQUE.value
            )

    def test_un_lot_n_accepte_qu_une_offre_par_type(self, session: Session) -> None:
        """Un lot porte au plus une offre de chaque type (technique + financière)."""
        appel = _make_appel_en_analyse(session)
        lot = _make_lot(session, appel)
        service = OffreService(session)
        service.creer_brouillon(
            lot.id, "OF-2026-01b", "Offre technique", type=TypeOffre.OFFRE_TECHNIQUE.value
        )

        with pytest.raises(ConflictError, match="existe déjà"):
            service.creer_brouillon(
                lot.id, "OF-2026-01c", "Seconde technique", type=TypeOffre.OFFRE_TECHNIQUE.value
            )

        # Un type différent reste possible : la réponse technique + financière.
        financiere = service.creer_brouillon(
            lot.id, "OF-2026-01d", "Offre financière", type=TypeOffre.OFFRE_FINANCIERE.value
        )
        assert financiere.type == TypeOffre.OFFRE_FINANCIERE.value

    def test_type_d_offre_inconnu_refuse(self, session: Session) -> None:
        appel = _make_appel_en_analyse(session)
        lot = _make_lot(session, appel)
        service = OffreService(session)

        with pytest.raises(Exception, match="Type d'offre inconnu"):
            service.creer_brouillon(lot.id, "OF-X", "Offre", type="type_invente")

    def test_reference_deja_prise_refusee_409(self, session: Session) -> None:
        """Régression : une référence déjà utilisée ne doit plus lever 500.

        Les deux offres sont des types **compatibles avec l'appel à
        proposition** (règle 26/10 : le type est contrôlé contre la nature de
        l'appel avant la référence).
        """
        appel = _make_appel_en_analyse(session)
        lot = _make_lot(session, appel)
        service = OffreService(session)
        service.creer_brouillon(
            lot.id, "OFFRE-FIN-L1", "Offre", type=TypeOffre.OFFRE_FINANCIERE.value
        )

        with pytest.raises(ConflictError, match="OFFRE-FIN-L1"):
            service.creer_brouillon(
                lot.id, "OFFRE-FIN-L1", "Doublon", type=TypeOffre.OFFRE_TECHNIQUE.value
            )

    def test_reference_auto_attribuee_unique_entre_appels(self, session: Session) -> None:
        """Deux appels portant un lot de même numéro : les références ne se
        battent plus (OFFRE-AUTRE-L1, puis OFFRE-AUTRE-L1-2)."""
        service = OffreService(session)
        appel_a = _make_appel_en_analyse(session)
        # Une offre « autre » répond à une manifestation d'intérêt, pas à un
        # appel à proposition (règle 26/10).
        appel_a.type = TypeAppel.APPEL_A_MANIFESTATION_INTERET.value
        lot_a = _make_lot(session, appel_a)
        offre_a = service.creer_brouillon(
            lot_a.id, None, "Offre appel A", type=TypeOffre.AUTRE.value
        )
        assert offre_a.reference == "OFFRE-AUTRE-L1"

        # Second appel, même numéro de lot : la base prise est contournée.
        autre_appel = Appel(
            organisation_id=appel_a.organisation_id,
            reference="AO-2026-02",
            titre="Second appel",
            type=TypeAppel.APPEL_A_MANIFESTATION_INTERET.value,
        )
        session.add(autre_appel)
        session.flush()
        lot_b = Lot(appel_id=autre_appel.id, numero="1", titre="Lot 1 B")
        session.add(lot_b)
        session.flush()
        offre_b = service.creer_brouillon(
            lot_b.id, None, "Offre appel B", type=TypeOffre.AUTRE.value
        )
        assert offre_b.reference == "OFFRE-AUTRE-L1-2"

        references = [
            offre.reference for offre in session.scalars(select(Offre)).all()
        ]
        assert len(references) == len(set(references))  # unicité réelle

    def test_publication_exige_la_revue(self, session: Session) -> None:
        appel = _make_appel_en_analyse(session)
        lot = _make_lot(session, appel)
        service = OffreService(session)
        offre = service.creer_brouillon(
            lot.id, "OF-2026-02", "Offre", type=TypeOffre.OFFRE_TECHNIQUE.value
        )

        with pytest.raises(ConflictError):
            service.publier(offre.id, Decision(decided_by="chef.carso"))

    def test_publication_tracee_avec_horodatage(self, session: Session) -> None:
        appel = _make_appel_en_analyse(session)
        lot = _make_lot(session, appel)
        service = OffreService(session)
        offre = service.creer_brouillon(
            lot.id, "OF-2026-03", "Offre", type=TypeOffre.OFFRE_TECHNIQUE.value
        )
        service.soumettre_revue(offre.id)

        service.publier(offre.id, Decision(decided_by="chef.carso"))

        assert offre.statut == StatutOffre.APPROUVE.value
        assert offre.approved_at is not None
        decision = session.scalars(select(Approbation)).one()
        assert decision.decided_by == "chef.carso"

    def test_budget_total_recalcule_depuis_lignes(self, session: Session) -> None:
        """Règle 7 : le total est recalculé par le serveur, jamais stocké aveuglément."""
        appel = _make_appel_en_analyse(session)
        lot = _make_lot(session, appel)
        service = OffreService(session)
        offre = service.creer_brouillon(
            lot.id, "OF-2026-04", "Offre", type=TypeOffre.OFFRE_TECHNIQUE.value
        )
        budget = Budget(offre_id=offre.id, devise="MAD")
        session.add(budget)
        session.flush()

        service.ajouter_ligne_budget(
            budget.id, LigneBudgetInput("formation", Decimal("3"), Decimal("1000"))
        )
        service.ajouter_ligne_budget(
            budget.id, LigneBudgetInput("logistique", Decimal("2.5"), Decimal("200"))
        )

        total = service.total_budget(budget.id)
        assert total.total == Decimal("3500.00")
        assert total.nb_lignes == 2

    def test_budget_approuve_est_verrouille(self, session: Session) -> None:
        appel = _make_appel_en_analyse(session)
        lot = _make_lot(session, appel)
        service = OffreService(session)
        offre = service.creer_brouillon(
            lot.id, "OF-2026-05", "Offre", type=TypeOffre.OFFRE_TECHNIQUE.value
        )
        budget = Budget(offre_id=offre.id, statut=StatutBudget.APPROUVE.value)
        session.add(budget)
        session.flush()

        with pytest.raises(ConflictError, match="nouvelle version"):
            service.ajouter_ligne_budget(
                budget.id, LigneBudgetInput("x", Decimal("1"), Decimal("1"))
            )


# --- Affectations : human-in-the-loop ------------------------------------------


class TestAffectationService:
    def test_proposition_agent_reste_proposee(self, session: Session) -> None:
        mission = _make_mission_planifiee(session)
        membre = _make_equipe(session)
        service = AffectationService(session)

        affectation = service.proposer(
            AffectationProposee(
                mission_id=mission.id,
                equipe_id=membre.id,
                role_dans_mission="formateur",
                proposed_by="AgentRH",
                source="ai_proposal",
            )
        )

        assert affectation.statut == StatutAffectation.PROPOSEE.value
        assert affectation.source_affectation == "ai_proposal"

    def test_approbation_renseigne_approved_by_et_trace(self, session: Session) -> None:
        mission = _make_mission_planifiee(session)
        membre = _make_equipe(session)
        service = AffectationService(session)
        affectation = service.proposer(
            AffectationProposee(
                mission_id=mission.id,
                equipe_id=membre.id,
                role_dans_mission="chef_mission",
                proposed_by="chef.carso",
            )
        )

        service.approuver(affectation.id, Decision(decided_by="dg.carso"))

        assert affectation.statut == StatutAffectation.APPROUVEE.value
        assert affectation.approved_by == "dg.carso"
        decision = session.scalars(select(Approbation)).one()
        assert decision.entity_type == "affectation_equipe"

    def test_mission_cloturee_refuse_le_staffing(self, session: Session) -> None:
        mission = _make_mission_planifiee(session)
        mission.statut = StatutMission.CLOTURE.value
        membre = _make_equipe(session)
        service = AffectationService(session)

        with pytest.raises(ConflictError, match="staffing"):
            service.proposer(
                AffectationProposee(
                    mission_id=mission.id,
                    equipe_id=membre.id,
                    role_dans_mission="formateur",
                    proposed_by="x",
                )
            )

    def test_role_libre_pas_de_liste_inventee(self, session: Session) -> None:
        """Q5 [P] : aucun vocabulaire de rôle imposé par le code."""
        mission = _make_mission_planifiee(session)
        membre = _make_equipe(session)
        service = AffectationService(session)

        affectation = service.proposer(
            AffectationProposee(
                mission_id=mission.id,
                equipe_id=membre.id,
                role_dans_mission="coordinateur_pedagogique",
                proposed_by="x",
            )
        )
        assert affectation.role_dans_mission == "coordinateur_pedagogique"


# --- Garde-fous budgets ----------------------------------------------------------


class TestBudgetGuards:
    def test_ajout_ligne_sur_budget_inconnu(self, session: Session) -> None:
        service = OffreService(session)
        with pytest.raises(NotFoundError):
            service.ajouter_ligne_budget(
                uuid4(), LigneBudgetInput("x", Decimal("1"), Decimal("1"))
            )
