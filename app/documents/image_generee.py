"""Image « générée » par un agent, prête à être déposée (INC-135).

Un agent qui produit une image a deux voies, selon ce que son modèle sait
émettre :

1. **l'image réelle** — le modèle rend des octets encodés en base64 (PNG, JPEG,
   GIF ou WebP). C'est un fichier binaire : rien ne doit être cru sur parole, la
   **signature** décide du type, jamais le nom ni un ``Content-Type`` ;
2. **l'image dessinée** — le modèle, qui n'émet que du texte, décrit l'image en
   SVG. Le serveur nettoie ce SVG (mêmes règles que tout SVG d'agent) puis le
   rasterise en PNG : l'utilisateur reçoit une image, pas du balisage.

Les deux chemins convergent ici pour appliquer **les mêmes bornes** que
l'insertion d'image dans un document (``app.documents.images_inline``) : une
image générée n'a aucune raison d'être plus lourde que ce qu'un livrable
accepterait, et la réduction est signalée à l'agent.

Rien n'est écrit sur le disque : le module rend les octets à déposer, la fiche
documentaire est créée par le service documentaire (une transaction, un statut).
"""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass

from app.core.errors import GenerationError
from app.core.errors import ValidationError as BusinessValidationError
from app.documents.images_inline import Reduction, reduire
from app.documents.validation import detecter_media

__all__ = [
    "EXTENSIONS_IMAGE",
    "OCTETS_MAX_IMAGE",
    "ImageGeneree",
    "depuis_base64",
    "depuis_svg",
    "verifier_octets",
]

#: Plafond du fichier fourni par le modèle : au-delà, l'appel est refusé plutôt
#: que de faire transiter des dizaines de mégaoctets dans une conversation.
OCTETS_MAX_IMAGE = 8 * 1024 * 1024

#: MIME que ce module sait réellement relire (Pillow les décode tous).
_MIMES_IMAGE = frozenset({"image/png", "image/jpeg", "image/gif", "image/webp"})

#: Extensions des formats acceptés (vocabulaire du stockage documentaire).
EXTENSIONS_IMAGE = frozenset({"png", "jpg", "gif", "webp"})

#: Échelle de rasterisation d'un SVG : 2.0 donne un rendu net à l'impression
#: (même valeur que le moteur documentaire).
_ECHELLE_SVG = 2.0


@dataclass(frozen=True, slots=True)
class ImageGeneree:
    """Octets à déposer et ce qu'il faut dire de leur préparation."""

    octets: bytes
    #: Extension réelle du fichier produit (``png``, ``jpg``, ``gif``, ``webp``).
    extension: str
    largeur: int
    hauteur: int
    #: Phrases à joindre au retour d'outil (réduction, rasterisation).
    avertissements: tuple[str, ...]
    #: SVG **assaini** réellement rasterisé (``depuis_svg`` seulement) : c'est
    #: lui que contrôle le diagnostic de design (INC-136).
    source_svg: str | None = None
    #: Taille du rendu **avant** réduction éventuelle : le contrôle des textes
    #: doit se faire dans les coordonnées du SVG, pas dans celles du PNG réduit.
    rendu: tuple[int, int] | None = None

    @property
    def octets_finis(self) -> int:
        """Poids du fichier déposé (octets)."""
        return len(self.octets)


def depuis_base64(charge: str, *, designation: str = "Image") -> ImageGeneree:
    """Prépare une image **réelle** fournie par le modèle en base64.

    Args:
        charge: contenu base64, avec ou sans préfixe ``data:image/...;base64,``.
        designation: libellé repris dans les phrases d'avertissement.

    Returns:
        L'image (bornée si nécessaire) prête au dépôt.

    Raises:
        ValidationError: charge vide, base64 invalide, contenu qui n'est pas une
            image reconnue, ou fichier au-delà de ``OCTETS_MAX_IMAGE``.
    """
    texte = "".join(str(charge or "").split())
    if texte[:5].lower() == "data:":
        texte = texte.partition(",")[2]
    if not texte:
        raise BusinessValidationError(
            "Image vide : fournir le contenu de l'image dans 'base64'."
        )
    try:
        octets = base64.b64decode(texte, validate=True)
    except (binascii.Error, ValueError) as erreur:
        raise BusinessValidationError(
            "Encodage base64 invalide : fournir les octets de l'image encodés en "
            "base64, sans commentaire ni texte autour.",
            details={"longueur": len(texte)},
        ) from erreur
    return verifier_octets(octets, designation=designation)


