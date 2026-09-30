"""Renderer HTML — ``DocumentSpec`` → page autonome (ADR 0005, Phase 8).

Le HTML n'est pas un livrable de CARSO : c'est l'aperçu instantané d'un
document structuré (débogage, revue rapide, retour visuel dans le chat) et la
démonstration que le style est bien **dans les tokens**, pas dans un renderer.

Caractéristiques :

- **autonome** : CSS intégré (``DocumentStyle.to_css``) et images encodées en
  ``data:`` — un seul fichier à ouvrir, aucune ressource externe ;
- **fidèle à la sémantique** : hiérarchie de titres, ``thead``/``tbody``,
  listes, encadrés, citations, sauts de page (``page-break-after``) ;
- **aucune dépendance** : HTML construit avec ``html.escape``, jamais de moteur
  de gabarit à installer.
"""

from __future__ import annotations

import base64
import html as html_lib
from typing import Any

from app.documents.assets import FournisseurImages, resoudre_image
from app.documents.spec import (
    MARQUEUR_A_COMPLETER,
    MARQUEUR_A_CONFIRMER,
    MARQUEUR_RE,
    Bloc,
    Citation,
    Couverture,
    DocumentSpec,
    Encadre,
    Image,
    Liste,
    Paires,
    Paragraphe,
    SautDePage,
    Tableau,
    Titre,
)
from app.documents.styles import DocumentStyle

__all__ = ["rendre_html", "rendre_html_texte"]

_ALIGNEMENTS = {"gauche": "left", "centre": "center", "droite": "right", "justifie": "justify"}


def rendre_html(spec: DocumentSpec, *, images: FournisseurImages | None = None) -> bytes:
    """Rend un ``DocumentSpec`` en page HTML autonome (UTF-8)."""
    return rendre_html_texte(spec, images=images).encode("utf-8")


def rendre_html_texte(spec: DocumentSpec, *, images: FournisseurImages | None = None) -> str:
    """Rend un ``DocumentSpec`` en HTML (chaîne), CSS et images inclus."""
    style = spec.style_effectif()
    geometrie = spec.geometrie()
    css = style.to_css(
        largeur_mm=geometrie.largeur_mm,
        hauteur_mm=geometrie.hauteur_mm,
        marges=geometrie.marges,
    )
    morceaux: list[str] = [
        "<!DOCTYPE html>",
        '<html lang="fr">',
        "<head>",
        '<meta charset="utf-8">',
        f"<title>{_echapper(spec.metadata.titre)}</title>",
        f"<style>\n{css}\n</style>",
        "</head>",
        "<body>",
    ]
    couverture = spec.couverture()
    if couverture is not None:
        morceaux.extend(_couverture(spec, couverture, images))
    for bloc in spec.blocs:
        morceaux.extend(_bloc(bloc, images, style))
    if spec.mise_en_page.mention_proposition:
        morceaux.append(
            '<p class="mention-proposition" style="font-size:8.5pt;color:#6B7280;">'
            f"{_echapper(spec.mise_en_page.mention_proposition)}</p>"
        )
    morceaux.extend(["</body>", "</html>", ""])
    return "\n".join(morceaux)


def _couverture(
    spec: DocumentSpec, couverture: Couverture, images: FournisseurImages | None
) -> list[str]:
    """Page de couverture en HTML (logo, titre, champs, mention)."""
    tokens = spec.style_effectif().couverture
    interieur: list[str] = ['<div class="couverture">']
    if couverture.logo_document_id is not None or couverture.logo_chemin:
        donnees = _image_data_uri(
            Image(
                document_id=couverture.logo_document_id,
                chemin=couverture.logo_chemin,
                largeur_mm=tokens.logo_largeur_mm,
            ),
            images,
        )
        if donnees:
            interieur.append(
                f'<img alt="Logo" src="{donnees}" style="width:{tokens.logo_largeur_mm}mm">'
            )
    interieur.append(
        f'<div class="titre">{_echapper(couverture.titre or spec.metadata.titre)}</div>'
    )
    sous_titre = couverture.sous_titre or spec.metadata.sous_titre
    if sous_titre:
        interieur.append(
            f'<p style="font-size:{tokens.sous_titre_taille_pt}pt">'
            f"{_echapper(sous_titre)}</p>"
        )
    organisation = couverture.organisation or spec.metadata.organisation
    if organisation:
        interieur.append(f"<p><strong>{_echapper(organisation)}</strong></p>")
    for paire in couverture.champs:
        interieur.append(
            f"<p>{_echapper(paire.libelle)} : {_echapper(paire.valeur)}</p>"
        )
    if couverture.date_ligne:
        interieur.append(f"<p><em>{_echapper(couverture.date_ligne)}</em></p>")
    if spec.mise_en_page.mention_proposition:
        interieur.append(
            f'<p style="font-size:8.5pt;color:#6B7280"><em>'
            f"{_echapper(spec.mise_en_page.mention_proposition)}</em></p>"
        )
    interieur.extend(['</div>', '<div class="saut-de-page"></div>'])
    return interieur


