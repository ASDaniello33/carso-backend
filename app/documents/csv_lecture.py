"""Lecture structurée d'un CSV : colonnes, lignes, encodage et séparateur.

Pourquoi ce module : ``lire_document`` rendait un CSV comme un bloc de texte
brut — 4 000 caractères de ``a;b;c`` où ni les colonnes ni les lignes n'étaient
identifiables — et aucun outil de recherche ne savait le fouiller
(``chercher_dans_document`` ne couvrait que docx/pdf/xlsx,
``lire_element_document`` aussi). L'agent ne pouvait donc ni citer une ligne
précise ni isoler une valeur : il relisait le fichier en espérant voir.

Ce que ce module rend, et pourquoi ainsi :

- **des colonnes nommées** (``colonnes``) et des lignes en dictionnaires
  (``valeurs``). Un index de colonne oblige le modèle à compter les ``;`` : le
  nom d'en-tête est la seule clé qu'il puisse citer sans se tromper ;
- **le repère de lecture est la ligne du fichier**, en-tête comprise
  (ligne 1). C'est le seul numéro que l'agent voit dans son éditeur et que
  ``relire`` peut réutiliser tel quel — un « index de donnée » décalé de un
  produisait exactement les décalages qu'on veut éviter ;
- **le séparateur et l'encodage sont détectés puis annoncés**
  (``separateur``, ``encodage``), avec une surcharge explicite possible : un
  export Excel français est en ``;`` et cp1252, un export anglo-saxon en ``,``
  et UTF-8. Aucune détection silencieuse : la valeur retenue est rendue ;
- **les défauts de forme sont dits** (``problemes``) : ligne de largeur
  inégale (le symptôme d'un séparateur mal détecté), en-tête absent ou en
  double. L'agent corrige son appel au lieu de subir une lecture fausse ;
- **tout retour est borné** (lignes d'aperçu, plage, longueur de cellule) et
  dit sa troncature : jamais de troncature silencieuse.

**Lecture en flux (constat du 13/11).** Un export réel de 17 Mo était refusé
entier (« CSV trop volumineux pour être lu en mémoire ») : l'agent statistique
ne pouvait ni l'apercevoir, ni le fouiller, ni en lire une ligne. Le fichier
n'est donc plus chargé en mémoire : il est parcouru **ligne par ligne**, en
gardant seulement la fenêtre demandée (aperçu, plage, ligne) ou les résultats
d'une recherche. Le nombre total de lignes est compté pendant ce parcours, et
la mémoire reste bornée quelle que soit la taille du fichier.

Ce module est en lecture seule et ne connaît ni tool ni permission : la
frontière (``document_read``, service documentaire, aucune manipulation de
chemin disque) reste dans ``app.tools.documents``.
"""

from __future__ import annotations

import csv
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.core.errors import ValidationError

__all__ = [
    "ENCODAGES",
    "MAX_LIGNES_APERCU",
    "MAX_LIGNES_PLAGE",
    "MAX_OCTETS_CSV",
    "SEPARATEURS",
    "TableCsv",
    "charger_csv",
    "lire_csv",
    "lire_csv_plage",
    "lire_element_csv",
    "structure_csv",
    "valeurs_ligne",
]

#: Séparateurs acceptés. Volontairement fermé : un séparateur inventé
#: (``::``, espace) produit un tableau à une colonne, donc une lecture fausse
#: silencieuse au lieu d'un refus lisible.
SEPARATEURS = (",", ";", "\t", "|")

#: Encodages acceptés, dans l'ordre de tentative (même politique que
#: ``app.documents.extraction.text`` qui lit déjà les fichiers texte).
ENCODAGES = ("utf-8-sig", "cp1252")

#: Lignes rendues par défaut par ``lire_csv`` (aperçu) et plafond absolu.
MAX_LIGNES_APERCU = 40
MAX_LIGNES_PLAGE = 200

#: Garde-fou de parcours, pas de chargement mémoire : au-delà, un fichier n'est
#: plus un export exploitable par un agent (et le parcours prendrait des
#: minutes). La mémoire, elle, reste bornée : seules les lignes demandées sont
#: conservées.
MAX_OCTETS_CSV = 512 * 1024 * 1024

#: Octets examinés pour détecter l'encodage et le séparateur.
_ECHANTILLON_DETECTION = 65536