def verifier_octets(octets: bytes, *, designation: str = "Image") -> ImageGeneree:
    """Contrôle la signature d'une image et applique les bornes d'insertion.

    Le type vient de la **signature** des octets (``detecter_media``) : un PNG
    nommé ``.jpg`` n'est pas un JPEG, et une vidéo n'est pas une image.

    Raises:
        ValidationError: contenu vide, trop lourd, non reconnu comme image ou
            illisible par Pillow.
    """
    if not octets:
        raise BusinessValidationError("Image vide : aucun octet à déposer.")
    if len(octets) > OCTETS_MAX_IMAGE:
        raise BusinessValidationError(
            f"Image trop lourde : {len(octets)} octets (maximum "
            f"{OCTETS_MAX_IMAGE}). Réduire l'image avant de l'envoyer.",
            details={"octets": len(octets), "octets_max": OCTETS_MAX_IMAGE},
        )
    regle = detecter_media(octets[:16])
    if regle is None or regle.mime_type not in _MIMES_IMAGE:
        raise BusinessValidationError(
            "Le contenu fourni n'est pas une image reconnue : seuls PNG, JPEG, "
            "GIF et WebP sont acceptés, reconnus à leur signature binaire.",
            details={
                "mime_detecte": regle.mime_type if regle is not None else None,
                "formats_acceptes": sorted(_MIMES_IMAGE),
            },
        )
    reduction = reduire(octets)
    return _depuis_reduction(
        reduction, regle.extension, designation=designation
    )


def depuis_svg(
    svg: str, *, designation: str = "Image", echelle: float = _ECHELLE_SVG
) -> ImageGeneree:
    """Rasterise un SVG **écrit par le modèle** en PNG prêt au dépôt.

    Le nettoyage est celui de tous les SVG d'agent (``assainir_svg``) : balises
    actives retirées, références distantes supprimées, ``style`` filtré. Un SVG
    refusé par le moteur ou une dépendance de rasterisation absente échoue avec
    un diagnostic explicite — jamais une image vide.

    Raises:
        ValidationError: aucun ``<svg>`` dans le contenu fourni.
        GenerationError: rasterisation indisponible (``resvg-py`` absent) ou SVG
            que le moteur refuse.
    """
    from app.documents.figures import png_depuis_svg
    from app.documents.professionnel.html_builder import assainir_svg

    propre = assainir_svg(svg)
    try:
        octets, largeur, hauteur = png_depuis_svg(propre, echelle=echelle)
    except GenerationError:
        raise
    reduction = reduire(octets)
    image = _depuis_reduction(reduction, "png", designation=designation)
    # La rasterisation est une transformation : un schéma fourni en SVG devient
    # un PNG, et l'agent doit le savoir avant de parler de « vecteur ».
    avertissements = (
        *image.avertissements,
        f"{designation} rasterisée depuis un SVG fourni "
        f"({largeur}×{hauteur} px à l'échelle {echelle:g}).",
    )
    return ImageGeneree(
        octets=image.octets,
        extension=image.extension,
        largeur=image.largeur or largeur,
        hauteur=image.hauteur or hauteur,
        avertissements=avertissements,
        source_svg=propre,
        rendu=(largeur, hauteur),
    )


def _depuis_reduction(
    reduction: Reduction, extension_repli: str, *, designation: str
) -> ImageGeneree:
    """Traduit une ``Reduction`` en image prête au dépôt.

    ``Reduction.format`` vient de Pillow ; sans Pillow (installation réduite) la
    réduction est intacte et le format retombe sur celui de la signature.
    """
    extension = (reduction.format or extension_repli).lower()
    if extension not in EXTENSIONS_IMAGE:
        extension = extension_repli
    phrase = reduction.phrase(designation)
    return ImageGeneree(
        octets=reduction.octets,
        extension=extension,
        largeur=reduction.largeur,
        hauteur=reduction.hauteur,
        avertissements=(phrase,) if phrase else (),
    )
