"""Contexte AG-UI des pièces jointes : fiche + extrait, jamais les octets.

Le dépôt et l'aperçu chat existent déjà. Ces tests vérifient que le modèle
reçoit, dans le même tour, la fiche du document et un extrait borné — et
qu'aucun UUID n'est inventé, aucun octet n'est poussé.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID

from app.agents.generateur_offre import DEFINITION as GENERATEUR
from app.agents.harness import build_system_prompt
from app.agents.pieces_jointes_contexte import (
    BLOC_PIECES_JOINTES,
    assembler_contexte,
    collecter_ids_documents,
    fiche_et_extrait,
    injecter_contexte_dans_payload,
)
from app.application.dto import ExtractedDocument
from app.core.config import Settings
from app.core.errors import NotFoundError, ValidationError

DOC_ID = UUID("8f1c5d2e-0000-4000-8000-000000000001")
APPEL_ID = UUID("aaaaaaaa-0000-4000-8000-000000000001")


class _Document:
    def __init__(self) -> None:
        self.id = DOC_ID
        self.nom = "appel-2026.pdf"
        self.type_document = "appel_proposition"
        self.statut = "draft"
        self.version = 1
        self.mime_type = "application/pdf"
        self.taille_octets = 2048
        self.appel_id = APPEL_ID
        self.offre_id = None
        self.mission_id = None
        self.equipe_id = None
        self.session_id = None
        self.organisation_id = None


class _Service:
    def __init__(
        self,
        *,
        document: object | None = None,
        texte: str = "MISSION CARSO 2026 — formation en gestion.",
        tronque: bool = False,
        extraire_erreur: Exception | None = None,
        obtenir_erreur: Exception | None = None,
    ) -> None:
        self.document = document if document is not None else _Document()
        self.texte = texte
        self.tronque = tronque
        self.extraire_erreur = extraire_erreur
        self.obtenir_erreur = obtenir_erreur

    def obtenir(self, identifiant: UUID) -> object:
        if self.obtenir_erreur is not None:
            raise self.obtenir_erreur
        if identifiant != DOC_ID:
            raise NotFoundError(f"Document {identifiant} introuvable")
        return self.document

    def extraire_texte(self, identifiant: UUID) -> ExtractedDocument:
        if self.extraire_erreur is not None:
            raise self.extraire_erreur
        return ExtractedDocument(
            document_id=identifiant,
            nom="appel-2026.pdf",
            extension=".pdf",
            mime_type="application/pdf",
            texte=self.texte,
            adaptateur="pdf",
            nb_caracteres=len(self.texte),
            nb_pages=1,
            nb_feuilles=None,
            feuilles=(),
            tronque=self.tronque,
        )


def _payload_parties() -> dict:
    return {
        "threadId": "fil-1",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Analyse ce document."},
                    {
                        "type": "binary",
                        "source": {
                            "type": "url",
                            "value": f"document://{DOC_ID}",
                        },
                        "metadata": {
                            "filename": "appel-2026.pdf",
                            "document_id": str(DOC_ID),
                        },
                    },
                ],
            }
        ],
        "context": [],
    }


def _payload_texte() -> dict:
    return {
        "threadId": "fil-1",
        "messages": [
            {
                "role": "user",
                "content": (
                    "Analyse ce document.\n"
                    f"[Pièce jointe : appel-2026.pdf — document {DOC_ID}]"
                ),
            }
        ],
        "context": [],
    }


# --- Collecte -----------------------------------------------------------------


def test_collecte_depuis_parties_binaires() -> None:
    assert collecter_ids_documents(_payload_parties()) == (str(DOC_ID),)


def test_collecte_depuis_texte_normalise() -> None:
    assert collecter_ids_documents(_payload_texte()) == (str(DOC_ID),)


def test_collecte_depuis_marqueur_courant() -> None:
    """Le marqueur courant (séparateur « · ») est lu comme l'ancien « — »."""
    payload = {
        "messages": [
            {
                "role": "user",
                "content": f"[Pièce jointe : appel-2026.pdf · document {DOC_ID}]",
            }
        ]
    }
    assert collecter_ids_documents(payload) == (str(DOC_ID),)


def test_collecte_dernier_message_seulement() -> None:
    payload = {
        "messages": [
            {
                "role": "user",
                "content": f"[Pièce jointe — document {DOC_ID}]",
            },
            {"role": "assistant", "content": "Reçu."},
            {"role": "user", "content": "Continue sans fichier."},
        ]
    }
    assert collecter_ids_documents(payload) == ()


def test_collecte_sans_doublon() -> None:
    payload = {
        "messages": [
            {
                "role": "user",
                "content": (
                    f"[Pièce jointe — document {DOC_ID}]\n"
                    f"[Pièce jointe — document {DOC_ID}]"
                ),
            }
        ]
    }
    assert collecter_ids_documents(payload) == (str(DOC_ID),)


def test_collecte_ignore_identifiant_invalide() -> None:
    payload = {
        "messages": [
            {"role": "user", "content": "[Pièce jointe — document pas-un-uuid]"}
        ]
    }
    assert collecter_ids_documents(payload) == ()


# --- Fiche + extrait ----------------------------------------------------------


