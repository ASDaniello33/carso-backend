"""Contrats internes des services applicatifs (Phase 4).

Dataclasses typées, sans dépendance ORM ni Pydantic : les schémas API
(Create/Read/Proposal/Approval, instruction/09 §5) viendront à la couche API
et traduiront ces contrats — jamais l'inverse.
"""

from dataclasses import dataclass, field
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any, BinaryIO
from uuid import UUID


@dataclass(frozen=True, slots=True)
class LigneBudgetInput:
    """Saisie d'une ligne budgétaire. ``cout_total`` n'est JAMAIS fourni :
    il est recalculé côté serveur (règle 7 — déterministe)."""

    categorie: str
    quantite: Decimal
    cout_unitaire: Decimal
    description: str | None = None
    unite: str | None = None


@dataclass(frozen=True, slots=True)
class BudgetTotal:
    """Total déterministe d'un budget, recalculé à partir des lignes."""

    budget_id: UUID
    devise: str
    total: Decimal
    nb_lignes: int


@dataclass(frozen=True, slots=True)
class DocumentUploadInput:
    """Dépôt d'un fichier documentaire.

    ``proposed_by_agent`` place le document en zone *proposal* (``proposed``) et
    l'attribue à un agent : aucune validation humaine n'est contournée par un
    dépôt, quel qu'en soit l'auteur (règle 6 [C]).

    Les ancres métier : **au plus une**. Si elle existe en base, elle détermine
    le dossier. Si elle est absente ou introuvable, le document est rangé sous
    ``generated/{id}/`` (type ``non_classe``) — un document sans objet métier
    reste un document. Plusieurs ancres restent refusées.
    """

    type_document: str
    nom: str
    stream: BinaryIO
    created_by: str | None = None
    proposed_by_agent: str | None = None
    organisation_id: UUID | None = None
    appel_a_proposition_id: UUID | None = None
    offre_id: UUID | None = None
    mission_id: UUID | None = None
    equipe_id: UUID | None = None
    session_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class DocumentAnchor:
    """Ancre métier résolue : objet de rattachement + dossier de stockage."""

    scope: str
    champ: str
    entity_id: UUID


@dataclass(frozen=True, slots=True)
class FicheCorbeille:
    """Fiche documentaire supprimée, telle que la corbeille l'expose (ADR 0006).

    Ce que la suppression a conservé, c'est la **fiche** : référence, version,
    auteur, motif et date. Le fichier, lui, a quitté le stockage — c'est pourquoi
    ``fichier_present`` dit s'il faut le redéposer pour restaurer, et
    ``blocage`` explique, quand il est renseigné, pourquoi la restauration est
    refusée (version réutilisée, version officielle déjà en place, trace
    incomplète).
    """

    document_id: UUID
    nom: str
    type_document: str
    version: int
    #: Statut que la fiche reprendra si elle est restaurée (écrit au tombstone).
    statut_avant: str
    supprime_le: datetime | None
    supprime_par: str | None
    motif: str | None
    expire_le: datetime | None
    expiree: bool
    jours_restants: int | None
    #: Durée de conservation appliquée à cette fiche (jours).
    retention_jours: int
    fichier_present: bool
    restaurable: bool
    blocage: str | None
    #: Ancres métier conservées : la corbeille se filtre par fiche métier (les
    #: documents supprimés d'une offre) sans rejoindre une seconde source.
    organisation_id: UUID | None = None
    appel_a_proposition_id: UUID | None = None
    offre_id: UUID | None = None
    mission_id: UUID | None = None
    equipe_id: UUID | None = None
    session_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class ExtractedDocument:
    """Résultat d'une extraction documentaire (lecture seule, rien n'est persisté)."""

    document_id: UUID
    nom: str
    extension: str
    mime_type: str | None
    texte: str
    adaptateur: str
    nb_caracteres: int
    nb_pages: int | None
    nb_feuilles: int | None
    feuilles: tuple[str, ...]
    tronque: bool


