"""Tests — correction ciblée d'un ``DocumentSpec`` (patch, jamais reconstruction).

Deux niveaux vérifiés :

1. ``app.documents.repair`` : chaque cible corrige **ce qu'elle annonce** et rien
   d'autre, le spec d'origine n'est jamais modifié, et une cible impossible est
   refusée avec un message qui donne les bornes réelles ;
2. le tool ``corriger_document`` : dépôt en proposition, ou **nouvelle version**
   d'un document déjà déposé (l'ancienne version reste sur disque).
"""

from __future__ import annotations

import io
import re
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.application.services import DocumentService
from app.core.errors import ValidationError
from app.documents.repair import appliquer_patch
from app.documents.spec import (
    Cellule,
    DocumentSpec,
    Metadonnees,
    Paragraphe,
    Tableau,
    Titre,
)
from app.documents.storage import LocalDocumentStorage
from app.domain.base import Base
from app.domain.organization import Organisation
from app.tools.documents import build_document_tools
from app.tools.permissions import AgentIdentity, PermissionPolicy

IDENTITE = AgentIdentity(agent_id="agent_generateur")
POLICY = PermissionPolicy(
    allowed_capabilities=frozenset({"document_read", "document_propose"})
)


def _spec() -> DocumentSpec:
    """Document de travail : un titre, un tableau de budget, un paragraphe."""
    return DocumentSpec(
        metadata=Metadonnees(titre="Offre technique — Lot 3", reference="OT-2026-014"),
        blocs=[
            Titre(niveau=1, texte="1. Budget"),
            Tableau(
                titre_tableau="Budget prévisionnel",
                entete=["Poste", "Quantité", "Montant"],
                lignes=[
                    ["Formation", "10", "4 000 000"],
                    ["Logistique", "4", "1 200 000"],
                    ["TOTAL", "", "5 000 000"],
                ],
                largeurs_mm=[60, 30, 40],
                total_ligne=2,
            ),
            Paragraphe(texte="Le budget couvre la totalité du lot."),
        ],
    )


def _tableau(spec: DocumentSpec, index: int = 1) -> Tableau:
    """Bloc tableau du spec (les données se lisent par ``lignes_normalisees``)."""
    bloc = spec.blocs[index]
    assert isinstance(bloc, Tableau)
    return bloc


def _cellule(tableau: Tableau, ligne: int, colonne: int) -> Cellule:
    """Cellule d'une ligne **de données** (les chaînes brutes sont normalisées)."""
    return tableau.lignes_normalisees()[ligne][colonne]


