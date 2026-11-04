"""Rédaction progressive d'un document professionnel (INC-105).

Un document long exigeait du modèle qu'il « écrive » tout le corps HTML dans
les arguments d'un SEUL tool call — une génération géante qui fait taire le
fournisseur en cours de route : plus aucun chunk, le run meurt, et l'outil
n'est jamais exécuté (aucun document déposé). Le mécanisme testé ici :

```text
demarrer (brouillon) → section ×N (une petite génération par appel)
                     → finaliser (assemblage, rendu, dépôt, brouillon supprimé)
```

Tout passe par les vraies couches (service + stockage local temporaire +
SQLite) : aucun mock du rendu ni du dépôt.
"""

from __future__ import annotations

import os
import time
import uuid as uuid_module
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.application.services import DocumentService
from app.documents.storage import LocalDocumentStorage
from app.domain.base import Base
from app.domain.organization import Organisation
from app.tools.documents import (
    _BORNE_SECTION_CARACTERES,
    build_document_tools,
)
from app.tools.permissions import AgentIdentity, PermissionPolicy

IDENTITE = AgentIdentity(agent_id="agent_generateur_offre")
POLICY = PermissionPolicy(
    allowed_capabilities=frozenset({"document_read", "document_propose"})
)


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as active:
        yield active
    engine.dispose()


@pytest.fixture
def storage(tmp_path: Path) -> LocalDocumentStorage:
    return LocalDocumentStorage(tmp_path / "carso", max_bytes=4 * 1024 * 1024)


@pytest.fixture
def service(session: Session, storage: LocalDocumentStorage) -> DocumentService:
    return DocumentService(session, storage=storage, actor_id="test")


@pytest.fixture
def organisation(session: Session) -> Organisation:
    entite = Organisation(nom="Client Formation")
    session.add(entite)
    session.flush()
    return entite


@pytest.fixture
def outils(service: DocumentService, brouillons: Path) -> dict[str, Any]:
    construits = build_document_tools(
        identite=IDENTITE,
        policy=POLICY,
        service_factory=lambda: service,
        dossier_brouillons=brouillons,
    )
    return {outil.name: outil for outil in construits}


@pytest.fixture
def brouillons(tmp_path: Path) -> Path:
    return tmp_path / "_brouillons"


def _assert_ok(reponse: dict[str, Any]) -> None:
    assert reponse.get("ok") is True, reponse


def _demarrer(outils: dict[str, Any], **surcharge: Any) -> dict[str, Any]:
    demande: dict[str, Any] = {
        "action": "demarrer",
        "titre": "Offre technique — lot 3 TEFIALA",
        "format": "docx",
        "type_document": "offre",
        "auteur": "CARSO",
        "propose_par": IDENTITE.agent_id,
    }
    demande.update(surcharge)
    return outils["generer_document_professionnel"].invoke(demande)


def _section(
    outils: dict[str, Any], brouillon_id: str, html: str, **surcharge: Any
) -> dict[str, Any]:
    demande: dict[str, Any] = {
        "action": "section",
        "brouillon_id": brouillon_id,
        "contenu_html": html,
        "propose_par": IDENTITE.agent_id,
    }
    demande.update(surcharge)
    return outils["generer_document_professionnel"].invoke(demande)


def _finaliser(
    outils: dict[str, Any], brouillon_id: str, **surcharge: Any
) -> dict[str, Any]:
    demande: dict[str, Any] = {
        "action": "finaliser",
        "brouillon_id": brouillon_id,
        "propose_par": IDENTITE.agent_id,
    }
    demande.update(surcharge)
    return outils["generer_document_professionnel"].invoke(demande)


def _sections_exemple() -> list[str]:
    return (
        "<section class=\"cover\"><h1 class=\"cover-title\">Offre technique</h1></section>",
        "<h1>1. Contexte</h1><p>L'USA097 demande une formation en incubation.</p>",
        "<h1>2. Démarche</h1><table class=\"data\"><thead><tr><th>Phase</th>"
        "<th class=\"num\">Durée</th></tr></thead><tbody><tr><td>Préparation</td>"
        "<td class=\"num\">2 semaines</td></tr></tbody></table>",
    )


def test_document_long_par_sections_aboutit(
    outils: dict[str, Any], organisation: Organisation, brouillons: Path
) -> None:
    ouverture = _demarrer(outils, ancre="organisation", entity_id=str(organisation.id))
    assert ouverture["ok"] is True
    assert ouverture["sections"] == 0
    identifiant = ouverture["brouillon_id"]

    for morceau in _sections_exemple():
        _assert_ok(_section(outils, identifiant, morceau))

    finalisation = _finaliser(outils, identifiant)
    assert finalisation["ok"] is True
    assert finalisation["statut"] == "proposed"
    assert finalisation["sections"] == 3
    assert finalisation["documents"]["format"] == "docx"
    assert finalisation["documents"]["principal"]

    # Le brouillon est supprimé après une finalisation réussie.
    assert not list((brouillons / IDENTITE.agent_id).glob("*.json"))


