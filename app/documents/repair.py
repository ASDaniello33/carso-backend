"""Corrections ciblées d'un ``DocumentSpec`` — patcher, jamais reconstruire.

Une relecture de document remonte presque toujours des corrections ponctuelles :
un montant à rectifier, une cellule à tramer, une colonne qui déborde, une phrase
à réécrire. Régénérer tout le document pour cela consomme un appel de modèle
complet et fait perdre les corrections déjà obtenues.

Ce module applique donc une liste d'**opérations ciblées** à un spec existant et
rend un **nouveau** spec :

```text
DocumentSpec + [opérations] → DocumentSpec (nouveau) + journal des changements
```

Cinq cibles, pas davantage :

| Opération | Ce qu'elle corrige |
| --- | --- |
| `document` | métadonnées (titre, référence, destinataire…) et mise en page |
| `style` | un token de style (`tableau.entete_fond`, `corps.taille_pt`, `titres.1.taille_pt`) |
| `bloc` | un bloc : champs modifiés, remplacement complet, insertion, suppression |
| `tableau` | les propriétés d'un tableau (titre, largeurs, alignements, ligne de total) |
| `cellule` | une cellule : texte, trame, graisse, alignement, fusions |

Règles tenues par ce module :

- **aucune mutation** : le spec reçu n'est jamais modifié — le patch part d'une
  copie sérialisée et le résultat est **revalidé** (Pydantic puis, pour les
  tableaux touchés, ``Tableau.grille``) ;
- **aucune perte silencieuse** : cible hors bornes, chemin de style inconnu ou
  bloc de type inconnu produisent une ``ValidationError`` qui donne les bornes
  réelles ;
- **adressage sans grille** : dans un tableau, une cellule se désigne par
  ``ligne`` (l'index de la ligne **telle qu'elle est écrite**, en-tête(s) compris)
  et ``colonne`` (l'index dans cette ligne) — exactement ce que l'agent a écrit,
  si bien qu'une fusion ne décale jamais les coordonnées ;
- **journal** : chaque opération laisse une ligne lisible (« bloc 4 : cellule
  (ligne 3, colonne 2) → « 4 500 000 » »), reprise telle quelle par le tool ;
- **opérations validées une à une** : un tableau doit rester cohérent après
  *chaque* opération, donc une modification structurelle (fusion, squelette de
  lignes) s'exprime en **une seule** opération — jamais en deux temps, dont le
  premier laisserait une colonne non couverte.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from app.core.errors import ValidationError
from app.documents.spec import (
    Cellule,
    DocumentSpec,
    MiseEnPage,
    Tableau,
    bloc_par_type,
)
from app.documents.spec import (
    Metadonnees as MetadonneesDocument,
)
from app.documents.styles import DocumentStyle, style_par_nom

__all__ = [
    "Operation",
    "PatchBloc",
    "PatchCellule",
    "PatchDocument",
    "PatchStyle",
    "PatchTableau",
    "appliquer_patch",
]


class PatchDocument(BaseModel):
    """Métadonnées et/ou mise en page du document (seuls les champs fournis changent)."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["document"] = "document"
    champs: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Métadonnées à corriger : titre, sous_titre, reference, organisation, "
            "destinataire, auteur, date_document (AAAA-MM-JJ), objet, type_document."
        ),
    )
    mise_en_page: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Mise en page à corriger : format, orientation, marges, entete_texte, "
            "pied_texte, numero_de_page, couverture."
        ),
    )


