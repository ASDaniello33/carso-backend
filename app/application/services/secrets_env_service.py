"""Variables du fichier ``.env`` modifiées par l'administration (Paramètres > Secrets).

Trois règles, toutes vérifiables :

- une **valeur n'est jamais relue** : l'API ne renvoie que les noms et leur
  présence, comme pour la clé d'un provider. Pour changer une valeur, on en
  saisit une nouvelle ;
- une valeur **refusée par la configuration n'est pas écrite** : le ``.env`` ne
  doit jamais être laissé dans un état qui empêche l'application de démarrer ;
- chaque geste est **tracé** avec le nom de la variable seulement, jamais sa
  valeur. Le cache de configuration est vidé après écriture : la valeur est lue
  par les prochains appels, sans redémarrer le backend. Les réglages lus une
  seule fois au démarrage (connexion PostgreSQL, secret d'authentification) font
  exception : l'écran le dit.
"""

from __future__ import annotations

import os

from sqlalchemy.orm import Session

from app.application.trace import TraceContext
from app.core.config import Settings, get_settings
from app.core.errors import NotFoundError, ValidationError
from app.domain.enums import ActionAudit, ActorType
from app.infrastructure.env_file import MOTIF_CLE, FichierEnv, FichierEnvErreur

#: Longueur maximale acceptée pour une valeur (une clé d'API reste bien en deçà).
LONGUEUR_MAX_VALEUR = 4000

#: Variables dont le changement doit aussi vider le cache de chiffrement.
_VARIABLES_CHIFFREMENT = frozenset({"AUTH_SECRET", "PARAMETRES_CHIFFREMENT_KEY"})


def valider_variable(cle: str, valeur: str) -> None:
    """Refuse un nom mal formé, une valeur multiligne ou invalide pour son réglage.

    Quand la variable correspond à un réglage connu (``Settings``), la valeur est
    validée **avant** l'écriture : un ``.env`` enregistré ne doit jamais laisser
    l'application incapable de démarrer. Un nom inconnu de ``Settings`` est
    accepté tel quel : il ne pilote aucun réglage, le refuser serait arbitraire.

    Raises:
        ValidationError: nom invalide, valeur trop longue ou mal typée.
    """
    if not MOTIF_CLE.match(cle):
        raise ValidationError(
            "Nom de variable invalide : lettres, chiffres et souligné, "
            "sans commencer par un chiffre.",
            details={"cle": cle},
        )
    if "\n" in valeur or "\r" in valeur:
        raise ValidationError(
            "Une valeur de variable ne peut pas contenir de retour à la ligne.",
            details={"cle": cle},
        )
    if len(valeur) > LONGUEUR_MAX_VALEUR:
        raise ValidationError(
            f"Valeur trop longue : {LONGUEUR_MAX_VALEUR} caractères maximum.",
            details={"cle": cle},
        )
    champ = cle.lower()
    if champ not in Settings.model_fields:
        return
    try:
        Settings(**{champ: valeur})
    except Exception as exc:  # noqa: BLE001 - message volontairement sans la valeur
        raise ValidationError(
            f"La valeur ne convient pas au réglage {cle}.",
            details={"cle": cle},
        ) from exc


class SecretsEnvService:
    """Liste et écrit les variables du fichier d'environnement du backend."""

    def __init__(
        self,
        session: Session,
        actor_id: str | None = None,
        fichier: FichierEnv | None = None,
    ) -> None:
        self._fichier = fichier or FichierEnv()
        self._trace = TraceContext(session, actor_id=actor_id)

    def etat(self) -> tuple[list[str], list[str]]:
        """Noms déclarés, puis ceux que l'environnement du processus impose.

        Une variable déjà posée dans l'environnement du processus l'emporte sur
        le fichier : le dire évite de croire qu'un changement n'a pas pris.
        """
        cles = self._fichier.cles()
        return cles, [cle for cle in cles if cle in os.environ]

    def enregistrer(self, cle: str, valeur: str) -> bool:
        """Écrit la variable ; rend ``True`` si elle existait déjà.

        Raises:
            ValidationError: nom ou valeur refusés (rien n'est écrit).
        """
        valider_variable(cle, valeur)
        try:
            existait = self._fichier.enregistrer(cle, valeur)
        except FichierEnvErreur as exc:
            raise ValidationError(str(exc), details={"cle": cle}) from exc
        self._apres_ecriture(cle)
        self._trace.record_event(
            actor_type=ActorType.HUMAIN.value,
            action=ActionAudit.SECRET_ENV_MODIFIE.value,
            entity_type="variable_env",
            entity_id=cle,
            after={"cle": cle, "creee": not existait},
        )
        return existait

    def supprimer(self, cle: str) -> None:
        """Retire la variable du fichier (le réglage reprend sa valeur par défaut).

        Raises:
            NotFoundError: la variable n'existe pas dans le fichier.
        """
        try:
            supprimee = self._fichier.supprimer(cle)
        except FichierEnvErreur as exc:
            raise ValidationError(str(exc), details={"cle": cle}) from exc
        if not supprimee:
            raise NotFoundError(
                "Cette variable n'existe pas dans le fichier .env",
                details={"cle": cle},
            )
        self._apres_ecriture(cle)
        self._trace.record_event(
            actor_type=ActorType.HUMAIN.value,
            action=ActionAudit.SECRET_ENV_SUPPRIME.value,
            entity_type="variable_env",
            entity_id=cle,
            after={"cle": cle},
        )

    def _apres_ecriture(self, cle: str) -> None:
        """Le fichier a changé : la prochaine lecture de configuration le voit."""
        get_settings.cache_clear()
        if cle in _VARIABLES_CHIFFREMENT:
            from app.core.chiffrement import effacer_cache

            effacer_cache()


__all__ = ["LONGUEUR_MAX_VALEUR", "SecretsEnvService", "valider_variable"]