@dataclass(frozen=True, slots=True)
class LigneLecturePlage:
    """Une **plage** lue dans un document (lecture seule, rien n'est persisté).

    Vocabulaire unique quel que soit le format : ``unite`` dit ce que
    ``debut``/``fin`` désignent (``pages`` pour un PDF, ``paragraphes`` pour un
    DOCX, ``lignes`` pour un XLSX) et ``total`` permet à l'appelant de savoir
    combien de lectures restent à faire.
    """

    document_id: UUID
    nom: str
    unite: str
    debut: int
    fin: int
    total: int | None
    texte: str
    tronque: bool

    @property
    def reste(self) -> int:
        """Éléments non lus après cette plage (0 = document lu jusqu'au bout)."""
        if self.total is None:
            return 0
        return max(0, self.total - self.fin)


@dataclass(frozen=True, slots=True)
class PageApercuDocument:
    """Une page d'un document rendue en image (aperçu visuel).

    ``chemin_logique`` est relatif au stockage : le chemin physique n'est jamais
    exposé, la page est servie par la route d'aperçu du document.
    """

    numero: int
    chemin_logique: str
    largeur_px: int
    hauteur_px: int
    #: ``False`` lorsque l'image existait déjà (aperçu réutilisé, non réécrit).
    cree: bool = False


@dataclass(frozen=True, slots=True)
class ApercuDocument:
    """Aperçu visuel d'un document : ses pages rendues en images inspectables.

    ``tronque`` signale qu'il reste des pages non rendues (plafond de volume) :
    l'agent sait alors que son inspection porte sur une partie du document.
    """

    document_id: UUID
    nom: str
    nb_pages: int
    dpi: int
    pages: tuple[PageApercuDocument, ...]
    tronque: bool = False


@dataclass(frozen=True, slots=True)
class DecisionInput:
    """Décision humaine sur une proposition (human-in-the-loop)."""

    decided_by: str
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class AffectationProposee:
    """Proposition d'affectation (agent ou humain). Le service la crée en
    statut ``proposee`` : la source ``ai_proposal`` ne change PAS la règle —
    l'approbation humaine reste obligatoire (règle 6 [C])."""

    mission_id: UUID
    equipe_id: UUID
    role_dans_mission: str
    proposed_by: str
    source: str = field(default="manual")  # manual | ai_proposal
    date_debut: date | None = None
    date_fin: date | None = None


@dataclass(frozen=True, slots=True)
class MissionDepuisOffresInput:
    """Création d'une mission issue d'offres approuvées (règle 5 [C]).

    Un appel à proposition se répond par une offre **technique** et une offre
    **financière** du même lot : la mission les référence toutes les deux
    (relation N-N). Toutes les offres doivent être approuvées et appartenir au
    même lot. La référence est exigée à la saisie : elle est unique
    (``uq_missions_reference``) et vérifiée par le service avant écriture.
    """

    offre_ids: tuple[UUID, ...]
    reference: str
    titre: str
    description: str | None = None
    date_debut: date | None = None
    date_fin: date | None = None
    #: Lieu d'exécution — donnée distincte (entité ``Lieu``).
    lieu_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class MissionDepuisAppelInput:
    """Création d'une mission depuis **un appel et son lot** (chemin UI unique).

    Le lot est celui de l'appel : l'organisation est **déduite de l'appel**
    (jamais fournie par l'appelant), et les offres **approuvées du lot** sont
    référencées. Ce chemin remplace la création « depuis des offres » dans
    l'interface : l'utilisateur part de l'appel, l'appel porte les offres et
    l'organisation — on ne lui redemande pas ce que le système sait déjà.
    """

    appel_a_proposition_id: UUID
    lot_id: UUID
    reference: str
    titre: str
    description: str | None = None
    date_debut: date | None = None
    date_fin: date | None = None
    lieu_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class MissionDirecteInput:
    """Saisie d'une prestation directe sans offre (aucune offre rattachée)."""

    organisation_id: UUID
    reference: str
    titre: str
    description: str | None = None
    date_debut: date | None = None
    date_fin: date | None = None
    lieu_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class LieuInput:
    """Création d'un lieu d'exécution (donnée distincte d'une mission)."""

    nom: str
    adresse: str | None = None
    ville: str | None = None
    pays: str | None = None
    zone: str | None = None


