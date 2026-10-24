"""Recherche web et lecture de pages pour agents (Lot 1, D4—LangSearch).

Trois tools, tous en **lecture pure** :

- ``langsearch_web_search``—recherche web **fédérée**—les résultats de l'API
  LangSearch et ceux des **moteurs publics** (``app/tools/moteurs_web`` : Bing,
  Yahoo, Brave, Mojeek, DuckDuckGo, interrogés tour à tour avec une empreinte de
  Chrome) sont fusionnés en **une** liste, alternée puis dédupliquée par URL, où
  chaque entrée porte ses ``sources``. Un fournisseur qui refuse (clé absente,
  panne, quota, captcha) ne fait pas échouer la recherche—les avertissements
  sont rendus tels quels et l'autre répond. Si **aucun** ne répond, l'échec est
  explicite—jamais une liste vide qui ferait croire à une absence de résultats.

C'est aussi le tool des **fichiers** : une image ou un document se cherchent avec
la recherche web classique et son opérateur ``filetype:`` (``… filetype:pdf``,
``… filetype:xlsx``, ``… filetype:jpg``), sans tool dédié. Les moteurs publics
qui honorent l'opérateur rendent directement l'adresse du fichier ; l'agent dit
lequel il a obtenu, et ne présente jamais une page comme un fichier.
- ``fetch_url``—contenu lisible d'une adresse—texte extrait d'une page HTML,
  JSON reformaté, ou texte brut.
- ``read_urls``—la même lecture sur plusieurs adresses en un appel, sous un
  budget de caractères global—cinq pages entières ne doivent pas noyer le
  modèle.

Garde commune—toute adresse est vérifiée par ``url_publique_verifiee`` avant
qu'un octet ne parte, **à chaque saut de redirection**—la boucle locale, le
réseau interne et les identifiants dans l'URL sont refusés (anti-SSRF). Les
redirections sont suivies explicitement, une par une, jamais déléguées à httpx.

Les **recherches** de ``langsearch_web_search`` sont mémorisées 30 minutes en
mémoire (``app/tools/cache_ttl``) : une requête identique rejouée dans un run ne
repart ni chez LangSearch ni chez les moteurs publics. Les échecs ne sont jamais
mémorisés, et la permission est revérifiée **avant** de lire le cache — un agent
sans ``web_search`` ne récupère pas le résultat d'un autre.

Ce que ces tools ne font pas—aucun fichier n'est écrit ni déposé, aucune ligne
n'est insérée en base. Une page est lue **en flux**, bornée en octets puis en
caractères—une adresse volumineuse est dite, jamais aspirée. Le texte rendu est
un extrait présenté comme source externe—jamais la vérité métier PostgreSQL
(AGENTS.md §6).

Secret—la clé LangSearch est un ``SecretStr`` de ``Settings``, déballée
uniquement pour l'en-tête ``Authorization``. Elle n'apparaît ni dans les logs, ni
dans la sortie d'un tool, ni dans un message inter-agent (instruction/08 §6).

Le nom ``langsearch_web_search`` est conservé—il est déclaré dans cinq contrats
d'agents et libellé dans l'interface. Sa description, elle, dit exactement ce
qu'il interroge aujourd'hui.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import urljoin, urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.errors import ValidationError
from app.documents.telechargement import url_publique_verifiee
from app.tools import moteurs_web
from app.tools.budget_recherche import consommer_recherche
from app.tools.cache_ttl import CacheTTL
from app.tools.fournisseurs_recherche import (
    EN_TETES,
    TIMEOUT_HTTP,
    pages_langsearch,
    soupe,
)
from app.tools.permissions import AgentIdentity, PermissionPolicy

__all__ = [
    "CACHE_RECHERCHE_WEB",
    "FetchUrlInput",
    "ReadUrlsInput",
    "WebSearchInput",
    "attacher_outils_web",
    "build_fetch_url_tool",
    "build_read_urls_tool",
    "build_web_search_tool",
]

#: Fournisseurs de la recherche fédérée. Ce sont aussi les valeurs du champ
#: ``sources`` du schéma d'entrée et des ``sources`` rendues par résultat.
SOURCE_LANGSEARCH = "langsearch"
SOURCE_MOTEURS = "moteurs"

#: Recherches web mémorisées 30 minutes, partagées par tous les agents du
#: processus : les résultats publics ne dépendent pas de l'agent qui demande.
CACHE_RECHERCHE_WEB: CacheTTL[dict[str, Any]] = CacheTTL()

_MAX_REQUETE = 400
#: Requêtes distinctes par appel : au-delà, l'agent regroupe encore ou étale.
_MAX_REQUETES = 5
_MAX_RESULTATS = 10
#: Plafond de la liste **fusionnée** d'un appel multi-requêtes : la largeur
#: augmente, le volume rendu au modèle reste borné.
_MAX_RESULTATS_FUSION = 20
#: Borne du schéma ``read_urls`` : la configuration peut serrer en dessous
#: (``web_read_urls_max``), jamais au-delà.
_MAX_URLS = 8
_MAX_REDIRECTIONS = 3
_STATUTS_REDIRECTION = frozenset({301, 302, 303, 307, 308})
_BLOC_LECTURE = 65_536

# --- Fraîcheur ---------------------------------------------------------------
#: Vocabulaire reçu → jeton LangSearch. Les deux vocabulaires (API et moteur) et
#: leurs alias français désignent la **même** demande : elle est traduite pour
#: chaque fournisseur, jamais devinée.
_FRAICHEUR = {
    "nolimit": "noLimit",
    "all": "noLimit",
    "aucune": "noLimit",
    "tout": "noLimit",
    "oneday": "oneDay",
    "day": "oneDay",
    "d": "oneDay",
    "24h": "oneDay",
    "jour": "oneDay",
    "oneweek": "oneWeek",
    "week": "oneWeek",
    "w": "oneWeek",
    "semaine": "oneWeek",
    "onemonth": "oneMonth",
    "month": "oneMonth",
    "m": "oneMonth",
    "mois": "oneMonth",
    "oneyear": "oneYear",
    "year": "oneYear",
    "y": "oneYear",
    "an": "oneYear",
    "annee": "oneYear",
}
#: Une plage de dates est comprise par l'API LangSearch **seule** : les moteurs
#: publics ne reçoivent pas de filtre de fraîcheur, et le dire vaut mieux que
#: laisser croire qu'il a été appliqué.
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _fraicheur_canonique(valeur: str) -> str:
    """Jeton de fraîcheur canonique (``noLimit``, ``oneDay``… ou une date).

    Raises:
        ValueError: valeur hors vocabulaire — refus explicite plutôt qu'un filtre
            ignoré en silence (le schéma du tool la traduit en message lisible).
    """
    brut = (valeur or "").strip()
    if not brut:
        return "noLimit"
    if _DATE.match(brut):
        return brut
    connu = _FRAICHEUR.get(brut.lower())
    if connu is None:
        msg = (
            "fraîcheur inconnue : valeurs acceptées noLimit, oneDay, oneWeek, "
            "oneMonth, oneYear (alias day/week/month/year, jour/semaine/mois/an) "
            "ou une date AAAA-MM-JJ"
        )
        raise ValueError(msg)
    return connu


# --- Recherche web -----------------------------------------------------------


class WebSearchInput(BaseModel):
    """Entrée typée de ``langsearch_web_search`` (recherche web fédérée).

    Plusieurs mots-clés se demandent en **un** appel : ``requetes`` accepte une
    liste, exécutée requête par requête puis fusionnée. C'est le remède à la
    rafale d'appels quasi identiques—trois variantes d'une même recherche
    coûtaient trois tours de modèle et rendaient trois fois les mêmes liens.
    ``requete`` reste accepté (appel simple, historique) ; les deux champs sont
    ramenés à une liste canonique, sans doublon et sans chaîne vide.
    """

    #: Un champ non prévu est refusé : l'agent corrige, il ne devine pas.
    model_config = ConfigDict(extra="forbid")

    requete: str | None = Field(
        default=None,
        max_length=_MAX_REQUETE,
        description="Une seule requête en langage naturel (raccourci de `requetes`).",
    )
    requetes: list[str] | None = Field(
        default=None,
        max_length=_MAX_REQUETES,
        description=(
            "Plusieurs requêtes en un seul appel : chaque mot-clé est cherché, "
            "les résultats sont fusionnés et dédoublonnés. À préférer aux "
            "appels successifs pour une même question."
        ),
    )
    max_results: int | None = Field(
        default=None,
        ge=1,
        le=_MAX_RESULTATS,
        description="Nombre de résultats (serre la borne Settings, jamais l'inverse).",
    )
    freshness: str = Field(
        default="noLimit",
        description=(
            "Fraîcheur demandée : noLimit | oneDay | oneWeek | oneMonth | oneYear "
            "(alias day/week/month/year, jour/semaine/mois/an) ou une date "
            "AAAA-MM-JJ."
        ),
    )
    include_domains: list[str] | None = Field(
        default=None,
        description="Restreindre aux domaines (ex. service-public.ma). LangSearch seul.",
    )
    exclude_domains: list[str] | None = Field(
        default=None,
        description="Exclure des domaines. LangSearch seul.",
    )
    niveau: Literal["ordinaire", "verification", "approfondie"] | None = Field(
        default=None,
        description=(
            "Profondeur annoncée pour ce tour : ordinaire (8 appels maximum), "
            "verification (15) ou approfondie (25). À envoyer au premier appel ; "
            "le plafond du tour s'y resserre."
        ),
    )
    texte_complet: bool = Field(
        default=False,
        description="Si vrai : contents.text (contexte page via l'API, pas un scraping).",
    )
    max_caracteres: int = Field(
        default=3000,
        ge=200,
        le=5000,
        description="Plafond par résultat en mode texte (défaut API = 5000).",
    )
    sources: list[Literal["langsearch", "moteurs"]] | None = Field(
        default=None,
        description=(
            "Fournisseurs interrogés : langsearch (API) et moteurs (Bing, Yahoo, "
            "Brave, Mojeek, DuckDuckGo, essayés tour à tour). Par défaut les "
            "deux : celui qui refuse n'empêche pas l'autre de répondre. Pour "
            "lire une page trouvée, utilise fetch_url ou read_urls."
        ),
    )

    @field_validator("freshness")
    @classmethod
    def _fraicheur_connue(cls, valeur: str) -> str:
        """Ramène la fraîcheur au vocabulaire canonique (refus explicite sinon)."""
        return _fraicheur_canonique(valeur)

    @model_validator(mode="after")
    def _requetes_canoniques(self) -> WebSearchInput:
        """Liste unique des requêtes : ordre conservé, doublons et vides écartés.

        Raises:
            ValueError: aucune requête exploitable, ou une requête trop longue.
        """
        brutes: list[str] = []
        if isinstance(self.requete, str):
            brutes.append(self.requete)
        brutes.extend(self.requetes or ())

        vues: set[str] = set()
        propres: list[str] = []
        for brute in brutes:
            texte = " ".join(str(brute).split())
            if not texte:
                continue
            if len(texte) > _MAX_REQUETE:
                msg = (
                    f"requête trop longue ({len(texte)} caractères, maximum "
                    f"{_MAX_REQUETE})"
                )
                raise ValueError(msg)
            cle = texte.casefold()
            if cle in vues:
                continue
            vues.add(cle)
            propres.append(texte)

        if not propres:
            msg = "au moins une requête non vide est nécessaire (requete ou requetes)"
            raise ValueError(msg)
        self.requetes = propres
        self.requete = " | ".join(propres)
        return self

def _cle_url(url: str) -> str:
    """Clé de dédoublonnage : schéma et hôte insensibles à la casse, fragment ignoré."""
    parties = urlsplit((url or "").strip())
    chemin = parties.path.rstrip("/")
    return f"{parties.scheme.lower()}://{parties.netloc.lower()}{chemin}?{parties.query}"


def _fusionner(
    lots: list[tuple[str, list[dict[str, Any]]]], nb: int
) -> list[dict[str, Any]]:
    """Fusionne les listes des fournisseurs en alternant, puis dédoublonne par URL.

    L'alternance est ce qui rend la fusion utile : le meilleur résultat de chaque
    fournisseur apparaît tôt, au lieu qu'un moteur recouvre entièrement l'autre.
    Deux entrées visant la même adresse sont **une** entrée : la première garde sa
    place, et un résumé manquant est complété par celui de l'autre fournisseur —
    ``sources`` liste alors les deux, la provenance reste exacte.
    """
    par_url: dict[str, dict[str, Any]] = {}
    ordre: list[str] = []
    curseurs = [0] * len(lots)
    while len(ordre) < nb:
        nouveau = False
        for index, (_source, resultats) in enumerate(lots):
            # Chaque tour, un fournisseur apporte son meilleur résultat **pas
            # encore vu** ; une adresse déjà connue ne consomme pas son tour.
            while curseurs[index] < len(resultats):
                item = dict(resultats[curseurs[index]])
                curseurs[index] += 1
                cle = _cle_url(item.get("url", ""))
                connu = par_url.get(cle)
                if connu is not None:
                    _completer(connu, item)
                    continue
                par_url[cle] = item
                ordre.append(cle)
                nouveau = True
                break
        if not nouveau:
            break
    return [par_url[cle] for cle in ordre[:nb]]


def _completer(connu: dict[str, Any], item: dict[str, Any]) -> None:
    """Complète une entrée déjà retenue par ce que dit l'autre fournisseur.

    Le premier résumé non vide gagne (l'ordre des sources est un ordre de
    préférence), mais une date ou une provenance manquante est comblée :
    ``sources`` finit par lister **toutes** les sources qui ont vu l'adresse.
    """
    if not connu["resume"] and item.get("resume"):
        connu["resume"] = item["resume"]
    if not connu.get("date_publication") and item.get("date_publication"):
        connu["date_publication"] = item["date_publication"]
    for source in item["sources"]:
        if source not in connu["sources"]:
            connu["sources"].append(source)


def _corps_langsearch(
    payload: dict[str, Any],
    *,
    requete: str,
    nb: int,
    fraicheur: str,
    plafond_texte: int,
) -> dict[str, Any]:
    """Corps JSON de ``POST /v1/web-search`` : la demande, traduite pour l'API.

    Endpoint officiel (tiret, docs.langsearch.com). Le mode texte passe par
    ``contents.text`` — l'API rend le contexte de la page, ce n'est pas un
    scraping (instruction/05 §4).
    """
    corps: dict[str, Any] = {"query": requete, "count": nb, "freshness": fraicheur}
    if payload.get("include_domains"):
        corps["includeDomains"] = list(payload["include_domains"])
    if payload.get("exclude_domains"):
        corps["excludeDomains"] = list(payload["exclude_domains"])
    if payload.get("texte_complet"):
        corps["contents"] = {
            "text": {
                "max_characters": min(
                    int(payload.get("max_caracteres") or 3000), plafond_texte
                )
            }
        }
    return corps


def build_web_search_tool(
    *,
    identite: AgentIdentity,
    policy: PermissionPolicy,
    settings: Any,
    client: httpx.Client | None = None,
) -> Any:
    """Construit le ``TypedTool`` ``langsearch_web_search`` pour UN agent.

    Args:
        identite: identité de l'agent appelant (traçabilité).
        policy: permissions de l'agent — la capacité ``web_search`` est exigée.
        settings: ``Settings`` (clé LangSearch, base URL, bornes de résultats).
        client: ``httpx.Client`` injectable pour les tests (mock réseau) ;
            un client par défaut est créé sinon (timeout borné, pas de retry
            implicite d'httpx).

    Returns:
        Un ``TypedTool`` nommé ``langsearch_web_search`` (schéma ``WebSearchInput``).
    """
    cle = settings.langsearch_api_key
    base_url = settings.langsearch_base_url.rstrip("/")
    plafond = settings.web_search_max_results
    plafond_texte = int(getattr(settings, "web_search_text_max_chars", 3000))
    #: Requêtes distinctes acceptées en un appel : la configuration peut serrer
    #: la borne du schéma (``web_search_max_queries``), jamais l'élargir.
    requetes_max = min(
        int(getattr(settings, "web_search_max_queries", _MAX_REQUETES) or _MAX_REQUETES),
        _MAX_REQUETES,
    )
    client_fourni = client is not None

    def _recherche_unique(
        cible: httpx.Client,
        payload: dict[str, Any],
        *,
        requete: str,
        demandees: list[str],
        nb: int,
        fraicheur: str,
    ) -> dict[str, Any]:
        """Une requête fédérée (LangSearch + moteurs), mémorisée 30 minutes.

        Rendered for one keyword only: the caller loops over the list and merges.
        Le cache est **par requête** : répéter un mot-clé dans un appel suivant
        ne repart pas chez les fournisseurs, alors qu'un mot-clé nouveau, lui,
        est réellement cherché.
        """
        # Clé = tout ce qui peut changer la réponse. La permission vient d'être
        # vérifiée : un agent refusé ne lit jamais une entrée écrite par un autre.
        cle_cache = (
            requete,
            nb,
            fraicheur,
            tuple(demandees),
            tuple(payload.get("include_domains") or ()),
            tuple(payload.get("exclude_domains") or ()),
            bool(payload.get("texte_complet")),
            int(payload.get("max_caracteres") or 0),
        )
        en_cache = CACHE_RECHERCHE_WEB.obtenir(cle_cache)
        if en_cache is not None:
            return en_cache

        avertissements: list[str] = []
        lots: list[tuple[str, list[dict[str, Any]]]] = []
        usage: dict[str, Any] = {}
        log_id: str | None = None

        if SOURCE_LANGSEARCH in demandees:
            try:
                # Clé absente => dit explicitement (jamais un None fuyant) ; le
                # moteur public, lui, prend le relais et la réponse le porte.
                if cle is None or not cle.get_secret_value().strip():
                    msg = (
                        "langsearch_web_search : LANGSEARCH_API_KEY n'est pas "
                        "configurée (SecretStr, cf. Settings) — seule DuckDuckGo "
                        "répond"
                    )
                    raise ValidationError(msg)
                resultats, usage, log_id = pages_langsearch(
                    cible,
                    cle=cle.get_secret_value(),
                    base_url=base_url,
                    corps=_corps_langsearch(
                        payload,
                        requete=requete,
                        nb=nb,
                        fraicheur=fraicheur,
                        plafond_texte=plafond_texte,
                    ),
                    nb=nb,
                    timeout=TIMEOUT_HTTP,
                )
                lots.append(
                    (
                        SOURCE_LANGSEARCH,
                        [{**item, "sources": [SOURCE_LANGSEARCH]} for item in resultats],
                    )
                )
            except (ValidationError, httpx.HTTPError) as erreur:
                avertissements.append(f"LangSearch indisponible : {erreur}")

        if SOURCE_MOTEURS in demandees:
            try:
                lots.append(
                    (
                        SOURCE_MOTEURS,
                        [
                            {**item, "sources": [SOURCE_MOTEURS]}
                            for item in moteurs_web.pages(requete, nb)
                        ],
                    )
                )
            except ValidationError as erreur:
                avertissements.append(f"Moteurs publics indisponibles : {erreur}")

        if not lots:
            msg = "Recherche web impossible : " + " ; ".join(avertissements)
            raise ValidationError(msg, details={"sources": demandees})

        resultats = _fusionner(lots, nb)
        if not resultats and avertissements:
            # Aucun fournisseur n'a rien rendu *et* au moins un a échoué : le dire
            # plutôt que renvoyer une liste vide qui ferait croire à une absence
            # de résultats.
            msg = "Recherche web sans résultat : " + " ; ".join(avertissements)
            raise ValidationError(msg, details={"sources": demandees})

        if SOURCE_MOTEURS in demandees and fraicheur != "noLimit":
            avertissements.append(
                "filtre de fraîcheur non appliqué par les moteurs publics "
                "(réservé à LangSearch) : leurs résultats peuvent être plus "
                "anciens que demandé"
            )

        reponse = {
            "requete": requete,
            "nb_resultats": len(resultats),
            "resultats": resultats,
            "sources_utilisees": [source for source, _ in lots],
            "avertissements": avertissements,
            "usage": usage,
            "log_id": log_id,
        }
        # Seul un résultat réussi est mémorisé : une panne passagère doit pouvoir
        # être retentée au prochain appel, pas rester collée 30 minutes.
        CACHE_RECHERCHE_WEB.memoriser(cle_cache, reponse)
        return reponse

    def _rechercher(cible: httpx.Client, payload: dict[str, Any]) -> dict[str, Any]:
        policy.require(identite.agent_id, "web_search")

        demandees = list(payload.get("sources") or (SOURCE_LANGSEARCH, SOURCE_MOTEURS))
        demande = payload.get("max_results") or plafond
        nb = min(int(demande), plafond)
        fraicheur = payload["freshness"]
        requetes = list(payload.get("requetes") or (payload["requete"],))
        if len(requetes) > requetes_max:
            msg = (
                f"{len(requetes)} requêtes dans un même appel, maximum {requetes_max} : "
                "regroupe les mots-clés proches ou fais un second appel."
            )
            raise ValidationError(msg, details={"requetes_max": requetes_max})

        # Un crédit par appel de tool, pas par mot-clé : regrouper cinq mots-clés
        # coûte un appel, c'est le geste encouragé (budget anti-boucle). Le
        # palier annoncé resserre le plafond du tour (8 / 15 / 25).
        consommer_recherche(payload.get("niveau"))

        blocs: list[dict[str, Any]] = []
        for requete in requetes:
            blocs.append(
                _recherche_unique(
                    cible,
                    payload,
                    requete=requete,
                    demandees=demandees,
                    nb=nb,
                    fraicheur=fraicheur,
                )
            )

        # Fusion **entre requêtes** : une adresse vue par deux mots-clés reste une
        # entrée, avec ses deux provenances ; la largeur augmente, le volume non.
        lots = [(bloc["requete"], bloc["resultats"]) for bloc in blocs]
        resultats = _fusionner(lots, min(_MAX_RESULTATS_FUSION, nb * len(requetes)))

        avertissements: list[str] = []
        sources_utilisees: list[str] = []
        usage: dict[str, Any] = {}
        log_id: str | None = None
        for bloc in blocs:
            for avertissement in bloc["avertissements"]:
                if avertissement not in avertissements:
                    avertissements.append(avertissement)
            for source in bloc["sources_utilisees"]:
                if source not in sources_utilisees:
                    sources_utilisees.append(source)
            for cle_usage, valeur in (bloc.get("usage") or {}).items():
                if isinstance(valeur, int | float) and not isinstance(valeur, bool):
                    usage[cle_usage] = usage.get(cle_usage, 0) + valeur
            log_id = bloc.get("log_id") or log_id

        return {
            "requete": " | ".join(requetes),
            "requetes": blocs,
            "nb_resultats": len(resultats),
            "resultats": resultats,
            "sources_utilisees": sources_utilisees,
            "avertissements": avertissements,
            "usage": usage,
            "log_id": log_id,
        }

    def _handler(payload: dict[str, Any]) -> dict[str, Any]:
        if client_fourni and client is not None:
            return _rechercher(client, payload)
        # Client éphémère par appel : pas d'état partagé à fermer, un agent peut
        # enchaîner plusieurs recherches sans ressource orpheline.
        with httpx.Client(timeout=TIMEOUT_HTTP, headers=EN_TETES) as temporaire:
            return _rechercher(temporaire, payload)

    from app.tools.base import TypedTool

    return TypedTool(
        name="langsearch_web_search",
        description=(
            "Recherche web sur deux sources fusionnées : l'API LangSearch et les "
            "moteurs publics (Bing, Yahoo, Brave, Mojeek, DuckDuckGo, essayés tour "
            "à tour). Un fournisseur en panne n'empêche pas l'autre de répondre : "
            "les avertissements le disent, résultat par résultat la clé `sources` "
            "dit d'où il vient. Plusieurs mots-clés se demandent en UN appel "
            "(`requetes` : liste, jusqu'à cinq), les résultats sont fusionnés et "
            "dédoublonnés : ne relance pas une recherche pour une variante proche, "
            "regroupe-la. Titre, URL, résumé (ou texte de page via "
            "texte_complet), fraîcheur et filtres de domaines. "
            "Budget d'appels par tour : annonce la profondeur dans `niveau` "
            "(ordinaire = 8 appels, verification = 15, approfondie = 25) au "
            "premier appel ; au-delà, l'outil refuse et demande de conclure avec "
            "les sources déjà obtenues. "
            "FICHIERS : un document ou une image se cherchent ici, avec "
            "l'opérateur dans la requête : « rapport annuel filetype:pdf », "
            "« budget filetype:xlsx », « tour Eiffel filetype:jpg » ; le lien "
            "rendu est alors celui du fichier, pas d'une page. "
            "Lecture seule : rien n'est écrit, aucun fichier n'est conservé. Pour "
            "lire le contenu d'une page trouvée, utilise fetch_url ou read_urls. "
            "Source complémentaire, jamais la vérité métier."
        ),
        handler=_handler,
        input_schema=WebSearchInput,
        tags=("web", "recherche"),
    )


# --- Lecture de pages --------------------------------------------------------


class FetchUrlInput(BaseModel):
    """Entrée typée de ``fetch_url``."""

    model_config = ConfigDict(extra="forbid")

    url: str = Field(
        min_length=8,
        max_length=2000,
        description="Adresse http(s) à lire (hôte public uniquement).",
    )
    methode: Literal["GET", "POST"] = Field(
        default="GET",
        description="GET (défaut) ou POST. Rien n'est envoyé au site au-delà des paramètres.",
    )
    parametres: dict[str, Any] | None = Field(
        default=None,
        description=(
            "GET : paramètres de requête ajoutés à l'adresse ; POST : corps JSON "
            "envoyé au service."
        ),
    )
    max_caracteres: int | None = Field(
        default=None,
        ge=200,
        le=200_000,
        description="Plafond du texte rendu (défaut : borne de configuration).",
    )


class ReadUrlsInput(BaseModel):
    """Entrée typée de ``read_urls``."""

    model_config = ConfigDict(extra="forbid")

    urls: list[str] = Field(
        min_length=1,
        max_length=_MAX_URLS,
        description="Adresses http(s) à lire, dans l'ordre où les contenus sont attendus.",
    )
    max_caracteres: int | None = Field(
        default=None,
        ge=200,
        le=200_000,
        description="Plafond de texte **par page** (défaut : borne de configuration).",
    )


def _texte_lisible(html: str, url: str) -> tuple[str, str]:
    """``(titre, texte)`` d'une page HTML — article si possible, corps sinon.

    ``trafilatura`` (extra « web ») retire menus et pieds de page : c'est le
    texte qu'un lecteur humain retiendrait. Absent, l'extraction retombe sur
    BeautifulSoup — un peu plus bruyante, jamais vide ni inventée.
    """
    soup = soupe(html)
    titre = " ".join(soup.title.get_text(" ", strip=True).split()) if soup.title else ""

    texte = ""
    try:
        import trafilatura

        texte = (
            trafilatura.extract(
                html,
                url=url or None,
                include_comments=False,
                include_tables=True,
                favor_recall=True,
            )
            or ""
        )
    except ImportError:  # pragma: no cover - dépend de l'environnement
        texte = ""

    if len(texte) < 200:
        for balise in soup(
            [
                "script",
                "style",
                "noscript",
                "header",
                "footer",
                "nav",
                "aside",
                "form",
                "iframe",
                "svg",
            ]
        ):
            balise.decompose()
        racine = soup.find("article") or soup.find("main") or soup.body or soup
        texte = racine.get_text("\n", strip=True)
    return titre, _nettoyer(texte)


def _nettoyer(texte: str) -> str:
    """Texte replié : espaces insécables et lignes vides en surnombre retirés."""
    sans_espaces = re.sub(r"[ \t\r\f\v]+", " ", texte)
    return re.sub(r"\n\s*\n+", "\n\n", sans_espaces).strip()


#: Jeu de caractères annoncé, dans l'en-tête ``Content-Type`` ou dans la page.
_CHARSET = re.compile(r"""charset=["']?\s*([\w.:+-]+)""", re.I)


def _decoder(octets: bytes, entetes: dict[str, str]) -> str:
    """Texte décodé : charset annoncé, sinon celui de la page, sinon un repli sûr.

    Le repli est ``cp1252`` après un UTF-8 refusé : les pages françaises encore
    servies en Windows-1252 s'affichent avec leurs accents, pas en « Ã© ».
    """
    annonce = _CHARSET.search(entetes.get("content-type", ""))
    if annonce is None:
        annonce = _CHARSET.search(octets[:4096].decode("ascii", errors="ignore"))
    jeux = ([annonce.group(1)] if annonce else []) + ["utf-8", "cp1252"]
    for jeu in jeux:
        try:
            return octets.decode(jeu)
        except (LookupError, UnicodeDecodeError):
            continue
    return octets.decode("utf-8", errors="replace")


@dataclass(frozen=True, slots=True)
class _ReponseLue:
    """Un saut HTTP déjà lu : statut, en-têtes, texte (vide si rien à lire)."""

    statut: int
    entetes: dict[str, str]
    texte: str
    octets_tronques: bool


def _aspirer(
    client: httpx.Client,
    *,
    url: str,
    methode: str,
    parametres: dict[str, Any] | None,
    timeout: float,
    max_octets: int,
) -> _ReponseLue:
    """Un saut HTTP **borné en octets** : le corps est lu en flux, jamais entier.

    Une adresse qui sert un fichier de plusieurs gigaoctets ne doit pas remplir
    la mémoire du serveur pour finir en « type non supporté » : la lecture
    s'arrête au plafond, et le dépassement est dit (``octets_tronques``).

    Raises:
        httpx.HTTPError: réseau coupé, délai dépassé — l'appelant en fait une
            entrée d'erreur lisible, jamais un traceback.
        ValidationError: corps annoncé au-delà du plafond (avant toute lecture).
    """
    with client.stream(
        methode,
        url,
        params=parametres if methode == "GET" else None,
        json=parametres if methode == "POST" else None,
        timeout=timeout,
        follow_redirects=False,
    ) as reponse:
        entetes = {cle.lower(): valeur for cle, valeur in reponse.headers.items()}
        if reponse.status_code != 200:
            # Redirection ou erreur : rien à lire, l'appelant décide.
            return _ReponseLue(reponse.status_code, entetes, "", False)
        annonce = entetes.get("content-length")
        if annonce and annonce.isdigit() and int(annonce) > max_octets:
            msg = (
                f"Page trop volumineuse ({int(annonce)} octets annoncés) : la "
                f"lecture s'arrête à {max_octets} octets"
            )
            raise ValidationError(msg, details={"max_octets": max_octets})
        morceaux: list[bytes] = []
        total = 0
        tronque = False
        for bloc in reponse.iter_bytes(_BLOC_LECTURE):
            total += len(bloc)
            if total > max_octets:
                tronque = True
                break
            morceaux.append(bloc)
        return _ReponseLue(
            reponse.status_code,
            entetes,
            _decoder(b"".join(morceaux), entetes),
            tronque,
        )


def _lire_page(
    client: httpx.Client,
    *,
    url: str,
    methode: str,
    parametres: dict[str, Any] | None,
    max_caracteres: int,
    max_octets: int,
    timeout: float,
) -> dict[str, Any]:
    """Contenu lisible d'une adresse — **une entrée par URL, jamais une levée**.

    Les échecs ordinaires (site qui répond 404, réseau coupé, type non textuel)
    remplissent ``erreur`` et laissent ``contenu`` vide : une adresse morte ne
    doit pas faire échouer la lecture des autres. Seul le refus **d'adresse**
    (schéma non http(s), hôte non public, identifiants embarqués) lève une
    ``ValidationError`` : c'est une décision de sécurité, pas un accident.
    """
    entree: dict[str, Any] = {
        "url": url,
        "statut": None,
        "titre": "",
        "type_contenu": "",
        "contenu": "",
        "tronque": False,
        "erreur": None,
    }

    courante = url_publique_verifiee(url)
    methode = methode.upper()
    reponse: _ReponseLue | None = None
    for _ in range(_MAX_REDIRECTIONS + 1):
        try:
            reponse = _aspirer(
                client,
                url=courante,
                methode=methode,
                parametres=parametres,
                timeout=timeout,
                max_octets=max_octets,
            )
        except httpx.HTTPError as exc:
            entree["erreur"] = f"Erreur réseau : {exc.__class__.__name__}"
            return entree
        except ValidationError as refus:
            entree["erreur"] = str(refus)
            return entree
        if reponse.statut not in _STATUTS_REDIRECTION:
            break
        cible = reponse.entetes.get("location")
        if not cible:
            entree["statut"] = reponse.statut
            entree["erreur"] = "Redirection sans destination annoncée"
            return entree
        # Chaque saut repasse par la garde : une redirection ne doit pas mener à
        # la boucle locale ou au réseau interne.
        courante = url_publique_verifiee(urljoin(courante, cible))
    else:
        entree["statut"] = reponse.statut if reponse is not None else None
        entree["erreur"] = f"Trop de redirections ({_MAX_REDIRECTIONS})"
        return entree

    if reponse is None:
        entree["erreur"] = "Aucune réponse du site"
        return entree

    entree["url_finale"] = courante
    entree["statut"] = reponse.statut
    type_contenu = reponse.entetes.get("content-type", "").lower()
    entree["type_contenu"] = type_contenu
    entree["tronque"] = reponse.octets_tronques

    if reponse.statut != 200:
        entree["erreur"] = f"HTTP {reponse.statut}"
        return entree

    if "json" in type_contenu:
        try:
            texte = json.dumps(json.loads(reponse.texte), ensure_ascii=False, indent=2)
        except ValueError:
            texte = _nettoyer(reponse.texte)
    elif "html" in type_contenu or "xml" in type_contenu or not type_contenu:
        entree["titre"], texte = _texte_lisible(reponse.texte, courante)
    elif type_contenu.startswith("text/"):
        texte = _nettoyer(reponse.texte)
    else:
        entree["erreur"] = f"Type non supporté : {type_contenu}"
        return entree

    if len(texte) > max_caracteres:
        texte = texte[:max_caracteres]
        entree["tronque"] = True
    entree["contenu"] = texte
    return entree


def build_fetch_url_tool(
    *,
    identite: AgentIdentity,
    policy: PermissionPolicy,
    settings: Any,
    client: httpx.Client | None = None,
) -> Any:
    """Construit le ``TypedTool`` ``fetch_url`` pour UN agent.

    Args:
        identite: identité de l'agent appelant (traçabilité).
        policy: permissions de l'agent — la capacité ``web_search`` est exigée :
            un agent qui n'a pas le droit de chercher sur le web n'a pas celui
            d'en lire les pages.
        settings: ``Settings`` (plafond de texte, délai).
        client: ``httpx.Client`` injectable (tests hors réseau).

    Returns:
        Un ``TypedTool`` nommé ``fetch_url`` (schéma ``FetchUrlInput``).
    """
    plafond = int(getattr(settings, "web_fetch_max_chars", 20_000))
    octets = int(getattr(settings, "web_fetch_max_bytes", 2_000_000))
    timeout = float(getattr(settings, "web_fetch_timeout_seconds", 15.0))
    client_fourni = client is not None

    def _lire(cible: httpx.Client, payload: dict[str, Any]) -> dict[str, Any]:
        policy.require(identite.agent_id, "web_search")
        demande = int(payload.get("max_caracteres") or plafond)
        return _lire_page(
            cible,
            url=payload["url"],
            methode=payload.get("methode") or "GET",
            parametres=payload.get("parametres"),
            max_caracteres=min(demande, plafond),
            max_octets=octets,
            timeout=timeout,
        )

    def _handler(payload: dict[str, Any]) -> dict[str, Any]:
        if client_fourni and client is not None:
            return _lire(client, payload)
        with httpx.Client(timeout=timeout, headers=EN_TETES) as temporaire:
            return _lire(temporaire, payload)

    from app.tools.base import TypedTool

    return TypedTool(
        name="fetch_url",
        description=(
            "Lit une adresse http(s) et rend son contenu lisible : texte extrait "
            "d'une page HTML (titre + corps, menus et pieds de page retirés), JSON "
            "reformaté, ou texte brut. GET avec paramètres de requête, ou POST "
            "avec un corps JSON pour interroger un service. Lecture seule : rien "
            "n'est écrit ni conservé, et les hôtes non publics (réseau interne, "
            "boucle locale) sont refusés. Multiple adresses : read_urls."
        ),
        handler=_handler,
        input_schema=FetchUrlInput,
        tags=("web", "lecture"),
    )


def build_read_urls_tool(
    *,
    identite: AgentIdentity,
    policy: PermissionPolicy,
    settings: Any,
    client: httpx.Client | None = None,
) -> Any:
    """Construit le ``TypedTool`` ``read_urls`` pour UN agent.

    Args:
        identite: identité de l'agent appelant (traçabilité).
        policy: permissions de l'agent — capacité ``web_search`` exigée.
        settings: ``Settings`` (plafonds de texte, nombre d'adresses, délai).
        client: ``httpx.Client`` injectable (tests hors réseau).

    Returns:
        Un ``TypedTool`` nommé ``read_urls`` (schéma ``ReadUrlsInput``).
    """
    plafond = int(getattr(settings, "web_fetch_max_chars", 20_000))
    plafond_urls = int(getattr(settings, "web_read_urls_max", 5))
    budget = int(getattr(settings, "web_read_urls_total_chars", 60_000))
    octets = int(getattr(settings, "web_fetch_max_bytes", 2_000_000))
    timeout = float(getattr(settings, "web_fetch_timeout_seconds", 15.0))
    client_fourni = client is not None

    def _lire(cible: httpx.Client, payload: dict[str, Any]) -> dict[str, Any]:
        policy.require(identite.agent_id, "web_search")
        urls: list[str] = list(payload["urls"])
        if len(urls) > plafond_urls:
            # Refus explicite : tronquer la liste laisserait croire que tout a
            # été lu (plafond ``web_read_urls_max``).
            msg = (
                f"read_urls lit au plus {plafond_urls} adresses par appel "
                f"({len(urls)} reçues) : découpe la demande"
            )
            raise ValidationError(msg, details={"max_urls": plafond_urls})

        par_page = min(int(payload.get("max_caracteres") or plafond), plafond)
        restant = budget
        resultats: list[dict[str, Any]] = []
        vues: set[str] = set()
        for url in urls:
            cle = _cle_url(url)
            if cle in vues:
                continue
            vues.add(cle)
            if restant <= 0:
                # Le budget est épuisé : l'adresse est **dite non lue**, jamais
                # silencieusement absente de la réponse.
                resultats.append(
                    {
                        "url": url,
                        "statut": None,
                        "titre": "",
                        "type_contenu": "",
                        "contenu": "",
                        "tronque": False,
                        "erreur": (
                            "non lue : le budget de caractères de l'appel est "
                            "épuisé (relance sur cette adresse seule)"
                        ),
                    }
                )
                continue
            try:
                entree = _lire_page(
                    cible,
                    url=url,
                    methode="GET",
                    parametres=None,
                    max_caracteres=min(par_page, restant),
                    max_octets=octets,
                    timeout=timeout,
                )
            except ValidationError as refus:
                # Adresse refusée par la garde anti-SSRF : les autres restent
                # lues, celle-ci est dite refusée avec la raison exacte.
                entree = {
                    "url": url,
                    "statut": None,
                    "titre": "",
                    "type_contenu": "",
                    "contenu": "",
                    "tronque": False,
                    "erreur": str(refus),
                }
            restant -= len(entree["contenu"])
            resultats.append(entree)
        return {
            "nb_urls": len(resultats),
            "nb_lues": sum(1 for entree in resultats if entree["contenu"]),
            "caracteres_total": sum(len(entree["contenu"]) for entree in resultats),
            "resultats": resultats,
        }

    def _handler(payload: dict[str, Any]) -> dict[str, Any]:
        if client_fourni and client is not None:
            return _lire(client, payload)
        with httpx.Client(timeout=timeout, headers=EN_TETES) as temporaire:
            return _lire(temporaire, payload)

    from app.tools.base import TypedTool

    return TypedTool(
        name="read_urls",
        description=(
            "Lit plusieurs adresses http(s) en un appel et rend, **pour chacune**, "
            "son titre et son contenu lisible (texte de la page, JSON reformaté ou "
            "texte brut) — ou la raison exacte de l'échec. Une adresse morte ne "
            "fait pas échouer les autres. Un budget de caractères global borne "
            "l'ensemble pour ne pas saturer la conversation ; une page qui dépasse "
            "porte `tronque: true`. Lecture seule : rien n'est écrit ni conservé."
        ),
        handler=_handler,
        input_schema=ReadUrlsInput,
        tags=("web", "lecture"),
    )


#: Tools web attachables à un contrat d'agent, dans l'ordre d'exposition.
_CONSTRUCTEURS: dict[str, Any] = {
    "langsearch_web_search": build_web_search_tool,
    "fetch_url": build_fetch_url_tool,
    "read_urls": build_read_urls_tool,
}


def attacher_outils_web(
    definition: Any,
    extra: tuple[Any, ...],
    *,
    settings: Any | None = None,
) -> tuple[Any, ...]:
    """Ajoute les tools web que le contrat de l'agent déclare.

    Construction paresseuse : le tool existe dès qu'il est déclaré, et le réseau
    n'est touché qu'à l'invocation. Une clé LangSearch absente n'empêche donc pas
    le contrat d'être complet — la recherche fédérée le dit à l'appel.
    """
    demandes = [nom for nom in _CONSTRUCTEURS if nom in getattr(definition, "tools", ())]
    if not demandes:
        return extra
    from app.core.config import get_settings

    reglages = settings or get_settings()
    outils = tuple(
        _CONSTRUCTEURS[nom](
            identite=AgentIdentity(agent_id=definition.agent_id),
            policy=definition.policy,
            settings=reglages,
        )
        for nom in demandes
    )
    return extra + outils
