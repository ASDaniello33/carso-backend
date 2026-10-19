"""Moteurs publics — lecture des pages, bascule d'un moteur à l'autre, vidéos.

Aucun appel réseau : la session ``curl_cffi`` est remplacée par un substitut qui
rend la page attendue par URL. Ce que ces tests verrouillent :

- un moteur qui **bloque** (captcha, 403, panne réseau) ne fait pas échouer la
  recherche — le suivant répond, et les résultats du précédent ne sont pas perdus ;
- « bloqué » et « rien trouvé » ne se confondent jamais : le premier est une
  erreur nommée quand *tous* échouent, le second est une liste vide ;
- la requête part telle quelle (c'est elle qui porte ``filetype:pdf``) ;
- les vidéos se lisent dans l'état initial de YouTube, jamais devinées.
"""

from __future__ import annotations

import base64
import json
from typing import Any

import pytest

from app.core.errors import ValidationError
from app.tools import moteurs_web

URL_BING = "https://www.bing.com/search"
URL_YAHOO = "https://search.yahoo.com/search"
URL_BRAVE = "https://search.brave.com/search"
URL_MOJEEK = "https://www.mojeek.com/search"
URL_DUCKDUCKGO = "https://html.duckduckgo.com/html/"
URL_YOUTUBE = "https://www.youtube.com/results"


class _Reponse:
    """Réponse minimale : ce que ``moteurs_web`` lit réellement."""

    def __init__(self, texte: str = "", statut: int = 200) -> None:
        self.text = texte
        self.status_code = statut


class _Session:
    """Session factice : une réponse par URL, et la trace des appels."""

    def __init__(self, pages: dict[str, Any]) -> None:
        self._pages = pages
        self.appels: list[str] = []
        self.requetes: list[dict[str, Any]] = []
        self.headers: dict[str, str] = {}
        self.fermee = False

    def request(self, _methode: str, url: str, **options: Any) -> Any:
        self.appels.append(url)
        self.requetes.append(options)
        reponse = self._pages.get(url)
        if reponse is None:
            raise ConnectionError(f"hôte injoignable : {url}")
        if isinstance(reponse, Exception):
            raise reponse
        return reponse

    def close(self) -> None:
        self.fermee = True


def _brancher(monkeypatch: pytest.MonkeyPatch, pages: dict[str, Any]) -> _Session:
    """Remplace la session : aucune empreinte de navigateur n'est créée."""
    session = _Session(pages)
    monkeypatch.setattr(moteurs_web, "_session", lambda: session)
    return session


def _page_bing(*lignes: tuple[str, str, str]) -> str:
    blocs = "".join(
        f'<li class="b_algo"><h2><a href="{url}">{titre}</a></h2><p>{resume}</p></li>'
        for titre, url, resume in lignes
    )
    return f"<html><body><ol>{blocs}</ol></body></html>"


def _page_yahoo(*lignes: tuple[str, str, str]) -> str:
    blocs = "".join(
        f'<div class="algo"><div class="compTitle"><h3>'
        f'<a href="{url}">{titre}</a></h3></div>'
        f'<div class="compText">{resume}</div></div>'
        for titre, url, resume in lignes
    )
    return f"<html><body>{blocs}</body></html>"


# --- Moteurs web -------------------------------------------------------------


