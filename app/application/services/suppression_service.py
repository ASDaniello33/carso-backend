"""Suppression réelle cascadée — orchestration explicite des dépendances.

Décision confirmée avec CARSO (22/09) : supprimer un élément emporte ses
dépendances. **Exception validée le 23/09** : les documents ne sont jamais
détruits. Leur ancre est détachée, leur fiche passe en ``supprime`` et leur
fichier est retiré du disque (la règle vit dans ``DocumentService``, pas ici).
Une pièce justificative survit à son fichier : c'est ce qui rend la suppression
vérifiable après coup. Neuf contraintes ``RESTRICT`` du schéma interdisent au
moteur de faire la cascade seul : elle est donc **explicite, ordonnée et tracée
ici**, en un seul endroit.

Ce que ce module garantit :

- ``impact`` ne modifie rien : il dénombre ce qui **disparaîtrait** et ce qui
  serait **conservé**, pour que l'interface demande une confirmation éclairée
  plutôt qu'un clic à l'aveugle ;
- ``supprimer`` **avertit** si un document officiel figure parmi les
  dépendances (liste dans ``documents_officiels``) mais n'empêche jamais la
  suppression : l'utilisateur confirme en connaissance de cause ;
- un ``audit_event`` conserve l'inventaire complet (ce qui est détruit, ce qui
  est conservé, les fichiers retirés) avec auteur et motif ;
- les fichiers sont retirés par la couche storage (``LocalDocumentStorage.remove``)
  à travers ``DocumentService`` : jamais par un chemin libre.

Le SQL de collecte vit ici, et nulle part ailleurs : c'est la contrepartie assumée
d'une cascade sur neuf tables (une méthode de repository par arête serait du bruit
sans gain de sécurité).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from sqlalchemy import delete, or_, select, update
from sqlalchemy.orm import Session

from app.application.dto import DecisionInput
from app.application.trace import Decision, TraceContext
from app.core.config import get_settings
from app.core.errors import NotFoundError, ValidationError
from app.documents.previews import chemin_apercu
from app.domain.document import Document
from app.domain.enums import ActionAudit, ActorType, StatutDocument
from app.domain.execution import (
    AffectationEquipe,
    Beneficiaire,
    Equipe,
    Mission,
    MissionOffre,
    ModeleDocument,
    Participation,
    SessionFormation,
    SupportFormation,
)
from app.domain.organization import (
    AppelAProposition,
    Budget,
    LigneBudget,
    Lot,
    Offre,
    Organisation,
)

_ENTITY = "suppression"

#: Vocabulaire unique de l'inventaire (clés plurielles, utilisées partout).
ORGANISATIONS = "organisations"
APPELS = "appels"
LOTS = "lots"
OFFRES = "offres"
MISSIONS = "missions"
SESSIONS = "sessions"
BENEFICIAIRES = "beneficiaires"
EQUIPES = "equipes"
DOCUMENTS = "documents"
BUDGETS = "budgets"
LIGNES_BUDGET = "lignes_budget"
SUPPORTS = "supports"
AFFECTATIONS = "affectations"
PARTICIPATIONS = "participations"
LIENS_MISSION_OFFRE = "liens_mission_offre"
MODELES = "modeles"

#: Types supprimables depuis l'API (racines de cascade).
TYPES_SUPPRIMABLES: frozenset[str] = frozenset(
    {
        ORGANISATIONS,
        APPELS,
        LOTS,
        OFFRES,
        MISSIONS,
        SESSIONS,
        BENEFICIAIRES,
        EQUIPES,
        DOCUMENTS,
    }
)

#: Modèle ORM de chaque racine + libellé singulier (messages et audit).
_RACINES: dict[str, tuple[Any, str]] = {
    ORGANISATIONS: (Organisation, "organisation"),
    APPELS: (AppelAProposition, "appel à proposition"),
    LOTS: (Lot, "lot"),
    OFFRES: (Offre, "offre"),
    MISSIONS: (Mission, "mission"),
    SESSIONS: (SessionFormation, "session"),
    BENEFICIAIRES: (Beneficiaire, "bénéficiaire"),
    EQUIPES: (Equipe, "équipe"),
    DOCUMENTS: (Document, "document"),
}

#: Colonne d'ancrage d'un ``Document`` par type d'entité.
_ANCRAGES: dict[str, Any] = {
    ORGANISATIONS: Document.organisation_id,
    APPELS: Document.appel_a_proposition_id,
    OFFRES: Document.offre_id,
    MISSIONS: Document.mission_id,
    EQUIPES: Document.equipe_id,
    SESSIONS: Document.session_id,
}

#: Ordre de suppression : feuilles d'abord (les ``RESTRICT`` du schéma sont
#: franchis dans cet ordre, jamais par le moteur). ``DOCUMENTS`` n'y figure pas :
#: les documents ne sont jamais détruits (règle 23/09) — ils sont détachés et
#: passés en ``supprime`` **avant** la suppression de leur ancre.
_ORDRE: tuple[str, ...] = (
    LIGNES_BUDGET,
    SUPPORTS,
    AFFECTATIONS,
    PARTICIPATIONS,
    MODELES,
    BUDGETS,
    SESSIONS,
    LIENS_MISSION_OFFRE,
    MISSIONS,
    OFFRES,
    LOTS,
    APPELS,
)

#: Table ORM de chaque catégorie de l'inventaire (pour la suppression de masse).
_TABLES: dict[str, Any] = {
    LIGNES_BUDGET: LigneBudget,
    SUPPORTS: SupportFormation,
    AFFECTATIONS: AffectationEquipe,
    PARTICIPATIONS: Participation,
    MODELES: ModeleDocument,
    BUDGETS: Budget,
    SESSIONS: SessionFormation,
    LIENS_MISSION_OFFRE: MissionOffre,
    MISSIONS: Mission,
    OFFRES: Offre,
    LOTS: Lot,
    APPELS: AppelAProposition,
    ORGANISATIONS: Organisation,
    BENEFICIAIRES: Beneficiaire,
    EQUIPES: Equipe,
}


@dataclass
class Inventaire:
    """Identifiants concernés, par catégorie (l'ordre des clés n'importe pas)."""

    par_type: dict[str, set[UUID]] = field(default_factory=dict)

    def ajouter(self, categorie: str, identifiants: set[UUID]) -> set[UUID]:
        """Ajoute des identifiants et renvoie **ceux qui sont nouveaux**."""
        connus = self.par_type.setdefault(categorie, set())
        nouveaux = identifiants - connus
        connus.update(nouveaux)
        return nouveaux

    def ids(self, categorie: str) -> set[UUID]:
        return self.par_type.get(categorie, set())

    def compte(self, categorie: str) -> int:
        return len(self.par_type.get(categorie, ()))

    def dependances(self) -> dict[str, int]:
        """Comptage lisible (catégories non vides, ordre stable)."""
        return {
            categorie: len(identifiants)
            for categorie, identifiants in sorted(self.par_type.items())
            if identifiants
        }


@dataclass(frozen=True, slots=True)
class ImpactSuppression:
    """Ce qui disparaîtrait : dénombrement, blocages éventuels, rien de plus."""

    type_entite: str
    libelle: str
    identifiant: UUID
    reference: str | None
    dependances: dict[str, int]
    conservees: dict[str, int]
    documents_officiels: tuple[str, ...]
    fichiers: int
    bloquant: bool
    message: str | None


@dataclass(frozen=True, slots=True)
class RapportSuppression:
    """Ce qui a été supprimé (l'inventaire est aussi dans l'``AuditEvent``)."""

    type_entite: str
    libelle: str
    identifiant: UUID
    reference: str | None
    dependances: dict[str, int]
    conservees: dict[str, int]
    fichiers_supprimes: int


class ServiceSuppression:
    """Cascade explicite : impact, garde-fous, exécution ordonnée, audit.

    Le service ne commit pas : la transaction appartient à l'appelant
    (instruction/04 §5), comme tous les services applicatifs.
    """

    def __init__(
        self,
        session: Session,
        *,
        storage: Any | None = None,
        actor_id: str | None = None,
    ) -> None:
        self._session = session
        self._actor_id = actor_id
        self._trace = TraceContext(session, actor_id=actor_id)
        self._reglages = get_settings()
        # Un stockage explicite évite tout accès disque implicite (tests,
        # déploiements à racine non montée) — même convention que
        # ``DocumentService``.
        self._storage: Any = storage

    # --- lecture : impact ------------------------------------------------------

    def impact(self, type_entite: str, identifiant: UUID) -> ImpactSuppression:
        """Dénombre les dépendances d'une suppression, sans rien modifier.

        Raises:
            ValidationError: type d'entité non supprimable.
            NotFoundError: entité inexistante.
        """
        modele, libelle = self._modele(type_entite)
        racine = self._charger(modele, libelle, identifiant)
        inventaire = self._collecter(type_entite, identifiant)
        officiels = self._documents_officiels(inventaire)
        conservees = self._conservees(inventaire)
        fichiers = self._compter_fichiers(inventaire)
        return ImpactSuppression(
            type_entite=type_entite,
            libelle=libelle,
            identifiant=identifiant,
            reference=self._reference(racine),
            dependances=self._dependances(inventaire),
            conservees=conservees,
            documents_officiels=officiels,
            fichiers=fichiers,
            bloquant=False,
            message=self._message(officiels, conservees),
        )

    # --- écriture : exécution --------------------------------------------------

    def supprimer(
        self, type_entite: str, identifiant: UUID, decision: Decision
    ) -> RapportSuppression:
        """Supprime l'entité et tout son sous-arbre, dans l'ordre des contraintes.

        Raises:
            ValidationError: type d'entité non supprimable.
            NotFoundError: entité inexistante.
        """
        modele, libelle = self._modele(type_entite)
        racine = self._charger(modele, libelle, identifiant)
        reference = self._reference(racine)

        inventaire = self._collecter(type_entite, identifiant)
        officiels = self._documents_officiels(inventaire)
        conservees = self._conservees(inventaire)

        # Les documents partent **avant** leur ancre : détachés, marqués supprimés,
        # fichiers retirés. Après la suppression du parent, la FK en cascade aurait
        # déjà emporté leur fiche.
        supprimes = self._conserver_documents(
            inventaire.ids(DOCUMENTS),
            DecisionInput(decided_by=decision.decided_by, reason=decision.reason),
        )
        self._executer(type_entite, identifiant, inventaire)

        dependances = self._dependances(inventaire)
        self._trace.record_event(
            actor_type=ActorType.HUMAIN.value,
            action=ActionAudit.SUPPRESSION_EFFECTUEE.value,
            entity_type=type_entite,
            entity_id=str(identifiant),
            after={
                "reference": reference,
                "dependances": dependances,
                "conservees": conservees,
                "fichiers_supprimes": supprimes,
                "documents_officiels": list(officiels),
            },
            event_metadata={
                "decided_by": decision.decided_by,
                "reason": decision.reason,
            },
        )
        return RapportSuppression(
            type_entite=type_entite,
            libelle=libelle,
            identifiant=identifiant,
            reference=reference,
            dependances=dependances,
            conservees=conservees,
            fichiers_supprimes=supprimes,
        )

    # --- collecte --------------------------------------------------------------

    def _collecter(self, type_entite: str, identifiant: UUID) -> Inventaire:
        """Descend le graphe des dépendances depuis la racine (sans cycle)."""
        inventaire = Inventaire()
        inventaire.ajouter(type_entite, {identifiant})
        a_parcourir: list[tuple[str, UUID]] = [(type_entite, identifiant)]

        while a_parcourir:
            parent, identifiant_parent = a_parcourir.pop()
            for categorie, identifiants in self._enfants(parent, {identifiant_parent}).items():
                for nouveau in inventaire.ajouter(categorie, identifiants):
                    a_parcourir.append((categorie, nouveau))

        # Les documents s'ancrent sur n'importe quelle entité de l'inventaire : ils
        # sont collectés pour être **conservés** (détachés, puis passés en
        # ``supprime``), jamais pour être détruits (règle validée 23/09).
        inventaire.ajouter(DOCUMENTS, self._documents_ancres(inventaire))
        # Un modèle qui référence un document comme template est un **dépendant** de
        # ce document : il survit à sa suppression et n'entre donc pas dans
        # l'inventaire. Sa résolution d'actif l'écarte simplement (repository).
        # Table d'association mission ↔ offre (aucun ORM associé ici).
        inventaire.ajouter(LIENS_MISSION_OFFRE, self._liens_mission_offre(inventaire))
        return inventaire

    def _enfants(self, parent: str, identifiants: set[UUID]) -> dict[str, set[UUID]]:
        """Dépendances directes d'un type d'entité."""
        if not identifiants:
            return {}
        if parent == ORGANISATIONS:
            return {
                APPELS: self._colonnes(
                    AppelAProposition.id, AppelAProposition.organisation_id, identifiants
                ),
                MISSIONS: self._colonnes(Mission.id, Mission.organisation_id, identifiants),
                MODELES: self._colonnes(
                    ModeleDocument.id, ModeleDocument.organisation_id, identifiants
                ),
            }
        if parent == APPELS:
            return {
                LOTS: self._colonnes(Lot.id, Lot.appel_a_proposition_id, identifiants),
            }
        if parent == LOTS:
            return {
                OFFRES: self._colonnes(Offre.id, Offre.lot_id, identifiants),
            }
        if parent == OFFRES:
            return {
                BUDGETS: self._colonnes(Budget.id, Budget.offre_id, identifiants),
                MISSIONS: self._missions_pour_offres(identifiants),
            }
        if parent == MISSIONS:
            return {
                SESSIONS: self._colonnes(
                    SessionFormation.id, SessionFormation.mission_id, identifiants
                ),
                AFFECTATIONS: self._colonnes(
                    AffectationEquipe.id, AffectationEquipe.mission_id, identifiants
                ),
                SUPPORTS: self._colonnes(
                    SupportFormation.id, SupportFormation.mission_id, identifiants
                ),
                BUDGETS: self._colonnes(Budget.id, Budget.mission_id, identifiants),
            }
        if parent == SESSIONS:
            return {
                PARTICIPATIONS: self._colonnes(
                    Participation.id, Participation.session_id, identifiants
                ),
            }
        if parent == BENEFICIAIRES:
            return {
                PARTICIPATIONS: self._colonnes(
                    Participation.id, Participation.beneficiaire_id, identifiants
                ),
            }
        if parent == EQUIPES:
            return {
                AFFECTATIONS: self._colonnes(
                    AffectationEquipe.id, AffectationEquipe.equipe_id, identifiants
                ),
                SUPPORTS: self._colonnes(
                    SupportFormation.id, SupportFormation.equipe_id, identifiants
                ),
            }
        if parent == BUDGETS:
            return {
                LIGNES_BUDGET: self._colonnes(
                    LigneBudget.id, LigneBudget.budget_id, identifiants
                ),
            }
        # Un document n'a pas de dépendants détruits : les modèles qui l'utilisent
        # comme template sont conservés (règle 23/09).
        return {}

    def _documents_ancres(self, inventaire: Inventaire) -> set[UUID]:
        """Documents rattachés à l'une des entités de l'inventaire."""
        trouves: set[UUID] = set()
        for categorie, colonne in _ANCRAGES.items():
            identifiants = inventaire.ids(categorie)
            if identifiants:
                trouves |= self._colonnes(Document.id, colonne, identifiants)
        return trouves

    def _missions_pour_offres(self, identifiants: set[UUID]) -> set[UUID]:
        """Missions rattachées à ces offres (association N-N ``mission_offres``)."""
        return self._colonnes(MissionOffre.mission_id, MissionOffre.offre_id, identifiants)

    def _liens_mission_offre(self, inventaire: Inventaire) -> set[UUID]:
        """Identifiants des liens mission ↔ offre concernés."""
        conditions = []
        missions = inventaire.ids(MISSIONS)
        offres = inventaire.ids(OFFRES)
        if missions:
            conditions.append(MissionOffre.mission_id.in_(missions))
        if offres:
            conditions.append(MissionOffre.offre_id.in_(offres))
        if not conditions:
            return set()
        return set(self._session.scalars(select(MissionOffre.id).where(or_(*conditions))))

    # --- exécution -------------------------------------------------------------

    def _executer(
        self, type_entite: str, identifiant: UUID, inventaire: Inventaire
    ) -> None:
        """Supprime dans l'ordre des contraintes, la racine en dernier.

        Les ``DELETE`` sont en masse (aucun chargement d'objet : rien à cascader
        côté ORM, l'ordre est déjà explicite). Les documents, eux, ont déjà été
        traités — détachés et passés en ``supprime`` avant cet appel.
        """
        for categorie in _ORDRE:
            identifiants = inventaire.ids(categorie)
            if identifiants:
                self._supprimer_categorie(categorie, identifiants)

        # Racine : ``documents`` est conservé, jamais supprimé (règle 23/09).
        if type_entite != DOCUMENTS:
            modele, _libelle = self._modele(type_entite)
            self._session.execute(delete(modele).where(modele.id == identifiant))

    def _supprimer_categorie(self, categorie: str, identifiants: set[UUID]) -> None:
        table = _TABLES[categorie]
        self._session.execute(delete(table).where(table.id.in_(identifiants)))

    # --- fichiers --------------------------------------------------------------

    def _chemins_fichiers(self, inventaire: Inventaire) -> list[str]:
        """Chemins logiques des documents **et** des aperçus dérivés."""
        identifiants = inventaire.ids(DOCUMENTS)
        if not identifiants:
            return []
        chemins = list(
            self._session.scalars(
                select(Document.storage_path).where(Document.id.in_(identifiants))
            )
        )
        apercus: list[str] = []
        for chemin in chemins:
            # ``chemin_apercu`` est la source de vérité du nommage des aperçus
            # (jamais un format recomposé ailleurs) ; la boucle est bornée par
            # le plafond de rendu, donc le nombre d'aperçus possible.
            for page in range(1, self._reglages.previews_max_pages + 1):
                apercus.append(chemin_apercu(chemin, page))
        return [*chemins, *apercus]

    def _compter_fichiers(self, inventaire: Inventaire) -> int:
        """Nombre de fichiers existants réellement concernés (aperçus compris)."""
        return sum(
            1 for chemin in self._chemins_fichiers(inventaire) if self.storage.exists(chemin)
        )

    # --- documents (conservation) ----------------------------------------------

    def _conserver_documents(self, identifiants: set[UUID], decision: DecisionInput) -> int:
        """Détache, marque supprimés et retire les fichiers des documents visés.

        La règle de suppression logique vit dans ``DocumentService`` : ce service ne
        la réimplémente pas, il l'orchestre sur tout un inventaire. Une fiche **déjà**
        supprimée n'est pas retraitée (elle n'a plus de fichier, et sa suppression est
        déjà tracée) — elle est seulement détachée de son ancre.

        Returns:
            Nombre de fichiers retirés du stockage (fichiers et aperçus).
        """
        if not identifiants:
            return 0
        statuts = dict(
            self._session.execute(
                select(Document.id, Document.statut).where(Document.id.in_(identifiants))
            ).all()
        )
        deja_supprimes = {
            document_id
            for document_id, statut in statuts.items()
            if statut == StatutDocument.SUPPRIME.value
        }
        # Le détachement précède toute suppression d'ancre : sans lui, la FK
        # ``ON DELETE CASCADE`` emporterait la fiche avec son parent.
        for document_id in sorted(identifiants, key=str):
            self._detacher(document_id)

        # Import local : la règle documentaire est appelée ici, mais importer le
        # module au chargement créerait un cycle avec l'assemblage des services.
        from app.application.services.document_service import DocumentService

        service = DocumentService(
            self._session, storage=self.storage, actor_id=self._actor_id
        )
        retires = 0
        for document_id in sorted(identifiants - deja_supprimes, key=str):
            retires += service.supprimer_document(document_id, decision)
        return retires

    def _detacher(self, document_id: UUID) -> None:
        """Retire les ancres métier d'un document (son parent va disparaître).

        Le chemin de stockage reste écrit : il dit d'où le fichier a été retiré,
        sans jamais redevenir une adresse exploitable.
        """
        self._session.execute(
            update(Document)
            .where(Document.id == document_id)
            .values({colonne.key: None for colonne in _ANCRAGES.values()})
        )

    def _modeles_pour_documents(self, identifiants: set[UUID]) -> int:
        """Nombre de modèles de documents utilisant un de ces documents comme template."""
        if not identifiants:
            return 0
        return len(
            self._colonnes(
                ModeleDocument.id, ModeleDocument.document_template_id, identifiants
            )
        )

    def _dependances(self, inventaire: Inventaire) -> dict[str, int]:
        """Ce qui sera réellement détruit (les fiches documentaires survivent)."""
        return {
            categorie: nombre
            for categorie, nombre in inventaire.dependances().items()
            if categorie != DOCUMENTS
        }

    def _conservees(self, inventaire: Inventaire) -> dict[str, int]:
        """Ce qui survit à la suppression : fiches documentaires et leurs modèles."""
        conservees: dict[str, int] = {}
        documents = inventaire.compte(DOCUMENTS)
        if documents:
            conservees[DOCUMENTS] = documents
        modeles = self._modeles_pour_documents(inventaire.ids(DOCUMENTS))
        if modeles:
            conservees[MODELES] = modeles
        return conservees

    @staticmethod
    def _message(officiels: tuple[str, ...], conservees: dict[str, int]) -> str | None:
        """Phrase rendue par l'interface : ce qui est officiel, ce qui est conservé."""
        morceaux: list[str] = []
        if officiels:
            morceaux.append("Documents officiels concernés : " + ", ".join(officiels) + ".")
        if conservees.get(DOCUMENTS):
            morceaux.append(
                f"{conservees[DOCUMENTS]} document(s) conservé(s) : fiche en statut "
                "« Supprimé », fichier retiré du stockage."
            )
        return " ".join(morceaux) or None

    # --- internes --------------------------------------------------------------

    @property
    def storage(self) -> Any:
        """Stockage documentaire (résolu paresseusement, comme ``DocumentService``)."""
        if self._storage is None:
            from app.documents.storage import LocalDocumentStorage

            self._storage = LocalDocumentStorage(
                self._reglages.storage_root, max_bytes=self._reglages.max_upload_bytes
            )
        return self._storage

    def _modele(self, type_entite: str) -> tuple[Any, str]:
        entree = _RACINES.get(type_entite)
        if entree is None:
            raise ValidationError(
                f"Type d'entité non supprimable : {type_entite!r}",
                details={"types_supprimables": sorted(TYPES_SUPPRIMABLES)},
            )
        return entree

    def _charger(self, modele: Any, libelle: str, identifiant: UUID) -> Any:
        entite = self._session.get(modele, identifiant)
        if entite is None:
            raise NotFoundError(
                f"{libelle.capitalize()} {identifiant} introuvable",
                details={"type_entite": modele.__tablename__, "identifiant": str(identifiant)},
            )
        return entite

    def _colonnes(self, cle: Any, colonne: Any, identifiants: set[UUID]) -> set[UUID]:
        """Identifiants dont ``colonne`` appartient à ``identifiants``."""
        if not identifiants:
            return set()
        return set(self._session.scalars(select(cle).where(colonne.in_(identifiants))))

    def _documents_officiels(self, inventaire: Inventaire) -> tuple[str, ...]:
        """Noms des documents ``approved`` concernés (blocage de la suppression)."""
        identifiants = inventaire.ids(DOCUMENTS)
        if not identifiants:
            return ()
        noms = self._session.scalars(
            select(Document.nom).where(
                Document.id.in_(identifiants),
                Document.statut == StatutDocument.APPROVED.value,
            )
        )
        return tuple(sorted({str(nom) for nom in noms}))

    @staticmethod
    def _reference(entite: Any) -> str | None:
        """Libellé humain d'une entité (référence, nom, titre ou numéro)."""
        for attribut in ("reference", "nom", "titre", "numero", "theme"):
            valeur = getattr(entite, attribut, None)
            if valeur:
                return str(valeur)
        return None


__all__ = [
    "ImpactSuppression",
    "RapportSuppression",
    "ServiceSuppression",
    "TYPES_SUPPRIMABLES",
]
