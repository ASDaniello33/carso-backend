"""Service de la base de connaissance — bibliothèque, recherche, réglages.

Trois responsabilités, une même règle : **rien n'est deviné**.

- la **bibliothèque** (ajouter, corriger, retirer, réindexer) : une ligne
  PostgreSQL fait foi, le fichier d'origine reste conservé et indexable ;
- la **recherche** : la question est vectorisée par le modèle *effectif* et
  l'index est interrogé seulement si sa signature correspond — comparer des
  vecteurs de deux modèles produirait des scores faux en silence ;
- la **configuration d'embedding** : administrable à chaud, prioritaire sur le
  ``.env``. Un réglage n'est accepté qu'après un **appel réel** de mesure de la
  dimension ; changer de modèle déclenche la réindexation complète (l'index
  précédent est incompatible, on ne le garde pas « au cas où »).

Aucun secret ne sort d'ici : la clé du provider est chiffrée au repos et seule
son existence est exposée.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from pathlib import Path
from typing import Any, BinaryIO
from uuid import UUID, uuid4

from sqlalchemy import delete, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.application.trace import TraceContext
from app.connaissances.embeddings import (
    ConfigurationEmbeddingEffective,
    Embedder,
    EmbeddingIndisponibleError,
    construire_embedder,
    depuis_settings,
    sonder,
    valider_configuration,
)
from app.connaissances.indexation import ENTITY, IndexationConnaissances
from app.connaissances.lecture import EXTENSIONS_CONNAISSANCE
from app.connaissances.racine import racine_connaissances
from app.connaissances.stockage import StockageConnaissances
from app.connaissances.vecteurs import IndexVecteurs, obtenir_index
from app.core.chiffrement import chiffrement_disponible, chiffrer, dechiffrer
from app.core.config import Settings, get_settings
from app.core.errors import (
    CarsoError,
    ConflictError,
    NotFoundError,
)
from app.core.errors import (
    ValidationError as BusinessValidationError,
)
from app.domain.connaissance import (
    ChunkConnaissance,
    ConfigurationEmbedding,
    DocumentConnaissance,
)
from app.domain.enums import ActionAudit, ActorType, StatutIndexationConnaissance
from app.infrastructure.repositories import (
    ChunkConnaissanceRepository,
    ConfigurationEmbeddingRepository,
    DocumentConnaissanceRepository,
)

logger = logging.getLogger(__name__)

#: Plafond de passages renvoyés par une recherche, quel que soit le demandeur.
MAX_PASSAGES_RENVOYES = 20

#: Plafond de formulations par appel. Un agent regroupe les variantes d'une
#: même question (synonymes, sigle développé, autre langue) au lieu d'enchaîner
#: les appels : au-delà de cinq, l'appel n'est plus un regroupement mais une
#: exploration — le modèle doit alors poser sa question autrement.
MAX_QUESTIONS_PAR_RECHERCHE = 5


class ConnaissanceService:
    """Use cases de la base de connaissance (aucun accès disque direct ailleurs)."""

    def __init__(
        self,
        session: Session,
        actor_id: str | None = None,
        *,
        settings: Settings | None = None,
        racine: Path | None = None,
        index: IndexVecteurs | None = None,
        embedder: Embedder | None = None,
    ) -> None:
        """
        Args:
            session: session de l'appelant (transaction côté appelant).
            actor_id: auteur tracé des gestes humains.
            racine: répertoire des ouvrages (tests) ; sinon la configuration.
            index: index vectoriel injecté (tests) ; sinon l'index du processus.
            embedder: modèle d'embedding injecté (tests) ; sinon celui résolu
                depuis la base et l'environnement.
        """
        self._session = session
        self._cfg = settings or get_settings()
        self._racine = Path(racine) if racine is not None else racine_connaissances(self._cfg)
        self._index_injecte = index
        self._embedder_injecte = embedder
        self.documents = DocumentConnaissanceRepository(session)
        self.chunks = ChunkConnaissanceRepository(session)
        self.configurations = ConfigurationEmbeddingRepository(session)
        self._trace = TraceContext(session, actor_id=actor_id)

    # --- Lecture ----------------------------------------------------------

    def lister(
        self,
        *,
        recherche: str | None = None,
        categorie: str | None = None,
        statut: str | None = None,
        langue: str | None = None,
        limite: int = 500,
    ) -> list[DocumentConnaissance]:
        """Ouvrages filtrés (les plus récents d'abord)."""
        return self.documents.lister(
            recherche=recherche,
            categorie=categorie,
            statut=statut,
            langue=langue,
            limite=limite,
        )

    def obtenir(self, document_id: UUID) -> DocumentConnaissance:
        """Un ouvrage, ou ``NotFoundError`` (jamais un ``None`` silencieux)."""
        document = self.documents.get(document_id)
        if document is None:
            raise NotFoundError(
                "Ouvrage inconnu de la base de connaissance",
                details={"document_id": str(document_id)},
            )
        return document

    def telecharger(self, document_id: UUID) -> tuple[str, Path]:
        """Nom logique et chemin physique d'un ouvrage, pour un téléchargement.

        Le chemin n'est **pas** renvoyé par l'API : il ne sert qu'à
        ``FileResponse`` (AGENTS.md §9).
        """
        from app.connaissances.racine import resoudre_fichier

        document = self.obtenir(document_id)
        chemin = resoudre_fichier(self._racine, document.chemin_stockage)
        if not chemin.is_file():
            raise NotFoundError(
                "Le fichier de cet ouvrage est absent du répertoire géré",
                details={"document_id": str(document_id)},
            )
        return document.nom_fichier, chemin

    def extraits(self, document_id: UUID, *, limite: int = 5) -> list[ChunkConnaissance]:
        """Premiers passages d'un ouvrage (aperçu lisible de l'indexation)."""
        document = self.obtenir(document_id)
        return sorted(document.chunks, key=lambda chunk: chunk.position)[:limite]

    def etat(self) -> dict[str, Any]:
        """Tableau de bord : volumétrie, index, configuration effective."""
        ouvrages, octets = self.documents.totaux()
        par_statut = self.documents.compter_par_statut()
        langues = self.documents.langues()
        categories = self.documents.categories()
        etat: dict[str, Any] = {
            "ouvrages": ouvrages,
            "passages": self.chunks.compter(),
            "octets": octets,
            "par_statut": par_statut,
            "langues": langues,
            "categories": categories,
            "formats_supportes": sorted(EXTENSIONS_CONNAISSANCE),
        }
        try:
            configuration = self._configuration_pour_affichage()
            etat["configuration"] = configuration.resume()
            etat["configure"] = True
        except CarsoError as exc:
            etat["configuration"] = None
            etat["configure"] = False
            etat["message_configuration"] = str(exc)
        ligne = self.configurations.get_active()
        etat["configuration_administree"] = ligne.resume() if ligne is not None else None
        etat["index"] = self._etat_index(etat.get("configuration"))
        return etat

    def _etat_index(
        self, configuration: dict[str, Any] | None
    ) -> dict[str, Any]:
        """État de l'index vectoriel, sans jamais faire échouer l'affichage."""
        try:
            index = self._index()
            signature = index.signature()
            passages = index.compter()
        except CarsoError as exc:
            return {"disponible": False, "message": str(exc)}
        attendu = (configuration or {}).get("signature")
        return {
            "disponible": True,
            "signature": signature,
            "passages": passages,
            "coherent": bool(signature) and (attendu is None or signature == attendu),
        }

    # --- Écriture ---------------------------------------------------------

    def ajouter(
        self,
        *,
        flux: BinaryIO,
        nom_fichier: str,
        titre: str,
        auteur: str | None = None,
        editeur: str | None = None,
        annee: int | None = None,
        categorie: str | None = None,
        langue: str = "fr",
        description: str | None = None,
        mots_cles: Sequence[str] = (),
        ajoute_par_id: UUID | None = None,
    ) -> DocumentConnaissance:
        """Dépose un ouvrage et le met en file d'indexation.

        L'indexation est **asynchrone** : la réponse revient dès que le fichier
        est stocké et la fiche écrite (un livre de 800 pages ne se vectorise pas
        en une requête HTTP). L'état est visible dans la fiche (``en_attente``).

        Raises:
            BusinessValidationError: titre manquant, année hors bornes, format
                non indexable, fichier vide ou trop volumineux.
            ConflictError: ce contenu exact est déjà dans la bibliothèque.
        """
        titre_propre = " ".join((titre or "").split())
        if len(titre_propre) < 2:
            raise BusinessValidationError(
                "Titre d'ouvrage manquant (2 caractères minimum)",
                details={"champ": "titre"},
            )
        if annee is not None and not 1000 <= int(annee) <= 2200:
            raise BusinessValidationError(
                "Année hors bornes (1000 à 2200)", details={"champ": "annee"}
            )
        langue_propre = (langue or "fr").strip().lower()[:10] or "fr"
        mots = sorted({" ".join(mot.split()) for mot in mots_cles if mot and mot.strip()})[:20]

        identifiant = uuid4()
        stockage = StockageConnaissances(
            self._racine, max_bytes=self._cfg.knowledge_max_upload_bytes
        )
        fichier = stockage.depot(
            document_id=str(identifiant), nom_fichier=nom_fichier, flux=flux
        )

        existant = self.documents.get_par_checksum(fichier.checksum_sha256)
        if existant is not None:
            self._retirer_fichier(fichier.chemin_logique)
            raise ConflictError(
                "Ce fichier est déjà présent dans la base de connaissance",
                details={"titre": existant.titre, "document_id": str(existant.id)},
            )

        document = DocumentConnaissance(
            id=identifiant,
            titre=titre_propre,
            auteur=(auteur or "").strip()[:255] or None,
            editeur=(editeur or "").strip()[:255] or None,
            annee=int(annee) if annee is not None else None,
            categorie=(categorie or "").strip()[:100] or None,
            langue=langue_propre,
            description=(description or "").strip() or None,
            mots_cles=list(mots),
            nom_fichier=fichier.nom_fichier,
            chemin_stockage=fichier.chemin_logique,
            mime_type=fichier.mime_type,
            taille_octets=fichier.taille_octets,
            checksum_sha256=fichier.checksum_sha256,
            statut=StatutIndexationConnaissance.EN_ATTENTE.value,
            ajoute_par_id=ajoute_par_id,
        )
        self.documents.add(document)
        try:
            self._session.flush()
        except IntegrityError as exc:
            # Deux dépôts simultanés du même fichier : la contrainte unique a
            # parlé en premier — le message reste celui d'un doublon, pas d'une
            # erreur technique.
            self._retirer_fichier(fichier.chemin_logique)
            raise ConflictError(
                "Ce fichier est déjà présent dans la base de connaissance",
                details={"nom_fichier": fichier.nom_fichier},
            ) from exc

        self._trace.record_event(
            actor_type=ActorType.HUMAIN.value,
            action=ActionAudit.CONNAISSANCE_AJOUTEE.value,
            entity_type=ENTITY,
            entity_id=document.id,
            after=document.resume(),
        )
        declencher_indexation()
        return document

    def modifier(
        self,
        document_id: UUID,
        *,
        titre: str | None = None,
        auteur: str | None = None,
        editeur: str | None = None,
        annee: int | None = None,
        categorie: str | None = None,
        langue: str | None = None,
        description: str | None = None,
        mots_cles: Sequence[str] | None = None,
    ) -> DocumentConnaissance:
        """Corrige la fiche d'un ouvrage (le fichier et les passages ne bougent pas).

        Les champs absents gardent leur valeur ; une chaîne vide efface le champ.
        La catégorie et la langue modifiées sont aussi recopiées dans le
        **payload** de l'index, pour que les filtres de recherche suivent.
        """
        document = self.obtenir(document_id)
        avant = document.resume()
        changements: list[str] = []

        if titre is not None:
            propre = " ".join(titre.split())
            if len(propre) < 2:
                raise BusinessValidationError(
                    "Titre d'ouvrage manquant (2 caractères minimum)",
                    details={"champ": "titre"},
                )
            document.titre = propre
            changements.append("titre")
        if auteur is not None:
            document.auteur = auteur.strip()[:255] or None
            changements.append("auteur")
        if editeur is not None:
            document.editeur = editeur.strip()[:255] or None
            changements.append("editeur")
        if annee is not None:
            if not 1000 <= int(annee) <= 2200:
                raise BusinessValidationError(
                    "Année hors bornes (1000 à 2200)", details={"champ": "annee"}
                )
            document.annee = int(annee)
            changements.append("annee")
        if categorie is not None:
            document.categorie = categorie.strip()[:100] or None
            changements.append("categorie")
        if langue is not None:
            document.langue = langue.strip().lower()[:10] or "fr"
            changements.append("langue")
        if description is not None:
            document.description = description.strip() or None
            changements.append("description")
        if mots_cles is not None:
            document.mots_cles = list(
                sorted({" ".join(mot.split()) for mot in mots_cles if mot and mot.strip()})[:20]
            )
            changements.append("mots_cles")

        if not changements:
            return document
        self._session.flush()
        self._trace.record_event(
            actor_type=ActorType.HUMAIN.value,
            action=ActionAudit.CONNAISSANCE_MODIFIEE.value,
            entity_type=ENTITY,
            entity_id=document.id,
            before=avant,
            after=document.resume(),
            event_metadata={"champs": changements},
        )
        return document

    def supprimer(self, document_id: UUID) -> int:
        """Retire un ouvrage : vecteurs, passages, fiche, puis fichier.

        L'inventaire de ce qui disparaît est conservé dans le journal d'audit
        (titre, auteur, nombre de passages) — le texte indexé, lui, n'est pas
        recopié dans une trace.

        Returns:
            Nombre de passages supprimés.
        """
        document = self.obtenir(document_id)
        avant = {
            **document.resume(),
            "auteur": document.auteur,
            "taille_octets": document.taille_octets,
        }
        chemin = document.chemin_stockage
        try:
            self._index().supprimer(str(document.id))
        except CarsoError as exc:
            # Un index indisponible ne bloque pas le retrait de la fiche : sans
            # elle, l'index n'est plus interrogé. L'erreur reste journalisée.
            logger.warning("Retrait des vecteurs impossible (%s)", exc)
        passages = self.chunks.supprimer_document(document.id)
        self._session.delete(document)
        self._session.flush()
        self._retirer_fichier(chemin)
        self._trace.record_event(
            actor_type=ActorType.HUMAIN.value,
            action=ActionAudit.CONNAISSANCE_SUPPRIMEE.value,
            entity_type=ENTITY,
            entity_id=document_id,
            before=avant,
            after={"passages_supprimes": passages},
        )
        return passages

    def reindexer(self, document_id: UUID) -> DocumentConnaissance:
        """Remet un ouvrage en file d'indexation (sans toucher aux passages actuels)."""
        document = self.obtenir(document_id)
        document.statut = StatutIndexationConnaissance.EN_ATTENTE.value
        document.erreur = None
        self._session.flush()
        self._trace.record_event(
            actor_type=ActorType.HUMAIN.value,
            action=ActionAudit.CONNAISSANCE_REINDEXATION_DEMANDEE.value,
            entity_type=ENTITY,
            entity_id=document.id,
            after={"titre": document.titre, "statut": document.statut},
        )
        declencher_indexation()
        return document

    def reindexer_tout(self) -> int:
        """Replanifie l'indexation de **toute** la bibliothèque.

        Geste d'administration : il ne supprime rien, il redemande l'indexation.
        Les passages existants restent interrogeables jusqu'au remplacement.
        """
        resultat = self._session.execute(
            update(DocumentConnaissance)
            .values(statut=StatutIndexationConnaissance.EN_ATTENTE.value, erreur=None)
            .execution_options(synchronize_session=False)
        )
        # ``Result`` générique pour mypy : le nombre de lignes est porté par le
        # curseur, présent sur tous les moteurs utilisés (PG, SQLite).
        total = int(getattr(resultat, "rowcount", 0) or 0)
        self._trace.record_event(
            actor_type=ActorType.HUMAIN.value,
            action=ActionAudit.CONNAISSANCE_REINDEXATION_DEMANDEE.value,
            entity_type=ENTITY,
            entity_id=None,
            after={"ouvrages": total},
            event_metadata={"portee": "toute la base"},
        )
        declencher_indexation()
        return total

    # --- Recherche --------------------------------------------------------

    def rechercher(
        self,
        question: str,
        *,
        limite: int = 5,
        document_id: UUID | None = None,
        categorie: str | None = None,
    ) -> dict[str, Any]:
        """Recherche les passages les plus proches d'**une** question.

        Même mécanique que :meth:`rechercher_plusieurs`, projetée sur une seule
        formulation : le contrat HTTP ne change pas, mais il n'existe qu'un
        chemin de recherche.

        Returns:
            ``{question, nombre, passages, signature, message}`` — chaque passage
            porte sa source (titre, auteur, page, section) et sa similarité.

        Raises:
            BusinessValidationError: question vide.
            ConflictError: l'index a été construit avec un autre modèle —
                réindexer avant de chercher (résultats sinon faux en silence).
            NotFoundError: aucun index/ouvrage indexé.
            EmbeddingIndisponibleError: modèle d'embedding inutilisable.
        """
        lot = self.rechercher_plusieurs(
            [question], limite=limite, document_id=document_id, categorie=categorie
        )
        groupe = lot["requetes"][0]
        return {
            "question": groupe["query"],
            "nombre": groupe["nombre"],
            "passages": groupe["passages"],
            "signature": lot["signature"],
            "message": groupe["message"],
        }

    def rechercher_plusieurs(
        self,
        questions: Sequence[str],
        *,
        limite: int = 5,
        document_id: UUID | None = None,
        categorie: str | None = None,
    ) -> dict[str, Any]:
        """Recherche **plusieurs formulations** en un seul passage.

        Un agent qui doit couvrir « définition de l'entrepreneur » et
        « entrepreneuriat » n'enchaîne pas deux appels : les formulations
        partent ensemble — **une** requête d'embedding, **un** contrôle de
        signature — et les passages reviennent groupés par formulation, dans
        l'ordre demandé. C'est ce qui coupe la boucle de reformulation : le
        modèle formule une fois, reçoit tout, puis conclut.

        Args:
            questions: une à ``MAX_QUESTIONS_PAR_RECHERCHE`` formulations ; un
                doublon exact (espaces normalisés) est fusionné.
            limite: passages **par** formulation (resserré par la configuration).

        Returns:
            ``{requetes: [{query, nombre, passages, message}], signature,
            total_passages, message}``.

        Raises:
            BusinessValidationError: aucune formulation exploitable, ou plus
                que le plafond par appel.
            ConflictError: l'index a été construit avec un autre modèle.
            NotFoundError: aucun index/ouvrage indexé.
            EmbeddingIndisponibleError: modèle d'embedding inutilisable.
        """
        textes: list[str] = []
        for question in questions:
            texte = " ".join((question or "").split())
            if len(texte) < 2:
                raise BusinessValidationError(
                    "Formulation de recherche vide", details={"champ": "query"}
                )
            if texte not in textes:
                textes.append(texte)
        if not textes:
            raise BusinessValidationError(
                "Aucune formulation de recherche fournie", details={"champ": "query"}
            )
        if len(textes) > MAX_QUESTIONS_PAR_RECHERCHE:
            raise BusinessValidationError(
                f"{len(textes)} formulations demandées : en regrouper au plus "
                f"{MAX_QUESTIONS_PAR_RECHERCHE} par appel (synonymes, sigle "
                "développé, autre langue dans la même liste)",
                details={"champ": "query", "maximum": MAX_QUESTIONS_PAR_RECHERCHE},
            )
        borne = max(
            1,
            min(
                int(limite),
                MAX_PASSAGES_RENVOYES,
                self._cfg.knowledge_recherche_max_resultats,
            ),
        )
        # Le modèle qui vectorise **est** celui dont la signature contrôle
        # l'index : jamais deux sources de vérité qui pourraient diverger.
        embedder = self._embedder()
        configuration = embedder.configuration
        index = self._index()
        signature_index = index.signature()
        if signature_index and signature_index != configuration.signature:
            raise ConflictError(
                "L'index de la base de connaissance a été construit avec un autre "
                "modèle d'embedding : réindexer avant de rechercher",
                details={"index": signature_index, "attendu": configuration.signature},
            )

        # Un seul appel d'embedding pour toutes les formulations : le coût d'un
        # regroupement n'est pas la somme de ses appels.
        vecteurs = embedder.vectoriser(textes)
        requetes: list[dict[str, Any]] = []
        for texte, vecteur in zip(textes, vecteurs, strict=True):
            resultats = index.rechercher(
                vecteur,
                limite=borne,
                document_id=str(document_id) if document_id else None,
                categorie=categorie or None,
            )
            passages = self._hydrater(resultats)
            requetes.append(
                {
                    "query": texte,
                    "nombre": len(passages),
                    "passages": passages,
                    "message": (
                        None
                        if passages
                        else "Aucun passage pertinent pour cette formulation."
                    ),
                }
            )
        total = sum(groupe["nombre"] for groupe in requetes)
        return {
            "requetes": requetes,
            "signature": signature_index or configuration.signature,
            "total_passages": total,
            "message": None
            if total
            else "Aucun passage pertinent trouvé dans la bibliothèque.",
        }

    def _hydrater(self, resultats: Sequence[Any]) -> list[dict[str, Any]]:
        """Relit les passages **en base** (le texte fait foi, pas le payload)."""
        identifiants = []
        for resultat in resultats:
            try:
                identifiants.append(UUID(str(resultat.chunk_id)))
            except ValueError:
                continue
        lignes = self.chunks.par_ids(identifiants)
        titres: dict[UUID, DocumentConnaissance] = {}
        passages: list[dict[str, Any]] = []
        for resultat in resultats:
            try:
                identifiant = UUID(str(resultat.chunk_id))
            except ValueError:
                continue
            chunk = lignes.get(identifiant)
            if chunk is None:
                continue
            document = titres.get(chunk.document_id)
            if document is None:
                document = self.documents.get(chunk.document_id)
                if document is None:
                    continue
                titres[chunk.document_id] = document
            passages.append(
                {
                    "chunk_id": str(chunk.id),
                    "document_id": str(document.id),
                    "titre": document.titre,
                    "auteur": document.auteur,
                    "categorie": document.categorie,
                    "langue": document.langue,
                    "page": chunk.page,
                    "section": chunk.section,
                    "position": chunk.position,
                    "similarite": round(float(resultat.score), 4),
                    "extrait": chunk.contenu,
                }
            )
        return passages

    # --- Configuration d'embedding ---------------------------------------

    def configuration_effective(self) -> ConfigurationEmbeddingEffective:
        """Modèle appliqué (administration, sinon environnement), sans secret exposé."""
        return self._configuration_effective()

    def tester_configuration(
        self,
        *,
        provider: str,
        model: str,
        base_url: str | None = None,
        api_key: str | None = None,
    ) -> dict[str, Any]:
        """Exécute un **vrai** appel d'embedding et rend la dimension mesurée.

        C'est le contrôle qui rend la signature fiable : la dimension est
        observée, jamais déduite d'un nom de modèle.
        """
        provider_n, model_n, base_url_n = valider_configuration(
            provider=provider, model=model, base_url=base_url
        )
        cle = self._cle_disponible(api_key)
        configuration = ConfigurationEmbeddingEffective(
            provider=provider_n,
            model=model_n,
            base_url=base_url_n,
            dimensions=0,
            api_key=cle,
            source="test",
        )
        sondage = sonder(configuration)
        return {
            "provider": provider_n,
            "model": model_n,
            "dimensions": sondage.dimensions,
            "signature": sondage.signature,
            "latence_ms": sondage.latence_ms,
        }

    def configurer(
        self,
        *,
        provider: str,
        model: str,
        base_url: str | None = None,
        api_key: str | None = None,
        conserver_cle: bool = True,
        modifie_par: str | None = None,
    ) -> tuple[ConfigurationEmbedding, bool]:
        """Enregistre le modèle d'embedding après un appel réel de validation.

        Returns:
            ``(ligne, reindexation_requise)`` — la réindexation est requise quand
            la signature change (les vecteurs de l'ancien modèle ne sont pas
            comparables aux nouveaux).

        Raises:
            EmbeddingIndisponibleError: configuration refusée par le provider.
            BusinessValidationError: clé fournie sans chiffrement disponible.
            CarsoError: index vectoriel indisponible pour la réinitialisation.
        """
        provider_n, model_n, base_url_n = valider_configuration(
            provider=provider, model=model, base_url=base_url
        )
        precedente = self.configurations.get_active()
        cle_chiffree, cle_claire = self._preparer_cle(
            api_key=api_key, conserver_cle=conserver_cle, precedente=precedente
        )
        sondage = sonder(
            ConfigurationEmbeddingEffective(
                provider=provider_n,
                model=model_n,
                base_url=base_url_n,
                dimensions=0,
                api_key=cle_claire,
                source="administration",
            )
        )

        avant: ConfigurationEmbeddingEffective | None
        try:
            avant = self._configuration_effective()
        except CarsoError:
            avant = None
        changera = avant is None or avant.signature != sondage.signature

        if precedente is not None:
            precedente.actif = False
        ligne = ConfigurationEmbedding(
            provider=provider_n,
            model=model_n,
            base_url=base_url_n,
            dimensions=sondage.dimensions,
            signature=sondage.signature,
            api_key_chiffree=cle_chiffree,
            actif=True,
            modifie_par=modifie_par,
        )
        self.configurations.add(ligne)
        self._session.flush()

        if changera:
            self._preparer_reindexation(
                signature=sondage.signature,
                dimensions=sondage.dimensions,
                raison="changement de modèle d'embedding",
            )
        self._trace.record_event(
            actor_type=ActorType.HUMAIN.value,
            action=ActionAudit.CONFIGURATION_EMBEDDING_MODIFIEE.value,
            entity_type="configuration_embedding",
            entity_id=ligne.id,
            after={**ligne.resume(), "reindexation_requise": changera},
            event_metadata={"avant": avant.resume() if avant is not None else None},
        )
        declencher_indexation()
        return ligne, changera

    def desactiver_configuration(self) -> bool:
        """Revient au modèle du ``.env`` (la décision est mémorisée).

        Raises:
            BusinessValidationError: le ``.env`` ne décrit aucun modèle
                exploitable — revenir dessus rendrait la recherche impossible.
        """
        precedente = self.configurations.get_active()
        if precedente is None:
            return False
        environnement = depuis_settings(self._cfg)
        valider_configuration(
            provider=environnement.provider,
            model=environnement.model,
            base_url=environnement.base_url,
        )
        if environnement.dimensions <= 0:
            raise BusinessValidationError(
                "Le modèle du .env n'a pas de dimensions mesurées "
                "(EMBEDDING_DIMENSIONS) : mesurer et enregistrer le modèle depuis "
                "cette page plutôt que de revenir au déploiement.",
                details={"champ": "embedding_dimensions"},
            )
        avant = self._configuration_effective()
        precedente.actif = False
        self._session.flush()
        changera = avant.signature != environnement.signature
        if changera:
            self._preparer_reindexation(
                signature=environnement.signature,
                dimensions=environnement.dimensions,
                raison="retour au modèle du .env",
            )
        self._trace.record_event(
            actor_type=ActorType.HUMAIN.value,
            action=ActionAudit.CONFIGURATION_EMBEDDING_MODIFIEE.value,
            entity_type="configuration_embedding",
            entity_id=precedente.id,
            after={**environnement.resume(), "actif": True, "reindexation_requise": changera},
            event_metadata={"avant": avant.resume(), "raison": "retour au .env"},
        )
        declencher_indexation()
        return True

    def _preparer_cle(
        self,
        *,
        api_key: str | None,
        conserver_cle: bool,
        precedente: ConfigurationEmbedding | None,
    ) -> tuple[str | None, str | None]:
        """Chiffre la clé fournie (ou reprend celle déjà enregistrée).

        Returns:
            ``(clé chiffrée à persister, clé en clair pour l'appel de mesure)``.
        """
        if api_key and api_key.strip():
            if not chiffrement_disponible():
                raise BusinessValidationError(
                    "Aucune clé de chiffrement configurée : renseigner "
                    "PARAMETRES_CHIFFREMENT_KEY (ou AUTH_SECRET) avant d'enregistrer "
                    "une clé d'embedding.",
                    details={"champ": "api_key"},
                )
            propre = api_key.strip()
            return chiffrer(propre), propre
        if conserver_cle and precedente is not None and precedente.api_key_chiffree:
            try:
                return precedente.api_key_chiffree, dechiffrer(precedente.api_key_chiffree)
            except CarsoError:
                logger.warning(
                    "Clé d'embedding administrée illisible (clé de chiffrement changée) : "
                    "repli sur la clé du .env"
                )
        return None, self._cle_disponible(None)

    def _cle_disponible(self, api_key: str | None) -> str | None:
        """Clé en clair à utiliser : fournie, sinon celle de l'environnement."""
        if api_key and api_key.strip():
            return api_key.strip()
        environnement = depuis_settings(self._cfg)
        return environnement.api_key

    def _configuration_pour_affichage(self) -> ConfigurationEmbeddingEffective:
        """Configuration à montrer : le modèle injecté prime (tests, usage avancé)."""
        if self._embedder_injecte is not None:
            return self._embedder_injecte.configuration
        return self._configuration_effective()

    def _configuration_effective(self) -> ConfigurationEmbeddingEffective:
        """Configuration appliquée, validée (dimensions mesurées incluses)."""
        ligne = self.configurations.get_active()
        if ligne is None:
            configuration = depuis_settings(self._cfg)
            valider_configuration(
                provider=configuration.provider,
                model=configuration.model,
                base_url=configuration.base_url,
            )
            if configuration.dimensions <= 0:
                raise EmbeddingIndisponibleError(
                    "Le modèle d'embedding n'est pas mesuré : l'enregistrer depuis "
                    "Paramètres → Base de connaissance (un appel réel mesure la "
                    "dimension), ou renseigner EMBEDDING_DIMENSIONS pour le déploiement.",
                    details={"champ": "dimensions"},
                )
            return configuration
        cle: str | None = None
        if ligne.api_key_chiffree:
            try:
                cle = dechiffrer(ligne.api_key_chiffree)
            except CarsoError:
                logger.warning(
                    "Clé d'embedding administrée illisible : repli sur la clé du .env"
                )
        return ConfigurationEmbeddingEffective(
            provider=ligne.provider,
            model=ligne.model,
            base_url=ligne.base_url,
            dimensions=int(ligne.dimensions),
            api_key=cle or self._cle_disponible(None),
            source="administration",
        )

    def _preparer_reindexation(
        self, *, signature: str, dimensions: int, raison: str
    ) -> None:
        """Recrée l'index pour un nouveau modèle et replanifie toute la base.

        Les passages en base sont supprimés : ils portent la signature de
        l'ancien modèle et ne peuvent pas être comparés aux nouveaux vecteurs.
        Le fichier d'origine, lui, reste en place — la réindexation le relit.
        """
        self._index().reinitialiser(signature, dimensions)
        self._session.execute(delete(ChunkConnaissance))
        self._session.execute(
            update(DocumentConnaissance)
            .values(
                statut=StatutIndexationConnaissance.EN_ATTENTE.value,
                erreur=None,
                nombre_chunks=0,
                signature_embedding=None,
                date_indexation=None,
            )
            .execution_options(synchronize_session=False)
        )
        self._trace.record_event(
            actor_type=ActorType.HUMAIN.value,
            action=ActionAudit.CONNAISSANCE_REINDEXATION_DEMANDEE.value,
            entity_type=ENTITY,
            entity_id=None,
            after={"signature": signature, "raison": raison},
            event_metadata={"portee": "toute la base", "modele": signature},
        )

    # --- Outils internes --------------------------------------------------

    def _index(self) -> IndexVecteurs:
        """Index vectoriel injecté (tests) ou celui du processus."""
        if self._index_injecte is not None:
            return self._index_injecte
        return obtenir_index()

    def _embedder(self) -> Embedder:
        """Modèle d'embedding injecté (tests) ou celui de la configuration."""
        if self._embedder_injecte is not None:
            return self._embedder_injecte
        return construire_embedder(self._configuration_effective())

    def indexer_maintenant(self, document_id: UUID) -> Any:
        """Indexe **immédiatement** un ouvrage (tests et réparation ponctuelle).

        Le worker de fond emprunte le même chemin : cette méthode ne change
        aucune règle, elle évite seulement d'attendre le tour suivant.
        """
        document = self.obtenir(document_id)
        indexation = IndexationConnaissances(
            self._session,
            embedder=self._embedder(),
            index=self._index(),
            racine=self._racine,
            settings=self._cfg,
        )
        return indexation.indexer(document)

    def _retirer_fichier(self, chemin_logique: str) -> None:
        """Supprime le fichier d'un ouvrage — jamais d'échec bloquant."""
        try:
            from app.connaissances.racine import resoudre_fichier

            resoudre_fichier(self._racine, chemin_logique).unlink(missing_ok=True)
        except (CarsoError, OSError) as exc:
            logger.warning("Fichier de connaissance non supprimé (%s)", exc)


def resoudre_embedder(
    session: Session, *, settings: Settings | None = None
) -> Embedder:
    """Embedder effectif hors service (worker d'indexation)."""
    return ConnaissanceService(session, settings=settings)._embedder()


def declencher_indexation() -> None:
    """Réveille le worker d'indexation sans attendre son tour périodique.

    Import tardif : le service ne dépend pas de l'infrastructure au chargement
    (même précaution que ``app.agents.toolkit`` avec les services).
    """
    try:
        from app.infrastructure.indexation_connaissances import reveiller

        reveiller()
    except Exception:  # noqa: BLE001 — un réveil raté n'empêche pas l'ajout
        logger.debug("Réveil du worker d'indexation impossible (mode test ?)")


__all__ = [
    "MAX_PASSAGES_RENVOYES",
    "MAX_QUESTIONS_PAR_RECHERCHE",
    "ConnaissanceService",
    "declencher_indexation",
    "resoudre_embedder",
]