def _bloc(
    bloc: Bloc, images: FournisseurImages | None, style: DocumentStyle
) -> list[str]:
    """Rend un bloc du spec en HTML."""
    if isinstance(bloc, Titre):
        niveau = min(4, max(2, bloc.niveau + 1))  # h1 est réservé au titre du document
        return [
            f"<h{niveau}>{_echapper(bloc.texte)}{_marqueur_html(bloc.texte, bloc.statut)}"
            f"</h{niveau}>"
        ]
    if isinstance(bloc, Paragraphe):
        return [
            f"<p>{_echapper(bloc.texte)}{_marqueur_html(bloc.texte, bloc.statut)}</p>"
        ]
    if isinstance(bloc, Liste):
        balise = "ol" if bloc.numerotee else "ul"
        items = "".join(
            f"<li>{_echapper(item)}{_marqueur_html(item, bloc.statut)}</li>"
            for item in bloc.items
        )
        return [f"<{balise}>{items}</{balise}>"]
    if isinstance(bloc, Tableau):
        return _tableau(bloc)
    if isinstance(bloc, Image):
        return _image(bloc, images)
    if isinstance(bloc, Encadre):
        tokens = style.encadre(bloc.genre)
        libelle = bloc.titre or tokens.libelle
        entete = f"<strong>{_echapper(libelle)} — </strong>" if libelle else ""
        return [
            f'<div class="encadre" style="border-color:{tokens.bordure};'
            f'background:{tokens.fond};color:{tokens.couleur_texte}">'
            f"{entete}{_echapper(bloc.texte)}</div>"
        ]
    if isinstance(bloc, Citation):
        attribution = (
            f"<footer>— {_echapper(bloc.attribution)}</footer>" if bloc.attribution else ""
        )
        return [
            f"<blockquote>{_echapper(bloc.texte)}{attribution}</blockquote>"
        ]
    if isinstance(bloc, Paires):
        lignes = "".join(
            f"<tr><th>{_echapper(paire.libelle)}</th><td>{_echapper(paire.valeur)}</td></tr>"
            for paire in bloc.paires
        )
        return [f'<table class="paires">{lignes}</table>']
    if isinstance(bloc, SautDePage):
        return ['<div class="saut-de-page"></div>']
    return []


def _tableau(bloc: Tableau) -> list[str]:
    """Tableau HTML : ``thead`` si un en-tête est déclaré, fusions et trames.

    Aucun en-tête n'est inventé : sans ``entete``/``entetes``, le tableau n'a
    pas de ``thead`` (donc pas de bandeau vide).
    """
    grille = bloc.grille()
    nb_entete = bloc.nb_lignes_entete()
    morceaux: list[str] = []
    if bloc.titre_tableau:
        morceaux.append(f"<p><strong>{_echapper(bloc.titre_tableau)}</strong></p>")
    morceaux.append("<table>")
    if bloc.largeurs_mm:
        morceaux.append("<colgroup>")
        for largeur in bloc.largeurs_mm:
            morceaux.append(f'<col style="width:{largeur}mm">')
        morceaux.append("</colgroup>")
    if nb_entete:
        morceaux.append("<thead>")
        for rangees in grille[:nb_entete]:
            morceaux.append(
                _ligne_tableau(bloc, rangees, balise="th", en_entete=True, total=False)
            )
        morceaux.append("</thead>")
    morceaux.append("<tbody>")
    for offset, rangees in enumerate(grille[nb_entete:]):
        total = bloc.total_ligne is not None and offset == bloc.total_ligne
        morceaux.append(
            _ligne_tableau(bloc, rangees, balise="td", en_entete=False, total=total)
        )
    morceaux.extend(["</tbody>", "</table>"])
    return morceaux