class PatchStyle(BaseModel):
    """Un token de style, désigné par un chemin pointé (les autres restent intacts)."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["style"] = "style"
    chemin: str = Field(
        min_length=1,
        max_length=120,
        description=(
            "Chemin du token : 'corps.taille_pt', 'tableau.entete_fond', "
            "'titres.1.taille_pt', 'encadres.avertissement.fond', 'couverture.couleur_accent'."
        ),
    )
    valeur: Any = Field(
        description="Nouvelle valeur du token (texte, nombre, booléen ou couleur '#RRGGBB')."
    )


class PatchBloc(BaseModel):
    """Un bloc : champs corrigés, remplacement complet, insertion ou suppression.

    Une seule intention à la fois : ``champs`` **ou** ``valeur``, et un seul
    ``supprimer`` / ``inserer_apres`` (un ordre contradictoire est refusé plutôt
    que deviné).
    """

    model_config = ConfigDict(extra="forbid")

    type: Literal["bloc"] = "bloc"
    bloc: int = Field(ge=0, description="Index du bloc dans ``document_spec.blocs`` (0-based).")
    champs: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Champs du bloc à remplacer (cf. schéma du bloc : 'texte', 'niveau', "
            "'items', 'role', 'legende'…). Les autres champs sont conservés."
        ),
    )
    valeur: dict[str, Any] | None = Field(
        default=None, description="Bloc de remplacement complet (doit porter 'type')."
    )
    inserer_apres: dict[str, Any] | None = Field(
        default=None, description="Nouveau bloc à insérer juste après celui-ci."
    )
    supprimer: bool = Field(default=False, description="Supprimer ce bloc.")


class PatchTableau(BaseModel):
    """Propriétés d'un tableau (le contenu des cellules se corrige avec ``cellule``)."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["tableau"] = "tableau"
    bloc: int = Field(ge=0, description="Index du bloc ``tableau`` dans ``blocs``.")
    champs: dict[str, Any] = Field(
        min_length=1,
        description=(
            "Propriétés à corriger : 'titre_tableau', 'largeurs_mm' (ex. [40, 60, 30] "
            "pour une colonne qui déborde), 'alignements', 'total_ligne', "
            "'repeter_entete', 'entete', 'entetes', 'lignes'."
        ),
    )


class PatchCellule(BaseModel):
    """Une cellule d'un tableau : contenu et présentation.

    ``cellule`` remplace la cellule ; sous forme d'objet, seuls les champs fournis
    changent (le texte, les fusions ou la trame non mentionnés sont conservés),
    et ``fusion_colonnes: 'fin'`` étend la cellule jusqu'à la dernière colonne.
    """

    model_config = ConfigDict(extra="forbid")

    type: Literal["cellule"] = "cellule"
    bloc: int = Field(ge=0, description="Index du bloc ``tableau`` dans ``blocs``.")
    ligne: int = Field(
        ge=0,
        description=(
            "Index de la ligne telle qu'elle est écrite : les lignes d'en-tête "
            "d'abord (dans l'ordre de 'entetes', ou la ligne 'entete'), puis les "
            "lignes de 'lignes'. Ne pas compter les fusions."
        ),
    )
    colonne: int = Field(ge=0, description="Index de la cellule dans cette ligne (0-based).")
    cellule: Cellule | str = Field(
        description=(
            "Nouveau contenu : une chaîne pour changer le texte, ou un objet "
            "{texte, fond '#RRGGBB', gras, alignement, fusion_colonnes, fusion_lignes, "
            "couleur_texte, taille_pt} pour changer aussi la présentation."
        )
    )


#: Opération de correction ciblée : le champ ``type`` choisit la cible.
Operation = Annotated[
    PatchDocument | PatchStyle | PatchBloc | PatchTableau | PatchCellule,
    Field(discriminator="type"),
]


#: Validateur des opérations : accepte les objets typés **et** les dictionnaires
#: bruts (c'est la forme qui arrive d'un agent, via le tool).
_ADAPTATEUR = TypeAdapter(list[Operation])


def appliquer_patch(
    spec: DocumentSpec, operations: Sequence[Operation | dict[str, Any]]
) -> tuple[DocumentSpec, list[str]]:
    """Applique des corrections ciblées et rend un **nouveau** spec.

    Args:
        spec: document de départ (jamais modifié).
        operations: corrections (objets typés ou dictionnaires bruts), appliquées
            dans l'ordre reçu.

    Returns:
        ``(nouveau spec revalidé, journal lisible des changements)``.

    Raises:
        ValidationError: opération mal formée ou inapplicable (cible hors bornes,
            chemin de style inconnu, champ inconnu, spec résultat invalide).
    """
    try:
        validees = _ADAPTATEUR.validate_python(list(operations))
    except Exception as erreur:
        raise ValidationError(
            f"Opération de correction invalide : {erreur}",
            details={"operations_recues": len(operations)},
        ) from erreur
    donnees = spec.model_dump(mode="json")
    journal: list[str] = []
    for index, operation in enumerate(validees, start=1):
        journal.append(_appliquer(donnees, operation, position=index))
    try:
        nouveau = DocumentSpec.model_validate(donnees)
    except Exception as erreur:  # revalidation : un patch ne peut pas casser le spec
        raise ValidationError(
            f"Correction refusée : le document obtenu n'est plus valide ({erreur}).",
            details={"operations": len(validees)},
        ) from erreur
    _verifier_tableaux(nouveau)
    return nouveau, journal