#: Longueur maximale d'une valeur de cellule rendue au modèle.
_MAX_CELLULE = 200

#: Défauts de forme signalés au plus (une ligne par défaut, pas un roman).
_MAX_PROBLEMES = 6


@dataclass(frozen=True, slots=True)
class TableCsv:
    """Un CSV parcouru : ses noms de colonnes, les lignes gardées, ses défauts.

    ``lignes`` ne contient **que** les lignes demandées (en-tête comprise
    lorsque ``garder`` l'accepte) : un appel qui n'a besoin que de l'aperçu ne
    conserve pas les 100 000 lignes du fichier. ``total_lignes`` est le compte
    exact du fichier, en-tête comprise, obtenu pendant le même parcours.
    """

    separateur: str
    encodage: str
    entete: list[str]
    lignes: list[list[str]]
    noms: list[str]
    problemes: list[str]
    total_lignes: int = 0
    #: Numéro de ligne du fichier (1 = en-tête) de chaque entrée de ``lignes``.
    #: Les lignes non gardées ne comptent pas : ce sont bien les repères réels.
    numeros: list[int] = field(default_factory=list)


def _decoder_entete(octets: bytes, encodage: str | None) -> tuple[str, str]:
    """Texte de tête et encodage retenu, sans échouer sur un caractère coupé.

    L'échantillon peut se terminer au milieu d'un caractère multi-octets :
    ce n'est pas un fichier mal encodé, c'est une lecture partielle. La partie
    décodable est donc conservée telle quelle, uniquement pour détecter le
    séparateur — le fichier complet sera ensuite lu avec l'encodage retenu.

    Raises:
        ValidationError: encodage demandé hors liste, ou échantillon non
            décodable avec les encodages acceptés.
    """
    if encodage:
        propre = str(encodage).strip().lower()
        if propre not in ENCODAGES:
            raise ValidationError(
                f"Encodage non pris en charge : {encodage!r}",
                details={"encodages_acceptes": list(ENCODAGES)},
            )
        candidats: tuple[str, ...] = (propre,)
    else:
        candidats = ENCODAGES
    for candidat in candidats:
        try:
            return octets.decode(candidat), candidat
        except UnicodeDecodeError as erreur:
            # Coupure sur le dernier caractère : on garde ce qui précède.
            if erreur.start >= len(octets) - 4:
                try:
                    return octets[: erreur.start].decode(candidat), candidat
                except UnicodeDecodeError:
                    continue
            continue
    raise ValidationError(
        "Fichier CSV non décodable (ni UTF-8 ni cp1252)",
        details={"encodages_acceptes": list(ENCODAGES)},
    )


def _detecter_separateur(texte: str, demande: str | None) -> str:
    """Séparateur du fichier : celui demandé, sinon celui mesuré.

    ``csv.Sniffer`` échoue sur un fichier d'une seule ligne ou à guillemets
    incohérents : le repli compte alors les séparateurs de l'en-tête, ce qui
    reste déterministe et vérifiable dans le retour.

    Raises:
        ValidationError: séparateur demandé hors liste.
    """
    if demande not in (None, ""):
        propre = str(demande).strip()
        if propre in ("\\t", "tab", "tabulation"):
            propre = "\t"
        if propre not in SEPARATEURS:
            raise ValidationError(
                f"Séparateur non pris en charge : {demande!r}",
                details={"separateurs_acceptes": list(SEPARATEURS)},
            )
        return propre

    echantillon = texte[:_ECHANTILLON_DETECTION]
    try:
        return csv.Sniffer().sniff(echantillon, delimiters="".join(SEPARATEURS)).delimiter
    except csv.Error:
        premiere = echantillon.splitlines()[0] if echantillon.splitlines() else ""
        compte = {separateur: premiere.count(separateur) for separateur in SEPARATEURS}
        meilleur = max(compte, key=lambda separateur: compte[separateur])
        return meilleur if compte[meilleur] else ","


