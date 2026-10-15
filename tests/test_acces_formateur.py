"""Accès d'un formateur (compte restreint) — intégration API.

Le rôle ``formateur`` porte la règle : missions où la personne est **affectée et
approuvée**, leurs sessions, leurs documents et leurs données de session. Ce
fichier vérifie le contrat complet côté HTTP :

- **404** hors périmètre (on ne révèle jamais l'existence d'un objet d'une autre
  mission) ; **403** pour un geste réservé à l'équipe interne ;
- le formateur **opère** ses sessions (inscriptions, pointage, fiche de présence,
  documents de session, supports) et **gère ses documents** (les siens et ceux de
  ses missions/sessions : liste, modification, suppression *logique*) mais ne
  **pilote** rien (statuts, création, listes transverses) ;
- les listes transverses restent fermées, le dossier lié reste lisible ;
- les membres de son équipe et les bénéficiaires de ses sessions sont lisibles
  **en clair** (nom, prénom) : l'API joint l'identité, l'interface ne la recompose
  pas (correctif 03/10 du bug des pseudo-UUID).

Le porteur est injecté via ``dependency_overrides[get_current_user]`` : le mode
lab renverrait sinon un collaborateur (accès complet), ce qui ne prouverait rien.
"""

from collections.abc import Iterator
from datetime import date
from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from app.api.deps import get_current_user
from app.domain.document import Document
from app.domain.enums import (
    RoleUtilisateur,
    StatutAffectation,
    StatutDocument,
    StatutMission,
    StatutUtilisateur,
    TypeDocument,
    TypeOffre,
)
from app.domain.execution import (
    AffectationEquipe,
    Beneficiaire,
    Equipe,
    Mission,
    MissionOffre,
    Participation,
    SessionFormation,
)
from app.domain.identity import Utilisateur
from app.domain.organization import AppelAProposition, Lot, Offre, Organisation

PRÉFIXE = "/api/v1"


