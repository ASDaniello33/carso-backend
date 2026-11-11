"""Repository du référentiel documentaire (instruction/06).

Les documents sont **versionnés** : une nouvelle version est une nouvelle ligne
(``version`` + 1), jamais une mise à jour du fichier d'une version antérieure.
Les méthodes ci-dessous permettent au service de calculer la version courante
et d'identifier les versions à archiver.

Un document **supprimé** (``supprime``, règle 23/09) garde sa ligne mais n'est
plus un document exploitable : son fichier a quitté le disque. Il est donc exclu
des listes de travail (documents d'une ancre, listes d'une organisation, etc.) et
du calcul de la version « active ». Les méthodes de listing sans filtre restent
la seule porte vers ces fiches conservées (page Documents, filtre « Supprimés »),
et ``list_versions`` garde l'historique lisible.
"""

import uuid
from typing import Any

from sqlalchemy import func, or_, select

from app.domain.document import Document
from app.domain.enums import StatutDocument
from app.domain.execution import ModeleDocument, SessionFormation
from app.infrastructure.repositories.base import BaseRepository

#: Un document approuvé puis archivé reste lisible, mais un document supprimé
#: n'est plus ni « actif » (find_active_by_name) ni listé dans les fiches métier.
_STATUTS_INEXPLOITABLES = (
    StatutDocument.ARCHIVED.value,
    StatutDocument.SUPPRIME.value,
)