def test_fiche_et_extrait_contiennent_identifiant_et_texte() -> None:
    item = fiche_et_extrait(str(DOC_ID), service=_Service(), max_chars=4000)

    assert "appel-2026.pdf" in item["description"]
    assert str(DOC_ID) in item["value"]
    assert "MISSION CARSO 2026" in item["value"]
    assert "Ne redemande pas" in item["value"]
    # Vocabulaire 26/10 : l'ancre se lit « appel » (le type est une donnée,
    # pas un préfixe) — la ligne reste précise : « Rattaché à : appel <id> ».
    assert "Rattaché à : appel" in item["value"]
    assert "data:" not in item["value"]
    assert "%PDF" not in item["value"]


def test_extrait_est_borne() -> None:
    long_texte = "A" * 8000
    item = fiche_et_extrait(
        str(DOC_ID), service=_Service(texte=long_texte), max_chars=400
    )

    assert "A" * 400 in item["value"]
    assert "A" * 401 not in item["value"]
    assert "tronqué" in item["value"]


def test_document_introuvable_ne_redemande_pas_uuid() -> None:
    item = fiche_et_extrait(
        str(DOC_ID),
        service=_Service(obtenir_erreur=NotFoundError("absent")),
        max_chars=4000,
    )

    assert str(DOC_ID) in item["value"]
    assert "introuvable" in item["value"]
    assert "Ne redemande pas" in item["value"]


def test_extraction_indisponible_laisse_la_fiche() -> None:
    item = fiche_et_extrait(
        str(DOC_ID),
        service=_Service(extraire_erreur=ValidationError("format inconnu")),
        max_chars=4000,
    )

    assert "appel-2026.pdf" in item["value"]
    assert "Extrait indisponible" in item["value"]
    assert "Ne redemande pas" in item["value"]


def test_assembler_un_item_par_id() -> None:
    autre = "bbbbbbbb-0000-4000-8000-000000000002"
    items = assembler_contexte(
        (str(DOC_ID), autre), service=_Service(), max_chars=4000
    )

    assert len(items) == 2
    assert str(DOC_ID) in items[0]["value"]
    assert autre in items[1]["value"]
    assert "introuvable" in items[1]["value"]


# --- Injection payload --------------------------------------------------------


def test_injection_ajoute_context_sans_toucher_au_prompt_utilisateur() -> None:
    service = _Service()
    session = SimpleNamespace(close=lambda: None)

    def factory() -> object:
        return session

    # Monkeypatch DocumentService à l'intérieur de l'injecteur via un service
    # déjà assemblé : on teste assembler + fusion ici, et injecter via stub.
    payload = _payload_texte()
    items = assembler_contexte((str(DOC_ID),), service=service, max_chars=4000)
    assert items
    fusion = list(payload["context"]) + items
    assert fusion[0]["value"].startswith("Fiche du document")
    assert "Analyse ce document." in payload["messages"][0]["content"]


def test_injecter_sans_id_rend_le_meme_objet() -> None:
    payload = {"messages": [{"role": "user", "content": "Bonjour"}], "context": []}
    assert injecter_contexte_dans_payload(payload) is payload


def test_injecter_avec_factory_qui_echoue_laisse_intact() -> None:
    def factory() -> object:
        raise RuntimeError("base indisponible")

    payload = _payload_texte()
    assert injecter_contexte_dans_payload(payload, session_factory=factory) is payload


def test_injecter_fusionne_le_contexte_existant(monkeypatch) -> None:
    service = _Service()
    session = SimpleNamespace(close=lambda: None)

    class _DocService:
        def __init__(self, _session: object) -> None:
            self._inner = service

        def obtenir(self, identifiant: UUID) -> object:
            return self._inner.obtenir(identifiant)

        def extraire_texte(self, identifiant: UUID) -> ExtractedDocument:
            return self._inner.extraire_texte(identifiant)

    monkeypatch.setattr(
        "app.application.services.document_service.DocumentService",
        _DocService,
    )
    payload = _payload_texte()
    payload["context"] = [{"description": "page", "value": "missions"}]
    enrichi = injecter_contexte_dans_payload(
        payload,
        session_factory=lambda: session,
        settings=Settings(_env_file=None),
    )

    assert enrichi is not payload
    assert enrichi["context"][0] == {"description": "page", "value": "missions"}
    assert "MISSION CARSO 2026" in enrichi["context"][1]["value"]
    assert enrichi["messages"] is payload["messages"]


# --- Prompt -------------------------------------------------------------------


def test_prompt_interdit_de_redemander_l_uuid() -> None:
    systeme = build_system_prompt(GENERATEUR)
    assert BLOC_PIECES_JOINTES in systeme
    assert "Ne le redemande jamais" in systeme


# --- Images (demande 04/10) ---------------------------------------------------


def test_image_ne_force_pas_les_outils_de_lecture_textuelle() -> None:
    """Une image se regarde si le modèle le peut — elle n'a pas de plage à lire."""
    document = _Document()
    document.nom = "photo-terrain.png"
    document.mime_type = "image/png"

    item = fiche_et_extrait(
        str(DOC_ID), service=_Service(document=document, texte=""), max_chars=4000
    )

    valeur = item["value"]
    assert "image" in valeur.lower()
    assert "N'appelle pas" in valeur
    assert "read_document_range" in valeur  # pour l'interdire explicitement
    assert "Sinon lis la suite" not in valeur


def test_document_textuel_garde_la_consigne_d_extraction() -> None:
    """Le PDF garde la consigne normale : lire la suite avec les outils."""
    item = fiche_et_extrait(str(DOC_ID), service=_Service(), max_chars=4000)
    valeur = item["value"]

    assert "Sinon lis la suite avec tes outils de lecture" in valeur
    assert "N'appelle pas" not in valeur
