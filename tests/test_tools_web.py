"""Tools web — recherche fédérée (LangSearch + DuckDuckGo) et lecture de pages.

Aucun appel réseau : le transport httpx est remplacé par un ``httpx.MockTransport``
qui rejoue la surface réellement consommée (POST /v1/web-search, page publique de
résultats, GET/POST d'une page), et la résolution DNS par une adresse publique
fixe — les gardes anti-SSRF sont donc exercées pour de vrai.
"""

from __future__ import annotations

import ipaddress
import json
import socket
from collections.abc import Callable
from typing import Any
from urllib.parse import quote

import httpx
import pytest

from app.core.config import Settings
from app.tools import web
from app.tools.permissions import AgentIdentity, PermissionPolicy
from app.tools.web import (
    FetchUrlInput,
    ReadUrlsInput,
    WebSearchInput,
    attacher_outils_web,
    build_fetch_url_tool,
    build_read_urls_tool,
    build_web_search_tool,
)

IDENTITE = AgentIdentity(agent_id="agent_test")
POLICY_WEB = PermissionPolicy(allowed_capabilities=frozenset({"web_search"}))
POLICY_VIDE = PermissionPolicy(allowed_capabilities=frozenset())

#: Adresse publique fixe : les gardes anti-SSRF passent, sans DNS réel.
PUBLIQUE = "93.184.216.34"
ADRESSE = "https://exemple.org/article"
ADRESSE_2 = "https://exemple.org/guide"

PAGE = (
    "<html><head><title>Barème officiel</title></head><body>"
    "<nav>Menu du site</nav>"
    "<article><h1>Barème 2026</h1><p>Le barème de la formation professionnelle "
    "distingue les niveaux et les zones d'intervention.</p></article>"
    "<footer>Pied de page</footer></body></html>"
)


def _resoudre(hote: str, port: Any, **kwargs: Any) -> list[Any]:
    """Résolution de test : un nom vise une adresse publique, une IP reste elle-même.

    Aucun DNS réel n'est consulté, mais une adresse littérale privée
    (``127.0.0.1``, ``10.1.2.3``) le reste : les gardes anti-SSRF sont donc
    exercées pour de vrai, y compris après une redirection.
    """
    del kwargs
    try:
        adresse = str(ipaddress.ip_address(hote))
    except ValueError:
        adresse = PUBLIQUE
    return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (adresse, port or 0))]


@pytest.fixture
def hote_public(monkeypatch: pytest.MonkeyPatch) -> None:
    """Résolution fixe : les tests ne dépendent pas d'un DNS réel."""
    monkeypatch.setattr(socket, "getaddrinfo", _resoudre)


def _settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "langsearch_api_key": "cle-test-1234",
        "web_search_max_results": 5,
    }
    base.update(overrides)
    return Settings(_env_file=None, **base)


def _reponse_langsearch(*urls: str, resumes: list[str] | None = None) -> str:
    textes = resumes if resumes is not None else [f"Résumé {i}" for i in range(len(urls))]
    resultats = [
        {"name": f"Résultat {i}", "url": url, "snippet": resume}
        for i, (url, resume) in enumerate(zip(urls, textes, strict=True))
    ]
    return json.dumps({"code": 200, "data": {"webPages": {"value": resultats}}})


def _page_resultats(*lignes: tuple[str, str]) -> str:
    """Page publique DuckDuckGo : un bloc ``div.result`` par résultat trouvé."""
    blocs = "".join(
        '<div class="result">'
        f'<a class="result__a" href="//duckduckgo.com/l/?uddg={quote(url, safe="")}">{titre}</a>'
        f'<a class="result__snippet">{titre} — extrait</a>'
        "</div>"
        for titre, url in lignes
    )
    return f"<html><body>{blocs}</body></html>"


def _transport(route: Callable[[httpx.Request], httpx.Response]) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(route))


# --- Recherche web : permission, clé, schéma ---------------------------------


