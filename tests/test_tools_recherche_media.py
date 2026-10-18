"""Tools ``rechercher_image`` / ``rechercher_video`` / ``rechercher_document``.

Aucun appel réseau : le transport httpx est remplacé par un ``httpx.MockTransport``
qui rejoue la surface réellement consommée (page de recherche pour le jeton
``vqd``, verticales JSON, page de résultats, API de repli).

Les tests de repli portent l'incident de production du 18/10/2026 : depuis
l'hébergeur, DuckDuckGo ne répond pas (``ConnectTimeout``) — un tool qui ne tient
qu'à ce fournisseur est mort une fois déployé. Les replis (ddgs multi-moteurs,
Openverse pour les images) sont dès lors exercés comme les chemins réels qu'ils
sont.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest

from app.core.config import Settings
from app.tools import fournisseurs_ddgs
from app.tools.permissions import AgentIdentity, PermissionPolicy
from app.tools.recherche_media import (
    EXTENSIONS_DOCUMENT,
    RechercheDocumentInput,
    RechercheImageInput,
    RechercheVideoInput,
    build_recherche_media_tool,
)

IDENTITE = AgentIdentity(agent_id="agent_test")
POLICY_WEB = PermissionPolicy(allowed_capabilities=frozenset({"web_search"}))
POLICY_VIDE = PermissionPolicy(allowed_capabilities=frozenset())

PAGE_VQD = '<html><body><script>vqd="4-3141592653";</script></body></html>'

HOTE_DUCKDUCKGO = "duckduckgo.com"
HOTE_DOCUMENTS = "html.duckduckgo.com"
HOTE_OPENVERSE = "api.openverse.org"


def _settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {"web_search_max_results": 5}
    base.update(overrides)
    return Settings(_env_file=None, **base)


def _images(nb: int) -> dict[str, Any]:
    return {
        "results": [
            {
                "title": f"Image {i}",
                "url": f"https://exemple.org/page-{i}",
                "image": f"https://exemple.org/photo-{i}.jpg",
                "thumbnail": f"https://exemple.org/thumb-{i}.jpg",
                "width": 800,
                "height": 600,
                "source": "exemple.org",
            }
            for i in range(nb)
        ]
    }


def _videos(nb: int) -> dict[str, Any]:
    return {
        "results": [
            {
                "title": f"Video {i}",
                # Réalité du moteur : ``url`` est la page de lecture (celle que
                # l'utilisateur ouvre) et ``content`` l'adresse du fichier.
                "url": f"https://exemple.org/regarder-{i}",
                "content": f"https://exemple.org/video-{i}.mp4",
                "images": {"medium": f"https://exemple.org/v-{i}.jpg"},
                "duration": "12:34",
                "uploader": "Chaine",
                "published": "2026-01-02",
            }
            for i in range(nb)
        ]
    }


def _openverse(*resultats: dict[str, Any]) -> dict[str, Any]:
    return {"results": list(resultats), "result_count": len(resultats)}


def _image_openverse(fichier: str = "https://cdn.exemple.org/photo.jpg") -> dict[str, Any]:
    return {
        "title": "Marché de Rabat",
        "foreign_landing_url": "https://exemple.org/page-image",
        "url": fichier,
        "thumbnail": "https://cdn.exemple.org/vignette.jpg",
        "width": 1024,
        "height": 768,
        "source": "flickr",
        "provider": "flickr",
        "license": "by",
        "license_version": "4.0",
    }


def _document_html(*liens: tuple[str, str]) -> str:
    """Page de résultats DuckDuckGo **réelle** : chaque lien dans ``div.result``.

    Le moteur enveloppe ses liens (``uddg``) : c'est l'adresse visée qui compte.
    """
    blocs = "".join(
        f'<div class="result results_links">'
        f'<a rel="nofollow" class="result__a" href="{lien}">{titre}</a>'
        f"</div>"
        for lien, titre in liens
    )
    return f"<html><body>{blocs}</body></html>"


def _enveloppe(url: str) -> str:
    """Lien tel que DuckDuckGo l'écrit (redirection ``uddg``)."""
    return f"//duckduckgo.com/l/?uddg={url.replace(':', '%3A').replace('/', '%2F')}&amp;rut=a"