def test_remplacement_d_une_section_existante(outils: dict[str, Any]) -> None:
    ouverture = _demarrer(outils)
    identifiant = ouverture["brouillon_id"]
    _section(outils, identifiant, "<h1>1. Contexte</h1><p>Brouillon à corriger.</p>")
    _section(outils, identifiant, "<h1>2. Démarche</h1><p>Méthodologie.</p>")

    remplacement = _section(
        outils,
        identifiant,
        "<h1>1. Contexte</h1><p>Version corrigée du contexte.</p>",
        section_index=1,
    )
    _assert_ok(remplacement)
    assert remplacement["sections"] == 2
    assert remplacement["remplacee"] == 1

    finalisation = _finaliser(outils, identifiant)
    assert finalisation["sections"] == 2


def test_section_hors_bornes_refusee(outils: dict[str, Any]) -> None:
    ouverture = _demarrer(outils)
    identifiant = ouverture["brouillon_id"]
    _section(outils, identifiant, "<h1>1. Contexte</h1><p>Seule section.</p>")

    reponse = _section(outils, identifiant, "<h1>3. Suite</h1><p>…</p>", section_index=2)
    assert reponse["ok"] is False
    assert "hors bornes" in reponse["error"]["message"]
    assert "indices 1 à 1" in reponse["error"]["message"]


def test_corps_trop_long_refuse_avant_la_generation(outils: dict[str, Any]) -> None:
    """Un corps géant est refusé AVANT que le modèle ne s'épuise.

    Constaté en recette : 13 331 chunks émis puis 300 s de silence, run mort
    sans document déposé. La borne renvoie l'agent vers la rédaction
    progressive, lisiblement.
    """
    from app.tools.documents import GenererDocumentProfessionnelInput

    with pytest.raises(ValueError) as excinfo:
        GenererDocumentProfessionnelInput(
            action="generer",
            titre="Offre technique",
            format="docx",
            type_document="offre",
            contenu_html="<p>" + "x" * 30_001 + "</p>",
            propose_par=IDENTITE.agent_id,
        )
    message = str(excinfo.value)
    assert "Corps trop long" in message
    assert "action='demarrer'" in message


def test_section_trop_longue_refusee(outils: dict[str, Any]) -> None:
    ouverture = _demarrer(outils)
    reponse = _section(
        outils, ouverture["brouillon_id"], "x" * (_BORNE_SECTION_CARACTERES + 1)
    )
    assert reponse["ok"] is False
    assert "bornée à" in reponse["error"]["message"]


def test_demarrer_avec_contenu_refuse(outils: dict[str, Any]) -> None:
    reponse = _demarrer(outils, contenu_html="<h1>Corps</h1>")
    assert reponse["ok"] is False
    assert "ne prend pas de corps" in reponse["error"]["message"]


def test_generer_exige_contenu(outils: dict[str, Any]) -> None:
    reponse = _demarrer(outils, action="generer")
    assert reponse["ok"] is False
    assert "contenu_html" in reponse["error"]["message"]


def test_brouillon_d_un_autre_agent_refuse(
    session: Session, brouillons: Path
) -> None:
    def construire(agent_id: str) -> dict[str, Any]:
        outils_liste = build_document_tools(
            identite=AgentIdentity(agent_id=agent_id),
            policy=POLICY,
            service_factory=lambda: session,
            dossier_brouillons=brouillons,
        )
        return {outil.name: outil for outil in outils_liste}

    catalogue_a = construire(IDENTITE.agent_id)
    catalogue_b = construire("agent_rh")

    ouverture = _demarrer(catalogue_a)
    reponse = _section(catalogue_b, ouverture["brouillon_id"], "<h1>Tentative</h1>")
    assert reponse["ok"] is False
    # Le chemin est résolu dans le dossier de l'agent appelant : l'agent B
    # voit « inconnu », pas « appartient à un autre agent » — l'existence
    # même d'un brouillon d'autrui n'est jamais révélée.
    assert "inconnu" in reponse["error"]["message"]


def test_finaliser_sans_section_refuse(outils: dict[str, Any]) -> None:
    ouverture = _demarrer(outils)
    reponse = _finaliser(outils, ouverture["brouillon_id"])
    assert reponse["ok"] is False
    assert "Brouillon vide" in reponse["error"]["message"]


