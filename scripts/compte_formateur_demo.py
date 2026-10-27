"""Compte formateur de démonstration — données rattachées, script idempotent.

Objectif : **rejouer le parcours formateur dans le navigateur** sans attendre
une donnée réelle. Le script crée (ou met à jour) un compte actif de rôle
``formateur``, la personne du vivier liée, une mission à laquelle elle est
**affectée et approuvée** (condition du périmètre), une session confirmée, des
bénéficiaires inscrits et pointés, et un CV déposé.

Idempotence
-----------
Relancer le script ne duplique rien : chaque entité est retrouvée par sa clé
métier (email, référence de mission, thème de session, identité de bénéficiaire,
nom de document). Le mot de passe de démonstration est **réécrit** à chaque
exécution : sa valeur est publique par construction, elle ne protège rien et ne
doit jamais servir hors d'un environnement de démonstration.

Le script n'écrit que par les **services métier** (`inscrire`, `activer`,
`proposer`/`approuver`, `planifier`, `inscrire`, `pointer`,
`enregistrer_document`) : aucune requête SQL hors repository, aucune décision
prise à la place d'un humain sur des données réelles.

Usage
-----
    cd backend
    ./.venv/Scripts/python.exe -m scripts.compte_formateur_demo
    ./.venv/Scripts/python.exe -m scripts.compte_formateur_demo --reinitialiser

``--reinitialiser`` supprime d'abord les données de démonstration (compte,
vivier, mission, session, documents) avant de les recréer.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, time, timedelta
from io import BytesIO
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.application.dto import (
    AffectationProposee,
    BeneficiaireInput,
    DocumentUploadInput,
    EquipeInput,
    MissionDirecteInput,
    OrganisationInput,
    ParticipationInscriptionInput,
    PresencePointageInput,
    SessionPlanificationInput,
)
from app.application.security import hasher_mot_de_passe
from app.application.services import (
    AffectationService,
    BeneficiaireService,
    DocumentService,
    EquipeService,
    LieuService,
    MissionService,
    OrganisationService,
    ParticipationService,
    PresenceService,
    SessionService,
    UtilisateurService,
)
from app.application.trace import Decision
from app.core.config import get_settings
from app.domain.document import Document
from app.domain.enums import (
    RoleUtilisateur,
    StatutEquipe,
    StatutMission,
    StatutPresence,
    StatutSessionFormation,
    StatutUtilisateur,
    TypeDocument,
)
from app.domain.execution import (
    AffectationEquipe,
    Beneficiaire,
    Mission,
    Participation,
    Presence,
    SessionFormation,
)
from app.domain.identity import Utilisateur
from app.domain.organization import Organisation
from app.infrastructure.database import SessionLocal
from app.infrastructure.repositories import (
    EquipeRepository,
    MissionRepository,
    OrganisationRepository,
    UtilisateurRepository,
)

logger = logging.getLogger("carso.demo")

# --- Identifiants de démonstration (publics par construction) -------------------

EMAIL = "formateur.demo@carso.mg"
MOT_DE_PASSE = "CarsoDemo2026!"
NOM = "Rakotomalala"
PRENOM = "Voahangy"
PROFIL = "Formatrice en gestion budgétaire et contrôle interne"

REFERENCE_MISSION = "DEMO-FORM-2026"
TITRE_MISSION = "Mission de démonstration — gestion budgétaire des collectivités"
THEME_SESSION = "Budgétisation de proximité et suivi des dépenses"
ORGANISATION = "Démo — Région d'Analamanga"
LIEU = "Antananarivo — démo"
NOM_CV = "cv_voahangy_rakotomalala.pdf"

#: Bénéficiaires fictifs (aucune donnée réelle n'est utilisée).
BENEFICIAIRES = (
    ("Rasoanaivo", "Hanta", "collectivité d'Ambatobe"),
    ("Andrianina", "Toky", "collectivité d'Ivandry"),
    ("Randriamampionona", "Naina", "collectivité d'Alasoratra"),
    ("Rabemananjara", "Miora", "collectivité d'Ankatsobo"),
    ("Rakotondrasoa", "Fanja", "collectivité d'Itaosy"),
)

#: Nombre de jours pointés au début de la session (contenu des fiches).
JOURS_PRESENCE = 2


# --- Génération du CV de démonstration -------------------------------------------


def _cv_demo() -> bytes:
    """PDF minimal du CV de démonstration (généré localement, sans donnée réelle).

    Polices cœur (latin-1) : le texte reste en ASCII/accents latins, ce qui
    évite d'embarquer une police Unicode dans un script de démonstration.
    """
    from fpdf import FPDF

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, f"{PRENOM} {NOM}", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", size=11)
    pdf.cell(0, 8, PROFIL, new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)
    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(0, 8, "Experience", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", size=11)
    for ligne in (
        "2021 - aujourd'hui : formatrice independante, budgets et marches publics.",
        "2018 - 2021 : responsable administrative, collectivite d'Antananarivo.",
        "2016 - 2018 : assistante de gestion, cabinet d'audit.",
    ):
        # `new_x/new_y` explicites : par défaut `multi_cell` laisse le curseur
        # en fin de ligne, et la ligne suivante n'aurait plus de place.
        pdf.multi_cell(0, 7, ligne, new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)
    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(0, 8, "Formations", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", size=11)
    pdf.multi_cell(
        0, 7, "Master en gestion des finances publiques (IRCAM).", new_x="LMARGIN", new_y="NEXT"
    )
    pdf.multi_cell(0, 7, "Certification audit interne (IFRI).", new_x="LMARGIN", new_y="NEXT")
    return bytes(pdf.output())


def _jours_presence(debut: date, fin: date) -> list[date]:
    """Jours ouvrés de la session à pointer, en ordre chronologique."""
    jours: list[date] = []
    jour = debut
    while jour <= fin and len(jours) < JOURS_PRESENCE:
        if jour.weekday() < 5:
            jours.append(jour)
        jour += timedelta(days=1)
    return jours


# --- Étapes idempotentes -----------------------------------------------------------


def _ou_creer_organisation(session: Session) -> Organisation:
    existante = OrganisationRepository(session).get_by_nom(ORGANISATION)
    if existante is not None:
        return existante
    return OrganisationService(session).creer(
        OrganisationInput(
            nom=ORGANISATION,
            type="collectivite",
            email="contact@demo.carso.mg",
        )
    )


def _ou_creer_vivier(session: Session):
    """Personne du vivier liée au compte (source de vérité du nom et du rôle)."""
    existante = EquipeRepository(session).get_by_email(EMAIL)
    if existante is None:
        return EquipeService(session).creer(
            EquipeInput(
                nom=NOM,
                prenom=PRENOM,
                email=EMAIL,
                telephone="+261 34 00 000 00",
                profil=PROFIL,
                statut=StatutEquipe.ACTIF.value,
                role_compte=RoleUtilisateur.FORMATEUR.value,
            )
        )
    if existante.statut != StatutEquipe.ACTIF.value:
        existante.statut = StatutEquipe.ACTIF.value
        session.flush()
    return existante


def _ou_creer_compte(session: Session, personne_id: UUID, admin_id: UUID) -> Utilisateur:
    """Compte actif de rôle ``formateur``, mot de passe de démonstration garanti."""
    service = UtilisateurService(session)
    compte = UtilisateurRepository(session).get_by_email(EMAIL)
    if compte is None:
        compte = service.inscrire(email=EMAIL, nom=NOM, prenom=PRENOM, mot_de_passe=MOT_DE_PASSE)
        compte = service.activer(
            compte.id, admin_id=admin_id, role=RoleUtilisateur.FORMATEUR.value
        )
        logger.info("Compte créé puis activé : %s", EMAIL)
    else:
        if compte.statut != StatutUtilisateur.ACTIF.value:
            compte = service.activer(
                compte.id, admin_id=admin_id, role=RoleUtilisateur.FORMATEUR.value
            )
            logger.info("Compte réactivé : %s", EMAIL)
        elif compte.role != RoleUtilisateur.FORMATEUR.value:
            compte = service.changer_role(
                compte.id, role=RoleUtilisateur.FORMATEUR.value, admin_id=admin_id
            )
            logger.info("Rôle aligné sur « formateur » : %s", EMAIL)
        # Mot de passe de démonstration : réécrit à chaque exécution (cf. module).
        compte.password_hash = hasher_mot_de_passe(MOT_DE_PASSE)
    if compte.equipe_id != personne_id:
        compte.equipe_id = personne_id
    session.flush()
    return compte


def _ou_creer_mission(session: Session, organisation_id: UUID, lieu_id: UUID) -> Mission:
    existante = MissionRepository(session).get_by_reference(REFERENCE_MISSION)
    if existante is not None:
        return existante
    debut = date.today() + timedelta(days=7)
    service = MissionService(session)
    mission = service.creer_directe(
        MissionDirecteInput(
            organisation_id=organisation_id,
            reference=REFERENCE_MISSION,
            titre=TITRE_MISSION,
            description=(
                "Mission de démonstration : cycle complet préparation/exécution "
                "(session, pointage, documents) pour valider le parcours formateur."
            ),
            date_debut=debut,
            date_fin=debut + timedelta(days=30),
            lieu_id=lieu_id,
        )
    )
    # Machine à états : la mission s'ouvre en « en preparation » (c'est la
    # phase de staffing : l'affectation y est possible). Le passage en
    # « en cours » a lieu après l'affectation (cf. `_lancer_mission`).
    return service.changer_statut(mission.id, StatutMission.PLANIFIE.value)


def _lancer_mission(session: Session, mission: Mission) -> Mission:
    """Passe la mission en « en cours » une fois l'équipe constituée."""
    if mission.statut == StatutMission.EN_COURS.value:
        return mission
    return MissionService(session).changer_statut(mission.id, StatutMission.EN_COURS.value)