def _appliquer(donnees: dict[str, Any], operation: Operation, *, position: int) -> str:
    """Applique une opération sur la copie sérialisée et rend sa ligne de journal."""
    if isinstance(operation, PatchDocument):
        return _appliquer_document(donnees, operation)
    if isinstance(operation, PatchStyle):
        return _appliquer_style(donnees, operation)
    if isinstance(operation, PatchBloc):
        return _appliquer_bloc(donnees, operation)
    if isinstance(operation, PatchTableau):
        return _appliquer_tableau(donnees, operation)
    return _appliquer_cellule(donnees, operation)


def _appliquer_document(donnees: dict[str, Any], patch: PatchDocument) -> str:
    """Corrige les métadonnées et la mise en page (champs explicitement fournis)."""
    if not patch.champs and not patch.mise_en_page:
        raise ValidationError(
            "Opération 'document' sans 'champs' ni 'mise_en_page' : rien à corriger."
        )
    touches: list[str] = []
    if patch.champs:
        _fusionner_champs(
            donnees["metadata"],
            patch.champs,
            modele=MetadonneesDocument,
            ou="metadata",
            touches=touches,
        )
    if patch.mise_en_page:
        _fusionner_champs(
            donnees["mise_en_page"],
            patch.mise_en_page,
            modele=MiseEnPage,
            ou="mise_en_page",
            touches=touches,
        )
    return f"document : {', '.join(touches)} modifié(s)"


def _fusionner_champs(
    cible: dict[str, Any],
    champs: dict[str, Any],
    *,
    modele: Any,
    ou: str,
    touches: list[str],
) -> None:
    """Fusionne des champs dans un dictionnaire, en refusant un champ inconnu."""
    connus = set(modele.model_fields)
    inconnus = sorted(set(champs) - connus)
    if inconnus:
        raise ValidationError(
            f"Champ inconnu pour {ou} : {', '.join(inconnus)}.",
            details={"champs_disponibles": sorted(connus)},
        )
    for cle, valeur in champs.items():
        cible[cle] = valeur
        touches.append(f"{ou}.{cle}")


def _appliquer_style(donnees: dict[str, Any], patch: PatchStyle) -> str:
    """Change un token de style, en résolvant le style effectif du document."""
    style = donnees.get("style") or style_par_nom(None).model_dump(mode="json")
    segments = [segment for segment in patch.chemin.split(".") if segment]
    if not segments:
        raise ValidationError("Chemin de style vide.")
    courant: Any = style
    for rang, segment in enumerate(segments[:-1]):
        cle = _cle(segment, courant, chemin=".".join(segments[: rang + 1]))
        courant = courant[cle]
        if not isinstance(courant, dict):
            raise ValidationError(
                f"Chemin de style invalide : {'.'.join(segments[: rang + 1])} n'est pas un groupe.",
                details={"chemin": patch.chemin},
            )
    derniere = _cle(segments[-1], courant, chemin=".".join(segments))
    ancienne = courant.get(derniere)
    courant[derniere] = patch.valeur
    try:
        DocumentStyle.model_validate(style)
    except Exception as erreur:
        courant[derniere] = ancienne
        raise ValidationError(
            f"Valeur refusée pour le style {patch.chemin} : {erreur}",
            details={"chemin": patch.chemin},
        ) from erreur
    donnees["style"] = style
    return f"style : {patch.chemin} → {patch.valeur!r}"


def _cle(segment: str, courant: dict[str, Any], *, chemin: str) -> Any:
    """Résout un segment de chemin (les clés numériques visent ``titres: {1..4}``)."""
    if segment in courant:
        return segment
    if segment.isdigit() and int(segment) in courant:
        return int(segment)
    raise ValidationError(
        f"Chemin de style inconnu : {chemin}.",
        details={"segments_disponibles": sorted(str(cle) for cle in courant)},
    )


