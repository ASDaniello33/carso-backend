"""DocumentRenderer — rendu déterministe sur une copie du modèle (INC-128).

Ce module ne reçoit **jamais** de texte à mettre en page : il reçoit un modèle
instrumenté et des valeurs, puis modifie les zones ancrées les unes après les
autres. Tout ce qui n'est pas ancré reste **exactement** tel quel.

Règles tenues :

- le paragraphe du modèle garde son ``w:pPr`` (alignement, retrait, interligne,
  numérotation) ; le run garde son ``w:rPr`` (police, taille, couleur, gras) ;
- un champ ``paragraphe`` peut recevoir plusieurs segments formatés : chaque
  segment reprend le ``w:rPr`` du modèle et n'en modifie que ce qui est demandé ;
- une ligne répétable est clonée à l'identique (``w:tcPr``, bordures, trames,
  largeurs conservés) autant de fois qu'il y a de lignes de données ;
- un bloc conditionnel est conservé si la valeur est vraie, supprimé sinon ;
- à la fin, **tous** les ``w:sdt carso:*`` sont détachés : le livrable ne
  contient aucun contrôle technique.

Une valeur obligatoire absente, ou un texte plus long que ``max_length``, fait
échouer le rendu avec la liste précise — on ne tronque pas en silence.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

from app.core.errors import ValidationError
from app.documents.moteur import analyse as analyse_docx
from app.documents.moteur import instrumentation as instrumentation_docx
from app.documents.moteur import paquet as paquet_docx
from app.documents.moteur import schemas as S

__all__ = ["DocumentRenderer", "Rendu", "rendre"]

_W = analyse_docx._W
_XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"


def _balise(nom: str) -> str:
    return f"{{{_W}}}{nom}"


def _texte(valeur: Any) -> str:
    """Valeur scalaire → texte du document (jamais ``None`` affiché)."""
    if valeur is None:
        return ""
    if isinstance(valeur, bool):
        return "oui" if valeur else "non"
    if isinstance(valeur, float):
        return f"{valeur:g}"
    return str(valeur)


def _controle_du_tag(racine: Any, tag: str) -> Any | None:
    for controle in racine.iter(_balise("sdt")):
        if instrumentation_docx._tag_de(controle) == tag:
            return controle
    return None


def _detacher(controle: Any) -> None:
    """Retire l'enveloppe ``w:sdt`` en conservant son contenu, à la même place."""
    parent = controle.getparent()
    if parent is None:
        return
    contenu = controle.find(_balise("sdtContent"))
    position = parent.index(controle)
    parent.remove(controle)
    if contenu is None:
        return
    for enfant in list(contenu):
        parent.insert(position, enfant)
        position += 1


def _supprimer(controle: Any) -> None:
    parent = controle.getparent()
    if parent is not None:
        parent.remove(controle)


def _paragraphe_utile(contenu: Any) -> Any | None:
    """Paragraphe à réécrire dans un ``w:sdtContent`` (paragraphe ou cellule)."""
    if contenu is None:
        return None
    direct = contenu.find(_balise("p"))
    if direct is not None:
        return direct
    for paragraphe in contenu.iter(_balise("p")):
        return paragraphe
    return None


def _rpr_modele(paragraphe: Any) -> Any | None:
    """``w:rPr`` du premier run : c'est lui qui porte la mise en forme du modèle."""
    for run in paragraphe.iter(_balise("r")):
        proprietes = run.find(_balise("rPr"))
        if proprietes is not None:
            return deepcopy(proprietes)
    return None


#: Ordre des enfants de ``w:rPr`` dans le schéma WordprocessingML. Insérer au bon
#: endroit évite de produire un XML que Word accepte mais qu'un validateur strict
#: refuse : la fidélité du fichier passe aussi par là.
_ORDRE_RPR = [
    "rStyle", "rFonts", "b", "bCs", "i", "iCs", "caps", "smallCaps", "strike",
    "dstrike", "outline", "shadow", "emboss", "imprint", "noProof", "snapToGrid",
    "vanish", "webHidden", "color", "spacing", "w", "kern", "position", "sz",
    "szCs", "highlight", "u", "effect", "bdr", "shd", "fitText", "vertAlign",
    "rtl", "cs", "em", "lang", "eastAsianLayout", "specVanish", "oMath",
]


