"""Édition d'une **copie** de modèle DOCX, élément par élément (INC-129).

Le constat qui a motivé ce module : les modèles réels des organisations ne
contiennent pas de ``{{placeholder}}``. Ils contiennent du texte rédigé (titres,
paragraphes fixes, tableaux, logos) que l'agent doit **réutiliser** en le
modifiant. Recopier le fichier puis réécrire ses éléments est la seule façon de
tenir la promesse « document conforme au modèle ».

Trois opérations, volontairement séparées :

- ``indexer`` : ce que le document contient **réellement**, avec un
  ``element_id`` stable pour cette version du fichier (mêmes identifiants que
  l'analyse : ``block_001``, ``block_002``… dans l'ordre du corps, puis des
  en-têtes et des pieds) ;
- ``remplacer_texte`` : le contenu d'un paragraphe change, **sa mise en forme ne
  change pas** — ``w:pPr`` est conservé tel quel et le ``w:rPr`` du premier run
  sert de modèle aux runs réécrits ;
- ``remplacer_par_html`` : un ou plusieurs éléments du corps sont remplacés
  (ou complétés) par du HTML stylisé — titres, paragraphes, listes, tableaux,
  images ``data:`` base64, SVG rasterisé. Le HTML est converti par le moteur
  existant (``html_to_docx``) puis **transplanté** dans la copie : styles
  explicites, relations d'images recréées, numérotation matérialisée.

Règles tenues, chacune vérifiée par test :

- **seule la partie ciblée change** : ``word/document.xml`` (ou l'en-tête/le
  pied visé), ses relations, ``[Content_Types].xml`` si un média est ajouté, et
  les médias ajoutés. Tout le reste du paquet est réinséré octet pour octet ;
- **aucune image perdue** : remplacer le texte d'un paragraphe qui porte une
  image est refusé, jamais appliqué en silence ;
- **aucun chemin disque** : les images passent en ``data:`` base64, donc un
  chemin introuvable ne peut pas produire un document cassé ;
- **aucune invention** : un ``element_id`` inconnu, un tableau visé par
  ``remplacer_texte`` ou un HTML vide échouent avec un message explicite et la
  liste des éléments disponibles.
"""

from __future__ import annotations

import hashlib
import posixpath
import re
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.core.errors import ValidationError
from app.documents.moteur import analyse as analyse_docx
from app.documents.moteur import paquet as paquet_docx

__all__ = [
    "Edition",
    "ElementModele",
    "indexer",
    "remplacer_par_html",
    "remplacer_texte",
]

_W = analyse_docx._W
_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"
_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"

#: Extensions de média acceptées à l'insertion, avec leur type de contenu.
_TYPES_MEDIA = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
    "bmp": "image/bmp",
    "tif": "image/tiff",
    "tiff": "image/tiff",
}

#: Styles de liste que le convertisseur HTML pose par nom : un modèle client ne
#: les définit pas forcément, la puce/le numéro serait alors perdu en silence.
_STYLES_PUCE = re.compile(r"^list(?:e)?bullet\s*(\d)?$", re.I)
_STYLES_NUMERO = re.compile(r"^list(?:e)?number\s*(\d)?$", re.I)


def _balise(nom: str) -> str:
    return f"{{{_W}}}{nom}"


@dataclass(frozen=True)
class ElementModele:
    """Élément éditable du document, tel que l'agent le lit."""

    element_id: str
    partie: str
    type: str
    texte: str
    style: str | None = None
    niveau_titre: int | None = None
    images: int = 0
    lignes: int = 0
    colonnes: int = 0

    def resume(self) -> dict[str, Any]:
        """Forme publique (celle du tool), sans objet interne."""
        return {
            "element_id": self.element_id,
            "partie": self.partie,
            "type": self.type,
            "texte": self.texte,
            "style": self.style,
            "niveau_titre": self.niveau_titre,
            "images": self.images,
            "lignes": self.lignes,
            "colonnes": self.colonnes,
        }


