"""Contexte de pièces jointes : fiche + extrait, jamais les octets.

Le dépôt (disque + PostgreSQL) et l'aperçu chat existent déjà. Le trou
était entre la référence ``document <uuid>`` et le modèle : celui-ci ne
recevait qu'une ligne et redemandait l'identifiant.

Ce module, appelé par le middleware AG-UI **après** la normalisation :

1. lit les identifiants du **dernier** message utilisateur du tour ;
2. charge chaque document via ``DocumentService`` (lecture seule) ;
3. produit des items ``{description, value}`` au format AG-UI ``Context``
   (fiche + extrait borné par ``AGENT_ATTACHMENT_EXCERPT_CHARS``).

Aucune mutation. Aucun octet, aucun chemin disque, aucun secret. Un
échec (base, document introuvable, extraction) n'interrompt jamais la
conversation : on signale l'absence et on laisse le tour continuer.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Sequence
from typing import Any
from uuid import UUID

from app.core.config import Settings, get_settings
from app.core.errors import CarsoError, NotFoundError, ValidationError

_logger = logging.getLogger(__name__)

#: UUID dans ``document <id>`` ou ``document://<id>``.
_RE_DOCUMENT_ID = re.compile(
    r"document(?:://|[ \u00a0]+)([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})"
)

_ANCRES: tuple[tuple[str, str], ...] = (
    ("appel_a_proposition_id", "appel à proposition"),
    ("offre_id", "offre"),
    ("mission_id", "mission"),
    ("equipe_id", "équipe"),
    ("session_id", "session"),
    ("organisation_id", "organisation"),
)


def dernier_message_utilisateur(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Dernier message ``role=user`` du tour — les pièces antérieures restent hors extrait."""
    messages = payload.get("messages")
    if not isinstance(messages, list):
        return None
    for message in reversed(messages):
        if isinstance(message, dict) and message.get("role") == "user":
            return message
    return None


def _uuid_ou_rien(valeur: object) -> str | None:
    if not isinstance(valeur, str):
        return None
    texte = valeur.strip()
    for prefixe in ("document://", "carso://document/"):
        if texte.startswith(prefixe):
            texte = texte[len(prefixe) :].strip("/ ")
            break
    try:
        return str(UUID(texte))
    except ValueError:
        return None


def _ids_depuis_parties(contenu: list[Any]) -> list[str]:
    ids: list[str] = []
    for partie in contenu:
        if not isinstance(partie, dict):
            continue
        metadonnees = partie.get("metadata")
        metadonnees = metadonnees if isinstance(metadonnees, dict) else {}
        source = partie.get("source")
        source = source if isinstance(source, dict) else {}
        for candidat in (
            metadonnees.get("document_id"),
            metadonnees.get("documentId"),
            source.get("value"),
        ):
            identifiant = _uuid_ou_rien(candidat)
            if identifiant:
                ids.append(identifiant)
                break
        texte = partie.get("text")
        if isinstance(texte, str):
            ids.extend(_RE_DOCUMENT_ID.findall(texte))
    return ids


def collecter_ids_documents(payload: dict[str, Any]) -> tuple[str, ...]:
    """Identifiants UUID du dernier message utilisateur, sans doublon, ordre conservé."""
    message = dernier_message_utilisateur(payload)
    if message is None:
        return ()
    contenu = message.get("content")
    bruts: list[str] = []
    if isinstance(contenu, list):
        bruts.extend(_ids_depuis_parties(contenu))
    elif isinstance(contenu, str):
        bruts.extend(_RE_DOCUMENT_ID.findall(contenu))
    vus: set[str] = set()
    uniques: list[str] = []
    for identifiant in bruts:
        normalise = _uuid_ou_rien(identifiant)
        if normalise is None or normalise in vus:
            continue
        vus.add(normalise)
        uniques.append(normalise)
    return tuple(uniques)


def _ancre_lisible(document: Any) -> str | None:
    for champ, libelle in _ANCRES:
        valeur = getattr(document, champ, None)
        if valeur is not None:
            return f"{libelle} {valeur}"
    return None


def _item(description: str, value: str) -> dict[str, str]:
    return {"description": description, "value": value}


