"""Téléchargement, aperçu, lecture de plage et résolution de dossier.

Le fichier n'est servi qu'après contrôle du périmètre, comme avant le
découpage : la garde est portée par le socle, pas par ce module.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any
from uuid import UUID

from app.application.dto import (
    ApercuDocument,
    ExtractedDocument,
    LigneLecturePlage,
    PageApercuDocument,
)
from app.application.services.document_service_commun import (
    _ANCHORS,
    CHAMP_PAR_ANCRE,
    _verifier_unite,
)
from app.core.config import get_settings
from app.core.errors import NotFoundError, ValidationError
from app.documents.extraction import extract_text
from app.documents.paths import extension_of, versioned_filename
from app.documents.previews import enregistrer_apercu, resoudre_apercu
from app.documents.rasterize import pages_pdf, rasteriser_pdf
from app.domain.document import Document
from app.domain.enums import StatutDocument


class AccesDocumentMixin:
    """See ``document_service`` pour le socle partagé."""

    def telecharger(self, document_id: UUID) -> tuple[str, Path]:
        """Nom de fichier proposé au téléchargement + chemin physique contrôlé.

        Le chemin physique n'est jamais renvoyé par l'API : il est consommé
        directement par la réponse de fichier.
        """
        courant = self._get_fichier(document_id)
        chemin = self.storage.resolve_existing(courant.storage_path)
        nom_fichier = versioned_filename(courant.nom, courant.version)
        return nom_fichier, chemin

    def apercu_document(
        self,
        document_id: UUID,
        *,
        pages: Sequence[int] | None = None,
        dpi: int | None = None,
    ) -> ApercuDocument:
        """Rend les pages d'un document en images inspectables (aperçu visuel).

        Le document doit être un **PDF** : sans moteur bureautique (LibreOffice)
        sur l'hôte, un DOCX ou un XLSX n'est pas convertible — l'erreur est
        explicite et indique qu'il faut la version PDF du document (celle que
        produit la génération d'offre).

        Images écrites **à côté du document** (``previews/<tige>/pNN.png``),
        jamais réécrites : l'aperçu d'une version immuable est stable, et un
        second appel ne retravaille rien (idempotent).

        Args:
            document_id: document PDF à prévisualiser.
            pages: numéros de pages (1-based) ; ``None`` = depuis la première.
            dpi: résolution, par défaut ``settings.previews_dpi``.

        Raises:
            ValidationError: document non PDF (conversion indisponible).
            NotFoundError: document ou fichier absent.
            GenerationError: poppler absent, PDF illisible.
        """
        courant = self._get_fichier(document_id)
        extension = extension_of(courant.nom)
        if extension != "pdf":
            msg = (
                f"Aperçu visuel impossible pour un fichier .{extension} : la "
                "conversion bureautique n'est pas disponible sur ce serveur. "
                "Générer (ou joindre) la version PDF du document, puis demander "
                "son aperçu."
            )
            raise ValidationError(
                msg, details={"document_id": str(document_id), "extension": extension}
            )

        settings = get_settings()
        resolution = dpi or settings.previews_dpi
        plafond = settings.previews_max_pages
        chemin = self.storage.resolve_existing(courant.storage_path)
        total = pages_pdf(chemin)
        rendues = rasteriser_pdf(
            chemin,
            dpi=resolution,
            pages=pages,
            max_pages=plafond,
            outil=settings.pdftoppm_path,
        )

        apercus: list[PageApercuDocument] = []
        for page in rendues:
            publiee = enregistrer_apercu(
                self.storage,
                courant.storage_path,
                page=page.numero,
                octets=page.octets,
                largeur_px=page.largeur_px,
                hauteur_px=page.hauteur_px,
            )
            apercus.append(
                PageApercuDocument(
                    numero=publiee.numero,
                    chemin_logique=publiee.chemin_logique,
                    largeur_px=publiee.largeur_px,
                    hauteur_px=publiee.hauteur_px,
                    cree=publiee.cree,
                )
            )
        return ApercuDocument(
            document_id=courant.id,
            nom=courant.nom,
            nb_pages=total if total is not None else len(rendues),
            dpi=resolution,
            pages=tuple(apercus),
            tronque=total is not None and total > len(rendues),
        )

    def chemin_page_apercu(self, document_id: UUID, page: int) -> tuple[str, Path]:
        """Nom de fichier proposé au navigateur + chemin physique d'une page d'aperçu.

        Même contrat que ``telecharger`` : le chemin physique n'est jamais
        renvoyé par l'API, il est consommé par la réponse de fichier.

        Raises:
            NotFoundError: document absent, ou page jamais rendue.
        """
        courant = self._get_fichier(document_id)
        chemin = resoudre_apercu(self.storage, courant.storage_path, page)
        nom_fichier = f"{Path(courant.nom).stem}-p{int(page):02d}.png"
        return nom_fichier, chemin

    def lire_plage(
        self,
        document_id: UUID,
        *,
        from_page: int | None = None,
        to_page: int | None = None,
        unite: str | None = None,
        feuille: str | None = None,
        max_chars: int | None = None,
    ) -> LigneLecturePlage:
        """Lit une **plage** de document (pages PDF, éléments DOCX, lignes XLSX).

        Lecture seule, bornée : c'est le chemin attendu pour un document volumineux
        (un appel de 80 pages ne se lit pas d'un bloc). ``unite``
        n'est jamais devinée — c'est le format qui décide, et la réponse le dit.

        Args:
            from_page: premier élément de la plage (1-indexé, inclusif).
            to_page: dernier élément inclusif ; borné à la fin du document.
            unite: ``pages`` | ``elements`` | ``lignes`` — contrôle explicite
                (une unité qui ne correspond pas au format est refusée ; un DOCX
                se lit par **éléments**, tableaux compris).
            feuille: nom de feuille XLSX (sans effet sur les autres formats).
            max_chars: plafond de caractères (défaut : réglage d'extraction).

        Raises:
            NotFoundError: document ou fichier absent.
            ValidationError: format sans lecture par plage, ou unité incohérente.
        """
        from app.documents.inspection import lire_docx_plage, lire_pdf_plage, lire_xlsx_plage

        courant = self._get_fichier(document_id)
        chemin = self.storage.resolve_existing(courant.storage_path)
        suffixe = chemin.suffix.lower()
        plafond = max_chars or self._max_chars

        if suffixe == ".pdf":
            _verifier_unite(unite, "pages", courant.nom)
            brut = lire_pdf_plage(chemin, from_page=from_page, to_page=to_page)
        elif suffixe == ".docx":
            _verifier_unite(unite, "elements", courant.nom)
            brut = lire_docx_plage(chemin, from_page=from_page, to_page=to_page)
        elif suffixe == ".xlsx":
            _verifier_unite(unite, "lignes", courant.nom)
            brut = lire_xlsx_plage(
                chemin, sheet=feuille, from_row=from_page, to_row=to_page
            )
        else:
            raise ValidationError(
                f"Lecture par plage non supportée pour {courant.nom} "
                "(formats : PDF, DOCX, XLSX)",
                details={"extension": suffixe},
            )

        texte = str(brut.get("texte") or "")
        tronque = len(texte) > plafond
        if tronque:
            texte = texte[:plafond]
        total = (
            brut.get("total_pages") or brut.get("total_elements") or brut.get("total_lignes")
        )
        return LigneLecturePlage(
            document_id=courant.id,
            nom=courant.nom,
            unite=str(brut.get("unite") or "pages"),
            debut=int(brut.get("debut") or 1),
            fin=int(brut.get("fin") or 1),
            total=int(total) if total else None,
            texte=texte,
            tronque=tronque or bool(brut.get("tronque")),
        )

    def extraire_texte(
        self, document_id: UUID, *, max_chars: int | None = None
    ) -> ExtractedDocument:
        """Extrait le texte d'un document (**lecture seule**, rien n'est persisté).

        C'est le point d'entrée que consommera ``AgentAnalyseurAppel`` : il
        obtient du texte, jamais un chemin de fichier ni un accès disque.

        ``max_chars`` (incrément 53) borne le travail pour un **aperçu** :
        ``lire_document`` lit le début du document sans extraire 200 000
        caractères. Sans valeur, le plafond du service s'applique (inchangé).
        """
        courant = self._get_fichier(document_id)
        chemin = self.storage.resolve_existing(courant.storage_path)
        plafond = max_chars or self._max_chars
        contenu = extract_text(chemin, nom_fichier=courant.nom, max_chars=plafond)

        return ExtractedDocument(
            document_id=courant.id,
            nom=courant.nom,
            extension=extension_of(courant.nom),
            mime_type=courant.mime_type,
            texte=contenu.texte,
            adaptateur=contenu.adaptateur,
            nb_caracteres=contenu.nb_caracteres,
            nb_pages=contenu.nb_pages,
            nb_feuilles=contenu.nb_feuilles,
            feuilles=contenu.feuilles,
            tronque=contenu.tronque,
        )

    def obtenir_modele(
        self, organisation_id: UUID, type_document: str
    ) -> tuple[Any, Path]:
        """Modèle **actif** d'une organisation et son fichier (INC-125).

        Le modèle vient du registre ``modeles_documents`` (une version active par
        couple organisation/type). Le fichier est téléchargé par le service :
        aucun chemin disque n'est fabriqué par l'appelant.

        Args:
            organisation_id: organisation cliente propriétaire du modèle.
            type_document: type du modèle (``offre_technique``, ``rapport``…).

        Returns:
            Le couple ``(modèle, chemin du fichier)``.

        Raises:
            NotFoundError: aucun modèle actif pour ce couple — le message dit
                quoi enregistrer (le tool le transmet à l'agent).
        """
        from app.application.services.modele_document_service import ModeleDocumentService

        modele = ModeleDocumentService(self._session).resoudre_modele_actif(
            organisation_id, type_document
        )
        _nom, chemin = self.telecharger(modele.document_template_id)
        return modele, chemin

    def dossier_de_l_ancre(self, ancre: str, entity_id: UUID) -> Path:
        """Dossier de stockage d'un objet métier, vérifié (existence + périmètre).

        C'est le dossier que lit un script ancré, et le parent de son dossier de
        travail temporaire. Aucun chemin n'est fabriqué depuis une entrée libre :
        le scope vient de ``_ANCHORS`` et l'identifiant est un UUID validé.

        Args:
            ancre: nom public de l'ancre (``organisation``, ``appel``, ``offre``,
                ``mission``, ``equipe``, ``session``).
            entity_id: UUID de l'objet métier.

        Returns:
            Le dossier du scope métier (créé par les dépôts, pas ici).

        Raises:
            ValidationError: ancre inconnue, ou stockage sans racine de fichier.
            NotFoundError: objet inexistant ou hors périmètre du compte.
        """
        champ = CHAMP_PAR_ANCRE.get(str(ancre or "").strip().lower())
        if champ is None:
            raise ValidationError(
                f"Ancre inconnue : {ancre!r}",
                details={"ancres_disponibles": sorted(CHAMP_PAR_ANCRE)},
            )
        if not self._ancre_existe(champ, entity_id):
            raise NotFoundError(f"{ancre} {entity_id} introuvable")
        self._exiger_ancre_autorisee(champ, entity_id)
        # Un **dossier** de scope (2 segments) n'est pas un chemin de fichier :
        # ``resolve_within_root`` exige ``{scope}/{id}/{fichier}``. Le scope vient
        # d'une table fermée et l'identifiant est un UUID, la vérification de
        # confinement reste faite après résolution.
        return self._dossier_scope(_ANCHORS[champ], entity_id)

    def dossier_organisation_de_l_ancre(
        self, ancre: str, entity_id: UUID
    ) -> Path | None:
        """Dossier de l'**organisation émettrice** d'une ancre (ses modèles).

        Un script d'offre ou de mission doit pouvoir lire les modèles du client
        sans accéder aux autres dossiers. La remontée est explicite :
        offre/session/mission → … → appel → organisation. Une équipe n'a pas
        d'organisation : ``None`` (rien de plus n'est lisible).

        Returns:
            Le dossier ``organisations/{id}``, ou ``None`` si l'organisation
            émettrice n'est pas déterminable (prestation directe sans appel).
        """
        organisation_id = self._organisation_de_l_ancre(ancre, entity_id)
        if organisation_id is None:
            return None
        return self._dossier_scope("organisations", organisation_id)

    def _dossier_scope(self, scope: str, entity_id: UUID) -> Path:
        """Dossier ``{scope}/{id}`` — construit, puis vérifié confiné à la racine."""
        racine = self._racine_stockage().resolve()
        dossier = (racine / scope / str(entity_id)).resolve()
        if not dossier.is_relative_to(racine):
            raise ValidationError(
                "Dossier d'ancre hors de la racine du stockage",
                details={"scope": scope},
            )
        return dossier

    def _get_fichier(self, document_id: UUID) -> Document:
        """Document dont le fichier est encore sur disque (téléchargement, lecture).

        Raises:
            NotFoundError: document inexistant ou supprimé (fichier retiré).
        """
        document = self._get(document_id)
        if document.statut == StatutDocument.SUPPRIME.value:
            msg = "Le fichier de ce document a été retiré du stockage (document supprimé)"
            raise NotFoundError(msg, details={"document_id": str(document_id)})
        return document

    def _organisation_de_l_ancre(self, ancre: str, entity_id: UUID) -> UUID | None:
        """Organisation émettrice d'un objet métier (``None`` si indéterminable)."""
        from app.infrastructure.repositories.appel import LotRepository

        nom = str(ancre or "").strip().lower()
        if nom == "organisation":
            return entity_id
        if nom == "appel":
            appel = self._appels.get(entity_id)
            return appel.organisation_id if appel is not None else None
        if nom == "offre":
            offre = self._offres.get(entity_id)
            if offre is None or offre.appel_id is None:
                return None
            appel = self._appels.get(offre.appel_id)
            return appel.organisation_id if appel is not None else None
        if nom == "mission":
            mission = self._missions.get(entity_id)
            if mission is None or mission.lot_id is None:
                return None
            lot = LotRepository(self._session).get(mission.lot_id)
            if lot is None or lot.appel_id is None:
                return None
            appel = self._appels.get(lot.appel_id)
            return appel.organisation_id if appel is not None else None
        if nom == "session":
            session_formation = self._sessions.get(entity_id)
            if session_formation is None:
                return None
            return self._organisation_de_l_ancre("mission", session_formation.mission_id)
        # Une équipe n'est rattachée à aucune organisation dans le modèle.
        return None

    def _racine_stockage(self) -> Path:
        """Racine physique du stockage (refus explicite si le stockage n'en a pas)."""
        racine = getattr(self.storage, "root", None)
        if racine is None:
            raise ValidationError(
                "Le stockage configuré n'expose pas de racine de fichiers : "
                "les dossiers d'ancre ne peuvent pas être résolus."
            )
        return Path(racine)