def _noms_colonnes(entete: list[str], largeur: int) -> tuple[list[str], list[str]]:
    """Noms de colonnes utilisables comme clés, et défauts de forme constatés.

    Une colonne sans en-tête reçoit ``colonne_<n>`` (l'agent peut quand même la
    citer) ; un en-tête en double est suffixé, sinon deux colonnes
    s'écraseraient dans le dictionnaire d'une ligne et une valeur disparaîtrait.
    """
    noms: list[str] = []
    problemes: list[str] = []
    for position in range(largeur):
        brut = entete[position] if position < len(entete) else ""
        base = brut or f"colonne_{position + 1}"
        if not brut:
            problemes.append(
                f"colonne {position + 1} sans en-tête : nommée '{base}' dans ce retour"
            )
        nom = base
        suffixe = 2
        while nom in noms:
            nom = f"{base}_{suffixe}"
            suffixe += 1
        if nom != base:
            problemes.append(
                f"en-tête en double '{base}' (colonne {position + 1}) : renommé '{nom}'"
            )
        noms.append(nom)
    return noms, problemes


def _cellule(valeur: str) -> str:
    """Valeur rendue au modèle : espaces normalisés, longueur bornée et dite."""
    propre = " ".join(str(valeur or "").split())
    if len(propre) <= _MAX_CELLULE:
        return propre
    return propre[:_MAX_CELLULE - 1] + "…"


def valeurs_ligne(noms: list[str], ligne: list[str]) -> dict[str, str]:
    """Une ligne en dictionnaire ``{nom de colonne: valeur}``.

    Une ligne plus courte que l'en-tête rend des chaînes vides au lieu de
    décaler les valeurs : une cellule manquante ne doit pas se lire comme la
    valeur de la colonne suivante.
    """
    return {
        nom: _cellule(ligne[position] if position < len(ligne) else "")
        for position, nom in enumerate(noms)
    }


def _ligne_vide(ligne: list[str]) -> bool:
    """Vrai pour une ligne sans aucune valeur (fin de fichier ou ligne blanche)."""
    return not any((cellule or "").strip() for cellule in ligne)