def _ou_creer_affectation(
    session: Session, mission_id: UUID, personne_id: UUID
) -> AffectationEquipe:
    """Affectation **approuvée** — c'est elle qui ouvre le périmètre du compte."""
    existante = session.scalar(
        select(AffectationEquipe).where(
            AffectationEquipe.mission_id == mission_id,
            AffectationEquipe.equipe_id == personne_id,
        )
    )
    if existante is not None:
        return existante
    service = AffectationService(session, actor_id="demo")
    proposition = service.proposer(
        AffectationProposee(
            mission_id=mission_id,
            equipe_id=personne_id,
            role_dans_mission="formateur",
            proposed_by="demo",
            source="manual",
        )
    )
    return service.approuver(proposition.id, Decision(decided_by="demo", reason="Démonstration"))


def _ou_creer_session(session: Session, mission_id: UUID) -> SessionFormation:
    service = SessionService(session)
    for existante in service.lister_pour_mission(mission_id):
        if existante.theme == THEME_SESSION:
            return existante
    debut = date.today() + timedelta(days=14)
    planifiee = service.planifier(
        SessionPlanificationInput(
            mission_id=mission_id,
            theme=THEME_SESSION,
            date_debut=debut,
            date_fin=debut + timedelta(days=4),
            lieu=LIEU,
        )
    )
    # Une session confirmée donne au formateur une session à opérer.
    return service.changer_statut(planifiee.id, StatutSessionFormation.PLANIFIE.value)


