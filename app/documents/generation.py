"""Génération documentaire — adaptateurs d'octets DOCX / XLSX / PDF (Lots L0 → T2).

Rôle dans le pipeline [D2] : produire les **octets** d'un document. Depuis
l'ADR 0005, le chemin DOCX/PDF passe par la représentation structurée
(``app.documents.spec``) et les renderers (``app.documents.renderers``) : il
n'existe donc plus qu'**un seul** chemin de style, partagé par la génération
historique et par les documents composés par les agents.

Les signatures publiques ``build_docx`` / ``build_xlsx`` / ``build_pdf`` et la
façade ``GenerateurDocument`` sont **inchangées** : les appelants existants
(service documentaire, tools, tests) continuent de fonctionner. Ce sont désormais
des adaptateurs minces : entrée minimale (titre, paragraphes, tableau) →
``DocumentSpec`` avec le style CARSO par défaut → renderer.

L'enregistrement officiel reste du ressort de ``DocumentService`` (statut
``proposed``, validation humaine) : ce module ne touche ni la base ni le stockage.

Convention des extras (``pyproject.toml``) : ``python-docx``, ``openpyxl`` et
``fpdf2`` vivent dans l'extra ``documents``. Une librairie absente lève une
``GenerationError`` explicite via ``require_library`` — jamais un fichier vide ni
un échec silencieux.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from app.core.errors import GenerationError
from app.documents.renderers import rendre
from app.documents.spec import (
    Couverture,
    DocumentSpec,
    Metadonnees,
    MiseEnPage,
    Paragraphe,
    Tableau,
    Titre,
)
from app.documents.styles import CARSO_DEFAUT, DocumentStyle

__all__ = [
    "build_docx",
    "build_xlsx",
    "build_pdf",
    "GenerateurDocument",
    "spec_document_simple",
]


def _texte(valeur: Any) -> str:
    """Normalise une valeur en texte affichable (``None`` → chaîne vide)."""
    if valeur is None:
        return ""
    if isinstance(valeur, Decimal):
        return f"{valeur:f}"
    return str(valeur)


def spec_document_simple(
    *,
    titre: str,
    paragraphes: list[str] | None = None,
    tableau: list[list[Any]] | None = None,
    style: DocumentStyle | None = None,
) -> DocumentSpec:
    """Compose un ``DocumentSpec`` minimal (titre, paragraphes, tableau).

    Utilisé par les adaptateurs historiques et par tout appelant qui n'a qu'un
    contenu simple à déposer. La première ligne du tableau est traitée comme son
    en-tête — convention déjà en vigueur (Lot L0).
    """
    blocs: list[Any] = [Titre(niveau=1, texte=titre)]
    for paragraphe in paragraphes or []:
        texte = _texte(paragraphe)
        if texte.strip():
            blocs.append(Paragraphe(texte=texte))
    if tableau:
        entete = [_texte(valeur) for valeur in tableau[0]]
        lignes = [[_texte(valeur) for valeur in ligne] for ligne in tableau[1:]]
        blocs.append(Tableau(entete=entete, lignes=lignes or [[""]]))
    # Les adaptateurs historiques gardent leur mise en page : titre + corps sur la
    # même page, **sans** couverture. Un document composé passant par un spec
    # explicite (offres, rapports) utilise la couverture du design system.
    return DocumentSpec(
        metadata=Metadonnees(titre=titre),
        mise_en_page=MiseEnPage(couverture=Couverture(active=False)),
        style=style or CARSO_DEFAUT,
        blocs=blocs,
    )


def build_docx(
    *,
    titre: str,
    paragraphes: list[str] | None = None,
    tableau: list[list[Any]] | None = None,
    style: DocumentStyle | None = None,
) -> bytes:
    """Construit un DOCX (titre + paragraphes + tableau optionnel).

    Args:
        titre: titre du document (premier titre de niveau 1).
        paragraphes: paragraphes de corps, dans l'ordre.
        tableau: lignes du tableau ; la première ligne est l'en-tête.
        style: design system à appliquer (défaut : ``CARSO_DEFAUT``).

    Raises:
        GenerationError: python-docx indisponible ou échec de construction.
    """
    spec = spec_document_simple(
        titre=titre, paragraphes=paragraphes, tableau=tableau, style=style
    )
    return rendre(spec, format="docx")


def build_pdf(
    *,
    titre: str,
    paragraphes: list[str] | None = None,
    tableau: list[list[Any]] | None = None,
    style: DocumentStyle | None = None,
) -> bytes:
    """Construit un PDF (titre + paragraphes + tableau optionnel) via fpdf2.

    Args:
        titre: titre du document (exigé, non vide).
        paragraphes: paragraphes de corps, dans l'ordre.
        tableau: lignes du tableau ; la première ligne est l'en-tête.
        style: design system à appliquer (défaut : ``CARSO_DEFAUT``).

    Raises:
        GenerationError: fpdf2 indisponible (extra ``documents``) ou échec moteur.
    """
    if not titre.strip():
        raise GenerationError("Un PDF exige un titre non vide")
    spec = spec_document_simple(
        titre=titre, paragraphes=paragraphes, tableau=tableau, style=style
    )
    return rendre(spec, format="pdf")


def build_xlsx(
    *,
    titre_feuille: str,
    lignes: list[list[Any]],
) -> bytes:
    """Construit un classeur XLSX d'une feuille (liste de bénéficiaires type).

    La signature est celle des appelants historiques (fiche de présence, listes),
    mais le rendu passe désormais par le **renderer XLSX** — donc par le même
    style que tout autre classeur : en-tête coloré et gras, lignes alternées,
    bordures, largeurs calculées, en-tête gelé et filtre automatique. Avant
    l'incrément 9, ce chemin produisait un classeur brut (openpyxl sans style)
    alors que ``rendre(spec, format="xlsx")`` n'existait pas encore.

    Args:
        titre_feuille: nom de l'onglet unique.
        lignes: lignes brutes ; la première ligne est l'en-tête.

    Raises:
        GenerationError: openpyxl indisponible ou échec de construction.
    """
    from app.documents.renderers.xlsx import rendre_tableur

    return rendre_tableur(
        titre_feuille=titre_feuille,
        lignes=[list(ligne) for ligne in lignes],
        style=CARSO_DEFAUT,
    )


class GenerateurDocument:
    """Façade unique : format → renderer. Sélection explicite, pas de magie.

    Exemple : ``GenerateurDocument().pdf(titre=..., tableau=[...])``.
    """

    def docx(self, **kwargs: Any) -> bytes:
        """Délègue à :func:`build_docx`."""
        return build_docx(**kwargs)

    def xlsx(self, **kwargs: Any) -> bytes:
        """Délègue à :func:`build_xlsx`."""
        return build_xlsx(**kwargs)

    def pdf(self, **kwargs: Any) -> bytes:
        """Délègue à :func:`build_pdf`."""
        return build_pdf(**kwargs)

    def spec(self, **kwargs: Any) -> bytes:
        """Rend un ``DocumentSpec`` déjà composé (format explicite requis)."""
        spec = kwargs.pop("spec", None)
        if not isinstance(spec, DocumentSpec):
            raise GenerationError("``spec`` doit être un DocumentSpec")
        return rendre(spec, **kwargs)