@dataclass
class Edition:
    """Résultat d'une édition : les octets, et ce qui a été touché."""

    octets: bytes
    element_id: str
    partie: str
    ancien_texte: str
    blocs_inseres: int = 0
    images_ajoutees: int = 0
    parties_modifiees: list[str] = field(default_factory=list)
    parties_ajoutees: list[str] = field(default_factory=list)
    avertissements: list[str] = field(default_factory=list)


# --- index -------------------------------------------------------------------


def indexer(octets: bytes) -> list[ElementModele]:
    """Éléments du document, dans l'ordre de lecture (corps, en-têtes, pieds).

    L'ordre et la numérotation sont **ceux de l'analyse** : un ``element_id`` lu
    ici désigne le même paragraphe que dans l'analyse du modèle.
    """
    analyse = analyse_docx.analyser(octets)
    return [
        ElementModele(
            element_id=bloc.block_id,
            partie=bloc.partie,
            type=bloc.type,
            texte=bloc.texte,
            style=bloc.style.nom,
            niveau_titre=bloc.niveau_titre,
            images=bloc.images,
            lignes=len(bloc.lignes),
            colonnes=bloc.colonnes,
        )
        for bloc in analyse.blocs
    ]


# --- ciblage -----------------------------------------------------------------


def _enfants_corps(partie: str, racine: Any) -> list[Any]:
    """Enfants éditables d'une partie — même parcours que l'analyse."""
    if partie == paquet_docx.PARTIE_DOCUMENT:
        corps = racine.find(_balise("body"))
        return list(corps) if corps is not None else []
    return list(racine)


def _cibler(paquet: paquet_docx.Paquet, element_id: str) -> tuple[Any, str, Any]:
    """Élément XML visé, sa partie et la racine de cette partie.

    Raises:
        ValidationError: identifiant inconnu (analyse périmée ou inventée).
    """
    compteur = 0
    for partie in paquet_docx.zones_texte(paquet):
        racine = paquet_docx.xml(paquet, partie)
        for enfant in _enfants_corps(partie, racine):
            if enfant.tag not in (_balise("p"), _balise("tbl")):
                continue
            compteur += 1
            if f"block_{compteur:03d}" == element_id:
                return enfant, partie, racine
    connus = ", ".join(f"block_{index:03d}" for index in range(1, compteur + 1))
    raise ValidationError(
        f"Élément {element_id!r} introuvable dans le document "
        f"(identifiants disponibles : {connus or 'aucun'}). Relire la structure "
        "du document : les identifiants sont recalculés à chaque modification.",
        details={"element_id": element_id},
    )


def _texte_de(element: Any) -> str:
    """Texte visible d'un paragraphe ou d'un tableau."""
    return analyse_docx.texte_element(element).strip()


# --- remplacement de texte ---------------------------------------------------


def _segments(contenu: Any) -> list[Any]:
    """Contenu fourni → segments de texte (chaîne ou liste de segments)."""
    from app.documents.moteur import schemas as S

    if isinstance(contenu, str):
        return [S.SegmentValeur(texte=contenu)]
    if isinstance(contenu, list):
        try:
            return [S.SegmentValeur.model_validate(item) for item in contenu]
        except Exception as exc:  # noqa: BLE001 - message unique et explicite
            raise ValidationError(
                "Contenu invalide : attendu une chaîne ou une liste de segments "
                "{texte, gras, italique, souligne, couleur, taille_pt}.",
                details={"erreur": str(exc)[:200]},
            ) from exc
    raise ValidationError(
        "Contenu invalide : attendu une chaîne ou une liste de segments de texte."
    )


