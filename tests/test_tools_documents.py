"""Lot 1 — tools documentaires (``generer_document`` / ``lire_document``).

Vérifie la frontière HITL : un agent génère => le document est en ``proposed``
(règle 6 [C]), jamais ``approved`` ; la lecture reste en lecture seule.
SQLite mémoire + storage local temporaire (mêmes fixtures que
``test_document_service.py``).
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.application.services import DocumentService
from app.documents.storage import LocalDocumentStorage
from app.domain.base import Base
from app.domain.organization import Organisation
from app.tools.documents import build_document_tools
from app.tools.permissions import AgentIdentity, PermissionPolicy

IDENTITE = AgentIdentity(agent_id="agent_generaliste")
POLICY_DOC = PermissionPolicy(
    allowed_capabilities=frozenset({"document_read", "document_propose"})
)
POLICY_LECTURE = PermissionPolicy(allowed_capabilities=frozenset({"document_read"}))


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as active:
        yield active
    engine.dispose()


@pytest.fixture
def storage(tmp_path: Path) -> LocalDocumentStorage:
    return LocalDocumentStorage(tmp_path / "carso", max_bytes=2 * 1024 * 1024)


@pytest.fixture
def service(session: Session, storage: LocalDocumentStorage) -> DocumentService:
    return DocumentService(session, storage=storage, actor_id="test")


@pytest.fixture
def organisation(session: Session) -> Organisation:
    organisation = Organisation(nom="Client Formation")
    session.add(organisation)
    session.flush()
    return organisation


def _tools(
    service: DocumentService,
    policy: PermissionPolicy = POLICY_DOC,
) -> dict[str, Any]:
    outils = build_document_tools(
        identite=IDENTITE,
        policy=policy,
        service_factory=lambda: service,
    )
    return {outil.name: outil for outil in outils}


def _payload(organisation: Organisation, **overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "format": "docx",
        "nom_fichier": "rapport_atelier",
        "type_document": "rapport",
        "ancre": "organisation",
        "entity_id": str(organisation.id),
        "propose_par": "agent_generaliste",
        "titre": "Rapport d'atelier",
        "paragraphes": ["Synthèse des travaux."],
        "tableau": [["Thème", "Effectif"], ["Gestion", "12"]],
    }
    base.update(overrides)
    return base


def test_generation_docx_en_statut_proposed(
    service: DocumentService, organisation: Organisation
) -> None:
    outil = _tools(service)["generer_document"]
    sortie = outil.invoke(_payload(organisation))

    assert sortie["statut"] == "proposed"  # HITL : jamais approved depuis un agent
    assert sortie["nom"] == "rapport_atelier.docx"
    document = service.obtenir(UUID(sortie["document_id"]))
    assert document.statut == "proposed"
    assert document.created_by == "agent_generaliste"


def test_generation_refusee_sans_permission(
    service: DocumentService, organisation: Organisation
) -> None:
    outil = _tools(service, POLICY_LECTURE)["generer_document"]
    sortie = outil.invoke(_payload(organisation))
    assert sortie["ok"] is False
    assert sortie["error"]["code"] == "permission_denied"


def test_generation_xlsx_en_statut_proposed(
    service: DocumentService, organisation: Organisation
) -> None:
    outil = _tools(service)["generer_document"]
    sortie = outil.invoke(
        _payload(
            organisation,
            format="xlsx",
            nom_fichier="liste_beneficiaires",
            type_document="liste_beneficiaires",
            paragraphes=None,
        )
    )
    assert sortie.get("statut") == "proposed" or sortie.get("ok") is True
    assert "liste_beneficiaires.xlsx" in sortie.get("nom", "")


def test_generation_pdf_en_statut_proposed(
    service: DocumentService, organisation: Organisation
) -> None:
    outil = _tools(service)["generer_document"]
    sortie = outil.invoke(_payload(organisation, format="pdf"))
    assert sortie["statut"] == "proposed"
    assert sortie["nom"] == "rapport_atelier.pdf"


def test_format_inconnu_refuse(
    service: DocumentService, organisation: Organisation
) -> None:
    """Le format est un vocabulaire fermé : refusé, avec les valeurs acceptées."""
    outil = _tools(service)["generer_document"]
    sortie = outil.invoke(_payload(organisation, format="exe"))
    assert sortie["ok"] is False
    assert "format" in sortie["error"]["message"]
    assert "docx" in sortie["error"]["message"]
    constat = sortie["error"]["details"]["erreurs"][0]
    assert constat["champ"] == "format"
    assert constat["valeurs_attendues"] == "docx, xlsx, pdf"
    assert constat["valeur_recue"] == "exe"


def test_ancre_inconnue_refusee(
    service: DocumentService, organisation: Organisation
) -> None:
    """Une ancre hors vocabulaire est une valeur inventée : refusée par le schéma."""
    outil = _tools(service)["generer_document"]
    sortie = outil.invoke(_payload(organisation, ancre="inconnue"))
    assert sortie["ok"] is False
    assert "ancre" in sortie["error"]["message"]
    assert "appel_a_proposition" in sortie["error"]["message"]


def test_ancre_inexistante_range_sous_generated(
    service: DocumentService, organisation: Organisation
) -> None:
    """Un UUID d'ancre inventé n'est pas écrit : le document est non classé."""
    outil = _tools(service)["generer_document"]
    sortie = outil.invoke(
        _payload(organisation, entity_id="00000000-0000-0000-0000-000000000001")
    )
    assert sortie["statut"] == "proposed"
    assert sortie["non_classe"] is True
    document = service.obtenir(UUID(sortie["document_id"]))
    assert document.type_document == "non_classe"
    assert document.organisation_id is None
    assert document.storage_path.startswith(f"generated/{document.id}/")


def test_generation_sans_ancre_range_sous_generated(
    service: DocumentService, organisation: Organisation
) -> None:
    outil = _tools(service)["generer_document"]
    sortie = outil.invoke(_payload(organisation, ancre=None, entity_id=None))
    assert sortie["statut"] == "proposed"
    assert sortie["non_classe"] is True
    assert "proposer_telechargement_document" in sortie["message"]


def test_nom_fichier_avec_separateur_refuse(
    service: DocumentService, organisation: Organisation
) -> None:
    """Le service refuse explicitement les séparateurs de chemin : pas de
    sanitisation silencieuse (AGENTS.md §7 — path traversal jamais corrigé)."""
    outil = _tools(service)["generer_document"]
    sortie = outil.invoke(_payload(organisation, nom_fichier="../evil/rapport_atelier"))
    assert sortie["ok"] is False
    msg = sortie["error"]["message"].lower()
    assert "separateur" in msg or "séparateur" in msg


def test_lecture_document(
    service: DocumentService, organisation: Organisation
) -> None:
    # Un vrai DOCX est généré par le tool (le service valide le contenu),
    # puis relu par le tool de lecture.
    generer = _tools(service)["generer_document"]
    depose = generer.invoke(_payload(organisation, nom_fichier="cv_tech"))

    outil = _tools(service)["lire_document"]
    sortie = outil.invoke({"document_id": depose["document_id"]})
    assert sortie["document_id"] == depose["document_id"]
    assert sortie["nom"] == "cv_tech.docx"
    assert sortie["taille_octets"] > 0
    assert "chemin" not in sortie  # jamais de chemin disque (AGENTS.md §7)


def test_lecture_document_inconnu(
    service: DocumentService, organisation: Organisation
) -> None:
    outil = _tools(service)["lire_document"]
    sortie = outil.invoke({"document_id": "00000000-0000-0000-0000-000000000002"})
    assert sortie["ok"] is False
    assert sortie["error"]["code"] == "not_found"


def test_lecture_refusee_sans_permission(
    service: DocumentService, organisation: Organisation
) -> None:
    outil = build_document_tools(
        identite=IDENTITE,
        policy=PermissionPolicy(allowed_capabilities=frozenset({"document_propose"})),
        service_factory=lambda: service,
    )
    lecture = [o for o in outil if o.name == "lire_document"][0]
    sortie = lecture.invoke({"document_id": "00000000-0000-0000-0000-000000000003"})
    assert sortie["ok"] is False
    assert sortie["error"]["code"] == "permission_denied"


def test_document_id_non_uuid_refuse(
    service: DocumentService, organisation: Organisation
) -> None:
    outil = _tools(service)["lire_document"]
    sortie = outil.invoke({"document_id": "pas-un-uuid"})
    assert sortie["ok"] is False
    assert "UUID" in sortie["error"]["message"]


def test_alias_read_document_identique_a_lire_document(
    service: DocumentService, organisation: Organisation
) -> None:
    """Le contrat des agents déclare ``read_document`` : l'alias doit exister."""
    outils = _tools(service)
    assert "read_document" in outils
    generer = outils["generer_document"]
    depose = generer.invoke(_payload(organisation, nom_fichier="alias_lecture"))
    sortie = outils["read_document"].invoke({"document_id": depose["document_id"]})
    assert sortie["document_id"] == depose["document_id"]
    assert "chemin" not in sortie


