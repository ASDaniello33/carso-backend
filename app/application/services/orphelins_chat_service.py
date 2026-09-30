"""Purge **validée** des fichiers orphelins du scope ``chat`` (incrément 34).

Pourquoi : le téléversement des pièces (messages, annonces) précède la
publication. Une pièce retirée du formulaire avant publication, ou une annonce
supprimée, laisse des octets sur le disque sans aucune ligne qui les référence.
Ces **orphelins** n'apparaissent nulle part et s'accumulent en silence.

Règles (AGENTS.md §2.2, §7 — jamais une destruction silencieuse) :

- la **détection** est une lecture pure : tout fichier sous
  ``{storage_root}/chat/`` dont le chemin logique n'est référencé par aucune
  colonne métier (``messages.piece_chemin``, ``annonces.piece_chemin``,
  ``annonces_pieces.chemin``, ``utilisateurs.photo_profil_chemin``) est un
  orphelin **candidat** ;
- la **suppression** est un geste d'administration : réservé aux
  administrateurs (couche API), motif obligatoire, liste de chemins **choisie
  par l'humain** (jamais « tout purger » en un clic), chaque chemin revalidé au
  moment de l'exécution (référencé entre-temps → ignoré, pas d'erreur) ;
- chaque chemin doit rester **confiné** au scope ``chat`` : validation du
  préfixe + résolution ``resolve_within_root`` (anti path-traversal) ;
- le geste est tracé dans l'``AuditEvent`` : inventaire ``before`` de ce qui a
  quitté le disque, motif, nombre purgé, ignorés — la seule trace conservée.

Les messages et annonces en soft-delete conservent leurs fichiers : la ligne
existe encore, ses octets restent référencés (réversibilité du soft-delete).
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.application.trace import Decision, TraceContext
from app.core.config import get_settings
from app.core.errors import ValidationError
from app.documents.paths import resolve_within_root
from app.documents.storage import DocumentStorage, LocalDocumentStorage
from app.domain.enums import ActionAudit, ActorType
from app.domain.identity import Utilisateur
from app.domain.social import Annonce, Message, PieceAnnonce

#: Préfixe unique autorisé : la purge ne peut jamais toucher un autre scope.
_PREFIXE_CHAT = "chat/"


@dataclass(frozen=True, slots=True)
class OrphelinChat:
    """Un fichier candidat à la purge (métadonnées affichées à l'admin)."""

    chemin: str
    nom: str
    taille_octets: int


@dataclass(frozen=True, slots=True)
class RapportPurgeOrphelins:
    """Résultat d'une purge validée (rendu tel quel par l'API)."""

    purges: int
    ignores: list[str]


class OrphelinsChatService:
    """Détection et purge validée des orphelins du stockage social."""

    def __init__(
        self,
        session: Session,
        *,
        storage: DocumentStorage | None = None,
        actor_id: str | None = None,
    ) -> None:
        reglages = get_settings()
        self._session = session
        self.storage: DocumentStorage = storage or LocalDocumentStorage(
            reglages.storage_root, max_bytes=reglages.max_upload_bytes
        )
        self._actor_id = actor_id

    # --- détection --------------------------------------------------------

    def lister_orphelins(self) -> list[OrphelinChat]:
        """Fichiers du scope ``chat`` non référencés par une ligne métier.

        Lecture pure : aucun fichier n'est modifié. Un fichier référencé par un
        message supprimé (soft-delete) n'est **pas** orphelin — sa ligne existe.
        """
        references = self._chemins_references()
        racine = self.storage.root / _PREFIXE_CHAT
        if not racine.is_dir():
            return []

        orphelins: list[OrphelinChat] = []
        for fichier in sorted(racine.rglob("*")):
            if not fichier.is_file():
                continue
            chemin = fichier.relative_to(self.storage.root).as_posix()
            if chemin in references:
                continue
            orphelins.append(
                OrphelinChat(
                    chemin=chemin,
                    nom=fichier.name,
                    taille_octets=fichier.stat().st_size,
                )
            )
        return orphelins

    # --- purge ------------------------------------------------------------

    def purger_orphelins(self, decision: Decision, chemins: list[str]) -> RapportPurgeOrphelins:
        """Supprime les orphelins **choisis** par l'administrateur.

        Args:
            decision: décision humaine (``decided_by`` vient du JWT côté API,
                ``reason`` est obligatoire — jamais une purge muette).
            chemins: chemins logiques **choisis dans la liste des candidats** —
                jamais « tout » : la sélection explicite reste la règle.

        Raises:
            ValidationError: motif vide, liste vide, ou chemin hors scope
                ``chat`` / non résoluble sous la racine de stockage.
        """
        if not (decision.reason or "").strip():
            raise ValidationError("Un motif est obligatoire pour purger les orphelins")
        if not chemins:
            raise ValidationError("Aucun fichier sélectionné pour la purge")

        # Confinement : tout chemin doit rester dans le scope chat. La liste
        # vient de l'API (admin), mais la règle est revalidée ici — la couche
        # de service est la garde-fou, pas la route.
        for chemin in chemins:
            if not chemin.startswith(_PREFIXE_CHAT):
                raise ValidationError(
                    f"Chemin hors scope chat refusé : {chemin}",
                    details={"prefixe_autorise": _PREFIXE_CHAT},
                )

        references = self._chemins_references()
        inventaire: dict[str, object] = {}
        purges = 0
        ignores: list[str] = []
        for chemin in chemins:
            # Revalidation **au moment de l'exécution** : une pièce publiée
            # entre le listing et la purge n'est plus orpheline — ignorée.
            if chemin in references:
                ignores.append(chemin)
                continue
            try:
                resolve_within_root(self.storage.root, chemin)
            except ValidationError:
                ignores.append(chemin)
                continue
            if not self.storage.exists(chemin):
                ignores.append(chemin)
                continue
            self.storage.remove(chemin)
            inventaire[chemin] = "purge"
            purges += 1

        if purges > 0:
            TraceContext(self._session, actor_id=self._actor_id).record_event(
                actor_type=ActorType.HUMAIN.value,
                action=ActionAudit.CHAT_ORPHELINS_PURGES.value,
                entity_type="chat",
                entity_id=None,
                before={"fichiers": inventaire},
                event_metadata={
                    "decided_by": decision.decided_by,
                    "reason": decision.reason,
                    "purges": purges,
                    "ignores": ignores,
                },
            )
        return RapportPurgeOrphelins(purges=purges, ignores=ignores)

    # --- internes ---------------------------------------------------------

    def _chemins_references(self) -> set[str]:
        """Chemins logiques référencés par une ligne (message, annonce, pièce,
        avatar de compte — incrément 26, sous ``chat/{id}/profil-*``)."""
        return {
            *self._session.scalars(
                select(Message.piece_chemin).where(Message.piece_chemin.is_not(None))
            ),
            *self._session.scalars(
                select(Annonce.piece_chemin).where(Annonce.piece_chemin.is_not(None))
            ),
            *self._session.scalars(select(PieceAnnonce.chemin)),
            # Photos de profil (scope chat) : référencées par la ligne compte,
            # servies par /annuaire/{id}/photo-profil — jamais orphelines.
            *self._session.scalars(
                select(Utilisateur.photo_profil_chemin).where(
                    Utilisateur.photo_profil_chemin.is_not(None)
                )
            ),
        }


__all__ = [
    "OrphelinChat",
    "OrphelinsChatService",
    "RapportPurgeOrphelins",
]
