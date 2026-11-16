"""Lecture et écriture du fichier ``.env`` du backend.

Le fichier reste la propriété de l'administrateur : les commentaires, l'ordre et
les lignes qu'on ne comprend pas sont conservés. Seule la ligne de la variable
visée change.

Deux règles portent la sécurité :

- une **valeur n'est jamais renvoyée** : ce module ne sait que l'écrire ;
- l'écriture est **atomique** : un arrêt au milieu ne laisse pas un ``.env``
  tronqué, donc pas une application incapable de démarrer.
"""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

from app.core.config import RACINE_BACKEND

#: Nom de variable d'environnement : lettres, chiffres et souligné, jamais un
#: chiffre en tête (même règle que le shell et ``os.environ``).
MOTIF_CLE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

#: Une affectation dans le fichier, avec ou sans préfixe ``export``.
_LIGNE_VARIABLE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$")

#: Valeur qu'on peut écrire nue : aucun espace, aucun caractère réservé.
_VALEUR_NUE = re.compile(r"^[A-Za-z0-9_./:@+%,~-]+$")


class FichierEnvErreur(ValueError):
    """Écriture refusée : la ligne ne serait pas relue comme prévu."""


def chemin_par_defaut() -> Path:
    """``backend/.env``, résolu depuis le paquet et non du répertoire courant."""
    return RACINE_BACKEND / ".env"


def _formater_valeur(valeur: str) -> str:
    """Valeur telle qu'elle sera relue à l'identique par le chargeur dotenv."""
    if valeur and _VALEUR_NUE.fullmatch(valeur):
        return valeur
    echappee = valeur.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{echappee}"'


class FichierEnv:
    """Édition ciblée du fichier d'environnement, sans le réécrire entièrement."""

    def __init__(self, chemin: Path | None = None) -> None:
        self.chemin = chemin or chemin_par_defaut()

    # --- lecture ---------------------------------------------------------------

    def _lire(self) -> list[str]:
        if not self.chemin.exists():
            return []
        return self.chemin.read_text(encoding="utf-8").splitlines()

    def _ecrire(self, lignes: list[str]) -> None:
        self.chemin.parent.mkdir(parents=True, exist_ok=True)
        contenu = "\n".join(lignes)
        if contenu:
            contenu += "\n"
        descripteur, temporaire = tempfile.mkstemp(
            dir=str(self.chemin.parent), prefix=".env.", suffix=".tmp"
        )
        try:
            with os.fdopen(descripteur, "w", encoding="utf-8", newline="\n") as flux:
                flux.write(contenu)
            # Même droits que le fichier remplacé : un .env lisible par le seul
            # service qui l'exploite ne doit pas devenir lisible par tous.
            if self.chemin.exists():
                os.chmod(temporaire, self.chemin.stat().st_mode)
            os.replace(temporaire, self.chemin)
        finally:
            if os.path.exists(temporaire):
                os.unlink(temporaire)

    def cles(self) -> list[str]:
        """Variables déclarées, dans l'ordre du fichier, sans doublon."""
        vues: set[str] = set()
        ordre: list[str] = []
        for ligne in self._lire():
            trouve = _LIGNE_VARIABLE.match(ligne)
            if trouve is None:
                continue
            cle = trouve.group(1)
            if cle in vues:
                continue
            vues.add(cle)
            ordre.append(cle)
        return ordre

    def contient(self, cle: str) -> bool:
        return cle in self.cles()

    # --- écriture --------------------------------------------------------------

    def enregistrer(self, cle: str, valeur: str) -> bool:
        """Écrit ``CLE=valeur`` et rend ``True`` si la clé existait déjà.

        Raises:
            FichierEnvErreur: nom de variable invalide, ou valeur multiligne.
        """
        if not MOTIF_CLE.match(cle):
            raise FichierEnvErreur(
                "Nom de variable invalide : lettres, chiffres et souligné, "
                "sans commencer par un chiffre."
            )
        if "\n" in valeur or "\r" in valeur:
            raise FichierEnvErreur(
                "Une valeur de variable ne peut pas contenir de retour à la ligne."
            )
        remplacee = False
        resultat: list[str] = []
        for ligne in self._lire():
            trouve = _LIGNE_VARIABLE.match(ligne)
            if trouve is not None and trouve.group(1) == cle:
                # Une variable répétée serait ambiguë : la dernière gagne côté
                # dotenv, on garde donc une seule ligne, à sa première place.
                if not remplacee:
                    resultat.append(f"{cle}={_formater_valeur(valeur)}")
                    remplacee = True
                continue
            resultat.append(ligne)
        if not remplacee:
            resultat.append(f"{cle}={_formater_valeur(valeur)}")
        self._ecrire(resultat)
        return remplacee

    def supprimer(self, cle: str) -> bool:
        """Retire la variable du fichier ; rend ``False`` si elle était absente."""
        resultat: list[str] = []
        trouvee = False
        for ligne in self._lire():
            affectation = _LIGNE_VARIABLE.match(ligne)
            if affectation is not None and affectation.group(1) == cle:
                trouvee = True
                continue
            resultat.append(ligne)
        if trouvee:
            self._ecrire(resultat)
        return trouvee


__all__ = [
    "FichierEnv",
    "FichierEnvErreur",
    "MOTIF_CLE",
    "chemin_par_defaut",
]