# --- Lot 2 : schémas typés des outils documentaires ---------------------------


def test_vocabulaires_typees_alignes_sur_le_domaine() -> None:
    """Un vocabulaire typé qui diverge du catalogue est un mensonge au modèle."""
    from typing import get_args

    from app.documents.profiles import PROFILS
    from app.documents.styles import STYLES_DISPONIBLES
    from app.tools.documents import (
        _ANCRES,
        _FORMATS,
        TypeAncre,
        TypeFormat,
        TypeProfil,
        TypeStyle,
    )

    assert set(get_args(TypeFormat)) == set(_FORMATS)
    assert set(get_args(TypeAncre)) == set(_ANCRES)
    assert set(get_args(TypeStyle)) == set(STYLES_DISPONIBLES)
    assert set(get_args(TypeProfil)) == set(PROFILS)


def _valeurs_enum(schema: dict[str, Any], champ: str) -> list[str]:
    """Valeurs d'énumération d'une propriété (directe ou dans un ``anyOf``)."""
    propriete = schema["properties"][champ]
    for variante in propriete.get("anyOf", [propriete]):
        candidat = variante.get("items", variante)
        if "enum" in candidat:
            return [str(valeur) for valeur in candidat["enum"]]
    raise AssertionError(f"Aucune énumération dans le schéma du champ {champ!r}")