def charger_csv(
    chemin: Path,
    *,
    separateur: str | None = None,
    encodage: str | None = None,
    garder: Callable[[int], bool] | None = None,
    sur_entete: Callable[[list[str]], None] | None = None,
    sur_ligne: Callable[[int, dict[str, str]], None] | None = None,
) -> TableCsv:
    """Parcourt un CSV en flux, en gardant seulement les lignes demandées.

    Args:
        chemin: fichier à lire (chemin déjà contrôlé par le service).
        separateur: séparateur imposé ; ``None`` le fait détecter.
        encodage: encodage imposé ; ``None`` essaie UTF-8 puis cp1252.
        garder: prédicat sur le numéro de ligne du fichier (1 = en-tête) ;
            ``None`` conserve toutes les lignes. Les lignes non retenues ne
            sont jamais stockées.
        sur_entete: rappel appelé une fois les noms de colonnes connus, avant
            les lignes : une recherche y résout sa colonne cible une seule fois.
        sur_ligne: rappel appelé pour chaque ligne de données non vide, dans
            l'ordre du fichier, avec ses valeurs en dictionnaire. Permet une
            recherche sans conserver le fichier.

    Returns:
        La table : en-tête, noms de colonnes, lignes gardées et total exact.

    Raises:
        ValidationError: fichier illisible, vide, trop gros pour un parcours,
            encodage ou séparateur hors liste.
    """
    try:
        taille = chemin.stat().st_size
    except OSError as erreur:
        raise ValidationError("CSV illisible") from erreur
    if taille > MAX_OCTETS_CSV:
        raise ValidationError(
            f"CSV trop volumineux pour un parcours d'agent ({taille} octets, "
            f"{MAX_OCTETS_CSV} au plus)",
            # Limite d'environnement : relancer le même appel ne peut pas
            # réussir — l'outil le dit au lieu de laisser le modèle essayer.
            details={
                "octets": taille,
                "plafond_octets": MAX_OCTETS_CSV,
                "action": "ne_pas_reessayer",
            },
        )

    try:
        with chemin.open("rb") as binaire:
            echantillon = binaire.read(_ECHANTILLON_DETECTION)
    except OSError as erreur:
        raise ValidationError("CSV illisible") from erreur
    if not echantillon:
        raise ValidationError("CSV vide : aucune ligne à lire")

    _texte_entete, encodage_retenu = _decoder_entete(echantillon, encodage)
    separateur_retenu = _detecter_separateur(_texte_entete, separateur)

    # ``newline=""`` : c'est ``csv`` qui gère les fins de ligne, sinon un champ
    # entre guillemets contenant un retour à la ligne est coupé en deux lignes.
    try:
        with chemin.open("r", encoding=encodage_retenu, newline="") as flux:
            lecteur = csv.reader(flux, delimiter=separateur_retenu)
            premiere = next(lecteur, None)
            if premiere is None or _ligne_vide(premiere):
                raise ValidationError("CSV vide : aucune ligne à lire")

            entete = [" ".join(cellule.split()) for cellule in premiere]
            largeur = len(entete)
            noms, problemes = _noms_colonnes(entete, largeur)
            if sur_entete is not None:
                sur_entete(noms)
            gardees: list[list[str]] = [premiere] if garder is None or garder(1) else []
            numeros: list[int] = [1] if gardees else []
            total = 1
            ecarts = 0
            signales = 0
            for numero, ligne in enumerate(lecteur, start=2):
                if _ligne_vide(ligne):
                    # Une ligne sans valeur n'est pas une donnée. Le numéro de
                    # ligne du fichier reste celui du lecteur : les repères des
                    # lignes suivantes ne glissent pas.
                    continue
                total += 1
                if len(ligne) != largeur:
                    ecarts += 1
                    if signales < _MAX_PROBLEMES:
                        signales += 1
                        problemes.append(
                            f"ligne {numero} : {len(ligne)} cellule(s) pour "
                            f"{largeur} colonne(s)"
                        )
                if sur_ligne is not None:
                    sur_ligne(numero, valeurs_ligne(noms, ligne))
                if garder is None or garder(numero):
                    gardees.append(ligne)
                    numeros.append(numero)
            if ecarts > signales:
                problemes.append(
                    f"… et {ecarts - signales} autre(s) ligne(s) de largeur "
                    "inégale (séparateur mal détecté ? passer 'separateur')"
                )
    except csv.Error as erreur:
        raise ValidationError(
            f"CSV illisible avec le séparateur {separateur_retenu!r}",
            details={"separateur": separateur_retenu},
        ) from erreur
    except UnicodeDecodeError as erreur:
        raise ValidationError(
            "Fichier CSV non décodable avec l'encodage détecté",
            details={"encodage": encodage_retenu},
        ) from erreur

    return TableCsv(
        separateur=separateur_retenu,
        encodage=encodage_retenu,
        entete=entete,
        lignes=gardees,
        noms=noms,
        problemes=problemes,
        total_lignes=total,
        numeros=numeros,
    )


def _plafond(valeur: Any, defaut: int, maximum: int, champ: str) -> int:
    """Plafond borné côté serveur (``None`` → valeur par défaut)."""
    if valeur in (None, ""):
        return defaut
    try:
        demande = int(valeur)
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"{champ} doit être un entier") from exc
    return max(1, min(demande, maximum))


def _entrees(
    lignes: list[list[str]], noms: list[str], numeros: list[int]
) -> list[dict[str, Any]]:
    """Lignes gardées (hors en-tête) en ``{ligne, valeurs}``, numérotées.

    Le numéro vient de ``numeros`` (repère réel du fichier), jamais de la
    position dans la liste : une ligne non gardée ne doit pas décaler la
    citation des suivantes.
    """
    return [
        {"ligne": numero, "valeurs": valeurs_ligne(noms, ligne)}
        for numero, ligne in zip(numeros, lignes, strict=True)
        if numero > 1
    ]