@dataclass(frozen=True, slots=True)
class LieuUpdateInput:
    """Modification partielle d'un lieu."""

    nom: str | None = None
    adresse: str | None = None
    ville: str | None = None
    pays: str | None = None
    zone: str | None = None


@dataclass(frozen=True, slots=True)
class SupportFormationInput:
    """Rattachement d'un support de formation à un formateur d'une mission.

    Le document existe déjà (``Document``) : le support ne crée jamais un second
    système documentaire, il porte la relation Formateur ↔ Mission ↔ Document.
    """

    mission_id: UUID
    equipe_id: UUID
    document_id: UUID
    libelle: str | None = None


@dataclass(frozen=True, slots=True)
class SessionPlanificationInput:
    """Saisie de planification d'une session rattachée à une mission existante."""

    mission_id: UUID
    theme: str | None = None
    date_debut: date | None = None
    date_fin: date | None = None
    lieu: str | None = None


# --- Lot C1 — CRUD des entités sans service (instruction/02 §A/§D/§G) ----------


@dataclass(frozen=True, slots=True)
class OrganisationInput:
    """Création d'une organisation cliente (instruction/02 §A)."""

    nom: str
    type: str | None = None
    adresse: str | None = None
    email: str | None = None
    telephone: str | None = None
    statut: str | None = None


@dataclass(frozen=True, slots=True)
class OrganisationUpdateInput:
    """Modification partielle d'une organisation — seuls les champs fournis
    (non ``None``) sont appliqués."""

    nom: str | None = None
    type: str | None = None
    adresse: str | None = None
    email: str | None = None
    telephone: str | None = None
    statut: str | None = None


@dataclass(frozen=True, slots=True)
class EquipeInput:
    """Création d'une personne du vivier (instruction/02 §D). Le CV n'est pas
    une colonne : il est rattaché via ``DocumentService`` (instruction/04).
    ``role_compte`` : rôle attribué au futur utilisateur lié (incrément 24)."""

    nom: str
    prenom: str
    email: str | None = None
    telephone: str | None = None
    profil: str | None = None
    statut: str | None = None
    role_compte: str | None = None


@dataclass(frozen=True, slots=True)
class EquipeUpdateInput:
    """Modification partielle d'une personne du vivier."""

    nom: str | None = None
    prenom: str | None = None
    email: str | None = None
    telephone: str | None = None
    profil: str | None = None
    statut: str | None = None
    role_compte: str | None = None


@dataclass(frozen=True, slots=True)
class AppelAPropositionInput:
    """Enregistrement humain d'un appel reçu (instruction/02 §B).

    Aucun champ extrait par IA : la référence, le titre et l'organisation
    cliente sont saisis. Le document source se rattache ensuite via
    ``DocumentService`` (ancre ``appel_a_proposition_id``).

    ``type`` : nature de l'appel (appel à proposition ou appel à manifestation
    d'intérêt). ``None`` retombe sur ``APPEL_A_PROPOSITION`` — jamais deviné
    depuis un autre champ.
    """

    organisation_id: UUID
    reference: str
    titre: str
    description: str | None = None
    date_reception: date | None = None
    date_limite: date | None = None
    type: str | None = None