def test_schema_outil_expose_les_enums_et_le_spec_structure() -> None:
    """Le schéma du tool est ce que le modèle lit : enums et spec doivent y être."""
    from app.tools.documents import GenererDocumentInput, ValiderDocumentInput

    schema = GenererDocumentInput.model_json_schema()
    assert _valeurs_enum(schema, "format") == ["docx", "xlsx", "pdf"]
    assert "appel_a_proposition" in _valeurs_enum(schema, "ancre")
    assert "carso_sobre" in _valeurs_enum(schema, "style")
    # Le document structuré est décrit par son schéma, pas par de la prose seule.
    assert "DocumentSpec" in schema["$defs"]
    assert {"metadata", "blocs", "sources"} <= set(
        schema["$defs"]["DocumentSpec"]["properties"]
    )
    # Les types de blocs sont énumérés dans le discriminateur : aucune devinette.
    blocs = schema["$defs"]["DocumentSpec"]["properties"]["blocs"]["items"]
    assert {
        "titre",
        "paragraphe",
        "liste",
        "tableau",
        "image",
        "encadre",
        "citation",
        "paires",
        "saut_de_page",
    } <= set(blocs["discriminator"]["mapping"])
    assert {"Titre", "Tableau", "Paires", "Cellule"} <= set(schema["$defs"])
    assert "document_spec" in schema["properties"]

    verifier = ValiderDocumentInput.model_json_schema()
    assert _valeurs_enum(verifier, "portees") == ["structure", "contenu", "style"]
    assert "offre_technique" in _valeurs_enum(verifier, "profil")

    corriger = _tools_schema_corriger()
    assert {"PatchCellule", "PatchBloc", "PatchTableau"} <= set(corriger["$defs"])


def _tools_schema_corriger() -> dict[str, Any]:
    """Schéma de ``CorrigerDocumentInput`` (dépendances injectées inutiles ici)."""
    from app.tools.documents import CorrigerDocumentInput

    return CorrigerDocumentInput.model_json_schema()


def test_document_spec_invalide_erreur_lisible(
    service: DocumentService, organisation: Organisation
) -> None:
    """Un spec incomplet est refusé en désignant le champ fautif, sans deviner."""
    outil = _tools(service)["generer_document"]
    sortie = outil.invoke(
        _payload(
            organisation,
            titre=None,
            paragraphes=None,
            tableau=None,
            document_spec={"metadata": {"reference": "X-1"}, "blocs": []},
        )
    )
    assert sortie["ok"] is False
    assert "metadata.titre" in sortie["error"]["message"]
    champs = [constat["champ"] for constat in sortie["error"]["details"]["erreurs"]]
    assert champs == ["document_spec.metadata.titre"]
    assert sortie["error"]["details"]["erreurs"][0]["probleme"] == "champ requis"


def test_operation_de_correction_inconnue_refusee(
    service: DocumentService, organisation: Organisation
) -> None:
    """Les opérations de correction sont un union typée : un type inventé est refusé."""
    outil = _tools(service)["corriger_document"]
    sortie = outil.invoke(
        {
            "document_spec": {
                "metadata": {"titre": "Rapport"},
                "blocs": [{"type": "paragraphe", "texte": "Contenu."}],
            },
            "operations": [{"type": "recolorer", "bloc": 0}],
            "nom_fichier": "rapport_corrige",
            "type_document": "rapport",
        }
    )
    assert sortie["ok"] is False
    assert "operations" in sortie["error"]["message"]


def test_entity_id_non_uuid_refuse(
    service: DocumentService, organisation: Organisation
) -> None:
    """Un identifiant d'ancrage mal formé est refusé avant tout accès au stockage."""
    outil = _tools(service)["generer_document"]
    sortie = outil.invoke(_payload(organisation, entity_id="pas-un-uuid"))
    assert sortie["ok"] is False
    assert "entity_id" in sortie["error"]["message"]
    assert "UUID" in sortie["error"]["message"]



# --- Incrément 52 : recherche ciblée, pagination honnête, alias de plage -----


def test_outil_chercher_dans_document_construit(service: DocumentService) -> None:
    """Le tool est bien monté dans le graphe (sinon il n'existe pas pour l'agent)."""
    assert "chercher_dans_document" in _tools(service)


def test_chercher_dans_document_sur_document_depose(
    service: DocumentService, organisation: Organisation
) -> None:
    """Retour attendu : [{page, content, ...}] + la page se relit aussitôt."""
    outils = _tools(service)
    depose = outils["generer_document"].invoke(
        _payload(organisation, nom_fichier="recherche_offre")
    )

    sortie = outils["chercher_dans_document"].invoke(
        {"document_id": depose["document_id"], "mot_cle": "Synthèse"}
    )

    assert sortie["document_id"] == depose["document_id"]
    assert sortie["format"] == "docx"
    assert sortie["unite"] == "elements"
    assert sortie["nb_resultats"] >= 1
    hit = sortie["resultats"][0]
    assert isinstance(hit["page"], int) and hit["page"] >= 1
    assert "Synthèse" in hit["content"]  # extrait borné contenant le mot-clé
    assert "chemin" not in sortie  # jamais de chemin disque (AGENTS.md §7)

    plage = outils["read_document_range"].invoke(
        {
            "document_id": depose["document_id"],
            "from_page": hit["page"],
            "to_page": hit["page"],
        }
    )
    assert plage["unite"] == "elements"
    assert "Synthèse" in plage["texte"]


