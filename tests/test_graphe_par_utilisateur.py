"""Tests — résolution du graphe dans le contexte de l'appelant (AG-UI).

L'endpoint AG-UI est monté **une fois au demarrage**, sans utilisateur : le
graphe y etait capture, donc fige au contexte global forever. Le proxy le
resout a chaque run. Ces tests verrouillent trois choses :

1. le graphe est demande **par compte** ;
2. le courtcache ne fait pas deborder d'un compte vers un autre ;
3. hors requete, c'est le contexte global servi (comportement historique).

Ils n'utilisent pas le graphe reel : un faux graphe suffit, et aucun acces
reseau n'est declenche.
"""

from __future__ import annotations

import pytest

from app.agents import graphe_par_utilisateur as module
from app.agents.graphe_par_utilisateur import GrapheParUtilisateur


class _FauxGraphe:
    def __init__(self, nom: str) -> None:
        self.nom = nom

    async def astream_events(self, *args, **kwargs):  # pragma: no cover - delegue
        return iter(())

    @property
    def nodes(self) -> dict[str, str]:
        return {"model": "model", "tools": "tools"}


@pytest.fixture
def lectures(monkeypatch):
    """Trace les (agent_id, utilisateur_id) demandés au manager."""
    demandes: list[tuple[str, str | None]] = []

    class _Manager:
        def graph(self, agent_id: str, utilisateur_id: str | None = None):
            demandes.append((agent_id, utilisateur_id))
            return _FauxGraphe(f"{agent_id}:{utilisateur_id}")

    monkeypatch.setattr(module, "demande_par_effectif", lambda: None)
    import app.agents.manager as manager_module

    monkeypatch.setattr(manager_module, "get_runtime_manager", lambda: _Manager())
    return demandes


def test_hors_requete_cest_le_contexte_global(lectures) -> None:
    """Aucun demandeur pose : le graphe global, exactement comme avant."""
    proxy = GrapheParUtilisateur("agent_rh")

    assert proxy.resoudre().nom == "agent_rh:None"
    assert lectures == [("agent_rh", None)]


def test_le_graphe_est_demande_par_compte(lectures, monkeypatch) -> None:
    """Le proxy passe l'identifiant pose par la garde AG-UI au manager."""
    monkeypatch.setattr(module, "demande_par_effectif", lambda: "compte-A")
    proxy = GrapheParUtilisateur("agent_statistique")

    assert proxy.resoudre().nom == "agent_statistique:compte-A"
    assert lectures == [("agent_statistique", "compte-A")]


def test_deux_comptes_ne_partagent_pas_le_meme_graphe(lectures, monkeypatch) -> None:
    """A puis B, dans la meme seconde : chacun son graphe.

    Le courtcache est la source de risque evidente — il doit etre indexe par
    le compte, jamais seulement par l'instant.
    """
    courant = {"id": "compte-A"}
    monkeypatch.setattr(module, "demande_par_effectif", lambda: courant["id"])
    proxy = GrapheParUtilisateur("agent_rh")

    assert proxy.resoudre().nom == "agent_rh:compte-A"
    courant["id"] = "compte-B"
    assert proxy.resoudre().nom == "agent_rh:compte-B"


def test_le_courtcache_reduit_les_resolutions(lectures, monkeypatch) -> None:
    """Plusieurs acces dans un meme run ne relisent pas la base a chaque fois."""
    monkeypatch.setattr(module, "demande_par_effectif", lambda: "compte-A")
    proxy = GrapheParUtilisateur("agent_rh")

    for _ in range(5):
        proxy.resoudre()

    assert len(lectures) == 1, "un run ne doit costing qu'une resolution"


def test_le_courtcache_expire(lectures, monkeypatch) -> None:
    """Au-dela de la duree, on relit : un changement de MCP prend effet."""
    monkeypatch.setattr(module, "demande_par_effectif", lambda: "compte-A")
    monkeypatch.setattr(module, "DUREE_MEMO_S", 0.0)
    proxy = GrapheParUtilisateur("agent_rh")

    proxy.resoudre()
    proxy.resoudre()

    assert len(lectures) == 2


def test_attributs_dunder_non_delegues(lectures) -> None:
    """`__getattr__` ne doit pas detourner les introspection Python."""
    proxy = GrapheParUtilisateur("agent_rh")

    # ``__getstate__`` est exclu : Python 3.11+ le definit sur ``object``,
    # il est donc resolu sans passer par ``__getattr__`` — ce serait tester
    # Python, pas le proxy.
    for nom in ("__deepcopy__", "__iter__", "__montage_magique"):
        with pytest.raises(AttributeError):
            getattr(proxy, nom)


def test_les_schemas_viennent_du_graphe_du_compte(lectures, monkeypatch) -> None:
    """Les schemas lus par la bibliotheque sont ceux du graphe resolu.

    C'etait l'inquiétude qui bloquait ce raccordement : si les schemas
    venaient du montage (donc du global) pendant que le run part du graphe
    personnel, l'agent annoncerait des outils qu'il n'a pas.
    """
    monkeypatch.setattr(module, "demande_par_effectif", lambda: "compte-A")
    proxy = GrapheParUtilisateur("agent_rh")

    assert proxy.nodes == {"model": "model", "tools": "tools"}