class TestCibles:
    """Chaque opération corrige sa cible, sans toucher au reste."""

    def test_cellule_texte(self) -> None:
        nouveau, journal = appliquer_patch(
            _spec(),
            [{"type": "cellule", "bloc": 1, "ligne": 3, "colonne": 2, "cellule": "5 400 000"}],
        )

        # Ligne 3 = la ligne TOTAL (0 = en-tête, puis les lignes de données).
        assert _cellule(_tableau(nouveau), 2, 2).texte == "5 400 000"
        assert journal == ["bloc 1 : cellule (ligne 3, colonne 2) → '5 400 000'"]

    def test_cellule_presentation_seulement(self) -> None:
        """Un objet cellule ne change que les champs fournis."""
        nouveau, _ = appliquer_patch(
            _spec(),
            [
                {
                    "type": "cellule",
                    "bloc": 1,
                    "ligne": 3,
                    "colonne": 0,
                    "cellule": {"fond": "#FCE4D6", "gras": True},
                }
            ],
        )

        cellule = _cellule(_tableau(nouveau), 2, 0)
        assert cellule.texte == "TOTAL"
        assert cellule.fond == "#FCE4D6"
        assert cellule.gras is True

    def test_cellule_dans_entete_simple(self) -> None:
        nouveau, _ = appliquer_patch(
            _spec(),
            [{"type": "cellule", "bloc": 1, "ligne": 0, "colonne": 2, "cellule": "Montant HT"}],
        )

        assert _tableau(nouveau).entete == ["Poste", "Quantité", "Montant HT"]
        assert _tableau(nouveau).lignes_entete()[0][2].texte == "Montant HT"

    def test_cellule_entete_avec_trame_convertit_en_entetes(self) -> None:
        """Une trame est réellement appliquée : l'en-tête simple devient 'entetes'."""
        nouveau, journal = appliquer_patch(
            _spec(),
            [
                {
                    "type": "cellule",
                    "bloc": 1,
                    "ligne": 0,
                    "colonne": 0,
                    "cellule": {"fond": "#E2EFDA"},
                }
            ],
        )

        tableau = _tableau(nouveau)
        assert tableau.entete is None
        assert tableau.lignes_entete()[0][0].fond == "#E2EFDA"
        assert "converti" in journal[0]

    def test_ligne_de_section_fusionnee_en_une_operation(self) -> None:
        """Une modification structurelle s'exprime en une opération : l'état intermédiaire
        d'une fusion ne doit jamais être un tableau bancal (chaque opération est validée)."""
        nouveau, journal = appliquer_patch(
            _spec(),
            [
                {
                    "type": "tableau",
                    "bloc": 1,
                    "champs": {
                        "lignes": [
                            ["Formation", "10", "4 000 000"],
                            ["Logistique", "4", "1 200 000"],
                            [
                                "TOTAL",
                                {"texte": "5 400 000", "fusion_colonnes": "fin", "gras": True},
                            ],
                        ]
                    },
                }
            ],
        )

        assert len(journal) == 1
        tableau = _tableau(nouveau)
        assert _cellule(tableau, 2, 1).fusion_colonnes == "fin"
        assert tableau.grille()[3][1].colonnes == 2
        assert tableau.grille()[3][0].colonnes == 1

    def test_tableau_largeurs(self) -> None:
        nouveau, journal = appliquer_patch(
            _spec(),
            [{"type": "tableau", "bloc": 1, "champs": {"largeurs_mm": [50, 25, 45]}}],
        )

        assert _tableau(nouveau).largeurs_mm == [50, 25, 45]
        assert journal == ["bloc 1 (tableau) : largeurs_mm modifié(s)"]

    def test_tableau_etat_intermediaire_bancal_refuse(self) -> None:
        """Un pas intermédiaire qui laisse une colonne non couverte est refusé, pas deviné."""
        with pytest.raises(ValidationError, match="non couverte"):
            appliquer_patch(
                _spec(),
                [
                    {
                        "type": "tableau",
                        "bloc": 1,
                        "champs": {
                            "lignes": [
                                ["Formation", "10", "4 000 000"],
                                ["Logistique", "4", "1 200 000"],
                                ["TOTAL", "5 000 000"],
                            ]
                        },
                    }
                ],
            )

    def test_tableau_grille_cassee_refusee(self) -> None:
        """Un tableau dont la grille casse est refusé : la correction n'est pas appliquée."""
        with pytest.raises(ValidationError, match="Correction refusée"):
            appliquer_patch(
                _spec(),
                [
                    {
                        "type": "tableau",
                        "bloc": 1,
                        "champs": {"lignes": [["Formation", "10", "4 000 000"], ["Logistique"]]},
                    }
                ],
            )

    def test_bloc_champs(self) -> None:
        nouveau, _ = appliquer_patch(
            _spec(),
            [{"type": "bloc", "bloc": 2, "champs": {"texte": "Budget révisé après arbitrage."}}],
        )

        bloc = nouveau.blocs[2]
        assert isinstance(bloc, Paragraphe)
        assert bloc.texte == "Budget révisé après arbitrage."

    def test_bloc_remplacement_insertion_suppression(self) -> None:
        remplace, _ = appliquer_patch(
            _spec(),
            [{"type": "bloc", "bloc": 2, "valeur": {"type": "paragraphe", "texte": "Nouveau."}}],
        )
        assert len(remplace.blocs) == 3
        assert isinstance(remplace.blocs[2], Paragraphe)

        insere, _ = appliquer_patch(
            remplace,
            [
                {
                    "type": "bloc",
                    "bloc": 2,
                    "inserer_apres": {"type": "titre", "niveau": 2, "texte": "Détail"},
                }
            ],
        )
        assert len(insere.blocs) == 4
        assert isinstance(insere.blocs[3], Titre)

        supprime, _ = appliquer_patch(insere, [{"type": "bloc", "bloc": 3, "supprimer": True}])
        assert len(supprime.blocs) == 3

    def test_style_token(self) -> None:
        nouveau, journal = appliquer_patch(
            _spec(),
            [{"type": "style", "chemin": "tableau.entete_fond", "valeur": "#0B2B4A"}],
        )

        assert nouveau.style_effectif().tableau.entete_fond == "#0B2B4A"
        assert "tableau.entete_fond" in journal[0]

    def test_style_taille_de_titre_par_niveau(self) -> None:
        nouveau, _ = appliquer_patch(
            _spec(), [{"type": "style", "chemin": "titres.1.taille_pt", "valeur": 20.0}]
        )

        assert nouveau.style_effectif().titres[1].taille_pt == 20.0
        assert nouveau.style_effectif().titres[2].taille_pt != 20.0

    def test_metadonnees(self) -> None:
        nouveau, _ = appliquer_patch(
            _spec(),
            [{"type": "document", "champs": {"reference": "OT-2026-015", "destinataire": "CARSO"}}],
        )

        assert nouveau.metadata.reference == "OT-2026-015"
        assert nouveau.metadata.destinataire == "CARSO"
        assert nouveau.metadata.titre == "Offre technique — Lot 3"

    def test_plusieurs_operations_dans_l_ordre(self) -> None:
        nouveau, journal = appliquer_patch(
            _spec(),
            [
                {"type": "style", "chemin": "corps.taille_pt", "valeur": 11.0},
                {"type": "cellule", "bloc": 1, "ligne": 1, "colonne": 2, "cellule": "4 200 000"},
                {"type": "document", "champs": {"titre": "Offre technique — Lot 3 (révisée)"}},
            ],
        )

        assert len(journal) == 3
        assert nouveau.metadata.titre.endswith("(révisée)")
        assert _cellule(_tableau(nouveau), 0, 2).texte == "4 200 000"