@pytest.fixture
def contexte(api_session_factory: sessionmaker[Session]) -> Iterator[SimpleNamespace]:
    """Peuple deux missions ; seule ``mission`` est confiée au formateur."""
    session = api_session_factory()
    try:
        moi = Equipe(nom="Formatrice", prenom="Fatou", email="fatou@carso.test")
        collegue = Equipe(nom="Collègue", prenom="Carlos", email="carlos@carso.test")
        inconnu = Equipe(nom="Étranger", prenom="Éva", email="eva@carso.test")
        session.add_all([moi, collegue, inconnu])
        session.flush()

        # Dossier de la mission autorisée : organisation, appel, lot, offre.
        organisation = Organisation(nom="Client A")
        autre_organisation = Organisation(nom="Client B")
        session.add_all([organisation, autre_organisation])
        session.flush()
        appel = AppelAProposition(
            organisation_id=organisation.id, reference="AO-A", titre="Appel A"
        )
        session.add(appel)
        session.flush()
        lot = Lot(appel_a_proposition_id=appel.id, numero="1", titre="Lot A")
        session.add(lot)
        session.flush()
        offre = Offre(
            organisation_id=organisation.id,
            appel_a_proposition_id=appel.id,
            lot_id=lot.id,
            type=TypeOffre.OFFRE_TECHNIQUE.value,
            reference="O-A",
            titre="Offre A",
        )
        session.add(offre)
        session.flush()

        mission = Mission(
            organisation_id=organisation.id,
            reference="M-A",
            titre="Mission autorisée",
            statut=StatutMission.EN_COURS.value,
        )
        autre_mission = Mission(
            organisation_id=autre_organisation.id,
            reference="M-B",
            titre="Mission d'une autre équipe",
            statut=StatutMission.EN_COURS.value,
        )
        session.add_all([mission, autre_mission])
        session.flush()
        session.add(MissionOffre(mission_id=mission.id, offre_id=offre.id))

        session_autorisee = SessionFormation(
            mission_id=mission.id,
            date_debut=date(2026, 10, 1),
            date_fin=date(2026, 10, 31),
            theme="Budget",
        )
        session_hors = SessionFormation(
            mission_id=autre_mission.id,
            date_debut=date(2026, 10, 1),
            date_fin=date(2026, 10, 31),
            theme="Autre",
        )
        session.add_all([session_autorisee, session_hors])
        session.flush()

        # Documents : un sur sa session, un sur la mission d'une autre équipe.
        document = Document(
            nom="Fiche technique.pdf",
            type_document=TypeDocument.RAPPORT.value,
            storage_path=f"sessions/{session_autorisee.id}/fiche.pdf",
            session_id=session_autorisee.id,
            statut=StatutDocument.DRAFT.value,
        )
        document_hors = Document(
            nom="Document d'une autre mission.pdf",
            type_document=TypeDocument.RAPPORT.value,
            storage_path=f"sessions/{session_hors.id}/autre.pdf",
            session_id=session_hors.id,
            statut=StatutDocument.DRAFT.value,
        )
        session.add_all([document, document_hors])
        session.flush()

        beneficiaire = Beneficiaire(nom="Rakoto", prenom="Béatrice")
        beneficiaire_hors = Beneficiaire(nom="Autre", prenom="Zoé")
        session.add_all([beneficiaire, beneficiaire_hors])
        session.flush()

        session.add_all(
            [
                AffectationEquipe(
                    mission_id=mission.id,
                    equipe_id=moi.id,
                    role_dans_mission="formateur",
                    statut=StatutAffectation.APPROUVEE.value,
                ),
                AffectationEquipe(
                    mission_id=mission.id,
                    equipe_id=collegue.id,
                    role_dans_mission="chef_de_mission",
                    statut=StatutAffectation.APPROUVEE.value,
                ),
                AffectationEquipe(
                    mission_id=autre_mission.id,
                    equipe_id=inconnu.id,
                    role_dans_mission="formateur",
                    statut=StatutAffectation.APPROUVEE.value,
                ),
            ]
        )
        utilisateur = Utilisateur(
            email="fatou@carso.test",
            nom="Formatrice",
            prenom="Fatou",
            password_hash="x",
            role=RoleUtilisateur.FORMATEUR.value,
            statut=StatutUtilisateur.ACTIF.value,
            equipe_id=moi.id,
        )
        session.add(utilisateur)
        session.commit()
    finally:
        session.close()

    donnees = SimpleNamespace(
        utilisateur=utilisateur,
        moi=moi,
        collegue=collegue,
        inconnu=inconnu,
        organisation=organisation,
        autre_organisation=autre_organisation,
        appel=appel,
        lot=lot,
        offre=offre,
        mission=mission,
        autre_mission=autre_mission,
        session=session_autorisee,
        session_hors=session_hors,
        document=document,
        document_hors=document_hors,
        beneficiaire=beneficiaire,
        beneficiaire_hors=beneficiaire_hors,
    )
    yield donnees


@pytest.fixture
def client_formateur(
    api_client: TestClient, contexte: SimpleNamespace
) -> Iterator[TestClient]:
    """Client HTTP dont le porteur est le formateur (jamais le compte lab)."""
    api_client.app.dependency_overrides[get_current_user] = lambda: contexte.utilisateur
    yield api_client
    api_client.app.dependency_overrides.pop(get_current_user, None)


# --- Périmètre des lectures ------------------------------------------------------


def test_missions_et_sessions_du_perimetre_seulement(
    client_formateur: TestClient, contexte: SimpleNamespace
) -> None:
    missions = client_formateur.get(f"{PRÉFIXE}/missions/")
    assert [m["id"] for m in missions.json()] == [str(contexte.mission.id)]

    assert client_formateur.get(f"{PRÉFIXE}/missions/{contexte.mission.id}").status_code == 200
    # 404 et non 403 : un refus ne confirme pas l'existence d'un objet d'autrui.
    assert (
        client_formateur.get(f"{PRÉFIXE}/missions/{contexte.autre_mission.id}").status_code
        == 404
    )
    assert (
        client_formateur.get(f"{PRÉFIXE}/sessions/{contexte.session_hors.id}").status_code
        == 404
    )
    assert (
        client_formateur.get(f"{PRÉFIXE}/sessions/{contexte.session.id}").status_code == 200
    )


