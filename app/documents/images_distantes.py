"""Images distantes d'un document : récupération **contrôlée** (INC-137).

Un document libre écrit par un agent cite des images par URL
(``<img src="https://…">``). Trois raisons de ne pas laisser le convertisseur
les chercher lui-même :

1. **poids** — une photo de 40 Mo n'a rien à faire dans un DOCX ; les bornes
   d'insertion (côté ≤ 1600 px, ≤ 1 Mio) s'appliquent ici comme ailleurs ;
2. **sécurité** — une URL peut viser ``169.254.169.254`` (métadonnées cloud) ou
   le réseau interne : le serveur ne doit pas devenir un proxy (SSRF) ;
3. **affichage garanti** — une URL morte ou un contenu qui n'est pas une image
   doit produire un **repère visible** et un avertissement, jamais une figure
   absente que l'utilisateur découvrirait à l'impression.

Le contenu récupéré est ensuite **embarqué en data URI** : le DOCX/PDF produit
ne dépend plus du réseau, et l'ouverture du fichier plus tard ne dépend plus de
la disponibilité du lien.
"""

from __future__ import annotations

import base64
import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urlparse

from app.core.errors import GenerationError

__all__ = [
    "COTE_MAX_DISTANTE",
    "OCTETS_MAX_DISTANTE",
    "ImageDistante",
    "en_data_uri",
    "recuperer_image",
    "url_autorisee",
]

#: Plafond du téléchargement : au-delà, l'image est refusée (jamais tronquée).
OCTETS_MAX_DISTANTE = 5 * 1024 * 1024

#: Secondes accordées au serveur distant (un lien lent ne bloque pas un tour).
DELAI_SECONDES = 6.0

#: Côté maximal accepté avant réduction.
COTE_MAX_DISTANTE = 4000

#: Schémas acceptés : jamais ``file:``, ``ftp:`` ou ``data:`` (déjà embarqué).
_SCHEMAS = frozenset({"http", "https"})

#: Hôtes explicitement refusés même s'ils résolvent vers une adresse publique.
_HOTES_INTERDITS = frozenset({"localhost", "localhost.localdomain", "metadata.google.internal"})


class ImageDistanteRefusee(GenerationError):
    """L'image distante n'a pas pu être récupérée ou n'est pas exploitable."""


@dataclass(frozen=True, slots=True)
class ImageDistante:
    """Octets d'une image distante, prêts à être embarqués."""

    octets: bytes
    mime_type: str

    @property
    def data_uri(self) -> str:
        return en_data_uri(self.octets, self.mime_type)


def url_autorisee(url: str) -> tuple[bool, str]:
    """L'URL est-elle une cible acceptable pour le serveur ?

    Returns:
        ``(True, "")`` si elle est acceptable, sinon ``(False, raison)`` avec une
        raison lisible — c'est elle qui apparaît dans l'avertissement du document.
    """
    brut = str(url or "").strip()
    if not brut:
        return False, "URL vide"
    parsee = urlparse(brut)
    if parsee.scheme.lower() not in _SCHEMAS:
        return False, f"schéma non autorisé ({parsee.scheme or 'aucun'})"
    hote = (parsee.hostname or "").strip().lower()
    if not hote:
        return False, "URL sans hôte"
    if hote in _HOTES_INTERDITS or hote.endswith(".local"):
        return False, "adresse locale refusée"
    try:
        adresses = {
            ipaddress.ip_address(info[4][0])
            for info in socket.getaddrinfo(hote, parsee.port or 443, proto=socket.IPPROTO_TCP)
        }
    except (socket.gaierror, ValueError, OSError):
        return False, "hôte introuvable"
    for adresse in adresses:
        if (
            adresse.is_private
            or adresse.is_loopback
            or adresse.is_link_local
            or adresse.is_reserved
            or adresse.is_multicast
            or adresse.is_unspecified
        ):
            return False, "adresse réseau interne refusée"
    return True, ""


def recuperer_image(url: str) -> ImageDistante:
    """Télécharge une image distante **bornée et vérifiée**.

    Le type vient de la **signature** des octets (``detecter_media``), jamais du
    ``Content-Type`` annoncé ni du nom de fichier.

    Raises:
        ImageDistanteRefusee: URL refusée, hôte injoignable, réponse non image,
            image trop lourde ou format non supporté. Le message dit lequel.
    """
    autorisee, raison = url_autorisee(url)
    if not autorisee:
        raise ImageDistanteRefusee(f"Image distante refusée ({raison}) : {url}")

    try:
        import httpx
    except ImportError as erreur:  # pragma: no cover - httpx est une dépendance du socle
        raise ImageDistanteRefusee(
            "Téléchargement d'image indisponible : 'httpx' n'est pas installé."
        ) from erreur

    try:
        with httpx.Client(
            timeout=DELAI_SECONDES, follow_redirects=True, headers={"User-Agent": "CARSO-AI"}
        ) as client:
            with client.stream("GET", url) as reponse:
                if reponse.status_code >= 400:
                    raise ImageDistanteRefusee(
                        f"Image distante inaccessible (HTTP {reponse.status_code}) : {url}"
                    )
                annonce = int(reponse.headers.get("content-length") or 0)
                if annonce > OCTETS_MAX_DISTANTE:
                    raise ImageDistanteRefusee(
                        f"Image distante trop lourde ({annonce} octets, maximum "
                        f"{OCTETS_MAX_DISTANTE}) : {url}"
                    )
                morceaux: list[bytes] = []
                total = 0
                for morceau in reponse.iter_bytes():
                    total += len(morceau)
                    if total > OCTETS_MAX_DISTANTE:
                        raise ImageDistanteRefusee(
                            f"Image distante trop lourde (plus de "
                            f"{OCTETS_MAX_DISTANTE} octets) : {url}"
                        )
                    morceaux.append(morceau)
    except ImageDistanteRefusee:
        raise
    except Exception as erreur:  # noqa: BLE001 - la cause réelle est transmise
        raise ImageDistanteRefusee(
            f"Image distante non récupérée ({type(erreur).__name__}) : {url}"
        ) from erreur

    octets = b"".join(morceaux)
    return _verifier(octets, url)


def _verifier(octets: bytes, url: str) -> ImageDistante:
    """Contrôle la signature et applique les bornes d'insertion."""
    from app.documents.images_inline import reduire
    from app.documents.validation import detecter_media

    if not octets:
        raise ImageDistanteRefusee(f"Image distante vide : {url}")
    regle = detecter_media(octets[:16])
    if regle is None or not regle.mime_type.startswith("image/"):
        raise ImageDistanteRefusee(
            f"Le lien ne renvoie pas une image (contenu non reconnu) : {url}"
        )
    reduction = reduire(octets)
    return ImageDistante(reduction.octets, str(regle.mime_type))


def en_data_uri(octets: bytes, mime_type: str = "image/png") -> str:
    """Emballe des octets en data URI (autonome, hors réseau)."""
    charge = base64.b64encode(octets).decode("ascii")
    return f"data:{mime_type};base64,{charge}"