def fiche_et_extrait(
    document_id: str,
    *,
    service: Any,
    max_chars: int,
) -> dict[str, str]:
    """Un item AG-UI ``Context`` : fiche + extrait, ou motif d'indisponibilité."""
    try:
        identifiant = UUID(document_id)
    except ValueError:
        return _item(
            f"Pièce jointe (référence {document_id})",
            "Cette référence n'est pas un identifiant de document CARSO. "
            "Ne redemande pas l'UUID à l'utilisateur.",
        )

    try:
        document = service.obtenir(identifiant)
    except NotFoundError:
        return _item(
            f"Pièce jointe — document {document_id}",
            (
                f"Identifiant déjà connu : {document_id}.\n"
                "Le document est introuvable en base. Ne redemande pas l'UUID ; "
                "signale-le à l'utilisateur."
            ),
        )
    except CarsoError as exc:
        return _item(
            f"Pièce jointe — document {document_id}",
            (
                f"Identifiant déjà connu : {document_id}.\n"
                f"Fiche indisponible ({exc}). Ne redemande pas l'UUID."
            ),
        )

    ancre = _ancre_lisible(document)
    lignes_fiche = [
        f"Identifiant déjà connu : {document.id}",
        f"Nom : {document.nom}",
        f"Type : {document.type_document}",
        f"Statut : {document.statut}",
        f"Version : {document.version}",
    ]
    if document.mime_type:
        lignes_fiche.append(f"MIME : {document.mime_type}")
    if document.taille_octets is not None:
        lignes_fiche.append(f"Taille : {document.taille_octets} octets")
    if ancre:
        lignes_fiche.append(f"Rattaché à : {ancre}")
    elif getattr(document, "type_document", None) == "non_classe":
        lignes_fiche.append("Rattaché à : aucun (document non classé)")

    extrait, mention = _extraire(identifiant, service=service, max_chars=max_chars)
    parties = [
        "Fiche du document joint à ce tour.",
        *lignes_fiche,
        "",
        "Ne redemande pas cet identifiant. Si l'extrait suffit, réponds. "
        "Sinon lis la suite avec tes outils de lecture "
        "(read_document_text, read_document_range, lire_document).",
        "",
        mention,
    ]
    if extrait:
        parties.extend(["---", extrait, "---"])
    return _item(f"Pièce jointe : {document.nom} (document {document.id})", "\n".join(parties))


def _extraire(identifiant: UUID, *, service: Any, max_chars: int) -> tuple[str, str]:
    """Texte borné + légende. Jamais d'exception hors de cette fonction."""
    try:
        extrait = service.extraire_texte(identifiant)
    except ValidationError as exc:
        return "", f"Extrait indisponible : {exc}"
    except CarsoError as exc:
        return "", f"Extrait indisponible : {exc}"
    except Exception as exc:  # pragma: no cover - moteur d'extraction imprévisible
        _logger.warning("Extraction pièce jointe %s : %s", identifiant, exc)
        return "", "Extrait indisponible (erreur d'extraction). Ne redemande pas l'UUID."

    texte = (extrait.texte or "").strip()
    if not texte:
        return "", "Aucun texte extractible (document scanné ou vide)."

    plafond = max(1, max_chars)
    tronque = bool(getattr(extrait, "tronque", False)) or len(texte) > plafond
    borne = texte[:plafond]
    mention = (
        f"Extrait (premiers {len(borne)} caractères"
        + (", tronqué" if tronque else "")
        + ")."
    )
    return borne, mention


def assembler_contexte(
    ids: Sequence[str],
    *,
    service: Any,
    max_chars: int,
) -> list[dict[str, str]]:
    """Un item par identifiant, dans l'ordre. Aucune invention d'id."""
    return [
        fiche_et_extrait(identifiant, service=service, max_chars=max_chars) for identifiant in ids
    ]


def injecter_contexte_dans_payload(
    payload: dict[str, Any],
    *,
    session_factory: Callable[[], Any] | None = None,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """Ajoute les items de contexte au payload, ou le rend inchangé (même objet)."""
    ids = collecter_ids_documents(payload)
    if not ids:
        return payload

    reglages = settings or get_settings()
    factory = session_factory
    if factory is None:
        try:
            from app.infrastructure.database import SessionLocal

            factory = SessionLocal
        except Exception:  # pragma: no cover - import / moteur
            _logger.warning("Pièces jointes : session indisponible, contexte non injecté")
            return payload

    session = None
    try:
        session = factory()
        from app.application.services.document_service import DocumentService

        items = assembler_contexte(
            ids,
            service=DocumentService(session),
            max_chars=reglages.agent_attachment_excerpt_chars,
        )
    except Exception:
        _logger.warning("Pièces jointes : assemblage du contexte échoué", exc_info=True)
        return payload
    finally:
        if session is not None:
            try:
                session.close()
            except Exception:  # pragma: no cover
                pass

    if not items:
        return payload

    existant = payload.get("context")
    fusion = list(existant) if isinstance(existant, list) else []
    fusion.extend(items)
    copie = dict(payload)
    copie["context"] = fusion
    return copie


#: Bloc system prompt — tous les agents exposés.
BLOC_PIECES_JOINTES = """
## Pièces jointes du chat
Lorsqu'un utilisateur joint un fichier à ce tour, tu reçois déjà :
- la **fiche** du document (identifiant, nom, type, statut) ;
- un **extrait borné** du texte (pas le fichier entier, jamais les octets).
L'identifiant est dans ce contexte. **Ne le redemande jamais.**
Si l'extrait suffit, réponds. Sinon lis la suite avec tes outils de lecture
(`read_document_text`, `read_document_range`, `lire_document`).
N'invente aucun contenu absent de l'extrait ou des tools.
""".strip()


__all__ = [
    "BLOC_PIECES_JOINTES",
    "assembler_contexte",
    "collecter_ids_documents",
    "dernier_message_utilisateur",
    "fiche_et_extrait",
    "injecter_contexte_dans_payload",
]
