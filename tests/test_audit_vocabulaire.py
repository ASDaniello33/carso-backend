"""Vocabulaire du journal d'audit — unités pures.

Le filtre d'audit affichait des clés techniques (« Effectuee », « Metadonnees
modifiees ») : ces tests verrouillent le fait que **chaque** action tracée porte
un nom français écrit à la main, et que les actions génériques disent l'entité.
"""

import pytest

from app.domain.audit_vocabulaire import (
    ACTIONS_PAR_ENTITE,
    ENTITES_SUPPRIMEES,
    LIBELLES_ACTION,
    LIBELLES_HISTORIQUES,
    libelle_action,
)
from app.domain.enums import ActionAudit


def test_chaque_action_tracee_a_un_libelle_explicite() -> None:
    """Sans libellé explicite, l'interface retomberait sur la clé technique."""
    manquantes = sorted(
        action.value for action in ActionAudit if action.value not in LIBELLES_ACTION
    )
    assert manquantes == [], f"actions sans libellé : {manquantes}"


def test_aucun_libelle_ne_laisse_passer_une_cle_technique() -> None:
    """Un libellé est une phrase, pas un identifiant (ni point, ni souligné)."""
    techniques = {
        action: libelle
        for action, libelle in LIBELLES_ACTION.items()
        if "." in libelle or "_" in libelle
    }
    assert techniques == {}


@pytest.mark.parametrize(
    ("action", "attendu"),
    [
        (ActionAudit.MISSION_CREEE.value, "Mission créée"),
        (ActionAudit.OFFRE_PUBLIEE.value, "Offre publiée"),
        (ActionAudit.DOCUMENT_PURGE.value, "Document purgé définitivement"),
        (ActionAudit.UTILISATEUR_SUPPRIME.value, "Compte supprimé"),
        (ActionAudit.SERVEUR_MCP_SUPPRIME.value, "Serveur MCP supprimé"),
    ],
)
def test_libelles_des_actions_courantes(action: str, attendu: str) -> None:
    assert libelle_action(action) == attendu


def test_suppression_dit_l_entite_touchee() -> None:
    """``suppression.effectuee`` seul ne dit rien : le libellé porte l'entité."""
    action = ActionAudit.SUPPRESSION_EFFECTUEE.value
    assert action in ACTIONS_PAR_ENTITE
    assert libelle_action(action, "offres") == "Suppression d'une offre de formation"
    assert libelle_action(action, "appels") == "Suppression d'un appel à proposition"
    assert libelle_action(action, "missions") == "Suppression d'une mission"


def test_entite_inconnue_ne_produit_pas_de_case_vide() -> None:
    """Journal d'une version antérieure, entité ajoutée plus tard : on dit « un élément »."""
    action = ActionAudit.SUPPRESSION_EFFECTUEE.value
    assert libelle_action(action, None) == "Suppression d'un élément"
    assert libelle_action(action, "ressource_disparue") == "Suppression d'un élément"


def test_les_actions_historiques_sont_nommees() -> None:
    """Des versions antérieures ont écrit des clés qui ne sont plus au vocabulaire.

    Elles sont toujours dans le journal (`publication_offre`, `affectation_equipe`…)
    et remonteraient dans l'historique d'une offre ou d'un appel : faute de
    libellé, elles s'afficheraient « Publication Offre », soit exactement le
    constat du 06/10. Chacune a donc son nom, et il est déjà en français.
    """
    for cle, libelle in LIBELLES_HISTORIQUES.items():
        assert libelle_action(cle) == libelle
        assert "." not in libelle and "_" not in libelle
    assert libelle_action("publication_offre") == "Offre publiée"
    assert libelle_action("affectation_equipe") == "Proposition d'affectation"


def test_action_inconnue_reste_lisible() -> None:
    """Vocabulaire étendu sans libellé : la trace reste affichée, jamais masquée."""
    assert libelle_action("nouveau_domaine.nouvelle_action") == "Nouvelle action"
    assert libelle_action("") == ""


def test_entites_supprimees_couvrent_les_ressources_supprimables() -> None:
    """Tout type supprimable côté API doit avoir sa forme articulée."""
    from app.application.services.suppression_service import TYPES_SUPPRIMABLES

    manquantes = sorted(TYPES_SUPPRIMABLES - set(ENTITES_SUPPRIMEES))
    assert manquantes == [], f"entités supprimables sans libellé : {manquantes}"
