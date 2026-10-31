"""Tests — noms d'outils MCP conformes à la grammaire des fournisseurs.

Constat du 31/10 : un agent portant le connecteur « Parallel Search » échouait à
chaque run avec

    Invalid 'tools[34].function.name': string does not match pattern.
    Expected a string that matches the pattern '^[a-zA-Z0-9_\\.-]+$'.

``tool_name_prefix=True`` reprend le nom **affiché** du connecteur comme préfixe :
`Parallel Search_web_search`. Le fournisseur refuse alors **toute la requête**,
pas seulement l'outil fautif. Ces tests verrouillent :

- la normalisation (:func:`nom_outil_conforme`) et son unicité
  (:func:`nom_outil_unique`) ;
- le nom effectivement exposé par :func:`proteger_outil_mcp` ;
- la clé de connexion assainie (la racine du préfixe) ;
- la déduplication **avant** enveloppe : personnel prime sur global, et deux
  outils qui se normalisent pareil sont suffixés au lieu d'être perdus.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any
from uuid import uuid4

import pytest
from langchain_core.tools import StructuredTool

from app.agents.mcp import (
    LIMITE_NOM_OUTIL,
    nom_outil_conforme,
    nom_outil_unique,
    outils_mcp_pour,
    paquet_disponible,
    proteger_outil_mcp,
    vider_cache,
)
from app.domain.mcp import ServeurMcp

_SCHEMA = {
    "type": "object",
    "properties": {"requete": {"type": "string"}},
    "required": ["requete"],
}

#: Motif **positif** exigé par les fournisseurs (celui du message d'erreur).
_MOTIF_VALIDE = re.compile(r"^[a-zA-Z0-9_.-]+$")


# --- 1. Normalisation ---------------------------------------------------------------


def test_un_libelle_de_connecteur_devient_un_nom_valide() -> None:
    """« Parallel Search » produisait un espace dans le nom exposé au modèle."""
    assert nom_outil_conforme("Parallel Search_web_search") == "Parallel_Search_web_search"
    assert nom_outil_conforme("Parallel Search") == "Parallel_Search"
    assert _MOTIF_VALIDE.fullmatch(nom_outil_conforme("Parallel Search_web_search"))


@pytest.mark.parametrize(
    ("brut", "attendu"),
    [
        ("recherche_distante", "recherche_distante"),
        ("web.search-v2_x", "web.search-v2_x"),
        ("Outil (accents) été/été", "Outil_accents_ete_ete"),
        ("  valeurs   espacées  ", "valeurs_espacees"),
        ("...", "outil_mcp"),
        ("", "outil_mcp"),
    ],
)
def test_normalisation_attendue(brut: str, attendu: str) -> None:
    assert nom_outil_conforme(brut) == attendu


def test_un_nom_conforme_n_est_jamais_renomme() -> None:
    """La normalisation ne touche pas un nom sain (contrat des graphes existants)."""
    for nom in ("recherche_distante", "Parallel_Search_web_fetch", "web.search-v2_x"):
        assert nom_outil_conforme(nom) == nom


def test_un_nom_trop_long_est_borne_et_stable() -> None:
    long = "o" * 120
    nom = nom_outil_conforme(long)

    assert len(nom) <= LIMITE_NOM_OUTIL
    assert nom == nom_outil_conforme(long), "même entrée, même nom (cache des outils)"
    assert nom != nom_outil_conforme("o" * 119 + "p"), "deux noms longs ne se confondent pas"


def test_deux_noms_identiques_sont_suffixes() -> None:
    pris = {"web_search"}
    unique = nom_outil_unique("web_search", pris)

    assert unique == "web_search_2"
    assert unique not in pris
    assert nom_outil_unique("libre", pris) == "libre"


def test_un_nom_long_suffixe_reste_dans_la_limite() -> None:
    nom = "z" * LIMITE_NOM_OUTIL
    unique = nom_outil_unique(nom, {nom})

    assert len(unique) <= LIMITE_NOM_OUTIL
    assert unique != nom


# --- 2. Nom exposé par l'enveloppe --------------------------------------------------


def _outil(nom: str, resultat: str = "ok") -> StructuredTool:
    """Outil MCP simulé : async-only, schéma dict, comme ceux des adaptateurs."""

    async def _coroutine(**arguments: Any) -> str:
        _ = arguments
        return resultat

    return StructuredTool.from_function(
        coroutine=_coroutine,
        name=nom,
        description="Outil distant de test",
        args_schema=_SCHEMA,
    )


def test_proteger_outil_mcp_expose_un_nom_conforme() -> None:
    """Le préfixe du connecteur ne fuit plus jusqu'au fournisseur."""
    outil = proteger_outil_mcp(
        _outil("Parallel Search_web_search"), serveur="Parallel Search"
    )

    assert outil.name == "Parallel_Search_web_search"
    assert outil.description == "Outil distant de test", "le contrat lisible est conservé"
    assert outil.args_schema == _SCHEMA