def test_chercher_dans_document_recherche_les_tableaux(
    service: DocumentService, organisation: Organisation
) -> None:
    """Le tableau du document déposé est lui aussi parcouru."""
    outils = _tools(service)
    depose = outils["generer_document"].invoke(
        _payload(organisation, nom_fichier="tableau_recherche")
    )

    sortie = outils["chercher_dans_document"].invoke(
        {"document_id": depose["document_id"], "mot_cle": "Gestion"}
    )

    assert sortie["nb_resultats"] == 1
    assert sortie["resultats"][0]["type_bloc"] == "tableau"
    assert "12" in sortie["resultats"][0]["content"]


def test_chercher_dans_document_refuse_sans_permission(
    service: DocumentService,
) -> None:
    outils = build_document_tools(
        identite=IDENTITE,
        policy=PermissionPolicy(allowed_capabilities=frozenset({"document_propose"})),
        service_factory=lambda: service,
    )
    chercher = [o for o in outils if o.name == "chercher_dans_document"][0]
    sortie = chercher.invoke(
        {"document_id": "00000000-0000-0000-0000-000000000004", "mot_cle": "budget"}
    )
    assert sortie["ok"] is False
    assert sortie["error"]["code"] == "permission_denied"


def test_plage_from_page_identique_a_from_index(
    service: DocumentService, organisation: Organisation
) -> None:
    """``from_page``/``to_page`` sont des alias : même sortie que l'ancienne forme."""
    outils = _tools(service)
    depose = outils["generer_document"].invoke(
        _payload(
            organisation,
            nom_fichier="plage_alias",
            paragraphes=["Premier", "Deuxième", "Troisième"],
            tableau=None,
        )
    )

    par_page = outils["read_document_range"].invoke(
        {
            "document_id": depose["document_id"],
            "from_page": 2,
            "to_page": 3,
        }
    )
    par_index = outils["read_document_range"].invoke(
        {
            "document_id": depose["document_id"],
            "from_index": 2,
            "to_index": 3,
        }
    )
    assert par_page == par_index
    assert par_page["debut"] == 2 and par_page["fin"] == 3
    # Le DOCX généré porte son titre au premier index : le corps suit.
    assert "Premier" in par_page["texte"] and "Deuxième" in par_page["texte"]
    assert "Troisième" not in par_page["texte"]

    # Les deux formes ensemble : l'ancienne forme l'emporte (rétro-compatibilité).
    mixte = outils["read_document_range"].invoke(
        {
            "document_id": depose["document_id"],
            "from_index": 1,
            "to_index": 1,
            "from_page": 3,
            "to_page": 3,
        }
    )
    assert mixte["debut"] == 1 and mixte["fin"] == 1
    assert "Troisième" not in mixte["texte"]
    assert mixte["texte"] != par_page["texte"]


def test_get_document_structure_expose_le_nombre_de_pages(
    service: DocumentService, organisation: Organisation
) -> None:
    """PDF : nombre exact. DOCX : ``null`` + unité de lecture (jamais deviné)."""
    outils = _tools(service)
    structure = outils["get_document_structure"]

    pdf = outils["generer_document"].invoke(
        _payload(organisation, format="pdf", nom_fichier="pages_pdf")
    )
    sortie_pdf = structure.invoke({"document_id": pdf["document_id"]})
    assert sortie_pdf["nb_pages"] == 1
    assert sortie_pdf["unite_pagination"] == "pages"

    docx = outils["generer_document"].invoke(
        _payload(organisation, nom_fichier="pages_docx")
    )
    sortie_docx = structure.invoke({"document_id": docx["document_id"]})
    assert sortie_docx["nb_pages"] is None
    assert sortie_docx["unite_pagination"] == "elements"
    assert sortie_docx["nb_elements"] >= 1
    assert sortie_docx["nb_paragraphes"] >= 1
    assert sortie_docx["nb_tableaux"] == 1
    # Chaque entrée de la hiérarchie porte l'index de flux à lire.
    assert all("element" in bloc for bloc in sortie_docx["hierarchie"])


def test_chercher_format_non_supporte_refuse(
    service: DocumentService,
) -> None:
    import io

    from app.application.dto import DocumentUploadInput

    document = service.enregistrer_document(
        DocumentUploadInput(
            type_document="autre",
            nom="notes.txt",
            stream=io.BytesIO(b"budget 2026"),
            created_by="test",
        )
    )
    sortie = _tools(service)["chercher_dans_document"].invoke(
        {"document_id": str(document.id), "mot_cle": "budget"}
    )
    assert sortie["ok"] is False
    assert "non supportée" in sortie["error"]["message"]


def test_schema_chercher_document_borne_les_resultats() -> None:
    """Le schéma borne ``max_resultats`` : le modèle ne peut pas demander 10 000."""
    from app.tools.documents import ChercherDocumentInput

    # Le schéma JSON est ce que le modèle lit réellement : bornes visibles là.
    schema = ChercherDocumentInput.model_json_schema()
    assert schema["properties"]["mot_cle"]["type"] == "array"
    assert schema["properties"]["mot_cle"]["items"]["minLength"] == 2
    entiers = [
        bloc
        for bloc in schema["properties"]["max_resultats"]["anyOf"]
        if bloc.get("type") == "integer"
    ]
    assert entiers and entiers[0]["minimum"] == 1 and entiers[0]["maximum"] == 200


# --- Incrément 53 : lire N'IMPORTE QUEL élément d'un document ------------------


