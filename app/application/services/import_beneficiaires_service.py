"""Service métier — import des bénéficiaires d'une session depuis un classeur.

Lieu **unique** de la règle d'import (AGENTS.md §2.5) : la route REST et les
outils de l'agent assistant formateur appellent ce même service. Il n'existe
donc qu'une seule vérité sur « comment un XLSX devient des bénéficiaires ».

Règles conservées à l'identique depuis les outils d'agent (aucune n'a été
réécrite) :

- colonnes ``nom`` et ``prenom`` requises (comparaison insensible à la casse) ;
- une ligne sans nom **ou** sans prénom est signalée incomplète, jamais devinée ;
- la clé d'identité est le couple (nom, prénom) en minuscules ;
- **aucune déduplication silencieuse** : un doublon est signalé, jamais fusionné
  (clinrules 04). Un doublon archivé est signalé comme tel — le réactiver est
  une décision humaine, pas un effet de bord de l'import.

Le service distingue deux temps, et c'est le cœur du *human-in-the-loop* :

1. :meth:`preparer` — lit, normalise, classe chaque ligne. **Aucune écriture.**
2. :meth:`confirmer` — écrit ce que l'utilisateur a confirmé (ses corrections
   font foi), puis inscrit les personnes à la session.

Ni ``commit`` ni ``rollback`` : la transaction appartient à l'appelant
(instruction/04 §5).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.application.dto import BeneficiaireInput, ParticipationInscriptionInput
from app.application.services.beneficiaire_service import BeneficiaireService
from app.application.services.document_service import DocumentService
from app.application.services.participation_service import ParticipationService
from app.application.trace import TraceContext
from app.core.errors import NotFoundError, ValidationError
from app.documents.inspection import inspecter_xlsx, lire_xlsx_plage
from app.domain.enums import ActionAudit, ActorType, StatutBeneficiaire

_ENTITY = "beneficiaire"

#: Colonnes obligatoires du classeur, en minuscules (règle d'origine).
COLONNES_REQUISES: tuple[str, ...] = ("nom", "prenom")

#: Colonnes facultatives reconnues -> champ du bénéficiaire.
#: La première colonne trouvée pour un champ gagne (ordre du dictionnaire).
COLONNES_FACULTATIVES: dict[str, str] = {
    "contact": "contact",
    "email": "contact",
    "telephone": "contact",
    "organisation": "organisation_origine",
    "organisation_origine": "organisation_origine",
    "identifiant": "identifiant_externe",
    "identifiant_externe": "identifiant_externe",
}

MAX_LIGNES_MAX = 500


class StatutLigneImport(StrEnum):
    """Classement d'une ligne d'aperçu — vocabulaire de l'application, pas du domaine."""

    VALIDE = "valide"
    INCOMPLETE = "incomplete"
    DOUBLON = "doublon_existant"
    DOUBLON_ARCHIVE = "doublon_archive"
    DEJA_INSCRIT = "deja_dans_session"


@dataclass(frozen=True, slots=True)
class LigneImport:
    """Une ligne candidate : ce que l'utilisateur voit et peut corriger."""

    nom: str
    prenom: str
    contact: str | None = None
    organisation_origine: str | None = None
    identifiant_externe: str | None = None
    statut: str = StatutLigneImport.VALIDE.value
    message: str = ""

    def en_donnees(self) -> dict[str, Any]:
        return {
            "nom": self.nom,
            "prenom": self.prenom,
            "contact": self.contact,
            "organisation_origine": self.organisation_origine,
            "identifiant_externe": self.identifiant_externe,
            "statut": self.statut,
            "message": self.message,
        }


@dataclass(frozen=True, slots=True)
class ApercuImport:
    """Résultat de :meth:`ImportBeneficiairesService.preparer` — aucune écriture."""

    feuille: str
    colonnes: list[str]
    lignes: list[LigneImport] = field(default_factory=list)

    @property
    def nb_lignes(self) -> int:
        return len(self.lignes)

    @property
    def nb_valides(self) -> int:
        return sum(1 for ligne in self.lignes if ligne.statut == StatutLigneImport.VALIDE.value)

    def en_donnees(self) -> dict[str, Any]:
        return {
            "feuille": self.feuille,
            "colonnes": self.colonnes,
            "nb_lignes": self.nb_lignes,
            "nb_valides": self.nb_valides,
            "lignes": [ligne.en_donnees() for ligne in self.lignes],
        }


@dataclass(frozen=True, slots=True)
class ResultatImport:
    """Décompte de :meth:`ImportBeneficiairesService.confirmer`."""

    crees: int = 0
    inscrits: int = 0
    ignores: int = 0
    doublons_signales: int = 0

    def en_donnees(self) -> dict[str, Any]:
        return {
            "crees": self.crees,
            "inscrits": self.inscrits,
            "ignores": self.ignores,
            "doublons_signales": self.doublons_signales,
        }


