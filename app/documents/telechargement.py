"""Rapatriement contrôlé d'une **image** externe trouvée sur le web.

Un utilisateur peut décider de faire entrer dans CARSO une image qu'il a trouvée
sur le web. Ce module ne fait **que** rapatrier les octets—il ne les enregistre
pas (le service documentaire s'en charge) et il ne devine rien.

**Les vidéos ne sont jamais rapatriées** (correctif de spécification du
08/10/2026)—elles se téléchargent depuis la source vers le disque de
l'utilisateur, comme un téléchargement ordinaire. Un fichier vidéo qu'il faut
conserver se dépose ensuite comme document, par le dépôt ordinaire.

Le danger d'un « télécharge cette URL pour moi » est connu—c'est le **SSRF**.
La même requête peut viser la boucle locale, le réseau interne, un service de
métadonnées d'hébergeur (`169.254.169.254`) ou un service d'administration
exposé sur le réseau privé. Un média « image » peut aussi être une page HTML
d'erreur, un exécutable renommé, ou un fichier de plusieurs gigaoctets.

Ce que ce module refuse donc, avant et pendant la requête :

- tout schéma autre que ``http``/``https`` (pas de ``file:``, ``ftp:``, ``data:``) ;
- une URL qui porte des identifiants (``user:pass@``)—jamais transmis ;
- un hôte dont **une seule** adresse résolue est privée, loopback, lien-local,
  multicast ou réservée—il faut que toutes soient publiques ;
- une redirection—elle est suivie **à la main**, chaque saut étant revalidé
  (sinon un hôte public peut renvoyer vers ``127.0.0.1``) ;
- un corps qui dépasse le plafond—vérifié sur ``Content-Length`` **et** en
  flux, car un en-tête absent ou menteur ne protège de rien ;
- un contenu qui n'est pas une **image reconnue à sa signature**—le
  ``Content-Type`` déclaré par le serveur n'autorise jamais un fichier, une page
  d'erreur servie en 200 est refusée au lieu d'être enregistrée, et une vidéo
  reconnue est refusée avec un motif explicite (elle n'entre pas dans CARSO).

Ce que ce module ne fait pas—suivre un média derrière une authentification,
rejouer des cookies, ni exécuter quoi que ce soit du contenu récupéré.
"""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from typing import Any
from urllib.parse import unquote, urljoin, urlsplit

import httpx

from app.core.errors import ValidationError
from app.documents.paths import sanitize_filename
from app.documents.validation import FileTypeRule, detecter_media

__all__ = ["MediaRecupere", "recuperer_media", "url_publique_verifiee"]

#: Nombre de sauts suivis avant de refuser (une redirection légitime suffit ;
#: au-delà, c'est une chaîne qui cache sa destination).
MAX_REDIRECTIONS = 3
_TAILLE_BLOC = 64 * 1024
_NOM_MAX = 80
#: Statuts qui ne sont jamais un média.
_STATUTS_REDIRECTION = frozenset({301, 302, 303, 307, 308})
#: Octets lus pour la détection du type réel (marge au-delà du décalage WebP).
_LECTURE_SIGNATURE = 16
_CARACTERES_NOM = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_ .()"
)
_EN_TETES = {
    "User-Agent": "CARSO-AI/1.0 (+agent interne CARSO)",
    "Accept": "image/*,*/*;q=0.1",
}


@dataclass(frozen=True, slots=True)
class MediaRecupere:
    """Image rapatriée, prête à être déposée comme document CARSO."""

    octets: bytes
    nom_fichier: str
    mime_type: str
    extension: str
    url_source: str
    url_finale: str
    taille_octets: int


def _refuser(message: str, **details: Any) -> ValidationError:
    return ValidationError(message, details=details or None)