class TestRefus:
    """Aucune perte silencieuse : une cible impossible est refusée, jamais devinée."""

    def test_spec_original_inchange(self) -> None:
        spec = _spec()
        appliquer_patch(
            spec, [{"type": "cellule", "bloc": 1, "ligne": 1, "colonne": 0, "cellule": "X"}]
        )

        assert _cellule(_tableau(spec), 0, 0).texte == "Formation"

    def test_bloc_hors_bornes(self) -> None:
        with pytest.raises(ValidationError, match="Index hors bornes"):
            appliquer_patch(_spec(), [{"type": "bloc", "bloc": 9, "champs": {"texte": "x"}}])

    def test_ligne_hors_bornes(self) -> None:
        with pytest.raises(ValidationError, match="Index hors bornes"):
            appliquer_patch(
                _spec(),
                [{"type": "cellule", "bloc": 1, "ligne": 12, "colonne": 0, "cellule": "x"}],
            )

    def test_colonne_hors_bornes(self) -> None:
        with pytest.raises(ValidationError, match="Index hors bornes"):
            appliquer_patch(
                _spec(),
                [{"type": "cellule", "bloc": 1, "ligne": 1, "colonne": 7, "cellule": "x"}],
            )

    def test_cible_non_tableau(self) -> None:
        with pytest.raises(ValidationError, match="pas 'tableau'"):
            appliquer_patch(
                _spec(),
                [{"type": "cellule", "bloc": 0, "ligne": 0, "colonne": 0, "cellule": "x"}],
            )

    def test_chemin_de_style_inconnu(self) -> None:
        with pytest.raises(ValidationError, match="Chemin de style inconnu"):
            appliquer_patch(_spec(), [{"type": "style", "chemin": "page.couleur", "valeur": "x"}])

    def test_valeur_de_style_refusee(self) -> None:
        with pytest.raises(ValidationError, match="Valeur refusée"):
            appliquer_patch(
                _spec(), [{"type": "style", "chemin": "corps.taille_pt", "valeur": 400}]
            )

    def test_champ_de_bloc_inconnu(self) -> None:
        with pytest.raises(ValidationError, match="Champ inconnu"):
            appliquer_patch(_spec(), [{"type": "bloc", "bloc": 2, "champs": {"police": "Arial"}}])

    def test_metadonnee_inconnue(self) -> None:
        with pytest.raises(ValidationError, match="Champ inconnu"):
            appliquer_patch(_spec(), [{"type": "document", "champs": {"couleur": "bleu"}}])

    def test_intention_de_bloc_ambigue(self) -> None:
        with pytest.raises(ValidationError, match="exactement une intention"):
            appliquer_patch(
                _spec(),
                [{"type": "bloc", "bloc": 2, "supprimer": True, "champs": {"texte": "x"}}],
            )


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as active:
        yield active
    engine.dispose()


@pytest.fixture
def service(session: Session, tmp_path: Path) -> DocumentService:
    return DocumentService(
        session,
        storage=LocalDocumentStorage(tmp_path / "carso", max_bytes=2 * 1024 * 1024),
        actor_id="test",
    )


@pytest.fixture
def organisation(session: Session) -> Organisation:
    organisation = Organisation(nom="Client Correction")
    session.add(organisation)
    session.flush()
    return organisation