def remplacer_texte(octets: bytes, element_id: str, contenu: Any) -> Edition:
    """Remplace le **texte** d'un paragraphe, sans toucher à sa mise en forme.

    Le ``w:pPr`` du modèle est conservé (style, alignement, retrait,
    interligne, numérotation) et le ``w:rPr`` du premier run sert de modèle :
    la police, la taille et la couleur du client s'appliquent au nouveau texte.

    Raises:
        ValidationError: identifiant inconnu, élément qui n'est pas un
            paragraphe, ou paragraphe portant une image (la remplacer la
            ferait disparaître — utiliser l'insertion HTML avec une image).
    """
    from app.documents.moteur import rendu as rendu_docx

    paquet = paquet_docx.lire(octets)
    element, partie, racine = _cibler(paquet, element_id)
    if element.tag != _balise("p"):
        raise ValidationError(
            f"L'élément {element_id} est un tableau : remplacer son texte "
            "n'aurait pas de sens. Fournir un tableau HTML à la place.",
            details={"element_id": element_id, "type": "tableau"},
        )
    if element.find(".//" + _balise("drawing")) is not None or (
        element.find(".//" + _balise("pict")) is not None
    ):
        raise ValidationError(
            f"L'élément {element_id} contient une image : la remplacer par du "
            "texte la supprimerait. Insérer un contenu HTML avec l'image en "
            "data URI base64 pour la conserver ou la changer.",
            details={"element_id": element_id},
        )
    segments = _segments(contenu)
    ancien = _texte_de(element)
    modele_rpr = rendu_docx._rpr_modele(element)
    rendu_docx._vider(element)
    for segment in segments:
        rendu_docx._ajouter_run(element, segment.texte, modele_rpr, segment)

    return Edition(
        octets=paquet_docx.ecrire(paquet, {partie: paquet_docx.serialiser(racine)}),
        element_id=element_id,
        partie=partie,
        ancien_texte=ancien,
        parties_modifiees=[partie],
    )


# --- insertion de contenu HTML ----------------------------------------------


def _chemin_rels(partie: str) -> str:
    """Chemin de la partie ``.rels`` d'une partie de contenu."""
    dossier = Path(partie).parent.as_posix()
    nom = Path(partie).name
    return f"{dossier}/_rels/{nom}.rels" if dossier != "." else f"_rels/{nom}.rels"


def _relations(paquet: paquet_docx.Paquet, partie: str) -> tuple[Any | None, dict[str, Any]]:
    """Racine ``.rels`` d'une partie et index ``rId`` → relation."""
    nom = _chemin_rels(partie)
    if nom not in paquet.parties:
        return None, {}
    racine = paquet_docx.xml(paquet, nom)
    return racine, {rel.get("Id"): rel for rel in racine.findall(f"{{{_REL_NS}}}Relationship")}


def _cible_absolue(partie: str, cible: str) -> str:
    """Cible d'une relation résolue en nom de partie du paquet."""
    if cible.startswith("/"):
        return cible.lstrip("/")
    return posixpath.normpath(posixpath.join(str(Path(partie).parent), cible))


def _prochain_id(relations: dict[str, Any]) -> str:
    """Identifiant de relation libre (``rId12`` → ``rId13``)."""
    numeros = [0]
    for identifiant in relations:
        trouve = re.fullmatch(r"rId(\d+)", identifiant or "")
        if trouve:
            numeros.append(int(trouve.group(1)))
    return f"rId{max(numeros) + 1}"


def _media_reutilisable(paquet: paquet_docx.Paquet, contenu: bytes) -> str | None:
    """Média déjà présent dans le paquet (même empreinte), ou ``None``."""
    empreinte = hashlib.sha1(contenu).hexdigest()
    for nom, existant in paquet.parties.items():
        if nom.startswith("word/media/") and hashlib.sha1(existant).hexdigest() == empreinte:
            return nom
    return None