PAGE_DOCUMENTS = _document_html(
    (_enveloppe("https://exemple.org/rapport-2026.pdf"), "Rapport annuel 2026"),
    # Même document servi par une seconde URL d'enveloppe : il ne compte qu'une fois.
    (_enveloppe("https://exemple.org/rapport-2026.pdf"), "Rapport annuel 2026 (miroir)"),
    (_enveloppe("https://exemple.org/budget.xlsx"), "Budget pr&eacute;visionnel"),
    # Une page ordinaire n'est jamais présentée comme un document.
    ("//exemple.org/page.html", "Page ordinaire"),
    # Classe composée : l'ancre reste un résultat.
    ("//exemple.org/guide.docx", "Guide"),
)


def _route_complete(request: httpx.Request) -> httpx.Response:
    """Transport factice : page ``vqd``, verticales JSON, page « documents »."""
    chemin = request.url.path
    if chemin.endswith("/i.js"):
        return httpx.Response(200, json=_images(9))
    if chemin.endswith("/v.js"):
        return httpx.Response(200, json=_videos(9))
    if request.url.host == HOTE_DOCUMENTS:
        return httpx.Response(200, text=PAGE_DOCUMENTS)
    if request.url.host == HOTE_OPENVERSE:
        return httpx.Response(200, json=_openverse(_image_openverse()))
    assert request.url.params.get("q"), "la page de vqd doit porter la requête"
    return httpx.Response(200, text=PAGE_VQD)


def _transport(*, openverse: Any = None) -> httpx.MockTransport:
    """Transport où DuckDuckGo est **injoignable**, comme depuis l'hébergeur.

    Le repli images répond ce que le test lui donne : c'est lui qu'on éprouve.
    """

    def _route(request: httpx.Request) -> httpx.Response:
        if request.url.host in {HOTE_DUCKDUCKGO, HOTE_DOCUMENTS}:
            raise httpx.ConnectTimeout("timed out", request=request)
        if request.url.host == HOTE_OPENVERSE and openverse is not None:
            return httpx.Response(200, json=openverse)
        raise httpx.ConnectTimeout("timed out", request=request)

    return httpx.MockTransport(_route)


class _FauxDDGS:
    """Remplacement de ``ddgs.DDGS`` : le comportement de chaque moteur.

    ``par_moteur`` associe un nom de backend à une liste de résultats ou à une
    exception — c'est la surface que ``fournisseurs_ddgs`` consomme réellement.
    """

    def __init__(self, par_moteur: dict[str, Any], **_kwargs: Any) -> None:
        self._par_moteur = par_moteur

    def _appel(self, requete: str, kwargs: dict[str, Any]) -> Any:
        resultat = self._par_moteur[kwargs["backend"]]
        if isinstance(resultat, Exception):
            raise resultat
        return resultat

    def images(self, requete: str, **kwargs: Any) -> Any:
        return self._appel(requete, kwargs)

    def videos(self, requete: str, **kwargs: Any) -> Any:
        return self._appel(requete, kwargs)

    def text(self, requete: str, **kwargs: Any) -> Any:
        return self._appel(requete, kwargs)