def test_proteger_outil_mcp_assainit_un_nom_impose() -> None:
    """Aucun appelant ne peut réinjecter un nom invalide (défense en profondeur)."""
    outil = proteger_outil_mcp(_outil("peu importe"), serveur="s", nom="Mon Outil!")

    assert outil.name == "Mon_Outil_"
    assert _MOTIF_VALIDE.fullmatch(outil.name)


# --- 3. Chargement par agent : clé assainie et déduplication ------------------------


class _FauxClientMcp:
    """Client MCP simulé : rend les outils déclarés pour la clé de connexion."""

    connexions_recues: list[dict[str, Any]] = []
    outils_par_cle: dict[str, list[Any]] = {}

    def __init__(self, connexions: dict[str, Any], **options: Any) -> None:
        self.connexions = connexions
        self.options = options
        _FauxClientMcp.connexions_recues.append(connexions)

    async def get_tools(self) -> list[Any]:
        cle = next(iter(self.connexions))
        return list(_FauxClientMcp.outils_par_cle.get(cle, []))


@pytest.fixture(autouse=True)
def _client_neutre() -> Any:
    """Cache global et faux client remis à zéro entre les tests."""
    vider_cache()
    _FauxClientMcp.connexions_recues = []
    _FauxClientMcp.outils_par_cle = {}
    yield
    _FauxClientMcp.connexions_recues = []
    _FauxClientMcp.outils_par_cle = {}
    vider_cache()


def _serveur(nom: str, *, utilisateur_id: Any = None) -> ServeurMcp:
    return ServeurMcp(
        nom=nom,
        utilisateur_id=utilisateur_id,
        transport="streamable_http",
        url="https://exemple.test/mcp",
        arguments=[],
        agents=["agent_statistique"],
        en_tetes_chiffres=None,
        mode_authentification="aucune",
        actif=True,
    )


def test_outils_mcp_pour_assainit_la_cle_du_connecteur(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """La racine du préfixe est le nom affiché : c'est lui qu'il faut assainir."""
    if not paquet_disponible():  # pragma: no cover - extra MCP absent
        pytest.skip("langchain-mcp-adapters non installé")

    _FauxClientMcp.outils_par_cle = {
        "Parallel_Search": [_outil("Parallel_Search_web_search", "résultat")]
    }
    monkeypatch.setattr(
        "langchain_mcp_adapters.client.MultiServerMCPClient", _FauxClientMcp
    )

    outils = outils_mcp_pour("agent_statistique", [_serveur("Parallel Search")])

    assert list(_FauxClientMcp.connexions_recues[0]) == ["Parallel_Search"]
    assert [outil.name for outil in outils] == ["Parallel_Search_web_search"]
    assert _MOTIF_VALIDE.fullmatch(outils[0].name)


def test_outils_mcp_pour_suffixe_deux_outils_qui_se_normalisent_pareil(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Deux capacités distinctes ne se perdent pas : elles sont suffixées."""
    if not paquet_disponible():  # pragma: no cover - extra MCP absent
        pytest.skip("langchain-mcp-adapters non installé")

    _FauxClientMcp.outils_par_cle = {
        "mcp": [_outil("web search", "un"), _outil("web_search", "deux")]
    }
    monkeypatch.setattr(
        "langchain_mcp_adapters.client.MultiServerMCPClient", _FauxClientMcp
    )

    outils = outils_mcp_pour("agent_statistique", [_serveur("mcp")])

    assert [outil.name for outil in outils] == ["web_search", "web_search_2"]


def test_outils_mcp_pour_ecarte_le_global_masque_par_le_personnel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Régression : la déduplication était calculée **après** l'ajout des outils.

    Son résultat n'alimentait que le journal ; les deux outils de même nom
    entraient dans le graphe et LangGraph refusait le montage.
    """
    if not paquet_disponible():  # pragma: no cover - extra MCP absent
        pytest.skip("langchain-mcp-adapters non installé")

    _FauxClientMcp.outils_par_cle = {
        "perso": [_outil("recherche", "personnel")],
        "global": [_outil("recherche", "global")],
    }
    monkeypatch.setattr(
        "langchain_mcp_adapters.client.MultiServerMCPClient", _FauxClientMcp
    )

    outils = outils_mcp_pour(
        "agent_statistique",
        [_serveur("global"), _serveur("perso", utilisateur_id=uuid4())],
    )

    assert [outil.name for outil in outils] == ["recherche"]
    assert asyncio.run(outils[0].ainvoke({"requete": "x"})) == "personnel"
