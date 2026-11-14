"""Socle partagé du service documentaire : constantes, garde-fous de périmètre.

Extrait de ``document_service`` sans modification de contenu. Ce module ne
dépend d'aucune classe du service : il porte ce que le parent et les mixins de
responsabilité doivent lire au même endroit.

Pourquoi un module séparé : les quatre mixins (métadonnées, corbeille,
visibilité, accès) utilisent les mêmes constantes d'ancre, de visibilité et de
listage. Les laisser dans ``document_service`` aurait forcé soit une
duplication, soit un import circulaire (le parent important les mixins).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.application.perimetre import Perimetre
from app.core.errors import ValidationError
from app.documents.paths import (
    SCOPE_PERSONNEL,
)
from app.domain.document import Document
from app.domain.enums import (
    RoleUtilisateur,
    TypeDocument,
)
from app.infrastructure.repositories import (
    SessionRepository,
)

_ENTITY = "document"

#: Scope des documents sans objet métier : le dossier du compte propriétaire.
_SCOPE_PERSONNEL = SCOPE_PERSONNEL
#: Champ fictif : aucune colonne FK n'est écrite pour ce repli.
_CHAMP_PERSONNEL = ""

#: Visibilité d'un document **sans ancre** (ADR 0013) : ``prive`` (défaut) ou
#: ``public``. Seul le propriétaire (ou l'administration) peut la changer.
_VISIBILITE_PRIVEE = "prive"
_VISIBILITE_PUBLIQUE = "public"

#: Ancres métier acceptées → dossier de stockage (instruction/06 §3).
#: ``organisations`` et ``sessions`` sont des scopes ajoutés depuis la cible
#: (voir ``app.documents.paths`` et l'ADR 0003).
_ANCHORS: dict[str, str] = {
    "organisation_id": "organisations",
    "appel_id": "appel_proposition",
    "offre_id": "offres",
    "mission_id": "missions",
    "equipe_id": "equipes",
    "session_id": "sessions",
}

#: Nom public d'ancre → colonne FK (INC-124 : scripts ancrés).
CHAMP_PAR_ANCRE: dict[str, str] = {
    "organisation": "organisation_id",
    "appel": "appel_id",
    "offre": "offre_id",
    "mission": "mission_id",
    "equipe": "equipe_id",
    "session": "session_id",
}

_META_KEY = "proposed_by_agent"

#: Compte pour lequel un document a été créé sous périmètre restreint : un
#: formateur qui fait générer un document par l'agent (ou le dépose lui-même)
#: doit toujours le retrouver dans Documents, même sans ancre métier
#: (incrément 74 : « tous les utilisateurs accèdent à ce qu'ils génèrent »).
_META_GENERE_POUR = "genere_pour"

#: Clé du *tombstone* écrit dans ``doc_metadata`` à la suppression logique :
#: qui, quand, pourquoi — la fiche survit au fichier. Il porte aussi l'échéance
#: de conservation et le statut de retour (ADR 0006).
_META_SUPPRESSION = "suppression"

#: Clé du journal des restaurations : chaque entrée dit quand, par qui, et si les
#: octets ont été **redéposés** (un fichier ne revient pas tout seul du disque).
_META_RESTAURATIONS = "restaurations"

#: Filtres de listing exposés par l'API → méthode du repository.
_LISTERS: dict[str, str] = {
    "organisation_id": "list_for_organisation",
    "appel_id": "list_for_appel",
    "offre_id": "list_for_offre",
    "mission_id": "list_for_mission",
    "equipe_id": "list_for_equipe",
    "session_id": "list_for_session",
}

logger = logging.getLogger(__name__)


def document_dans_perimetre(
    document: Document, perimetre: Perimetre, session: Session
) -> bool:
    """Vrai si le document est accessible au compte — règle unique (ADR 0013).

    1. **L'ancre prime** : un document rattaché à un objet métier suit le
       périmètre de cet objet — accès complet pour l'administration et
       l'équipe interne ; pour un compte restreint, la mission (ou la session
       de cette mission, ou la personne du vivier) doit appartenir à son
       périmètre.
    2. **Sans ancre**, le document est **propre à son propriétaire** : seul le
       propriétaire, un **administrateur**, ou — si le propriétaire l'a
       déclaré **public** — tout compte actif y accède. Le court-circuit
       « accès complet ⇒ tout » ne s'applique plus ici : un collaborateur ne
       voit pas le document personnel d'un autre collaborateur.

    Règle unique : ``DocumentService`` l'applique à ses lectures, et les agents
    de consultation (généraliste, statistique) l'appellent pour leurs propres
    lectures — une seule définition, jamais deux lectures divergentes.
    """
    # --- CV : un document de personne lisible par tous ---------------------
    # Décision CARSO (13/11) : le CV appartient au dossier personnel de la
    # personne, mais il sert à constituer les équipes — tout compte actif le
    # consulte, même hors de son périmètre de missions. L'ancre ``equipe_id``
    # continue de ranger le fichier au bon endroit ; elle ne le cloisonne plus.
    # Un appel sans compte (script, balayage) garde la règle commune ci-dessous.
    if (
        document.type_document == TypeDocument.CV.value
        and perimetre.utilisateur_id is not None
    ):
        return True
    if document.mission_id is not None:
        return perimetre.mission_autorisee(document.mission_id) or _est_administrateur(
            perimetre
        )
    if document.session_id is not None:
        session_formation = SessionRepository(session).get(document.session_id)
        return session_formation is not None and (
            perimetre.mission_autorisee(session_formation.mission_id)
            or _est_administrateur(perimetre)
        )
    if document.equipe_id is not None:
        return perimetre.equipe_autorisee(document.equipe_id) or _est_administrateur(
            perimetre
        )
    if document.organisation_id is not None or document.appel_id is not None:
        # Ancres de l'équipe interne : le référentiel commercial n'est pas du
        # périmètre d'un formateur, l'administration le couvre toujours.
        return perimetre.est_complet
    if document.offre_id is not None:
        return perimetre.est_complet
    # --- Sans ancre métier : propriété documentaire (ADR 0013) -------------
    # Appel **sans compte** (scripts, balayage, mode lab) : accès complet
    # implicite, même convention que ``perimetre_effectif`` — aucune requête
    # utilisateur ne se présente sans ``utilisateur_id``.
    if perimetre.est_complet and perimetre.utilisateur_id is None:
        return True
    if _est_administrateur(perimetre):
        return True
    if perimetre.utilisateur_id is not None and document.proprietaire_id == perimetre.utilisateur_id:
        return True
    if document.visibilite == _VISIBILITE_PUBLIQUE:
        return perimetre.utilisateur_id is not None
    # Compatibilité des fiches créées avant la colonne ``proprietaire_id`` :
    # ``created_by`` reste honoré pour le compte qui a déposé (incrément 74).
    if perimetre.utilisateur_id is not None and document.proprietaire_id is None:
        pour = str(perimetre.utilisateur_id)
        if document.created_by == pour:
            return True
        meta = document.doc_metadata or {}
        return meta.get(_META_GENERE_POUR) == pour
    return False


def _code_integrite(erreur: IntegrityError) -> str:
    """Code d'intégrité d'une erreur, quel que soit le pilote.

    PostgreSQL expose un ``sqlstate`` (``23503`` clé étrangère, ``23505``
    unicité) sur l'exception d'origine. Le pilote SQLite, lui, ne le fournit
    pas : on lit alors le message, ce qui garde la traduction testable sans
    base PostgreSQL. Un code inconnu rend une chaîne vide, donc aucune
