"""Préparation d'une image pour un modèle multimodal.

Un agent qui « lit une image » doit la **voir** : le contenu part donc vers le
modèle sous forme de bloc ``image_url`` (data-URI). Deux contraintes décident de
ce module :

- **le coût et la limite du provider** — une photo de téléphone brute coûte
  cher et dépasse parfois la taille acceptée : on borne le plus grand côté et on
  ne réencode que si nécessaire ;
- **la fidélité** — un PNG (capture, schéma, texte) reste un PNG : le convertir
  en JPEG dégraderait précisément ce qu'on veut lire. Seul un JPEG reste un
  JPEG.

Rien n'est écrit sur le disque : l'image préparée vit en mémoire, en base64,
dans le résultat du tool. Le fichier d'origine n'est jamais modifié.
"""

from __future__ import annotations

import base64
import io
from dataclasses import dataclass
from pathlib import Path

from app.core.errors import ValidationError

#: Plus grand côté conservé. Au-delà, le provider réduit de toute façon l'image
#: et facture les tuiles perdues : borner ici évite d'envoyer du détail illisible.
COTE_MAX = 1_600

#: Plafond du fichier source (un document déposé ne doit pas saturer un run).
OCTETS_MAX = 8 * 1024 * 1024

#: Formats que Pillow sait réellement décoder pour nous (l'allowlist de dépôt
#: accepte GIF et WebP aussi : ils sont convertis en PNG).
_FORMATS_SIMPLE = {"PNG", "JPEG"}


@dataclass(frozen=True, slots=True)
class ImagePreparee:
    """Image prête pour le modèle (aucun chemin disque exposé)."""

    mime_type: str
    base64: str
    largeur: int
    hauteur: int
    octets: int
    #: Vrai quand l'image a été réduite ou convertie : le modèle doit le savoir
    #: avant de lire un petit texte qui n'existe plus.
    transformee: bool


def preparer_image(chemin: Path, *, mime_type: str | None = None) -> ImagePreparee:
    """Lit une image et la rend dans une forme bornée pour un modèle multimodal.

    Args:
        chemin: fichier déjà validé et rangé par le stockage contrôlé.
        mime_type: type déterminé par le serveur au dépôt (jamais celui déclaré
            par le client) ; sert seulement de repli quand Pillow ne le donne pas.

    Raises:
        ValidationError: fichier absent, trop gros, illisible, ou Pillow absent.
    """
    octets = chemin.read_bytes()
    if not octets:
        raise ValidationError("Image vide : aucun contenu à lire")
    if len(octets) > OCTETS_MAX:
        raise ValidationError(
            f"Image trop lourde ({len(octets)} octets) : maximum {OCTETS_MAX}",
            details={"octets": len(octets), "maximum": OCTETS_MAX},
        )

    try:
        from PIL import Image
    except ImportError as erreur:  # pragma: no cover - dépend de l'installation
        raise ValidationError(
            "Lecture d'image indisponible sur ce serveur (Pillow non installé)"
        ) from erreur

    try:
        with Image.open(io.BytesIO(octets)) as image:
            image.load()
            format_source = (image.format or "").upper()
            largeur, hauteur = image.size
            if largeur <= 0 or hauteur <= 0:
                raise ValidationError("Image de dimensions invalides")

            reduite = max(largeur, hauteur) > COTE_MAX
            if reduite:
                image.thumbnail((COTE_MAX, COTE_MAX))
                largeur, hauteur = image.size

            if not reduite and format_source in _FORMATS_SIMPLE:
                # Rien à faire : les octets d'origine sont déjà la meilleure
                # version (aucune perte, aucun réencodage inutile).
                return ImagePreparee(
                    mime_type=(
                        "image/jpeg" if format_source == "JPEG" else "image/png"
                    ),
                    base64=base64.b64encode(octets).decode("ascii"),
                    largeur=largeur,
                    hauteur=hauteur,
                    octets=len(octets),
                    transformee=False,
                )

            sortie = io.BytesIO()
            if format_source == "JPEG":
                image.convert("RGB").save(sortie, format="JPEG", quality=85, optimize=True)
                mime_sortie = "image/jpeg"
            else:
                # PNG, GIF, WebP : conversion sans perte additionnelle (le texte
                # et les traits d'un schéma doivent rester nets).
                if image.mode not in ("RGB", "RGBA", "L", "LA", "P"):
                    image = image.convert("RGBA")
                image.save(sortie, format="PNG", optimize=True)
                mime_sortie = "image/png"
            donnees = sortie.getvalue()
            return ImagePreparee(
                mime_type=mime_sortie,
                base64=base64.b64encode(donnees).decode("ascii"),
                largeur=largeur,
                hauteur=hauteur,
                octets=len(donnees),
                transformee=True,
            )
    except ValidationError:
        raise
    except Exception as erreur:  # noqa: BLE001 - fichier corrompu, format exotique
        raise ValidationError(
            "Image illisible : le fichier n'est pas une image exploitable",
            details={"mime_type": mime_type or "inconnu"},
        ) from erreur


__all__ = ["COTE_MAX", "OCTETS_MAX", "ImagePreparee", "preparer_image"]