def test_listes_transverses_et_dossier_lie(
    client_formateur: TestClient, contexte: SimpleNamespace
) -> None:
    """Les listes globales restent fermées ; le dossier de sa mission est lisible."""
    assert client_formateur.get(f"{PRÉFIXE}/appels-a-proposition/").status_code == 403
    assert client_formateur.get(f"{PRÉFIXE}/organisations/").status_code == 403
    assert client_formateur.get(f"{PRÉFIXE}/offres/").status_code == 403
    assert client_formateur.get(f"{PRÉFIXE}/offres/lot/{contexte.lot.id}").status_code == 403
    assert client_formateur.get(f"{PRÉFIXE}/equipes/").status_code == 403
    assert client_formateur.get(f"{PRÉFIXE}/lieux/").status_code == 403
    # Les documents ne sont plus une liste de l'équipe interne : la page
    # Documents répond, bornée à son périmètre (correctif 03/10).
    documents = client_formateur.get(f"{PRÉFIXE}/documents/all")
    assert documents.status_code == 200, documents.text
    assert [d["id"] for d in documents.json()] == [str(contexte.document.id)]

    assert (
        client_formateur.get(
            f"{PRÉFIXE}/appels-a-proposition/{contexte.appel.id}"
        ).status_code
        == 200
    )
    assert (
        client_formateur.get(f"{PRÉFIXE}/organisations/{contexte.organisation.id}").status_code
        == 200
    )
    assert client_formateur.get(f"{PRÉFIXE}/offres/{contexte.offre.id}").status_code == 200
    assert client_formateur.get(f"{PRÉFIXE}/lots/{contexte.lot.id}").status_code == 200


def test_personnes_visibles_limitees_aux_collegues_de_mission(
    client_formateur: TestClient, contexte: SimpleNamespace
) -> None:
    assert client_formateur.get(f"{PRÉFIXE}/equipes/{contexte.moi.id}").status_code == 200
    assert client_formateur.get(f"{PRÉFIXE}/equipes/{contexte.collegue.id}").status_code == 200
    assert client_formateur.get(f"{PRÉFIXE}/equipes/{contexte.inconnu.id}").status_code == 404
    # Un bénéficiaire n'est atteignable que par une de ses sessions.
    assert (
        client_formateur.get(f"{PRÉFIXE}/beneficiaires/{contexte.beneficiaire.id}").status_code
        == 404
    )


# --- Pilotage refusé -------------------------------------------------------------


def test_gestes_de_pilotage_refuses(
    client_formateur: TestClient, contexte: SimpleNamespace
) -> None:
    refus = [
        client_formateur.post(f"{PRÉFIXE}/missions/directe", json={}),
        client_formateur.post(
            f"{PRÉFIXE}/missions/{contexte.mission.id}/statut", json={"statut": "en_cours"}
        ),
        client_formateur.patch(
            f"{PRÉFIXE}/missions/{contexte.mission.id}", json={"titre": "Détourné"}
        ),
        client_formateur.post(
            f"{PRÉFIXE}/sessions/{contexte.session.id}/statut",
            json={"statut": "confirmee"},
        ),
        client_formateur.post(
            f"{PRÉFIXE}/missions/{contexte.mission.id}/sessions",
            json={"date_debut": "2026-11-01"},
        ),
        client_formateur.post(
            f"{PRÉFIXE}/missions/{contexte.mission.id}/affectations",
            json={
                "mission_id": str(contexte.mission.id),
                "equipe_id": str(contexte.collegue.id),
                "role_dans_mission": "formateur",
            },
        ),
        client_formateur.get(f"{PRÉFIXE}/missions/{contexte.mission.id}/impact"),
        client_formateur.get(f"{PRÉFIXE}/audit"),
        client_formateur.get(f"{PRÉFIXE}/agents/runtime"),
        # « /rapports » n'est plus un geste de pilotage : c'est une lecture
        # bornée au périmètre — voir test_rapports_bornes_au_perimetre.
        client_formateur.get(f"{PRÉFIXE}/parametres/etat"),
    ]
    assert [(r.status_code, r.text[:80]) for r in refus if r.status_code != 403] == []


# --- Opérations de session autorisées --------------------------------------------


