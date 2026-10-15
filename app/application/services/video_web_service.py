"""Lecture d'une vidéo du web—ses résolutions, et le fichier relayé au client.

Deux responsabilités, deux classes :

- ``VideoWebService`` **lit** ce que la page propose et rend les adresses des
  pistes, chacune signée pour le relais. Aucune écriture—ni fichier, ni
  document, ni ligne en base—et aucun muxage ;
- ``RelaisVideoService`` **retransmet** un fichier signé vers le navigateur du
  client, en flux, sans le conserver. C'est ce qui rend le clic « Télécharger »
  effectif—les plateformes n'autorisent pas le navigateur à lire leurs fichiers
  directement (mesuré le 09/10/2026—ni CORS pour notre origine, ni
  ``Content-Disposition``). Quand la résolution a **deux** pistes (vidéo et son),
  il les **assemble** au passage (``app.documents.muxage_video``)—un seul
  fichier, lisible avec le son, toujours sans rien écrire.

Le geste est humain—l'utilisateur voit un résultat de recherche vidéo et
demande les liens. Aucun agent ne déclenche ces appels, ce qui garde sous
contrôle l'adresse qui sera effectivement interrogée et téléchargée.

**Réparation d'un refus de la source** (09/10/2026)—les adresses des
plateformes expirent, et un clic sur un lien encore affiché tombe alors sur un
``403`` (ou ``410``). Le jeton signé porte la page, la hauteur et le genre de la
piste ; ``RelaisVideoService`` réanalyse donc la page **une seule fois** et
reprend le fichier avec l'adresse fraîche, plutôt que de rendre l'erreur à
l'utilisateur. Rien d'autre ne change—même fichier, même résolution, et si la
piste n'existe plus, l'erreur d'origine est rendue telle quelle.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import replace

import httpx

from app.core.config import get_settings
from app.documents.muxage_video import muxage_disponible, ouvrir_muxage
from app.documents.relais_video import (
    CibleRelais,
    FluxRelaye,
    SourceVideoIndisponible,
    ouvrir_flux_relaye,
    signer_jeton,
    sonder_sources_disponibles,
    verifier_jeton,
)
from app.documents.telechargement import url_publique_verifiee
from app.documents.video_web import ResolutionFlux, VideoFlux, extraire_flux_video

__all__ = ["RelaisVideoService", "VideoWebService"]

_logger = logging.getLogger(__name__)


class VideoWebService:
    """Résolutions d'une vidéo du web et adresses directes — **lecture seule**."""

    def __init__(self) -> None:
        self._reglages = get_settings()

    def analyser(self, url: str) -> VideoFlux:
        """Ce que la page propose réellement pour cette adresse.

        L'adresse passe d'abord la garde anti-SSRF (schéma http(s), sans
        identifiants, hôte dont **toutes** les adresses sont publiques) : CARSO
        interroge une page du web, jamais le réseau interne.

        Raises:
            ValidationError: adresse refusée, extracteur absent ou en échec, délai
                dépassé, ou aucune résolution vidéo exploitable.
        """
        return extraire_flux_video(
            url_publique_verifiee(url),
            timeout=self._reglages.video_analyse_timeout_seconds,
        )

    def signer_lien(self, cible: CibleRelais) -> str:
        """Signe la cible d'un fichier pour le relais de téléchargement.

        Le jeton porte l'adresse, son expiration **et** de quoi retrouver la
        piste (page, hauteur, genre) si la source refuse l'adresse plus tard. Il
        ne vaut que pour ce fichier, et seulement quelques minutes : c'est ce qui
        empêche le relais de servir de proxy ouvert.
        """
        return signer_jeton(cible, settings=self._reglages)

    def signer_complet(self, resolution: ResolutionFlux, *, page: str) -> str | None:
        """Lien signé d'**un** MP4 vidéo + son, ou ``None`` si c'est impossible.

        Trois conditions, toutes vérifiables : la résolution a deux pistes
        distinctes (et non un flux déjà complet), la vidéo **et** le son sont des
        fichiers, et le serveur sait assembler (ffmpeg présent). Sinon rien n'est
        signé — l'écran propose les deux fichiers et le dit, plutôt qu'un lien qui
        échouerait.
        """
        if resolution.combine is not None or resolution.video is None or resolution.audio is None:
            return None
        video, son = resolution.video, resolution.audio
        if not (
            video.url
            and video.telechargeable_directement
            and son.url
            and son.telechargeable_directement
        ):
            return None
        if not muxage_disponible(self._reglages):
            return None
        return signer_jeton(
            CibleRelais(
                url=video.url,
                audio_url=son.url,
                page=page,
                hauteur=resolution.hauteur,
                piste="video",
            ),
            settings=self._reglages,
        )