def _ou_creer_participations(
    session: Session, session_formation_id: UUID
) -> list[Participation]:
    """Bénéficiaires fictifs inscrits à la session (sans doublon)."""
    service = ParticipationService(session)
    inscriptions = service.lister_pour_session(session_formation_id)
    deja = {participation.beneficiaire_id for participation in inscriptions}
    if len(deja) >= len(BENEFICIAIRES):
        return inscriptions
    service_beneficiaire = BeneficiaireService(session)
    for nom, prenom, origine in BENEFICIAIRES:
        beneficiaire = session.scalar(
            select(Beneficiaire).where(
                Beneficiaire.nom == nom, Beneficiaire.prenom == prenom
            )
        )
        if beneficiaire is None:
            beneficiaire = service_beneficiaire.creer(
                BeneficiaireInput(nom=nom, prenom=prenom, organisation_origine=origine)
            )
        if beneficiaire.id in deja:
            continue
        inscriptions.append(
            service.inscrire(
                session_formation_id,
                ParticipationInscriptionInput(beneficiaire_id=beneficiaire.id),
            )
        )
    return inscriptions


def _pointer(session: Session, participations: list[Participation], debut: date, fin: date) -> int:
    """Pointage des inscrits sur les jours ouvrés de la session."""
    service = PresenceService(session)
    poses = 0
    for jour in _jours_presence(debut, fin):
        for participation in participations:
            deja = service.lister_pour_participation(participation.id)
            if any(existant.date == jour for existant in deja):
                continue
            service.pointer_participation(
                participation.id,
                PresencePointageInput(
                    date=jour,
                    presence=StatutPresence.PRESENT.value,
                    heure_arrivee=time(8, 30),
                    heure_depart=time(12, 0),
                ),
            )
            poses += 1
    return poses


def _deposer_cv(session: Session, personne_id: UUID) -> Document | None:
    """Dépose le CV de démonstration sur la fiche du vivier (version 1)."""
    service = DocumentService(session, actor_id="demo")
    if service.documents.find_active_by_name(NOM_CV, TypeDocument.CV.value) is not None:
        return None
    return service.enregistrer_document(
        DocumentUploadInput(
            type_document=TypeDocument.CV.value,
            nom=NOM_CV,
            stream=BytesIO(_cv_demo()),
            created_by="demo",
            equipe_id=personne_id,
        )
    )


def _admin_id(session: Session) -> UUID:
    """Administrateur existant : il **décide** les validations du script."""
    admin = session.scalar(
        select(Utilisateur).where(Utilisateur.role == RoleUtilisateur.ADMINISTRATEUR.value)
    )
    if admin is None:
        raise SystemExit(
            "Aucun administrateur en base : démarrez l'API une fois pour que le "
            "bootstrap crée admin@carso.mg, puis relancez ce script."
        )
    return admin.id


