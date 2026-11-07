"""TemplateInstrumenter — pose des ancres techniques sur une copie du modèle (INC-128).

Le document du client n'est **jamais** modifié. L'instrumentation produit une
copie de travail dans laquelle chaque zone validée est entourée d'un Content
Control (``w:sdt``) dont le ``w:tag`` vaut ``carso:<clé>`` :

- le texte d'origine reste **dans** le contrôle : rien n'est effacé, rien n'est
  ajouté visuellement ;
- le rendu retrouvera ses zones **par tag**, jamais par position ni par
  ressemblance de texte — c'est ce qui rend le rendu robuste ;
- seules les parties touchées sont réécrites (``paquet.ecrire``).

Ce que l'instrumentation **refuse**, plutôt que de produire un fichier douteux :

- une analyse qui ne correspond plus au fichier (empreinte différente) ;
- un tag ``carso:`` déjà présent dans le modèle (collision d'ancres) ;
- une ligne de tableau répétable qui porte une fusion verticale ``vMerge``
  « suite » : la cloner produirait un tableau incohérent ;
- une ligne répétable sans colonnes déclarées.

Ces refus sont des ``ValidationError`` explicites, jamais un avertissement perdu.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.core.errors import ValidationError
from app.documents.moteur import analyse as analyse_docx
from app.documents.moteur import paquet as paquet_docx
from app.documents.moteur import schemas as S

__all__ = [
    "Instrumentation",
    "TemplateInstrumenter",
    "instrumenter",
    "tags_carso",
]

_W = analyse_docx._W  # même namespace que l'analyseur, une seule définition
_XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"


def _balise(nom: str) -> str:
    return f"{{{_W}}}{nom}"


def _tag_de(controle: Any) -> str | None:
    """Valeur du ``w:tag`` d'un ``w:sdt`` (``None`` s'il n'en a pas)."""
    balise = controle.find(f"{_balise('sdtPr')}/{_balise('tag')}")
    if balise is None:
        return None
    return balise.get(_balise("val"))


def tags_carso(paquet: paquet_docx.Paquet) -> list[str]:
    """Tags ``carso:`` présents dans le paquet, dans l'ordre du document."""
    trouves: list[str] = []
    for nom in paquet_docx.zones_texte(paquet):
        racine = paquet_docx.xml(paquet, nom)
        for controle in racine.iter(_balise("sdt")):
            tag = _tag_de(controle)
            if tag and tag.startswith(S.TAG_PREFIX):
                trouves.append(tag)
    return trouves


def _envelopper(parent: Any, element: Any, *, tag: str, alias: str) -> Any:
    """Remplace ``element`` par un ``w:sdt`` qui le contient, à la même position."""
    from lxml import etree

    controle = etree.Element(_balise("sdt"))
    proprietes = etree.SubElement(controle, _balise("sdtPr"))
    etiquette = etree.SubElement(proprietes, _balise("tag"))
    etiquette.set(_balise("val"), tag)
    nom = etree.SubElement(proprietes, _balise("alias"))
    nom.set(_balise("val"), alias[:120])
    contenu = etree.SubElement(controle, _balise("sdtContent"))
    parent.replace(element, controle)
    contenu.append(element)
    return controle