def _moteur_ddgs(par_moteur: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    """Branche le faux client ddgs (DuckDuckGo reste injoignable dans le transport)."""
    monkeypatch.setattr(
        fournisseurs_ddgs, "_DDGS_REEL", lambda **kwargs: _FauxDDGS(par_moteur, **kwargs)
    )


def _tout_injoignable(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectTimeout("timed out", request=request)


def _tool(
    nature: str,
    *,
    client: httpx.Client,
    policy: PermissionPolicy = POLICY_WEB,
    settings: Settings | None = None,
) -> Any:
    return build_recherche_media_tool(
        nature=nature,  # type: ignore[arg-type]
        identite=IDENTITE,
        policy=policy,
        settings=settings or _settings(),
        client=client,
    )


@pytest.fixture(autouse=True)
def _sans_attente(monkeypatch: pytest.MonkeyPatch) -> None:
    """Aucune attente réelle entre deux tentatives (transport factice).

    Le garde anti-réseau ddgs, lui, est global : ``conftest.sans_ddgs_reel``.
    """
    monkeypatch.setattr("app.tools.fournisseurs_recherche.attendre", lambda _secondes: None)


# --- Permission et schémas ---------------------------------------------------


def test_permission_absente_refusee() -> None:
    tool = _tool("image", client=httpx.Client(transport=_transport()), policy=POLICY_VIDE)
    sortie = tool.invoke({"requete": "formation"})
    assert sortie["ok"] is False
    assert sortie["error"]["code"] == "permission_denied"


@pytest.mark.parametrize(
    "schema",
    [RechercheImageInput, RechercheVideoInput, RechercheDocumentInput],
)
def test_la_requete_est_bornee(schema: type) -> None:
    """Une requête trop courte ou trop longue est refusée (jamais tronquée en silence)."""
    import pydantic

    with pytest.raises(pydantic.ValidationError):
        schema(requete="x")
    with pytest.raises(pydantic.ValidationError):
        schema(requete="a" * 400)
    with pytest.raises(pydantic.ValidationError):
        schema(requete="budget", max_resultats=99)


def test_les_extensions_par_defaut_couvrent_les_formats_bureautiques() -> None:
    assert {"pdf", "docx", "xlsx", "pptx"} <= set(EXTENSIONS_DOCUMENT)


# --- DuckDuckGo (fournisseur principal) --------------------------------------


def test_recherche_image_rend_le_fichier_en_plus_de_la_page() -> None:
    """L'image doit être **affichable** : le fichier, pas seulement sa page."""
    tool = _tool("image", client=httpx.Client(transport=httpx.MockTransport(_route_complete)))
    sortie = tool.invoke({"requete": "riziculture madagascar", "max_resultats": 3})
    assert sortie["ok"] is True
    assert sortie["nb_resultats"] == 3
    assert sortie["sources_utilisees"] == ["DuckDuckGo"]
    premier = sortie["resultats"][0]
    assert premier["titre"] == "Image 0"
    assert premier["url"] == "https://exemple.org/page-0", "la page situe l'image"
    assert premier["image"] == "https://exemple.org/photo-0.jpg", "le fichier l'affiche"
    assert premier["vignette"].endswith("thumb-0.jpg")
    assert (premier["largeur"], premier["hauteur"]) == (800, 600)


def test_recherche_image_sans_thumbnail_garde_une_image_affichable() -> None:
    """Sans vignette, c'est l'image qui s'affiche — jamais une carte vide."""

    def _route(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/i.js"):
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "title": "Sans vignette",
                            "url": "https://exemple.org/p",
                            "image": "https://exemple.org/p.jpg",
                        }
                    ]
                },
            )
        return httpx.Response(200, text=PAGE_VQD)

    tool = _tool("image", client=httpx.Client(transport=httpx.MockTransport(_route)))
    premier = tool.invoke({"requete": "formation"})["resultats"][0]
    assert premier["image"] == "https://exemple.org/p.jpg"
    assert premier["vignette"] == "https://exemple.org/p.jpg"


def test_recherche_video_rend_la_page_le_fichier_la_duree_et_l_auteur() -> None:
    tool = _tool("video", client=httpx.Client(transport=httpx.MockTransport(_route_complete)))
    sortie = tool.invoke({"requete": "gestes de premiers secours"})
    assert sortie["nb_resultats"] == 5, "le plafond de configuration serre la demande"
    premier = sortie["resultats"][0]
    assert premier["url"] == "https://exemple.org/regarder-0", "la page de lecture"
    assert premier["media"] == "https://exemple.org/video-0.mp4", "le fichier jouable"
    assert premier["vignette"] == "https://exemple.org/v-0.jpg"
    assert premier["duree"] == "12:34"
    assert premier["auteur"] == "Chaine"


def test_video_sans_fichier_direct_n_annonce_aucun_media() -> None:
    """Une page d'hébergeur n'est pas un média : le lecteur ne doit pas s'y casser."""

    def _route(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/v.js"):
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "title": "Page seulement",
                            "url": "https://exemple.org/regarder",
                            "content": "https://exemple.org/regarder",
                        }
                    ]
                },
            )
        return httpx.Response(200, text=PAGE_VQD)

    tool = _tool("video", client=httpx.Client(transport=httpx.MockTransport(_route)))
    premier = tool.invoke({"requete": "formation"})["resultats"][0]
    assert premier["url"] == "https://exemple.org/regarder"
    assert premier["media"] == "", "aucun média n'est inventé depuis une page"
    assert premier["vignette"] == "", "sans vignette : rien plutôt qu'une fausse image"


