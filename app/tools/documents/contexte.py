"""Contexte d'exécution partagé par les tools documentaires.

Les 29 outils de ``app.tools.documents`` ont besoin du même état : l'identité
de l'agent (traçabilité des dépôts), la politique de permission, la fabrique de
service, et trois racines techniques. Cet état était auparavant capturé par
fermetures imbriquées dans ``build_document_tools`` : 22 handlers dépendaient
d'une conversion d'identifiant, 8 d'un dépôt de document, 8 du moteur
documentaire, et 5 des racines de travail. Aucun de ces handlers n'était
lisible ni testable seul.

Ce module remplace ces fermetures par un objet explicite, au sens du patron
GRASP Information Expert : l'état partagé vit dans une classe qui le possède,
et non dans une portée de fonction qui le capture.

Contrat inchangé, à trois égards :

- les noms d'attributs sont ceux des anciennes variables capturées
  (``agent_id``, ``policy``, ``service_factory``), pour que le corps des
  handlers n'ait pas à changer ;
- les méthodes reprennent exactement le corps des fermetures qu'elles
  remplacent (``_uuid``, ``_deposer``, ``_moteur_documentaire``, racines) ;
- une propriété ``identite`` expose la même valeur qu'avant, si bien que
  ``identite.agent_id`` continue de fonctionner à l'identique.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any
from uuid import UUID

from app.application.dto import DocumentUploadInput
from app.core.errors import ValidationError
from app.domain.enums import StatutDocument, TypeDocument
from app.tools.documents.ancrage import _kwargs_ancre, _memes_ancres, _type_depot
from app.tools.documents.schemas import _ANCRES
from app.tools.permissions import AgentIdentity, PermissionPolicy


class ContexteOutils:
    """État partagé par les tools construits pour UN agent.

    Une instance par appel de ``build_document_tools`` : elle porte l'identité
    de l'agent, ses permissions et les racines de travail, et fournit les
    opérations d'infrastructure que chaque handler consomme.

    Attributs:
        agent_id: identifiant de l'agent appelant, écrit dans les dépôts.
        policy: politique de permission ; chaque handler exige sa capacité.
        service_factory: callable ``f() -> DocumentService`` fourni par
            l'appelant (l'agent n'ouvre pas de session lui-même).
        dossier_brouillons: surcharge de test de la racine des brouillons.
        dossier_sources: surcharge de test de la racine des sources.
    """

    def __init__(
        self,
        *,
        identite: AgentIdentity,
        policy: PermissionPolicy,
        service_factory: Any,
        dossier_brouillons: Any = None,
        dossier_sources: Any = None,
    ) -> None:
        self.agent_id = identite.agent_id
        self.policy = policy
        self.service_factory = service_factory
        self._dossier_brouillons = dossier_brouillons
        self._dossier_sources = dossier_sources
        self._identite = identite

    @property
    def identite(self) -> AgentIdentity:
        """Identité complète, telle que la recevaient les fermetures d'origine.

        Les handlers lisaient ``identite.agent_id`` ; la propriété garde cette
        écriture valide sans leur imposer de réécriture.
        """
        return self._identite

    # ------------------------------------------------------------------
    # Identifiants
    # ------------------------------------------------------------------

    def uuid(self, valeur: Any, champ: str) -> UUID:
        """Identifiant déjà validé par le schéma, ou texte à convertir.

        Le schéma type désormais les identifiants en ``UUID`` : le handler reçoit
        donc un ``UUID`` (jamais un texte), mais il reste tolérant à un texte pour
        un appel direct au handler (tests, chemin déterministe).
        """
        if isinstance(valeur, UUID):
            return valeur
        try:
            return UUID(str(valeur))
        except (ValueError, TypeError, AttributeError) as erreur:
            raise ValidationError(f"{champ} n'est pas un UUID valide") from erreur

    # ------------------------------------------------------------------
    # Sessions et racines techniques
    # ------------------------------------------------------------------

    def moteur_documentaire(self) -> Any:
        """Moteur documentaire sur la session de l'appel (jamais une autre)."""
        from app.application.services.moteur_documentaire_service import (
            MoteurDocumentaireService,
        )

        service = self.service_factory()
        return MoteurDocumentaireService(
            service.session,
            document_service=service,
            actor_id=self.agent_id,
        )

    def racine_brouillons(self) -> Path:
        """Racine technique des brouillons (surcharge de test, sinon storage_root)."""
        if self._dossier_brouillons is not None:
            return Path(self._dossier_brouillons)
        from app.core.config import get_settings
        from app.documents.brouillons import racine_brouillons

        return racine_brouillons(get_settings().storage_root)

    def racine_sources_rendus(self) -> Path:
        """Racine technique des sources de livrables (surcharge de test sinon storage)."""
        if self._dossier_sources is not None:
            return Path(self._dossier_sources)
        from app.core.config import get_settings
        from app.documents.sources import racine_sources

        return racine_sources(get_settings().storage_root)

    # ------------------------------------------------------------------
    # Dépôt d'un document produit
    # ------------------------------------------------------------------

    def deposer(
        self, octets: bytes, payload: dict[str, Any], extension: str
    ) -> dict[str, Any]:
        """Dépose un document produit en statut ``proposed`` (jamais ``approved``)."""
        service = self.service_factory()
        nom = payload["nom_fichier"]
        if not nom.lower().endswith(extension):
            nom = f"{nom}{extension}"
        # Alias de type converti ici (offre_financiere → offre...) : la
        # conversion est signalée au modèle dans le retour, pour qu'il
        # s'aligne tout seul sur le vocabulaire réel.
        type_demande = str(payload.get("type_document") or "").strip().lower()
        type_depot = _type_depot(payload)
        # Révision imposée : un jumeau (PDF d'un DOCX) reprend la révision de
        # son principal au lieu d'ouvrir une série parallèle. Absente, la
        # révision est calculée par le service sur le livrable entier.
        imposee = payload.get("revision_imposee")
        kwargs_dto: dict[str, Any] = {
            "type_document": type_depot,
            "nom": nom,
            "stream": io.BytesIO(octets),
            "proposed_by_agent": payload.get("propose_par") or self.agent_id,
            "created_by": payload.get("propose_par") or self.agent_id,
            "revision_imposee": int(imposee) if imposee is not None else None,
        }
        ancres = _kwargs_ancre(payload)
        kwargs_dto.update(ancres)
        entree = DocumentUploadInput(**kwargs_dto)
        # Un document sans ancre est enregistré par le service sous le type
        # ``non_classe`` : chercher sa version sous le type *demandé* ne
        # trouvait rien et retombait sur « version 1 existe déjà ». La recherche
        # de version se fait donc sur le type réellement écrit en base.
        type_reel = type_depot if ancres else TypeDocument.NON_CLASSE.value
        # Un document du même nom, du même type et du même rattachement est
        # **révisé**, pas dupliqué : c'est une NOUVELLE VERSION (v2, v3…). Sans
        # cela, chaque régénération butait sur « version 1 existe déjà » et
        # l'agent n'avait d'autre issue que de renommer le document à la main.
        existant = service.derniere_version(nom, type_reel)
        if (
            existant is not None
            and existant.statut
            not in (StatutDocument.ARCHIVED.value, StatutDocument.SUPPRIME.value)
            and _memes_ancres(existant, ancres)
        ):
            document = service.remplacer_document(existant.id, entree)
            remplace: str | None = str(existant.id)
        else:
            document = service.enregistrer_document(entree)
            remplace = None
        non_classe = all(
            getattr(document, champ) is None for champ in _ANCRES.values()
        )
        sortie: dict[str, Any] = {
            "document_id": str(document.id),
            "statut": document.statut,
            "nom": document.nom,
            "type_document": document.type_document,
            "version": document.version,
        }
        if remplace is not None:
            sortie["nouvelle_version_de"] = remplace
        if type_demande and type_demande != type_depot:
            sortie["type_document_convertis"] = (
                f"'{type_demande}' → '{type_depot}' : les noms de profils "
                "(offre_financiere, offre_technique...) ne sont pas des types "
                "de dépôt ; utiliser le type effectif ci-dessus au prochain appel."
            )
        if non_classe:
            sortie["non_classe"] = True
            sortie["message"] = (
                f"Document déposé en statut 'proposed' sans rattachement métier "
                f"(dossier personnel {document.id}). Validation humaine requise. "
                "Proposer le téléchargement via proposer_telechargement_document."
            )
        else:
            sortie["message"] = (
                "Document déposé en statut 'proposed' (validation humaine requise)."
            )
        return sortie