class DocumentRepository(BaseRepository[Document]):
    model = Document

    def find_active_by_name(self, nom: str, type_document: str) -> Document | None:
        """Document exploitable portant ce nom et ce type (recherche de version).

        ``archived`` et ``supprime`` sont exclus : le premier est rangé, le second a
        perdu son fichier — ni l'un ni l'autre n'est une version de travail.
        """
        stmt = select(Document).where(
            Document.nom == nom,
            Document.type_document == type_document,
            Document.statut.notin_(_STATUTS_INEXPLOITABLES),
        )
        return self.session.scalars(stmt).first()

    def find_version(
        self, nom: str, type_document: str, version: int
    ) -> Document | None:
        """Version précise d'un document nommé (unicité logique nom/type/version).

        Une fiche supprimée ne bloque pas la réutilisation du nom : redéposer un
        fichier du même nom crée une nouvelle version 1, l'ancienne restant
        consultable et marquée « Supprimé ».
        """
        stmt = select(Document).where(
            Document.nom == nom,
            Document.type_document == type_document,
            Document.version == version,
            Document.statut != StatutDocument.SUPPRIME.value,
        )
        return self.session.scalars(stmt).first()

    def current_version(self, nom: str, type_document: str) -> int:
        """Numéro de version le plus élevé pour ce nom et ce type (0 si aucun)."""
        stmt = select(func.max(Document.version)).where(
            Document.nom == nom,
            Document.type_document == type_document,
        )
        return int(self.session.scalar(stmt) or 0)

    def list_versions(self, nom: str, type_document: str) -> list[Document]:
        """Historique des versions, de la plus récente à la plus ancienne."""
        stmt = (
            select(Document)
            .where(Document.nom == nom, Document.type_document == type_document)
            .order_by(Document.version.desc())
        )
        return list(self.session.scalars(stmt))

    def list_all(self, *, statut: str | None = None) -> list[Document]:
        """Tous les documents, du plus récent au plus ancien (usage UI de gestion).

        Filtre facultatif par statut (``draft``/``proposed``/``approved``/
        ``archived``/``supprime``). Sans filtre, les fiches **supprimées** sont
        écartées : elles restent accessibles en demandant explicitement leur statut,
        jamais noyées dans la liste courante. Tables modestes : pas de pagination
        côté base pour l'instant, la page UI borne l'affichage.
        """
        stmt = select(Document).order_by(Document.updated_at.desc())
        if statut is not None:
            stmt = stmt.where(Document.statut == statut)
        else:
            stmt = stmt.where(Document.statut != StatutDocument.SUPPRIME.value)
        return list(self.session.scalars(stmt))

    def list_supprimes(self) -> list[Document]:
        """Fiches en corbeille (``supprime``), de la plus récemment supprimée.

        Porte d'entrée de la corbeille (ADR 0006) : c'est la **seule** lecture
        qui retourne les fiches supprimées sans qu'un statut soit demandé, parce
        que la corbeille n'est faite que de celles-là. L'ordre suit ``updated_at``
        (mis à jour à la suppression) plutôt que ``created_at`` : la corbeille se
        lit du geste le plus récent au plus ancien.
        """
        stmt = (
            select(Document)
            .where(Document.statut == StatutDocument.SUPPRIME.value)
            .order_by(Document.updated_at.desc())
        )
        return list(self.session.scalars(stmt))

    def list_approved(self, nom: str, type_document: str) -> list[Document]:
        """Versions actuellement officielles pour ce nom et ce type."""
        stmt = select(Document).where(
            Document.nom == nom,
            Document.type_document == type_document,
            Document.statut == StatutDocument.APPROVED.value,
        )
        return list(self.session.scalars(stmt))

    def _documents_actifs(self, colonne: Any, valeur: uuid.UUID) -> list[Document]:
        """Documents exploitables portés par une ancre métier (supprimés exclus).

        Un document supprimé a perdu son fichier : le laisser dans la fiche métier
        proposerait un téléchargement mort. Les fiches conservées restent visibles
        dans la page Documents, sous le filtre « Supprimés ».
        """
        stmt = select(Document).where(
            colonne == valeur,
            Document.statut != StatutDocument.SUPPRIME.value,
        )
        return list(self.session.scalars(stmt))

    def list_personnels_de(self, proprietaire_id: uuid.UUID) -> list[Document]:
        """Documents **personnels** d'un compte : sans objet métier et à lui.

        Deux conditions, et une seule définition pour toute l'application :

        1. **aucune ancre** — organisation, appel, offre, mission, intervenant,
           session. Un document rattaché à un objet métier appartient à cet
           objet, jamais au dossier privé de qui l'a déposé.
        2. **le compte en est propriétaire** — un document sans ancre d'un
           collègue n'est pas le mien, et l'administration, qui *voit* tout, ne
           doit pas voir « mon dossier » peuplé des dossiers des autres.

        Le rangement de stockage ne décide pas : un fichier peut rester sous
        ``missions/<id>/`` après la suppression de son ancre, et rester le
        document personnel de son auteur. Les six ancres font foi, pas le chemin.
        """
        stmt = (
            select(Document)
            .where(
                Document.proprietaire_id == proprietaire_id,
                Document.statut != StatutDocument.SUPPRIME.value,
                Document.organisation_id.is_(None),
                Document.appel_id.is_(None),
                Document.offre_id.is_(None),
                Document.mission_id.is_(None),
                Document.equipe_id.is_(None),
                Document.session_id.is_(None),
            )
            .order_by(Document.created_at.desc())
        )
        return list(self.session.scalars(stmt))

    def list_by_checksum(self, checksum_sha256: str) -> list[Document]:
        """Documents partageant ce contenu (détection de doublon, contrôle)."""
        stmt = select(Document).where(Document.checksum_sha256 == checksum_sha256)
        return list(self.session.scalars(stmt))

    def list_for_mission(self, mission_id: uuid.UUID) -> list[Document]:
        """Pièces de la mission **et de sa session** (appartenance partagée).

        La mission a une session (``uq_sessions_mission_id``). Une pièce déposée
        depuis la session appartient aussi à la mission : les deux ancres sont
        lues ensemble, en une requête, sans doublon — le document n'est qu'une
        ligne, il ne peut pas apparaître deux fois.
        """
        session_de_mission = select(SessionFormation.id).where(
            SessionFormation.mission_id == mission_id
        )
        stmt = (
            select(Document)
            .where(
                or_(
                    Document.mission_id == mission_id,
                    Document.session_id.in_(session_de_mission),
                ),
                Document.statut != StatutDocument.SUPPRIME.value,
            )
            .order_by(Document.created_at.desc())
        )
        return list(self.session.scalars(stmt))

    def list_for_appel(self, appel_id: uuid.UUID) -> list[Document]:
        return self._documents_actifs(Document.appel_id, appel_id)

    def list_for_offre(self, offre_id: uuid.UUID) -> list[Document]:
        return self._documents_actifs(Document.offre_id, offre_id)

    def list_for_organisation(self, organisation_id: uuid.UUID) -> list[Document]:
        return self._documents_actifs(Document.organisation_id, organisation_id)

    def list_for_equipe(self, equipe_id: uuid.UUID) -> list[Document]:
        return self._documents_actifs(Document.equipe_id, equipe_id)

    def list_for_session(self, session_id: uuid.UUID) -> list[Document]:
        """Pièces de la session **et de sa mission** (appartenance partagée).

        Sens inverse du listing de mission : une pièce déposée sur la mission
        (fiche technique, liste des bénéficiaires) sert aussi à la session. La
        mission est résolue en base depuis la session, jamais devinée par
        l'appelant — un identifiant de session ne dit pas la mission au client.
        """
        mission_de_session = select(SessionFormation.mission_id).where(
            SessionFormation.id == session_id
        )
        stmt = (
            select(Document)
            .where(
                or_(
                    Document.session_id == session_id,
                    Document.mission_id.in_(mission_de_session),
                ),
                Document.statut != StatutDocument.SUPPRIME.value,
            )
            .order_by(Document.created_at.desc())
        )
        return list(self.session.scalars(stmt))