def test_recherche_document_ecarte_ce_qui_n_est_pas_un_fichier() -> None:
    """Un lien qui n'est pas un fichier n'est jamais présenté comme un document."""
    tool = _tool("document", client=httpx.Client(transport=httpx.MockTransport(_route_complete)))
    sortie = tool.invoke({"requete": "budget formation madagascar"})
    assert sortie["ok"] is True
    urls = [item["url"] for item in sortie["resultats"]]
    assert urls == [
        "https://exemple.org/rapport-2026.pdf",
        "https://exemple.org/budget.xlsx",
        "https://exemple.org/guide.docx",
    ], "la page ordinaire est écartée, l'ordre du moteur est gardé"
    assert [item["type_fichier"] for item in sortie["resultats"]] == ["pdf", "xlsx", "docx"]
    assert sortie["resultats"][1]["titre"] == "Budget prévisionnel"


def test_les_liens_enveloppes_par_le_moteur_sont_rendus_reels() -> None:
    """Un résultat ne doit jamais citer l'adresse du moteur : c'est le document qui compte."""
    tool = _tool("document", client=httpx.Client(transport=httpx.MockTransport(_route_complete)))
    sortie = tool.invoke({"requete": "budget formation"})
    assert [item["domaine"] for item in sortie["resultats"]] == ["exemple.org"] * 3


def test_recherche_document_borne_les_extensions_demandees() -> None:
    """``extensions`` restreint la requête : la demande est explicite."""
    vues: list[str] = []

    def _route(request: httpx.Request) -> httpx.Response:
        if request.url.host == HOTE_DOCUMENTS:
            # La page de résultats est interrogée en POST (formulaire) : la
            # requête est dans le corps, pas dans l'adresse.
            corps = parse_qs(request.content.decode())
            vues.append(corps.get("q", [""])[0])
            return httpx.Response(200, text=PAGE_DOCUMENTS)
        return httpx.Response(200, text=PAGE_VQD)

    tool = _tool("document", client=httpx.Client(transport=httpx.MockTransport(_route)))
    sortie = tool.invoke({"requete": "budget", "extensions": ["pdf"]})
    assert sortie["ok"] is True
    assert "filetype:pdf" in vues[0]
    assert "filetype:xlsx" not in vues[0]
    assert [item["type_fichier"] for item in sortie["resultats"]] == ["pdf"]