def url_publique_verifiee(url: str) -> str:
    """URL acceptable pour une requête sortante, ou refus explicite.

    Publique parce qu'un second usage existe (``app.documents.video_web``, qui
    confie l'adresse à ``yt-dlp`` pour lire une page vidéo) : les deux doivent
    opposer **la même** garde à une adresse visant la boucle locale ou le réseau
    interne. Un hôte privé doit être refusé avant que quoi que ce soit ne parte —
    y compris un extracteur tiers.
    """
    brut = (url or "").strip()
    if not brut:
        raise _refuser("URL manquante : indiquez l'adresse du média à importer")
    parties = urlsplit(brut)
    if parties.scheme not in ("http", "https"):
        raise _refuser(
            "Seules les adresses http(s) peuvent être importées",
            schema=parties.scheme or None,
        )
    if parties.username or parties.password:
        raise _refuser(
            "L'adresse ne doit pas contenir d'identifiants : CARSO ne s'authentifie "
            "pas à votre place sur un site externe",
            hote=parties.hostname,
        )
    if not parties.hostname:
        raise _refuser("L'adresse ne désigne aucun hôte")
    _hote_public(parties.hostname)
    return brut


def _hote_public(hote: str) -> None:
    """Refuse un hôte dont une adresse résolue n'est pas publique (anti-SSRF).

    La résolution est explicite : c'est elle qui révèle qu'un joli nom de
    domaine pointe, en fait, vers ``127.0.0.1`` ou vers le réseau interne.
    """
    try:
        infos = socket.getaddrinfo(hote, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise _refuser("Hôte introuvable : impossible de résoudre l'adresse", hote=hote) from exc

    adresses: list[str] = []
    for info in infos:
        brut = info[4][0]
        adresses.append(brut)
        try:
            adresse = ipaddress.ip_address(brut)
        except ValueError as exc:
            # Une adresse que l'on ne sait pas classer est refusée : le défaut
            # est le refus, jamais l'autorisation par ignorance.
            raise _refuser(
                "Hôte refusé : adresse illisible, impossible de vérifier qu'elle "
                "est publique",
                hote=hote,
                adresse=brut,
            ) from exc
        if not adresse.is_global:
            raise _refuser(
                "Hôte refusé : l'adresse visée n'est pas publique (réseau interne, "
                "boucle locale ou adresse réservée)",
                hote=hote,
                adresse=brut,
            )
    if not adresses:
        raise _refuser("Hôte introuvable : aucune adresse pour ce nom", hote=hote)


def _client(client: httpx.Client | None, timeout: float) -> tuple[httpx.Client, bool]:
    """Client de requête et « est-il à nous ? » (fermé seulement s'il est nôtre)."""
    if client is not None:
        return client, False
    return httpx.Client(timeout=timeout, headers=_EN_TETES, follow_redirects=False), True


def _saut(
    client: httpx.Client,
    url: str,
    *,
    timeout: float,
    max_bytes: int,
) -> tuple[str, bytes, str, str]:
    """Un saut HTTP : ``(url_suivante, octets, type_réel, url_finale)``.

    Un statut de redirection rend l'URL suivante (sans corps) ; un 200 rend les
    octets **déjà bornés** et le type reconnu à la signature.
    """
    courant = url_publique_verifiee(url)
    with client.stream("GET", courant, timeout=timeout) as reponse:
        if reponse.status_code in _STATUTS_REDIRECTION:
            cible = reponse.headers.get("location")
            if not cible:
                raise _refuser(
                    "Redirection sans destination : le média ne peut pas être rapatrié",
                    statut=reponse.status_code,
                )
            return urljoin(courant, cible), b"", "", courant
        if reponse.status_code != 200:
            raise _refuser(
                f"Le site a répondu {reponse.status_code} : le média n'a pas pu être "
                "rapatrié",
                statut=reponse.status_code,
                url=courant,
            )

        annonce = reponse.headers.get("content-length")
        if annonce and annonce.isdigit() and int(annonce) > max_bytes:
            raise _refuser(
                f"Média trop volumineux ({int(annonce)} octets) : le plafond "
                f"d'import est de {max_bytes} octets",
                annonce_octets=int(annonce),
                max_octets=max_bytes,
            )

        morceaux: list[bytes] = []
        total = 0
        for morceau in reponse.iter_bytes(_TAILLE_BLOC):
            total += len(morceau)
            if total > max_bytes:
                raise _refuser(
                    f"Média trop volumineux : le plafond d'import est de {max_bytes} "
                    "octets",
                    max_octets=max_bytes,
                )
            morceaux.append(morceau)
    return "", b"".join(morceaux), reponse.headers.get("content-type", ""), courant


def _base_nom(url: str) -> str:
    """Nom de base proposé par l'URL (sans extension), nettoyé ou vide."""
    chemin = urlsplit(url).path
    dernier = unquote(chemin.rsplit("/", 1)[-1]) if chemin else ""
    sans_extension = dernier.rsplit(".", 1)[0] if "." in dernier else dernier
    propre = "".join(caractere for caractere in sans_extension if caractere in _CARACTERES_NOM)
    return " ".join(propre.split())[:_NOM_MAX].strip(" .")


def _nom_fichier(url: str, regle: FileTypeRule, nom_propose: str | None) -> str:
    """Nom de fichier sûr, **dont l'extension est celle du type reconnu**.

    Le nom vient d'une URL tierce : il est filtré, et l'extension du fichier est
    imposée par la signature réelle — l'URL peut annoncer ``.php``, le document
    enregistré reste une image ou une vidéo du type détecté.
    """
    brut = (nom_propose or "").strip() or _base_nom(url)
    hote = urlsplit(url).hostname or "web"
    if not brut:
        brut = f"media-{hote.split('.')[0]}"
    candidat = f"{brut}.{regle.extension}"
    try:
        return sanitize_filename(candidat)
    except ValidationError:
        # Nom d'URL inexploitable (réservé Windows, caractère interdit…) : un
        # nom générique et honnête vaut mieux qu'un refus surprenant.
        return sanitize_filename(f"media-{hote.split('.')[0]}.{regle.extension}")


def recuperer_media(
    url: str,
    *,
    max_bytes: int,
    timeout: float = 20.0,
    nom: str | None = None,
    client: httpx.Client | None = None,
) -> MediaRecupere:
    """Rapatrie une image externe, sous garde-fous, prête à être déposée.

    Args:
        url: adresse fournie par l'utilisateur (celle du résultat affiché).
        max_bytes: plafond de taille de l'image rapatriée.
        timeout: durée de connexion/lecture par saut.
        nom: nom souhaité, sans extension (l'extension est celle du type réel).
        client: client ``httpx`` injectable (les tests n'atteignent pas le réseau).

    Returns:
        L'image : octets, nom de fichier sûr, MIME et extension **du type
        reconnu à la signature**, URL d'origine, URL finale après redirections.

    Raises:
        ValidationError: schéma refusé, hôte non public ou introuvable,
            redirection en boucle ou trop nombreuse, taille au-delà du plafond,
            statut HTTP non 200, contenu qui n'est pas une image reconnue, ou
            **vidéo** reconnue (jamais rapatriée dans CARSO).
    """
    source = url_publique_verifiee(url)
    notre_client, a_fermer = _client(client, timeout)
    try:
        courante = source
        for _ in range(MAX_REDIRECTIONS + 1):
            suivante, octets, _type_annonce, url_finale = _saut(
                notre_client, courante, timeout=timeout, max_bytes=max_bytes
            )
            if suivante:
                courante = suivante
                continue
            regle = detecter_media(octets[:_LECTURE_SIGNATURE])
            if regle is None or not regle.est_media:
                raise _refuser(
                    "Le contenu rapatrié n'est pas une image reconnue : "
                    "rien n'est enregistré (le type annoncé par le site ne fait pas foi)",
                    url=url_finale,
                )
            if regle.mime_type.startswith("video/"):
                raise _refuser(
                    "Cette adresse sert une vidéo : CARSO ne rapatrie jamais de vidéo "
                    "dans son stockage. Téléchargez-la depuis la source sur votre poste "
                    "(l'écran de lecture le propose), puis déposez-la comme document si "
                    "vous devez la conserver.",
                    url=url_finale,
                )
            return MediaRecupere(
                octets=octets,
                nom_fichier=_nom_fichier(url_finale, regle, nom),
                mime_type=regle.mime_type,
                extension=regle.extension,
                url_source=source,
                url_finale=url_finale,
                taille_octets=len(octets),
            )
        raise _refuser(
            f"Trop de redirections ({MAX_REDIRECTIONS}) : la destination du média "
            "n'est pas fiable",
            url=source,
        )
    finally:
        if a_fermer:
            notre_client.close()