def test_brouillon_inconnu_refuse(outils: dict[str, Any]) -> None:
    reponse = _section(outils, str(uuid_module.uuid4()), "<h1>Orphelin</h1>")
    assert reponse["ok"] is False
    assert "inconnu" in reponse["error"]["message"]


def test_purge_des_brouillons_perimes(outils: dict[str, Any], brouillons: Path) -> None:
    premiere = _demarrer(outils)
    dossier = brouillons / IDENTITE.agent_id
    fichiers = list(dossier.glob("*.json"))
    assert len(fichiers) == 1
    ancien = time.time() - 8 * 24 * 3600
    os.utime(fichiers[0], (ancien, ancien))

    seconde = _demarrer(outils)
    assert seconde["brouillon_id"] != premiere["brouillon_id"]
    restants = list(dossier.glob("*.json"))
    assert len(restants) == 1
    assert restants[0].stem == seconde["brouillon_id"]


def test_corriger_document_ne_restitue_plus_le_spec_complet(
    service: DocumentService, organisation: Organisation
) -> None:
    outils_liste = build_document_tools(
        identite=IDENTITE,
        policy=POLICY,
        service_factory=lambda: service,
        dossier_brouillons=None,
    )
    catalogue = {outil.name: outil for outil in outils_liste}
    spec = {
        "metadata": {
            "titre": "Offre technique — lot 2",
            "reference": "CARSO-2026-100",
            "type_document": "offre_technique",
        },
        "profil": "offre_technique",
        "blocs": [
            {"type": "titre", "texte": "Contexte"},
            {"type": "paragraphe", "texte": "Le CARSO demande une formation."},
        ],
    }
    sortie = catalogue["corriger_document"].invoke(
        {
            "document_spec": spec,
            "operations": [
                {"type": "document", "champs": {"reference": "CARSO-2026-101"}}
            ],
            "format": "docx",
            "nom_fichier": "offre_lot2_corrigee",
            "type_document": "offre",
            "ancre": "organisation",
            "entity_id": str(organisation.id),
            "propose_par": IDENTITE.agent_id,
        }
    )
    assert sortie["ok"] is True
    assert sortie["statut"] == "proposed"
    assert sortie["operations_appliquees"]
    # L'écho du spec complet a disparu : le modèle vient de l'écrire, le
    # renvoyer doublerait la charge du tour suivant (INC-105).
    assert "document_spec" not in sortie
    assert "resume_document" in sortie


def test_plan_annonce_progression_partie_par_partie(outils: dict[str, Any]) -> None:
    """Le plan figé à l'ouverture pilote la progression affichée au chat.

    Une boucle apparente (« Generator appelle 6 fois le même outil ») devient
    une avancée lisible : « Partie 2/4 ». Le libellé vient du retour de l'outil
    (``progression``), pas d'une reconstruction côté frontend.
    """
    ouverture = _demarrer(
        outils, plan=["Contexte", "Démarche", "Budget", "Planning"]
    )
    _assert_ok(ouverture)
    assert ouverture["parties"] == 4
    assert ouverture["partie"] == 0
    assert "1/4" in ouverture["message"]

    identifiant = ouverture["brouillon_id"]
    premiere = _section(outils, identifiant, _sections_exemple()[0])
    _assert_ok(premiere)
    assert premiere["progression"] == "Partie 1/4"
    assert premiere["parties"] == 4

    # Le plan est relu depuis le brouillon (pas de mémoire du process) : un
    # second appel repart du même plan et annonce la partie suivante.
    seconde = _section(outils, identifiant, _sections_exemple()[1])
    assert seconde["progression"] == "Partie 2/4"
    assert "Budget" in seconde["message"]


def test_sans_plan_la_progression_reste_absente(outils: dict[str, Any]) -> None:
    """Sans plan, un simple compteur : ne pas inventer de total."""
    ouverture = _demarrer(outils)
    assert ouverture["parties"] is None
    assert ouverture["progression"] is None

    reponse = _section(outils, ouverture["brouillon_id"], _sections_exemple()[1])
    assert reponse["progression"] is None
    assert reponse["sections"] == 1


def test_finaliser_apres_un_plan_complet(outils: dict[str, Any]) -> None:
    """Le plan guide l'écriture puis s'efface : la finalisation reste possible."""
    plan = ["Couverture", "Contexte", "Démarche"]
    ouverture = _demarrer(outils, plan=plan)
    identifiant = ouverture["brouillon_id"]
    for morceau in _sections_exemple():
        _assert_ok(_section(outils, identifiant, morceau))

    finalisation = _finaliser(outils, identifiant)
    _assert_ok(finalisation)
    assert finalisation["sections"] == len(plan)
    assert finalisation["statut"] == "proposed"