def _cibles(paquet: paquet_docx.Paquet, analyse: S.AnalyseModele) -> dict[str, Any]:
    """Élément XML de chaque ``block_id``, par re-parcours déterministe.

    Raises:
        ValidationError: le fichier ne correspond plus à l'analyse (ordre ou
            nombre de blocs différent) — on ne devine jamais une correspondance.
    """
    cibles: dict[str, Any] = {}
    index = 0
    for nom in paquet_docx.zones_texte(paquet):
        racine = paquet_docx.xml(paquet, nom)
        if nom == paquet_docx.PARTIE_DOCUMENT:
            corps = racine.find(_balise("body"))
            enfants = list(corps) if corps is not None else []
        else:
            enfants = list(racine)
        for enfant in enfants:
            if enfant.tag not in (_balise("p"), _balise("tbl")):
                continue
            if index >= len(analyse.blocs):
                raise ValidationError(
                    "Le document contient plus de blocs que l'analyse : "
                    "relancer l'analyse avant d'instrumenter."
                )
            bloc = analyse.blocs[index]
            if bloc.partie != nom:
                raise ValidationError(
                    "Analyse incohérente avec le fichier : relancer l'analyse.",
                    details={"attendu": bloc.partie, "trouve": nom},
                )
            cibles[bloc.block_id] = enfant
            index += 1
    if index != len(analyse.blocs):
        raise ValidationError(
            "Le document contient moins de blocs que l'analyse : "
            "relancer l'analyse avant d'instrumenter.",
            details={"lues": len(analyse.blocs), "trouvees": index},
        )
    return cibles


@dataclass
class Instrumentation:
    """Copie instrumentée + ce qui a réellement été posé."""

    octets: bytes
    tags: list[str] = field(default_factory=list)
    parties: list[str] = field(default_factory=list)
    avertissements: list[str] = field(default_factory=list)


def _ligne_modele(
    tableau: Any, bloc: S.Bloc, champ: S.ChampModele
) -> tuple[Any, list[Any]]:
    """Ligne de données à cloner et cellules à ancrer.

    Raises:
        ValidationError: tableau sans ligne de données, ligne à fusion verticale
            (clonage incohérent), ou tableau dont la structure a changé.
    """
    candidates = [ligne for ligne in bloc.lignes if not ligne.entete]
    if not candidates:
        raise ValidationError(
            f"Tableau {bloc.block_id} sans ligne de données : la ligne répétable "
            f"doit exister dans le modèle (clé {champ.field_key!r})."
        )
    # Une ligne sans fusion est le meilleur gabarit : cloner une ligne à
    # ``gridSpan`` ou ``vMerge`` dupliquerait des cellules fusionnées. On la
    # préfère donc, sans jamais en inventer une.
    sans_fusion = [
        ligne
        for ligne in candidates
        if all(cellule.fusion_horizontale == 1 for cellule in ligne.cellules)
        and not any(cellule.fusion_verticale for cellule in ligne.cellules)
    ]
    modele = (sans_fusion or candidates)[0]
    # Toute fusion verticale sur la ligne modèle est refusée, qu'elle soit le
    # début (« restart ») ou la suite : cloner l'une ou l'autre produit un
    # tableau dont les cellules fusionnées ne correspondent plus à rien.
    if any(cellule.fusion_verticale for cellule in modele.cellules):
        raise ValidationError(
            f"Ligne {modele.index} du tableau {bloc.block_id} : fusion verticale "
            "présente. Cloner cette ligne produirait un tableau incohérent — "
            "choisir une autre ligne de données ou retirer la fusion dans le modèle.",
            details={"block_id": bloc.block_id, "ligne": modele.index},
        )
    lignes_xml = tableau.findall(_balise("tr"))
    if modele.index >= len(lignes_xml):
        raise ValidationError(
            f"Ligne {modele.index} introuvable dans le tableau {bloc.block_id} : "
            "relancer l'analyse."
        )
    return lignes_xml[modele.index], list(lignes_xml[modele.index].findall(_balise("tc")))


