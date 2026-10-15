"""Relais de téléchargement vidéo—le navigateur enregistre, CARSO ne garde rien.

Les plateformes servent leurs fichiers depuis un CDN qui **n'autorise pas** le
navigateur à les lire—mesuré le 09/10/2026 sur YouTube, la réponse ne porte ni
``Access-Control-Allow-Origin`` pour notre origine (Google ne l'accorde qu'à
``https://www.youtube.com``) ni ``Content-Disposition``—même en ajoutant
``file=``, ``title=``, ``download=1`` ou ``response-content-disposition``. Un
``fetch`` côté client est donc illisible et ``<a download>`` est ignoré en
origine croisée—le navigateur ouvre le fichier et le joue au lieu de
l'enregistrer.

D'où ce relais—le client demande le fichier **à CARSO**, qui le retransmet
depuis la source tel quel, en flux, avec un ``Content-Disposition: attachment``.
Le navigateur enregistre alors sur le disque de l'utilisateur, comme un
téléchargement ordinaire. Ce que CARSO ne fait **jamais**—écrire, conserver,
versionner ou documenter ces octets—aucun fichier, aucune ligne, aucun
document ; rien n'est assemblé non plus.

Sécurité—deux verrous, parce que la route accepte un lien sans en-tête
d'authentification (un `<a href>` ne peut pas porter de Bearer) :

1. **jeton signé**—l'adresse n'est acceptée que si elle vient d'une analyse
   faite par CARSO. Le flux signe chaque adresse (HMAC-SHA-256, expiration de
   ``DUREE_JETON_SECONDES``) ; le relais refuse tout ce qui n'est pas signé par
   cette instance. Ce n'est donc pas un proxy ouvert—on ne peut pas y faire
   passer une adresse arbitraire ;
2. **garde anti-SSRF**—même signée, une adresse est revalidée
   (``url_publique_verifiee``) avant l'appel, et une redirection n'est pas
   suivie—un CDN public ne peut pas nous renvoyer vers le réseau interne.

Réparation (09/10/2026)—une adresse de plateforme **expire**—le CDN répond
alors ``403`` (ou ``410``) au moment du clic. Plutôt que de renvoyer l'erreur à
l'utilisateur, le jeton porte de quoi **retrouver la même piste** (page analysée,
hauteur, genre) ; ``RelaisVideoService`` réanalyse la page et reprend le fichier
avec l'adresse fraîche, une seule fois, sans rien changer à ce que l'utilisateur
a demandé.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
import time
import unicodedata
from collections.abc import Iterator
from dataclasses import dataclass
from urllib.parse import quote, unquote, urlsplit

import httpx

from app.core.config import Settings, get_settings
from app.core.errors import ValidationError
from app.documents.telechargement import url_publique_verifiee

__all__ = [
    "DUREE_JETON_SECONDES",
    "STATUTS_RECUPERABLES",
    "CibleRelais",
    "FluxRelaye",
    "SourceVideoIndisponible",
    "entete_disposition",
    "nom_propose",
    "ouvrir_flux_relaye",
    "signer_jeton",
    "sonder_sources_disponibles",
    "verifier_jeton",
]

#: Durée de vie d'un lien signé. Assez longue pour un téléchargement lancé depuis
#: l'écran de résolutions, assez courte pour qu'un lien recopié ne serve plus.
DUREE_JETON_SECONDES = 30 * 60

#: Refus de la source qui **se réparent** en réanalysant la page : l'adresse a
#: expiré (``403``) ou n'existe plus à cette adresse (``410``). Un ``404`` ou une
#: panne réseau ne se réparent pas de la même façon et ne sont pas repris.
STATUTS_RECUPERABLES: frozenset[int] = frozenset({403, 410})


class SourceVideoIndisponible(ValidationError):
    """La source a refusé le fichier — avec le statut, pour décider d'une reprise.

    Sous-classe de :class:`ValidationError` : la route répond ``422`` avec le
    motif, exactement comme avant. Le statut permet au service de relancer
    l'analyse **seulement** quand cela peut réparer le téléchargement.
    """

    def __init__(self, message: str, *, statut: int, url: str | None = None) -> None:
        details: dict[str, object] = {"statut": statut}
        if url is not None:
            details["url"] = url
        super().__init__(message, details=details)
        self.statut = statut

    @property
    def recuperable(self) -> bool:
        """Vrai quand une nouvelle analyse de la page peut rendre un lien valide."""
        return self.statut in STATUTS_RECUPERABLES


@dataclass(frozen=True, slots=True)
class CibleRelais:
    """Ce qu'un lien signé désigne : un fichier, et de quoi le retrouver.

    ``url`` seule suffirait à télécharger ; ``page``, ``hauteur`` et ``piste``
    servent à **réparer** un refus de la source (403/410) en réanalysant la page
    et en reprenant la même piste. Ils valent ``None`` quand le lien a été signé
    sans ce contexte (relais d'une adresse quelconque) : dans ce cas, aucun
    secours n'est tenté, et l'erreur de la source est rendue telle quelle.
    """

    url: str
    page: str | None = None
    hauteur: int | None = None
    #: ``video`` | ``audio`` | ``combine`` — la nature du fichier, pas son nom.
    piste: str | None = None
    #: Adresse de la piste son à assembler avec ``url`` (fichier image+son).
    #: Renseignée, le relais **assemble** les deux pistes en un MP4 lisible au
    #: lieu de servir une vidéo muette ; ``vide``, il retransmet ``url`` telle
    #: quelle. Mesuré le 09/10/2026 : les plateformes ne publient plus de fichier
    #: complet (et leurs liens combinés exigent un PO Token), et une vidéo sans
    #: son ne s'ouvre pas toujours avec le lecteur du poste.
    audio_url: str | None = None

    @property
    def reparables(self) -> bool:
        """Vrai si l'analyse peut être relancée pour retrouver **cette** piste."""
        return bool(self.page) and self.hauteur is not None and self.piste is not None

#: Clé de secours hors configuration (lab, tests) : les jetons restent valides
#: tant que le processus tourne. En production, ``AUTH_SECRET`` est la clé.
_CLE_LOCALE = secrets.token_bytes(32)

_TAILLE_BLOC = 64 * 1024
_NOM_MAX = 120
#: Caractères interdits dans un nom de fichier Windows ou dangereux partout.
_INTERDITS = '<>:"/\\|?*'
#: En-têtes de la requête sortante : CARSO se déclare, le navigateur n'est pas imité.
_EN_TETES = {
    "User-Agent": "CARSO-AI/1.0 (+agent interne CARSO)",
    "Accept": "*/*",
}
#: Extensions de repli quand le type MIME est connu mais le nom ne l'est pas.
_EXTENSIONS_MIME = {
    "video/mp4": "mp4",
    "video/webm": "webm",
    "video/ogg": "ogv",
    "video/quicktime": "mov",
    "audio/mp4": "m4a",
    "audio/mpeg": "mp3",
    "audio/webm": "weba",
}


@dataclass(frozen=True, slots=True)
class FluxRelaye:
    """Fichier en cours de retransmission, prêt pour une réponse en flux."""

    octets: Iterator[bytes]
    nom_fichier: str
    type_mime: str
    taille_octets: int | None


def _cle(settings: Settings | None = None) -> bytes:
    """Clé de signature : celle de l'authentification, sinon une clé locale."""
    reglages = settings or get_settings()
    secret = reglages.auth_secret
    if secret is not None and secret.get_secret_value().strip():
        return secret.get_secret_value().encode("utf-8")
    return _CLE_LOCALE


def _encoder(donnees: bytes) -> str:
    return base64.urlsafe_b64encode(donnees).decode("ascii").rstrip("=")


def _decoder(texte: str) -> bytes:
    remplissage = "=" * (-len(texte) % 4)
    return base64.urlsafe_b64decode(texte + remplissage)


def _signature(charge: bytes, cle: bytes) -> bytes:
    return hmac.new(cle, charge, hashlib.sha256).digest()


def signer_jeton(
    cible: CibleRelais | str,
    *,
    settings: Settings | None = None,
    duree: int = DUREE_JETON_SECONDES,
    maintenant: float | None = None,
) -> str:
    """Signe une cible de téléchargement : ``<charge>.<signature>``, valable ``duree``.

    La charge porte l'expiration, l'adresse **et** de quoi retrouver la piste
    (page analysée, hauteur, genre). Un jeton ne vaut donc que pour un fichier,
    pour un temps borné, et il ne peut pas désigner autre chose après
    réanalyse. Rien n'est conservé côté serveur — la vérification est purement
    cryptographique.

    Args:
        cible: l'adresse seule (relais d'une adresse quelconque, sans secours
            possible) ou la cible complète rendue par l'analyse des résolutions.
    """
    if not isinstance(cible, CibleRelais):
        cible = CibleRelais(url=cible)
    expiration = int((maintenant if maintenant is not None else time.time()) + duree)
    charge = _charge(expiration, cible)
    return f"{_encoder(charge)}.{_encoder(_signature(charge, _cle(settings)))}"


def _charge(expiration: int, cible: CibleRelais) -> bytes:
    """Charge signée : JSON compact et stable (les champs vides sont omis)."""
    donnees: dict[str, object] = {"exp": expiration, "url": cible.url}
    if cible.page:
        donnees["page"] = cible.page
    if cible.hauteur is not None:
        donnees["hauteur"] = cible.hauteur
    if cible.piste:
        donnees["piste"] = cible.piste
    if cible.audio_url:
        donnees["son"] = cible.audio_url
    return json.dumps(donnees, separators=(",", ":"), sort_keys=True).encode("utf-8")


def verifier_jeton(
    jeton: str,
    *,
    settings: Settings | None = None,
    maintenant: float | None = None,
) -> CibleRelais:
    """Rend la cible signée, ou refuse.

    Raises:
        ValidationError: jeton illisible, signature invalide, jeton expiré, ou
            adresse signée qui n'est pas http(s).
    """
    try:
        charge_texte, signature_texte = jeton.split(".", 1)
        charge = _decoder(charge_texte)
        signature = _decoder(signature_texte)
    except (ValueError, TypeError) as exc:
        raise ValidationError("Lien de téléchargement invalide") from exc

    if not hmac.compare_digest(signature, _signature(charge, _cle(settings))):
        raise ValidationError("Lien de téléchargement invalide (signature)")

    try:
        donnees = json.loads(charge.decode("utf-8"))
        expiration = int(donnees["exp"])
        url = str(donnees["url"])
    except (ValueError, TypeError, KeyError) as exc:
        raise ValidationError("Lien de téléchargement invalide") from exc

    reference = int(maintenant if maintenant is not None else time.time())
    if expiration < reference:
        raise ValidationError(
            "Lien de téléchargement expiré : relancez l'analyse des résolutions "
            "pour en obtenir un nouveau"
        )
    if not url.startswith(("http://", "https://")):
        raise ValidationError("Lien de téléchargement invalide (adresse non http)")

    hauteur = donnees.get("hauteur")
    piste = donnees.get("piste")
    son = donnees.get("son")
    return CibleRelais(
        url=url,
        page=str(donnees["page"]) if donnees.get("page") else None,
        hauteur=int(hauteur) if isinstance(hauteur, int) else None,
        piste=str(piste) if piste else None,
        audio_url=str(son) if son else None,
    )


def sonder_sources_disponibles(
    video_url: str,
    audio_url: str,
    *,
    timeout: float = 60.0,
    client: httpx.Client | None = None,
) -> None:
    """Vérifie que **les deux** pistes sont servies avant d'assembler.

    Pourquoi sonder alors que ffmpeg irait les chercher lui-même : un assemblage
    qui échoue après l'envoi de l'en-tête HTTP laisserait un fichier vide et une
    erreur invisible. La sonde transforme ce cas en refus **net** — et, quand le
    refus est un ``403``/``410`` (adresse expirée), c'est lui qui déclenche la
    reprise par réanalyse.

    Raises:
        SourceVideoIndisponible: la source refuse la piste (403/410).
        ValidationError: autre statut d'erreur, ou source injoignable.
    """
    notre_client = client or httpx.Client(
        timeout=httpx.Timeout(timeout, connect=10.0),
        follow_redirects=False,
        headers=_EN_TETES,
    )
    a_fermer = client is None
    try:
        for adresse in (video_url, audio_url):
            source = url_publique_verifiee(adresse)
            requete = notre_client.build_request(
                "GET", source, headers={"Range": "bytes=0-0"}
            )
            try:
                with notre_client.stream("GET", source, headers=requete.headers) as reponse:
                    _verifier_reponse(reponse, source)
            except httpx.HTTPError as exc:
                raise ValidationError(
                    "La source n'a pas répondu : le fichier n'a pas pu être ouvert",
                    details={"url": source},
                ) from exc
    finally:
        if a_fermer:
            notre_client.close()


def nom_propose(nom: str | None, url: str, type_mime: str) -> str:
    """Nom de fichier sûr pour l'enregistrement : proposé, nettoyé, avec extension.

    Le nom vient du client (titre de la vidéo + hauteur) : il ne peut pas
    contenir de séparateur de chemin, de caractère de contrôle ni de guillemet —
    ces octets partent dans un en-tête HTTP et dans un système de fichiers. Un
    point qui ne précède pas une extension plausible n'est pas une extension : il
    disparaît au lieu de produire ``clip.avi.mp4`` ou ``evil ..mp4``.
    """
    brut = unicodedata.normalize("NFC", nom or "").strip()
    propre = "".join(" " if _interdit(caractere) else caractere for caractere in brut)
    propre = " ".join(propre.split())

    # Le type **réel** décide de l'extension, jamais le suffixe proposé par le
    # client : « clip.avi » servi en mp4 devient « clip.mp4 ». Seul le vrai
    # suffixe du nom (quand il correspond au type) est conservé par la suite.
    tete, point, queue = propre.rpartition(".")
    base = tete if point and queue.isalnum() and 1 <= len(queue) <= 5 else propre
    extension = _extension_type(url, type_mime)

    base = " ".join(re.sub(r"\s*\.\s*", " ", base).split()).strip(" .")[:_NOM_MAX]
    if not base:
        return f"video.{extension}"
    return f"{base[: _NOM_MAX - len(extension) - 1].strip(' .')}.{extension}"


def _interdit(caractere: str) -> bool:
    """Caractère à remplacer dans un nom : chemin, contrôle, guillemet, pipe…"""
    return caractere in _INTERDITS or ord(caractere) < 32 or ord(caractere) == 127


def _extension_type(url: str, type_mime: str) -> str:
    """Extension retenue quand le nom n'en porte pas : type MIME, sinon chemin."""
    mime = type_mime.split(";")[0].strip().lower()
    if mime in _EXTENSIONS_MIME:
        return _EXTENSIONS_MIME[mime]
    chemin = unquote(urlsplit(url).path)
    if "." in chemin.rsplit("/", 1)[-1]:
        candidate = chemin.rsplit("/", 1)[-1].rsplit(".", 1)[1].lower()
        if 1 <= len(candidate) <= 5 and candidate.isalnum():
            return candidate
    return "bin"


def ouvrir_flux_relaye(
    url: str,
    *,
    nom: str | None = None,
    timeout: float = 60.0,
    client: httpx.Client | None = None,
) -> FluxRelaye:
    """Ouvre le fichier distant et rend de quoi le retransmettre en flux.

    Le corps n'est jamais matérialisé : il passe par blocs vers le client, et
    rien n'est écrit sur le disque du serveur.

    Raises:
        SourceVideoIndisponible: la source a refusé le fichier (403/410) — le
            service peut alors relancer l'analyse.
        ValidationError: adresse refusée (hôte non public), redirection non
            suivie, autre erreur de la source, ou réponse impossible à lire.
    """
    source = url_publique_verifiee(url)
    notre_client = client or httpx.Client(
        timeout=httpx.Timeout(timeout, connect=10.0),
        follow_redirects=False,
        headers=_EN_TETES,
    )
    a_fermer = client is None

    try:
        reponse = notre_client.send(notre_client.build_request("GET", source), stream=True)
    except httpx.HTTPError as exc:
        if a_fermer:
            notre_client.close()
        raise ValidationError(
            "La source n'a pas répondu : le fichier n'a pas pu être ouvert",
            details={"url": source},
        ) from exc

    type_mime, taille = _verifier_reponse(reponse, source)

    def _octets() -> Iterator[bytes]:
        try:
            yield from reponse.iter_bytes(_TAILLE_BLOC)
        finally:
            reponse.close()
            if a_fermer:
                notre_client.close()

    return FluxRelaye(
        octets=_octets(),
        nom_fichier=nom_propose(nom, source, type_mime),
        type_mime=type_mime,
        taille_octets=taille,
    )


def _verifier_reponse(reponse: httpx.Response, source: str) -> tuple[str, int | None]:
    """Type et taille d'une réponse valide — la redirection et l'erreur refusent."""
    code = reponse.status_code
    if code in (301, 302, 303, 307, 308):
        reponse.close()
        raise ValidationError(
            "La source redirige vers une autre adresse : ce lien n'est pas "
            "téléchargeable directement",
            details={"url": source, "statut": code},
        )
    if code >= 400:
        reponse.close()
        message = f"La source a refusé le téléchargement (HTTP {code})"
        if code in STATUTS_RECUPERABLES:
            raise SourceVideoIndisponible(message, statut=code, url=source)
        raise ValidationError(message, details={"url": source, "statut": code})

    type_mime = reponse.headers.get("content-type", "").split(";")[0].strip().lower()
    longueur = reponse.headers.get("content-length", "")
    return type_mime or "application/octet-stream", int(longueur) if longueur.isdigit() else None


def entete_disposition(nom: str) -> str:
    """``Content-Disposition`` d'un vrai téléchargement, nom UTF-8 compris."""
    ascii_sur = "".join(caractere if ord(caractere) < 128 else "_" for caractere in nom) or "video"
    quoted = ascii_sur.replace('"', "_").replace("\\", "_")
    return f'attachment; filename="{quoted}"; filename*=UTF-8\'\'{quote(nom, safe="")}'