def _appliquer_bloc(donnees: dict[str, Any], patch: PatchBloc) -> str:
    """Corrige un bloc : champs, remplacement, insertion ou suppression."""
    intentions = [
        patch.champs is not None,
        patch.valeur is not None,
        patch.inserer_apres is not None,
        patch.supprimer,
    ]
    if sum(intentions) != 1:
        raise ValidationError(
            "Opération 'bloc' : fournir exactement une intention "
            "(champs, valeur, inserer_apres ou supprimer)."
        )
    blocs = donnees["blocs"]
    _verifier_index(patch.bloc, len(blocs), "blocs", position=patch.bloc + 1)
    if patch.supprimer:
        retire = blocs.pop(patch.bloc)
        return f"bloc {patch.bloc} supprimé ({retire.get('type')})"
    if patch.inserer_apres is not None:
        nouveau = _bloc_valide(patch.inserer_apres, f"bloc inséré après {patch.bloc}")
        blocs.insert(patch.bloc + 1, nouveau.model_dump(mode="json"))
        return f"bloc {nouveau.type} inséré après {patch.bloc}"
    if patch.valeur is not None:
        remplacant = _bloc_valide(patch.valeur, f"bloc {patch.bloc}")
        blocs[patch.bloc] = remplacant.model_dump(mode="json")
        return f"bloc {patch.bloc} remplacé par un bloc {remplacant.type}"
    actuel = _bloc_valide(blocs[patch.bloc], f"bloc {patch.bloc}")
    connus = set(type(actuel).model_fields)
    inconnus = sorted(set(patch.champs or {}) - connus)
    if inconnus:
        raise ValidationError(
            f"Champ inconnu pour un bloc {actuel.type} : {', '.join(inconnus)}.",
            details={"champs_disponibles": sorted(connus)},
        )
    modifie = actuel.model_dump(mode="json")
    for cle, valeur in (patch.champs or {}).items():
        modifie[cle] = valeur
    valide = _bloc_valide(modifie, f"bloc {patch.bloc}")
    blocs[patch.bloc] = valide.model_dump(mode="json")
    return f"bloc {patch.bloc} ({valide.type}) : {', '.join(sorted(patch.champs or {}))} modifié(s)"


def _appliquer_tableau(donnees: dict[str, Any], patch: PatchTableau) -> str:
    """Corrige les propriétés d'un tableau (largeurs, titre, total…)."""
    tableau = _tableau_cible(donnees, patch.bloc)
    connus = set(Tableau.model_fields)
    inconnus = sorted(set(patch.champs) - connus)
    if inconnus:
        raise ValidationError(
            f"Propriété de tableau inconnue : {', '.join(inconnus)}.",
            details={"proprietes_disponibles": sorted(connus)},
        )
    modifie = tableau.model_dump(mode="json")
    for cle, valeur in patch.champs.items():
        modifie[cle] = valeur
    try:
        valide = Tableau.model_validate(modifie)
        valide.grille()
    except Exception as erreur:
        raise ValidationError(
            f"Correction refusée pour le tableau du bloc {patch.bloc} : {erreur}",
            details={"bloc": patch.bloc, "proprietes": sorted(patch.champs)},
        ) from erreur
    donnees["blocs"][patch.bloc] = valide.model_dump(mode="json")
    return f"bloc {patch.bloc} (tableau) : {', '.join(sorted(patch.champs))} modifié(s)"


def _appliquer_cellule(donnees: dict[str, Any], patch: PatchCellule) -> str:
    """Corrige une cellule : contenu et/ou présentation, fusions comprises."""
    tableau = _tableau_cible(donnees, patch.bloc)
    lignes_entete = tableau.lignes_entete()
    lignes_donnees = tableau.lignes_normalisees()
    lignes = lignes_entete + lignes_donnees
    _verifier_index(
        patch.ligne, len(lignes), f"tableau du bloc {patch.bloc}", position=patch.ligne + 1
    )
    ligne_courante = lignes[patch.ligne]
    _verifier_index(
        patch.colonne,
        len(ligne_courante),
        f"ligne {patch.ligne} du tableau {patch.bloc}",
        position=patch.colonne + 1,
    )
    existante = ligne_courante[patch.colonne]
    nouvelle = _cellule_patchee(existante, patch.cellule)
    note = _ecrire_cellule(
        donnees, patch.bloc, patch.ligne, len(lignes_entete), patch.colonne, nouvelle
    )
    return (
        f"bloc {patch.bloc} : cellule (ligne {patch.ligne}, colonne {patch.colonne}) "
        f"→ {nouvelle.texte!r}{note}"
    )