def _regler(proprietes: Any, nom: str, attributs: dict[str, str] | None = None) -> Any:
    """Crée ou met à jour un enfant de ``w:rPr`` en respectant l'ordre du schéma."""
    from lxml import etree

    existant = proprietes.find(_balise(nom))
    if existant is None:
        existant = etree.Element(_balise(nom))
        rang = _ORDRE_RPR.index(nom) if nom in _ORDRE_RPR else len(_ORDRE_RPR)
        position = len(proprietes)
        for index, enfant in enumerate(proprietes):
            local = etree.QName(enfant).localname
            if local in _ORDRE_RPR and _ORDRE_RPR.index(local) > rang:
                position = index
                break
        proprietes.insert(position, existant)
    for cle, valeur in (attributs or {}).items():
        existant.set(_balise(cle), valeur)
    return existant


def _appliquer_segment(proprietes: Any, segment: S.SegmentValeur) -> None:
    """Mise en forme demandée, appliquée **par-dessus** celle du modèle."""
    for nom, valeur in (("b", segment.gras), ("i", segment.italique)):
        if valeur is None:
            continue
        _regler(proprietes, nom, {} if valeur else {"val": "0"})
    if segment.souligne is not None:
        _regler(proprietes, "u", {"val": "single" if segment.souligne else "none"})
    if segment.couleur:
        _regler(proprietes, "color", {"val": segment.couleur})
    if segment.taille_pt:
        demi = str(int(round(segment.taille_pt * 2)))
        _regler(proprietes, "sz", {"val": demi})
        _regler(proprietes, "szCs", {"val": demi})


def _vider(paragraphe: Any) -> None:
    """Retire le contenu texte du paragraphe, en conservant ``w:pPr``."""
    for enfant in list(paragraphe):
        if enfant.tag == _balise("pPr"):
            continue
        paragraphe.remove(enfant)


def _ajouter_run(paragraphe: Any, texte: str, modele_rpr: Any,
                 segment: S.SegmentValeur | None = None) -> None:
    """Ajoute un run au texte donné, avec le ``w:rPr`` du modèle (copié)."""
    from lxml import etree

    run = etree.SubElement(paragraphe, _balise("r"))
    proprietes = deepcopy(modele_rpr) if modele_rpr is not None else None
    if proprietes is None and segment is not None:
        proprietes = etree.Element(_balise("rPr"))
    if proprietes is not None:
        if segment is not None:
            _appliquer_segment(proprietes, segment)
        run.append(proprietes)
    for index, morceau in enumerate(texte.split("\n")):
        if index:
            etree.SubElement(run, _balise("br"))
        noeud = etree.SubElement(run, _balise("t"))
        noeud.set(_XML_SPACE, "preserve")
        noeud.text = morceau


def _segments(valeur: Any, champ: S.ChampModele) -> list[S.SegmentValeur]:
    """Valeur d'un champ → segments de texte à écrire (jamais réinventés)."""
    if valeur is None:
        return []
    if isinstance(valeur, str):
        return [S.SegmentValeur(texte=valeur)]
    if isinstance(valeur, list) and all(isinstance(item, str) for item in valeur):
        return [S.SegmentValeur(texte=" ".join(str(item) for item in valeur))]
    if isinstance(valeur, list):
        try:
            return [S.SegmentValeur.model_validate(item) for item in valeur]
        except Exception as exc:
            raise ValidationError(
                f"{champ.field_key} : segments invalides — attendu une liste de "
                "{texte, gras, italique, couleur, taille_pt}",
                details={"erreur": str(exc)[:200]},
            ) from exc
    return [S.SegmentValeur(texte=_texte(valeur))]


