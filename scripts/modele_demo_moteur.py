"""Modèle DOCX de démonstration pour le moteur documentaire (INC-128).

Crée — de façon **idempotente** — une organisation de démonstration, un DOCX de
modèle réaliste (logo, en-tête, pied, tableau) et son entrée au registre des
modèles. Sert à rejouer la chaîne « structure → validation confirmée →
génération » dans le navigateur, sans attendre un fichier client réel.

Le modèle est laissé **non validé** : la validation doit rester une décision
humaine, prise dans la conversation. Relancer le script ne duplique rien.

Usage :
    cd backend
    ./.venv/Scripts/python.exe -m scripts.modele_demo_moteur
"""

from __future__ import annotations

import io
import sys

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Pt, RGBColor
from PIL import Image
from sqlalchemy.orm import Session

from app.application.dto import DocumentUploadInput, OrganisationInput
from app.application.services.document_service import DocumentService
from app.application.services.modele_document_service import ModeleDocumentService
from app.application.services.organisation_service import OrganisationService
from app.domain.organization import Organisation
from app.infrastructure.database import SessionLocal
from app.infrastructure.repositories.organisation import OrganisationRepository

NOM_ORGANISATION = "Organisation démo — modèle DOCX"
NOM_MODELE = "Modèle démo — offre technique v2"
NOM_FICHIER = "modele_demo_offre_technique_v2.docx"
EMAIL_COMPTE = "collaborateur.demo@carso.mg"
MOT_DE_PASSE = "CarsoDemo2026!"  # démonstration uniquement, jamais hors dev
TYPE_MODELE = "modele_offre_technique"
REFERENCE_APPEL = "DEMO-MODELE-2026"
REFERENCE_OFFRE = "OFFRE-DEMO-MODELE"
REFERENCE_MISSION = "MISSION-DEMO-MODELE"


def _logo() -> bytes:
    """Petit PNG uni : suffit à prouver que les images restent intactes."""
    tampon = io.BytesIO()
    Image.new("RGB", (64, 32), (31, 78, 121)).save(tampon, format="PNG")
    return tampon.getvalue()


def _document_modele() -> bytes:
    document = Document()
    section = document.sections[0]
    section.header.paragraphs[0].text = "Organisation démo — Direction des programmes"
    section.footer.paragraphs[0].text = "Document de démonstration — usage interne"
    document.add_picture(io.BytesIO(_logo()))

    document.add_paragraph("{{titre_offre}}")
    client = document.add_paragraph()
    client.add_run("Client : ").bold = True
    prestation = client.add_run("{{client}}")
    prestation.italic = True
    prestation.font.size = Pt(12)
    prestation.font.color.rgb = RGBColor(0x1F, 0x4E, 0x79)
    client.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY

    document.add_heading("1. Contexte", level=1)
    document.add_paragraph("Texte fixe du modèle : ne doit jamais changer.")
    document.add_heading("2. Activités", level=1)
    tableau = document.add_table(rows=3, cols=3)
    tableau.style = "Table Grid"
    for colonne, texte in enumerate(("Activité", "Durée", "Responsable")):
        tableau.cell(0, colonne).text = texte
    tableau.cell(1, 0).text = "Formation"
    tableau.cell(1, 1).text = "5 jours"
    tableau.cell(1, 2).text = "Formateur principal"
    tableau.cell(2, 0).text = "Suivi"
    tableau.cell(2, 1).text = "2 jours"
    tableau.cell(2, 2).text = "Coach"
    document.add_heading("3. Méthodologie", level=1)
    document.add_paragraph("Méthode du modèle, à adapter.")
    tampon = io.BytesIO()
    document.save(tampon)
    return tampon.getvalue()


def _organisation(session: Session) -> Organisation:
    existante = OrganisationRepository(session).get_by_nom(NOM_ORGANISATION)
    if existante is not None:
        return existante
    return OrganisationService(session).creer(
        OrganisationInput(nom=NOM_ORGANISATION, type="entreprise")
    )