def test_lire_element_document_relit_le_locator_d_un_hit_de_tableau(
    service: DocumentService, organisation: Organisation
) -> None:
    """Le locator rendu par la recherche se recopie tel quel : plus de boucle."""
    outils = _tools(service)
    depose = outils["generer_document"].invoke(
        _payload(organisation, nom_fichier="element_tableau")
    )

    sortie = outils["chercher_dans_document"].invoke(
        {"document_id": depose["document_id"], "mot_cle": "Gestion"}
    )
    hit = sortie["resultats"][0]
    assert hit["type_bloc"] == "tableau"

    arguments = hit["relire"]["arguments"]
    # L'identifiant du document est ajouté par le tool : le modèle n'a rien à
    # reconstruire pour appeler l'outil de relecture.
    assert arguments["document_id"] == depose["document_id"]
    assert arguments["type_element"] == "tableau"
    assert arguments["index"] == 1 and arguments["sous_index"] == 2

    relu = outils["lire_element_document"].invoke(arguments)
    assert relu["document_id"] == depose["document_id"]
    assert relu["type_element"] == "tableau"
    assert relu["ligne_tableau"] == 2
    assert relu["texte"] == "Gestion | 12"

    # Le repère d'élément (``page``) lit le tableau **entier**, en-tête compris.
    tableau = outils["lire_element_document"].invoke(
        {
            "document_id": depose["document_id"],
            "type_element": "element",
            "index": hit["page"],
        }
    )
    assert tableau["type"] == "tableau"
    assert "Thème | Effectif" in tableau["texte"]
    assert "Gestion | 12" in tableau["texte"]
    assert "chemin" not in relu  # jamais de chemin disque (AGENTS.md §7)


def test_lire_element_document_page_pdf_et_feuille_xlsx(
    service: DocumentService, organisation: Organisation
) -> None:
    outils = _tools(service)
    pdf = outils["generer_document"].invoke(
        _payload(organisation, format="pdf", nom_fichier="element_pdf")
    )
    page = outils["lire_element_document"].invoke(
        {"document_id": pdf["document_id"], "type_element": "page", "index": 1}
    )
    assert page["unite"] == "pages" and page["total"] == 1
    assert "Synthèse" in page["texte"]

    classeur = outils["generer_document"].invoke(
        _payload(organisation, format="xlsx", nom_fichier="element_xlsx")
    )
    feuille = outils["lire_element_document"].invoke(
        {"document_id": classeur["document_id"], "type_element": "feuille"}
    )
    assert feuille["feuille"]
    assert "Thème" in " ".join(feuille["entete"])
    assert "Thème | Effectif" in feuille["texte"]

    ligne = outils["lire_element_document"].invoke(
        {
            "document_id": classeur["document_id"],
            "type_element": "ligne",
            "feuille": feuille["feuille"],
            "index": 2,
        }
    )
    assert ligne["unite"] == "lignes"
    assert "Gestion | 12" == ligne["texte"]


def test_lire_element_document_index_hors_document_dit_le_total(
    service: DocumentService, organisation: Organisation
) -> None:
    """Un index hors document est refusé en **disant le total** (aide, pas échec muet)."""
    outils = _tools(service)
    depose = outils["generer_document"].invoke(
        _payload(organisation, nom_fichier="element_hors_bornes")
    )

    sortie = outils["lire_element_document"].invoke(
        {"document_id": depose["document_id"], "type_element": "element", "index": 99}
    )

    assert sortie["ok"] is False
    assert "hors document" in sortie["error"]["message"]
    assert sortie["error"]["details"]["total"] >= 1


def test_lire_element_document_type_absent_du_format_est_refuse(
    service: DocumentService, organisation: Organisation
) -> None:
    """« page » n'existe pas pour un DOCX : refus explicite avec les types valides."""
    outils = _tools(service)
    depose = outils["generer_document"].invoke(
        _payload(organisation, nom_fichier="element_type_inconnu")
    )

    sortie = outils["lire_element_document"].invoke(
        {"document_id": depose["document_id"], "type_element": "page", "index": 1}
    )

    assert sortie["ok"] is False
    valides = sortie["error"]["details"]["types_elements"]
    assert "element" in valides and "tableau" in valides and "page" not in valides


def test_lire_element_document_refuse_sans_permission(service: DocumentService) -> None:
    outils = build_document_tools(
        identite=IDENTITE,
        policy=PermissionPolicy(allowed_capabilities=frozenset({"document_propose"})),
        service_factory=lambda: service,
    )
    outil = [o for o in outils if o.name == "lire_element_document"][0]

    sortie = outil.invoke(
        {"document_id": "00000000-0000-0000-0000-000000000005", "index": 1}
    )

    assert sortie["ok"] is False
    assert sortie["error"]["code"] == "permission_denied"


def test_lire_document_rend_un_apercu_de_texte(
    service: DocumentService, organisation: Organisation
) -> None:
    """« lire un document » ne rend plus seulement la fiche (source de boucles)."""
    outils = _tools(service)
    depose = outils["generer_document"].invoke(
        _payload(organisation, nom_fichier="apercu_lecture")
    )

    sortie = outils["lire_document"].invoke({"document_id": depose["document_id"]})

    assert sortie["texte_lisible"] is True
    assert "Synthèse" in sortie["apercu_texte"]
    assert "Gestion" in sortie["apercu_texte"]  # le tableau du DOCX est extrait
    assert sortie["nb_caracteres"] > 0
    assert sortie["taille_octets"] > 0
    assert "chemin" not in sortie