def _ecrire_valeur(controle: Any, valeur: Any, champ: S.ChampModele) -> None:
    """Réécrit le contenu d'une zone ancrée, en gardant la mise en forme du modèle.

    Raises:
        ValidationError: zone sans paragraphe exploitable.
    """
    contenu = controle.find(_balise("sdtContent"))
    paragraphe = _paragraphe_utile(contenu)
    if paragraphe is None:
        raise ValidationError(
            f"Zone {champ.field_key} : aucun paragraphe dans le contrôle, "
            "impossible d'y écrire un texte."
        )
    modele_rpr = _rpr_modele(paragraphe)
    modeles_paragraphes: list[str] = []

    # Champ « paragraphe » alimenté par une liste de paragraphes : le premier
    # réutilise le paragraphe du modèle, les suivants en sont des copies
    # (même w:pPr), insérées à la suite.
    if champ.type == "paragraphe" and isinstance(valeur, list) and valeur and all(
        isinstance(item, str) for item in valeur
    ):
        modeles_paragraphes = [str(item) for item in valeur]
        premiere, reste = modeles_paragraphes[0], modeles_paragraphes[1:]
        _vider(paragraphe)
        if premiere.strip():
            _ajouter_run(paragraphe, premiere, modele_rpr)
        parent = paragraphe.getparent()
        position = parent.index(paragraphe) + 1
        for texte in reste:
            if not texte.strip():
                continue
            copie = deepcopy(paragraphe)
            _vider(copie)
            _ajouter_run(copie, texte, modele_rpr)
            parent.insert(position, copie)
            position += 1
        return

    _vider(paragraphe)
    segments = _segments(valeur, champ)
    if not segments:
        return
    for segment in segments:
        if not segment.texte:
            continue
        _ajouter_run(paragraphe, segment.texte, modele_rpr, segment)


def _verite(valeur: Any) -> bool:
    """Valeur de condition : ``"non"``/``"0"``/vide valent faux (jamais ``bool("non")``)."""
    if isinstance(valeur, str):
        return valeur.strip().casefold() not in ("", "0", "false", "non", "no", "off")
    if isinstance(valeur, (list, dict)):
        return bool(valeur)
    return bool(valeur)


def _est_vide(valeur: Any) -> bool:
    return valeur is None or (isinstance(valeur, str) and not valeur.strip()) or (
        isinstance(valeur, (list, dict)) and not valeur
    )


def _preparer(configuration: S.ConfigurationModele, valeurs: dict[str, Any]) -> None:
    """Vérifie les données **avant** de toucher au document.

    Raises:
        ValidationError: champ obligatoire absent, contenu trop long, ou lignes
            répétables mal formées. Le document n'est alors pas produit du tout.
    """
    manquants: list[str] = []
    trop_longs: list[str] = []
    mal_formes: list[str] = []
    for champ in configuration.champs:
        valeur = valeurs.get(champ.field_key)
        if _est_vide(valeur):
            if champ.obligatoire:
                manquants.append(champ.field_key)
            continue
        if isinstance(valeur, str) and champ.max_length and len(valeur) > champ.max_length:
            trop_longs.append(f"{champ.field_key} ({len(valeur)} > {champ.max_length})")
        if champ.type == "lignes":
            if not isinstance(valeur, list) or not all(
                isinstance(ligne, dict) for ligne in valeur
            ):
                mal_formes.append(
                    f"{champ.field_key} : attendu une liste d'objets "
                    f"{champ.colonnes or []}"
                )
    if manquants:
        raise ValidationError(
            "Champs obligatoires non renseignés : " + ", ".join(manquants),
            details={"champs_manquants": manquants},
        )
    if trop_longs:
        raise ValidationError(
            "Contenu trop long pour le champ : " + ", ".join(trop_longs),
            details={"champs_trop_longs": trop_longs},
        )
    if mal_formes:
        raise ValidationError(
            "Données de lignes répétables mal formées : " + " ; ".join(mal_formes),
            details={"champs_mal_formes": mal_formes},
        )