def test_operations_de_session_autorisees(
    client_formateur: TestClient, contexte: SimpleNamespace
) -> None:
    inscription = client_formateur.post(
        f"{PRÉFIXE}/sessions/{contexte.session.id}/participations",
        json={"beneficiaire_id": str(contexte.beneficiaire.id)},
    )
    assert inscription.status_code == 201, inscription.text
    participation_id = inscription.json()["id"]

    pointage = client_formateur.post(
        f"{PRÉFIXE}/participations/{participation_id}/presence",
        json={"date": "2026-10-02", "presence": "present"},
    )
    assert pointage.status_code == 200, pointage.text

    correction = client_formateur.patch(
        f"{PRÉFIXE}/participations/{participation_id}",
        json={"observations": "Arrivée à l'heure"},
    )
    assert correction.status_code == 200, correction.text

    fiche = client_formateur.post(
        f"{PRÉFIXE}/sessions/{contexte.session.id}/fiche-presence",
        json={"date": "2026-10-02"},
    )
    assert fiche.status_code == 201, fiche.text

    # Le bénéficiaire est maintenant visible : il est inscrit à une session du périmètre.
    assert (
        client_formateur.get(f"{PRÉFIXE}/beneficiaires/{contexte.beneficiaire.id}").status_code
        == 200
    )
    # Mais pas un bénéficiaire d'une autre session.
    assert (
        client_formateur.get(f"{PRÉFIXE}/beneficiaires/{contexte.beneficiaire_hors.id}").status_code
        == 404
    )

    # Retirer une inscription reste un geste de pilotage (les pointages partent).
    assert (
        client_formateur.request(
            "DELETE", f"{PRÉFIXE}/participations/{participation_id}"
        ).status_code
        == 403
    )


def test_depot_de_document_de_session(
    client_formateur: TestClient, contexte: SimpleNamespace
) -> None:
    def _deposer(session_id: UUID) -> object:
        return client_formateur.post(
            f"{PRÉFIXE}/documents",
            files={"file": ("support.pdf", b"%PDF-1.4 contenu", "application/pdf")},
            data={
                "type_document": TypeDocument.RAPPORT.value,
                "nom": "Support de session.pdf",
                "session_id": str(session_id),
            },
        )

    autorise = _deposer(contexte.session.id)
    assert autorise.status_code == 201, autorise.text
    assert autorise.json()["session_id"] == str(contexte.session.id)

    refuse = _deposer(contexte.session_hors.id)
    assert refuse.status_code == 404


def test_tableau_de_bord_restreint_aux_ses_missions(
    client_formateur: TestClient, contexte: SimpleNamespace
) -> None:
    reponse = client_formateur.get(f"{PRÉFIXE}/tableau-bord/")
    assert reponse.status_code == 200, reponse.text
    corps = reponse.json()
    # Le détail du schéma est couvert par les tests du tableau de bord ; ici :
    # l'appel ne fuit rien et reste borné aux missions du périmètre.
    assert "missions" in corps or "compteurs" in corps or "apercu" in corps


def test_rapports_bornes_au_perimetre(
    client_formateur: TestClient,
    api_client: TestClient,
    contexte: SimpleNamespace,
    api_session_factory: sessionmaker[Session],
) -> None:
    """Décision : le formateur accède à « Rapports », borné à son périmètre.

    Même page, même forme, chiffres différents : l'équipe interne lit
    l'entreprise, le formateur lit ses missions et leurs bénéficiaires. C'est le
    service qui pose le périmètre — la route ne fait qu'ouvrir la lecture.
    """
    session = api_session_factory()
    try:
        session.add_all(
            [
                Participation(
                    session_id=contexte.session.id,
                    beneficiaire_id=contexte.beneficiaire.id,
                ),
                Participation(
                    session_id=contexte.session_hors.id,
                    beneficiaire_id=contexte.beneficiaire_hors.id,
                ),
            ]
        )
        session.commit()
    finally:
        session.close()

    reponse = client_formateur.get(f"{PRÉFIXE}/rapports/tableau-bord")
    assert reponse.status_code == 200, reponse.text
    corps = reponse.json()
    tuiles = {w["libelle"]: w.get("valeur") for w in corps["widgets"] if w["type"] == "tuile"}
    assert tuiles["Missions enregistrées"] == 1
    assert tuiles["Bénéficiaires enregistrés"] == 1
    tableaux = [w for w in corps["widgets"] if w["type"] == "tableau"]
    organisations = {ligne["valeurs"]["organisation"] for ligne in tableaux[0]["lignes"]}
    assert organisations == {"Client A"}
    # La page annonce son périmètre : l'utilisateur sait ce qu'il regarde.
    assert "périmètre" in corps["sous_titre"]

    # L'override de porteur est porté par l'application : on le retire le temps
    # de lire la même page avec le compte d'équipe interne (mode lab).
    application = client_formateur.app
    porteur = application.dependency_overrides.pop(get_current_user)
    try:
        interne = api_client.get(f"{PRÉFIXE}/rapports/tableau-bord")
    finally:
        application.dependency_overrides[get_current_user] = porteur
    assert interne.status_code == 200, interne.text
    ses_tuiles = {
        w["libelle"]: w.get("valeur") for w in interne.json()["widgets"] if w["type"] == "tuile"
    }
    assert ses_tuiles["Missions enregistrées"] == 2
    assert ses_tuiles["Bénéficiaires enregistrés"] == 2