traduction : l'erreur d'origine remonte intacte.
    """
    origine = getattr(erreur, "orig", None)
    sqlstate = getattr(origine, "sqlstate", None)
    if sqlstate:
        return str(sqlstate)
    message = str(origine or erreur)
    if "FOREIGN KEY constraint failed" in message:
        return "23503"
    if "UNIQUE constraint failed" in message:
        return "23505"
    if "NOT NULL constraint failed" in message:
        return "23502"
    return ""


def _est_administrateur(perimetre: Perimetre) -> bool:
    """Vrai si le compte est un administrateur (rôle, pas périmètre complet).

    Un collaborateur a un accès complet au métier **ancré** ; seul
    l'administrateur couvre en plus les documents sans ancre d'un autre
    compte (règle validée avec CARSO, 26/10).
    """
    return perimetre.role == RoleUtilisateur.ADMINISTRATEUR.value


def _verifier_unite(unite: str | None, attendue: str, nom: str) -> None:
    """Refuse une unité de lecture qui ne correspond pas au format du fichier.

    Une unité fausse (« pages » pour un DOCX) produirait une lecture
    trompeuse : mieux vaut un refus explicite qu'un texte pris au hasard.

    L'unité attendue d'un DOCX est ``elements`` (incrément 53) : paragraphes
    **et** tableaux, l'unité que ``get_document_structure`` annonce et que
    ``read_document_range`` relit.
    """
    if unite is None or unite == attendue:
        return
    msg = (
        f"Unité de lecture incompatible avec {nom} : attendu {attendue!r}, "
        f"reçu {unite!r}"
    )
    raise ValidationError(msg, details={"unite": unite, "unite_attendue": attendue})


def _date_iso(valeur: object) -> datetime | None:
    """Lit une date ISO écrite dans un tombstone ; ``None`` si absente ou illisible.

    Une valeur corrompue ne fait jamais échouer la corbeille : la fiche s'affiche
    sans échéance plutôt que de rendre la page inaccessible. Les dates sont
    normalisées en UTC — comparer une date naïve à ``datetime.now(UTC)`` lèverait
    une ``TypeError``, donc une fiche illisible en apparence.
    """
    if not isinstance(valeur, str) or not valeur:
        return None
    try:
        lue = datetime.fromisoformat(valeur)
    except ValueError:
        return None
    return lue if lue.tzinfo is not None else lue.replace(tzinfo=UTC)


def _texte_ou_none(valeur: object) -> str | None:
    """Texte d'un champ de tombstone, ou ``None`` (jamais ``"None"`` affiché)."""
    if valeur is None:
        return None
    texte = str(valeur)
    return texte or None