def test_permission_absente_refusee() -> None:
    tool = build_web_search_tool(
        identite=IDENTITE,
        policy=POLICY_VIDE,
        settings=_settings(),
        client=_transport(lambda r: httpx.Response(200, text=_reponse_langsearch("https://a.test/1"))),
    )
    sortie = tool.invoke({"requete": "formation"})
    assert sortie["ok"] is False
    assert sortie["error"]["code"] == "permission_denied"


def test_reseau_injoignable_est_une_erreur_explicite(monkeypatch: pytest.MonkeyPatch) -> None:
    """Un ``ConnectTimeout`` est dit à l'agent (erreur métier), jamais brut.

    Laissé traverser, il crasherait le graphe LangGraph et interromprait tout
    le run AG-UI (incident vu en production le 18/10/2026).
    """
    appels = {"post": 0}

    def _route(request: httpx.Request) -> httpx.Response:
        appels["post"] += 1
        raise httpx.ConnectTimeout("timed out", request=request)

    monkeypatch.setattr(web, "_attendre", lambda _secondes: None)
    tool = build_web_search_tool(
        identite=IDENTITE,
        policy=POLICY_WEB,
        settings=_settings(),
        client=_transport(_route),
    )
    sortie = tool.invoke({"requete": "formation", "sources": ["langsearch"]})
    assert sortie["ok"] is False
    assert "injoignable" in sortie["error"]["message"]
    assert "ConnectTimeout" in sortie["error"]["message"]
    assert appels["post"] == 2, "un échec réseau doit être retenté (_TENTATIVES), puis dit"


def test_corps_langsearch_illisible_est_une_erreur_explicite() -> None:
    """Un 200 non-JSON de LangSearch ne sort pas en JSONDecodeError brut."""
    tool = build_web_search_tool(
        identite=IDENTITE,
        policy=POLICY_WEB,
        settings=_settings(),
        client=_transport(lambda r: httpx.Response(200, text="<html>anti-bot</html>")),
    )
    sortie = tool.invoke({"requete": "formation", "sources": ["langsearch"]})
    assert sortie["ok"] is False
    assert "illisible" in sortie["error"]["message"]


def test_cle_absente_erreur_explicite() -> None:
    """Sans clé : erreur explicite quand LangSearch est la seule source demandée."""
    appels: list[httpx.Request] = []

    def _route(request: httpx.Request) -> httpx.Response:
        appels.append(request)
        return httpx.Response(200, text=_reponse_langsearch("https://a.test/1"))

    tool = build_web_search_tool(
        identite=IDENTITE,
        policy=POLICY_WEB,
        settings=_settings(langsearch_api_key=None),
        client=_transport(_route),
    )
    sortie = tool.invoke({"requete": "formation", "sources": ["langsearch"]})
    assert sortie["ok"] is False
    assert "LANGSEARCH_API_KEY" in sortie["error"]["message"]
    assert appels == []  # aucun appel HTTP émis


def test_requete_vide_refusee_par_le_schema() -> None:
    tool = build_web_search_tool(
        identite=IDENTITE,
        policy=POLICY_WEB,
        settings=_settings(),
        client=_transport(lambda r: httpx.Response(200)),
    )
    sortie = tool.invoke({"requete": ""})
    assert sortie["ok"] is False


def test_fraicheur_inconnue_refusee_par_le_schema() -> None:
    """Une fraîcheur hors vocabulaire est refusée, jamais ignorée en silence."""
    tool = build_web_search_tool(
        identite=IDENTITE,
        policy=POLICY_WEB,
        settings=_settings(),
        client=_transport(lambda r: httpx.Response(200)),
    )
    sortie = tool.invoke({"requete": "formation", "freshness": "la semaine dernière"})
    assert sortie["ok"] is False
    assert "fraîcheur" in sortie["error"]["message"]


# --- Recherche web : appel réel (transport simulé) ---------------------------