@dataclass
class Rendu:
    """Livrable produit + journal de ce qui a été remplacé, gardé ou ignoré."""

    octets: bytes
    champs_remplaces: list[str] = field(default_factory=list)
    champs_conserves: list[str] = field(default_factory=list)
    champs_supprimes: list[str] = field(default_factory=list)
    tags_inconnus: list[str] = field(default_factory=list)
    blocs_conserves: list[str] = field(default_factory=list)
    lignes: dict[str, int] = field(default_factory=dict)
    parties: list[str] = field(default_factory=list)
    avertissements: list[str] = field(default_factory=list)


def _traiter_lignes(
    racine: Any,
    configuration: S.ConfigurationModele,
    valeurs: dict[str, Any],
    rapport: Rendu,
) -> None:
    """Clone la ligne modèle autant de fois qu'il y a de lignes de données."""
    prefixe = f"{S.TAG_PREFIX}lignes:"
    for controle in list(racine.iter(_balise("sdt"))):
        tag = instrumentation_docx._tag_de(controle)
        if not tag or not tag.startswith(prefixe) or controle.getparent() is None:
            continue
        cle = tag[len(prefixe):]
        champ = configuration.champ(cle)
        if champ is None:
            continue  # traité comme tag inconnu par la passe suivante
        lignes = valeurs.get(cle) or []
        if not isinstance(lignes, list):
            raise ValidationError(f"{cle} : les lignes doivent être une liste d'objets")
        for index, ligne in enumerate(lignes):
            if not isinstance(ligne, dict):
                raise ValidationError(f"{cle} : la ligne {index + 1} n'est pas un objet")
            copie = deepcopy(controle)
            for cellule in list(copie.iter(_balise("sdt"))):
                if cellule is copie:
                    continue
                tag_cellule = instrumentation_docx._tag_de(cellule)
                if not tag_cellule or not tag_cellule.startswith(f"{S.TAG_PREFIX}{cle}."):
                    continue
                colonne = tag_cellule[len(S.TAG_PREFIX) + len(cle) + 1:]
                if colonne not in ligne and colonne not in (champ.colonnes or []):
                    rapport.avertissements.append(
                        f"{cle} : colonne {colonne!r} absente de la ligne {index + 1}, "
                        "cellule laissée vide."
                    )
                _ecrire_valeur(cellule, ligne.get(colonne, ""), champ)
                _detacher(cellule)
            ligne_xml = copie.find(f"{_balise('sdtContent')}/{_balise('tr')}")
            if ligne_xml is None:
                raise ValidationError(
                    f"{cle} : l'ancre répétable n'entoure pas une ligne de tableau "
                    "(w:tr) — réinstrumenter le modèle."
                )
            controle.addprevious(ligne_xml)
        if not lignes:
            rapport.avertissements.append(
                f"{cle} : aucune ligne fournie — la ligne modèle du tableau a été retirée."
            )
        _supprimer(controle)
        rapport.lignes[cle] = len(lignes)
        rapport.champs_remplaces.append(cle)


