"""Tests — Paramètres > Secrets : variables du fichier ``.env``.

Trois promesses sont vérifiées, parce qu'elles sont faciles à casser :

1. **le fichier reste lisible par un humain** — commentaires et ordre conservés,
   une ligne remplacée en place, une variable répétée ramenée à une seule ;
2. **une valeur ne sort jamais** — l'API ne renvoie que les noms, et une valeur
   invalide pour son réglage est refusée avant l'écriture ;
3. **l'accès est réservé aux administrateurs** — un collaborateur reçoit 403.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.application.services.secrets_env_service import valider_variable
from app.core.config import Settings
from app.core.errors import ValidationError as BusinessValidationError
from app.domain.enums import RoleUtilisateur
from app.domain.identity import Utilisateur
from app.infrastructure.env_file import FichierEnv, FichierEnvErreur


def _admin() -> Utilisateur:
    return Utilisateur(
        email=f"admin-{uuid4().hex[:8]}@carso.test",
        nom="Admin",
        prenom="Carso",
        password_hash="x",
        role=RoleUtilisateur.ADMINISTRATEUR.value,
    )


def _brancher_admin(api_client: TestClient) -> None:
    from app.api.deps import get_current_user, require_admin

    admin = _admin()
    api_client.app.dependency_overrides[require_admin] = lambda: admin
    api_client.app.dependency_overrides[get_current_user] = lambda: admin


# --- 1. Fichier ---------------------------------------------------------------


def test_cles_lit_commentaires_export_et_doublons(tmp_path) -> None:
    """Les commentaires et lignes vides sont ignorés ; un doublon ne compte qu'une fois."""
    chemin = tmp_path / ".env"
    chemin.write_text(
        "# Configuration\n"
        "\n"
        "PREMIERE=valeur\n"
        'export SECONDE="deux mots"\n'
        "PREMIERE=autre\n",
        encoding="utf-8",
    )
    assert FichierEnv(chemin).cles() == ["PREMIERE", "SECONDE"]


def test_enregistrer_remplace_en_place_et_conserve_le_reste(tmp_path) -> None:
    """La ligne visée change, les commentaires et les autres variables restent."""
    chemin = tmp_path / ".env"
    chemin.write_text("# En-tête\nCLE=ancienne\n# Pause\nAUTRE=stable\n", encoding="utf-8")

    existait = FichierEnv(chemin).enregistrer("CLE", "nouvelle")

    assert existait is True
    assert chemin.read_text(encoding="utf-8") == (
        "# En-tête\nCLE=nouvelle\n# Pause\nAUTRE=stable\n"
    )


def test_enregistrer_ajoute_une_variable_absente(tmp_path) -> None:
    """Une variable nouvelle s'ajoute en fin de fichier, sans toucher au reste."""
    chemin = tmp_path / ".env"
    chemin.write_text("EXISTANTE=un\n", encoding="utf-8")

    existait = FichierEnv(chemin).enregistrer("NOUVELLE", "deux mots")

    assert existait is False
    assert chemin.read_text(encoding="utf-8") == 'EXISTANTE=un\nNOUVELLE="deux mots"\n'


def test_enregistrer_refuse_une_cle_invalide(tmp_path) -> None:
    """Un nom qui ne serait pas relu comme une variable est refusé."""
    with pytest.raises(FichierEnvErreur):
        FichierEnv(tmp_path / ".env").enregistrer("2-INVALIDE", "x")


def test_supprimer_retire_la_ligne_et_rien_d_autre(tmp_path) -> None:
    """Seule la variable visée disparaît, une variable voisine reste."""
    chemin = tmp_path / ".env"
    chemin.write_text("UN=1\nDEUX=2\n# note\n", encoding="utf-8")
    fichier = FichierEnv(chemin)

    assert fichier.supprimer("UN") is True
    assert fichier.supprimer("UN") is False
    assert chemin.read_text(encoding="utf-8") == "DEUX=2\n# note\n"


# --- 2. Validation ------------------------------------------------------------


