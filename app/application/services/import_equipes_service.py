"""Service métier — import du vivier depuis un classeur Excel.

Lieu **unique** de la règle « un XLSX devient plusieurs personnes du vivier » :
l'outil de l'agent RH appelle ce service, une route REST pourra le faire sans
réécrire la règle (AGENTS.md §2.5).

Règles, toutes lisibles dans le classeur (aucune devinée) :

- colonnes ``nom`` et ``prenom`` obligatoires (casse et accents neutres) ;
- une ligne sans nom **ou** sans prénom est signalée incomplète, jamais devinée ;
- colonnes facultatives reconnues : ``email``, ``telephone``, ``profil``,
  ``fonction`` — toute autre colonne est ignorée, sans invention ;
- la clé d'identité d'un doublon est l'**email** s'il est renseigné, sinon le
  couple (nom, prénom) en minuscules : un doublon est **signalé**, jamais
  fusionné ni recréé (règle clinrules 04, comme l'import des bénéficiaires) ;
- le **statut** n'est pas importé : une personne créée naît ``actif`` (règle du
  service ``EquipeService.creer``) — un statut écrit dans un tableur ne fait pas
  foi sur l'état d'un compte ;
- les **CV ne sont pas importés ici** : ils se déposent sur la fiche de la
  personne, un par un (décision du 18/11).

Le service distingue l'aperçu (aucune écriture) de l'import (écriture), comme
l'import des bénéficiaires : l'humain voit ce qui serait créé avant que ça le
soit. Ni ``commit`` ni ``rollback`` — la transaction appartient à l'appelant.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.application.dto import EquipeInput
from app.application.services.document_service import DocumentService
from app.application.services.equipe_service import EquipeService
from app.core.errors import ValidationError
from app.documents.inspection import inspecter_xlsx, lire_xlsx_plage
from app.domain.execution import Equipe

#: Colonnes obligatoires du classeur (comparaison sans accents ni casse).
COLONNES_REQUISES: tuple[str, ...] = ("nom", "prenom")

#: Colonnes facultatives reconnues → champ du vivier. Le premier synonyme trouvé
#: gagne : importer deux fois la même valeur n'a pas de sens.
COLONNES_FACULTATIVES: dict[str, str] = {
    "email": "email",
    "mail": "email",
    "courriel": "email",
    "telephone": "telephone",
    "téléphone": "telephone",
    "tel": "telephone",
    "profil": "profil",
    "specialite": "profil",
    "spécialité": "profil",
    "fonction": "fonction",
}

#: Bornes de service (pas une règle métier) : au-delà, l'aperçu est illisible.
MAX_LIGNES_MAX = 500
MAX_LIGNES_APERCU = 200


class StatutLigneEquipe(StrEnum):
    """Classement d'une ligne d'aperçu — vocabulaire de l'application."""

    VALIDE = "valide"
    INCOMPLETE = "incomplete"
    DOUBLON = "doublon_existant"
    DOUBLON_FICHIER = "doublon_dans_le_fichier"


@dataclass(frozen=True, slots=True)
class LigneEquipe:
    """Une personne candidate : ce que l'utilisateur voit avant l'écriture."""

    nom: str
    prenom: str
    email: str | None = None
    telephone: str | None = None
    profil: str | None = None
    fonction: str | None = None
    statut: str = StatutLigneEquipe.VALIDE.value
    message: str = ""

    def en_donnees(self) -> dict[str, Any]:
        return {
            "nom": self.nom,
            "prenom": self.prenom,
            "email": self.email,
            "telephone": self.telephone,
            "profil": self.profil,
            "fonction": self.fonction,
            "statut": self.statut,
            "message": self.message,
        }


@dataclass(frozen=True, slots=True)
class ApercuImportEquipes:
    """Résultat de :meth:`ImportEquipesService.preparer` — aucune écriture."""

    feuille: str
    colonnes: list[str]
    lignes: list[LigneEquipe] = field(default_factory=list)

    @property
    def nb_lignes(self) -> int:
        return len(self.lignes)

    @property
    def nb_valides(self) -> int:
        return sum(
            1 for ligne in self.lignes if ligne.statut == StatutLigneEquipe.VALIDE.value
        )

    def en_donnees(self) -> dict[str, Any]:
        return {
            "feuille": self.feuille,
            "colonnes": self.colonnes,
            "nb_lignes": self.nb_lignes,
            "nb_valides": self.nb_valides,
            "lignes": [ligne.en_donnees() for ligne in self.lignes],
        }