class ImportBeneficiairesService:
    """Use cases : inspecter, preparer (aperçu), confirmer (écriture sous contrôle)."""

    def __init__(self, session: Session, actor_id: str | None = None) -> None:
        self._session = session
        self.documents = DocumentService(session, actor_id=actor_id)
        self.beneficiaires = BeneficiaireService(session, actor_id=actor_id)
        self.participations = ParticipationService(session, actor_id=actor_id)
        self._trace = TraceContext(session, actor_id=actor_id)

    # --- lecture ------------------------------------------------------------

    def inspecter(self, document_id: UUID) -> dict[str, Any]:
        """Structure du classeur : feuilles et dimensions, sans lire les données."""
        return inspecter_xlsx(self._chemin(document_id))

    def preparer(
        self,
        document_id: UUID,
        *,
        sheet: str | None = None,
        max_lignes: int = 200,
        session_id: UUID | None = None,
    ) -> ApercuImport:
        """Aperçu corrigeable — **aucune écriture**.

        ``session_id`` permet de signaler les personnes déjà inscrites à cette
        session : c'est la seule raison pour laquelle le contexte de session est
        nécessaire à l'aperçu.

        Raises:
            ValidationError: classeur vide, colonnes obligatoires absentes.
            NotFoundError: document ou session introuvable.
        """
        if max_lignes < 1 or max_lignes > MAX_LIGNES_MAX:
            raise ValidationError(
                f"Le nombre de lignes doit être compris entre 1 et {MAX_LIGNES_MAX}",
                details={"max_lignes": max_lignes},
            )
        if session_id is not None and self.participations.sessions.get(session_id) is None:
            raise NotFoundError(f"Session {session_id} introuvable")

        plage = lire_xlsx_plage(
            self._chemin(document_id), sheet=sheet, from_row=1, to_row=max_lignes
        )
        lignes_brutes: list[list[Any]] = plage["lignes"]
        if not lignes_brutes:
            raise ValidationError("Feuille vide : rien à importer")

        en_tetes = [str(c).strip().lower() for c in lignes_brutes[0]]
        absentes = [colonne for colonne in COLONNES_REQUISES if colonne not in en_tetes]
        if absentes:
            raise ValidationError(
                "Colonnes obligatoires absentes du classeur : "
                + ", ".join(f"« {colonne} »" for colonne in absentes),
                details={
                    "colonnes_trouvees": en_tetes,
                    "colonnes_requises": list(COLONNES_REQUISES),
                },
            )

        index = {nom: position for position, nom in enumerate(en_tetes)}
        connus = self._index_beneficiaires()
        deja = self._beneficiaires_de_session(session_id)

        vues: list[LigneImport] = []
        for ligne in lignes_brutes[1:]:
            nom = self._cellule(ligne, index, "nom")
            prenom = self._cellule(ligne, index, "prenom")
            facultatif = self._champs_facultatifs(ligne, index)
            if not nom or not prenom:
                vues.append(
                    LigneImport(
                        nom=nom,
                        prenom=prenom,
                        statut=StatutLigneImport.INCOMPLETE.value,
                        message="Nom et prénom sont tous deux nécessaires.",
                        **facultatif,
                    )
                )
                continue
            statut, message = self._classer(nom, prenom, connus, deja)
            vues.append(
                LigneImport(nom=nom, prenom=prenom, statut=statut, message=message, **facultatif)
            )

        return ApercuImport(
            feuille=plage["feuille"],
            colonnes=[str(c) for c in lignes_brutes[0]],
            lignes=vues,
        )

    # --- écriture -----------------------------------------------------------

    def confirmer(
        self, *, session_id: UUID | None, lignes: list[LigneImport]
    ) -> ResultatImport:
        """Enregistre les lignes **confirmées** et les inscrit à la session.

        Les lignes reçues font foi : elles portent les corrections de
        l'utilisateur. Chaque ligne est malgré tout revalidée (nom et prénom
        présents) et un doublon apparu entre-temps est signalé plutôt que
        fusionné.

        Raises:
            ValidationError: aucune ligne fournie.
            NotFoundError: session introuvable.
        """
        if not lignes:
            raise ValidationError("Aucune ligne à importer")
        if session_id is not None and self.participations.sessions.get(session_id) is None:
            raise NotFoundError(f"Session {session_id} introuvable")

        connus = self._index_beneficiaires()
        deja = self._beneficiaires_de_session(session_id)

        crees = 0
        inscrits = 0
        ignores = 0
        doublons = 0
        for ligne in lignes:
            nom = (ligne.nom or "").strip()
            prenom = (ligne.prenom or "").strip()
            if not nom or not prenom:
                ignores += 1
                continue
            cle = (nom.lower(), prenom.lower())
            identifiant, archive = connus.get(cle, (None, False))
            if identifiant is None:
                cree = self.beneficiaires.creer(
                    BeneficiaireInput(
                        nom=nom,
                        prenom=prenom,
                        contact=ligne.contact,
                        organisation_origine=ligne.organisation_origine,
                        identifiant_externe=ligne.identifiant_externe,
                    )
                )
                identifiant = cree.id
                connus[cle] = (identifiant, False)
                crees += 1
            elif archive:
                # Un doublon archivé n'est pas ressuscité en silence.
                doublons += 1
                continue
            else:
                doublons += 1

            if session_id is not None and identifiant not in deja:
                self.participations.inscrire(
                    session_id, ParticipationInscriptionInput(beneficiaire_id=identifiant)
                )
                deja.add(identifiant)
                inscrits += 1

        self._trace.record_event(
            actor_type=ActorType.HUMAIN.value,
            action=ActionAudit.BENEFICIAIRES_IMPORTES.value,
            entity_type=_ENTITY,
            entity_id=session_id,
            after={
                "crees": crees,
                "inscrits": inscrits,
                "ignores": ignores,
                "doublons_signales": doublons,
            },
            event_metadata={
                "source": "classeur",
                "session_id": str(session_id) if session_id else None,
            },
        )
        return ResultatImport(
            crees=crees, inscrits=inscrits, ignores=ignores, doublons_signales=doublons
        )

    def importer(
        self,
        document_id: UUID,
        *,
        session_id: UUID | None = None,
        sheet: str | None = None,
        max_lignes: int = MAX_LIGNES_MAX,
    ) -> ResultatImport:
        """Aperçu + confirmation en un appel — chemin des outils d'agent.

        L'outil d'agent est déjà soumis à approbation humaine (HITL) : la
        confirmation de l'utilisateur *est* le déclenchement de l'outil. La
        page, elle, utilise ``preparer`` puis ``confirmer`` séparément pour
        laisser corriger le tableau.
        """
        apercu = self.preparer(
            document_id, sheet=sheet, max_lignes=max_lignes, session_id=session_id
        )
        retenues = [
            ligne for ligne in apercu.lignes if ligne.statut == StatutLigneImport.VALIDE.value
        ]
        return self.confirmer(session_id=session_id, lignes=retenues)

    # --- internes -----------------------------------------------------------

    def _chemin(self, document_id: UUID) -> Path:
        _nom, chemin = self.documents.telecharger(document_id)
        if chemin.suffix.lower() != ".xlsx":
            raise ValidationError(
                "Le document n'est pas un classeur XLSX",
                details={"document_id": str(document_id), "suffixe": chemin.suffix},
            )
        return chemin

    def _index_beneficiaires(self) -> dict[tuple[str, str], tuple[UUID, bool]]:
        """Index (nom, prénom) → (identifiant, est archivé)."""
        return {
            (b.nom.lower().strip(), b.prenom.lower().strip()): (
                b.id,
                b.statut == StatutBeneficiaire.ARCHIVE.value,
            )
            for b in self.beneficiaires.lister()
        }

    def _beneficiaires_de_session(self, session_id: UUID | None) -> set[UUID]:
        if session_id is None:
            return set()
        return {
            p.beneficiaire_id for p in self.participations.lister_pour_session(session_id)
        }

    @staticmethod
    def _classer(
        nom: str,
        prenom: str,
        connus: dict[tuple[str, str], tuple[UUID, bool]],
        deja: set[UUID],
    ) -> tuple[str, str]:
        connue = connus.get((nom.lower(), prenom.lower()))
        if connue is None:
            return StatutLigneImport.VALIDE.value, "Nouveau bénéficiaire."
        identifiant, archive = connue
        if archive:
            return (
                StatutLigneImport.DOUBLON_ARCHIVE.value,
                "Personne déjà connue mais archivée : la réactiver est une décision.",
            )
        if identifiant in deja:
            return StatutLigneImport.DEJA_INSCRIT.value, "Déjà inscrit à cette session."
        return (
            StatutLigneImport.DOUBLON.value,
            "Personne déjà connue : elle sera inscrite, pas dupliquée.",
        )

    @staticmethod
    def _cellule(ligne: list[Any], index: dict[str, int], nom: str) -> str:
        position = index.get(nom)
        if position is None or position >= len(ligne):
            return ""
        valeur = ligne[position]
        return "" if valeur is None else str(valeur).strip()

    @staticmethod
    def _champs_facultatifs(
        ligne: list[Any], index: dict[str, int]
    ) -> dict[str, str | None]:
        champs: dict[str, str | None] = {}
        for colonne, champ in COLONNES_FACULTATIVES.items():
            if champ in champs:
                continue
            valeur = ImportBeneficiairesService._cellule(ligne, index, colonne)
            if valeur:
                champs[champ] = valeur
        return champs


__all__ = [
    "COLONNES_FACULTATIVES",
    "COLONNES_REQUISES",
    "MAX_LIGNES_MAX",
    "ApercuImport",
    "ImportBeneficiairesService",
    "LigneImport",
    "ResultatImport",
    "StatutLigneImport",
]