class ModeleDocumentRepository(BaseRepository[ModeleDocument]):
    model = ModeleDocument

    def list_for_organisation(self, organisation_id: uuid.UUID) -> list[ModeleDocument]:
        stmt = select(ModeleDocument).where(
            ModeleDocument.organisation_id == organisation_id
        )
        return list(self.session.scalars(stmt))

    def list_for_type(
        self, organisation_id: uuid.UUID, type_document: str
    ) -> list[ModeleDocument]:
        """Modèles d'une organisation pour un type de document, versions décroissantes."""
        stmt = (
            select(ModeleDocument)
            .where(
                ModeleDocument.organisation_id == organisation_id,
                ModeleDocument.type_document == type_document,
            )
            .order_by(ModeleDocument.version.desc())
        )
        return list(self.session.scalars(stmt))

    def find_active(self, organisation_id: uuid.UUID, type_document: str) -> ModeleDocument | None:
        """Modèle de plus haute version pour ce couple (organisation, type).

        Un modèle dont le template a été supprimé est écarté : son fichier n'existe
        plus, le retenir produirait une génération impossible. Le modèle valide
        précédent reprend alors la main ; s'il n'en existe aucun, la résolution
        échoue explicitement (jamais de repli silencieux).
        """
        stmt = (
            select(ModeleDocument)
            .join(Document, ModeleDocument.document_template_id == Document.id)
            .where(
                ModeleDocument.organisation_id == organisation_id,
                ModeleDocument.type_document == type_document,
                Document.statut != StatutDocument.SUPPRIME.value,
            )
            .order_by(ModeleDocument.version.desc())
        )
        return self.session.scalars(stmt).first()

    def list_for_template(self, document_template_id: uuid.UUID) -> list[ModeleDocument]:
        """Modèles de documents qui utilisent ce document comme gabarit.

        Sert à la purge d'une fiche (ADR 0006) : un modèle **survit** à la
        suppression de son gabarit (règle 23/09) mais **bloque** sa purge — il ne
        faut jamais détruire une configuration cliente par effet de bord. La FK
        ``RESTRICT`` dirait la même chose par une erreur SQL : ici, l'appelant
        reçoit la liste et peut l'expliquer à l'humain.
        """
        stmt = select(ModeleDocument).where(
            ModeleDocument.document_template_id == document_template_id
        )
        return list(self.session.scalars(stmt))

    def current_version(self, organisation_id: uuid.UUID, type_document: str) -> int:
        """Version la plus élevée enregistrée pour ce couple (0 si aucun modèle)."""
        stmt = select(func.max(ModeleDocument.version)).where(
            ModeleDocument.organisation_id == organisation_id,
            ModeleDocument.type_document == type_document,
        )
        return int(self.session.scalar(stmt) or 0)
