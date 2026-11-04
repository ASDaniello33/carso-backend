"""Découpage d'un corps HTML en sections — édition ciblée d'un livrable.

Un corps de ``generer_document_professionnel`` est une suite de blocs de premier
niveau : ``<section class="cover">``, ``<nav class="toc">``, un ``<section>``
par partie, un ``<div class="page-break">``, une ``<section class="slide">`` par
diapositive d'un deck. Corriger **une** partie ne demande donc pas de réécrire
les autres : on découpe le corps, on remplace un morceau, on réassemble.

Règles assumées :

- un morceau commence à un élément de premier niveau et **absorbe** le texte qui
  le suit (les retours à la ligne entre sections ne sont jamais perdus) ;
- un fragment de remplacement est normalisé puis re-parsé : il doit contenir
  **un seul élément racine** (une section, un tableau, un paragraphe…) — deux
  racines signifient que l'agent a envoyé la moitié du document, et le dire vaut
  mieux que de deviner où couper ;
- la numérotation des sections est celle du corps stocké (1-based), celle que
  ``lister`` renvoie : l'agent ne recalcule jamais un index à partir du texte lu.
"""

from __future__ import annotations

from bs4 import BeautifulSoup, Tag

from app.core.errors import ValidationError

__all__ = [
    "TITRE_MAX",
    "assembler",
    "decouper",
    "decrire",
    "normaliser_fragment",
]

#: Longueur maximale du titre repris dans un descriptif de section.
TITRE_MAX = 120


#: Un élément de premier niveau qui **ouvre** une section : une partie du
#: document commence à une ``<section>``, un sommaire (``<nav>``), un titre ou
#: un filet. Les autres éléments (paragraphe, tableau, liste, figure, encadré)
#: appartiennent à la section en cours : découper à chaque balise séparerait un
#: titre de son paragraphe et rendrait la correction inutilisable.
_OUVRANTS = frozenset({"section", "nav", "article", "header", "h1", "h2", "h3", "hr"})


def decouper(corps: str) -> list[str]:
    """Découpe un corps HTML en sections (ordre et contenu conservés)."""
    soup = BeautifulSoup(corps or "", "html.parser")
    morceaux: list[str] = []
    courant: list[str] = []
    for enfant in soup.contents:
        if isinstance(enfant, Tag) and (enfant.name in _OUVRANTS or not courant):
            if courant:
                morceaux.append("".join(courant))
            courant = [str(enfant)]
        else:
            courant.append(str(enfant))
    if courant:
        morceaux.append("".join(courant))
    # Un morceau fait uniquement d'espaces n'est pas une section (il reste
    # collé au morceau précédent quand il en suit un).
    return [morceau for morceau in morceaux if morceau.strip()]


def assembler(morceaux: list[str]) -> str:
    """Réassemble les morceaux — le corps complet attendu par le rendu."""
    return "".join(morceaux)


def _titre_du_morceau(soupe: BeautifulSoup) -> str:
    for balise in ("h1", "h2", "h3"):
        titre = soupe.find(balise)
        if titre is not None:
            texte = " ".join(titre.get_text(" ", strip=True).split())
            if texte:
                return texte[:TITRE_MAX]
    return ""


def decrire(morceaux: list[str]) -> list[dict[str, object]]:
    """Décrit chaque section : index, balise, classes, titre, taille.

    Le titre vient du premier ``h1``/``h2``/``h3`` : c'est ce que l'agent et
    l'utilisateur reconnaissent dans le document (« Budget prévisionnel »),
    jamais un identifiant opaque.
    """
    descriptions: list[dict[str, object]] = []
    for index, morceau in enumerate(morceaux, start=1):
        soupe = BeautifulSoup(morceau, "html.parser")
        racine = next(
            (enfant for enfant in soupe.contents if isinstance(enfant, Tag)), None
        )
        classes: list[str] = []
        if racine is not None:
            valeur = racine.get("class") or []
            classes = list(valeur) if isinstance(valeur, list) else str(valeur).split()
        descriptions.append(
            {
                "index": index,
                "balise": racine.name if racine is not None else "",
                "classes": classes,
                "titre": _titre_du_morceau(soupe),
                "caracteres": len(morceau),
            }
        )
    return descriptions


def normaliser_fragment(html: str, *, format_document: str) -> str:
    """Valide et normalise le HTML d'**une** section à insérer.

    Raises:
        ValidationError: fragment vide, plusieurs racines, ou (pour un deck)
            racine qui n'est pas une ``<section>`` — un deck est une suite de
            diapositives, insérer un paragraphe nu casserait tout le fichier.
    """
    corps = (html or "").strip()
    if not corps:
        raise ValidationError(
            "La section à insérer est vide : fournir le HTML d'UNE section "
            "(titre, paragraphes, tableaux…)."
        )
    soupe = BeautifulSoup(corps, "html.parser")
    racines = [enfant for enfant in soupe.contents if isinstance(enfant, Tag)]
    if not racines:
        raise ValidationError(
            "La section fournie ne contient aucun élément HTML : écrire la "
            "section (par exemple <section><h2>Titre</h2><p>Texte</p></section>)."
        )
    # Une section peut légitimement être plusieurs éléments (« <h1>Titre</h1> »
    # suivi de son paragraphe, d'un tableau…), mais elle n'ouvre qu'UNE partie :
    # deux titres ou deux sections signifient que l'agent a envoyé la moitié du
    # document, et le dire vaut mieux que de deviner où couper.
    ouvrants = [racine for racine in racines if racine.name in _OUVRANTS]
    if format_document == "pptx":
        if len(racines) != 1 or racines[0].name != "section":
            libelles = ", ".join(f"<{racine.name}>" for racine in racines[:5])
            raise ValidationError(
                "Une diapositive est UNE <section class='slide'> (ou 'cover') : "
                f"la section fournie contient {len(racines)} racine(s) "
                f"({libelles}). Envelopper le contenu dans une section de "
                "diapositive.",
                details={"racines": len(racines)},
            )
    elif len(ouvrants) > 1:
        libelles = ", ".join(f"<{racine.name}>" for racine in ouvrants[:5])
        raise ValidationError(
            f"La section fournie ouvre {len(ouvrants)} parties ({libelles}) : "
            "une section = UNE partie (un seul titre, ou une seule <section>). "
            "Pour corriger plusieurs parties, faire un appel par section — le "
            "reste du document est conservé tel quel.",
            details={"ouvrants": len(ouvrants)},
        )
    return "".join(str(racine) for racine in racines)