def test_lire_document_sans_extracteur_dit_non_lisible(
    service: DocumentService,
) -> None:
    """Extension sans extracteur (.doc) : la fiche reste lisible, le contenu non."""
    import io

    from app.application.dto import DocumentUploadInput

    document = service.enregistrer_document(
        DocumentUploadInput(
            type_document="autre",
            nom="ancien_scan.doc",
            stream=io.BytesIO(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 32),
            created_by="test",
        )
    )
    sortie = _tools(service)["lire_document"].invoke({"document_id": str(document.id)})

    assert sortie["texte_lisible"] is False
    assert sortie["apercu_texte"] is None
    assert "extracteur" in sortie["message"]


def test_type_element_aligne_sur_le_module_elements() -> None:
    """Un vocabulaire d'éléments qui diverge du domaine est un mensonge au modèle."""
    from typing import get_args

    from app.documents.elements import TYPES_ELEMENTS
    from app.tools.documents import TypeElement

    valides = {valeur for valeurs in TYPES_ELEMENTS.values() for valeur in valeurs}
    assert set(get_args(TypeElement)) == valides


# --- Incrément 62 : lectures groupées (index et mots-clés en liste) -----------


def test_schema_des_lectures_groupees_borne_les_listes() -> None:
    """Les bornes vivent dans le schéma JSON : le modèle les lit réellement."""
    from app.tools.documents import (
        MAX_ELEMENTS_PAR_LECTURE,
        MAX_MOTS_CLES_PAR_RECHERCHE,
        ChercherDocumentInput,
        LireElementInput,
    )

    schema_index = LireElementInput.model_json_schema()["properties"]["index"]["anyOf"][0]
    assert schema_index["type"] == "array"
    assert schema_index["minItems"] == 1
    assert schema_index["maxItems"] == MAX_ELEMENTS_PAR_LECTURE
    assert schema_index["items"]["minimum"] == 1  # index 1-indexé

    schema_mots = ChercherDocumentInput.model_json_schema()["properties"]["mot_cle"]
    assert schema_mots["type"] == "array"
    assert schema_mots["minItems"] == 1
    assert schema_mots["maxItems"] == MAX_MOTS_CLES_PAR_RECHERCHE
    assert schema_mots["items"]["minLength"] == 2
    assert schema_mots["items"]["maxLength"] == 200
    # Rien dans le schéma qu'un fournisseur de modèle ne saurait pas lire.
    assert "strip_whitespace" not in schema_mots["items"]


def test_lire_element_document_lit_plusieurs_elements_en_un_appel(
    service: DocumentService, organisation: Organisation
) -> None:
    """``index: [2, 3, 4]`` rend ``elements`` : N éléments en un seul appel."""
    outils = _tools(service)
    depose = outils["generer_document"].invoke(
        _payload(
            organisation,
            nom_fichier="elements_groupes",
            tableau=None,
            paragraphes=["Premier", "Deuxième", "Troisième"],
        )
    )

    sortie = outils["lire_element_document"].invoke(
        {
            "document_id": depose["document_id"],
            "type_element": "element",
            "index": [2, 3, 4],
        }
    )

    assert sortie["nb_demandes"] == 3
    assert sortie["nb_lus"] == 3 and sortie["nb_echecs"] == 0
    assert [element["index"] for element in sortie["elements"]] == [2, 3, 4]
    assert all(element["lu"] is True for element in sortie["elements"])
    assert "Premier" in sortie["elements"][0]["texte"]
    assert "Deuxième" in sortie["elements"][1]["texte"]
    assert "Troisième" in sortie["elements"][2]["texte"]
    assert "chemin" not in sortie  # jamais de chemin disque (AGENTS.md §7)


def test_lire_element_document_liste_d_un_index_garde_le_retour_unitaire(
    service: DocumentService, organisation: Organisation
) -> None:
    """Une liste d'un index (locator recopié) rend le même retour qu'un index seul."""
    outils = _tools(service)
    depose = outils["generer_document"].invoke(
        _payload(
            organisation,
            nom_fichier="element_liste_un",
            tableau=None,
            paragraphes=["Premier", "Deuxième"],
        )
    )

    seul = outils["lire_element_document"].invoke(
        {"document_id": depose["document_id"], "index": 2}
    )
    en_liste = outils["lire_element_document"].invoke(
        {"document_id": depose["document_id"], "index": [2]}
    )

    assert "elements" not in en_liste  # retour unitaire, pas groupé
    assert en_liste["element"] == seul["element"] == 2
    assert en_liste["texte"] == seul["texte"]


def test_lire_element_document_echec_partiel_dit_l_index(
    service: DocumentService, organisation: Organisation
) -> None:
    """Un index hors document n'efface pas les lectures valides du même appel."""
    outils = _tools(service)
    depose = outils["generer_document"].invoke(
        _payload(
            organisation,
            nom_fichier="element_echec_partiel",
            tableau=None,
            paragraphes=["Premier", "Deuxième"],
        )
    )

    sortie = outils["lire_element_document"].invoke(
        {"document_id": depose["document_id"], "index": [2, 99]}
    )

    assert sortie["nb_lus"] == 1 and sortie["nb_echecs"] == 1
    assert sortie["elements"][0]["lu"] is True
    assert "Premier" in sortie["elements"][0]["texte"]
    echec = sortie["elements"][1]
    assert echec["index"] == 99 and echec["lu"] is False
    assert "hors document" in echec["erreur"]  # la raison exacte, jamais un blanc


