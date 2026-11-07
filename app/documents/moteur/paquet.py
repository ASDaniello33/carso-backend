"""Paquet DOCX : lecture, parties XML et réécriture fidèle (INC-128).

Un ``.docx`` est une archive ZIP de parties XML et de ressources binaires.
Réenregistrer un tel fichier par une bibliothèque de haut niveau réécrit
``styles.xml`` et perd des extensions propres à Word : c'est précisément la
fidélité qu'on cherche à tenir qui se casse. Ce module est donc la seule porte
d'entrée du paquet :

- **lecture** : toutes les parties sont conservées en mémoire, dans l'ordre de
  l'archive, avec leurs métadonnées ZIP d'origine ;
- **réécriture** : seules les parties fournies sont remplacées ; toutes les
  autres sont réinsérées **octet pour octet** (vérifié par test, pas supposé) ;
- **sécurité** : archive refusée si elle n'est pas un DOCX, si un chemin
  d'archive est suspect (``..``, absolu, antislash), trop d'entrées ou un volume
  décompressé déraisonnable (protection ZIP bomb).

Aucune règle métier ici : uniquement du paquet.
"""

from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.core.errors import GenerationError, ValidationError

__all__ = [
    "PARTIE_DOCUMENT",
    "Paquet",
    "ecrire",
    "est_partie_texte",
    "lire",
    "serialiser",
    "xml",
    "zones_texte",
]

PARTIE_DOCUMENT = "word/document.xml"

#: Plafonds de sécurité : un modèle professionnel reste très loin en dessous.
_MAX_ENTREES = 2000
_MAX_OCTETS_DECOMPRESSES = 100 * 1024 * 1024

_NOMS_INTERDITS = re.compile(r"(^/)|(^[a-zA-Z]:)|(\\)|(^|/)\.\.(/|$)")

_ZONE_ENTETE = re.compile(r"^word/header\d*\.xml$")
_ZONE_PIED = re.compile(r"^word/footer\d*\.xml$")


def est_partie_texte(nom: str) -> bool:
    """Cette partie porte-t-elle du contenu visible du document ?

    Corps, en-têtes et pieds. ``styles.xml``/``numbering.xml`` sont lus pour
    comprendre la mise en forme, jamais modifiés par l'instrumentation.
    """
    return (
        nom == PARTIE_DOCUMENT
        or bool(_ZONE_ENTETE.match(nom))
        or bool(_ZONE_PIED.match(nom))
    )


def _numero_partie(nom: str) -> int:
    """Ordre stable des en-têtes/pieds (``header2.xml`` avant ``header10.xml``)."""
    chiffres = re.sub(r"\D", "", Path(nom).stem)
    return int(chiffres) if chiffres else 0


def zones_texte(paquet: Paquet) -> list[str]:
    """Parties de contenu, dans un ordre déterministe : corps, puis zones."""
    entetes = sorted(
        (nom for nom in paquet.parties if _ZONE_ENTETE.match(nom)), key=_numero_partie
    )
    pieds = sorted(
        (nom for nom in paquet.parties if _ZONE_PIED.match(nom)), key=_numero_partie
    )
    zones: list[str] = []
    if PARTIE_DOCUMENT in paquet.parties:
        zones.append(PARTIE_DOCUMENT)
    return zones + entetes + pieds


@dataclass
class Paquet:
    """Contenu d'un DOCX : parties décompressées + métadonnées ZIP d'origine."""

    parties: dict[str, bytes] = field(default_factory=dict)
    infos: dict[str, zipfile.ZipInfo] = field(default_factory=dict)

    def contient(self, nom: str) -> bool:
        return nom in self.parties

    def partie(self, nom: str) -> bytes:
        """Octets d'une partie.

        Raises:
            ValidationError: partie absente du paquet.
        """
        try:
            return self.parties[nom]
        except KeyError as exc:
            raise ValidationError(
                f"Partie absente du DOCX : {nom!r}",
                details={"parties": sorted(self.parties)[:60]},
            ) from exc

    def nb_images(self) -> int:
        """Nombre de ressources graphiques du paquet (``word/media/*``)."""
        return sum(1 for nom in self.parties if nom.startswith("word/media/"))