def _supprimer_donnees(session: Session) -> None:
    """Supprime les données de démonstration (relance « à blanc »)."""
    compte = UtilisateurRepository(session).get_by_email(EMAIL)
    personne = EquipeRepository(session).get_by_email(EMAIL)
    mission = MissionRepository(session).get_by_reference(REFERENCE_MISSION)

    documents = session.scalars(select(Document).where(Document.nom == NOM_CV)).all()
    service_documents = DocumentService(session, actor_id="demo")
    for document in documents:
        try:
            service_documents.storage.remove(document.storage_path)
        except Exception as exc:  # noqa: BLE001 — fichier déjà absent : sans conséquence
            logger.warning("Fichier de démonstration absent (%s) : %s", document.storage_path, exc)
        session.delete(document)

    if mission is not None:
        sessions = session.scalars(
            select(SessionFormation).where(SessionFormation.mission_id == mission.id)
        ).all()
        sessions_id = [s.id for s in sessions] or [UUID(int=0)]
        participations = session.scalars(
            select(Participation).where(Participation.session_id.in_(sessions_id))
        ).all()
        participations_id = [p.id for p in participations] or [UUID(int=0)]
        for pointage in session.scalars(
            select(Presence).where(Presence.participation_id.in_(participations_id))
        ).all():
            session.delete(pointage)
        for participation in participations:
            session.delete(participation)
        for affectation in session.scalars(
            select(AffectationEquipe).where(AffectationEquipe.mission_id == mission.id)
        ).all():
            session.delete(affectation)
        for session_formation in sessions:
            session.delete(session_formation)
        session.delete(mission)

    if personne is not None:
        session.delete(personne)
    if compte is not None:
        session.delete(compte)
    session.flush()
    logger.info("Données de démonstration supprimées (compte, vivier, mission, documents)")


def _afficher(compte, mission, affectation, session_formation, participations, pointages, cv):
    print("\n" + "=" * 70)
    print("COMPTE FORMATEUR DE DÉMONSTRATION")
    print("=" * 70)
    print("  Page de connexion : /connexion")
    print(f"  Email             : {compte.email}")
    print(f"  Mot de passe      : {MOT_DE_PASSE}")
    print(f"  Rôle / statut     : {compte.role} / {compte.statut}")
    print(f"  Vivier            : {PRENOM} {NOM} — {PROFIL}")
    print(f"  Mission           : {mission.reference} — {mission.titre} ({mission.statut})")
    print(f"  Affectation       : {affectation.role_dans_mission} / {affectation.statut}")
    print(
        f"  Session           : {session_formation.theme} ({session_formation.statut}, "
        f"{session_formation.date_debut} → {session_formation.date_fin})"
    )
    print(f"  Bénéficiaires     : {len(participations)} inscrits, {pointages} pointages posés")
    print(f"  Document          : {NOM_CV if cv else 'déjà présent'}")
    print(f"  Stockage          : {get_settings().storage_root}")
    print("-" * 70)
    print("À rejouer : /tableau-bord, /missions, /sessions, /documents, /rapports,")
    print("puis le chat (agent_generaliste_readonly, agent_statistique,")
    print("agent_assistant_formateur).")
    print("=" * 70 + "\n")


def main() -> int:
    analyseur = argparse.ArgumentParser(description="Compte formateur de démonstration")
    analyseur.add_argument(
        "--reinitialiser",
        action="store_true",
        help="supprime les données de démonstration avant de les recréer",
    )
    arguments = analyseur.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")
    # Console Windows en cp1252 : le récapitulatif contient des accents et des
    # tirets typographiques. On affiche ce qu'on peut, jamais d'exception.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    session = SessionLocal()
    try:
        admin_id = _admin_id(session)
        if arguments.reinitialiser:
            _supprimer_donnees(session)
        organisation = _ou_creer_organisation(session)
        lieu = LieuService(session).obtenir_ou_creer(LIEU)
        personne = _ou_creer_vivier(session)
        compte = _ou_creer_compte(session, personne.id, admin_id)
        mission = _ou_creer_mission(session, organisation.id, lieu.id)
        affectation = _ou_creer_affectation(session, mission.id, personne.id)
        mission = _lancer_mission(session, mission)
        session_formation = _ou_creer_session(session, mission.id)
        participations = _ou_creer_participations(session, session_formation.id)
        pointages = _pointer(
            session,
            participations,
            session_formation.date_debut,
            session_formation.date_fin,
        )
        cv = _deposer_cv(session, personne.id)
        session.commit()
        _afficher(compte, mission, affectation, session_formation, participations, pointages, cv)
        return 0
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())