def _greffer_medias(
    paquet: paquet_docx.Paquet,
    partie: str,
    source: paquet_docx.Paquet,
    noeuds: list[Any],
) -> tuple[dict[str, bytes], dict[str, bytes], int]:
    """Recrée dans le paquet cible les relations référencées par les nœuds copiés.

    Les images sont ajoutées comme parties du paquet (même empreinte = même
    partie : un logo répété n'est écrit qu'une fois) et les liens externes comme
    relations ``External``.

    Returns:
        ``(parties modifiées, parties ajoutées, nombre d'images ajoutées)``.

    Raises:
        ValidationError: relation du contenu converti absente ou cible perdue.
    """
    from lxml import etree

    racine_rels, relations = _relations(paquet, partie)
    modifications: dict[str, bytes] = {}
    ajouts: dict[str, bytes] = {}
    if racine_rels is None:
        raise ValidationError(
            "Document sans fichier de relations : impossible d'ajouter une image "
            "sans produire un paquet incohérent."
        )
    #: Le contenu converti vit toujours dans ``word/document.xml`` du DOCX
    #: temporaire, même quand on édite un en-tête ou un pied.
    racine_source = None
    source_rels = _chemin_rels(paquet_docx.PARTIE_DOCUMENT)
    if source_rels in source.parties:
        racine_source = paquet_docx.xml(source, source_rels)
    if racine_source is None:
        return {}, {}, 0
    relations_source = {
        rel.get("Id"): rel for rel in racine_source.findall(f"{{{_REL_NS}}}Relationship")
    }
    images = 0
    #: Un même ``rId`` du document temporaire peut être référencé par plusieurs
    #: nœuds copiés : la correspondance garantit une seule relation par source
    #: (les identifiants des deux paquets se ressemblent — ``rId5`` existe des
    #: deux côtés — donc on ne peut pas se fier à leur valeur).
    correspondance: dict[str, str] = {}
    for noeud in noeuds:
        for reference in noeud.iter():
            for attribut, identifiant in list(reference.attrib.items()):
                if not attribut.startswith(f"{{{_R}}}"):
                    continue
                if identifiant in correspondance:
                    reference.set(attribut, correspondance[identifiant])
                    continue
                relation = relations_source.get(identifiant)
                if relation is None:
                    raise ValidationError(
                        f"Relation {identifiant!r} absente du contenu converti : "
                        "le document produit serait cassé."
                    )
                nouveau = _prochain_id(relations)
                if (relation.get("TargetMode") or "") == "External":
                    element = etree.SubElement(racine_rels, f"{{{_REL_NS}}}Relationship")
                    element.set("Id", nouveau)
                    element.set("Type", relation.get("Type") or "")
                    element.set("Target", relation.get("Target") or "")
                    element.set("TargetMode", "External")
                    relations[nouveau] = element
                    correspondance[identifiant] = nouveau
                    reference.set(attribut, nouveau)
                    continue
                cible = _cible_absolue(
                    paquet_docx.PARTIE_DOCUMENT, relation.get("Target") or ""
                )
                if cible not in source.parties:
                    raise ValidationError(
                        f"Partie {cible!r} absente du contenu converti : le "
                        "document produit serait cassé."
                    )
                contenu = source.parties[cible]
                extension = Path(cible).suffix.lstrip(".").lower()
                if extension not in _TYPES_MEDIA:
                    raise ValidationError(
                        f"Format d'image non pris en charge : {extension!r} "
                        f"(acceptés : {', '.join(sorted(_TYPES_MEDIA))})"
                    )
                nom = _media_reutilisable(paquet, contenu) or _media_reutilisable(
                    paquet_docx.Paquet(parties=ajouts), contenu
                )
                if nom is None:
                    empreinte = hashlib.sha1(contenu).hexdigest()[:16]
                    nom = f"word/media/carso_{empreinte}.{extension}"
                    ajouts[nom] = contenu
                    images += 1
                element = etree.SubElement(racine_rels, f"{{{_REL_NS}}}Relationship")
                element.set("Id", nouveau)
                element.set("Type", relation.get("Type") or "")
                element.set(
                    "Target", posixpath.relpath(nom, str(Path(partie).parent))
                )
                relations[nouveau] = element
                correspondance[identifiant] = nouveau
                reference.set(attribut, nouveau)
    if correspondance:
        modifications[_chemin_rels(partie)] = paquet_docx.serialiser(racine_rels)
    if ajouts:
        modifications.update(_declare_types_media(paquet, ajouts))
    return modifications, ajouts, images