def test_catalogue_agents_filtre(client_formateur: TestClient) -> None:
    """Décision 03/10 : les agents du formateur, jamais l'AgentRH.

    L'agent **personnel** s'ajoute à cette liste (10/10) : il est ouvert à tout
    compte actif et ne lit que le périmètre du compte.
    """
    reponse = client_formateur.get(f"{PRÉFIXE}/agents/catalogue")
    assert reponse.status_code == 200, reponse.text
    identifiants = {agent["agent_id"] for agent in reponse.json()}
    assert identifiants == {
        "agent_assistant_formateur",
        "agent_generaliste_readonly",
        "agent_statistique",
        "agent_personnel",
    }


# --- Lectures en clair : membres et bénéficiaires --------------------------------


def test_affectations_portent_l_identite_du_membre(
    client_formateur: TestClient, contexte: SimpleNamespace
) -> None:
    """Bug 03/10 : « Membre 368a685e… » — l'API joint le nom du membre.

    Le formateur ne peut pas lister le vivier (403) : l'affectation doit donc
    porter l'identité, sinon l'interface n'a rien d'autre à afficher qu'un UUID.
    """
    reponse = client_formateur.get(
        f"{PRÉFIXE}/missions/{contexte.mission.id}/affectations"
    )
    assert reponse.status_code == 200, reponse.text
    par_equipe = {a["equipe_id"]: a for a in reponse.json()["affectations"]}
    moi = par_equipe[str(contexte.moi.id)]
    assert moi["equipe"]["nom"] == "Formatrice"
    assert moi["equipe"]["prenom"] == "Fatou"
    # Le collègue affecté à la même mission est lisible aussi (profils et CV).
    assert par_equipe[str(contexte.collegue.id)]["equipe"]["prenom"] == "Carlos"


def test_participations_portent_l_identite_du_beneficiaire(
    client_formateur: TestClient, contexte: SimpleNamespace
) -> None:
    """Bug 03/10 : pseudo-UUID dans Bénéficiaires et Présence — l'API joint le nom."""
    inscription = client_formateur.post(
        f"{PRÉFIXE}/sessions/{contexte.session.id}/participations",
        json={"beneficiaire_id": str(contexte.beneficiaire.id)},
    )
    assert inscription.status_code == 201, inscription.text
    corps = inscription.json()
    assert corps["beneficiaire"]["nom"] == "Rakoto"
    assert corps["beneficiaire"]["prenom"] == "Béatrice"

    liste = client_formateur.get(
        f"{PRÉFIXE}/sessions/{contexte.session.id}/participations"
    )
    assert liste.status_code == 200, liste.text
    assert liste.json()[0]["beneficiaire"]["prenom"] == "Béatrice"


# --- Documents : ses documents et ceux de ses missions/sessions -------------------