def lire_csv(
    chemin: Path,
    *,
    separateur: str | None = None,
    encodage: str | None = None,
    max_lignes: Any = None,
) -> dict[str, Any]:
    """Aperçu structuré d'un CSV : colonnes, dimensions, premières lignes.

    Le fichier est parcouru en flux : seules les lignes de l'aperçu sont
    conservées. L'aperçu ne tient pas lieu de lecture exhaustive : les lignes
    utiles se trouvent par ``chercher_dans_document`` (mot-clé, colonne) et se
    relisent par ``lire_element_document`` (``type_element='ligne'``).

    Raises:
        ValidationError: CSV illisible, vide, encodage ou séparateur refusé.
    """
    plafond = _plafond(max_lignes, MAX_LIGNES_APERCU, MAX_LIGNES_PLAGE, "max_lignes")
    table = charger_csv(
        chemin,
        separateur=separateur,
        encodage=encodage,
        garder=lambda numero: numero <= 1 + plafond,
    )
    total = table.total_lignes
    apercu = _entrees(table.lignes[: 1 + plafond], table.noms, table.numeros[: 1 + plafond])
    return {
        "format": "csv",
        "separateur": table.separateur,
        "encodage": table.encodage,
        "colonnes": table.noms,
        "entete": table.entete,
        "nb_colonnes": len(table.noms),
        "nb_lignes": total,
        "nb_donnees": max(0, total - 1),
        "apercu_lignes": apercu,
        "tronque": max(0, total - 1) > len(apercu),
        "problemes": table.problemes,
        "lecture_en_flux": True,
    }


def lire_csv_plage(
    chemin: Path,
    *,
    from_row: int | None = None,
    to_row: int | None = None,
    separateur: str | None = None,
    encodage: str | None = None,
) -> dict[str, Any]:
    """Lit une plage de **lignes** d'un CSV (1-indexée, bornes incluses).

    La ligne 1 est l'en-tête, comme dans le fichier. Sans ``to_row``, la plage
    s'arrête au plafond (``MAX_LIGNES_PLAGE``) et ``tronque`` le dit : lire
    « jusqu'à la fin » d'un export de 5 000 lignes noierait le contexte.

    Raises:
        ValidationError: CSV illisible ou plage inversée.
    """
    try:
        debut = 1 if from_row is None else max(1, int(from_row))
        fin_demandee = None if to_row is None else max(1, int(to_row))
    except (TypeError, ValueError) as exc:
        raise ValidationError("from_row/to_row doivent être des entiers") from exc
    if fin_demandee is not None and debut > fin_demandee:
        raise ValidationError(
            "Plage invalide",
            details={"from_row": debut, "to_row": fin_demandee},
        )
    # Sans borne explicite, la plage s'arrête au plafond ; une borne explicite
    # est honorée (elle reste bornée par le fichier). Même contrat qu'avant.
    fin_plafond = debut + MAX_LIGNES_PLAGE - 1
    fin_limite = fin_plafond if fin_demandee is None else fin_demandee

    table = charger_csv(
        chemin,
        separateur=separateur,
        encodage=encodage,
        garder=lambda numero: numero == 1 or debut <= numero <= fin_limite,
    )
    total = table.total_lignes
    fin = min(total, fin_limite)
    if debut > fin:
        raise ValidationError(
            "Plage hors document",
            details={"from_row": debut, "to_row": fin_demandee, "total": total},
        )
    # ``numeros`` porte le repère réel de chaque ligne conservée : la ligne 1
    # est l'en-tête, et les lignes non gardées ne décalent rien.
    numerotees = [
        {"ligne": numero, "valeurs": valeurs_ligne(table.noms, ligne)}
        for numero, ligne in zip(table.numeros, table.lignes, strict=True)
        if debut <= numero <= fin
    ]
    return {
        "format": "csv",
        "unite": "lignes",
        "debut": debut,
        "fin": fin,
        "total": total,
        "from_row": debut,
        "to_row": fin,
        "separateur": table.separateur,
        "encodage": table.encodage,
        "colonnes": table.noms,
        "tronque": fin < total,
        "lignes": numerotees,
        "problemes": table.problemes,
        "lecture_en_flux": True,
        # Contrat commun aux plages : un texte tabulé est toujours lisible,
        # même quand l'appelant ne connaît pas le format.
        "texte": "\n".join(
            " | ".join(f"{nom}={valeur}" for nom, valeur in entree["valeurs"].items())
            for entree in numerotees
        ),
    }