def _outils(service: DocumentService) -> dict[str, Any]:
    return {
        outil.name: outil
        for outil in build_document_tools(
            identite=IDENTITE, policy=POLICY, service_factory=lambda: service
        )
    }


def _correction(organisation: Organisation, **overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "document_spec": _spec().model_dump(mode="json"),
        "operations": [
            {"type": "cellule", "bloc": 1, "ligne": 3, "colonne": 2, "cellule": "5 400 000"}
        ],
        "format": "docx",
        "nom_fichier": "offre_lot3",
        "type_document": "offre",
        "ancre": "organisation",
        "entity_id": str(organisation.id),
        "propose_par": IDENTITE.agent_id,
    }
    base.update(overrides)
    return base


def _texte_docx(chemin: Path) -> list[str]:
    """Tous les textes d'un DOCX déposé (octets réels, pas le spec)."""
    xml = (
        zipfile.ZipFile(io.BytesIO(chemin.read_bytes()))
        .read("word/document.xml")
        .decode("utf-8")
    )
    return re.findall(r"<w:t[^>]*>([^<]*)</w:t>", xml)


class TestOutilCorrigerDocument:
    """Le tool : correction ciblée → dépôt proposé, ou nouvelle version."""

    def test_depot_en_proposition(
        self, service: DocumentService, organisation: Organisation
    ) -> None:
        sortie = _outils(service)["corriger_document"].invoke(_correction(organisation))

        assert sortie["ok"] is True
        assert sortie["statut"] == "proposed"
        assert sortie["operations_appliquees"]
        assert sortie["document_spec"]["blocs"][1]["lignes"][2][2]["texte"] == "5 400 000"
        _nom, chemin = service.telecharger(UUID(sortie["document_id"]))
        assert "5 400 000" in _texte_docx(chemin)

    def test_nouvelle_version_conserve_l_ancienne(
        self, service: DocumentService, organisation: Organisation
    ) -> None:
        outils = _outils(service)
        premier = outils["generer_document"].invoke(
            {
                "document_spec": _spec().model_dump(mode="json"),
                "format": "docx",
                "nom_fichier": "offre_lot3",
                "type_document": "offre",
                "ancre": "organisation",
                "entity_id": str(organisation.id),
                "propose_par": IDENTITE.agent_id,
            }
        )
        assert premier["ok"] is True

        corrige = outils["corriger_document"].invoke(
            _correction(organisation, document_id=premier["document_id"])
        )

        assert corrige["ok"] is True
        assert corrige["version"] == 2
        assert corrige["document_id"] != premier["document_id"]
        assert "nouvelle version" in corrige["message"]
        _nom1, chemin1 = service.telecharger(UUID(premier["document_id"]))
        _nom2, chemin2 = service.telecharger(UUID(corrige["document_id"]))
        assert chemin1.exists() and chemin2.exists()
        assert chemin1 != chemin2
        assert "5 400 000" in _texte_docx(chemin2)

    def test_version_inconnue(self, service: DocumentService, organisation: Organisation) -> None:
        sortie = _outils(service)["corriger_document"].invoke(
            _correction(organisation, document_id="00000000-0000-0000-0000-000000000001")
        )

        assert sortie["ok"] is False
        assert sortie["error"]["code"] in ("not_found", "business_validation_error")

    def test_sans_nom_fichier_refuse(
        self, service: DocumentService, organisation: Organisation
    ) -> None:
        sortie = _outils(service)["corriger_document"].invoke(
            _correction(organisation, nom_fichier=None, type_document=None)
        )

        assert sortie["ok"] is False
        assert "nom_fichier" in sortie["error"]["message"]

    def test_operation_invalide_est_expliquee(
        self, service: DocumentService, organisation: Organisation
    ) -> None:
        sortie = _outils(service)["corriger_document"].invoke(
            _correction(organisation, operations=[{"type": "cellule", "bloc": 1}])
        )

        assert sortie["ok"] is False
        assert sortie["error"]["code"] == "business_validation_error"

    def test_validation_signalee_apres_correction(
        self, service: DocumentService, organisation: Organisation
    ) -> None:
        """Le document corrigé est revalidé : les constats reviennent dans le retour."""
        sortie = _outils(service)["corriger_document"].invoke(
            _correction(
                organisation,
                operations=[
                    {"type": "bloc", "bloc": 2, "champs": {"texte": "(à compléter)"}},
                ],
            )
        )

        assert sortie["ok"] is True
        assert sortie["validation"]
        assert isinstance(sortie["warnings"], list)
