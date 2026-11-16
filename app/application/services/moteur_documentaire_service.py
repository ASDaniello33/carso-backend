"""Service métier — moteur documentaire des modèles d'organisation (INC-128).

```text
ModeleDocument (versionné)
  ├── analyse          (DocumentAnalyzer, stockée)
  ├── configuration    (proposition LLM revue, puis validée par un humain)
  └── document_instrumente_id  (copie technique ancrée, un Document)
```

Trois garanties portées ici, et non par les routes :

1. **isolation** : un modèle est toujours cherché **sous son organisation**.
   Un identifiant appartenant à une autre organisation répond « introuvable »,
   jamais « interdit » — l'existence d'un modèle d'un tiers n'est pas révélée ;
2. **validation humaine** : `generer` refuse un modèle dont la configuration
   n'est pas `validee` ; ré-analyser ou modifier la configuration remet le
   statut à `a_valider` et détache la copie technique devenue périmée ;
3. **aucun fichier non vérifié** : le livrable passe par `DocumentValidator`
   avant d'être déposé. S'il est invalide, rien n'est déposé.

Le fichier d'origine n'est jamais réécrit : ni l'analyse, ni l'instrumentation,
ni la génération ne le modifient.
"""

from __future__ import annotations

import io
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.application.dto import DocumentUploadInput
from app.application.services.document_service import DocumentService
from app.core.errors import GenerationError, NotFoundError, ValidationError
from app.documents.moteur import analyse as analyse_docx
from app.documents.moteur import edition as edition_docx
from app.documents.moteur import instrumentation as instrumentation_docx
from app.documents.moteur import paquet as paquet_docx
from app.documents.moteur import rendu as rendu_docx
from app.documents.moteur import schemas as S
from app.documents.moteur import validation as validation_docx
from app.documents.moteur.comprehension import TemplateUnderstandingService
from app.domain.document import Document
from app.domain.execution import ModeleDocument
from app.domain.organization import Offre
from app.infrastructure.repositories import ModeleDocumentRepository
from app.infrastructure.repositories.mission import MissionRepository
from app.infrastructure.repositories.offre import OffreRepository
from app.infrastructure.repositories.organisation import OrganisationRepository
from app.infrastructure.repositories.session import SessionRepository

__all__ = ["ANCRES_MODELE", "MoteurDocumentaireService"]

_STATUT_BROUILLON = "brouillon"
_STATUT_A_VALIDER = "a_valider"
_STATUT_VALIDEE = "validee"

#: Types de modèle candidats, dans l'ordre de recherche. Le registre accepte
#: l'union des vocabulaires : ``modele_offre_technique`` (registre INC-116) et
#: ``offre_technique`` (type de document) désignent le même gabarit.
_TYPE_MODELE_GENERIQUE = "offre"

#: Ancres qui reçoivent une copie de modèle : l'offre (générateur d'offre), la
#: mission et la session (assistant formateur). Une copie sans objet métier n'a
#: ni dossier ni périmètre d'accès : les autres ancres sont refusées.
ANCRES_MODELE = ("offre", "mission", "session")

#: Types de dépôt qui désignent le document d'une **offre** (les seuls qu'un
#: modèle d'offre couvre). Un rapport ou une lettre rattachés à l'offre ne sont
#: pas ce document et gardent la génération libre.
_TYPES_DOCUMENT_OFFRE = frozenset(
    {"offre", "offre_technique", "offre_financiere", "offre_formation"}
)

#: Type de **dépôt** du document de travail, par ancre. Une offre garde son type
#: documentaire ; une mission/session reçoit un document « autre » (le type réel
#: se choisit avec ``type_document`` — fiche technique, rapport…).
_TYPE_DEPOT_DEFAUT = {"offre": "offre", "mission": "autre", "session": "autre"}

#: Clé étrangère du document, par ancre.
_CHAMP_ANCRE = {"offre": "offre_id", "mission": "mission_id", "session": "session_id"}

#: Filtre de listing documentaire, par ancre (``DocumentService.lister_pour``).
_ANCRE_LISTER = {
    "offre": "offre_id",
    "mission": "mission_id",
    "session": "session_id",
}


@dataclass(frozen=True)
class _Sujet:
    """Objet métier qui reçoit la copie d'un modèle.

    Regroupe ce dont la chaîne a besoin quel que soit l'objet : son ancre, son
    identifiant, son organisation (qui possède les modèles) et une référence
    lisible pour nommer la copie et les messages.
    """

    ancre: str
    id: UUID
    org_id: UUID
    organisation_nom: str
    reference: str
    titre: str
    objet: Any = field(repr=False, default=None)

    @property
    def organisation_id(self) -> UUID:
        """Organisation propriétaire des modèles appliqués à cet objet."""
        return self.org_id

    @property
    def libelle(self) -> str:
        """Désignation lisible (type d'objet + référence)."""
        return f"{self.ancre} {self.reference}"