def test_lire_element_document_refuse_sous_index_avec_plusieurs_index(
    service: DocumentService, organisation: Organisation
) -> None:
    """``sous_index`` vise UNE ligne : avec plusieurs index, l'ambiguïté est refusée."""
    outils = _tools(service)
    depose = outils["generer_document"].invoke(
        _payload(organisation, nom_fichier="element_sous_index_groupe")
    )

    sortie = outils["lire_element_document"].invoke(
        {
            "document_id": depose["document_id"],
            "type_element": "tableau",
            "index": [1, 1],
            "sous_index": 1,
        }
    )

    assert sortie["ok"] is False
    assert "sous_index" in sortie["error"]["message"]


def test_lire_element_document_refuse_liste_pour_une_feuille(
    service: DocumentService, organisation: Organisation
) -> None:
    """Une feuille se décrit par son nom : une liste d'index n'a pas de sens."""
    outils = _tools(service)
    depose = outils["generer_document"].invoke(
        _payload(
            organisation,
            format="xlsx",
            nom_fichier="element_feuille_groupee",
            type_document="liste_beneficiaires",
            paragraphes=None,
        )
    )

    sortie = outils["lire_element_document"].invoke(
        {
            "document_id": depose["document_id"],
            "type_element": "feuille",
            "index": [1, 2],
        }
    )

    assert sortie["ok"] is False
    assert "feuille" in sortie["error"]["message"]


def test_lire_element_document_refuse_trop_d_index(
    service: DocumentService, organisation: Organisation
) -> None:
    """Au-delà du plafond, refus explicite : jamais une troncature silencieuse."""
    from app.tools.documents import MAX_ELEMENTS_PAR_LECTURE

    outils = _tools(service)
    depose = outils["generer_document"].invoke(
        _payload(organisation, nom_fichier="element_trop_d_index")
    )

    sortie = outils["lire_element_document"].invoke(
        {
            "document_id": depose["document_id"],
            "index": list(range(1, MAX_ELEMENTS_PAR_LECTURE + 2)),
        }
    )

    assert sortie["ok"] is False
    assert "index" in sortie["error"]["message"]


def test_chercher_dans_document_plusieurs_mots_cles(
    service: DocumentService, organisation: Organisation
) -> None:
    """``mot_cle: [...]`` rend une entrée par mot-clé, dans l'ordre demandé."""
    outils = _tools(service)
    depose = outils["generer_document"].invoke(
        _payload(organisation, nom_fichier="recherche_groupee")
    )

    sortie = outils["chercher_dans_document"].invoke(
        {
            "document_id": depose["document_id"],
            "mot_cle": ["Synthèse", "Gestion", "introuvable"],
        }
    )

    assert sortie["nb_recherches"] == 3
    assert sortie["mots_cles"] == ["Synthèse", "Gestion", "introuvable"]
    assert [entree["mot_cle"] for entree in sortie["recherches"]] == [
        "Synthèse",
        "Gestion",
        "introuvable",
    ]
    assert sortie["recherches"][0]["nb_resultats"] >= 1
    assert sortie["recherches"][1]["resultats"][0]["type_bloc"] == "tableau"
    assert sortie["recherches"][2]["nb_resultats"] == 0
    assert sortie["nb_resultats"] == sum(
        entree["nb_resultats"] for entree in sortie["recherches"]
    )
    assert sortie["tronque"] is False
    # Les locators restent prêts à recopier, mot-clé par mot-clé.
    relire = sortie["recherches"][1]["resultats"][0]["relire"]
    assert relire["arguments"]["document_id"] == depose["document_id"]
    # Le message commun est porté UNE fois, pas répété par mot-clé.
    assert all("message" not in entree for entree in sortie["recherches"])
    assert "chemin" not in sortie


def test_chercher_dans_document_un_mot_cle_en_liste_garde_le_retour_unitaire(
    service: DocumentService, organisation: Organisation
) -> None:
    """Une liste d'un seul mot-clé rend le contrat unitaire d'origine."""
    outils = _tools(service)
    depose = outils["generer_document"].invoke(
        _payload(organisation, nom_fichier="recherche_liste_un")
    )

    sortie = outils["chercher_dans_document"].invoke(
        {"document_id": depose["document_id"], "mot_cle": ["Synthèse"]}
    )

    assert "recherches" not in sortie
    assert sortie["mot_cle"] == "Synthèse"
    assert sortie["nb_resultats"] >= 1


def test_chercher_dans_document_refuse_un_mot_cle_vide(
    service: DocumentService, organisation: Organisation
) -> None:
    """Un mot-clé réduit à des espaces est refusé par le schéma, pas fouillé."""
    outils = _tools(service)
    depose = outils["generer_document"].invoke(
        _payload(organisation, nom_fichier="recherche_mot_vide")
    )

    sortie = outils["chercher_dans_document"].invoke(
        {"document_id": depose["document_id"], "mot_cle": ["   "]}
    )

    assert sortie["ok"] is False
    assert "mot_cle" in sortie["error"]["message"]