class TemplateInstrumenter:
    """Pose les ancres ``carso:*`` sur une copie du modèle."""

    def instrumenter(
        self,
        source: Any,
        analyse: S.AnalyseModele,
        configuration: S.ConfigurationModele,
    ) -> Instrumentation:
        """Produit la copie technique ancrée.

        Raises:
            ValidationError: analyse périmée, modèle déjà instrumenté, zone
                impossible à ancrer (voir la docstring du module).
        """
        paquet = paquet_docx.lire(source)
        if analyse_docx._empreinte(paquet) != analyse.empreinte:
            raise ValidationError(
                "Le fichier ne correspond plus à l'analyse (empreinte différente) : "
                "relancer l'analyse du modèle avant de le valider."
            )
        existants = tags_carso(paquet)
        if existants:
            raise ValidationError(
                "Ce modèle contient déjà des ancres carso : instrumenter le fichier "
                "d'origine, jamais une copie technique.",
                details={"tags": existants[:10]},
            )

        cibles = _cibles(paquet, analyse)
        racines: dict[str, Any] = {}
        tags: list[str] = []
        avertissements: list[str] = []

        for bloc in analyse.blocs:
            champs = [champ for champ in configuration.champs if champ.block_id == bloc.block_id]
            for champ in champs:
                if champ.classification is S.Classification.STATIC:
                    continue
                element = cibles[bloc.block_id]
                parent = element.getparent()
                if parent is None:  # pragma: no cover - impossible sur un DOCX lu
                    continue

                if champ.classification is S.Classification.REPEATABLE:
                    if bloc.type != "tableau":
                        raise ValidationError(
                            f"{champ.field_key} : une ligne répétable exige un tableau "
                            f"({bloc.block_id} est un {bloc.type})."
                        )
                    if not champ.colonnes:
                        raise ValidationError(
                            f"{champ.field_key} : déclarer les colonnes de la ligne "
                            "répétable (elles deviennent les clés de chaque ligne)."
                        )
                    ligne, cellules = _ligne_modele(element, bloc, champ)
                    for colonne, cellule in zip(champ.colonnes, cellules, strict=False):
                        tag = f"{S.TAG_PREFIX}{champ.field_key}.{colonne}"
                        _envelopper(cellule.getparent(), cellule, tag=tag, alias=colonne)
                        tags.append(tag)
                    if len(champ.colonnes) != len(cellules):
                        avertissements.append(
                            f"{champ.field_key} : {len(champ.colonnes)} colonne(s) "
                            f"déclarée(s) pour {len(cellules)} cellule(s) dans la ligne "
                            f"{bloc.block_id} ; seules les premières sont ancrées."
                        )
                    tag_ligne = f"{S.TAG_PREFIX}lignes:{champ.field_key}"
                    _envelopper(ligne.getparent(), ligne, tag=tag_ligne, alias=champ.label)
                    tags.append(tag_ligne)
                elif champ.classification is S.Classification.CONDITIONAL:
                    tag = f"{S.TAG_PREFIX}conditionnel:{champ.field_key}"
                    _envelopper(parent, element, tag=tag, alias=champ.label)
                    tags.append(tag)
                else:  # DYNAMIC
                    if bloc.type == "tableau":
                        raise ValidationError(
                            f"{champ.field_key} : un tableau se déclare REPEATABLE "
                            f"(ligne clonée) ou reste STATIC, jamais DYNAMIC."
                        )
                    tag = f"{S.TAG_PREFIX}{champ.field_key}"
                    _envelopper(parent, element, tag=tag, alias=champ.label)
                    tags.append(tag)

                # La partie touchée est re-sérialisée une seule fois, à la fin.
                racines[bloc.partie] = element.getroottree().getroot()

        dupliques = sorted({tag for tag in tags if tags.count(tag) > 1})
        if dupliques:
            raise ValidationError(
                "Tags d'ancrage dupliqués : deux champs ne peuvent pas viser le même tag.",
                details={"tags": dupliques},
            )

        remplacements = {
            nom: paquet_docx.serialiser(racine) for nom, racine in racines.items()
        }
        return Instrumentation(
            octets=paquet_docx.ecrire(paquet, remplacements),
            tags=tags,
            parties=sorted(remplacements),
            avertissements=avertissements,
        )


def instrumenter(
    source: Any, analyse: S.AnalyseModele, configuration: S.ConfigurationModele
) -> Instrumentation:
    """Raccourci : instrumente un modèle validé."""
    return TemplateInstrumenter().instrumenter(source, analyse, configuration)
