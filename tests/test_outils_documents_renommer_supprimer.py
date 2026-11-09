"""Renommer et supprimer un document — outils manquants du système documentaire.

Deux gestes demandés qui n'existaient pas pour les agents :

1. **renommer** : changer le nom d'un document existant **sans le régénérer**.
   Délègue à ``DocumentService.modifier_metadonnees`` (métadonnées seules) ;
   le refus sur un document versionné est celui du service — on ne le contourne
   pas ;
2. **supprimer** : mise à la corbeille, **après validation humaine** (le tool est
   déclaré dans ``approval_required``, donc interrompu par le middleware HITL
   avant exécution). La décision est tracée avec le compte demandeur quand il
   est connu.

Aucune base n'est nécessaire : le service documentaire est un double, comme dans
``test_tools_images_agents.py``.
"""

from __future__ import annotations

from uuid import uuid4

from app.application.trace import demande_par
from app.core.errors import ConflictError
from app.tools.documents import build_document_tools
from app.tools.permissions import AgentIdentity, PermissionPolicy

CAPACITES = frozenset({"document_read", "document_propose"})


class _DocumentDouble:
    """Fiche minimale renvoyée par le service double."""

    def __init__(self, identifiant, nom: str = "compte_rendu.docx") -> None:
        self.id = identifiant
        self.nom = nom
        self.type_document = "rapport"
        self.version = 1
        self.statut = "proposed"


class _ServiceDouble:
    """Service documentaire minimal : renommage et suppression seuls."""

    def __init__(self, *, erreur_renommage: Exception | None = None) -> None:
        self.identifiant = uuid4()
        self.document = _DocumentDouble(self.identifiant)
        self.erreur_renommage = erreur_renommage
        self.renommages: list[object] = []
        self.suppressions: list[tuple[object, object]] = []

    def obtenir(self, document_id):
        return self.document

    def modifier_metadonnees(self, document_id, entree):
        if self.erreur_renommage is not None:
            raise self.erreur_renommage
        self.renommages.append(entree)
        self.document.nom = entree.nom
        return self.document

    def supprimer_document(self, document_id, decision):
        self.suppressions.append((document_id, decision))
        self.document.statut = "supprime"
        return 2


def _outils(service: _ServiceDouble, capacites: frozenset[str] = CAPACITES):
    construits = build_document_tools(
        identite=AgentIdentity(agent_id="agent_generaliste_readonly"),
        policy=PermissionPolicy(allowed_capabilities=capacites),
        service_factory=lambda: service,
    )
    return {tool.name: tool for tool in construits}, service


# --- 1. Renommage -----------------------------------------------------------


def test_renommer_change_le_nom_sans_toucher_au_contenu() -> None:
    outils, service = _outils(_ServiceDouble())

    sortie = outils["renommer_document"].invoke(
        {"document_id": str(service.identifiant), "nom": "compte_rendu_final.docx"}
    )

    assert sortie["ok"] is True, sortie
    assert sortie["nom"] == "compte_rendu_final.docx"
    # Le service n'a reçu QUE le nom : aucune régénération, aucun contenu.
    entree = service.renommages[0]
    assert entree.nom == "compte_rendu_final.docx"
    assert entree.type_document is None
    assert entree.doc_metadata is None
    assert service.document.version == 1


def test_renommer_exige_la_capacite_de_modification() -> None:
    outils, service = _outils(_ServiceDouble(), frozenset({"document_read"}))

    sortie = outils["renommer_document"].invoke(
        {"document_id": str(service.identifiant), "nom": "x.docx"}
    )

    assert sortie["ok"] is False
    assert "document_propose" in sortie["error"]["message"]


def test_renommer_remonte_le_refus_du_document_versionne() -> None:
    """Le service refuse plus d'une version : le message arrive tel quel."""
    erreur = ConflictError(
        "Un document à plusieurs versions ne peut pas être renommé ni changer "
        "de type : archivez-le puis déposez un nouveau document"
    )
    outils, service = _outils(_ServiceDouble(erreur_renommage=erreur))

    sortie = outils["renommer_document"].invoke(
        {"document_id": str(service.identifiant), "nom": "x.docx"}
    )

    assert sortie["ok"] is False
    assert "plusieurs versions" in sortie["error"]["message"]


# --- 2. Suppression ---------------------------------------------------------


def test_supprimer_trace_le_compte_demandeur_et_le_motif() -> None:
    outils, service = _outils(_ServiceDouble())
    identifiant = str(service.identifiant)

    with demande_par("6c0fa6aa-0bff-4987-bba9-3d2a775f2d19"):
        sortie = outils["supprimer_document"].invoke(
            {"document_id": identifiant, "motif": "doublon signalé par l'utilisateur"}
        )

    assert sortie["ok"] is True, sortie
    assert sortie["suppression"] == "corbeille"
    _, decision = service.suppressions[0]
    assert decision.decided_by == "6c0fa6aa-0bff-4987-bba9-3d2a775f2d19"
    assert decision.reason == "doublon signalé par l'utilisateur"


def test_supprimer_sans_compte_connu_ne_pretend_pas_a_un_humain() -> None:
    outils, service = _outils(_ServiceDouble())

    sortie = outils["supprimer_document"].invoke(
        {"document_id": str(service.identifiant), "motif": "demande de l'utilisateur"}
    )

    assert sortie["ok"] is True, sortie
    _, decision = service.suppressions[0]
    assert decision.decided_by == "agent:agent_generaliste_readonly"


def test_supprimer_exige_la_capacite_de_modification() -> None:
    outils, service = _outils(_ServiceDouble(), frozenset({"document_read"}))

    sortie = outils["supprimer_document"].invoke(
        {"document_id": str(service.identifiant), "motif": "test"}
    )

    assert sortie["ok"] is False
    assert not service.suppressions


# --- 3. HITL : la suppression attend la décision humaine --------------------


def test_les_six_agents_declarent_renommer_et_supprimer() -> None:
    from app.agents.catalogue import AGENT_DEFINITIONS

    for definition in AGENT_DEFINITIONS:
        assert "renommer_document" in definition.tools, definition.agent_id
        assert "supprimer_document" in definition.tools, definition.agent_id
        # Sans cette déclaration, le middleware n'interrompt pas : le document
        # serait supprimé sans validation.
        assert "supprimer_document" in definition.approval_required, definition.agent_id


def test_le_renommage_n_est_pas_interrompu() -> None:
    """Renommer ne détruit rien : seul le geste destructeur passe par le HITL."""
    from app.agents.catalogue import AGENT_DEFINITIONS

    for definition in AGENT_DEFINITIONS:
        assert "renommer_document" not in definition.approval_required, definition.agent_id


# --- 4. Copie de modèle : nom personnalisé déjà pris en charge --------------


def test_copier_modele_accepte_un_nom_personnalise() -> None:
    """Le nom de la copie est réglable à la source (``nom_fichier``)."""
    from app.tools.documents import CopierModeleDocumentInput

    champ = CopierModeleDocumentInput.model_fields["nom_fichier"]
    assert champ.default is None
    assert "Nom" in (champ.description or "")


def test_remplacer_element_model_existe_toujours() -> None:
    """L'édition d'un élément précis reste celle du système existant."""
    service = _ServiceDouble()
    outils, _ = _outils(service)

    assert "remplacer_element_model" in outils
    assert "remplir_modele" in outils
