"""Tools ``rechercher_image`` / ``rechercher_video`` / ``rechercher_document``.

Aucun appel réseau : le transport httpx est remplacé par un ``httpx.MockTransport``
qui rejoue la surface réellement consommée (page de recherche pour le jeton
``vqd``, verticales JSON, endpoint léger pour les documents).
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from app.core.config import Settings
from app.tools import recherche_media
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


PAGE_DOCUMENTS = "".join(
    (
        "<html><body>",
        # Le moteur enveloppe ses liens (``uddg``) : c'est l'adresse visée qui compte.
        '<a rel="nofollow" class="result__a" '
        'href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexemple.org%2Frapport-2026.pdf&amp;rut=a">'
        "Rapport annuel 2026</a>",
        # Même document servi par une seconde URL d'enveloppe : il ne compte qu'une fois.
        '<a class="result__a" '
        'href="https://duckduckgo.com/l/?uddg=https%3A%2F%2Fexemple.org%2Frapport-2026.pdf">'
        "Rapport annuel 2026 (miroir)</a>",
        '<a class="result__a" '
        'href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexemple.org%2Fbudget.xlsx">'
        "Budget pr&eacute;visionnel</a>",
        # Une page ordinaire n'est jamais présentée comme un document.
        '<a class="result__a" href="//exemple.org/page.html">Page ordinaire</a>',
        # Classe composée : l'ancre reste un résultat.
        '<a class="result__a js-result" href="//exemple.org/guide.docx">Guide</a>',
        "</body></html>",
    )
)


def _transport(
    *, verticale: str = "images", documents: str = PAGE_DOCUMENTS
) -> httpx.MockTransport:
    """Transport factice : page ``vqd``, une verticale JSON, page « documents »."""

    def _route(request: httpx.Request) -> httpx.Response:
        chemin = request.url.path
        if chemin.endswith("/i.js"):
            return httpx.Response(200, json=_images(9))
        if chemin.endswith("/v.js"):
            return httpx.Response(200, json=_videos(9))
        if "html." in request.url.host:
            return httpx.Response(200, text=documents)
        assert request.url.params.get("q"), "la page de vqd doit porter la requête"
        return httpx.Response(200, text=PAGE_VQD)

    return httpx.MockTransport(_route)


def _tool(nature: str, *, client: httpx.Client, policy: PermissionPolicy = POLICY_WEB) -> Any:
    return build_recherche_media_tool(
        nature=nature,  # type: ignore[arg-type]
        identite=IDENTITE,
        policy=policy,
        settings=_settings(),
        client=client,
    )


def test_permission_absente_refusee() -> None:
    tool = _tool("image", client=httpx.Client(transport=_transport()), policy=POLICY_VIDE)
    sortie = tool.invoke({"requete": "formation"})
    assert sortie["ok"] is False
    assert sortie["error"]["code"] == "permission_denied"


def test_recherche_image_rend_le_fichier_en_plus_de_la_page() -> None:
    """L'image doit être **affichable** : le fichier, pas seulement sa page."""
    tool = _tool("image", client=httpx.Client(transport=_transport()))
    sortie = tool.invoke({"requete": "riziculture madagascar", "max_resultats": 3})
    assert sortie["ok"] is True
    assert sortie["nb_resultats"] == 3
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
                        {"title": "Sans vignette", "url": "https://exemple.org/p", "image": "https://exemple.org/p.jpg"}
                    ]
                },
            )
        return httpx.Response(200, text=PAGE_VQD)

    tool = _tool("image", client=httpx.Client(transport=httpx.MockTransport(_route)))
    premier = tool.invoke({"requete": "formation"})["resultats"][0]
    assert premier["image"] == "https://exemple.org/p.jpg"
    assert premier["vignette"] == "https://exemple.org/p.jpg"


def test_recherche_video_rend_la_page_le_fichier_la_duree_et_l_auteur() -> None:
    tool = _tool("video", client=httpx.Client(transport=_transport()))
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
    tool = _tool("document", client=httpx.Client(transport=_transport()))
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
    tool = _tool("document", client=httpx.Client(transport=_transport()))
    sortie = tool.invoke({"requete": "budget formation"})
    urls = [item["url"] for item in sortie["resultats"]]
    assert urls == [
        "https://exemple.org/rapport-2026.pdf",
        "https://exemple.org/budget.xlsx",
        "https://exemple.org/guide.docx",
    ], "le même document servi deux fois ne compte qu'une fois"
    assert [item["domaine"] for item in sortie["resultats"]] == ["exemple.org"] * 3