@dataclass(frozen=True, slots=True)
class ResultatImportEquipes:
    """Décompte de :meth:`ImportEquipesService.importer`."""

    crees: int = 0
    ignores: int = 0
    doublons_signales: int = 0
    personnes: list[dict[str, str]] = field(default_factory=list)

    def en_donnees(self) -> dict[str, Any]:
        return {
            "crees": self.crees,
            "ignores": self.ignores,
            "doublons_signales": self.doublons_signales,
            "personnes": self.personnes,
        }


def _normaliser(texte: Any) -> str:
    """Texte de cellule sans espaces superflus ni casse (accents conservés)."""
    return str(texte).strip() if texte is not None else ""


def _cle_texte(texte: str) -> str:
    """Clé de comparaison d'un en-tête : minuscules, sans accents, sans espaces."""
    sans = (
        texte.replace("é", "e")
        .replace("è", "e")
        .replace("ê", "e")
        .replace("à", "a")
        .replace("ô", "o")
        .replace("î", "i")
        .replace("ï", "i")
    )
    return "".join(sans.lower().split())


class ImportEquipesService:
    """Use cases : inspecter, préparer (aperçu), importer (écriture)."""

    def __init__(self, session: Session, actor_id: str | None = None) -> None:
        self._session = session
        self.documents = DocumentService(session, actor_id=actor_id)
        self.equipes = EquipeService(session, actor_id=actor_id)

    # --- lecture ------------------------------------------------------------

    def inspecter(self, document_id: UUID) -> dict[str, Any]:
        """Structure du classeur : feuilles et dimensions, sans lire les données."""
        return inspecter_xlsx(self._chemin(document_id))

    def preparer(
        self,
        document_id: UUID,
        *,
        sheet: str | None = None,
        max_lignes: int = MAX_LIGNES_APERCU,
    ) -> ApercuImportEquipes:
        """Aperçu corrigeable — **aucune écriture**.

        Raises:
            ValidationError: classeur vide, colonnes obligatoires absentes,
                nombre de lignes hors bornes.
            NotFoundError: document introuvable ou illisible.
        """
        if max_lignes < 1 or max_lignes > MAX_LIGNES_MAX:
            raise ValidationError(
                f"Le nombre de lignes doit être compris entre 1 et {MAX_LIGNES_MAX}",
                details={"max_lignes": max_lignes},
            )
        plage = lire_xlsx_plage(
            self._chemin(document_id), sheet=sheet, from_row=1, to_row=max_lignes
        )
        lignes_brutes: list[list[Any]] = plage["lignes"]
        if not lignes_brutes:
            raise ValidationError("Feuille vide : rien à importer")

        en_tetes_brutes = [str(c) if c is not None else "" for c in lignes_brutes[0]]
        cles = [_cle_texte(entete) for entete in en_tetes_brutes]
        absentes = [colonne for colonne in COLONNES_REQUISES if colonne not in cles]
        if absentes:
            raise ValidationError(
                "Colonnes obligatoires absentes du classeur : "
                + ", ".join(f"« {colonne} »" for colonne in absentes),
                details={
                    "colonnes_trouvees": en_tetes_brutes,
                    "colonnes_requises": list(COLONNES_REQUISES),
                },
            )

        index: dict[str, int] = {}
        for position, colonne in enumerate(cles):
            if colonne and colonne not in index:
                index[colonne] = position

        connus = self._index_vivier()
        vues: list[LigneEquipe] = []
        cles_fichier: set[str] = set()
        for ligne in lignes_brutes[1:]:
            charge = self._ligne(ligne, index)
            if not charge["nom"] or not charge["prenom"]:
                vues.append(
                    LigneEquipe(
                        **charge,
                        statut=StatutLigneEquipe.INCOMPLETE.value,
                        message="Nom et prénom sont tous deux nécessaires.",
                    )
                )
                continue
            cle = self._cle_identite(charge)
            if cle in cles_fichier:
                vues.append(
                    LigneEquipe(
                        **charge,
                        statut=StatutLigneEquipe.DOUBLON_FICHIER.value,
                        message="Cette personne apparaît plusieurs fois dans le classeur.",
                    )
                )
                continue
            cles_fichier.add(cle)
            if cle in connus:
                vues.append(
                    LigneEquipe(
                        **charge,
                        statut=StatutLigneEquipe.DOUBLON.value,
                        message=(
                            "Personne déjà présente dans le vivier "
                            f"({connus[cle]}) : rien n'est créé, sa fiche reste la source."
                        ),
                    )
                )
                continue
            vues.append(LigneEquipe(**charge))

        return ApercuImportEquipes(
            feuille=plage["feuille"],
            colonnes=en_tetes_brutes,
            lignes=vues,
        )

    # --- écriture -----------------------------------------------------------

    def importer(
        self,
        document_id: UUID,
        *,
        sheet: str | None = None,
        max_lignes: int = MAX_LIGNES_MAX,
    ) -> ResultatImportEquipes:
        """Crée les personnes **valides** du classeur (les doublons sont signalés).

        Les CV ne sont pas importés : ils se déposent ensuite sur chaque fiche.
        """
        apercu = self.preparer(document_id, sheet=sheet, max_lignes=max_lignes)
        retenues = [
            ligne for ligne in apercu.lignes if ligne.statut == StatutLigneEquipe.VALIDE.value
        ]
        if not retenues:
            raise ValidationError(
                "Aucune ligne valide à importer : vérifier les colonnes nom/prénom "
                "et les doublons signalés.",
                details={
                    "nb_lignes": apercu.nb_lignes,
                    "nb_valides": apercu.nb_valides,
                },
            )

        crees: list[dict[str, str]] = []
        ignores = 0
        doublons = 0
        for ligne in retenues:
            cle = self._cle_identite(
                {
                    "nom": ligne.nom,
                    "prenom": ligne.prenom,
                    "email": ligne.email,
                }
            )
            if cle in self._index_vivier():
                # Apparu entre l'aperçu et l'écriture : signalé, jamais recréé.
                doublons += 1
                continue
            equipe = self.equipes.creer(
                EquipeInput(
                    nom=ligne.nom,
                    prenom=ligne.prenom,
                    email=ligne.email,
                    telephone=ligne.telephone,
                    profil=ligne.profil,
                    fonction=ligne.fonction,
                )
            )
            crees.append(
                {
                    "equipe_id": str(equipe.id),
                    "nom": equipe.nom,
                    "prenom": equipe.prenom,
                    "nom_complet": " ".join(
                        part for part in (equipe.prenom, equipe.nom) if (part or "").strip()
                    ),
                }
            )
        return ResultatImportEquipes(
            crees=len(crees),
            ignores=ignores,
            doublons_signales=doublons,
            personnes=crees,
        )

    # --- internes -----------------------------------------------------------

    def _chemin(self, document_id: UUID):
        """Chemin physique contrôlé du classeur (jamais un chemin fourni)."""
        _, chemin = self.documents.telecharger(document_id)
        return chemin

    def _ligne(self, ligne: list[Any], index: dict[str, int]) -> dict[str, Any]:
        """Champs reconnus d'une ligne — ce qui est absent reste vide."""
        champs: dict[str, Any] = {"nom": "", "prenom": ""}
        for cle, champ in COLONNES_FACULTATIVES.items():
            position = index.get(cle)
            if position is None or champ in champs:
                continue
            valeur = _normaliser(ligne[position] if position < len(ligne) else None)
            champs[champ] = valeur or None
        champs["nom"] = _normaliser(ligne[index["nom"]] if index["nom"] < len(ligne) else None)
        champs["prenom"] = _normaliser(
            ligne[index["prenom"]] if index["prenom"] < len(ligne) else None
        )
        return champs

    @staticmethod
    def _cle_identite(charge: dict[str, Any]) -> str:
        """Clé de doublon : email si présent, sinon (nom, prénom) sans casse."""
        email = (charge.get("email") or "").strip().lower()
        if email:
            return f"email:{email}"
        return (
            f"nom:{(charge.get('nom') or '').strip().lower()}"
            f"|{(charge.get('prenom') or '').strip().lower()}"
        )

    def _index_vivier(self) -> dict[str, str]:
        """Identités déjà enregistrées → nom affichable (email et nom/prénom)."""
        connus: dict[str, str] = {}
        lignes = self._session.execute(
            select(Equipe.nom, Equipe.prenom, Equipe.email)
        ).all()
        for nom, prenom, email in lignes:
            etiquette = " ".join(part for part in (prenom, nom) if (part or "").strip())
            if email and email.strip():
                connus.setdefault(f"email:{email.strip().lower()}", etiquette)
            connus.setdefault(
                f"nom:{(nom or '').strip().lower()}|{(prenom or '').strip().lower()}",
                etiquette,
            )
        return connus


__all__ = [
    "ApercuImportEquipes",
    "COLONNES_FACULTATIVES",
    "COLONNES_REQUISES",
    "ImportEquipesService",
    "LigneEquipe",
    "MAX_LIGNES_APERCU",
    "MAX_LIGNES_MAX",
    "ResultatImportEquipes",
    "StatutLigneEquipe",
]