def lire_element_csv(
    chemin: Path,
    *,
    type_element: Any,
    index: Any = None,
    separateur: str | None = None,
    encodage: str | None = None,
) -> dict[str, Any]:
    """Lit **une** ligne d'un CSV (``element`` et ``ligne`` sont le même repère).

    Raises:
        ValidationError: type d'élément inconnu, index absent ou hors fichier.
    """
    propre = str(type_element or "ligne").strip().lower()
    if propre not in ("element", "ligne"):
        raise ValidationError(
            f"Type d'élément inconnu pour un CSV : {type_element!r} "
            "(un CSV n'a qu'une table : 'ligne' ou 'element' ; "
            "l'aperçu complet se lit avec lire_document, une plage avec "
            "read_document_range)",
            details={"types_elements": ["element", "ligne"], "format": "csv"},
        )
    if index is None:
        raise ValidationError(
            "index est obligatoire (1-indexé, ligne du fichier) pour une ligne CSV",
            details={"champ": "index"},
        )
    try:
        numero = int(index)
    except (TypeError, ValueError) as exc:
        raise ValidationError("index doit être un entier (1-indexé)") from exc
    if numero < 1:
        raise ValidationError(
            "index doit être supérieur ou égal à 1 (1-indexé)", details={"index": numero}
        )

    table = charger_csv(
        chemin,
        separateur=separateur,
        encodage=encodage,
        garder=lambda position: position == 1 or position == numero,
    )
    total = table.total_lignes
    if numero > total:
        raise ValidationError(
            f"Index {numero} hors document : {total} ligne(s) au total",
            details={"index": numero, "total": total, "type_element": "ligne"},
        )
    trouvee = next(
        (ligne for position, ligne in zip(table.numeros, table.lignes, strict=True)
         if position == numero),
        None,
    )
    if trouvee is None:
        raise ValidationError(
            f"Index {numero} hors document : {total} ligne(s) au total",
            details={"index": numero, "total": total, "type_element": "ligne"},
        )
    valeurs = valeurs_ligne(table.noms, trouvee)
    return {
        "format": "csv",
        "type_element": "ligne",
        "index": numero,
        "unite": "lignes",
        "total": total,
        "separateur": table.separateur,
        "encodage": table.encodage,
        "colonnes": table.noms,
        "valeurs": valeurs,
        "texte": " | ".join(f"{nom}={valeur}" for nom, valeur in valeurs.items()),
        # Ligne 1 = en-tête : le dire évite que l'agent prenne les noms de
        # colonnes pour une donnée (et cite un bénéficiaire nommé « Nom »).
        "est_entete": numero == 1,
        "lecture_en_flux": True,
    }


def structure_csv(
    chemin: Path,
    *,
    separateur: str | None = None,
    encodage: str | None = None,
    max_lignes: Any = 200,
) -> dict[str, Any]:
    """Structure d'un CSV : colonnes, exemples, comptages, dimensions.

    Utile avant toute lecture : l'agent sait quelles colonnes existent, ce
    qu'elles contiennent vraiment (un exemple non vide) et combien de lignes
    le fichier porte. L'échantillon est borné et son caractère partiel est dit
    dans ``echantillon_tronque`` : un exemple n'est pas une statistique.

    Raises:
        ValidationError: CSV illisible ou vide.
    """
    plafond = _plafond(max_lignes, 200, 1000, "max_lignes")
    table = charger_csv(
        chemin,
        separateur=separateur,
        encodage=encodage,
        garder=lambda numero: numero <= 1 + plafond,
    )
    exemples: dict[str, str] = {nom: "" for nom in table.noms}
    non_vides: dict[str, int] = {nom: 0 for nom in table.noms}
    for ligne in table.lignes[1:]:
        valeurs = valeurs_ligne(table.noms, ligne)
        for nom, valeur in valeurs.items():
            if valeur:
                non_vides[nom] += 1
                if not exemples[nom]:
                    exemples[nom] = valeur[:80]
    echantillon = max(0, min(plafond, table.total_lignes - 1))
    return {
        "format": "csv",
        "separateur": table.separateur,
        "encodage": table.encodage,
        "entete": table.entete,
        "colonnes": [
            {
                "nom": nom,
                "exemple": exemples[nom],
                "valeurs_non_vides_sur_echantillon": non_vides[nom],
            }
            for nom in table.noms
        ],
        "nb_colonnes": len(table.noms),
        "nb_lignes": table.total_lignes,
        "nb_donnees": max(0, table.total_lignes - 1),
        "echantillon_lignes": echantillon,
        "echantillon_tronque": max(0, table.total_lignes - 1) > echantillon,
        "apercu_lignes": _entrees(table.lignes[:6], table.noms, table.numeros[:6]),
        "problemes": table.problemes,
        "lecture_en_flux": True,
    }