def lire(source: Path | str | bytes) -> Paquet:
    """Lit un DOCX en mémoire, sans jamais le modifier.

    Args:
        source: chemin du fichier ou contenu binaire déjà chargé.

    Raises:
        ValidationError: archive illisible, sans ``word/document.xml``, ou dont
            les entrées ne respectent pas les garde-fous de sécurité.
    """
    octets = source if isinstance(source, bytes) else Path(source).read_bytes()
    try:
        with zipfile.ZipFile(io.BytesIO(octets)) as archive:
            infos_brutes = archive.infolist()
            if len(infos_brutes) > _MAX_ENTREES:
                raise ValidationError(
                    "DOCX refusé : trop d'entrées dans l'archive",
                    details={"entrees": len(infos_brutes), "plafond": _MAX_ENTREES},
                )
            total = 0
            parties: dict[str, bytes] = {}
            infos: dict[str, zipfile.ZipInfo] = {}
            for info in infos_brutes:
                nom = info.filename
                if _NOMS_INTERDITS.search(nom):
                    raise ValidationError(
                        "DOCX refusé : chemin d'archive suspect",
                        details={"entree": nom},
                    )
                total += info.file_size
                if total > _MAX_OCTETS_DECOMPRESSES:
                    raise ValidationError(
                        "DOCX refusé : volume décompressé excessif",
                        details={"plafond_octets": _MAX_OCTETS_DECOMPRESSES},
                    )
                if info.is_dir():
                    continue
                parties[nom] = archive.read(info)
                infos[nom] = info
    except zipfile.BadZipFile as exc:
        raise ValidationError(
            "Fichier DOCX illisible : ce n'est pas une archive ZIP valide"
        ) from exc

    if PARTIE_DOCUMENT not in parties:
        raise ValidationError(
            "Archive sans word/document.xml : ce fichier n'est pas un DOCX Word"
        )
    return Paquet(parties=parties, infos=infos)


def xml(paquet: Paquet, nom: str) -> Any:
    """Élément lxml racine d'une partie XML.

    Raises:
        ValidationError: XML mal formé (le document est refusé, jamais réparé en
            silence).
    """
    from lxml import etree

    contenu = paquet.partie(nom)
    try:
        return etree.fromstring(contenu)
    except etree.XMLSyntaxError as exc:
        raise ValidationError(
            f"XML illisible dans {nom} : le document est mal formé",
            details={"erreur": str(exc)[:300]},
        ) from exc


def serialiser(element: Any) -> bytes:
    """Sérialise un élément lxml comme partie XML de paquet Office."""
    from lxml import etree

    return etree.tostring(
        element, xml_declaration=True, encoding="UTF-8", standalone=True
    )


def ecrire(
    paquet: Paquet,
    remplacements: dict[str, bytes],
    ajouts: dict[str, bytes] | None = None,
) -> bytes:
    """Réécrit le paquet en ne remplaçant que les parties fournies.

    Les autres parties sont réinsérées avec leurs octets d'origine : aucune
    normalisation, aucun passage par une bibliothèque DOCX.

    Args:
        paquet: paquet lu par :func:`lire`.
        remplacements: ``{nom de partie: nouveaux octets}``. Un nom inconnu est
            refusé (on ne remplace pas une partie qu'on n'a pas lue).
        ajouts: ``{nom de partie: octets}`` de **nouvelles** parties (médias
            insérés par l'édition, par exemple). Un nom déjà présent est
            refusé : écraser une partie existante passe par ``remplacements``.

    Raises:
        ValidationError: remplacement d'une partie absente, ou ajout d'une
            partie déjà présente / hors du dossier ``word/``.
        GenerationError: échec d'écriture de l'archive.
    """
    inconnues = sorted(set(remplacements) - set(paquet.parties))
    if inconnues:
        raise ValidationError(
            "Remplacement d'une partie absente du DOCX",
            details={"parties": inconnues},
        )
    nouvelles = dict(ajouts or {})
    deja_presentes = sorted(set(nouvelles) & set(paquet.parties))
    if deja_presentes:
        raise ValidationError(
            "Ajout d'une partie déjà présente dans le DOCX",
            details={"parties": deja_presentes},
        )
    suspectes = sorted(nom for nom in nouvelles if not nom.startswith("word/"))
    if suspectes:
        raise ValidationError(
            "Ajout d'une partie hors du dossier word/",
            details={"parties": suspectes},
        )

    tampon = io.BytesIO()
    try:
        with zipfile.ZipFile(tampon, "w") as archive:
            for nom, contenu in paquet.parties.items():
                info = paquet.infos.get(nom)
                donnees = remplacements.get(nom, contenu)
                if info is None:  # pragma: no cover - lecture garantit l'info
                    archive.writestr(nom, donnees, compress_type=zipfile.ZIP_DEFLATED)
                    continue
                copie = zipfile.ZipInfo(filename=nom, date_time=info.date_time)
                copie.compress_type = info.compress_type
                copie.external_attr = info.external_attr
                archive.writestr(copie, donnees)
            for nom in sorted(nouvelles):
                archive.writestr(nom, nouvelles[nom], compress_type=zipfile.ZIP_DEFLATED)
    except (OSError, zipfile.BadZipFile) as exc:  # pragma: no cover - disque plein
        raise GenerationError("Échec d'écriture du paquet DOCX") from exc
    return tampon.getvalue()