class MoteurDocumentaireService:
    """Cas d'usage : registre des modèles, copie dans une offre, édition de la copie."""

    def __init__(
        self,
        session: Session,
        *,
        document_service: DocumentService | None = None,
        actor_id: str | None = None,
        chat_model: Any | None = None,
    ) -> None:
        self._session = session
        self._modeles = ModeleDocumentRepository(session)
        self._documents = document_service or DocumentService(session, actor_id=actor_id)
        self._actor_id = actor_id
        self._chat_model = chat_model

    # --- lecture ------------------------------------------------------------

    def obtenir_modele(self, organisation_id: UUID, modele_id: UUID) -> ModeleDocument:
        """Modèle de **cette** organisation.

        Raises:
            NotFoundError: identifiant inconnu ou appartenant à une autre
                organisation (même réponse : rien n'est révélé).
        """
        modele = self._modeles.get(modele_id)
        if modele is None or modele.organisation_id != organisation_id:
            raise NotFoundError(
                f"Modèle de document {modele_id} introuvable pour cette organisation"
            )
        return modele

    def analyse_de(self, modele: ModeleDocument) -> S.AnalyseModele:
        """Analyse stockée, ou erreur explicite si le modèle n'a pas été analysé.

        Raises:
            ValidationError: aucune analyse enregistrée.
        """
        if not modele.analyse:
            raise ValidationError(
                "Ce modèle n'a pas encore été analysé : lancer l'analyse avant "
                "de le configurer."
            )
        return S.AnalyseModele.model_validate(modele.analyse)

    def configuration_de(self, modele: ModeleDocument) -> S.ConfigurationModele:
        """Configuration stockée (``brouillon`` si aucune n'existe encore)."""
        if not modele.configuration:
            return S.ConfigurationModele(nom=modele.nom, type_document=modele.type_document)
        return S.ConfigurationModele.model_validate(modele.configuration)

    # --- analyse ------------------------------------------------------------

    def analyser(
        self,
        organisation_id: UUID,
        modele_id: UUID,
        *,
        proposer: bool = False,
    ) -> ModeleDocument:
        """Analyse le DOCX du modèle et, si demandé, propose une classification.

        Ré-analyser **remet la validation à zéro** : une analyse différente
        invaliderait silencieusement les ancres de la copie technique.

        Raises:
            ValidationError: modèle non DOCX, illisible, ou proposition LLM
                inexploitable.
            NotFoundError: modèle hors de cette organisation.
        """
        modele = self.obtenir_modele(organisation_id, modele_id)
        octets = self._octets_modele(modele)
        analyse = analyse_docx.analyser(octets)

        modele.analyse = analyse.model_dump(mode="json")
        modele.statut_analyse = "analyse"
        modele.statut_validation = _STATUT_BROUILLON
        modele.document_instrumente_id = None
        modele.schema_generation = None

        if proposer:
            proposition = TemplateUnderstandingService(self._chat_model).proposer(
                analyse, nom=modele.nom, type_document=modele.type_document
            )
            configuration = S.ConfigurationModele(
                nom=modele.nom,
                type_document=modele.type_document,
                statut=_STATUT_BROUILLON,
                champs=[
                    S.ChampModele(**champ.model_dump()) for champ in proposition.champs
                ],
            )
            modele.configuration = configuration.model_dump(mode="json")
            modele.statut_validation = _STATUT_A_VALIDER
        self._session.flush()
        return modele


    # --- configuration ------------------------------------------------------

    def enregistrer_configuration(
        self,
        organisation_id: UUID,
        modele_id: UUID,
        configuration: S.ConfigurationModele,
    ) -> ModeleDocument:
        """Enregistre une configuration **non validée** (brouillon).

        Une configuration modifiée après validation détache la copie technique :
        les ancres posées ne correspondent plus au nouveau découpage. L'ancien
        document instrumenté reste dans le référentiel documentaire, intact.

        Raises:
            ValidationError: modèle non analysé, ou ``block_id`` inconnu.
            NotFoundError: modèle hors de cette organisation.
        """
        modele = self.obtenir_modele(organisation_id, modele_id)
        analyse = self.analyse_de(modele)
        self._verifier_blocs(analyse, configuration)

        brouillon = configuration.model_copy(update={"statut": _STATUT_BROUILLON})
        modele.configuration = brouillon.model_dump(mode="json")
        modele.statut_validation = _STATUT_A_VALIDER
        modele.document_instrumente_id = None
        modele.schema_generation = None
        self._session.flush()
        return modele

    def valider(
        self, organisation_id: UUID, modele_id: UUID
    ) -> tuple[ModeleDocument, S.RapportValidation]:
        """Valide la configuration : instrumente le modèle et contrôle la copie.

        Raises:
            ValidationError: configuration vide, instrumentation impossible
                (fusion verticale, tag dupliqué…), ou copie instrumentée
                invalide — dans ce cas **rien** n'est rattaché au modèle.
        """
        modele = self.obtenir_modele(organisation_id, modele_id)
        analyse = self.analyse_de(modele)
        configuration = self.configuration_de(modele)
        if not configuration.champs:
            raise ValidationError(
                "Configuration vide : aucun champ à ancrer. Classer au moins une "
                "zone dynamique avant de valider le modèle."
            )
        self._verifier_blocs(analyse, configuration)
        validee = configuration.model_copy(update={"statut": _STATUT_VALIDEE})

        octets = self._octets_modele(modele)
        copie = instrumentation_docx.instrumenter(octets, analyse, validee)
        rapport = validation_docx.DocumentValidator().valider_instrumente(
            copie.octets, validee
        )
        if not rapport.ok:
            raise ValidationError(
                "La copie instrumentée est invalide : " + " ; ".join(rapport.erreurs),
                details={"rapport": rapport.model_dump()},
            )

        # Nom technique **sans accents ni tirets typographiques** : le nom
        # d'affichage du modèle est libre (français), le nom de fichier non
        # (``sanitize_filename`` refuse ces caractères).
        document = self._documents.deposer_livrable(
            DocumentUploadInput(
                type_document="template",
                nom=(
                    f"modele-instrumente-v{modele.version}-"
                    f"{str(modele.id)[:8]}.docx"
                ),
                stream=io.BytesIO(copie.octets),
                created_by=self._actor_id,
                organisation_id=modele.organisation_id,
                doc_metadata={
                    "modele_document_id": str(modele.id),
                    "version_modele": modele.version,
                    "role": "modele_instrumente",
                },
            )
        )
        modele.configuration = validee.model_dump(mode="json")
        modele.document_instrumente_id = document.id
        modele.statut_validation = _STATUT_VALIDEE
        modele.schema_generation = {
            "modele_document_id": str(modele.id),
            "version_modele": modele.version,
            "champs": [
                {
                    "cle": champ.field_key,
                    "type": champ.type,
                    "obligatoire": champ.obligatoire,
                    "max_length": champ.max_length,
                    "colonnes": champ.colonnes,
                    "instruction": champ.instruction,
                }
                for champ in validee.champs
            ],
        }
        self._session.flush()
        return modele, rapport


    # --- génération ---------------------------------------------------------

    def generer(
        self,
        organisation_id: UUID,
        modele_id: UUID,
        valeurs: dict[str, Any],
        *,
        nom_fichier: str,
        type_document: str,
        propose_par: str | None = None,
        appel_id: UUID | None = None,
        offre_id: UUID | None = None,
    ) -> tuple[Any, rendu_docx.Rendu, S.RapportValidation]:
        """Génère un livrable depuis le modèle validé, puis le dépose.

        Args:
            valeurs: valeurs par clé de champ (texte, segments, lignes).
            nom_fichier: nom logique du livrable.
            type_document: type de **dépôt** du livrable (vocabulaire documentaire).
            propose_par: agent auteur ; le document est alors ``proposed``.
            appel_id / offre_id: ancre métier du livrable, une seule à la fois.

        Returns:
            ``(document déposé, rendu, rapport de validation)``.

        Raises:
            ValidationError: modèle non validé, données incomplètes, ou fichier
                instrumenté introuvable.
            GenerationError: livrable invalide — **rien n'est déposé**.
        """
        modele = self.obtenir_modele(organisation_id, modele_id)
        if modele.statut_validation != _STATUT_VALIDEE or modele.document_instrumente_id is None:
            raise ValidationError(
                "Modèle non validé : une génération part uniquement d'une "
                "configuration validée (et de sa copie instrumentée)."
            )
        configuration = self.configuration_de(modele)
        analyse = self.analyse_de(modele)
        _nom, chemin = self._documents.telecharger(modele.document_instrumente_id)

        rendu = rendu_docx.rendre(
            chemin.read_bytes(), configuration, valeurs, analyse=analyse
        )
        rapport = validation_docx.valider_livrable(
            rendu.octets,
            modele=self._octets_modele(modele),
            parties_modifiees=rendu.parties,
        )
        if not rapport.ok:
            raise GenerationError(
                "Le livrable produit est invalide : " + " ; ".join(rapport.erreurs),
                details={"rapport": rapport.model_dump()},
            )

        # Une seule ancre à la fois : l'organisation n'est transmise que si
        # aucun objet métier n'ancre déjà le livrable.
        organisation_ancre = (
            None if (appel_id or offre_id) else modele.organisation_id
        )
        document = self._documents.deposer_livrable(
            DocumentUploadInput(
                type_document=type_document,
                nom=nom_fichier,
                stream=io.BytesIO(rendu.octets),
                created_by=propose_par or self._actor_id,
                proposed_by_agent=propose_par,
                organisation_id=organisation_ancre,
                appel_id=appel_id,
                offre_id=offre_id,
                doc_metadata={
                    "modele_document_id": str(modele.id),
                    "version_modele": modele.version,
                    "moteur": "INC-128",
                    "champs_remplaces": rendu.champs_remplaces,
                },
            )
        )
        self._session.flush()
        return document, rendu, rapport

    # --- internes -----------------------------------------------------------

    def _octets_modele(self, modele: ModeleDocument) -> bytes:
        """Contenu du DOCX d'origine, jamais modifié.

        Raises:
            ValidationError: modèle non DOCX ou fichier introuvable.
        """
        _nom, chemin = self._documents.telecharger(modele.document_template_id)
        if chemin.suffix.lower() != ".docx":
            raise ValidationError(
                f"Le modèle « {modele.nom} » est un fichier "
                f"{chemin.suffix.lstrip('.').upper()} : le moteur documentaire ne "
                "traite que les DOCX. Un PDF ne donne que son identité visuelle.",
                details={"format": chemin.suffix.lstrip(".").lower()},
            )
        return chemin.read_bytes()

    def _verifier_blocs(
        self, analyse: S.AnalyseModele, configuration: S.ConfigurationModele
    ) -> None:
        """Chaque champ doit viser un bloc réellement présent dans l'analyse.

        Raises:
            ValidationError: ``block_id`` inconnu (inventé ou analyse périmée).
        """
        connus = {bloc.block_id for bloc in analyse.blocs}
        inconnus = sorted(
            champ.block_id for champ in configuration.champs if champ.block_id not in connus
        )
        if inconnus:
            raise ValidationError(
                "Champs rattachés à des blocs absents de l'analyse : "
                + ", ".join(sorted(set(inconnus))),
                details={"block_ids": sorted(set(inconnus))},
            )

    # --- découverte et copie d'un modèle dans une offre (INC-129) ------------

    def modele_actif(self, organisation_id: UUID, type_document: str) -> ModeleDocument:
        """Modèle de plus haute version pour ce couple (organisation, type).

        Raises:
            NotFoundError: aucun modèle enregistré — jamais de repli silencieux.
        """
        modele = self._modeles.find_active(organisation_id, type_document)
        if modele is None:
            raise NotFoundError(
                f"Aucun modèle « {type_document} » enregistré pour cette organisation"
            )
        return modele

    def lister(
        self,
        organisation_id: UUID,
        *,
        type_document: str | None = None,
        valides_seulement: bool = False,
    ) -> list[dict[str, Any]]:
        """Modèles de l'organisation, du plus récent au plus ancien."""
        if type_document:
            modeles = self._modeles.list_for_type(organisation_id, type_document)
        else:
            modeles = sorted(
                self._modeles.list_for_organisation(organisation_id),
                key=lambda modele: (modele.type_document, -modele.version),
            )
        entrees: list[dict[str, Any]] = []
        for modele in modeles:
            pret = modele.statut_validation == _STATUT_VALIDEE
            if valides_seulement and not pret:
                continue
            entrees.append(
                {
                    "modele_id": str(modele.id),
                    "nom": modele.nom,
                    "type_document": modele.type_document,
                    "version": modele.version,
                    "statut_analyse": modele.statut_analyse,
                    "statut_validation": modele.statut_validation,
                    "document_template_id": str(modele.document_template_id),
                }
            )
        return entrees

    def structure_modele(self, organisation_id: UUID, modele_id: UUID) -> dict[str, Any]:
        """Éléments réels d'un modèle — ce que la copie contiendra.

        Analyse le DOCX s'il ne l'est pas encore (lecture déterministe : aucune
        classification inventée). Le fichier n'est jamais modifié.
        """
        modele = self.obtenir_modele(organisation_id, modele_id)
        if not modele.analyse:
            self.analyser(organisation_id, modele_id, proposer=False)
        octets = self._octets_modele(modele)
        elements = edition_docx.indexer(octets)
        analyse = S.AnalyseModele.model_validate(modele.analyse)
        return {
            "modele_id": str(modele.id),
            "nom": modele.nom,
            "type_document": modele.type_document,
            "version": modele.version,
            "document_template_id": str(modele.document_template_id),
            "empreinte": analyse.empreinte,
            "nombre_elements": len(elements),
            "elements": [element.resume() for element in elements],
            "zones": [zone.model_dump(mode="json") for zone in analyse.zones],
            "images": analyse.images,
            "placeholders": analyse.placeholders,
            "avertissements": list(analyse.avertissements),
            "etape_suivante": (
                "Appeler `copier_modele_document` avec `ancre` + `entity_id` "
                "(offre, mission ou session) : le modèle est copié tel quel dans "
                "cet objet, puis édité élément par élément. Les identifiants "
                "ci-dessus seront les mêmes dans la copie."
            ),
        }

    def modele_pour_sujet(
        self,
        sujet: _Sujet,
        *,
        modele_id: UUID | None = None,
        type_document: str | None = None,
    ) -> ModeleDocument | None:
        """Modèle à appliquer à cet objet, ou ``None`` (aucun modèle utilisable).

        Ordre de recherche explicite : modèle désigné, puis type demandé, puis
        type de l'offre (préfixé ou non), puis modèle générique ``offre``. Pour
        une mission ou une session, sans type ni identifiant : si l'organisation
        n'a **qu'un** modèle, il est pris ; sinon la liste est renvoyée pour
        choisir. Aucun gabarit n'est inventé.
        """
        if modele_id is not None:
            return self.obtenir_modele(sujet.organisation_id, modele_id)
        candidats: list[str] = []
        if type_document:
            candidats.append(type_document)
        else:
            candidats.extend(self._types_modele_de_l_objet(sujet))
        for candidat in dict.fromkeys(candidats):
            modele = self._modeles.find_active(sujet.organisation_id, candidat)
            if modele is not None:
                return modele
        modeles = self._modeles.list_for_organisation(sujet.organisation_id)
        if not modeles:
            return None
        if candidats:
            # Un type a été demandé et l'organisation ne l'a pas : le dire avec
            # la liste réelle, plutôt que de prendre un autre modèle en silence.
            raise ValidationError(
                f"Aucun modèle « {type_document or candidats[0]} » pour "
                f"« {sujet.organisation_nom} ». Le document se produit alors "
                "avec `generer_document_professionnel` : un modèle d'un autre "
                "type ne s'applique pas (un gabarit d'offre n'est jamais une "
                "fiche de présence). Si un modèle du bon type existe sous un "
                "autre nom, le désigner par `modele_id`.",
                details={
                    "modeles": [
                        {
                            "modele_id": str(modele.id),
                            "nom": modele.nom,
                            "type_document": modele.type_document,
                            "version": modele.version,
                        }
                        for modele in modeles
                    ]
                },
            )
        if len(modeles) == 1:
            return modeles[0]
        raise ValidationError(
            f"Plusieurs modèles existent pour « {sujet.organisation_nom} » : "
            "préciser `modele_id` (ou `type_document`) pour choisir celui qui "
            "s'applique.",
            details={
                "modeles": [
                    {
                        "modele_id": str(modele.id),
                        "nom": modele.nom,
                        "type_document": modele.type_document,
                        "version": modele.version,
                    }
                    for modele in modeles
                ]
            },
        )

    def copier_modele_dans_objet(
        self,
        ancre: str,
        entity_id: UUID,
        *,
        modele_id: UUID | None = None,
        type_document: str | None = None,
        nom_fichier: str | None = None,
    ) -> tuple[Document, ModeleDocument, list[edition_docx.ElementModele]]:
        """Copie le modèle **tel quel** dans l'offre, la mission ou la session.

        Le fichier de l'organisation est déposé sans réécriture : son nom est
        déterministe (``modele-<référence>.docx``), donc un second appel renvoie
        la **version en cours** au lieu d'empiler des copies.

        Args:
            ancre: ``offre``, ``mission`` ou ``session``.
            entity_id: identifiant de cet objet.
            modele_id: modèle désigné ; sinon résolution par type (voir
                :meth:`modele_pour_sujet`).
            type_document: type de **dépôt** du document de travail (vocabulaire
                documentaire). Défaut : ``offre`` pour une offre, ``autre``
                sinon.
            nom_fichier: nom du document de travail (défaut déterministe).

        Raises:
            NotFoundError: objet inconnu, ou organisation sans modèle — aucun
                gabarit n'est alors inventé (génération professionnelle).
            ValidationError: ancre non prise en charge, modèle non DOCX.
        """
        sujet = self._sujet(ancre, entity_id)
        modele = self.modele_pour_sujet(
            sujet, modele_id=modele_id, type_document=type_document
        )
        if modele is None:
            raise NotFoundError(
                f"Aucun modèle de document pour l'organisation « "
                f"{sujet.organisation_nom} » : la chaîne modèle ne s'applique "
                "pas. Utiliser la génération professionnelle "
                "(generer_document_professionnel) et le dire à l'utilisateur.",
                details={"ancre": ancre, "entity_id": str(sujet.id)},
            )
        octets = self._octets_modele(modele)
        nom = self._nom_travail(sujet, nom_fichier)
        depot = type_document or _TYPE_DEPOT_DEFAUT[ancre]
        # Une copie de travail se reconnaît à sa métadonnée, pas à son nom (le
        # nom du livrable est libre, cf. `nom_fichier`). Sans cela, un second
        # appel avec un autre nom empilait une deuxième copie du même modèle, et
        # l'agent éditait celle qui ne portait pas le bon nom.
        existant = self._document_travail(sujet, modele_id=modele.id)
        if existant is None:
            existant = self._document_nomme(sujet, nom)
        elements = edition_docx.indexer(octets)
        if existant is not None:
            return existant, modele, elements
        document = self._documents.deposer_livrable(
            DocumentUploadInput(
                type_document=depot,
                nom=nom,
                stream=io.BytesIO(octets),
                created_by=self._actor_id,
                proposed_by_agent=self._actor_id,
                doc_metadata={
                    **self._metadonnees_travail(modele, ancre),
                    "type_depot": depot,
                },
                **{_CHAMP_ANCRE[ancre]: sujet.id},
            )
        )
        self._session.flush()
        return document, modele, elements

    def exiger_copie_modele(
        self, ancre: str, entity_id: UUID, *, type_document: str | None = None
    ) -> None:
        """Refuse un document neuf quand le modèle doit être copié puis édité.

        Appelé par les outils de **génération** (professionnel, HTML, structuré,
        rédaction progressive) avant de produire quoi que ce soit. La règle
        CARSO : un modèle de l'organisation ne se réinterprète pas, il se copie
        (``copier_modele_document``) et s'édite élément par élément
        (``remplacer_element_model`` / ``remplir_modele``) — générer un document
        neuf donnerait un livrable conforme au thème CARSO, jamais au client.

        Deux cas sont refusés, et deux seulement :

        1. une **copie du modèle existe déjà** sur l'objet et le document
           demandé a le même type de dépôt : c'est la copie qui s'édite, pas un
           second document du même type qui se crée ;
        2. l'objet est une **offre** et son organisation a un modèle applicable :
           aucun document d'offre ne se produit à neuf, même avant la copie.

        Un autre type de dépôt reste possible sur un objet qui porte une copie
        (annexe, lettre, note), et une offre dont l'organisation n'a aucun
        modèle garde la génération professionnelle.

        Raises:
            NotFoundError: objet inconnu.
            ValidationError: génération refusée — le message nomme les outils à
                employer et les modèles disponibles.
        """
        sujet = self._sujet(ancre, entity_id)
        depot = (type_document or _TYPE_DEPOT_DEFAUT[ancre]).strip().lower()
        copie = self._document_travail(sujet)
        if copie is not None and self._copie_couvre_le_type(sujet, copie):
            type_copie = (copie.doc_metadata or {}).get("type_depot") or copie.type_document
            if str(type_copie).lower() == depot:
                raise ValidationError(
                    f"« {sujet.libelle} » a déjà sa copie du modèle "
                    f"(document {copie.id}, version {copie.version}) : ce "
                    "document s'ÉDITE, il ne se régénère pas. Relire sa "
                    "structure (obtenir_structure), puis remplacer "
                    "les éléments (remplacer_element_model) et remplir les "
                    "sections (remplir_modele).",
                    details={
                        "ancre": ancre,
                        "entity_id": str(sujet.id),
                        "document_id": str(copie.id),
                        "version": copie.version,
                        "type_document": depot,
                        "outils": [
                            "obtenir_structure",
                            "remplacer_element_model",
                            "remplir_modele",
                        ],
                    },
                )
        if ancre != "offre" or depot not in _TYPES_DOCUMENT_OFFRE:
            return
        modeles = self._modeles_applicables(sujet)
        if not modeles:
            return
        raise ValidationError(
            f"Aucun document d'offre ne se génère à neuf pour « {sujet.libelle} "
            f"» : l'organisation « {sujet.organisation_nom} » a déposé "
            f"{len(modeles)} modèle(s) applicable(s) à ce type d'offre. "
            "Copier le modèle "
            "(copier_modele_document), puis éditer la copie : "
            "remplacer_element_model pour les textes et titres, remplir_modele "
            "pour les sections rédigées. Aucun texte du modèle n'est inventé.",
            details={
                "ancre": ancre,
                "entity_id": str(sujet.id),
                "modeles": modeles,
                "outils": [
                    "lister_modeles_documents",
                    "obtenir_structure",
                    "copier_modele_document",
                    "remplacer_element_model",
                    "remplir_modele",
                ],
            },
        )

    def _copie_couvre_le_type(self, sujet: _Sujet, document: Document) -> bool:
        """La copie de travail vient-elle d'un modèle applicable à cet objet ?

        Une copie héritée d'un modèle d'un **autre** type d'offre — le modèle
        technique du client copié dans l'offre financière du même lot — n'est
        pas le document de ce type : elle ne bloque pas la génération du bon
        livrable. Sans ce contrôle, l'offre financière restait fermée par une
        copie qui ne lui correspond pas (``OFFRE-FIN-L01``, modèle FID
        ``Model_offre_technique_FID.docx``).

        Une copie sans modèle consigné, ou dont le modèle a disparu du
        registre, reste considérée comme valable : on ne rouvre pas la
        génération sur une copie dont on ignore l'origine.
        """
        modele_id = (document.doc_metadata or {}).get("modele_document_id")
        if not modele_id:
            return True
        try:
            modele = self.obtenir_modele(sujet.organisation_id, UUID(str(modele_id)))
        except (NotFoundError, ValueError):
            return True
        candidats = self._types_modele_de_l_objet(sujet)
        if not candidats:
            return True
        return str(modele.type_document) in candidats

    def _types_modele_de_l_objet(self, sujet: _Sujet) -> tuple[str, ...]:
        """Types de modèle candidats pour cet objet, du plus précis au plus large.

        C'est **le type de l'offre** qui commande : le modèle technique déposé
        par le client ne couvre pas l'offre financière du même lot, et
        réciproquement. Le vocabulaire du registre accepte le type nu
        (``offre_technique``), sa forme préfixée (``modele_offre_technique``),
        la variante ``modele_offre_<type>`` (``modele_offre_autre``) puis le
        générique ``offre`` / ``modele_offre``, qui vaut pour toute offre.

        Hors offre, aucun candidat : la résolution reste celle de
        l'appelant (un seul modèle du registre, ou une liste à trancher).
        """
        if not isinstance(sujet.objet, Offre):
            return ()
        type_offre = str(sujet.objet.type)
        variantes = [type_offre, f"modele_{type_offre}"]
        if not type_offre.startswith("offre_"):
            # Type d'offre « autre » : le registre le nomme ``modele_offre_autre``.
            variantes.append(f"modele_offre_{type_offre}")
        variantes.extend((_TYPE_MODELE_GENERIQUE, f"modele_{_TYPE_MODELE_GENERIQUE}"))
        return tuple(dict.fromkeys(variantes))

    def _modeles_applicables(self, sujet: _Sujet) -> list[dict[str, Any]]:
        """Modèles du registre qui couvrent **réellement** cet objet.

        Le type filtre la liste : un ``modele_offre_technique`` n'est pas
        applicable à une offre financière, même s'il est le seul du registre.
        Sans ce filtre, un modèle déposé pour un autre type d'offre bloquait la
        génération de l'offre demandée et invitait à copier un gabarit qui ne
        lui correspond pas (constaté sur ``OFFRE-FIN-L01`` : le modèle technique
        du FID fermait l'offre financière).

        Mêmes types candidats que :meth:`modele_pour_sujet` (un seul endroit à
        corriger) et même résolution ``find_active`` (un gabarit supprimé est
        écarté). Un registre sans modèle applicable donne une liste vide : la
        génération libre reste alors ouverte.
        """
        retenus: list[ModeleDocument] = []
        vus: set[UUID] = set()
        for candidat in self._types_modele_de_l_objet(sujet):
            modele = self._modeles.find_active(sujet.organisation_id, candidat)
            if modele is None or modele.id in vus:
                continue
            vus.add(modele.id)
            retenus.append(modele)
        return [
            {
                "modele_id": str(modele.id),
                "nom": modele.nom,
                "type_document": modele.type_document,
                "version": modele.version,
            }
            for modele in retenus
        ]

    def document_de_travail(self, ancre: str, entity_id: UUID) -> tuple[_Sujet, Document]:
        """Copie du modèle en cours pour cet objet.

        Raises:
            NotFoundError: aucune copie — l'agent doit d'abord copier le modèle
                (``copier_modele_document``).
        """
        sujet = self._sujet(ancre, entity_id)
        document = self._document_travail(sujet)
        if document is None:
            raise NotFoundError(
                f"Aucun document de travail pour « {sujet.libelle} » : copier "
                "d'abord le modèle de l'organisation (copier_modele_document), "
                "puis éditer la copie.",
                details={"ancre": ancre, "entity_id": str(sujet.id)},
            )
        return sujet, document

    def structure_document(self, ancre: str, entity_id: UUID) -> dict[str, Any]:
        """Éléments éditables de la copie en cours, avec leur identifiant.

        Les identifiants sont recalculés à chaque lecture : après une édition,
        relire la structure avant de viser un autre élément.
        """
        sujet, document = self.document_de_travail(ancre, entity_id)
        octets = self._octets_document(document)
        elements = edition_docx.indexer(octets)
        return {
            "ancre": sujet.ancre,
            "entity_id": str(sujet.id),
            "reference": sujet.reference,
            "document_id": str(document.id),
            "nom": document.nom,
            "version": document.version,
            "statut": document.statut,
            "modele": self._modele_du_document(document),
            "nombre_elements": len(elements),
            "elements": [element.resume() for element in elements],
            "historique": [
                {
                    "version": candidat.version,
                    "courante": candidat.version == document.version,
                    "statut": candidat.statut,
                    "nature": (candidat.doc_metadata or {}).get("derniere_edition"),
                    "document_id": str(candidat.id),
                }
                for candidat in self._historique_copie(sujet, document)
            ],
            "etape_suivante": (
                "Remplacer le texte d'un élément avec `remplacer_element_model` "
                "(style du modèle conservé) ou insérer du contenu HTML stylisé "
                "avec `remplir_modele` (titres, listes, tableaux, images en data "
                "URI base64). Relire la structure entre deux lots d'éditions. Un "
                "lot raté s'annule avec `revenir_version_document` (`version` "
                "omettant ⇒ dernier geste ; `version` donnée ⇒ version précise "
                "de `historique`), puis se refait après relecture."
            ),
        }

    def remplacer_element(
        self,
        ancre: str,
        entity_id: UUID,
        element_id: str,
        nouveau_element: Any,
    ) -> dict[str, Any]:
        """Remplace le texte d'**un** élément, style du modèle conservé.

        Raccourci de :meth:`remplacer_elements` pour le cas courant (un titre,
        une ligne). Le même chemin est emprunté : une seule version déposée.
        """
        return self.remplacer_elements(
            ancre,
            entity_id,
            [{"element_id": element_id, "nouveau_element": nouveau_element}],
        )

    def remplacer_elements(
        self,
        ancre: str,
        entity_id: UUID,
        remplacements: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Remplace le texte de plusieurs éléments, en **une seule version**.

        Chaque entrée porte un ``element_id`` et son ``nouveau_element`` (chaîne
        ou segments de style). Les remplacements sont appliqués de la fin vers le
        début du document ; un remplacement de texte ne change pas le nombre de
        blocs, donc les identifiants lus ensemble restent valides.

        Raises:
            ValidationError: liste vide, doublon d'identifiant, identifiant
                inconnu ou élément non textuel (tableau, paragraphe illustré).
            NotFoundError: aucune copie de modèle pour cet objet.
            GenerationError: copie produite invalide — **rien n'est déposé**.
        """
        if not remplacements:
            raise ValidationError(
                "Aucun remplacement fourni : nommer au moins un `element_id` et "
                "son `nouveau_element`."
            )
        vus: set[str] = set()
        for item in remplacements:
            element_id = str(item.get("element_id") or "").strip()
            if not element_id:
                raise ValidationError(
                    "Chaque remplacement doit nommer son `element_id` "
                    "(identifiant lu par `obtenir_structure`)."
                )
            if element_id in vus:
                raise ValidationError(
                    f"L'élément {element_id} est visé deux fois : garder une "
                    "seule entrée par élément."
                )
            vus.add(element_id)

        sujet, document = self.document_de_travail(ancre, entity_id)
        octets = self._octets_document(document)
        total: dict[str, Any] = {
            "remplacements": [],
            "parties_modifiees": set(),
            "parties_ajoutees": set(),
        }
        for item in sorted(
            remplacements,
            key=lambda entree: _rang_element(str(entree["element_id"])),
            reverse=True,
        ):
            element_id = str(item["element_id"])
            resultat = edition_docx.remplacer_texte(
                octets, element_id, item.get("nouveau_element")
            )
            octets = resultat.octets
            total["parties_modifiees"].update(resultat.parties_modifiees)
            total["remplacements"].append(
                {
                    "element_id": element_id,
                    "ancien_texte": resultat.ancien_texte,
                    "nouveau_texte": _texte_du_remplacement(item.get("nouveau_element")),
                }
            )
        edition = edition_docx.Edition(
            octets=octets,
            element_id=total["remplacements"][0]["element_id"],
            partie=paquet_docx.PARTIE_DOCUMENT,
            ancien_texte=total["remplacements"][0]["ancien_texte"],
            parties_modifiees=sorted(total["parties_modifiees"]),
            parties_ajoutees=sorted(total["parties_ajoutees"]),
        )
        nouveau = self._enregistrer_edition(
            sujet,
            document,
            edition,
            nature="remplacement_texte",
            details={"remplacements": total["remplacements"]},
        )
        sortie: dict[str, Any] = {
            "ancre": sujet.ancre,
            "entity_id": str(sujet.id),
            "document_id": str(nouveau.id),
            "version": nouveau.version,
            "statut": nouveau.statut,
            "remplacements": total["remplacements"],
            "nombre": len(total["remplacements"]),
            "message": (
                "Texte remplacé dans la copie du modèle ({nombre} élément(s), "
                "une seule version) : les styles, l'en-tête, le pied et les "
                "éléments non visés sont inchangés."
            ).format(nombre=len(total["remplacements"])),
        }
        if len(total["remplacements"]) == 1:
            # Forme du remplacement unitaire : le premier élément reste nommé
            # directement, pour un appelant qui n'a visé qu'un titre.
            sortie["element_id"] = total["remplacements"][0]["element_id"]
            sortie["ancien_texte"] = total["remplacements"][0]["ancien_texte"]
        return sortie

    def remplir(
        self,
        ancre: str,
        entity_id: UUID,
        remplissages: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Applique plusieurs remplissages HTML à la copie, en une version.

        Chaque entrée vise un élément (``element_id``) et fournit un HTML
        stylisé (``contenu_html``), éventuellement en ``position`` ``avant`` ou
        ``apres`` l'élément. Les entrées sont appliquées de la fin vers le début
        du document : les identifiants fournis restent donc valides.

        Raises:
            ValidationError: liste vide, doublon d'identifiant, HTML invalide.
            NotFoundError: aucune copie de modèle pour cet objet.
            GenerationError: copie produite invalide — **rien n'est déposé**.
        """
        if not remplissages:
            raise ValidationError(
                "Aucun contenu fourni : décrire au moins un remplissage "
                "({element_id, contenu_html})."
            )
        vus: set[str] = set()
        for item in remplissages:
            element_id = str(item.get("element_id") or "").strip()
            if not element_id:
                raise ValidationError(
                    "Chaque remplissage doit nommer son `element_id` "
                    "(identifiant lu par `obtenir_structure`)."
                )
            if element_id in vus:
                raise ValidationError(
                    f"L'élément {element_id} est visé deux fois : regrouper son "
                    "contenu en un seul HTML."
                )
            vus.add(element_id)

        sujet, document = self.document_de_travail(ancre, entity_id)
        octets = self._octets_document(document)
        total: dict[str, Any] = {
            "blocs_inseres": 0,
            "images_ajoutees": 0,
            "avertissements": [],
            "elements": [],
            "parties_modifiees": set(),
            "parties_ajoutees": set(),
        }
        for item in sorted(
            remplissages,
            key=lambda entree: _rang_element(str(entree["element_id"])),
            reverse=True,
        ):
            element_id = str(item["element_id"])
            resultat = edition_docx.remplacer_par_html(
                octets,
                element_id,
                str(item.get("contenu_html") or ""),
                position=str(item.get("position") or "remplacer"),
            )
            octets = resultat.octets
            total["blocs_inseres"] += resultat.blocs_inseres
            total["images_ajoutees"] += resultat.images_ajoutees
            total["avertissements"].extend(resultat.avertissements)
            total["parties_modifiees"].update(resultat.parties_modifiees)
            total["parties_ajoutees"].update(resultat.parties_ajoutees)
            total["elements"].append(
                {
                    "element_id": element_id,
                    "ancien_texte": resultat.ancien_texte,
                    "blocs_inseres": resultat.blocs_inseres,
                }
            )
        edition = edition_docx.Edition(
            octets=octets,
            element_id=total["elements"][0]["element_id"],
            partie=paquet_docx.PARTIE_DOCUMENT,
            ancien_texte=total["elements"][0]["ancien_texte"],
            blocs_inseres=total["blocs_inseres"],
            images_ajoutees=total["images_ajoutees"],
            parties_modifiees=sorted(total["parties_modifiees"]),
            parties_ajoutees=sorted(total["parties_ajoutees"]),
            avertissements=sorted(set(total["avertissements"])),
        )
        nouveau = self._enregistrer_edition(
            sujet,
            document,
            edition,
            nature="remplissage_html",
            details={"elements": total["elements"]},
        )
        return {
            "ancre": sujet.ancre,
            "entity_id": str(sujet.id),
            "document_id": str(nouveau.id),
            "version": nouveau.version,
            "statut": nouveau.statut,
            "elements_remplis": total["elements"],
            "blocs_inseres": total["blocs_inseres"],
            "images_ajoutees": total["images_ajoutees"],
            "avertissements": sorted(set(total["avertissements"])),
            "message": (
                "Contenu inséré dans la copie du modèle : les éléments visés sont "
                "remplacés, tout le reste (couverture, en-tête, pied, styles, "
                "textes fixes) est conservé. Relire la structure avant la suite."
            ),
        }

    def revenir_version_precedente(self, ancre: str, entity_id: UUID) -> dict[str, Any]:
        """Raccourci : annule la dernière édition (voir :meth:`revenir_version`)."""
        return self.revenir_version(ancre, entity_id)

    def revenir_version(
        self,
        ancre: str,
        entity_id: UUID,
        version: int | None = None,
    ) -> dict[str, Any]:
        """Restaure une version de l'historique de la copie de travail.

        Le retour arrière **ne supprime rien** : le contenu de la version
        restaurée est déposé comme **nouvelle version**, qui devient le document
        de travail. L'historique reste complet et auditable.

        Args:
            version: version à restaurer, telle que lue dans ``historique`` (ou
                dans une réponse d'édition). Omise : la version immédiatement
                précédente, c'est-à-dire l'annulation du dernier geste.

        Usage : un lot HTML ou de texte a abîmé une section ; plutôt que de
        réparer à l'aveugle, on revient à l'état connu voulu puis on refait le
        travail.

        Raises:
            NotFoundError: aucune copie de modèle pour cet objet.
            ValidationError: version inconnue (l'historique est listé), version
                déjà courante, ou fichier restauré qui ne passe plus le
                contrôle structurel.
        """
        sujet, document = self.document_de_travail(ancre, entity_id)
        historique = self._historique_copie(sujet, document)
        if version is None:
            precedentes = [
                candidat
                for candidat in historique
                if candidat.version < document.version
            ]
            if not precedentes:
                raise ValidationError(
                    f"« {sujet.libelle} » n'a pas de version antérieure : la copie "
                    f"{document.nom} en est à sa version {document.version}, il n'y "
                    "a rien à annuler.",
                    details={
                        "ancre": ancre,
                        "entity_id": str(sujet.id),
                        "version": document.version,
                        "versions_disponibles": [
                            candidat.version for candidat in historique
                        ],
                    },
                )
            cible = precedentes[0]
        else:
            cible = next(
                (candidat for candidat in historique if candidat.version == version),
                None,
            )
            if cible is None:
                disponibles = sorted({candidat.version for candidat in historique})
                raise ValidationError(
                    f"La version {version} n'existe pas pour « {sujet.libelle} » "
                    f"(versions disponibles : "
                    f"{', '.join(str(numero) for numero in disponibles)}).",
                    details={
                        "ancre": ancre,
                        "entity_id": str(sujet.id),
                        "version_demandee": version,
                        "versions_disponibles": disponibles,
                    },
                )
            if cible.version == document.version:
                raise ValidationError(
                    f"La version {version} est déjà celle du document de travail : "
                    "choisir une autre version de l'historique.",
                    details={
                        "ancre": ancre,
                        "entity_id": str(sujet.id),
                        "version": version,
                    },
                )
        octets = self._octets_document(cible)
        if octets == self._octets_document(document):
            raise ValidationError(
                f"La version {cible.version} a un contenu identique à la version "
                f"courante ({document.version}) : rien à restaurer.",
                details={
                    "ancre": ancre,
                    "entity_id": str(sujet.id),
                    "version": cible.version,
                    "version_courante": document.version,
                },
            )
        rapport = validation_docx.DocumentValidator().valider_edition(
            octets,
            precedent=octets,
            parties_modifiees=[],
            parties_ajoutees=[],
        )
        if not rapport.ok:
            raise ValidationError(
                "La version "
                f"{cible.version} ne peut pas être restaurée : "
                + " ; ".join(rapport.erreurs),
                details={"rapport": rapport.model_dump()},
            )
        edition = edition_docx.Edition(
            octets=octets,
            element_id="",
            partie=paquet_docx.PARTIE_DOCUMENT,
            ancien_texte="",
            parties_modifiees=[],
            parties_ajoutees=[],
        )
        nouveau = self._enregistrer_edition(
            sujet,
            document,
            edition,
            nature="retour_arriere",
            details={
                "version_annulee": document.version,
                "version_restauree": cible.version,
                "document_restaure_id": str(cible.id),
            },
            precedent=octets,
        )
        return {
            "ancre": sujet.ancre,
            "entity_id": str(sujet.id),
            "document_id": str(nouveau.id),
            "version": nouveau.version,
            "statut": nouveau.statut,
            "version_restauree": cible.version,
            "version_annulee": document.version,
            "versions_disponibles": sorted(
                {candidat.version for candidat in historique}
            ),
            "message": (
                "Retour arrière effectué : le contenu de la version "
                f"{cible.version} est redevenu la copie de travail (nouvelle "
                f"version {nouveau.version}, version {document.version} "
                "conservée dans l'historique). Relire la structure avant de "
                "refaire l'édition."
            ),
        }

    def _historique_copie(self, sujet: _Sujet, document: Document) -> list[Document]:
        """Versions actives de cette copie, la plus récente d'abord.

        Une copie est identifiée par son objet et son **nom déterministe** :
        l'historique est donc la famille de documents de même nom sur l'objet.
        """
        return self._versions(sujet, document.nom)

    def _versions(self, sujet: _Sujet, nom: str) -> list[Document]:
        """Documents actifs portant ce nom sur l'objet, du plus récent au plus ancien."""
        return sorted(
            (
                candidat
                for candidat in self._documents.lister_pour(
                    _ANCRE_LISTER[sujet.ancre], sujet.id
                )
                if candidat.nom == nom
                and candidat.statut not in ("archive", "supprime")
            ),
            key=lambda candidat: candidat.version,
            reverse=True,
        )

    # --- internes de la chaîne modèle (INC-129) -----------------------------

    def _sujet(self, ancre: str, entity_id: UUID) -> _Sujet:
        """Objet métier qui reçoit la copie, résolu par son ancre.

        Trois ancres sont prises en charge : l'offre (générateur d'offre), la
        mission et la session (assistant formateur). Les autres refusent
        explicitement — une copie sans objet métier n'aurait aucun dossier où
        vivre, ni aucun périmètre d'accès.

        Raises:
            ValidationError: ancre hors du périmètre de la chaîne modèle.
            NotFoundError: objet inconnu.
        """
        if ancre not in ANCRES_MODELE:
            raise ValidationError(
                f"Ancre {ancre!r} non prise en charge par la chaîne modèle "
                f"(acceptées : {', '.join(ANCRES_MODELE)}).",
                details={"ancres": list(ANCRES_MODELE)},
            )
        if ancre == "offre":
            objet = OffreRepository(self._session).get(entity_id)
            if objet is None:
                raise NotFoundError(f"Offre {entity_id} introuvable")
            return _Sujet(
                ancre=ancre,
                id=objet.id,
                org_id=objet.organisation_id,
                organisation_nom=objet.organisation.nom,
                reference=objet.reference,
                titre=objet.titre,
                objet=objet,
            )
        if ancre == "mission":
            mission = MissionRepository(self._session).get(entity_id)
            if mission is None:
                raise NotFoundError(f"Mission {entity_id} introuvable")
            return _Sujet(
                ancre=ancre,
                id=mission.id,
                org_id=mission.organisation_id,
                organisation_nom=self._nom_organisation(mission.organisation_id),
                reference=mission.reference,
                titre=mission.titre,
                objet=mission,
            )
        session = SessionRepository(self._session).get(entity_id)
        if session is None:
            raise NotFoundError(f"Session {entity_id} introuvable")
        mission = session.mission
        return _Sujet(
            ancre=ancre,
            id=session.id,
            org_id=mission.organisation_id,
            organisation_nom=self._nom_organisation(mission.organisation_id),
            reference=f"{mission.reference}-session-{str(session.id)[:8]}",
            titre=session.theme or mission.titre,
            objet=session,
        )

    def _nom_organisation(self, organisation_id: UUID) -> str:
        """Nom de l'organisation propriétaire des modèles."""
        organisation = OrganisationRepository(self._session).get(organisation_id)
        return organisation.nom if organisation is not None else str(organisation_id)

    def _document_nomme(self, sujet: _Sujet, nom: str) -> Document | None:
        """Copie de travail **lisible** portant ce nom, la plus récente.

        Une fiche sans fichier n'est pas une copie de travail : elle ne peut
        être ni lue ni éditée. Ce cas se produit quand le dépôt du livrable a
        enregistré la fiche sans écrire les octets (incident constaté : deux
        copies homonymes sur une même offre, l'une sans fichier). La retenir
        ferait échouer toute édition suivante — et la copie qui porte vraiment
        le modèle resterait inutilisée alors qu'elle existe.

        On retient donc la version la plus récente dont le fichier est présent,
        et ``None`` s'il n'y en a aucune : l'appelant recopie le modèle.
        """
        for candidat in self._versions(sujet, nom):
            try:
                self._documents.telecharger(candidat.id)
            except Exception:  # noqa: BLE001 - fiche orpheline : on essaie la suivante
                continue
            return candidat
        return None

    def _versions_travail(
        self, sujet: _Sujet, *, modele_id: UUID | None = None
    ) -> list[Document]:
        """Copies de travail de l'objet, quel que soit leur nom, les plus récentes d'abord."""
        return sorted(
            (
                candidat
                for candidat in self._documents.lister_pour(
                    _ANCRE_LISTER[sujet.ancre], sujet.id
                )
                if (candidat.doc_metadata or {}).get("role") == "copie_modele"
                and candidat.statut not in ("archive", "supprime")
                and (
                    modele_id is None
                    or str((candidat.doc_metadata or {}).get("modele_document_id"))
                    == str(modele_id)
                )
            ),
            key=lambda candidat: (candidat.version, candidat.created_at),
            reverse=True,
        )

    def _document_travail(
        self, sujet: _Sujet, *, modele_id: UUID | None = None
    ) -> Document | None:
        """Copie de travail **lisible** de l'objet, quel que soit son nom.

        Le nom d'une copie est libre : `nom_fichier` permet de nommer le livrable
        (``Offre technique OFFRE-TECH-L99.docx``). La seule signature stable reste
        donc la métadonnée ``role = copie_modele``. Le nom déterministe
        ``modele-<référence>`` reste le repli des copies déposées avant cette
        règle, et des tests qui n'écrivent pas les métadonnées.
        """
        for candidat in self._versions_travail(sujet, modele_id=modele_id):
            try:
                self._documents.telecharger(candidat.id)
            except Exception:  # noqa: BLE001 - fiche orpheline : on essaie la suivante
                continue
            return candidat
        return self._document_nomme(sujet, self._nom_travail(sujet))

    def _copie_fantome(self, sujet: _Sujet, nom: str) -> list[Document]:
        """Fiches homonymes dont le fichier manque (diagnostic, jamais éditées)."""
        orphelines: list[Document] = []
        for candidat in self._versions(sujet, nom):
            try:
                self._documents.telecharger(candidat.id)
            except Exception:  # noqa: BLE001
                orphelines.append(candidat)
        return orphelines

    def _nom_travail(self, sujet: _Sujet, nom_fichier: str | None = None) -> str:
        """Nom **déterministe** de la copie de travail d'un objet."""
        if nom_fichier:
            return nom_fichier
        return f"modele-{_nom_fichier_sur(sujet.reference)}.docx"

    def _metadonnees_travail(self, modele: ModeleDocument, ancre: str) -> dict[str, Any]:
        """Métadonnées qui rattachent la copie à son modèle d'origine."""
        return {
            "role": "copie_modele",
            "ancre": ancre,
            "modele_document_id": str(modele.id),
            "version_modele": modele.version,
            "nom_modele": modele.nom,
            "moteur": "INC-129",
        }

    def _modele_du_document(self, document: Document) -> dict[str, Any] | None:
        """Modèle d'origine d'une copie, tel que consigné dans ses métadonnées."""
        metadonnees = document.doc_metadata or {}
        if not metadonnees.get("modele_document_id"):
            return None
        return {
            "modele_id": metadonnees.get("modele_document_id"),
            "nom": metadonnees.get("nom_modele"),
            "version": metadonnees.get("version_modele"),
        }

    def _octets_document(self, document: Document) -> bytes:
        """Octets d'un document déposé (jamais modifié sur disque)."""
        _nom, chemin = self._documents.telecharger(document.id)
        return chemin.read_bytes()

    def _enregistrer_edition(
        self,
        sujet: _Sujet,
        document: Document,
        edition: edition_docx.Edition,
        *,
        nature: str,
        details: dict[str, Any],
        precedent: bytes | None = None,
    ) -> Document:
        """Vérifie la copie éditée puis dépose une **nouvelle version**.

        Args:
            precedent: version de référence pour le contrôle des parties.
                Par défaut la copie courante. Un **retour arrière** passe le
                fichier restauré lui-même : reculer modifie légitimement toutes
                les parties vis-à-vis de la version annulée, ce n'est plus une
                édition incrémentale.

        Raises:
            GenerationError: copie invalide (partie perdue, partie non visée
                modifiée) — rien n'est déposé.
        """
        reference = self._octets_document(document) if precedent is None else precedent
        rapport = validation_docx.DocumentValidator().valider_edition(
            edition.octets,
            precedent=reference,
            parties_modifiees=edition.parties_modifiees,
            parties_ajoutees=edition.parties_ajoutees,
        )
        if not rapport.ok:
            raise GenerationError(
                "La copie éditée est invalide : " + " ; ".join(rapport.erreurs),
                details={"rapport": rapport.model_dump()},
            )
        metadonnees = dict(document.doc_metadata or {})
        metadonnees.update(
            {
                "role": "copie_modele",
                "ancre": sujet.ancre,
                "derniere_edition": nature,
                "edition": details,
                "controle": {
                    "ok": rapport.ok,
                    "parties_conservees": rapport.details.get("parties_conservees"),
                    "visuel": rapport.visuel,
                },
            }
        )
        nouveau = self._documents.deposer_livrable(
            DocumentUploadInput(
                type_document=document.type_document,
                nom=document.nom,
                stream=io.BytesIO(edition.octets),
                created_by=self._actor_id,
                proposed_by_agent=self._actor_id,
                doc_metadata=metadonnees,
                **{_CHAMP_ANCRE[sujet.ancre]: sujet.id},
            )
        )
        self._session.flush()
        return nouveau


def _texte_du_remplacement(contenu: Any) -> str:
    """Texte lisible d'un contenu de remplacement (chaîne ou segments)."""
    if contenu is None:
        return ""
    if isinstance(contenu, str):
        return contenu
    if isinstance(contenu, list):
        morceaux = []
        for segment in contenu:
            texte = getattr(segment, "texte", None)
            if texte is None and isinstance(segment, dict):
                texte = segment.get("texte")
            morceaux.append(str(texte or ""))
        return "".join(morceaux)
    return str(contenu)


def _rang_element(element_id: str) -> int:
    """Rang numérique d'un ``block_007`` (0 si l'identifiant n'a pas ce format)."""
    chiffres = "".join(caractere for caractere in element_id if caractere.isdigit())
    return int(chiffres) if chiffres else 0


def _nom_fichier_sur(valeur: str) -> str:
    """Nom de fichier sûr : accents retirés, séparateurs remplacés."""
    ascii_ = unicodedata.normalize("NFKD", valeur or "")
    ascii_ = ascii_.encode("ascii", "ignore").decode("ascii")
    nettoye = re.sub(r"[^A-Za-z0-9._-]+", "-", ascii_).strip("-.")
    return nettoye or "offre"