def test_valider_refuse_une_cle_et_une_valeur_multiligne() -> None:
    """Le nom fautif et le retour à la ligne sont refusés, avec la clé en détail."""
    with pytest.raises(BusinessValidationError) as nom:
        valider_variable("pas bon", "x")
    assert nom.value.details == {"cle": "pas bon"}

    with pytest.raises(BusinessValidationError):
        valider_variable("CLE_SIMPLE", "ligne1\nligne2")


def test_valider_refuse_une_valeur_invalide_pour_son_reglage() -> None:
    """Un entier mal saisi est refusé : le .env ne doit pas empêcher le démarrage."""
    with pytest.raises(BusinessValidationError, match="AGENT_MAX_ITERATIONS"):
        valider_variable("AGENT_MAX_ITERATIONS", "pas-un-entier")

    # La même valeur via le champ pydantic est bien refusée par Settings
    # (``ValidationError`` pydantic hérite de ``ValueError``).
    with pytest.raises(ValueError):
        Settings(agent_max_iterations="pas-un-entier")

    # Une valeur correcte passe et l'écran garde le nom tel quel.
    valider_variable("AGENT_MAX_ITERATIONS", "42")


def test_valider_accepte_une_variable_inconnue_de_settings() -> None:
    """Une variable que l'application ne lit pas ne pilote rien : elle est acceptée."""
    valider_variable("CLE_POUR_UN_OUTIL_EXTERNE", "n-importe-quelle-valeur")


# --- 3. API -------------------------------------------------------------------


def test_secrets_reserves_aux_administrateurs(api_client: TestClient) -> None:
    """Sans administrateur (compte lab), la liste et l'écriture sont refusées."""
    assert api_client.get("/api/v1/parametres/secrets").status_code == 403
    assert (
        api_client.put("/api/v1/parametres/secrets/CLE", json={"valeur": "x"}).status_code
        == 403
    )


def test_api_ecrit_lit_puis_retire_sans_jamais_renvoyer_la_valeur(
    api_client: TestClient, tmp_path, monkeypatch
) -> None:
    """Le cycle complet, et la valeur reste introuvable dans toutes les réponses."""
    from app.infrastructure import env_file

    monkeypatch.setattr(env_file, "RACINE_BACKEND", tmp_path)
    (tmp_path / ".env").write_text("# note\nEXISTANTE=un\n", encoding="utf-8")
    _brancher_admin(api_client)
    valeur = "valeur-tres-secrete-42"

    ecriture = api_client.put("/api/v1/parametres/secrets/NOUVELLE_CLE", json={"valeur": valeur})
    assert ecriture.status_code == 200
    assert valeur not in ecriture.text

    assert (tmp_path / ".env").read_text(encoding="utf-8") == (
        f"# note\nEXISTANTE=un\nNOUVELLE_CLE={valeur}\n"
    )

    etat = api_client.get("/api/v1/parametres/secrets")
    assert etat.status_code == 200
    assert valeur not in etat.text
    cles = [entree["cle"] for entree in etat.json()["entrees"]]
    assert cles == ["EXISTANTE", "NOUVELLE_CLE"]

    assert api_client.delete("/api/v1/parametres/secrets/NOUVELLE_CLE").status_code == 204
    assert (tmp_path / ".env").read_text(encoding="utf-8") == "# note\nEXISTANTE=un\n"


def test_api_refuse_une_valeur_invalide_sans_toucher_au_fichier(
    api_client: TestClient, tmp_path, monkeypatch
) -> None:
    """Une valeur refusée par la configuration n'est pas écrite du tout."""
    from app.infrastructure import env_file

    monkeypatch.setattr(env_file, "RACINE_BACKEND", tmp_path)
    avant = "AGENT_MAX_ITERATIONS=50\n"
    (tmp_path / ".env").write_text(avant, encoding="utf-8")
    _brancher_admin(api_client)

    reponse = api_client.put(
        "/api/v1/parametres/secrets/AGENT_MAX_ITERATIONS", json={"valeur": "pas-un-entier"}
    )

    assert reponse.status_code == 422
    assert (tmp_path / ".env").read_text(encoding="utf-8") == avant