def _compte_interne(session: Session):
    """Compte collaborateur actif (interne) : périmètre complet, démo seulement.

    Un formateur ne voit que ses missions : un modèle rattaché à une
    organisation hors périmètre lui répond « introuvable ». Ce compte de
    démonstration représente donc l'équipe interne qui valide et génère.
    """
    from app.application.security import hasher_mot_de_passe
    from app.application.services.utilisateur_service import UtilisateurService
    from app.domain.enums import RoleUtilisateur, StatutUtilisateur
    from app.domain.identity import Utilisateur
    from app.infrastructure.repositories.utilisateur import UtilisateurRepository

    comptes = UtilisateurRepository(session)
    admin = session.scalars(
        session.query(Utilisateur).filter(
            Utilisateur.role == RoleUtilisateur.ADMINISTRATEUR.value
        ).statement
    ).first()
    if admin is None:
        raise SystemExit("Aucun administrateur en base : démarrer l'API une fois.")
    from app.application.dto import EquipeInput
    from app.application.services.equipe_service import EquipeService
    from app.domain.enums import StatutEquipe
    from app.infrastructure.repositories import EquipeRepository

    personne = EquipeRepository(session).get_by_email(EMAIL_COMPTE)
    if personne is None:
        personne = EquipeService(session).creer(
            EquipeInput(
                nom="Démo",
                prenom="Collaborateur",
                email=EMAIL_COMPTE,
                statut=StatutEquipe.ACTIF.value,
                role_compte=RoleUtilisateur.COLLABORATEUR.value,
            )
        )
    service = UtilisateurService(session)
    compte = comptes.get_by_email(EMAIL_COMPTE)
    if compte is None:
        compte = service.inscrire(
            email=EMAIL_COMPTE,
            nom="Démo",
            prenom="Collaborateur",
            mot_de_passe=MOT_DE_PASSE,
        )
        compte = service.activer(
            compte.id, admin_id=admin.id, role=RoleUtilisateur.COLLABORATEUR.value
        )
    if compte.equipe_id != personne.id:
        compte.equipe_id = personne.id
    if compte and compte.statut != StatutUtilisateur.ACTIF.value:
        compte = service.activer(
            compte.id, admin_id=admin.id, role=RoleUtilisateur.COLLABORATEUR.value
        )
    compte.password_hash = hasher_mot_de_passe(MOT_DE_PASSE)
    session.flush()
    return compte


def _offre_demo(session: Session, organisation: Organisation, modele):
    """Offre de démonstration rattachée à un appel et un lot (idempotent).

    La chaîne « copier le modèle dans l'offre » a besoin d'un objet métier :
    sans offre, il n'y a rien à éditer. Aucune donnée n'est inventée pour
    autant — l'appel, le lot et l'offre sont explicites et relançables.
    """
    from app.domain.enums import StatutAppel, StatutOffre, TypeAppel, TypeOffre
    from app.domain.organization import Appel, Lot, Offre
    from app.infrastructure.repositories.appel import AppelRepository
    from app.infrastructure.repositories.offre import OffreRepository

    offres = OffreRepository(session)
    existante = offres.get_by_reference(REFERENCE_OFFRE)
    if existante is not None:
        return existante
    appels = AppelRepository(session)
    appel = appels.get_by_reference(REFERENCE_APPEL)
    if appel is None:
        appel = Appel(
            organisation_id=organisation.id,
            type=TypeAppel.APPEL_A_PROPOSITION.value,
            reference=REFERENCE_APPEL,
            titre="Appel de démonstration — formation IA appliquée",
            statut=StatutAppel.RECU.value,
        )
        session.add(appel)
        session.flush()
    lot = next((item for item in appel.lots if item.numero == "1"), None)
    if lot is None:
        lot = Lot(
            appel_id=appel.id,
            numero="1",
            titre="Lot 1 — renforcement des capacités en intelligence artificielle",
            objectifs="Former les équipes aux usages professionnels de l'IA.",
            resultats_attendus="Équipes autonomes sur les cas d'usage prioritaires.",
        )
        session.add(lot)
        session.flush()
    offre = Offre(
        organisation_id=organisation.id,
        appel_id=appel.id,
        lot_id=lot.id,
        type=TypeOffre.OFFRE_TECHNIQUE.value,
        reference=REFERENCE_OFFRE,
        titre="Offre technique de démonstration — formation IA appliquée",
        statut=StatutOffre.BROUILLON.value,
        version=1,
        # ``offres.modele_document_id`` référence le **fichier** du modèle
        # (table ``documents``), pas l'entrée du registre.
        modele_document_id=modele.document_template_id,
    )
    session.add(offre)
    session.flush()
    return offre