def test_bing_rend_l_adresse_visee_et_non_l_enveloppe(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bing enveloppe ses liens (``/ck/a?…&u=a1<base64>``) : c'est l'adresse visée qui compte.

    Mesuré le 18/10/2026 : sans décodage, l'agent recevait l'enveloppe du moteur
    et citait `bing.com` au lieu de la source.
    """
    cible = "https://exemple.org/rapport.pdf"
    encodee = base64.urlsafe_b64encode(cible.encode()).decode().rstrip("=")
    enveloppe = f"https://www.bing.com/ck/a?!&&p=abc123&u=a1{encodee}&ntb=1"
    page = _page_bing(("Rapport 2026", enveloppe, "PDF officiel"))
    session = _brancher(monkeypatch, {URL_BING: _Reponse(page)})
    resultats = moteurs_web.pages("rapport annuel filetype:pdf", 5)

    assert resultats == [
        {"titre": "Rapport 2026", "url": cible, "resume": "PDF officiel"}
    ]
    # La requête part telle quelle : c'est elle qui porte l'opérateur ``filetype:``.
    assert session.requetes[0]["params"]["q"] == "rapport annuel filetype:pdf"
    assert session.fermee, "la session doit être fermée après la recherche"


def test_yahoo_decode_l_adresse_percent_encodee(monkeypatch: pytest.MonkeyPatch) -> None:
    """Yahoo rend ``/RU=https%3a%2f%2f…`` : sans décodage, l'URL n'est pas ouvrable."""
    page = _page_yahoo(
        (
            "Guide",
            "https://r.search.yahoo.com/_ylt=x/RV=2/RE=1/RU=https%3a%2f%2fexemple.org%2fa/RK=2/RS=abc-",
            "Extrait",
        )
    )
    _brancher(monkeypatch, {URL_BING: _Reponse("", statut=403), URL_YAHOO: _Reponse(page)})

    assert [item["url"] for item in moteurs_web.pages("guide", 5)] == ["https://exemple.org/a"]


def test_un_moteur_qui_bloque_laisse_la_place_au_suivant(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bing renvoie un captcha : Yahoo répond, et la recherche aboutit."""
    session = _brancher(
        monkeypatch,
        {
            URL_BING: _Reponse("<html><body>captcha</body></html>"),
            URL_YAHOO: _Reponse(_page_yahoo(("Guide", "https://exemple.org/guide", "Extrait"))),
        },
    )
    resultats = moteurs_web.pages("guide", 5)

    assert [item["url"] for item in resultats] == ["https://exemple.org/guide"]
    assert session.appels == [URL_BING, URL_YAHOO]


def test_une_panne_reseau_n_arrete_pas_la_recherche(monkeypatch: pytest.MonkeyPatch) -> None:
    """Une exception de transport est un constat, pas une fin de recherche."""
    session = _brancher(
        monkeypatch,
        {
            URL_BING: ConnectionError("coupure"),
            URL_YAHOO: _Reponse(_page_yahoo(("Guide", "https://exemple.org/guide", ""))),
        },
    )
    resultats = moteurs_web.pages("guide", 5)

    assert len(resultats) == 1
    assert session.appels[:2] == [URL_BING, URL_YAHOO]


def test_aucun_moteur_ne_repond_nomme_chaque_cause(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tous muets : erreur métier nommant **chaque** moteur, jamais une liste vide."""
    _brancher(monkeypatch, {})

    with pytest.raises(ValidationError) as capture:
        moteurs_web.pages("guide", 5)

    message = str(capture.value)
    assert "Aucun moteur public n'a répondu" in message
    for moteur in moteurs_web.MOTEURS:
        assert moteur in message


def test_moteurs_muets_mais_vides_n_est_pas_une_erreur(monkeypatch: pytest.MonkeyPatch) -> None:
    """Les moteurs ont répondu « rien » : liste vide, pas un échec."""
    _brancher(
        monkeypatch,
        {
            URL_BING: _Reponse("<html><body>aucun résultat</body></html>"),
            URL_YAHOO: _Reponse("<html><body></body></html>"),
            URL_BRAVE: _Reponse("<html><body></body></html>"),
            URL_MOJEEK: _Reponse("<html><body></body></html>"),
            URL_DUCKDUCKGO: _Reponse("<html><body></body></html>"),
        },
    )
    assert moteurs_web.pages("xyzzy introuvable", 5) == []


def test_un_refus_http_est_conserve_comme_cause(monkeypatch: pytest.MonkeyPatch) -> None:
    """Un 403 nommé vaut mieux qu'un « aucun résultat » trompeur."""
    _brancher(
        monkeypatch,
        {
            URL_BING: _Reponse("", statut=403),
            URL_YAHOO: _Reponse("", statut=429),
            URL_BRAVE: _Reponse("", statut=503),
            URL_MOJEEK: _Reponse("", statut=500),
            URL_DUCKDUCKGO: _Reponse("", statut=403),
        },
    )
    with pytest.raises(ValidationError) as capture:
        moteurs_web.pages("guide", 5)
    assert "HTTP 403" in str(capture.value)


def test_les_liens_sans_adresse_sont_ecartes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Un résultat sans URL http(s) n'est pas un résultat."""
    page = _page_bing(("Sans lien", "/relatif", ""), ("Bon", "https://a.test/1", ""))
    _brancher(monkeypatch, {URL_BING: _Reponse(page)})
    assert [item["url"] for item in moteurs_web.pages("x", 5)] == ["https://a.test/1"]


def test_la_borne_est_respectee(monkeypatch: pytest.MonkeyPatch) -> None:
    lignes = tuple((f"T{i}", f"https://a.test/{i}", "") for i in range(8))
    _brancher(monkeypatch, {URL_BING: _Reponse(_page_bing(*lignes))})
    assert len(moteurs_web.pages("x", 3)) == 3


# --- Vidéos ------------------------------------------------------------------


def _page_youtube(*videos: tuple[str, str, str, str]) -> str:
    entrees = [
        {
            "videoRenderer": {
                "videoId": identifiant,
                "title": {"runs": [{"text": titre}]},
                "lengthText": {"simpleText": duree},
                "ownerText": {"runs": [{"text": chaine}]},
                "publishedTimeText": {"simpleText": "il y a 3 jours"},
                "thumbnail": {"thumbnails": [{"url": "https://i.ytimg.com/1.jpg"}]},
            }
        }
        for identifiant, titre, duree, chaine in videos
    ]
    donnees = {"contents": {"twoColumnSearchResultsRenderer": {"x": entrees}}}
    return (
        "<html><body><script>var ytInitialData = "
        + json.dumps(donnees)
        + ";</script></body></html>"
    )


def test_videos_lit_l_etat_initial_de_youtube(monkeypatch: pytest.MonkeyPatch) -> None:
    _brancher(
        monkeypatch,
        {
            URL_YOUTUBE: _Reponse(
                _page_youtube(("abc123DEF45", "Tuto FastAPI", "12:34", "Chaîne CARSO"))
            )
        },
    )
    resultats = moteurs_web.videos("tuto fastapi", 5)

    assert resultats == [
        {
            "titre": "Tuto FastAPI",
            "url": "https://www.youtube.com/watch?v=abc123DEF45",
            "vignette": "https://i.ytimg.com/1.jpg",
            "duree": "12:34",
            "auteur": "Chaîne CARSO",
            "date": "il y a 3 jours",
        }
    ]


def test_videos_page_inattendue_est_une_erreur(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sans état initial, l'échec est dit : aucun résultat n'est inventé."""
    _brancher(monkeypatch, {URL_YOUTUBE: _Reponse("<html><body>consent</body></html>")})
    with pytest.raises(ValidationError) as capture:
        moteurs_web.videos("tuto", 5)
    assert "YouTube" in str(capture.value)


def test_videos_refus_http_est_une_erreur_metier(monkeypatch: pytest.MonkeyPatch) -> None:
    """Un 403 ne sort pas en exception privée : il deviendrait un run interrompu."""
    _brancher(monkeypatch, {URL_YOUTUBE: _Reponse("", statut=403)})
    with pytest.raises(ValidationError) as capture:
        moteurs_web.videos("tuto", 5)
    assert "403" in str(capture.value)


def test_videos_panne_reseau_est_une_erreur_metier(monkeypatch: pytest.MonkeyPatch) -> None:
    _brancher(monkeypatch, {URL_YOUTUBE: ConnectionError("coupure")})
    with pytest.raises(ValidationError) as capture:
        moteurs_web.videos("tuto", 5)
    assert "ConnectionError" in str(capture.value)


def test_videos_bornees(monkeypatch: pytest.MonkeyPatch) -> None:
    _brancher(
        monkeypatch,
        {
            URL_YOUTUBE: _Reponse(
                _page_youtube(
                    ("aaaaaaaaaaa", "Un", "1:00", "A"),
                    ("bbbbbbbbbbb", "Deux", "2:00", "B"),
                )
            )
        },
    )
    assert len(moteurs_web.videos("x", 1)) == 1