def _traiter_champs(
    racine: Any,
    configuration: S.ConfigurationModele,
    valeurs: dict[str, Any],
    rapport: Rendu,
) -> None:
    """Remplace les zones dynamiques et applique les conditions."""
    prefixe = S.TAG_PREFIX
    for controle in list(racine.iter(_balise("sdt"))):
        tag = instrumentation_docx._tag_de(controle)
        if not tag or not tag.startswith(prefixe) or controle.getparent() is None:
            continue
        reste = tag[len(prefixe):]
        if reste.startswith("conditionnel:"):
            cle = reste.split(":", 1)[1]
            champ = configuration.champ(cle)
            if champ is None:
                rapport.tags_inconnus.append(tag)
                _detacher(controle)
                continue
            if _verite(valeurs.get(cle)):
                _detacher(controle)
                rapport.champs_remplaces.append(cle)
            else:
                _supprimer(controle)
                rapport.champs_supprimes.append(cle)
            continue
        if reste.startswith("lignes:") or "." in reste:
            # Ancre répétable non reconnue par la configuration : on conserve le
            # contenu du modèle et on le dit, plutôt que d'écrire au hasard.
            rapport.tags_inconnus.append(tag)
            _detacher(controle)
            continue
        champ = configuration.champ(reste)
        if champ is None:
            rapport.tags_inconnus.append(tag)
            _detacher(controle)
            continue
        if reste not in valeurs:
            rapport.champs_conserves.append(reste)
            _detacher(controle)
            continue
        _ecrire_valeur(controle, valeurs[reste], champ)
        _detacher(controle)
        if reste not in rapport.champs_remplaces:
            rapport.champs_remplaces.append(reste)


class DocumentRenderer:
    """Produit un livrable à partir d'une copie instrumentée et de valeurs."""

    def rendre(
        self,
        source: Any,
        configuration: S.ConfigurationModele,
        donnees: S.DonneesGeneration | dict[str, Any],
        *,
        analyse: S.AnalyseModele | None = None,
    ) -> Rendu:
        """Rend le document.

        Args:
            source: DOCX **instrumenté** (octets ou chemin).
            configuration: configuration validée (``statut="validee"``).
            donnees: valeurs par clé de champ.
            analyse: analyse d'origine, pour lister les blocs laissés intacts.

        Raises:
            ValidationError: configuration non validée, document non instrumenté,
                champ obligatoire absent, contenu trop long, lignes mal formées.
        """
        if configuration.statut != "validee":
            raise ValidationError(
                "Configuration non validée : un modèle « brouillon » ne produit "
                "aucun document. Valider la configuration avant de générer."
            )
        paquet = paquet_docx.lire(source)
        if not instrumentation_docx.tags_carso(paquet):
            raise ValidationError(
                "Ce document n'est pas instrumenté (aucune ancre carso) : "
                "valider le modèle avant de générer un livrable."
            )
        valeurs = (
            dict(donnees.valeurs)
            if isinstance(donnees, S.DonneesGeneration)
            else dict(donnees)
        )
        _preparer(configuration, valeurs)

        rapport = Rendu(octets=b"")
        remplacements: dict[str, bytes] = {}
        for nom in paquet_docx.zones_texte(paquet):
            racine = paquet_docx.xml(paquet, nom)
            if not any(
                (instrumentation_docx._tag_de(controle) or "").startswith(S.TAG_PREFIX)
                for controle in racine.iter(_balise("sdt"))
            ):
                continue
            _traiter_lignes(racine, configuration, valeurs, rapport)
            _traiter_champs(racine, configuration, valeurs, rapport)
            remplacements[nom] = paquet_docx.serialiser(racine)

        rapport.parties = sorted(remplacements)
        rapport.octets = paquet_docx.ecrire(paquet, remplacements)

        connues = {champ.field_key for champ in configuration.champs}
        ignorees = sorted(set(valeurs) - connues)
        if ignorees:
            rapport.avertissements.append(
                "Données fournies sans champ correspondant (ignorées) : "
                + ", ".join(ignorees)
            )
        if analyse is not None:
            ciblees = {champ.block_id for champ in configuration.champs}
            rapport.blocs_conserves = [
                bloc.block_id for bloc in analyse.blocs if bloc.block_id not in ciblees
            ]
        return rapport


def rendre(
    source: Any,
    configuration: S.ConfigurationModele,
    donnees: S.DonneesGeneration | dict[str, Any],
    *,
    analyse: S.AnalyseModele | None = None,
) -> Rendu:
    """Raccourci : rend un livrable depuis un modèle instrumenté."""
    return DocumentRenderer().rendre(source, configuration, donnees, analyse=analyse)