def _mission_demo(session: Session, organisation: Organisation, offre):
    """Mission + session de démonstration (idempotent).

    L'assistant formateur applique les modèles sur une **mission** ou une
    **session** : sans elles, la chaîne n'a nulle part où copier le fichier.
    """
    from datetime import date, timedelta

    from app.domain.enums import StatutMission, StatutSessionFormation
    from app.domain.execution import Mission, SessionFormation
    from app.infrastructure.repositories.mission import MissionRepository

    missions = MissionRepository(session)
    mission = missions.get_by_reference(REFERENCE_MISSION)
    if mission is not None:
        return mission, mission.session
    debut = date.today()
    mission = Mission(
        organisation_id=organisation.id,
        lot_id=offre.lot_id,
        reference=REFERENCE_MISSION,
        titre="Mission de démonstration — formation IA appliquée",
        description=(
            "Mission de démonstration : sert à rejouer la chaîne « copier le "
            "modèle puis l'éditer » depuis la section Missions."
        ),
        date_debut=debut,
        date_fin=debut + timedelta(days=5),
        statut=StatutMission.PLANIFIE.value,
    )
    session.add(mission)
    session.flush()
    session_formation = SessionFormation(
        mission_id=mission.id,
        date_debut=debut,
        date_fin=debut + timedelta(days=5),
        lieu="Toliara",
        theme="Formation IA appliquée — session 1",
        statut=StatutSessionFormation.PLANIFIE.value,
    )
    session.add(session_formation)
    session.flush()
    return mission, session_formation


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    session = SessionLocal()
    try:
        organisation = _organisation(session)
        documents = DocumentService(session, actor_id="demo")
        registre = ModeleDocumentService(session)
        # Chercher le modèle d'abord : c'est lui qui référence le fichier. Un
        # fichier orphelin (exécution interrompue) ne doit pas empêcher la
        # relance — d'où le suffixe unique en cas de conflit de stockage.
        modeles = registre.lister(organisation.id)
        modele = next((m for m in modeles if m.nom == NOM_MODELE), None)
        if modele is None:
            from uuid import uuid4

            from app.core.errors import ConflictError

            nom_fichier = NOM_FICHIER
            try:
                existant = documents.enregistrer_document(
                    DocumentUploadInput(
                        type_document="template",
                        nom=nom_fichier,
                        stream=io.BytesIO(_document_modele()),
                        created_by="demo",
                        organisation_id=organisation.id,
                    )
                )
            except ConflictError:
                nom_fichier = f"{NOM_FICHIER[:-5]}-{uuid4().hex[:6]}.docx"
                existant = documents.enregistrer_document(
                    DocumentUploadInput(
                        type_document="template",
                        nom=nom_fichier,
                        stream=io.BytesIO(_document_modele()),
                        created_by="demo",
                        organisation_id=organisation.id,
                    )
                )
            modele = registre.enregistrer_modele(
                organisation.id,
                nom=NOM_MODELE,
                type_document=TYPE_MODELE,
                document_template_id=existant.id,
                registered_by="demo",
            )
        # Le fichier du modèle suffit à la chaîne INC-129 : il est copié dans
        # l'offre puis édité. L'analyse (lecture déterministe des éléments) est
        # conservée pour que l'interface montre sa structure.
        from app.application.services.moteur_documentaire_service import (
            MoteurDocumentaireService,
        )

        moteur = MoteurDocumentaireService(
            session, document_service=documents, actor_id="demo"
        )
        if not modele.analyse:
            moteur.analyser(organisation.id, modele.id)
        offre = _offre_demo(session, organisation, modele)
        mission, session_formation = _mission_demo(session, organisation, offre)
        compte = _compte_interne(session)
        session.commit()
        print(f"compte_interne   = {compte.email} / {MOT_DE_PASSE}")
        print(f"organisation_id = {organisation.id}")
        print(f"organisation     = {organisation.nom}")
        print(f"modele_id        = {modele.id}")
        print(f"modele           = {modele.nom} (v{modele.version})")
        print(f"type_modele      = {modele.type_document}")
        print(f"offre_id         = {offre.id}")
        print(f"offre            = {offre.reference} ({offre.type})")
        print(f"mission_id       = {mission.id}")
        print(f"mission          = {mission.reference} ({mission.statut})")
        print(f"session_id       = {session_formation.id}")
        print(
            "chaîne           = copier_modele_document → "
            "obtenir_structure_document → remplir_modele / remplacer_element_model"
        )
        return 0
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