def _declare_types_media(
    paquet: paquet_docx.Paquet, ajouts: dict[str, bytes]
) -> dict[str, bytes]:
    """Déclare les extensions des médias ajoutés dans ``[Content_Types].xml``."""
    from lxml import etree

    nom_partie = "[Content_Types].xml"
    if nom_partie not in paquet.parties:
        return {}
    extensions = {Path(nom).suffix.lstrip(".").lower() for nom in ajouts}
    racine = paquet_docx.xml(paquet, nom_partie)
    declares = {
        (defaut.get("Extension") or "").lower()
        for defaut in racine.findall(
            "{http://schemas.openxmlformats.org/package/2006/content-types}Default"
        )
    }
    manquantes = sorted(extensions - declares)
    if not manquantes:
        return {}
    ns = "http://schemas.openxmlformats.org/package/2006/content-types"
    premier_override = racine.find(f"{{{ns}}}Override")
    for extension in manquantes:
        defaut = etree.Element(f"{{{ns}}}Default")
        defaut.set("Extension", extension)
        defaut.set("ContentType", _TYPES_MEDIA[extension])
        if premier_override is None:
            racine.append(defaut)
        else:
            premier_override.addprevious(defaut)
    return {nom_partie: paquet_docx.serialiser(racine)}


def _materialiser_listes(noeuds: list[Any]) -> int:
    """Donne une puce/un numéro **explicite** aux paragraphes de liste.

    Le convertisseur HTML pose ``pStyle`` = « List Bullet »/« List Number ».
    Ces styles viennent du modèle python-docx : un modèle client ne les définit
    pas, et Word afficherait alors des paragraphes sans puce, sans erreur. On
    écrit donc le marqueur dans le texte, avec un retrait négatif — même rendu,
    aucune dépendance au fichier de styles du client.

    Returns:
        Nombre de paragraphes matérialisés.
    """
    from lxml import etree

    compteurs: dict[int, int] = {}
    materialises = 0
    for noeud in noeuds:
        if noeud.tag != _balise("p"):
            compteurs.clear()
            continue
        ppr = noeud.find(_balise("pPr"))
        style = ""
        if ppr is not None and ppr.find(_balise("pStyle")) is not None:
            style = ppr.find(_balise("pStyle")).get(_balise("val")) or ""
        nom = style.replace(" ", "")
        puce = _STYLES_PUCE.match(nom)
        numero = _STYLES_NUMERO.match(nom)
        if not puce and not numero:
            compteurs.clear()
            continue
        chiffre = (puce or numero).group(1)  # type: ignore[union-attr]
        niveau = max(0, int(chiffre) - 1) if chiffre else 0
        if puce:
            marqueur = "•"
        else:
            compteurs[niveau] = compteurs.get(niveau, 0) + 1
            marqueur = f"{compteurs[niveau]}."
        indentation = ppr.find(_balise("ind")) if ppr is not None else None
        if ppr is not None:
            if ppr.find(_balise("pStyle")) is not None:
                ppr.remove(ppr.find(_balise("pStyle")))
            if indentation is None:
                indentation = etree.SubElement(ppr, _balise("ind"))
            indentation.set(_balise("left"), str(360 * (niveau + 1)))
            indentation.set(_balise("hanging"), "360")
        run = etree.Element(_balise("r"))
        texte = etree.SubElement(run, _balise("t"))
        texte.set(_XML_SPACE, "preserve")
        texte.text = f"{marqueur}\t"
        noeud.insert(0 if ppr is None else 1, run)
        materialises += 1
    return materialises