def test_recherche_document_borne_les_extensions_demandees() -> None:
    """``extensions`` restreint la requête : la demande est explicite."""
    vues: list[str] = []

    def _route(request: httpx.Request) -> httpx.Response:
        if "html." in request.url.host:
            vues.append(request.url.params.get("q", ""))
            return httpx.Response(200, text=PAGE_DOCUMENTS)
        return httpx.Response(200, text=PAGE_VQD)

    tool = _tool("document", client=httpx.Client(transport=httpx.MockTransport(_route)))
    sortie = tool.invoke({"requete": "budget", "extensions": ["pdf"]})
    assert sortie["ok"] is True
    assert "filetype:pdf" in vues[0]
    assert "filetype:xlsx" not in vues[0]
    assert [item["type_fichier"] for item in sortie["resultats"]] == ["pdf"]


def test_reseau_injoignable_est_une_erreur_explicite(monkeypatch: pytest.MonkeyPatch) -> None:
    """Un ``ConnectTimeout`` est dit à l'agent, jamais laissé crasher le run.

    Une exception hors ``CarsoError`` traverse ``to_langchain_tool`` (qui ne
    rattrape que ``ToolException``) et interrompt tout le run AG-UI — c'est
    exactement l'incident vu en production le 18/10/2026 (tool=rechercher_image,
    « Run AG-UI interrompu : ConnectTimeout »).
    """
    appels = {"page": 0}

    def _route(request: httpx.Request) -> httpx.Response:
        appels["page"] += 1
        raise httpx.ConnectTimeout("timed out", request=request)

    monkeypatch.setattr(recherche_media, "_attendre", lambda _secondes: None)
    tool = _tool("image", client=httpx.Client(transport=httpx.MockTransport(_route)))
    sortie = tool.invoke({"requete": "formation"})
    assert sortie["ok"] is False
    assert "injoignable" in sortie["error"]["message"]
    assert "ConnectTimeout" in sortie["error"]["message"]
    assert appels["page"] == 3, "un échec réseau doit être retenté, puis dit"


def test_reponse_non_json_est_une_erreur_explicite(monkeypatch: pytest.MonkeyPatch) -> None:
    """Un 200 non-JSON (mur anti-bot, corps tronqué) ne sort pas en JSONDecodeError.

    Même contrat que pour le réseau : erreur métier dite à l'agent, jamais une
    exception brute qui interromprait le run AG-UI.
    """

    def _route(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/i.js"):
            return httpx.Response(200, text="<html>blocage anti-bot</html>")
        return httpx.Response(200, text=PAGE_VQD)

    monkeypatch.setattr(recherche_media, "_attendre", lambda _secondes: None)
    tool = _tool("image", client=httpx.Client(transport=httpx.MockTransport(_route)))
    sortie = tool.invoke({"requete": "formation"})
    assert sortie["ok"] is False
    assert "illisible" in sortie["error"]["message"]


def test_jeton_absent_est_une_erreur_explicite() -> None:
    """Sans jeton, aucun résultat n'est inventé : l'erreur est dite."""

    def _route(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>rien</html>")

    tool = _tool("image", client=httpx.Client(transport=httpx.MockTransport(_route)))
    sortie = tool.invoke({"requete": "formation"})
    assert sortie["ok"] is False
    assert "vqd" in sortie["error"]["message"]


def test_refus_du_fournisseur_est_remonte(monkeypatch: pytest.MonkeyPatch) -> None:
    appels = {"page": 0}

    def _route(request: httpx.Request) -> httpx.Response:
        appels["page"] += 1
        return httpx.Response(503, text="indisponible")

    monkeypatch.setattr(recherche_media, "_attendre", lambda _secondes: None)
    tool = _tool("image", client=httpx.Client(transport=httpx.MockTransport(_route)))
    sortie = tool.invoke({"requete": "formation"})
    assert sortie["ok"] is False
    assert "503" in sortie["error"]["message"]
    assert "tentatives" in sortie["error"]["message"]
    assert appels["page"] == 3, "un refus temporaire doit être retenté, puis dit"


def test_une_rafale_accusee_puis_servie_aboutit(monkeypatch: pytest.MonkeyPatch) -> None:
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

    monkeypatch.setattr(recherche_media, "_attendre", lambda _secondes: None)
    tool = _tool("image", client=httpx.Client(transport=httpx.MockTransport(_route)))
    sortie = tool.invoke({"requete": "formation"})
    assert sortie["ok"] is True
    assert appels["page"] == 2, "la première tentative accusée réception doit être refaite"
    assert sortie["nb_resultats"] == 3


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