class RelaisVideoService:
    """Retransmet un fichier vidéo signé vers le navigateur — **rien n'est gardé**.

    Aucun octet n'est écrit : ni fichier de travail, ni document, ni ligne en
    base. Le serveur est un tuyau, pas un entrepôt.
    """

    def __init__(
        self,
        *,
        client: httpx.Client | None = None,
        analyse: Callable[[str], VideoFlux] | None = None,
    ) -> None:
        self._reglages = get_settings()
        self._client = client
        #: Analyse réutilisée pour la reprise : injectable pour être testée sans
        #: réseau (le service de lecture est sinon construit à l'identique).
        self._analyse = analyse or VideoWebService().analyser

    def ouvrir(self, jeton: str, *, nom: str | None = None) -> FluxRelaye:
        """Vérifie le jeton signé, ouvre le flux distant, et **répare** si besoin.

        Une adresse refusée par la source (``403``/``410`` : elle a expiré)
        déclenche une réanalyse de la page et une seconde tentative, **une seule**,
        sur la même piste. Tout autre refus est rendu tel quel.

        Raises:
            ValidationError: jeton invalide ou expiré, adresse signée refusée
                par la garde anti-SSRF, ou source qui refuse le fichier (y
                compris après la reprise).
        """
        cible = verifier_jeton(jeton, settings=self._reglages)
        try:
            return self._servir(cible, nom)
        except SourceVideoIndisponible as refus:
            if not (refus.recuperable and cible.reparables):
                raise
            fraiche = self._cible_fraiche(cible)
            if fraiche is None:
                raise
            _logger.info(
                "Relais vidéo : adresse refusée (%s) — reprise sur %sp %s de %s",
                refus.statut,
                cible.hauteur,
                cible.piste,
                cible.page,
            )
            return self._servir(fraiche, nom)

    def _servir(self, cible: CibleRelais, nom: str | None) -> FluxRelaye:
        """Sert le fichier de la cible — assemblé, ou tel quel.

        Deux pistes (vidéo + son) ⇒ l'assemblage à la volée fait **un** fichier
        lisible. Les deux adresses sont sondées avant, pour qu'un refus de la
        source (403/410) soit un refus net — et déclenche la reprise — plutôt
        qu'un assemblage vide découvert après l'envoi des en-têtes.
        """
        if cible.audio_url:
            sonder_sources_disponibles(
                cible.url,
                cible.audio_url,
                timeout=self._reglages.video_analyse_timeout_seconds,
                client=self._client,
            )
            return ouvrir_muxage(
                cible.url,
                cible.audio_url,
                nom=nom,
                reglages=self._reglages,
            )
        return ouvrir_flux_relaye(
            cible.url,
            nom=nom,
            timeout=self._reglages.video_analyse_timeout_seconds,
            client=self._client,
        )

    def _cible_fraiche(self, cible: CibleRelais) -> CibleRelais | None:
        """Réanalyse la page et rend la cible **de la même piste**, ou ``None``.

        Les deux adresses sont rafraîchies ensemble quand le fichier est assemblé :
        reprendre l'une avec l'autre périmée ne donnerait rien.

        Une réanalyse impossible (page injoignable, source qui a changé d'avis)
        n'efface pas le refus d'origine : il est journalisé, et c'est l'erreur de
        la source que l'utilisateur lit.
        """
        if not cible.page or cible.hauteur is None or not cible.piste:
            return None
        try:
            flux = self._analyse(cible.page)
        except Exception as exc:  # noqa: BLE001 - le refus d'origine reste la vérité
            _logger.warning("Reprise du relais impossible pour %s : %s", cible.page, exc)
            return None
        piste = flux.piste_pour(cible.hauteur, cible.piste)
        if piste is None or not piste.url:
            _logger.warning(
                "Reprise du relais : la piste %sp %s n'existe plus sur %s",
                cible.hauteur,
                cible.piste,
                cible.page,
            )
            return None
        fraiche = replace(cible, url=piste.url)
        if not cible.audio_url:
            return fraiche
        son = flux.piste_pour(cible.hauteur, "audio")
        if son is None or not son.url:
            # La piste vidéo existe encore, le son non : on ne sert pas une vidéo
            # muette à la place de ce qui a été demandé.
            _logger.warning(
                "Reprise du relais : la piste son %sp n'existe plus sur %s",
                cible.hauteur,
                cible.page,
            )
            return None
        return replace(fraiche, audio_url=son.url)