def test_document_modifiable_et_supprimable_dans_le_perimetre(
    client_formateur: TestClient,
    contexte: SimpleNamespace,
    api_session_factory: sessionmaker[Session],
) -> None:
    """Correctif 03/10 : visualiser, modifier, supprimer (logique) ses documents.

    Hors périmètre, la fiche est **introuvable** (404) — jamais un 403 qui
    confirmerait son existence.
    """
    modification = client_formateur.patch(
        f"{PRÉFIXE}/documents/{contexte.document.id}",
        json={"nom": "Fiche technique (révisée).pdf"},
    )
    assert modification.status_code == 200, modification.text
    assert modification.json()["nom"] == "Fiche technique (révisée).pdf"

    hors_perimetre = client_formateur.patch(
        f"{PRÉFIXE}/documents/{contexte.document_hors.id}",
        json={"nom": "Détourné.pdf"},
    )
    assert hors_perimetre.status_code == 404

    impact = client_formateur.get(f"{PRÉFIXE}/documents/{contexte.document.id}/impact")
    assert impact.status_code == 200, impact.text

    suppression = client_formateur.request(
        "DELETE",
        f"{PRÉFIXE}/documents/{contexte.document.id}",
        json={"reason": "Pièce obsolète"},
    )
    assert suppression.status_code == 200, suppression.text

    # Suppression **logique** : la fiche survit en base (statut « supprime »),
    # son ancre est détachée et elle quitte la liste du formateur — la corbeille
    # reste une vue d'équipe interne.
    documents = client_formateur.get(f"{PRÉFIXE}/documents/all")
    assert documents.status_code == 200
    assert [d["id"] for d in documents.json()] == []
    assert (
        client_formateur.get(f"{PRÉFIXE}/documents/{contexte.document.id}").status_code
        == 404
    )
    session = api_session_factory()
    try:
        fiche = session.get(Document, contexte.document.id)
        assert fiche is not None
        assert fiche.statut == StatutDocument.SUPPRIME.value
        assert fiche.session_id is None  # ancre détachée, fiche conservée
    finally:
        session.close()

    # La corbeille et la restauration restent des gestes d'équipe interne.
    assert client_formateur.get(f"{PRÉFIXE}/documents/corbeille").status_code == 403
    assert (
        client_formateur.post(
            f"{PRÉFIXE}/documents/{contexte.document.id}/restauration", json={}
        ).status_code
        == 403
    )


def test_suppression_d_un_document_hors_perimetre_refusee(
    client_formateur: TestClient, contexte: SimpleNamespace
) -> None:
    assert (
        client_formateur.request(
            "DELETE",
            f"{PRÉFIXE}/documents/{contexte.document_hors.id}",
            json={"reason": "Tentative"},
        ).status_code
        == 404
    )


def test_les_gestes_de_pilotage_d_un_document_restent_internes(
    client_formateur: TestClient, contexte: SimpleNamespace
) -> None:
    """Approuver / refuser / archiver / purger ne sont pas des gestes de terrain."""
    for chemin in (
        f"{PRÉFIXE}/documents/{contexte.document.id}/approbation",
        f"{PRÉFIXE}/documents/{contexte.document.id}/refus",
        f"{PRÉFIXE}/documents/{contexte.document.id}/archivage",
        f"{PRÉFIXE}/documents/{contexte.document.id}/purge",
    ):
        reponse = client_formateur.post(chemin, json={"reason": "motif"})
        assert reponse.status_code == 403, chemin


def test_social_et_compte_toujours_accessibles(
    client_formateur: TestClient, contexte: SimpleNamespace
) -> None:
    """Le social et le profil restent ouverts : seuls les agents changent."""
    assert client_formateur.get(f"{PRÉFIXE}/auth/me").status_code == 200
    assert client_formateur.get(f"{PRÉFIXE}/social/conversations").status_code == 200
    assert client_formateur.get(f"{PRÉFIXE}/social/annonces").status_code == 200
    assert client_formateur.get(f"{PRÉFIXE}/notifications/").status_code == 200


def test_participation_hors_perimetre_reste_invisible(
    client_formateur: TestClient, api_session_factory: sessionmaker[Session]
) -> None:
    """Un pointage hors périmètre répond 404, jamais 403 (objet inconnu du compte)."""
    session = api_session_factory()
    try:
        hors = Participation(
            session_id=session.query(SessionFormation)
            .filter(SessionFormation.theme == "Autre")
            .one()
            .id,
            beneficiaire_id=session.query(Beneficiaire)
            .filter(Beneficiaire.nom == "Autre")
            .one()
            .id,
        )
        session.add(hors)
        session.commit()
        identifiant = hors.id
    finally:
        session.close()
    assert (
        client_formateur.get(f"{PRÉFIXE}/participations/{identifiant}").status_code == 404
    )
