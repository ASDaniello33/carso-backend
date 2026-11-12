"""Tools documentaires pour agents (Lot 1, pipeline [D2] ; ADR 0005).

Rôle unique—donner aux agents un accès **contrôlé** à la lecture, l'analyse,
la génération, la validation et l'inspection visuelle des documents. La
frontière HITL (human-in-the-loop) reste intacte :

- ``generer_document`` produit les octets (``app.documents.renderers``), les
  dépose via ``DocumentService.enregistrer_document`` avec ``proposed_by_agent``
  => statut ``proposed`` (règle 6 [C]—jamais ``approved`` depuis un agent) ;
- ``lire_document`` / ``get_document_structure`` / ``read_document_range`` /
  ``chercher_dans_document`` / ``lire_element_document`` délèguent à
  ``DocumentService`` pour la lecture (``telecharger`` / ``extraire_texte``),
  sans contourner le stockage contrôlé (jamais de chemin disque manipulé par
  l'agent) ; ``chercher_dans_document`` est la recherche ciblée qui remplace
  le shell (éléments + extraits bornés au lieu d'une lecture intégrale) et
  chaque hit porte ``relire``—les arguments exacts de
  ``lire_element_document`` pour le relire à l'identique ;
- ``analyze_document_reference`` rend le **plan** et le **style** d'un document
  de référence (jamais son texte—on réutilise une identité visuelle, on ne
  recopie pas un contenu) ;
- ``valider_document`` vérifie une structure avant ou après génération ;
- ``render_document`` rend les pages d'un PDF en images—l'agent (et
  l'utilisateur) voient le **rendu réel**, pas une promesse.

Enchaînement attendu pour un livrable :
``analyze_document_reference → generer_document(document_spec) → valider_document
→ render_document → (corriger) → présenter``.

Le tool ne porte aucune règle métier—il valide son entrée, vérifie la
permission, délègue (AGENTS.md §2.5—Agent → Tool → Service → Repository).

Une ancre métier **réelle** range le fichier sous l'objet correspondant.
Une ancre absente, inconnue ou inventée range le document sous
``workdir/{compte}/{id}/`` (type ``non_classe``)—aucun UUID n'est inventé.

Depuis ce découpage, le module est un paquet : les schémas d'entrée vivent dans
``app.tools.documents.schemas``, la fabrique et ses helpers dans
``app.tools.documents.fabrique``. Le chemin d'import ne change pas : les
appelants continuent d'écrire ``from app.tools.documents import ...``.
"""

from __future__ import annotations

__all__ = [
    "BUDGET_STRUCTURE_CARACTERES",
    "build_document_tools",
    "APERCU_EXTRACTION",
    "APERCU_TEXTE",
    "ChercherDocumentInput",
    "CibleLecture",
    "ComposerImageInput",
    "CopierModeleDocumentInput",
    "CorrigerDocumentInput",
    "GenererDocumentHtmlInput",
    "GenererDocumentInput",
    "GenererDocumentProfessionnelInput",
    "GenererImageInput",
    "GenererImageSvgInput",
    "GraphiqueInput",
    "IllustrationInput",
    "ImporterTableauInput",
    "InspecterDocumentInput",
    "ItemIllustrationInput",
    "LireDocumentInput",
    "LireElementInput",
    "LireImageInput",
    "LirePlageInput",
    "ListerModelesDocumentsInput",
    "MAX_ELEMENTS_PAR_LECTURE",
    "MAX_MOTS_CLES_PAR_RECHERCHE",
    "MAX_PLAGES_PAR_LECTURE",
    "ModeleAncre",
    "ModeleRefInput",
    "ModeleSegmentTexte",
    "ModifierSectionDocumentInput",
    "ObtenirStructureDocumentInput",
    "ObtenirStructureModeleInput",
    "PlageLueInput",
    "ReferenceDocumentInput",
    "RemplacerElementModeleInput",
    "RemplirModeleInput",
    "RemplissageModeleInput",
    "RenderDocumentInput",
    "RenommerDocumentInput",
    "RevenirVersionDocumentInput",
    "SerieGraphiqueInput",
    "SupprimerDocumentInput",
    "TypeAncre",
    "TypeElement",
    "TypeFormat",
    "TypePortee",
    "TypeProfil",
    "TypeStyle",
    "ValiderDocumentInput",
    "VerifierImageSvgInput",
]

from app.tools.documents.fabrique import (
    build_document_tools,
)
from app.tools.documents.lecture import (
    BUDGET_STRUCTURE_CARACTERES,
)
from app.tools.documents.lecture import _structure_feuilles_xlsx as _structure_feuilles_xlsx
from app.tools.documents.redaction import _specs_graphiques as _specs_graphiques
from app.tools.documents.schemas import _ANCRES as _ANCRES
from app.tools.documents.schemas import _BORNE_SECTION_CARACTERES as _BORNE_SECTION_CARACTERES
from app.tools.documents.schemas import _FORMATS as _FORMATS
from app.tools.documents.schemas import (
    APERCU_EXTRACTION,
    APERCU_TEXTE,
    MAX_ELEMENTS_PAR_LECTURE,
    MAX_MOTS_CLES_PAR_RECHERCHE,
    MAX_PLAGES_PAR_LECTURE,
    ChercherDocumentInput,
    CibleLecture,
    ComposerImageInput,
    CopierModeleDocumentInput,
    CorrigerDocumentInput,
    GenererDocumentHtmlInput,
    GenererDocumentInput,
    GenererDocumentProfessionnelInput,
    GenererImageInput,
    GenererImageSvgInput,
    GraphiqueInput,
    IllustrationInput,
    ImporterTableauInput,
    InspecterDocumentInput,
    ItemIllustrationInput,
    LireDocumentInput,
    LireElementInput,
    LireImageInput,
    LirePlageInput,
    ListerModelesDocumentsInput,
    ModeleAncre,
    ModeleRefInput,
    ModeleSegmentTexte,
    ModifierSectionDocumentInput,
    ObtenirStructureDocumentInput,
    ObtenirStructureModeleInput,
    PlageLueInput,
    ReferenceDocumentInput,
    RemplacerElementModeleInput,
    RemplirModeleInput,
    RemplissageModeleInput,
    RenderDocumentInput,
    RenommerDocumentInput,
    RevenirVersionDocumentInput,
    SerieGraphiqueInput,
    SupprimerDocumentInput,
    TypeAncre,
    TypeElement,
    TypeFormat,
    TypePortee,
    TypeProfil,
    TypeStyle,
    ValiderDocumentInput,
    VerifierImageSvgInput,
)
