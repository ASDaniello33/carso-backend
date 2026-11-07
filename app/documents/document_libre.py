"""Document **libre** : HTML sémantique + classes CARSO, CSS de l'agent (INC-137).

``generer_document`` produisait jusqu'ici deux choses très différentes selon les
champs fournis (titre/paragraphes/tableau, ou un ``document_spec`` énorme), avec
deux moteurs de rendu : le résultat dépendait du chemin, et les erreurs de schéma
étaient fréquentes. Le même outil accepte désormais ce que les modèles savent
écrire : **du HTML**.

Le contrat est celui de ``generer_document_professionnel`` — HTML + graphiques
SVG ancrés — avec une différence : pas de couverture ni de sommaire imposés, et
une **feuille de style de classes** que l'agent peut reprendre, surcharger ou
ignorer pour écrire sa propre mise en forme.

Trois garanties tenues par le serveur, dans l'ordre du besoin exprimé :

1. **typographie uniforme** — les tailles en ``pt`` sont alignées sur une échelle
   unique (8, 9, 11, 13, 16, 20, 26) et les ``px`` convertis ; un document ne
   mélange pas six corps de texte, et l'alignement est **annoncé** ;
2. **images par lien** — un ``<img src="https://…">`` est récupéré, vérifié,
   réduit puis **embarqué** (data URI) : le fichier produit ne dépend plus du
   réseau, et une URL morte laisse un repère visible au lieu d'un trou ;
3. **pas de tiret cadratin** — « — » est remplacé par « - » dans le texte, et le
   remplacement est signalé (la consigne est aussi donnée aux agents).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html import escape

__all__ = [
    "CLASSES_DOCUMENT_LIBRE",
    "FEUILLE_DE_STYLE",
    "ECHELLE_TYPOGRAPHIQUE",
    "ResultatDocumentLibre",
    "habiller",
    "normaliser_css",
    "preparer_corps",
    "rendre_document_libre",
    "sans_tirets_cadratins",
]

#: Échelle typographique unique du document (points). Toute taille en ``pt``
#: écrite par l'agent est alignée sur la plus proche : c'est ce qui garantit
#: l'uniformité sans lui retirer le choix du corps.
ECHELLE_TYPOGRAPHIQUE: tuple[float, ...] = (8.0, 9.0, 11.0, 13.0, 16.0, 20.0, 26.0)

#: Correspondance px → pt (96 dpi, convention CSS).
_PX_EN_PT = 0.75

#: Classes documentées, reprises par la feuille de style et par le prompt.
CLASSES_DOCUMENT_LIBRE: dict[str, str] = {
    "h1": "Titre de niveau 1 (page de titre, grande section)",
    "h2": "Titre de niveau 2 (section)",
    "h3": "Titre de niveau 3 (sous-section)",
    "h4": "Titre de niveau 4",
    "h5": "Titre de niveau 5",
    "h6": "Titre de niveau 6 (le plus petit)",
    "p": "Paragraphe de corps (justifié)",
    "introduction": "Chapeau : paragraphe d'ouverture, plus grand, non justifié",
    "bold": "Texte en gras (dans un paragraphe)",
    "italic": "Texte en italique",
    "souligne": "Texte souligné",
    "reference": "Référence, note de bas de sens, mention légale (petit, gris)",
    "liste": "Liste à puces (ul)",
    "liste-numerotee": "Liste numérotée (ol)",
    "tableau": "Tableau de données (en-tête gris)",
    "entete": "Cellule d'en-tête d'un tableau",
    "total": "Ligne de total d'un tableau (gras, fond léger)",
    "encadre": "Encadré d'information (bord gauche coloré)",
    "note": "Note secondaire (texte gris)",
    "avertissement": "Encadré d'avertissement (bord gauche rouge)",
    "citation": "Citation (italique, retrait)",
    "legende": "Légende d'image ou de tableau (petit, centré)",
    "image": "Image centrée, largeur maximale",
    "image-large": "Image pleine largeur",
    "image-indisponible": "Repère affiché quand une image distante n'a pas pu être chargée",
    "signature": "Bloc de signature en fin de document",
    "date": "Date ou lieu, aligné à droite",
    "saut-page": "Saut de page",
}

#: Feuille de style du document libre. Les tailles sont exprimées en ``em`` pour
#: que le corps choisi par l'agent (``body { font-size: … }``) se propage à toute
#: la hiérarchie : la liberté porte sur **une** décision, pas sur vingt.
FEUILLE_DE_STYLE = """
/* --- Feuille de style du document libre : classes CARSO ------------------- */
body.document { font-family: "Calibri", "Segoe UI", Arial, sans-serif;
            font-size: 11pt; color: #1a1a1a; line-height: 1.45; }
.h1 { font-size: 2.0em; font-weight: bold; color: #0b1f33;
                margin: 0 0 6pt 0; line-height: 1.15; }
.h2 { font-size: 1.45em; font-weight: bold; color: #0b1f33;
                margin: 14pt 0 5pt 0; line-height: 1.2; }
.h3 { font-size: 1.2em; font-weight: bold; color: #1f3b57;
                margin: 11pt 0 4pt 0; }
.h4 { font-size: 1.05em; font-weight: bold; color: #1f3b57;
                margin: 9pt 0 3pt 0; }
.h5 { font-size: 1em; font-weight: bold; color: #333333; margin: 7pt 0 2pt 0; }
.h6 { font-size: 0.95em; font-weight: bold; font-style: italic;
                color: #444444; margin: 6pt 0 2pt 0; }
.p { font-size: 1em; text-align: justify; margin: 0 0 6pt 0; }
.introduction { font-size: 1.15em; color: #33414f; margin: 0 0 10pt 0; }
.bold { font-weight: bold; }
.italic { font-style: italic; }
.souligne { text-decoration: underline; }
.reference { font-size: 0.8em; color: #555555; line-height: 1.3; }
.note { font-size: 0.9em; color: #555555; }
.liste { margin: 0 0 6pt 0; padding-left: 18pt; }
.liste-numerotee { margin: 0 0 6pt 0; padding-left: 18pt; }
.tableau { width: 100%; border-collapse: collapse; margin: 8pt 0 10pt 0;
                     font-size: 0.92em; }
.tableau th, .tableau .entete { background-color: #eaf0f6;
                     font-weight: bold; text-align: left; padding: 4pt; }
.tableau td { padding: 4pt; border-bottom: 1px solid #d5dde5; }
.tableau tr.total td { font-weight: bold; background-color: #f4f7fa; }
.encadre { border-left: 3px solid #0070d8; background-color: #f2f7fd;
                     padding: 6pt 8pt; margin: 8pt 0; }
.avertissement { border-left: 3px solid #c0392b; background-color: #fdf3f2;
                     padding: 6pt 8pt; margin: 8pt 0; }
.citation { font-style: italic; color: #33414f; margin: 8pt 18pt;
                      border-left: 2px solid #c9d4de; padding-left: 8pt; }
.legende { font-size: 0.8em; color: #555555; text-align: center; margin: 2pt 0 8pt 0; }
.image { display: block; margin: 8pt auto; max-width: 100%; }
.image-large { display: block; margin: 8pt 0; width: 100%; }
.image-indisponible { font-size: 0.85em; color: #a03024;
                     border: 1px dashed #c0392b; padding: 4pt; }
.signature { margin-top: 18pt; }
.date { text-align: right; margin: 10pt 0 0 0; }
.saut-page { page-break-after: always; }
"""

#: Sélecteurs qui produisent un saut de page en DOCX (pas de CSS page-break).
_CLASSES_SAUT_PAGE = {"saut-page"}


@dataclass(slots=True)
class ResultatDocumentLibre:
    """Document libre rendu : octets, format, pagination et avertissements."""

    octets: bytes
    format: str
    pages: int | None = None
    avertissements: list[str] = field(default_factory=list)


def sans_tirets_cadratins(corps: str) -> tuple[str, int]:
    """Remplace « — » et « – » par « - » dans le **texte** (jamais dans le HTML).

    Le tiret cadratin est une signature typographique de modèle de langage : il
    rend un document administratif daté. Le remplacement est compté pour être
    annoncé, et les attributs (``class``, ``href``) ne sont pas touchés.
    """
    remplacements = 0

    def _remplacer(morceau: re.Match[str]) -> str:
        nonlocal remplacements
        texte = morceau.group(0)
        nouveau = texte.replace("—", "-").replace("–", "-")
        if nouveau != texte:
            remplacements += texte.count("—") + texte.count("–")
        return nouveau

    # Le texte hors balises : on découpe sur les balises pour ne pas toucher
    # aux attributs.
    parties = re.split(r"(<[^>]+>)", corps or "")
    sortie = []
    for partie in parties:
        if partie.startswith("<"):
            sortie.append(partie)
        else:
            sortie.append(re.sub(r"[^<]+", _remplacer, partie))
    return "".join(sortie), remplacements


def normaliser_css(css: str | None) -> tuple[str, list[str]]:
    """Aligne les tailles de police du CSS de l'agent sur l'échelle unique.

    Les valeurs relatives (``em``, ``%``, ``rem``) sont laissées telles quelles :
    elles s'appliquent à la taille de base choisie, donc à l'échelle. Les valeurs
    absolues (``pt``, ``px``) sont converties puis alignées sur
    ``ECHELLE_TYPOGRAPHIQUE`` ; chaque alignement est annoncé.

    Returns:
        Le CSS normalisé et les phrases d'avertissement (vide si rien n'a bougé).
    """
    texte = str(css or "")
    if not texte.strip():
        return "", []
    avertissements: list[str] = []

    def _aligner(morceau: re.Match[str]) -> str:
        valeur = morceau.group(1).strip().lower()
        nombre, unite = _lire_taille(valeur)
        if nombre is None or unite not in {"pt", "px"}:
            return morceau.group(0)
        if unite == "px":
            # Le document se mesure en points : une taille en pixels n'a pas le
            # même sens à l'écran et à l'impression. La conversion est annoncée.
            converti = nombre * _PX_EN_PT
            alignee = min(ECHELLE_TYPOGRAPHIQUE, key=lambda pas: abs(pas - converti))
            avertissements.append(
                f"Taille de police en pixels convertie et alignée sur l'échelle du "
                f"document : {valeur} → {alignee:g}pt."
            )
            return f"font-size: {alignee:g}pt"
        alignee = min(ECHELLE_TYPOGRAPHIQUE, key=lambda pas: abs(pas - nombre))
        if abs(alignee - nombre) < 0.3:
            return f"font-size: {alignee:g}pt"
        avertissements.append(
            f"Taille de police alignée sur l'échelle du document : "
            f"{valeur} → {alignee:g}pt."
        )
        return f"font-size: {alignee:g}pt"

    normalise = re.sub(
        r"font-size\s*:\s*([^;}]+)", _aligner, texte, flags=re.IGNORECASE
    )
    return normalise, avertissements


def _lire_taille(valeur: str) -> tuple[float | None, str]:
    """``(nombre, unité)`` d'une taille CSS, ou ``(None, "")`` si illisible."""
    correspondance = re.match(r"^(-?\d*\.?\d+)\s*(pt|px|em|rem|%)?$", valeur.strip())
    if not correspondance:
        return None, ""
    nombre = float(correspondance.group(1))
    return nombre, (correspondance.group(2) or "pt").lower()


def preparer_corps(
    corps: str,
    *,
    graphiques: dict | None = None,
    illustrations: dict | None = None,
) -> tuple[str, list[str]]:
    """Nettoie le corps HTML : figures, images distantes, tirets cadratins.

    Returns:
        Le corps prêt à être habillé et les avertissements cumulés.
    """
    from app.documents.generation_html import integrer_figures
    from app.documents.images_distantes import ImageDistanteRefusee, recuperer_image

    avertissements: list[str] = []
    corps, cadratins = sans_tirets_cadratins(corps)
    if cadratins:
        avertissements.append(
            f"{cadratins} tiret(s) cadratin(s) remplacé(s) par « - » : le document "
            "n'utilise pas ce caractère."
        )

    corps = integrer_figures(corps, graphiques, illustrations)
    corps, images = _inliner_images(corps, recuperer_image, ImageDistanteRefusee)
    avertissements.extend(images)
    return corps, avertissements


def _inliner_images(corps: str, recuperer, refus) -> tuple[str, list[str]]:
    """Remplace chaque ``<img src=http…>`` par un data URI, ou par un repère."""
    from bs4 import BeautifulSoup

    avertissements: list[str] = []
    soup = BeautifulSoup(corps or "", "html.parser")
    for balise in soup.find_all("img"):
        source = str(balise.get("src") or "").strip()
        if not source.lower().startswith(("http://", "https://")):
            continue
        try:
            image = recuperer(source)
        except refus as erreur:
            avertissements.append(str(erreur))
            repere = soup.new_tag("span", attrs={"class": "image-indisponible"})
            repere.string = f"[Image non chargée : {source}]"
            balise.replace_with(repere)
            continue
        balise["src"] = image.data_uri
        classes = balise.get("class") or []
        if isinstance(classes, str):
            classes = classes.split()
        if "image" not in classes:
            balise["class"] = [*classes, "image"]
    return str(soup), avertissements


def habiller(
    corps: str,
    *,
    css: str | None = None,
    titre: str = "Document",
    auteur: str = "",
) -> str:
    """Assemble une page HTML complète : classes CARSO puis CSS de l'agent.

    L'ordre compte : la feuille de l'agent vient **après** celle du serveur, donc
    elle peut reprendre une classe (couleur, police, taille relative) sans avoir
    à la réécrire.
    """
    css_agent, _ = normaliser_css(css)
    return (
        '<!DOCTYPE html><html lang="fr"><head><meta charset="utf-8">'
        f"<title>{escape(titre)}</title>"
        f'<meta name="author" content="{escape(auteur)}">'
        f"<style>{FEUILLE_DE_STYLE}</style>"
        + (f"<style>{css_agent}</style>" if css_agent.strip() else "")
        + f'</head><body class="document">{corps}</body></html>'
    )


def rendre_document_libre(
    corps: str,
    *,
    format: str,
    css: str | None = None,
    titre: str = "Document",
    auteur: str = "",
    graphiques: dict | None = None,
    illustrations: dict | None = None,
) -> ResultatDocumentLibre:
    """Rend le corps HTML en DOCX ou PDF, avec la mise en forme demandée.

    - **DOCX** : conversion native HTML → Word (``html_to_docx``) — les classes,
      couleurs, polices et tailles sont appliquées ; les SVG sont rasterisés en
      PNG (Word ne lit pas le SVG).
    - **PDF** : rendu HTML réel par Chromium quand il est disponible (fidélité
      CSS complète) ; sinon repli sur l'extraction de structure du moteur CARSO,
      **annoncé** dans les avertissements (mise en forme non conservée).

    Raises:
        ValidationError: corps vide, format hors vocabulaire.
        GenerationError: échec du moteur (Chromium indisponible puis repli
            impossible, SVG refusé).
    """
    from app.core.errors import ValidationError

    cle = str(format or "docx").strip().lower()
    if cle not in {"docx", "pdf"}:
        raise ValidationError(
            f"Format inconnu pour un document libre : {format!r} (docx ou pdf)."
        )
    if not str(corps or "").strip():
        raise ValidationError("Le document est vide : fournir un corps HTML.")

    corps_prepare, avertissements = preparer_corps(
        corps, graphiques=graphiques, illustrations=illustrations
    )
    css_normalise, avertissements_typo = normaliser_css(css)
    avertissements.extend(avertissements_typo)
    html_complet = habiller(
        corps_prepare, css=css_normalise, titre=titre, auteur=auteur
    )

    if cle == "docx":
        from app.documents.generation_html import embarquer_svg_pour_docx
        from app.documents.html_to_docx import html_string_to_docx_bytes

        octets = html_string_to_docx_bytes(embarquer_svg_pour_docx(html_complet))
        return ResultatDocumentLibre(
            octets=octets, format=cle, avertissements=avertissements
        )

    octets, pages, repli = _rendre_pdf_libre(
        corps_prepare, css_normalise, titre=titre, auteur=auteur
    )
    if repli:
        avertissements.append(repli)
    return ResultatDocumentLibre(
        octets=octets, format=cle, pages=pages, avertissements=avertissements
    )


def _rendre_pdf_libre(
    corps: str, css: str, *, titre: str, auteur: str
) -> tuple[bytes, int | None, str | None]:
    """PDF par Chromium (fidèle) ; repli structurel annoncé s'il est absent."""
    try:
        from app.documents.professionnel.html_builder import preparer
        from app.documents.professionnel.pdf_renderer import navigateur_disponible

        disponible, _raison = navigateur_disponible()
        if disponible:
            import tempfile
            from pathlib import Path

            from app.documents.professionnel.pdf_renderer import rendre_pdf

            document = preparer(corps)
            with tempfile.TemporaryDirectory(prefix="carso_libre_") as dossier:
                chemin = Path(dossier) / "document.pdf"
                pages = rendre_pdf(
                    document,
                    chemin,
                    theme="corporate",
                    titre=titre,
                    auteur=auteur,
                    css_supplementaire=FEUILLE_DE_STYLE + "\n" + css,
                )
                return chemin.read_bytes(), pages, None
    except Exception:  # noqa: BLE001 - le repli explicite vaut mieux qu'une panne
        pass

    from app.documents.generation_html import convertir_html_bytes

    octets = convertir_html_bytes(
        habiller(corps, css=css, titre=titre, auteur=auteur), format="pdf"
    )
    return (
        octets,
        None,
        "PDF rendu par le moteur structurel CARSO (Chromium indisponible) : le "
        "contenu est fidèle, la mise en forme CSS ne l'est pas. Installer "
        "l'extra documents_pro pour un PDF identique au HTML.",
    )