@dataclass(frozen=True, slots=True)
class LotInput:
    """Création d'un lot rattaché à un appel à proposition existant (instruction/02 §B)."""

    appel_a_proposition_id: UUID
    numero: str
    titre: str
    zone: str | None = None
    objectifs: str | None = None
    resultats_attendus: str | None = None
    mission_description: str | None = None
    partenariat: str | None = None
    date_fin: date | None = None
    donnees_source: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class LotUpdateInput:
    """Modification partielle d'un lot (``appel_a_proposition_id`` jamais modifiable)."""

    numero: str | None = None
    titre: str | None = None
    zone: str | None = None
    objectifs: str | None = None
    resultats_attendus: str | None = None
    mission_description: str | None = None
    partenariat: str | None = None
    date_fin: date | None = None
    donnees_source: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class BeneficiaireInput:
    """Création d'un bénéficiaire. Données sensibles minimisées [? Q9] : les
    champs au-delà du minimum confirmé ne sont pas saisis ici."""

    nom: str
    prenom: str
    contact: str | None = None
    organisation_origine: str | None = None
    identifiant_externe: str | None = None


@dataclass(frozen=True, slots=True)
class BeneficiaireUpdateInput:
    """Modification partielle d'un bénéficiaire."""

    nom: str | None = None
    prenom: str | None = None
    contact: str | None = None
    organisation_origine: str | None = None
    identifiant_externe: str | None = None


@dataclass(frozen=True, slots=True)
class ParticipationInscriptionInput:
    """Inscription d'un bénéficiaire à une session (unique par couple [C])."""

    beneficiaire_id: UUID


@dataclass(frozen=True, slots=True)
class PresencePointageInput:
    """Pointage d'**une personne pour une date** (règle confirmée CARSO, 24/09).

    Le statut reprend le vocabulaire validé le 20/09 (``StatutPresence``) ; la
    colonne reste un texte (vocabulaire encore [?] côté CARSO), c'est donc le
    service qui refuse une valeur hors du référentiel. ``date`` est la date du
    pointage, obligatoirement dans la plage de la session ou de sa mission.
    """

    date: date
    presence: str
    heure_arrivee: time | None = None
    heure_depart: time | None = None


@dataclass(frozen=True, slots=True)
class PresenceLotEntree:
    """Une ligne de pointage d'un envoi en lot, pour la date de l'envoi."""

    participation_id: UUID
    presence: str
    heure_arrivee: time | None = None
    heure_depart: time | None = None


@dataclass(frozen=True, slots=True)
class ParticipationUpdateInput:
    """Évaluation/observations d'une participation (résultats [P]).

    Ce n'est pas le pointage : la présence et les heures vivent dans
    ``Presence``, une ligne par date, depuis le 24/09.
    """

    evaluation: str | None = None
    observations: str | None = None


# --- Modification humaine (règle confirmée CARSO, 22/09) ----------------------
# Ces entrées servent le CRUD d'interface. Elles restent partielles : un champ
# absent (``None``) n'est jamais appliqué, un champ fourni à ``None`` non plus
# (les services les distinguent par sentinelle quand la mise à zéro est permise).


@dataclass(frozen=True, slots=True)
class OffreUpdateInput:
    """Modification d'une offre : identité (titre, type, échéances).

    Le lot et l'appel ne bougent pas — une offre répond à ``Appel + Lot`` ;
    changer de lot n'est pas une modification, c'est une autre offre.
    """

    titre: str | None = None
    type: str | None = None
    date_debut_prevue: date | None = None
    date_fin_prevue: date | None = None


@dataclass(frozen=True, slots=True)
class SessionUpdateInput:
    """Modification d'une session (la mission, elle, ne change jamais)."""

    theme: str | None = None
    date_debut: date | None = None
    date_fin: date | None = None
    lieu: str | None = None


@dataclass(frozen=True, slots=True)
class AppelUpdateInput:
    """Modification d'un appel reçu (jamais une donnée extraite par IA)."""

    reference: str | None = None
    titre: str | None = None
    description: str | None = None
    type: str | None = None
    date_reception: date | None = None
    date_limite: date | None = None
    organisation_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class DocumentMetadonneesUpdateInput:
    """Métadonnées d'un document — jamais son contenu.

    Le contenu d'un document passe par ``remplacer_document`` (nouvelle version),
    jamais par une modification en place : une version officielle est immuable.
    """

    nom: str | None = None
    type_document: str | None = None
    doc_metadata: dict[str, Any] | None = None
