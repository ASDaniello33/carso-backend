"""Réduction d'une image inline avant insertion dans un document (INC-134).

Une image arrive souvent à la résolution de l'appareil — une photo de 4000 px ou
une capture de 6 Mio — alors qu'elle sera imprimée sur 80 à 160 mm. Insérée
telle quelle, elle alourdit le DOCX/PDF sans gagner un détail **visible** : à
cette largeur, 1600 px valent déjà ≈ 250 dpi.

Ce module borne donc la charge utile **avant** l'insertion :

- plus grand côté ≤ ``COTE_MAX_INLINE`` (1600 px) ;
- octets ≤ ``OCTETS_CIBLE`` (1 Mio) ;
- **fidélité** — un JPEG reste un JPEG (qualité dégressive, jamais sous 70) ;
  PNG, GIF statique et WebP sortent en PNG, sans perte de netteté sur le texte
  et les traits d'un schéma ;
- **animation préservée** — un GIF/WebP animé n'est jamais réduit : redimensionner
  une seule image détruirait les suivantes ;
- **jamais plus lourd** — une réduction qui produit plus d'octets est refusée et
  l'original conservé.

Toute réduction réelle est **décrite** (``Reduction.phrase()``) pour que l'agent
la signale à l'utilisateur : une image silencieusement dégradée serait une perte
de donnée (AGENTS.md §2.2).

Le module ne écrit rien sur le disque : l'image réduite vit en mémoire et
remplace la charge base64 du spec ou de la data-URI HTML.
"""

from __future__ import annotations

import base64
import binascii
import io
import re
from dataclasses import dataclass
from typing import Any, Final

from app.core.errors import ValidationError

__all__ = [
    "COTE_MAX_INLINE",
    "OCTETS_CIBLE",
    "Reduction",
    "reduire",
    "reduire_blocs",
    "reduire_data_uri",
]

#: Plus grand côté conservé. À 160 mm de large, 1600 px font ≈ 250 dpi : au-delà,
#: le détail existe dans le fichier mais reste invisible à l'impression.
COTE_MAX_INLINE: Final = 1_600

#: Poids visé d'une image insérée (1 Mio). Une photo de document à 1600 px tient
#: largement dedans ; au-delà, on recompresse puis on redescend en dimension.
OCTETS_CIBLE: Final = 1_048_576

#: Plancher de dimension : une image plus petite deviendrait illisible pour tenir
#: un poids — on préfère un fichier un peu lourd à une figure qu'on ne lit plus.
_COTE_PLANCHER: Final = 640

#: Tentatives d'encodage : compression plus forte, puis paliers de dimension
#: jusqu'à ``_COTE_PLANCHER`` (une image qui ne tient pas le poids visé s'arrête
#: là plutôt que de devenir illisible).
_PASSES: Final = 6
_FACTEUR_PASSE: Final = 0.8

#: Qualités JPEG essayées d'une passe à l'autre (le dernier palier reste lisible).
_QUALITES_JPEG: Final = (85, 78, 70)

_FORMATS_JPEG: Final = ("jpg", "jpeg")