def _ligne_tableau(
    bloc: Tableau,
    rangees: list[Any],
    *,
    balise: str,
    en_entete: bool,
    total: bool,
) -> str:
    """Une ligne HTML : balise de cellule, ``colspan``/``rowspan``, style de cellule."""
    cellules: list[str] = []
    for placee in rangees:
        attributs: list[str] = []
        if placee.colonnes > 1:
            attributs.append(f'colspan="{placee.colonnes}"')
        if placee.lignes > 1:
            attributs.append(f'rowspan="{placee.lignes}"')
        styles: list[str] = []
        alignement = placee.cellule.alignement
        if alignement is None and bloc.alignements and placee.colonne < len(bloc.alignements):
            alignement = bloc.alignements[placee.colonne]
        if alignement:
            styles.append(f"text-align:{_ALIGNEMENTS[alignement]}")
        if placee.cellule.fond:
            styles.append(f"background:{placee.cellule.fond}")
        gras = placee.cellule.gras if placee.cellule.gras is not None else total
        if gras:
            styles.append("font-weight:bold")
        if placee.cellule.couleur_texte:
            styles.append(f"color:{placee.cellule.couleur_texte}")
        if styles:
            attributs.append(f'style="{ ";".join(styles) }"')
        suffixe = " " + " ".join(attributs) if attributs else ""
        cellules.append(
            f"<{balise}{suffixe}>{_echapper(placee.cellule.texte)}</{balise}>"
        )
    return f"<tr>{''.join(cellules)}</tr>"


def _image(bloc: Image, images: FournisseurImages | None) -> list[str]:
    """Image en ``data:`` (ou description si elle est introuvable)."""
    donnees = _image_data_uri(bloc, images)
    if donnees is None:
        return [f'<p><em>[Image indisponible : {_echapper(bloc.alt or "")}]</em></p>']
    legende = f"<figcaption>{_echapper(bloc.legende)}</figcaption>" if bloc.legende else ""
    return [
        f'<figure><img alt="{_echapper(bloc.alt or bloc.legende or "")}" '
        f'src="{donnees}" style="width:{bloc.largeur_mm}mm">{legende}</figure>'
    ]


def _image_data_uri(bloc: Image, images: FournisseurImages | None) -> str | None:
    """Encapsule les octets d'une image en ``data:`` (HTML autonome)."""
    if images is None:
        return None
    contenu = resoudre_image(bloc, images)
    if contenu is None:
        return None
    octets, nom = contenu
    mime = _mime(nom)
    return f"data:{mime};base64,{base64.b64encode(octets).decode('ascii')}"


def _mime(nom: str) -> str:
    """Type MIME déduit de l'extension du fichier image."""
    extension = nom.rsplit(".", 1)[-1].lower() if "." in nom else "png"
    return {
        "png": "image/png",
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "gif": "image/gif",
        "bmp": "image/bmp",
        "webp": "image/webp",
        "svg": "image/svg+xml",
    }.get(extension, "image/png")


def _marqueur_html(texte: str, statut: str) -> str:
    """Marqueur visible d'une donnée absente ou à confirmer."""
    if MARQUEUR_RE.search(texte):
        return ""
    if statut in ("inconnu", "manquant"):
        return f" <em>{_echapper(MARQUEUR_A_COMPLETER)}</em>"
    if statut == "a_confirmer":
        return f" <em>{_echapper(MARQUEUR_A_CONFIRMER)}</em>"
    return ""


def _echapper(texte: Any) -> str:
    """Échappe un texte pour HTML (jamais de balise injectée depuis un document)."""
    return html_lib.escape(str(texte), quote=True)