def _cellule_patchee(existante: Cellule, remplacement: Cellule | str) -> Cellule:
    """Applique un remplacement de cellule (objet : seuls les champs fournis changent)."""
    if isinstance(remplacement, str):
        return existante.model_copy(update={"texte": remplacement})
    fournis = remplacement.model_dump(exclude_unset=True)
    fournis.pop("type", None)
    return existante.model_copy(update=fournis)


def _ecrire_cellule(
    donnees: dict[str, Any],
    index_bloc: int,
    ligne: int,
    nb_lignes_entete: int,
    colonne: int,
    cellule: Cellule,
) -> str:
    """Écrit la cellule au bon emplacement (en-tête(s) ou données) et rend une note.

    Une ligne d'en-tête écrite en ``entete`` (simple liste de textes) ne peut pas
    porter de présentation : si le patch apporte une trame, une graisse ou une
    fusion, l'en-tête est converti en ``entetes`` pour que la correction soit
    réellement appliquée — la note le dit dans le journal, rien n'est silencieux.
    """
    valide = _bloc_valide(donnees["blocs"][index_bloc], f"bloc {index_bloc}")
    assert isinstance(valide, Tableau)
    note = ""
    en_entete = ligne < nb_lignes_entete
    entete_simple = valide.entete
    if en_entete and entete_simple is not None:
        purement_texte = not cellule.model_fields_set - {"texte"}
        if purement_texte:
            entete = list(entete_simple)
            entete[colonne] = cellule.texte
            valide = valide.model_copy(update={"entete": entete})
        else:
            valide = valide.model_copy(
                update={
                    "entete": None,
                    "entetes": [[Cellule(texte=texte) for texte in entete_simple]],
                }
            )
            note = " (en-tête converti en 'entetes' pour porter la présentation)"
    if en_entete and valide.entetes is not None:
        modifiees = [list(rangee) for rangee in valide.entetes]
        modifiees[ligne][colonne] = cellule
        valide = valide.model_copy(update={"entetes": modifiees})
    elif not en_entete:
        index_donnee = ligne - nb_lignes_entete
        modifiees = [list(rangee) for rangee in valide.lignes]
        modifiees[index_donnee][colonne] = cellule
        valide = valide.model_copy(update={"lignes": modifiees})
    valide.grille()
    donnees["blocs"][index_bloc] = valide.model_dump(mode="json")
    return note


def _tableau_cible(donnees: dict[str, Any], index: int) -> Tableau:
    """Bloc ``tableau`` visé, ou erreur qui dit ce que contient le document."""
    _verifier_index(index, len(donnees["blocs"]), "blocs", position=index + 1)
    bloc = _bloc_valide(donnees["blocs"][index], f"bloc {index}")
    if not isinstance(bloc, Tableau):
        raise ValidationError(
            f"Le bloc {index} est de type {bloc.type}, pas 'tableau'.",
            details={"bloc": index, "type": bloc.type},
        )
    return bloc


def _verifier_index(index: int, taille: int, ou: str, *, position: int) -> None:
    """Refuse un index hors bornes en indiquant la plage réelle."""
    if index < 0 or index >= taille:
        raise ValidationError(
            f"Index hors bornes : {index} pour {ou} ({taille} élément(s)).",
            details={"ou": ou, "position": position, "taille": taille},
        )


def _bloc_valide(donnees: dict[str, Any], ou: str) -> Any:
    """Construit un bloc typé depuis un dictionnaire, avec un message situé."""
    try:
        return bloc_par_type(donnees)
    except Exception as erreur:
        raise ValidationError(f"Bloc invalide ({ou}) : {erreur}") from erreur


def _verifier_tableaux(spec: DocumentSpec) -> None:
    """Refuse un spec dont un tableau ne tient pas sur sa grille (patch incohérent)."""
    for index, bloc in enumerate(spec.blocs):
        if isinstance(bloc, Tableau):
            try:
                bloc.grille()
            except ValidationError as erreur:
                raise ValidationError(
                    f"Correction refusée : le tableau du bloc {index} est incohérent "
                    f"({erreur})."
                ) from erreur