#: Data-URI d'image dans un corps HTML (la charge est la seule partie réécrite).
_RE_DATA_URI: Final = re.compile(
    r"data:image/(?:png|jpe?g|gif|webp);base64,(?P<charge>[A-Za-z0-9+/=\s]+)",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class Reduction:
    """Octets à insérer et trace de ce qui a changé (aucune modification : intacte)."""

    octets: bytes
    format: str
    largeur: int
    hauteur: int
    octets_source: int
    largeur_source: int
    hauteur_source: int
    modifiee: bool

    def phrase(self, designation: str = "Image") -> str:
        """Phrase lisible décrivant la transformation (vide si intacte)."""
        if not self.modifiee:
            return ""
        avant, apres = _poids(self.octets_source), _poids(len(self.octets))
        if (self.largeur, self.hauteur) == (
            self.largeur_source,
            self.hauteur_source,
        ):
            return (
                f"{designation} recompressée de {avant} à {apres} "
                "(dimensions conservées)."
            )
        return (
            f"{designation} réduite de {self.largeur_source}×{self.hauteur_source} px "
            f"({avant}) à {self.largeur}×{self.hauteur} px ({apres})."
        )


def reduire(octets: bytes) -> Reduction:
    """Réduit une image inline si elle dépasse les bornes d'insertion.

    Args:
        octets: contenu de l'image (format détecté par son contenu, jamais par un
            nom de fichier).

    Returns:
        Les octets **à insérer** : l'original intact quand rien ne dépasse ou que
        la réduction ne gagnerait rien, sinon la version réduite.

    Raises:
        ValidationError: contenu vide ou illisible par Pillow.
    """
    if not octets:
        raise ValidationError("Image vide : aucun contenu à réduire")
    try:
        from PIL import Image
    except ImportError:  # pragma: no cover - dépend de l'installation
        # Sans Pillow, aucune mesure : l'image est insérée telle quelle plutôt
        # que de faire échouer une génération qui fonctionnait.
        return _intacte(octets, "", 0, 0)

    try:
        with Image.open(io.BytesIO(octets)) as source:
            source.load()
            format_source = _format_de(source.format)
            largeur, hauteur = source.size
            if getattr(source, "n_frames", 1) > 1:
                # Animation (GIF/WebP) : une image isolée n'a plus de sens.
                return _intacte(octets, format_source, largeur, hauteur)
            if (
                max(largeur, hauteur) <= COTE_MAX_INLINE
                and len(octets) <= OCTETS_CIBLE
            ):
                return _intacte(octets, format_source, largeur, hauteur)
            travail = source.copy()
    except Exception as erreur:  # noqa: BLE001 - fichier corrompu, format exotique
        raise ValidationError(
            "Image illisible : réduction impossible",
            details={"octets": len(octets)},
        ) from erreur

    donnees, extension = octets, format_source
    for passe in range(_PASSES):
        if max(travail.size) > COTE_MAX_INLINE:
            travail.thumbnail((COTE_MAX_INLINE, COTE_MAX_INLINE))
        qualite = _QUALITES_JPEG[min(passe, len(_QUALITES_JPEG) - 1)]
        donnees, extension = _encoder(travail, format_source, qualite)
        if len(donnees) <= OCTETS_CIBLE:
            break
        largeur_courante, hauteur_courante = travail.size
        suite = (
            max(_COTE_PLANCHER, int(largeur_courante * _FACTEUR_PASSE)),
            max(_COTE_PLANCHER, int(hauteur_courante * _FACTEUR_PASSE)),
        )
        if suite == travail.size:
            break
        travail = travail.resize(suite)

    reduction = Reduction(
        octets=donnees,
        format=extension,
        largeur=travail.width,
        hauteur=travail.height,
        octets_source=len(octets),
        largeur_source=largeur,
        hauteur_source=hauteur,
        modifiee=True,
    )
    if len(donnees) >= len(octets):
        # Réduire qui alourdit n'est pas une réduction : l'original fait foi.
        return _intacte(octets, format_source, largeur, hauteur)
    return reduction


def reduire_blocs(blocs: Any) -> tuple[list[Any], list[str]]:
    """Réduit les images inline d'une liste de **blocs bruts** (avant validation).

    Appelé par les tools de génération sur l'entrée du modèle : la réduction a
    lieu une seule fois, au point où l'agent fournit ses données, et le retour
    d'outil peut donc dire ce qui a été transformé. Un bloc illisible est laissé
    tel quel — la validation du ``DocumentSpec`` en donne la raison précise.

    Returns:
        Les blocs (copies des seuls blocs modifiés) et les phrases de réduction.
    """
    if not isinstance(blocs, list):
        return [], []
    phrases: list[str] = []
    sortie: list[Any] = []
    for bloc in blocs:
        if not (
            isinstance(bloc, dict)
            and bloc.get("type") == "image"
            and isinstance(bloc.get("base64"), str)
        ):
            sortie.append(bloc)
            continue
        charge = "".join(bloc["base64"].split())
        if charge[:5].lower() == "data:":
            charge = charge.partition(",")[2]
        try:
            reduction = reduire(base64.b64decode(charge, validate=True))
        except (binascii.Error, ValueError, ValidationError):
            sortie.append(bloc)
            continue
        if not reduction.modifiee:
            sortie.append(bloc)
            continue
        phrases.append(reduction.phrase(_designation(bloc)))
        sortie.append(
            {
                **bloc,
                "base64": base64.b64encode(reduction.octets).decode("ascii"),
            }
        )
    return sortie, phrases


def reduire_data_uri(html: str) -> tuple[str, list[str]]:
    """Réduit les images ``data:`` d'un corps HTML, en signalant les réductions.

    Seule la charge base64 est réécrite : le reste du HTML sort **identique**
    (aucune re-sérialisation, donc aucun risque d'abîmer un markup travaillé).
    Une data-URI illisible est laissée telle quelle — la validation du corps la
    signalera.

    Returns:
        Le HTML (réécrit ou inchangé) et les phrases de réduction, dans l'ordre
        d'apparition des images.
    """
    phrases: list[str] = []

    def _remplacer(match: re.Match[str]) -> str:
        charge = "".join(match.group("charge").split())
        try:
            octets = base64.b64decode(charge, validate=True)
            reduction = reduire(octets)
        except (binascii.Error, ValueError, ValidationError):
            return match.group(0)
        if not reduction.modifiee:
            return match.group(0)
        phrases.append(reduction.phrase(f"Image inline {len(phrases) + 1}"))
        mime = "jpeg" if reduction.format == "jpg" else reduction.format
        encodee = base64.b64encode(reduction.octets).decode("ascii")
        return f"data:image/{mime};base64,{encodee}"

    return _RE_DATA_URI.sub(_remplacer, html), phrases


def _designation(bloc: dict[str, Any]) -> str:
    """Désignation lisible d'un bloc image (légende, sinon texte alternatif)."""
    for cle, forme in (("legende", "Image « {} »"), ("alt", "Image ({})")):
        valeur = bloc.get(cle)
        if isinstance(valeur, str) and valeur.strip():
            return forme.format(valeur.strip())
    return "Image"


def _encoder(image: Any, format_source: str, qualite: int) -> tuple[bytes, str]:
    """Encode l'image sans changer sa nature (JPEG reste JPEG, le reste en PNG)."""
    sortie = io.BytesIO()
    if format_source in _FORMATS_JPEG:
        image.convert("RGB").save(
            sortie, format="JPEG", quality=qualite, optimize=True, progressive=True
        )
        return sortie.getvalue(), "jpg"
    if image.mode not in ("RGB", "RGBA", "L", "LA", "P"):
        image = image.convert("RGBA")
    image.save(sortie, format="PNG", optimize=True)
    return sortie.getvalue(), "png"


def _format_de(nom: str | None) -> str:
    """Extension normalisée depuis le format décodé par Pillow."""
    return {
        "JPEG": "jpg",
        "PNG": "png",
        "GIF": "gif",
        "WEBP": "webp",
    }.get((nom or "").upper(), "")


def _intacte(octets: bytes, format: str, largeur: int, hauteur: int) -> Reduction:
    """Réduction nulle : les octets d'origine, sans transformation."""
    return Reduction(
        octets=octets,
        format=format,
        largeur=largeur,
        hauteur=hauteur,
        octets_source=len(octets),
        largeur_source=largeur,
        hauteur_source=hauteur,
        modifiee=False,
    )


def _poids(octets: int) -> str:
    """Poids lisible en français (virgule décimale, unités binaires)."""
    if octets < 1024:
        return f"{octets} octets"
    if octets < OCTETS_CIBLE:
        return f"{octets / 1024:.0f} Kio"
    return f"{octets / (1024 * 1024):.1f} Mio".replace(".", ",")