def test_une_rafale_accusee_puis_servie_aboutit() -> None:
    """Le « 202 » de DuckDuckGo n'est pas un refus définitif : la recherche aboutit."""
    appels = {"page": 0, "verticale": 0}

    def _route(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/i.js"):
            appels["verticale"] += 1
            return httpx.Response(200, json=_images(3))
        appels["page"] += 1
        if appels["page"] == 1:
            return httpx.Response(202, text="")
        return httpx.Response(200, text=PAGE_VQD)

    tool = _tool("image", client=httpx.Client(transport=httpx.MockTransport(_route)))
    sortie = tool.invoke({"requete": "formation"})
    assert sortie["ok"] is True
    assert appels["page"] == 2, "la première tentative accusée réception doit être refaite"
    assert sortie["nb_resultats"] == 3


# --- Repli : DuckDuckGo injoignable (incident de production) -----------------


def test_images_repliees_sur_openverse_quand_duckduckgo_est_injoignable() -> None:
    """Le ``ConnectTimeout`` de production ne doit plus tuer la recherche d'images."""
    tool = _tool(
        "image",
        client=httpx.Client(transport=_transport(openverse=_openverse(_image_openverse()))),
    )
    sortie = tool.invoke({"requete": "marche rabat"})
    assert sortie["ok"] is True
    assert sortie["sources_utilisees"] == ["Openverse"]
    premier = sortie["resultats"][0]
    assert premier["image"] == "https://cdn.exemple.org/photo.jpg"
    assert premier["url"] == "https://exemple.org/page-image", "la page d'origine situe l'image"
    assert premier["licence"] == "by 4.0", "une image reprise doit dire sa licence"
    assert "DuckDuckGo injoignable" in sortie["avertissements"][0]
    assert "ConnectTimeout" in sortie["avertissements"][0]


def test_images_repliees_sur_ddgs_bing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bing via ddgs rend des images web actuelles — avant le recours Openverse."""
    _moteur_ddgs(
        {
            "bing": [
                {
                    "title": "Atelier de formation",
                    "url": "https://exemple.org/page-image",
                    "image": "https://exemple.org/photo.jpg",
                    "thumbnail": "https://exemple.org/vignette.jpg",
                    "width": 1024,
                    "height": 768,
                    "source": "exemple.org",
                }
            ],
            "duckduckgo": RuntimeError("jamais appelé après Bing"),
        },
        monkeypatch,
    )
    tool = _tool("image", client=httpx.Client(transport=_transport()))
    sortie = tool.invoke({"requete": "atelier formation"})
    assert sortie["ok"] is True
    assert sortie["sources_utilisees"] == ["ddgs"]
    premier = sortie["resultats"][0]
    assert premier["image"] == "https://exemple.org/photo.jpg"
    assert premier["vignette"].endswith("vignette.jpg")
    assert premier["url"] == "https://exemple.org/page-image"
    assert (premier["largeur"], premier["hauteur"]) == (1024, 768)
    assert "DuckDuckGo injoignable" in sortie["avertissements"][0]


def test_videos_repliees_sur_ddgs_annoncent_le_media_direct(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Une page d'hébergeur reste une page ; un fichier direct devient jouable."""
    _moteur_ddgs(
        {
            "duckduckgo": [
                {
                    "title": "Gestes qui sauvent",
                    "content": "https://exemple.org/seance.mp4",
                    "duration": "6:27",
                    "uploader": "Croix-Rouge",
                    "published": "2026-01-02",
                    "image": "https://exemple.org/vignette.jpg",
                },
                {
                    "title": "Page seulement",
                    "content": "https://www.youtube.com/watch?v=abc123",
                    "image": "",
                },
            ]
        },
        monkeypatch,
    )
    tool = _tool("video", client=httpx.Client(transport=_transport()))
    sortie = tool.invoke({"requete": "premiers secours"})
    assert sortie["ok"] is True
    assert sortie["sources_utilisees"] == ["ddgs"]
    premier, second = sortie["resultats"]
    assert premier["media"] == "https://exemple.org/seance.mp4"
    assert premier["duree"] == "6:27"
    assert premier["auteur"] == "Croix-Rouge"
    assert second["media"] == "", "une page de lecture n'est pas un fichier"
    assert second["vignette"] == ""


def test_documents_replies_sur_ddgs_filtrent_par_extension(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Le premier moteur muet n'empêche pas le suivant de répondre."""
    _moteur_ddgs(
        {
            "yahoo": RuntimeError("RatelimitException"),
            "google": [
                {"title": "Rapport annuel 2026", "href": "https://exemple.org/rapport.pdf"},
                {"title": "Page ordinaire", "href": "https://exemple.org/page.html"},
                {"title": "Budget", "href": "https://exemple.org/budget.xlsx"},
            ],
            "brave": RuntimeError("jamais appelé"),
        },
        monkeypatch,
    )
    tool = _tool("document", client=httpx.Client(transport=_transport()))
    sortie = tool.invoke({"requete": "budget formation", "extensions": ["pdf"]})
    assert sortie["ok"] is True
    assert sortie["sources_utilisees"] == ["ddgs"]
    assert [item["type_fichier"] for item in sortie["resultats"]] == ["pdf"]
    assert sortie["resultats"][0]["titre"] == "Rapport annuel 2026"


def test_ddgs_echoue_sur_tous_ses_moteurs_est_dit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Chaque moteur muet est nommé — et le tool échoue proprement avec DuckDuckGo."""
    _moteur_ddgs(
        {
            "yahoo": RuntimeError("RatelimitException"),
            "google": RuntimeError("TimeoutException"),
            "brave": RuntimeError("RequestError"),
        },
        monkeypatch,
    )
    tool = _tool("document", client=httpx.Client(transport=_transport()))
    sortie = tool.invoke({"requete": "budget"})
    assert sortie["ok"] is False
    message = sortie["error"]["message"]
    assert "aucun moteur n'a répondu" in message
    assert "yahoo (RuntimeError)" in message
    assert "google (RuntimeError)" in message
    assert sortie["error"]["details"]["fournisseurs"] == ["DuckDuckGo", "ddgs"]


def test_paquet_ddgs_absent_est_dit(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sans l'extra « web », le repli dit ce qui manque au lieu de deviner."""
    monkeypatch.setattr(fournisseurs_ddgs, "_DDGS_REEL", None)
    tool = _tool("document", client=httpx.Client(transport=_transport()))
    sortie = tool.invoke({"requete": "budget"})
    assert sortie["ok"] is False
    assert "ddgs" in sortie["error"]["message"]
    assert "extra « web »" in sortie["error"]["message"]


def test_aucun_fournisseur_ne_repond_nomme_chaque_cause() -> None:
    """Tous muets : l'erreur dit **qui** n'a pas répondu, jamais « erreur réseau »."""
    tool = _tool("image", client=httpx.Client(transport=httpx.MockTransport(_tout_injoignable)))
    sortie = tool.invoke({"requete": "formation"})
    assert sortie["ok"] is False
    message = sortie["error"]["message"]
    assert "aucun fournisseur" in message
    assert "DuckDuckGo injoignable" in message
    assert "ddgs : aucun moteur" in message
    assert "Openverse injoignable" in message
    assert sortie["error"]["details"]["fournisseurs"] == ["DuckDuckGo", "ddgs", "Openverse"]


def test_le_repli_n_est_pas_interroge_quand_duckduckgo_repond(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Un fournisseur qui répond évite l'appel de repli (pas de latence en double)."""
    hotes: list[str] = []

    def _route(request: httpx.Request) -> httpx.Response:
        hotes.append(request.url.host)
        return _route_complete(request)

    def _jamais(**_kwargs: Any) -> None:
        raise AssertionError("ddgs ne doit pas être appelé quand DuckDuckGo répond")

    monkeypatch.setattr(fournisseurs_ddgs, "_DDGS_REEL", _jamais)
    tool = _tool("image", client=httpx.Client(transport=httpx.MockTransport(_route)))
    sortie = tool.invoke({"requete": "formation"})
    assert sortie["ok"] is True
    assert HOTE_OPENVERSE not in hotes
    assert sortie["avertissements"] == []


def test_reponse_non_json_est_une_erreur_explicite() -> None:
    """Un 200 non-JSON (mur anti-bot, corps tronqué) ne sort pas en JSONDecodeError."""

    def _route(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>blocage anti-bot</html>")

    tool = _tool("image", client=httpx.Client(transport=httpx.MockTransport(_route)))
    sortie = tool.invoke({"requete": "formation"})
    assert sortie["ok"] is False
    assert "illisible" in sortie["error"]["message"]


def test_jeton_absent_est_une_erreur_explicite() -> None:
    """Sans jeton, aucun résultat n'est inventé : l'erreur est dite."""

    def _route(request: httpx.Request) -> httpx.Response:
        if request.url.host == HOTE_OPENVERSE:
            raise httpx.ConnectTimeout("timed out", request=request)
        return httpx.Response(200, text="<html>rien</html>")

    tool = _tool("image", client=httpx.Client(transport=httpx.MockTransport(_route)))
    sortie = tool.invoke({"requete": "formation"})
    assert sortie["ok"] is False
    assert "vqd" in sortie["error"]["message"]


def test_refus_du_fournisseur_est_remonte() -> None:
    """Un refus (503) est retenté une fois, puis dit — le repli est nommé aussi."""
    appels = {"page": 0}

    def _route(request: httpx.Request) -> httpx.Response:
        if request.url.host in {HOTE_DUCKDUCKGO, HOTE_DOCUMENTS}:
            appels["page"] += 1
        return httpx.Response(503, text="indisponible")

    tool = _tool("image", client=httpx.Client(transport=httpx.MockTransport(_route)))
    sortie = tool.invoke({"requete": "formation"})
    assert sortie["ok"] is False
    assert "503" in sortie["error"]["message"]
    assert appels["page"] == 2, "un refus temporaire doit être retenté, puis dit"