def test_recherche_reelle_avec_mock_transport() -> None:
    capture: dict[str, Any] = {}
    urls = [f"https://exemple.org/{i}" for i in range(3)]

    def _route(request: httpx.Request) -> httpx.Response:
        capture["auth"] = request.headers.get("Authorization")
        capture["url"] = str(request.url)
        capture["corps"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(200, text=_reponse_langsearch(*urls))

    tool = build_web_search_tool(
        identite=IDENTITE,
        policy=POLICY_WEB,
        settings=_settings(),
        client=_transport(_route),
    )
    sortie = tool.invoke(
        {"requete": "appel offre formation 2026", "max_results": 3, "sources": ["langsearch"]}
    )

    assert capture["auth"] == "Bearer cle-test-1234"
    assert capture["corps"]["query"] == "appel offre formation 2026"
    assert "/v1/web-search" in str(capture.get("url", "/v1/web-search"))
    assert sortie["nb_resultats"] == 3
    assert sortie["resultats"][0]["titre"] == "Résultat 0"
    assert sortie["resultats"][0]["url"] == "https://exemple.org/0"
    assert sortie["resultats"][0]["resume"] == "Résumé 0"
    assert sortie["resultats"][0]["sources"] == ["langsearch"]
    assert "date_publication" in sortie["resultats"][0]
    assert sortie["sources_utilisees"] == ["langsearch"]
    assert sortie["avertissements"] == []
    # La clé n'apparaît jamais dans la sortie du tool.
    assert "cle-test-1234" not in json.dumps(sortie)


def test_plafond_settings_serre_la_borne() -> None:
    capture: dict[str, Any] = {}

    def _route(request: httpx.Request) -> httpx.Response:
        capture["corps"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(
            200, text=_reponse_langsearch(*[f"https://exemple.org/{i}" for i in range(10)])
        )

    tool = build_web_search_tool(
        identite=IDENTITE,
        policy=POLICY_WEB,
        settings=_settings(web_search_max_results=5),
        client=_transport(_route),
    )
    sortie = tool.invoke({"requete": "test", "max_results": 8, "sources": ["langsearch"]})
    assert capture["corps"]["count"] == 5  # 8 demandé (<= borne schéma), 5 autorisés
    assert sortie["nb_resultats"] <= 5


def test_texte_complet_et_domaines() -> None:
    capture: dict[str, Any] = {}

    def _route(request: httpx.Request) -> httpx.Response:
        capture["url"] = str(request.url)
        capture["corps"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(
            200,
            text=json.dumps(
                {
                    "code": 200,
                    "log_id": "log-1",
                    "data": {
                        "webPages": {
                            "value": [
                                {
                                    "name": "JO",
                                    "url": "https://sgg.gov.ma/x",
                                    "text": "Texte officiel…",
                                    "datePublished": "2026-01-01",
                                }
                            ]
                        }
                    },
                    "usage": {"input_tokens": 4, "output_tokens": 12},
                }
            ),
        )

    tool = build_web_search_tool(
        identite=IDENTITE,
        policy=POLICY_WEB,
        settings=_settings(),
        client=_transport(_route),
    )
    sortie = tool.invoke(
        {
            "requete": "cahier des charges formation",
            "texte_complet": True,
            "include_domains": ["sgg.gov.ma"],
            "freshness": "oneYear",
            "sources": ["langsearch"],
        }
    )
    assert capture["url"].endswith("/v1/web-search")
    assert capture["corps"]["contents"]["text"]["max_characters"] == 3000
    assert capture["corps"]["includeDomains"] == ["sgg.gov.ma"]
    assert capture["corps"]["freshness"] == "oneYear"
    assert sortie["resultats"][0]["resume"].startswith("Texte officiel")
    assert sortie["usage"]["output_tokens"] == 12
    assert sortie["log_id"] == "log-1"


def test_erreur_http_explicite() -> None:
    tool = build_web_search_tool(
        identite=IDENTITE,
        policy=POLICY_WEB,
        settings=_settings(),
        client=_transport(lambda r: httpx.Response(401, text="denied")),
    )
    sortie = tool.invoke({"requete": "test", "sources": ["langsearch"]})
    assert sortie["ok"] is False
    assert "401" in sortie["error"]["message"]


def test_client_par_defaut_sans_reseau() -> None:
    """Sans client injecté : construction OK, l'échec réseau est **dit** (payload).

    Le port 1 est garanti non écouté ; l'erreur de connexion devient un
    avertissement puis un refus explicite — jamais une exception brute qui
    remonterait dans le graphe, jamais un secret dans le message.
    """
    tool = build_web_search_tool(
        identite=IDENTITE,
        policy=POLICY_WEB,
        settings=_settings(langsearch_base_url="http://127.0.0.1:1"),
    )
    sortie = tool.invoke({"requete": "test", "sources": ["langsearch"]})
    assert sortie["ok"] is False
    assert "LangSearch indisponible" in sortie["error"]["message"]
    assert "cle-test-1234" not in json.dumps(sortie)


def test_refus_temporaire_est_retente(monkeypatch: pytest.MonkeyPatch) -> None:
    """Un « 503 » passager vaut une seconde tentative avant d'être annoncé."""
    appels = {"n": 0}

    def _route(request: httpx.Request) -> httpx.Response:
        appels["n"] += 1
        if appels["n"] == 1:
            return httpx.Response(503, text="indisponible")
        return httpx.Response(200, text=_reponse_langsearch("https://exemple.org/1"))

    monkeypatch.setattr(web, "_attendre", lambda _secondes: None)
    tool = build_web_search_tool(
        identite=IDENTITE,
        policy=POLICY_WEB,
        settings=_settings(),
        client=_transport(_route),
    )
    sortie = tool.invoke({"requete": "test", "sources": ["langsearch"]})
    assert sortie["ok"] is True
    assert appels["n"] == 2
    assert sortie["nb_resultats"] == 1


# --- Recherche web : fusion des deux fournisseurs ----------------------------


def _route_fusion(request: httpx.Request) -> httpx.Response:
    if "langsearch" in request.url.host or request.url.path.endswith("/v1/web-search"):
        return httpx.Response(
            200,
            text=_reponse_langsearch(
                "https://exemple.org/a", "https://exemple.org/b"
            ),
        )
    return httpx.Response(
        200,
        text=_page_resultats(
            ("Moteur A", "https://exemple.org/a"),
            ("Moteur C", "https://exemple.org/c"),
        ),
    )


def test_fusion_alternee_et_dedoublonnage() -> None:
    """Les deux sources sont fondues : une adresse vue deux fois reste une entrée."""
    tool = build_web_search_tool(
        identite=IDENTITE,
        policy=POLICY_WEB,
        settings=_settings(),
        client=_transport(_route_fusion),
    )
    sortie = tool.invoke({"requete": "barème formation", "max_results": 5})

    assert sortie["sources_utilisees"] == ["langsearch", "duckduckgo"]
    urls = [item["url"] for item in sortie["resultats"]]
    assert urls == [
        "https://exemple.org/a",  # meilleur LangSearch, en tête
        "https://exemple.org/c",  # meilleur DuckDuckGo, juste après
        "https://exemple.org/b",
    ]
    # La première garde son résumé ; un résumé manquant est complété par l'autre.
    assert sortie["resultats"][0]["sources"] == ["langsearch", "duckduckgo"]
    assert sortie["resultats"][0]["resume"] == "Résumé 0"
    assert sortie["resultats"][1]["sources"] == ["duckduckgo"]
    assert sortie["resultats"][1]["resume"] == "Moteur C — extrait"


def test_duckduckgo_remplace_langsearch_en_panne() -> None:
    """Clé absente : le moteur public répond, et la réponse dit ce qui manque."""
    tool = build_web_search_tool(
        identite=IDENTITE,
        policy=POLICY_WEB,
        settings=_settings(langsearch_api_key=None),
        client=_transport(_route_fusion),
    )
    sortie = tool.invoke({"requete": "barème formation"})

    assert sortie["ok"] is True
    assert sortie["sources_utilisees"] == ["duckduckgo"]
    assert sortie["nb_resultats"] == 2
    assert all(item["sources"] == ["duckduckgo"] for item in sortie["resultats"])
    assert any("LANGSEARCH_API_KEY" in motif for motif in sortie["avertissements"])


def test_langsearch_repond_quand_duckduckgo_refuse() -> None:
    def _route(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/v1/web-search"):
            return httpx.Response(200, text=_reponse_langsearch("https://exemple.org/a"))
        return httpx.Response(403, text="bloqué")

    tool = build_web_search_tool(
        identite=IDENTITE,
        policy=POLICY_WEB,
        settings=_settings(),
        client=_transport(_route),
    )
    sortie = tool.invoke({"requete": "formation"})
    assert sortie["ok"] is True
    assert sortie["sources_utilisees"] == ["langsearch"]
    assert any("DuckDuckGo indisponible" in motif for motif in sortie["avertissements"])


def test_aucun_fournisseur_ne_repond_est_un_echec_dit() -> None:
    tool = build_web_search_tool(
        identite=IDENTITE,
        policy=POLICY_WEB,
        settings=_settings(langsearch_api_key=None),
        client=_transport(lambda r: httpx.Response(500, text="panne")),
    )
    sortie = tool.invoke({"requete": "formation"})
    assert sortie["ok"] is False
    assert "LangSearch indisponible" in sortie["error"]["message"]
    assert "DuckDuckGo indisponible" in sortie["error"]["message"]


def test_sources_restreintes_a_duckduckgo() -> None:
    """``sources`` restreint vraiment : aucun appel à l'API non demandée."""
    vus: list[str] = []

    def _route(request: httpx.Request) -> httpx.Response:
        vus.append(str(request.url))
        return httpx.Response(200, text=_page_resultats(("Moteur", "https://exemple.org/c")))

    tool = build_web_search_tool(
        identite=IDENTITE,
        policy=POLICY_WEB,
        settings=_settings(),
        client=_transport(_route),
    )
    sortie = tool.invoke({"requete": "formation", "sources": ["duckduckgo"]})
    assert sortie["nb_resultats"] == 1
    assert all("web-search" not in url for url in vus)


def test_fraicheur_traduite_pour_chaque_fournisseur() -> None:
    """« semaine » est la même demande : ``oneWeek`` pour l'API, ``w`` pour le moteur."""
    captures: dict[str, Any] = {}

    def _route(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/v1/web-search"):
            captures["langsearch"] = json.loads(request.content.decode("utf-8"))["freshness"]
            return httpx.Response(200, text=_reponse_langsearch("https://exemple.org/a"))
        captures["duckduckgo"] = request.content.decode("utf-8")
        return httpx.Response(200, text=_page_resultats(("Moteur", "https://exemple.org/c")))

    tool = build_web_search_tool(
        identite=IDENTITE,
        policy=POLICY_WEB,
        settings=_settings(),
        client=_transport(_route),
    )
    sortie = tool.invoke({"requete": "formation", "freshness": "semaine"})

    assert captures["langsearch"] == "oneWeek"
    assert "df=w" in captures["duckduckgo"]
    assert sortie["avertissements"] == []


def test_fraicheur_en_date_avertit_pour_duckduckgo() -> None:
    """Une plage de dates n'est comprise que par l'API : le dire, jamais l'ignorer."""
    tool = build_web_search_tool(
        identite=IDENTITE,
        policy=POLICY_WEB,
        settings=_settings(),
        client=_transport(_route_fusion),
    )
    sortie = tool.invoke({"requete": "formation", "freshness": "2026-01-01"})
    assert any("DuckDuckGo" in motif for motif in sortie["avertissements"])


# --- Lecture d'une page : ``fetch_url`` --------------------------------------


def _fetch(route: Callable[[httpx.Request], httpx.Response], **overrides: Any) -> Any:
    return build_fetch_url_tool(
        identite=IDENTITE,
        policy=POLICY_WEB,
        settings=_settings(**overrides),
        client=_transport(route),
    )


def test_fetch_url_extrait_titre_et_texte(hote_public: None) -> None:
    def _route(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, text=PAGE, headers={"content-type": "text/html; charset=utf-8"}
        )

    tool = _fetch(_route)
    sortie = tool.invoke({"url": ADRESSE})

    assert sortie["ok"] is True
    assert sortie["statut"] == 200
    assert sortie["titre"] == "Barème officiel"
    assert "Barème 2026" in sortie["contenu"]
    assert "Menu du site" not in sortie["contenu"]
    assert "Pied de page" not in sortie["contenu"]
    assert sortie["tronque"] is False


def test_fetch_url_rend_le_json() -> None:
    def _route(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text='{"mission": "M-1", "sessions": 3}',
            headers={"content-type": "application/json"},
        )

    tool = _fetch(_route)
    sortie = tool.invoke({"url": ADRESSE})
    assert sortie["ok"] is True
    assert json.loads(sortie["contenu"]) == {"mission": "M-1", "sessions": 3}


def test_fetch_url_transmet_les_parametres_get(hote_public: None) -> None:
    vues: dict[str, Any] = {}

    def _route(request: httpx.Request) -> httpx.Response:
        vues["params"] = dict(request.url.params)
        vues["methode"] = request.method
        return httpx.Response(200, text="ok", headers={"content-type": "text/plain"})

    tool = _fetch(_route)
    sortie = tool.invoke({"url": ADRESSE, "parametres": {"annee": "2026"}})
    assert sortie["ok"] is True
    assert vues["methode"] == "GET"
    assert vues["params"] == {"annee": "2026"}


def test_fetch_url_post_envoie_le_corps_json(hote_public: None) -> None:
    vues: dict[str, Any] = {}

    def _route(request: httpx.Request) -> httpx.Response:
        vues["methode"] = request.method
        vues["corps"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(200, text="{}", headers={"content-type": "application/json"})

    tool = _fetch(_route)
    sortie = tool.invoke({"url": ADRESSE, "methode": "POST", "parametres": {"q": "missions"}})
    assert sortie["ok"] is True
    assert vues == {"methode": "POST", "corps": {"q": "missions"}}


def test_fetch_url_type_non_supporte_dit(hote_public: None) -> None:
    tool = _fetch(
        lambda r: httpx.Response(200, content=b"\x89PNG", headers={"content-type": "image/png"})
    )
    sortie = tool.invoke({"url": ADRESSE})
    assert sortie["ok"] is True
    assert sortie["contenu"] == ""
    assert "Type non supporté" in sortie["erreur"]


def test_fetch_url_tronque_au_plafond(hote_public: None) -> None:
    long_page = PAGE.replace("Le barème", "Le barème " + "détaillé " * 400)

    def _route(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=long_page, headers={"content-type": "text/html"})

    tool = _fetch(_route, web_fetch_max_chars=300)
    sortie = tool.invoke({"url": ADRESSE})
    assert len(sortie["contenu"]) == 300
    assert sortie["tronque"] is True


def test_fetch_url_refuse_un_hote_non_public() -> None:
    """Une adresse qui vise la boucle locale est refusée avant tout départ."""
    appels: list[str] = []
    tool = _fetch(lambda r: appels.append(str(r.url)) or httpx.Response(200, text=PAGE))
    sortie = tool.invoke({"url": "http://127.0.0.1:8080/administration"})
    assert sortie["ok"] is False
    assert "publique" in sortie["error"]["message"]
    assert appels == []


def test_fetch_url_refuse_une_redirection_vers_le_reseau_interne(hote_public: None) -> None:
    """Chaque saut repasse par la garde : une redirection ne contourne pas le refus."""
    vus: list[str] = []

    def _route(request: httpx.Request) -> httpx.Response:
        vus.append(request.url.host)
        return httpx.Response(302, headers={"location": "http://10.1.2.3/interne"})

    tool = _fetch(_route)
    sortie = tool.invoke({"url": ADRESSE})
    assert sortie["ok"] is False
    assert "publique" in sortie["error"]["message"]
    assert vus == ["exemple.org"], "la cible interne n'est jamais contactée"


def test_fetch_url_erreur_reseau_devient_une_entree_lisible(hote_public: None) -> None:
    def _route(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connexion refusée")

    tool = _fetch(_route)
    sortie = tool.invoke({"url": ADRESSE})
    assert sortie["ok"] is True
    assert sortie["contenu"] == ""
    assert sortie["erreur"].startswith("Erreur réseau")


def test_fetch_url_permission_absente(hote_public: None) -> None:
    tool = build_fetch_url_tool(
        identite=IDENTITE,
        policy=POLICY_VIDE,
        settings=_settings(),
        client=_transport(lambda r: httpx.Response(200, text=PAGE)),
    )
    sortie = tool.invoke({"url": ADRESSE})
    assert sortie["ok"] is False
    assert sortie["error"]["code"] == "permission_denied"


# --- Lecture de plusieurs pages : ``read_urls`` ------------------------------


def _read(route: Callable[[httpx.Request], httpx.Response], **overrides: Any) -> Any:
    return build_read_urls_tool(
        identite=IDENTITE,
        policy=POLICY_WEB,
        settings=_settings(**overrides),
        client=_transport(route),
    )


def test_read_urls_lit_plusieurs_pages(hote_public: None) -> None:
    def _route(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=PAGE, headers={"content-type": "text/html"})

    tool = _read(_route)
    sortie = tool.invoke({"urls": [ADRESSE, ADRESSE_2]})

    assert sortie["ok"] is True
    assert sortie["nb_urls"] == 2
    assert sortie["nb_lues"] == 2
    assert [entree["url"] for entree in sortie["resultats"]] == [ADRESSE, ADRESSE_2]
    assert all("Barème 2026" in entree["contenu"] for entree in sortie["resultats"])
    assert sortie["caracteres_total"] > 0


def test_read_urls_dedoublonne_les_adresses(hote_public: None) -> None:
    appels = {"n": 0}

    def _route(request: httpx.Request) -> httpx.Response:
        appels["n"] += 1
        return httpx.Response(200, text=PAGE, headers={"content-type": "text/html"})

    tool = _read(_route)
    sortie = tool.invoke({"urls": [ADRESSE, ADRESSE + "/"]})
    assert sortie["nb_urls"] == 1
    assert appels["n"] == 1


def test_read_urls_refuse_au_dela_du_plafond() -> None:
    """Refus explicite : une liste tronquée laisserait croire que tout a été lu."""
    tool = _read(lambda r: httpx.Response(200, text=PAGE), web_read_urls_max=2)
    sortie = tool.invoke({"urls": ["https://a.test/1", "https://b.test/2", "https://c.test/3"]})
    assert sortie["ok"] is False
    assert "2" in sortie["error"]["message"]


def test_read_urls_une_adresse_refusee_laisse_lire_les_autres(hote_public: None) -> None:
    def _route(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=PAGE, headers={"content-type": "text/html"})

    tool = _read(_route)
    sortie = tool.invoke({"urls": ["http://127.0.0.1:9000/prive", ADRESSE]})

    assert sortie["ok"] is True
    assert sortie["nb_lues"] == 1
    assert "publique" in sortie["resultats"][0]["erreur"]
    assert "Barème 2026" in sortie["resultats"][1]["contenu"]


def test_read_urls_dit_une_adresse_en_echec(hote_public: None) -> None:
    def _route(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/absente":
            return httpx.Response(404, text="non trouvé", headers={"content-type": "text/html"})
        return httpx.Response(200, text=PAGE, headers={"content-type": "text/html"})

    tool = _read(_route)
    sortie = tool.invoke({"urls": ["https://exemple.org/absente", ADRESSE]})
    assert sortie["nb_lues"] == 1
    assert sortie["resultats"][0]["statut"] == 404
    assert sortie["resultats"][0]["erreur"] == "HTTP 404"


def test_read_urls_budget_epuise_dit_la_page_non_lue(hote_public: None) -> None:
    """Le budget atteint, l'adresse restante est **dite** non lue."""
    large = "<html><body><article><p>" + ("formation " * 200) + "</p></article></body></html>"

    def _route(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=large, headers={"content-type": "text/html"})

    tool = _read(
        _route,
        web_fetch_max_chars=1000,
        web_read_urls_max=3,
        web_read_urls_total_chars=1000,
    )
    sortie = tool.invoke({"urls": [ADRESSE, ADRESSE_2, "https://exemple.org/trois"]})

    assert sortie["nb_urls"] == 3
    assert sortie["caracteres_total"] == 1000
    assert sortie["resultats"][2]["erreur"].startswith("non lue")
    assert sortie["resultats"][2]["contenu"] == ""


def test_read_urls_permission_absente(hote_public: None) -> None:
    tool = build_read_urls_tool(
        identite=IDENTITE,
        policy=POLICY_VIDE,
        settings=_settings(),
        client=_transport(lambda r: httpx.Response(200, text=PAGE)),
    )
    sortie = tool.invoke({"urls": [ADRESSE]})
    assert sortie["ok"] is False
    assert sortie["error"]["code"] == "permission_denied"


# --- Attachement au contrat --------------------------------------------------


class _Definition:
    """Contrat minimal : seuls ``tools``, ``policy`` et ``agent_id`` sont lus."""

    agent_id = "agent_test"
    policy = POLICY_WEB


def test_attacher_outils_web_attache_ce_que_le_contrat_declare() -> None:
    class _Complet(_Definition):
        tools = ("langsearch_web_search", "fetch_url", "read_urls")

    class _Rien(_Definition):
        tools = ("query_analytics",)

    attaches = attacher_outils_web(_Complet(), ())
    assert [outil.name for outil in attaches] == [
        "langsearch_web_search",
        "fetch_url",
        "read_urls",
    ]
    assert attacher_outils_web(_Rien(), ("existante",)) == ("existante",)


# --- Schémas -----------------------------------------------------------------


@pytest.mark.parametrize("schema", [FetchUrlInput, ReadUrlsInput, WebSearchInput])
def test_les_schemas_sont_fermes(schema: type) -> None:
    """Un champ inconnu est refusé : le périmètre d'entrée n'est pas deviné."""
    import pydantic

    with pytest.raises(pydantic.ValidationError):
        schema.model_validate({"url": ADRESSE, "urls": [ADRESSE], "requete": "x", "inconnu": 1})


def test_parametres_par_defaut_documentes() -> None:
    """Sanité : les valeurs par défaut de Settings restent sûres."""
    reglages = Settings(_env_file=None)
    assert reglages.langsearch_api_key is None  # pas de clé implicite
    assert reglages.web_search_max_results == 5
    assert reglages.web_fetch_max_chars == 20_000
    assert reglages.web_fetch_max_bytes == 2_000_000
    assert reglages.web_read_urls_max == 5
    assert reglages.web_read_urls_total_chars == 60_000
    assert reglages.shell_allowed_roots == ""  # shell désactivé par défaut