def test_read_document_range_plusieurs_plages(
    service: DocumentService, organisation: Organisation
) -> None:
    """``plages: [...]`` lit plusieurs plages en UN appel, dans l'ordre demandé."""
    outils = _tools(service)
    depose = outils["generer_document"].invoke(
        _payload(
            organisation,
            nom_fichier="plages_groupees",
            paragraphes=["Un", "Deux", "Trois", "Quatre", "Cinq", "Six"],
            tableau=None,
        )
    )

    sortie = outils["read_document_range"].invoke(
        {
            "document_id": depose["document_id"],
            "plages": [
                {"from_page": 2, "to_page": 3},
                {"from_index": 5, "to_index": 5},
            ],
        }
    )

    assert sortie["nb_plages"] == 2
    assert sortie["nb_lues"] == 2
    assert sortie["nb_echecs"] == 0
    assert [entree["lu"] for entree in sortie["plages"]] == [True, True]
    # Chaque entrée garde le contrat unitaire (bornes inclusives, texte borné).
    # Le DOCX généré porte son titre à l'index 1 : les paragraphes suivent.
    premiere, seconde = sortie["plages"]
    assert premiere["debut"] == 2 and premiere["fin"] == 3
    assert "Un" in premiere["texte"] and "Deux" in premiere["texte"]
    assert "Trois" not in premiere["texte"]
    assert seconde["debut"] == 5 and seconde["fin"] == 5
    assert "Quatre" in seconde["texte"]
    assert sortie["unite"] == "elements"
    assert "chemin" not in sortie  # jamais de chemin disque (AGENTS.md §7)


def test_read_document_range_une_plage_garde_le_retour_unitaire(
    service: DocumentService, organisation: Organisation
) -> None:
    """Une liste d'une seule plage rend le contrat unitaire d'origine."""
    outils = _tools(service)
    depose = outils["generer_document"].invoke(
        _payload(
            organisation,
            nom_fichier="plages_liste_un",
            paragraphes=["Un", "Deux"],
            tableau=None,
        )
    )

    sortie = outils["read_document_range"].invoke(
        {"document_id": depose["document_id"], "plages": [{"from_page": 2, "to_page": 2}]}
    )

    assert "plages" not in sortie
    assert sortie["debut"] == 2 and sortie["fin"] == 2
    assert "Un" in sortie["texte"]


def test_read_document_range_echec_dit_dans_son_entree(
    service: DocumentService, organisation: Organisation
) -> None:
    """Une plage en échec porte son erreur sans faire perdre les autres lectures."""
    outils = _tools(service)
    depose = outils["generer_document"].invoke(
        _payload(organisation, nom_fichier="plages_echec", paragraphes=["Un", "Deux"], tableau=None)
    )

    sortie = outils["read_document_range"].invoke(
        {
            "document_id": depose["document_id"],
            "plages": [
                {"from_page": 2, "to_page": 2},
                {"from_page": 3, "to_page": 1},  # plage inversée
                {"from_page": 999, "to_page": 1000},  # hors document
            ],
        }
    )

    assert sortie["nb_plages"] == 3
    assert sortie["nb_lues"] == 1
    assert sortie["nb_echecs"] == 2
    assert sortie["plages"][0]["lu"] is True
    for entree in sortie["plages"][1:]:
        assert entree["lu"] is False
        assert entree["erreur"]


def test_read_document_range_refuse_plages_et_bornes_scalaires(
    service: DocumentService, organisation: Organisation
) -> None:
    """Ambiguïté refusée, jamais devinée : plages OU from/to, jamais les deux."""
    outils = _tools(service)
    depose = outils["generer_document"].invoke(
        _payload(organisation, nom_fichier="plages_mixte", paragraphes=["Un", "Deux"], tableau=None)
    )

    sortie = outils["read_document_range"].invoke(
        {
            "document_id": depose["document_id"],
            "plages": [{"from_page": 1, "to_page": 1}],
            "from_page": 2,
        }
    )

    assert sortie["ok"] is False
    assert "plages" in sortie["error"]["message"]
    assert "exclusifs" in sortie["error"]["message"]


def test_read_document_range_refuse_une_plage_sans_bornes(
    service: DocumentService, organisation: Organisation
) -> None:
    """Dans une liste, une plage sans bornes lirait tout : refus explicite."""
    outils = _tools(service)
    depose = outils["generer_document"].invoke(
        _payload(
            organisation,
            nom_fichier="plages_sans_bornes",
            paragraphes=["Un", "Deux"],
            tableau=None,
        )
    )

    sortie = outils["read_document_range"].invoke(
        {
            "document_id": depose["document_id"],
            "plages": [{}, {"from_page": 1, "to_page": 1}],
        }
    )

    assert sortie["nb_plages"] == 2
    assert sortie["nb_echecs"] == 1
    assert "bornes" in sortie["plages"][0]["erreur"]
    assert sortie["plages"][1]["lu"] is True


def test_read_document_range_refuse_trop_de_plages(
    service: DocumentService, organisation: Organisation
) -> None:
    """Au-delà du plafond, refus explicite : jamais une troncature silencieuse."""
    from app.tools.documents import MAX_PLAGES_PAR_LECTURE

    outils = _tools(service)
    depose = outils["generer_document"].invoke(
        _payload(organisation, nom_fichier="plages_trop", paragraphes=["Un"], tableau=None)
    )

    sortie = outils["read_document_range"].invoke(
        {
            "document_id": depose["document_id"],
            "plages": [
                {"from_page": 1, "to_page": 1}
                for _ in range(MAX_PLAGES_PAR_LECTURE + 1)
            ],
        }
    )

    assert sortie["ok"] is False
    assert "plages" in sortie["error"]["message"]