def _html_converti(html: str) -> bytes:
    """HTML fourni → DOCX temporaire, SVG rasterisé, images en ``data:``.

    Raises:
        ValidationError: HTML vide ou conversion impossible.
    """
    from app.documents.generation_html import _svg_vers_png_data_uri
    from app.documents.html_to_docx import ConversionHtmlError, html_string_to_docx_bytes

    contenu = (html or "").strip()
    if not contenu:
        raise ValidationError("HTML vide : rien à insérer dans le modèle.")
    if "<svg" in contenu:
        contenu = _svg_vers_png_data_uri(contenu)
    try:
        return html_string_to_docx_bytes(contenu)
    except ConversionHtmlError as exc:
        raise ValidationError(
            f"HTML non convertible en contenu Word : {exc}",
            details={"html": contenu[:300]},
        ) from exc


def remplacer_par_html(
    octets: bytes,
    element_id: str,
    html: str,
    *,
    position: str = "remplacer",
) -> Edition:
    """Remplace (ou complète) un élément par du contenu HTML stylisé.

    Le HTML est converti en blocs Word (titres, paragraphes, listes, tableaux,
    images ``data:`` base64, SVG rasterisé), puis **transplanté** à la place de
    l'élément visé : le reste du modèle n'est pas retouché.

    Args:
        octets: copie du modèle à modifier.
        element_id: élément de référence (identifiant lu par ``indexer``).
        html: fragment HTML stylisé.
        position: ``remplacer`` (défaut), ``apres`` ou ``avant`` l'élément.

    Raises:
        ValidationError: identifiant inconnu, position inconnue, HTML vide ou
            non convertible.
    """
    if position not in ("remplacer", "apres", "avant"):
        raise ValidationError(
            f"Position inconnue : {position!r} (attendu : remplacer, apres, avant)"
        )
    paquet = paquet_docx.lire(octets)
    element, partie, racine = _cibler(paquet, element_id)
    ancien = _texte_de(element)

    temporaire = paquet_docx.lire(_html_converti(html))
    racine_temp = paquet_docx.xml(temporaire, paquet_docx.PARTIE_DOCUMENT)
    source = [
        enfant
        for enfant in _enfants_corps(paquet_docx.PARTIE_DOCUMENT, racine_temp)
        if enfant.tag in (_balise("p"), _balise("tbl"))
    ]
    if not source:
        raise ValidationError("Le HTML fourni ne produit aucun bloc Word : rien à insérer.")

    copies = [deepcopy(enfant) for enfant in source]
    materialises = _materialiser_listes(copies)
    modifications, ajouts, images = _greffer_medias(paquet, partie, temporaire, copies)

    parent = element.getparent()
    position_element = parent.index(element)
    retrait = 0 if position == "avant" else 1
    if position == "remplacer":
        parent.remove(element)
        retrait = 0
    for decalage, noeud in enumerate(copies):
        parent.insert(position_element + retrait + decalage, noeud)

    modifications[partie] = paquet_docx.serialiser(racine)
    edition = paquet_docx.ecrire(paquet, modifications, ajouts)
    avertissements: list[str] = []
    if materialises:
        avertissements.append(
            f"{materialises} paragraphe(s) de liste rendus avec une puce/un "
            "numéro explicite (le modèle du client ne définit pas les styles "
            "de liste Word)."
        )
    return Edition(
        octets=edition,
        element_id=element_id,
        partie=partie,
        ancien_texte=ancien,
        blocs_inseres=len(copies),
        images_ajoutees=images,
        parties_modifiees=sorted(set(modifications) | {partie}),
        parties_ajoutees=sorted(ajouts),
        avertissements=avertissements,
    )
