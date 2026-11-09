"""Catalogue des fournisseurs et modèles d'IA — service applicatif.

**Ce qu'il fait.** Il mémorise les **connexions** (fournisseur : protocole, URL
de base, clé chiffrée) et les **modèles** enregistrés chez chacune, puis permet
d'appliquer un modèle enregistré à la configuration runtime — sans ressaisir
d'identifiants.

**Ce qu'il ne fait pas.** Il ne construit aucun modèle ni aucun graphe :
``AgentRuntimeService.enregistrer`` reste l'unique porte d'écriture de la
configuration appliquée (validation, versionnement, audit), et
``app.agents.manager`` l'applique à chaud. Le catalogue propose, le runtime
applique.

**Sécurité.** La clé n'entre ici qu'en clair pour un appel réseau immédiat
(découverte) ou pour être chiffrée ; elle n'est **jamais** renvoyée par
``lister``/``resume`` — seulement le drapeau ``cle_enregistree``.

**Règles de dépendance** (aucune cascade silencieuse) :

- supprimer un fournisseur qui porte des modèles est refusé : supprimer d'abord
  ses modèles, geste explicite ;
- supprimer (ou modifier l'identité de) un fournisseur/modèle **appliqué** à la
  configuration runtime est refusé tant qu'un autre modèle n'est pas appliqué —
  sinon l'agent tournerait sur une configuration fantôme.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.application.trace import TraceContext
from app.core.chiffrement import chiffrement_disponible, chiffrer, dechiffrer
from app.core.errors import ConflictError, NotFoundError
from app.core.errors import ValidationError as BusinessValidationError
from app.domain.catalogue_ia import PROTOCOLES_IA, FournisseurIA, ModeleIA
from app.domain.enums import ActionAudit, ActorType
from app.infrastructure.repositories.catalogue_ia import (
    FournisseurIARepository,
    ModeleIARepository,
)

_ENTITY_FOURNISSEUR = "fournisseur_ia"
_ENTITY_MODELE = "modele_ia"


class CatalogueModelesService:
    """Lecture et écriture du catalogue (fournisseurs puis modèles)."""

    def __init__(self, session: Session, actor_id: str | None = None) -> None:
        self._session = session
        self.fournisseurs = FournisseurIARepository(session)
        self.modeles = ModeleIARepository(session)
        self._trace = TraceContext(session, actor_id=actor_id)

    # --- Lecture --------------------------------------------------------------

    def lister(self) -> list[dict[str, Any]]:
        """Fournisseurs avec leurs modèles, prêts pour l'API (aucun secret)."""
        return [
            {
                **fournisseur.resume(),
                "modeles": [modele.resume() for modele in fournisseur.modeles],
            }
            for fournisseur in self.fournisseurs.lister()
        ]

    def obtenir_fournisseur(self, fournisseur_id: uuid.UUID) -> FournisseurIA:
        """Fournisseur existant, ou 404 nommé."""
        fournisseur = self.fournisseurs.obtenir(fournisseur_id)
        if fournisseur is None:
            raise NotFoundError(
                f"Fournisseur inconnu : {fournisseur_id}",
                details={"fournisseur_id": str(fournisseur_id)},
            )
        return fournisseur

    def resume_fournisseur(self, fournisseur_id: uuid.UUID) -> dict[str, Any]:
        """Fournisseur et ses modèles, pour la réponse API.

        Les modèles sont relus par le repository : la relation d'un objet déjà
        chargé dans la session ne se rafraîchit pas après un ajout, et la réponse
        montrerait une liste vide juste après l'enregistrement.
        """
        fournisseur = self.obtenir_fournisseur(fournisseur_id)
        modeles = self.modeles.lister_pour(fournisseur.id)
        return {
            **fournisseur.resume(),
            "modeles": [modele.resume() for modele in modeles],
        }

    # --- Fournisseurs ---------------------------------------------------------

    def ajouter_fournisseur(
        self,
        *,
        nom: str,
        protocole: str,
        base_url: str | None = None,
        api_key: str | None = None,
    ) -> FournisseurIA:
        """Crée une connexion nommée.

        Raises:
            BusinessValidationError: nom vide, protocole inconnu, ``base_url``
                absente pour un endpoint compatible, clé fournie sans chiffrement.
            ConflictError: un fournisseur porte déjà ce nom.
        """
        nom_propre = self._nom_valide(nom)
        protocole_propre = self._protocole_valide(protocole)
        base_propre = self._base_url_valide(protocole_propre, base_url)
        if self.fournisseurs.par_nom(nom_propre) is not None:
            raise ConflictError(
                f"Un fournisseur nommé « {nom_propre} » existe déjà",
                details={"nom": nom_propre},
            )
        fournisseur = FournisseurIA(
            nom=nom_propre,
            protocole=protocole_propre,
            base_url=base_propre,
            api_key_chiffree=self._chiffrer(api_key),
            modifie_par=self._actor_id(),
        )
        self.fournisseurs.add(fournisseur)
        self._session.flush()
        self._tracer(ActionAudit.FOURNISSEUR_IA_MODIFIE, fournisseur, "création")
        return fournisseur

    def modifier_fournisseur(
        self,
        fournisseur_id: uuid.UUID,
        *,
        nom: str | None = None,
        protocole: str | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
        conserver_cle: bool = True,
    ) -> FournisseurIA:
        """Met à jour la connexion.

        La clé n'est réécrite que si ``api_key`` est fournie ; ``conserver_cle=False``
        la retire (retour à la clé d'environnement du runtime). Changer l'URL de
        base ne casse aucun modèle : ils appartiennent toujours au fournisseur.
        """
        fournisseur = self.obtenir_fournisseur(fournisseur_id)
        nom_propre = self._nom_valide(nom) if nom is not None else fournisseur.nom
        protocole_propre = self._protocole_valide(protocole) if protocole else fournisseur.protocole
        base_propre = self._base_url_valide(
            protocole_propre,
            base_url if base_url is not None else fournisseur.base_url,
        )
        if nom_propre != fournisseur.nom:
            autre = self.fournisseurs.par_nom(nom_propre)
            if autre is not None and autre.id != fournisseur.id:
                raise ConflictError(
                    f"Un fournisseur nommé « {nom_propre} » existe déjà",
                    details={"nom": nom_propre},
                )
        if api_key:
            fournisseur.api_key_chiffree = self._chiffrer(api_key)
        elif not conserver_cle:
            fournisseur.api_key_chiffree = None
        fournisseur.nom = nom_propre
        fournisseur.protocole = protocole_propre
        fournisseur.base_url = base_propre
        fournisseur.modifie_par = self._actor_id()
        self._session.flush()
        self._tracer(ActionAudit.FOURNISSEUR_IA_MODIFIE, fournisseur, "modification")
        return fournisseur

    def supprimer_fournisseur(self, fournisseur_id: uuid.UUID) -> None:
        """Supprime une connexion **sans modèles** et hors runtime appliqué.

        Raises:
            ConflictError: des modèles y sont enregistrés, ou la configuration
                runtime appliquée pointe sur ce fournisseur.
        """
        fournisseur = self.obtenir_fournisseur(fournisseur_id)
        modeles = self.modeles.lister_pour(fournisseur.id)
        if modeles:
            raise ConflictError(
                f"Le fournisseur « {fournisseur.nom} » porte "
                f"{len(modeles)} modèle(s) : supprimez-les d'abord",
                details={
                    "fournisseur_id": str(fournisseur.id),
                    "modeles": [modele.nom_modele for modele in modeles],
                },
            )
        if self._fournisseur_applique(fournisseur):
            raise ConflictError(
                "Ce fournisseur porte la configuration appliquée au runtime : "
                "appliquez d'abord un autre modèle",
                details={"fournisseur_id": str(fournisseur.id)},
            )
        self._tracer(ActionAudit.FOURNISSEUR_IA_MODIFIE, fournisseur, "suppression", avant=True)
        self._session.delete(fournisseur)
        self._session.flush()

    # --- Modèles --------------------------------------------------------------

    def ajouter_modeles(
        self, fournisseur_id: uuid.UUID, noms: list[str]
    ) -> tuple[list[ModeleIA], list[str]]:
        """Enregistre des modèles chez un fournisseur, sans doublon.

        Args:
            fournisseur_id: connexion cible.
            noms: identifiants de modèles (issus de la découverte ou saisis).

        Returns:
            ``(créés, ignorés)`` — ``ignorés`` liste les noms déjà enregistrés
            (ou vides), dans l'ordre reçu. Aucun doublon n'est créé, y compris
            lorsque le même nom apparaît deux fois dans la requête.
        """
        fournisseur = self.obtenir_fournisseur(fournisseur_id)
        crees: list[ModeleIA] = []
        ignores: list[str] = []
        vus: set[str] = set()
        for brut in noms or []:
            nom = (brut or "").strip()
            if not nom:
                continue
            if nom in vus:
                ignores.append(nom)
                continue
            vus.add(nom)
            if self.modeles.par_nom(fournisseur.id, nom) is not None:
                ignores.append(nom)
                continue
            modele = ModeleIA(
                fournisseur_id=fournisseur.id,
                nom_modele=nom,
                modifie_par=self._actor_id(),
            )
            self.modeles.add(modele)
            crees.append(modele)
        if crees:
            self._session.flush()
            self._tracer(ActionAudit.MODELE_IA_MODIFIE, fournisseur, "ajout de modèles")
        return crees, ignores

    def supprimer_modele(self, modele_id: uuid.UUID) -> None:
        """Supprime un modèle enregistré, jamais celui appliqué au runtime."""
        modele = self.modeles.obtenir_avec_fournisseur(modele_id)
        if modele is None:
            raise NotFoundError(
                f"Modèle inconnu : {modele_id}", details={"modele_id": str(modele_id)}
            )
        if self._modele_applique(modele):
            raise ConflictError(
                "Ce modèle est appliqué au runtime : appliquez-en un autre avant "
                "de le retirer du catalogue",
                details={"modele_id": str(modele.id)},
            )
        self._tracer(ActionAudit.MODELE_IA_MODIFIE, modele.fournisseur, "suppression de modèle")
        self._session.delete(modele)
        self._session.flush()

    # --- Découverte -----------------------------------------------------------

    async def decouvrir(self, fournisseur_id: uuid.UUID) -> dict[str, Any]:
        """Interroge le fournisseur et rend les modèles qu'il propose.

        Returns:
            ``{"disponibles": [...], "deja_enregistres": [...]}`` — l'interface
            ne propose que les nouveaux, sans jamais dupliquer.

        Raises:
            DecouverteIndisponible: protocole sans découverte, URL absente,
                route absente du fournisseur.
            AgentConfigurationError: clé refusée ou fournisseur injoignable.
        """
        from app.agents.decouverte_modeles import decouvrir_modeles

        fournisseur = self.obtenir_fournisseur(fournisseur_id)
        disponibles = await decouvrir_modeles(
            protocole=fournisseur.protocole,
            base_url=fournisseur.base_url,
            api_key=dechiffrer(fournisseur.api_key_chiffree),
        )
        existants = {modele.nom_modele for modele in self.modeles.lister_pour(fournisseur.id)}
        return {
            "disponibles": disponibles,
            "deja_enregistres": sorted(existants),
        }

    # --- Application au runtime ----------------------------------------------

    def preparer_application(self, modele_id: uuid.UUID) -> dict[str, Any]:
        """Paramètres de la configuration runtime pour ce modèle enregistré.

        Le service ne l'écrit pas : la route appelle
        ``AgentRuntimeService.enregistrer`` (validation, versionnement, audit)
        puis applique la configuration au manager. Ici, on ne fait que réunir
        la connexion — clé comprise — pour éviter toute ressaisie.
        """
        modele = self.modeles.obtenir_avec_fournisseur(modele_id)
        if modele is None:
            raise NotFoundError(
                f"Modèle inconnu : {modele_id}", details={"modele_id": str(modele_id)}
            )
        fournisseur = modele.fournisseur
        if fournisseur is None or not fournisseur.actif:
            raise BusinessValidationError(
                "Le fournisseur de ce modèle est désactivé : réactivez-le avant de l'appliquer",
                details={"fournisseur_id": str(modele.fournisseur_id)},
            )
        return {
            "provider": fournisseur.protocole,
            "model": modele.nom_modele,
            "base_url": fournisseur.base_url,
            "api_key": dechiffrer(fournisseur.api_key_chiffree),
            "fournisseur": fournisseur.nom,
        }

    # --- Garde-fous -----------------------------------------------------------

    def _fournisseur_applique(self, fournisseur: FournisseurIA) -> bool:
        """Vrai si un **modèle enregistré** de ce fournisseur est appliqué.

        Comparer seulement protocole et URL bloquerait la suppression d'un
        fournisseur vide qui partage l'endpoint de la configuration active (deux
        connexions au même agrégateur sont légitimes) : c'est le modèle qui
        identifie la configuration appliquée.
        """
        return any(
            self._modele_applique(modele) for modele in self.modeles.lister_pour(fournisseur.id)
        )

    def _modele_applique(self, modele: ModeleIA) -> bool:
        """Vrai si la configuration runtime active applique ce modèle précis."""
        from app.infrastructure.repositories.agent_runtime import (
            ConfigurationRuntimeAgentRepository,
        )

        actif = ConfigurationRuntimeAgentRepository(self._session).get_active()
        if actif is None or modele.fournisseur is None:
            return False
        return (
            actif.model == modele.nom_modele
            and actif.provider == modele.fournisseur.protocole
            and (actif.base_url or None) == (modele.fournisseur.base_url or None)
        )

    # --- Validations et trace -------------------------------------------------

    def _nom_valide(self, nom: str | None) -> str:
        propre = (nom or "").strip()
        if not propre:
            raise BusinessValidationError("Le nom du fournisseur est obligatoire")
        if len(propre) > 120:
            raise BusinessValidationError("Nom de fournisseur trop long (120 caractères au plus)")
        return propre

    def _protocole_valide(self, protocole: str | None) -> str:
        propre = (protocole or "").strip().lower()
        if propre not in PROTOCOLES_IA:
            raise BusinessValidationError(
                f"Protocole inconnu : {protocole!r} "
                f"(valeurs acceptées : {', '.join(PROTOCOLES_IA)})",
                details={"protocoles": list(PROTOCOLES_IA)},
            )
        return propre

    def _base_url_valide(self, protocole: str, base_url: str | None) -> str | None:
        """URL de base : exigée pour un endpoint compatible, facultative ailleurs."""
        propre = (base_url or "").strip() or None
        if protocole == "openai_compatible" and propre is None:
            raise BusinessValidationError(
                "Une URL de base est obligatoire pour un endpoint compatible "
                "OpenAI (ex. https://exemple.ai/v1)"
            )
        if propre is not None and not propre.startswith(("http://", "https://")):
            raise BusinessValidationError(
                "L'URL de base doit commencer par http:// ou https://",
                details={"base_url": propre},
            )
        return propre

    def _chiffrer(self, api_key: str | None) -> str | None:
        """Clé chiffrée au repos ; jamais stockée en clair."""
        propre = (api_key or "").strip()
        if not propre:
            return None
        if not chiffrement_disponible():
            raise BusinessValidationError(
                "Chiffrement indisponible sur ce serveur : la clé ne peut pas "
                "être enregistrée (configurer la clé de chiffrement).",
                details={"chiffrement": False},
            )
        return chiffrer(propre)

    def _actor_id(self) -> str | None:
        return getattr(self._trace, "actor_id", None)

    def _tracer(
        self,
        action: ActionAudit,
        entite: Any,
        geste: str,
        *,
        avant: bool = False,
    ) -> None:
        """Audit du geste — jamais de secret, jamais d'objet ORM."""
        self._trace.record_event(
            actor_type=ActorType.HUMAIN.value,
            action=action.value,
            entity_type=(
                _ENTITY_FOURNISSEUR if isinstance(entite, FournisseurIA) else _ENTITY_MODELE
            ),
            entity_id=str(getattr(entite, "id", "")),
            before=entite.resume() if avant else None,
            after={**entite.resume(), "geste": geste},
        )


__all__ = ["CatalogueModelesService"]
