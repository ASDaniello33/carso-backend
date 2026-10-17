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
``generated/{id}/`` (type ``non_classe``)—aucun UUID n'est inventé.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from app.application.dto import DocumentUploadInput
from app.core.errors import ValidationError
from app.documents.repair import Operation
from app.documents.spec import DocumentSpec
from app.tools.permissions import AgentIdentity, PermissionPolicy

__all__ = [
    "TypeAncre",
    "TypeFormat",
    "TypePortee",
    "TypeProfil",
    "TypeStyle",
    "CorrigerDocumentInput",
    "GenererDocumentInput",
    "GenererDocumentHtmlInput",
    "GenererDocumentProfessionnelInput",
    "GraphiqueInput",
    "ImporterTableauInput",
    "SerieGraphiqueInput",
    "LireDocumentInput",
    "InspecterDocumentInput",
    "LirePlageInput",
    "PlageLueInput",
    "ChercherDocumentInput",
    "LireElementInput",
    "ClonerDocumentInput",
    "RemplirTemplateInput",
    "ReferenceDocumentInput",
    "ValiderDocumentInput",
    "RenderDocumentInput",
    "build_document_tools",
]

#: Vocabulaire fermé des formats : le modèle ne peut pas inventer ``word`` ou ``exe``.
TypeFormat = Literal["docx", "xlsx", "pdf"]

#: Vocabulaire fermé des ancres métier réelles (doit rester aligné sur ``_ANCRES``).
TypeAncre = Literal[
    "organisation",
    "appel_a_proposition",
    "offre",
    "mission",
    "equipe",
    "session",
]

#: Styles documentaires nommés (doit rester aligné sur ``STYLES_DISPONIBLES``).
TypeStyle = Literal["carso_defaut", "carso_sobre", "carso_institutionnel"]

#: Profils de livrable connus (doit rester aligné sur ``profiles.PROFILS``).
TypeProfil = Literal[
    "offre_technique",
    "offre_financiere",
    "offre_autre",
    "fiche_technique",
    "fiche_presence",
    "checklist",
    "rapport",
    "courrier",
]

#: Portées de validation documentaire.
TypePortee = Literal["structure", "contenu", "style"]

#: Éléments lisibles par ``lire_element_document`` (aligné sur
#: ``app.documents.elements.TYPES_ELEMENTS`` — un type hors format est refusé
#: avec la liste des types valides).
TypeElement = Literal[
    "element",
    "paragraphe",
    "tableau",
    "entete",
    "pied",
    "page",
    "ligne",
    "feuille",
]

#: Caractères d'aperçu rendus par ``lire_document`` (le contexte reste borné).
APERCU_TEXTE = 4_000
#: Plafond d'extraction pour cet aperçu (borne le travail disque/CPU).
APERCU_EXTRACTION = 20_000

#: Index lus par un appel groupé de ``lire_element_document`` (au-delà : refus
#: explicite, jamais une troncature silencieuse — même règle que ``read_urls``).
MAX_ELEMENTS_PAR_LECTURE = 20
#: Mots-clés fouillés par un appel groupé de ``chercher_dans_document`` (chaque
#: mot-clé garde son propre plafond ``max_resultats``).
MAX_MOTS_CLES_PAR_RECHERCHE = 8
#: Plages lues par un appel groupé de ``read_document_range``. Plus serré que
#: les éléments : une plage peut être longue, 8 plages bornent déjà le
#: déversement (chaque plage garde son propre retour unitaire).
MAX_PLAGES_PAR_LECTURE = 8

_ANCRES: dict[str, str] = {
    "organisation": "organisation_id",
    "appel_a_proposition": "appel_a_proposition_id",
    "offre": "offre_id",
    "mission": "mission_id",
    "equipe": "equipe_id",
    "session": "session_id",
}

_FORMATS: dict[str, tuple[str, str]] = {
    "docx": (".docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    "xlsx": (".xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    "pdf": (".pdf", "application/pdf"),
}

#: Le PPTX n'existe que sur la voie professionnelle (``format='pptx'`` du tool
#: ``generer_document_professionnel``) : ``TypeFormat`` du tool structuré ne le
#: propose pas, sa génération DOCX/XLSX/PDF n'a pas de deck natif.
_FORMAT_PPTX: tuple[str, str] = (
    ".pptx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
)


class GenererDocumentInput(BaseModel):
    """Entrée typée : format, nom, ancre optionnelle, contenu minimal générique.

    Le contenu est volontairement **générique** (listes de lignes) : c'est de
    la mise en forme, pas une règle métier. Les cas d'usage spécialisés (offre
    de formation, fiche de présence) composeront leurs propres outils à partir
    de ``build_docx``/``build_pdf`` — ce tool générique reste la porte commune.
    """

    format: TypeFormat = Field(description="Format cible : 'docx' | 'xlsx' | 'pdf'.")
    nom_fichier: str = Field(
        description="Nom logique du document (extension ajoutée selon le format)."
    )
    type_document: str = Field(description="Vocabulaire TypeDocument (valide par le service).")
    ancre: TypeAncre | None = Field(
        default=None,
        description=(
            "Rattachement optionnel : 'organisation' | 'appel_a_proposition' | "
            "'offre' | 'mission' | 'equipe' | 'session'. "
            "Omettre si aucun objet métier n'est identifié — ne pas inventer."
        ),
    )
    entity_id: UUID | None = Field(
        default=None,
        description=(
            "UUID de l'objet métier d'ancrage, uniquement si l'ancre est réelle. "
            "Ne jamais inventer d'identifiant."
        ),
    )
    propose_par: str = Field(
        description="Identifiant de l'agent proposant (ex. 'agent_generaliste')."
    )
    titre: str | None = Field(
        default=None,
        description=(
            "Titre du document — pour une génération simple, sans ``document_spec``."
        ),
    )
    paragraphes: list[str] | None = Field(
        default=None, description="Paragraphes de corps (docx / pdf)."
    )
    tableau: list[list[Any]] | None = Field(
        default=None,
        description=(
            "Génération simple uniquement : lignes du tableau, la première ligne "
            "étant l'en-tête. Pour un tableau nommé, fusionné ou mis en évidence, "
            "utiliser ``document_spec`` (bloc 'tableau')."
        ),
    )
    document_spec: DocumentSpec | None = Field(
        default=None,
        description=(
            "Document structuré — chemin attendu pour un vrai livrable. "
            "Forme : {metadata: {titre (requis), sous_titre, reference, "
            "organisation, destinataire, auteur, date_document (AAAA-MM-JJ), objet, "
            "type_document}, blocs: [ ... ], sources: [{genre: document_source | "
            "base_de_donnees | saisie_utilisateur | modele_reference | "
            "information_derivee | hypothese, reference, precision}], "
            "mise_en_page: {format: A4|Letter, orientation: portrait|paysage, "
            "marges, couverture: {active, titre, sous_titre, logo_document_id, "
            "champs: [{libelle, valeur}]}}}. "
            "Chaque bloc porte un champ 'type' qui choisit sa forme : "
            "titre {niveau: 1-4, texte} | paragraphe {texte, role: corps|chapeau|note} | "
            "liste {items: [texte], numerotee, profondeur: 0-3} | "
            "tableau {entete: [texte] OU entetes: [[cellule]], lignes: [[cellule]], "
            "largeurs_mm: [float], alignements, total_ligne, repeter_entete} | "
            "image {document_id ou chemin, largeur_mm, legende} | "
            "encadre {genre: info|note|avertissement|succes, titre, texte} | "
            "citation {texte, attribution} | paires {paires: [{libelle, valeur}], "
            "colonnes: 1-3} | saut_de_page {}. "
            "Une cellule de tableau est soit un texte ('Mois 1'), soit un objet "
            "{texte, fusion_colonnes (entier ; 'fin' = jusqu'à la dernière colonne), "
            "fusion_lignes, fond ('#RRGGBB'), gras, alignement: gauche|centre|droite, "
            "couleur_texte}. RÈGLE DE TABLEAU : dès que les colonnes ont un sens, "
            "fournir 'entete' (une ligne) ou 'entetes' (plusieurs lignes d'en-tête "
            "fusionnées) ; sans en-tête déclaré la première ligne de 'lignes' est "
            "une DONNÉE, jamais un titre de colonne. Une ligne de section pleine "
            "largeur s'écrit [{texte': '...', 'fusion_colonnes': 'fin'}]. "
            "Donnée absente : écrire '(à compléter)' dans le texte et marquer le "
            "bloc {statut: inconnu|manquant|a_confirmer} — jamais d'invention ni "
            "de blanc silencieux. Le modèle décrit QUOI écrire, le code choisit "
            "COMMENT le mettre en forme. Fournir 'document_spec' OU 'titre'."
        ),
    )
    style: TypeStyle | None = Field(
        default=None,
        description=(
            "Style documentaire nommé : 'carso_defaut' (défaut), 'carso_sobre' "
            "ou 'carso_institutionnel'. Sans effet si ``document_spec.style`` "
            "est déjà renseigné."
        ),
    )
    valider: bool = Field(
        default=True,
        description=(
            "Valider le document (structure, contenu, style) avant dépôt : les "
            "constats sont renvoyés dans ``validation`` et ``warnings`` — un "
            "livrable incomplet est signalé, jamais passé sous silence."
        ),
    )


class GenererDocumentHtmlInput(BaseModel):
    """Entrée du tool ``generer_document_html`` : HTML + habillage cible.

    Le HTML est un type **standard** que le modèle produit avec aisance, et
    le CSS permet des styles très variés (couleurs, encadrés, images, tableaux
    stylés) sans composer le ``DocumentSpec`` strict. C'est le chemin
    **prioritaire** d'un document personnalisé ; la chaîne structurée reste
    disponible pour les livrables à structure imposée (offres, fiches...).

    Limites honnêtes, transmises au tool : pas de moteur bureautique sur le
    serveur — le PDF est rendu par le moteur CARSO depuis une structure
    extraite du HTML (contenu identique, mise en page réaliste, pas une copie
    pixel du DOCX). Les fusions ``colspan``/``rowspan`` sont gérées : DOCX en
    fusions réelles, PDF en grille aplatie (contenu identique).
    """

    format: Literal["docx", "pdf"] = Field(
        description="Format cible : 'docx' (fidèle au HTML+CSS) ou 'pdf'."
    )
    html: str = Field(
        min_length=1,
        max_length=300_000,
        description=(
            "Page HTML complète (ou fragment) avec ses <style> et, si besoin, "
            "des images data-URI ou http(s). Utiliser des styles inline ou une "
            "balise <style> : le rendu respecte couleurs, polices, alignements, "
            "bordures et fonds. Contenu des données : uniquement des informations "
            "réelles issues de lectures (search_*, read_document) ou de "
            "l'utilisateur — une donnée absente s'écrit '(à compléter)', jamais "
            "une invention."
        ),
    )
    nom_fichier: str = Field(
        description="Nom logique du document (extension ajoutée selon le format)."
    )
    type_document: str = Field(
        description=(
            "Type de dépôt (vocabulaire FERMÉ, valeurs exactes) : 'offre', "
            "'appel_proposition', 'cv', 'fiche_technique', 'fiche_presence', "
            "'liste_beneficiaires', 'checklist', 'rapport', 'justificatif', "
            "'budget', 'template', 'autre', 'non_classe'. "
            "ATTENTION : les noms de profils ('offre_financiere', "
            "'offre_technique') ne sont PAS des types — pour une offre "
            "financière ou technique, utiliser 'offre'. "
            "Les alias usuels sont convertis automatiquement (le retour porte "
            "alors 'type_document_convertis')."
        )
    )
    ancre: TypeAncre | None = Field(
        default=None,
        description=(
            "Rattachement optionnel : 'organisation' | 'appel_a_proposition' | "
            "'offre' | 'mission' | 'equipe' | 'session'. "
            "Omettre si aucun objet métier n'est identifié — ne pas inventer."
        ),
    )
    entity_id: UUID | None = Field(
        default=None,
        description=(
            "UUID de l'objet métier d'ancrage, uniquement si l'ancre est réelle. "
            "Ne jamais inventer d'identifiant."
        ),
    )
    propose_par: str = Field(
        description="Identifiant de l'agent proposant (ex. 'agent_generateur_offre')."
    )
    css: str | None = Field(
        default=None,
        description="CSS additionnel appliqué après le <style> du HTML (facultatif).",
    )


class SerieGraphiqueInput(BaseModel):
    """Une série nommée d'un graphique à axes : un nom et ses valeurs."""

    nom: str = Field(
        min_length=1,
        max_length=60,
        description="Nom de la série (affiché dans la légende), ex. '2025' ou 'Réalisé'.",
    )
    valeurs: list[float] = Field(
        min_length=1,
        max_length=60,
        description="Une valeur par label du graphique, dans le même ordre.",
    )


class GraphiqueInput(BaseModel):
    """Spec d'un graphique inséré dans un livrable professionnel (incrément 69).

    Le SVG est construit **par le moteur** — l'agent ne fournit que des données
    et ne peut donc pas injecter de balise. Les valeurs doivent venir d'une
    lecture réelle (``search_*``, ``read_document``) ou de l'utilisateur : une
    donnée absente s'écrit « (à compléter) », jamais une invention.

    L'insertion se fait par ancre : le corps HTML porte
    ``<figure data-graphique="id"></figure>`` au même ``id``, et le moteur
    remplace l'ancre par le graphique et sa légende.
    """

    id: str = Field(
        min_length=1,
        max_length=64,
        description=(
            "Identifiant d'ancrage, unique dans le document. Le corps HTML doit "
            "porter <figure data-graphique=\"ce-meme-id\"></figure> là où le "
            "graphique doit apparaître."
        ),
    )
    type: Literal["bar", "line", "pie", "donut"] = Field(
        description=(
            "'bar' : barres groupées (comparer des séries par catégorie) ; "
            "'line' : courbes (évolution dans le temps) ; "
            "'pie' : camembert (répartition, ≤ 5-6 parts) ; "
            "'donut' : anneau (répartition avec total au centre)."
        )
    )
    titre: str | None = Field(
        default=None, max_length=200, description="Titre affiché au-dessus du graphique."
    )
    labels: list[str] = Field(
        min_length=1,
        max_length=60,
        description=(
            "Étiquettes de l'axe X (bar/line) ou parts (pie/donut). Courts : les "
            "libellés trop longs sont tronqués à l'affichage."
        ),
    )
    series: list[SerieGraphiqueInput] | None = Field(
        default=None,
        description=(
            "bar/line uniquement : une ou plusieurs séries nommées, chacune avec "
            "autant de valeurs que de labels. Deux séries donnent des barres "
            "groupées ou deux courbes."
        ),
    )
    valeurs: list[float] | None = Field(
        default=None,
        description=(
            "pie/donut uniquement : une valeur par label (toutes ≥ 0, somme > 0). "
            "Valeurs négatives refusées."
        ),
    )
    y_label: str | None = Field(
        default=None, max_length=40, description="Titre de l'axe Y (bar/line)."
    )
    legende: str | None = Field(
        default=None,
        max_length=300,
        description=(
            "Légende de la figure (figcaption), par exemple « Figure 1 – CA par "
            "trimestre ». Sans légende, le titre est repris."
        ),
    )
    largeur: int = Field(default=640, ge=200, le=2400, description="Largeur en pixels.")
    hauteur: int = Field(default=400, ge=150, le=1600, description="Hauteur en pixels.")


class GenererDocumentProfessionnelInput(BaseModel):
    """Entrée du tool ``generer_document_professionnel`` : corps HTML + habillage.

    Le moteur **injecte un thème professionnel** (polices, couleurs, marges,
    en-tête, pied « 2 / 5 », couverture pleine page, sommaire aux numéros de page
    calculés). L'agent n'écrit donc que le **corps** du document, avec les classes
    du thème (``cover``, ``toc``, ``lead``, ``table.kpi``, ``table.data``,
    ``callout``, ``table.layout``, ``badge``, ``page-break``, ``signature``) :
    ni ``<style>``, ni CSS en ligne — le thème s'en charge.

    C'est le chemin **prioritaire** pour un livrable DOCX ou PDF.
    ``generer_document_html`` reste disponible pour un document **libre** (styles
    entièrement écrits par l'agent), et ``generer_document(document_spec)`` pour
    une structure imposée.

    Le rendu est réellement exécuté par un navigateur Chromium côté serveur (Edge,
    Chrome ou le Chromium de Playwright) : le PDF est fidèle au HTML, la sortie
    n'est jamais une approximation. Limites assumées : sans navigateur, le DOCX
    reste produit mais sans PDF jumeau (l'aperçu des pages est alors indisponible)
    et le PDF échoue explicitement ; ``css_supplementaire`` n'a aucun effet sur un
    DOCX (rendu natif Word).
    """

    contenu_html: str = Field(
        min_length=1,
        max_length=300_000,
        description=(
            "Contenu du **<body>** uniquement, en HTML sémantique avec les classes "
            "du thème : <section class='cover'> (couverture), <nav class='toc'><h2>"
            "Sommaire</h2></nav> (laisser VIDE : rempli et numéroté automatiquement), "
            "<p class='lead'>, <table class='kpi'>, <table class='data'> avec <thead> "
            "et classe 'num' sur les colonnes de chiffres, <div class='callout'> "
            "(+ success | warning | danger), <table class='layout'>, <div "
            "class='page-break'>, <span class='badge'>, <figure>, <div "
            "class='signature'>. "
            "GRAPHIQUE DE DONNÉES : pour un graphique à partir de chiffres, écris une "
            "ancre <figure data-graphique='id'></figure> (VIDE) à l'endroit voulu et "
            "déclare le même 'id' dans le paramètre graphiques — le moteur dessine "
            "barres, courbes, camembert ou anneau. "
            "SCHÉMA / ILLUSTRATION : tu peux écrire toi-même un <svg> inline "
            "(organigramme, flèches, cycle, frise, diagramme, schéma technique) ; il "
            "est nettoyé (ni <script>, ni <foreignObject>, ni référence distante) puis "
            "intégré au document — vectoriel en PDF, converti en image en DOCX."
            "Donne-lui un viewBox et une <title> (elle devient le texte alternatif), "
            "et une couleur explicite (fill/stroke) : le thème ne colore pas le SVG. "
            "Ne PAS écrire de <style> ni de CSS en ligne (le thème est injecté ; "
            "un <style> est supprimé et signalé en avertissement). "
            "Données : uniquement des informations réelles issues de lectures "
            "(search_*, read_document) ou de l'utilisateur — une donnée absente "
            "s'écrit '(à compléter)', jamais une invention. "
            "DECK PPTX (format='pptx') : même paramètre, mais le corps est une "
            "suite de diapositives — une <section class='cover'> (title de "
            "couverture, .cover-label, .cover-subtitle, .cover-meta) puis une "
            "<section class='slide'> par diapositive (h1 = titre, p.lead = "
            "message clé, ul/ol, table.kpi, table.data, figure, div.callout, "
            "aside.notes = notes présentateur). Une idée par diapositive, "
            "6 puces maximum : le moteur REFUSE ce qui déborderait plutôt que "
            "de rogner le texte. "
            "CLASSEUR XLSX (format='xlsx') : chaque table.data (thead + lignes, "
            "classe 'num' sur les colonnes de chiffres) devient une feuille "
            "stylée (en-tête figé, colonnes dimensionnées, lignes alternées) "
            "nommée d'après le titre qui la précède ; les table.kpi alimentent "
            "une feuille « Chiffres clés ». Un PDF jumeau est déposé pour "
            "l'aperçu. SANS AUCUN TABLEAU, l'appel échoue : pour un document "
            "texte, choisir pdf/docx."
        ),
    )
    titre: str = Field(
        min_length=1,
        max_length=300,
        description=(
            "Titre du document : il apparaît dans l'en-tête de chaque page et dans "
            "les métadonnées du fichier. Titre court et explicite."
        ),
    )
    format: Literal["pdf", "docx", "pptx", "xlsx"] = Field(
        description=(
            "Format cible. 'pdf' (défaut de fait pour un livrable à remettre), "
            "'docx' seulement si l'utilisateur doit modifier le fichier, "
            "'pptx' pour une présentation/deck 16:9 native (PowerPoint), "
            "'xlsx' pour un classeur Excel stylé (chaque table.data devient une "
            "feuille : exige au moins un tableau dans le contenu)."
        )
    )
    theme: Literal["corporate", "moderne", "nature"] = Field(
        default="corporate",
        description=(
            "Palette du thème : 'corporate' (bleu, défaut), 'moderne' (violet, "
            "tech/créatif), 'nature' (vert, RSE/santé/agro)."
        ),
    )
    orientation: Literal["portrait", "paysage"] = Field(
        default="portrait",
        description=(
            "Orientation A4. 'paysage' si un tableau dépasse 6 colonnes. "
            "Sans objet pour 'pptx' (16:9 fixe)."
        ),
    )
    auteur: str | None = Field(
        default=None,
        description="Auteur du document (métadonnées). À renseigner dès qu'il est connu.",
    )
    css_supplementaire: str | None = Field(
        default=None,
        description=(
            "Micro-ajustements CSS — **à éviter**. Sans effet sur un DOCX."
        ),
    )
    nom_fichier: str | None = Field(
        default=None,
        description=(
            "Nom logique du document (sans extension). Dérivé du titre si omis. "
            "L'extension est ajoutée selon le format."
        ),
    )
    graphiques: list[GraphiqueInput] | None = Field(
        default=None,
        max_length=12,
        description=(
            "Graphiques de DONNÉES (barres, courbes, camembert, anneau). Chaque "
            "entrée remplace l'ancre <figure data-graphique='id'> de même 'id'. "
            "Le graphique est dessiné par le moteur et il est intégré au PDF comme "
            "au DOCX. À utiliser dès qu'un jeu de chiffres réels gagnerait à être "
            "comparé visuellement. Pour un schéma sans chiffres (organigramme, "
            "flux, cycle), écris directement un <svg> dans contenu_html."
        ),
    )
    type_document: str = Field(
        description=(
            "Type de dépôt (vocabulaire FERMÉ, valeurs exactes) : 'offre', "
            "'appel_proposition', 'cv', 'fiche_technique', 'fiche_presence', "
            "'liste_beneficiaires', 'checklist', 'rapport', 'justificatif', "
            "'budget', 'template', 'autre', 'non_classe'. "
            "ATTENTION : les noms de profils ('offre_financiere', "
            "'offre_technique') ne sont PAS des types — pour une offre "
            "financière ou technique, utiliser 'offre'. "
            "Les alias usuels sont convertis automatiquement (le retour porte "
            "alors 'type_document_convertis')."
        )
    )
    ancre: TypeAncre | None = Field(
        default=None,
        description=(
            "Rattachement optionnel : 'organisation' | 'appel_a_proposition' | "
            "'offre' | 'mission' | 'equipe' | 'session'. "
            "Omettre si aucun objet métier n'est identifié — ne pas inventer."
        ),
    )
    entity_id: UUID | None = Field(
        default=None,
        description=(
            "UUID de l'objet métier d'ancrage, uniquement si l'ancre est réelle. "
            "Ne jamais inventer d'identifiant."
        ),
    )
    propose_par: str = Field(
        description="Identifiant de l'agent proposant (ex. 'agent_generateur_offre')."
    )


class ImporterTableauInput(BaseModel):
    """Import d'un tableau d'un classeur XLSX vers un bloc ``tableau`` du spec.

    À utiliser dès qu'un tableau existe **déjà** dans un classeur CARSO (annexe,
    matrice d'activités, budget, planning) : l'import conserve les fusions de
    cellules, les trames et les largeurs réelles. Ressaisir ces tableaux à la
    main est la première source d'erreur et de perte de fidélité.
    """

    document_id: UUID = Field(
        description=(
            "UUID du classeur .xlsx déjà enregistré (annexe de l'appel, matrice "
            "d'activités…). Ne jamais inventer d'identifiant."
        )
    )
    feuille: str | None = Field(
        default=None,
        description=(
            "Nom de la feuille ; la première du classeur si omis. "
            "get_document_structure liste les feuilles disponibles."
        ),
    )
    ligne_entete: int = Field(
        default=1,
        description=(
            "Ligne Excel (1-based) qui porte les en-têtes de colonnes ; "
            "0 = aucune ligne d'en-tête (le tableau n'en aura pas)."
        ),
    )
    colonne_min: int = Field(default=1, description="Première colonne à importer (1-based).")
    colonne_max: int | None = Field(
        default=None, description="Dernière colonne (1-based) ; bord de la feuille si omis."
    )
    ligne_min: int = Field(default=1, description="Première ligne à importer (1-based).")
    ligne_max: int | None = Field(
        default=None, description="Dernière ligne (1-based) ; bas de la feuille si omis."
    )
    titre_tableau: str | None = Field(
        default=None, description="Titre affiché au-dessus du tableau importé."
    )


class CorrigerDocumentInput(BaseModel):
    """Correction ciblée d'un document structuré — sans le régénérer.

    Une relecture remonte rarement un document à refaire : elle remonte un montant
    à rectifier, une cellule à tramer, une colonne qui déborde, une phrase à
    réécrire. Fournir le ``document_spec`` déjà construit et la liste des
    corrections ciblées évite un appel de génération complet et conserve tout ce
    qui était déjà bon.

    Sans ``document_id``, le document corrigé est déposé en **proposition**.
    Avec ``document_id`` (la version déjà déposée), le document corrigé devient
    la **version suivante** : l'ancienne reste sur disque, jamais d'écrasement.
    """

    document_spec: DocumentSpec = Field(
        description="Le document structuré à corriger (tel qu'obtenu précédemment)."
    )
    operations: list[Operation] = Field(
        min_length=1,
        max_length=200,
        description=(
            "Corrections ciblées, appliquées dans l'ordre. Chaque opération porte 'type' : "
            "'cellule' {bloc, ligne, colonne, cellule} — contenu/présentation d'une cellule "
            "(une chaîne change le texte ; un objet {texte, fond, gras, alignement, "
            "fusion_colonnes ('fin' = jusqu'à la dernière), fusion_lignes, couleur_texte, "
            "taille_pt} ne change que les champs fournis) ; "
            "'bloc' {bloc, champs | valeur | inserer_apres | supprimer} — un bloc ; "
            "'tableau' {bloc, champs} — propriétés d'un tableau (largeurs_mm contre une "
            "colonne qui déborde, titre_tableau, alignements, total_ligne…) ; "
            "'style' {chemin, valeur} — un token de style ('corps.taille_pt', "
            "'tableau.entete_fond', 'titres.1.taille_pt') ; "
            "'document' {champs, mise_en_page} — métadonnées (titre, reference, "
            "destinataire…) et mise en page. "
            "Dans un tableau, 'ligne' compte les lignes **telles qu'elles sont "
            "écrites** (en-tête(s) comprises) et 'colonne' l'index dans cette ligne : "
            "une fusion ne décale jamais les coordonnées. "
            "Chaque opération est validée aussitôt : elle doit laisser un document "
            "cohérent. Une modification structurelle (fusion, squelette de lignes) "
            "s'exprime donc en UNE seule opération, jamais en deux temps."
        ),
    )
    document_id: UUID | None = Field(
        default=None,
        description=(
            "UUID de la version déjà déposée à remplacer : une NOUVELLE version est "
            "créée, l'ancienne est conservée. Omettre pour un nouveau dépôt."
        ),
    )
    document_pdf_id: UUID | None = Field(
        default=None,
        description=(
            "UUID du PDF jumeau, si la version corrigée doit aussi remplacer la version "
            "PDF (celui produit avec le DOCX par ``generer_document``)."
        ),
    )
    format: TypeFormat = Field(
        default="docx", description="Format cible : 'docx' | 'pdf' | 'xlsx'."
    )
    nom_fichier: str | None = Field(
        default=None, description="Nom logique du document (obligatoire sans ``document_id``)."
    )
    type_document: str | None = Field(
        default=None, description="Vocabulaire TypeDocument (obligatoire sans ``document_id``)."
    )
    ancre: TypeAncre | None = Field(
        default=None,
        description=(
            "Rattachement optionnel : 'organisation' | 'appel_a_proposition' | 'offre' | "
            "'mission' | 'equipe' | 'session'. Ignoré quand ``document_id`` est fourni "
            "(le document garde son rattachement)."
        ),
    )
    entity_id: UUID | None = Field(
        default=None, description="UUID de l'objet d'ancrage, uniquement si l'ancre est réelle."
    )
    propose_par: str | None = Field(
        default=None, description="Identifiant de l'agent proposant."
    )
    valider: bool = Field(
        default=True,
        description="Revalider le document corrigé (structure, contenu, style).",
    )


class LireDocumentInput(BaseModel):
    """Entrée typée de la lecture : un identifiant de document, rien d'autre."""

    document_id: UUID = Field(description="UUID du document à lire.")


class InspecterDocumentInput(BaseModel):
    """Inspection de structure (métadonnées, styles, feuilles, pages)."""

    document_id: UUID = Field(description="UUID du document à inspecter.")


class PlageLueInput(BaseModel):
    """Une plage à l'intérieur d'un appel groupé (``plages`` de LirePlageInput)."""

    from_index: int | None = Field(default=None, ge=1)
    to_index: int | None = Field(default=None, ge=1)
    from_page: int | None = Field(default=None, ge=1, description="Alias de from_index.")
    to_page: int | None = Field(default=None, ge=1, description="Alias de to_index.")


class LirePlageInput(BaseModel):
    """Lecture bornée. Pour un DOCX, from/to désignent des paragraphes.

    ``from_page``/``to_page`` sont les **alias** de ``from_index``/``to_index`` :
    le repère est le même pour les trois formats (page PDF, paragraphe DOCX,
    ligne XLSX). Si les deux formes sont fournies, ``from_index``/``to_index``
    l'emportent.

    ``plages`` lit **plusieurs** plages en UN appel : une liste de bornes, une
    entrée de retour par plage. Incompatible avec les bornes scalaires —
    l'ambiguïté est refusée, jamais devinée.
    """

    document_id: UUID
    plages: list[PlageLueInput] | None = Field(
        default=None,
        min_length=1,
        max_length=MAX_PLAGES_PAR_LECTURE,
        description=(
            "Plusieurs plages en UN appel : plages: [{from_page: 10, to_page: "
            "12}, {from_page: 40, to_page: 42}] → ``plages`` au retour (une "
            "entrée par plage, dans l'ordre demandé). Incompatible avec des "
            "bornes scalaires from/to. Une plage seule garde le retour "
            "unitaire d'origine."
        ),
    )
    from_index: int | None = None
    to_index: int | None = None
    from_page: int | None = Field(
        default=None,
        description=(
            "Première page (alias de from_index) : page PDF, paragraphe DOCX, "
            "ligne XLSX — 1-indexée."
        ),
    )
    to_page: int | None = Field(
        default=None,
        description="Dernière page (alias de to_index), incluse.",
    )
    sheet: str | None = Field(default=None, description="Feuille XLSX (optionnel).")


class ChercherDocumentInput(BaseModel):
    """Entrée de ``chercher_dans_document`` : un document + un ou plusieurs mots-clés.

    ``mot_cle`` est une **liste** (ex. ``["budget", "lot 3"]``) : un seul appel
    fouille plusieurs mots-clés, chacun gardant son contrat de résultat. Une
    chaîne seule reste acceptée pour une recherche unique (retour unitaire).
    """

    document_id: UUID = Field(description="UUID du document à fouiller.")
    mot_cle: list[Annotated[str, Field(min_length=2, max_length=200)]] = Field(
        min_length=1,
        max_length=MAX_MOTS_CLES_PAR_RECHERCHE,
        description=(
            "Un ou plusieurs mots-clés — LISTE : ['budget', 'lot 3'] fouille les "
            "deux en UN appel et le retour porte une entrée par mot-clé "
            "(``recherches``). Une chaîne seule est acceptée (recherche unique, "
            "retour unitaire). Recherche littérale (insensible à la casse, "
            "aucun opérateur ni expression régulière) : ne PAS relire le "
            "document en entier."
        ),
    )
    max_resultats: int | None = Field(
        default=None,
        ge=1,
        le=200,
        description="Pages renvoyées au plus (défaut 50, plafond serveur 200).",
    )
    feuille: str | None = Field(
        default=None,
        description="XLSX uniquement : limiter la recherche à une feuille (sinon toutes).",
    )

    @field_validator("mot_cle", mode="before")
    @classmethod
    def _mot_cle_normalise(cls, valeur: Any) -> Any:
        """Une chaîne seule vaut ``[mot_cle]`` ; chaque mot-clé est nettoyé.

        Le nettoyage (espaces autour) est fait **avant** les bornes de longueur :
        un mot-clé réduit à des espaces est refusé par le schéma (minLength 2),
        jamais transmis à la recherche. Pas de ``strip_whitespace`` dans le
        schéma JSON : les fournisseurs de modèles lisent le schéma tel quel.
        """
        if isinstance(valeur, str):
            valeur = [valeur]
        if isinstance(valeur, list):
            return [item.strip() if isinstance(item, str) else item for item in valeur]
        return valeur


class LireElementInput(BaseModel):
    """Entrée de ``lire_element_document`` : un document + un ou plusieurs éléments.

    ``type_element`` dit **quoi** lire, ``index`` **où** (1-indexé, une liste
    pour lire plusieurs éléments en un appel). Le repère ``element`` est commun
    aux trois formats (élément de flux DOCX, page PDF, ligne XLSX) : c'est celui
    que ``chercher_dans_document`` renvoie dans ``relire`` — à recopier tel quel,
    jamais à recalculer.
    """

    document_id: UUID = Field(description="UUID du document à lire.")
    type_element: TypeElement = Field(
        default="element",
        description=(
            "Quoi lire : 'element' (repère commun), 'paragraphe' | 'tableau' | "
            "'entete' | 'pied' (DOCX), 'page' (PDF), 'ligne' | 'feuille' "
            "(XLSX). Un type absent du format du document est refusé avec la "
            "liste des types valides."
        ),
    )
    index: list[Annotated[int, Field(ge=1)]] | None = Field(
        default=None,
        min_length=1,
        max_length=MAX_ELEMENTS_PAR_LECTURE,
        description=(
            "Position(s) 1-indexée(s) de l'élément. Une LISTE lit plusieurs "
            "éléments en UN appel : index: [100, 120, 130] → ``elements`` (une "
            "entrée par index, dans l'ordre demandé). Un entier seul reste "
            "accepté (forme des locators ``hit['relire']``). Obligatoire sauf "
            "pour type_element='feuille', décrite par son nom via 'feuille'."
        ),
    )
    sous_index: int | None = Field(
        default=None,
        ge=1,
        description="DOCX uniquement : ligne d'un tableau à lire seule (1-indexée).",
    )
    feuille: str | None = Field(
        default=None,
        description="XLSX uniquement : nom de la feuille (sans nom : la première).",
    )
    max_lignes: int | None = Field(
        default=None,
        ge=1,
        le=200,
        description="Lignes lues au plus (tableau DOCX, feuille XLSX) — défaut 40, plafond 200.",
    )
    max_caracteres: int | None = Field(
        default=None,
        ge=1,
        le=40_000,
        description="Caractères lus au plus d'une page PDF — défaut 8 000, plafond 40 000.",
    )

    @field_validator("index", mode="before")
    @classmethod
    def _index_scalaire_accepte(cls, valeur: Any) -> Any:
        """Un index seul (entier) vaut ``[index]`` — locators ``hit['relire']``."""
        if isinstance(valeur, int) and not isinstance(valeur, bool):
            return [valeur]
        return valeur


class ClonerDocumentInput(BaseModel):
    """Clone la structure d'une référence + contenu fourni → statut proposed."""

    reference_document_id: UUID
    format: TypeFormat = Field(description="'docx' | 'xlsx' | 'pdf'.")
    nom_fichier: str
    type_document: str
    ancre: TypeAncre | None = Field(
        default=None,
        description="Rattachement optionnel (voir generer_document).",
    )
    entity_id: UUID | None = Field(
        default=None,
        description="UUID de l'objet métier, uniquement si l'ancre est réelle.",
    )
    propose_par: str
    titre: str
    paragraphes: list[str] | None = None
    tableau: list[list[Any]] | None = None


class RemplirTemplateInput(BaseModel):
    """Remplit ``{{cle}}`` d'un template DOCX → statut proposed."""

    template_document_id: UUID
    valeurs: dict[str, str]
    nom_fichier: str
    type_document: str
    ancre: TypeAncre | None = Field(
        default=None,
        description="Rattachement optionnel (voir generer_document).",
    )
    entity_id: UUID | None = Field(
        default=None,
        description="UUID de l'objet métier, uniquement si l'ancre est réelle.",
    )
    propose_par: str


class ReferenceDocumentInput(BaseModel):
    """Analyse d'un document de référence : plan + style, sans son contenu."""

    document_id: UUID = Field(
        description=(
            "UUID du document de référence déjà enregistré (modèle du client, "
            "ancienne proposition…). Ne jamais inventer d'identifiant."
        )
    )
    niveau: Literal["resume", "complet"] = Field(
        default="resume",
        description="Profondeur d'analyse : 'resume' (défaut) ou 'complet'.",
    )


class ValiderDocumentInput(BaseModel):
    """Validation d'un document structuré avant ou après génération."""

    document_spec: DocumentSpec = Field(
        description="Document structuré à vérifier (même schéma que generer_document)."
    )
    profil: TypeProfil | None = Field(
        default=None,
        description=(
            "Profil de livrable attendu : 'offre_technique', 'offre_financiere', "
            "'offre_autre', 'fiche_technique', 'fiche_presence', 'checklist', "
            "'rapport', 'courrier'. Omettre pour laisser le système déduire."
        ),
    )
    portees: list[TypePortee] | None = Field(
        default=None,
        description="Portées à vérifier : 'structure', 'contenu', 'style' (défaut : les trois).",
    )


class RenderDocumentInput(BaseModel):
    """Aperçu visuel : rend les pages d'un document PDF en images.

    À utiliser **après** une génération, pour vérifier le rendu réel plutôt que
    de le supposer : le résultat alimente ``afficher_apercu_document`` côté
    interface, l'utilisateur voit les pages dans le fil.
    """

    document_id: UUID = Field(
        description=(
            "UUID du document **PDF** à prévisualiser. Un DOCX/XLSX n'est pas "
            "convertible sur ce serveur : générer la version PDF du document "
            "(``generer_document`` avec ``format='pdf'``) puis la prévisualiser."
        )
    )
    dpi: int | None = Field(
        default=None,
        description="Résolution des images (défaut : configuration serveur, 110).",
    )
    pages: list[int] | None = Field(
        default=None,
        description=(
            "Pages à rendre (1-based). Omettre pour rendre depuis la première "
            "page, dans la limite du plafond de pages du serveur."
        ),
    )


#: Alias fréquents du modèle → type ``TypeDocument`` valide. Les noms de
#: **profils** de livrable (offre_financiere, offre_technique...) ne sont pas
#: des types de dépôt : ils sont convertis, jamais rejetés — chaque conversion
#: évite un aller-retour d'erreur tool call.
_ALIAS_TYPE_DOCUMENT: dict[str, str] = {
    # Profils de livrable → type de dépôt réel.
    "offre_financiere": "offre",
    "offre_technique": "offre",
    "offre_autre": "offre",
    # Variantes de formulation.
    "offre_formation": "offre",  # historique lisible, dépôt → nouveau vocab.
    "appel": "appel_proposition",
    "appel_a_projet": "appel_proposition",
    "fiche_presence": "fiche_presence",
    "liste_des_beneficiaires": "liste_beneficiaires",
    "presence": "fiche_presence",
    "check_list": "checklist",
    "photo": "autre",
    "image": "autre",
    "note": "rapport",
    "note_de_synthese": "rapport",
    "facture": "budget",
}


def _specs_graphiques(bruts: Any) -> dict[str, dict[str, Any]]:
    """Convertit les entrées du tool en specs de ``render_chart`` (ordre conservé).

    C'est ici que la forme « outil » (séries nommées en liste, valeurs séparées)
    rejoint la forme « moteur » attendue par ``graphiques.render_chart`` : un seul
    point de traduction, plutôt que deux vocabulaires qui divergent.

    Raises:
        ValidationError: deux graphiques partagent le même ``id`` (l'insertion
            serait ambiguë : le moteur refuserait la seconde ancre).
    """
    specs: dict[str, dict[str, Any]] = {}
    for brut in bruts or []:
        entree = brut if isinstance(brut, dict) else brut.model_dump()
        identifiant = str(entree.get("id") or "").strip()
        if not identifiant:
            raise ValidationError("Chaque graphique doit porter un 'id' non vide.")
        if identifiant in specs:
            raise ValidationError(
                f"Identifiant de graphique en double : {identifiant!r}",
                details={"ids": sorted(specs)},
            )
        spec: dict[str, Any] = {
            "type": entree.get("type"),
            "labels": list(entree.get("labels") or []),
            "title": entree.get("titre") or "",
            "y_label": entree.get("y_label") or "",
            "legende": entree.get("legende") or "",
            "description": entree.get("legende") or entree.get("titre") or "",
            "width": entree.get("largeur", 640),
            "height": entree.get("hauteur", 400),
        }
        series = entree.get("series")
        if series:
            spec["series"] = {
                str(s.get("nom")): list(s.get("valeurs") or [])
                for s in (s if isinstance(s, dict) else s.model_dump() for s in series)
            }
        if entree.get("valeurs") is not None:
            spec["values"] = list(entree["valeurs"])
        specs[identifiant] = spec
    return specs


def _type_depot(payload: dict[str, Any]) -> str:
    """Type de dépôt normalisé : alias converti, inconnu → erreur pédagogique.

    L'erreur liste les valeurs valides **et** la conversion des alias : le
    modèle corrige son appel au coup suivant sans deviner (les erreurs tool
    call viennent presque toujours d'un vocabulaire supposé, pas d'une
    intention fausse).
    """
    brute = str(payload.get("type_document") or "").strip().lower()
    if not brute:
        from app.domain.enums import TypeDocument

        return TypeDocument.NON_CLASSE.value
    converti = _ALIAS_TYPE_DOCUMENT.get(brute)
    if converti is not None:
        return converti
    from app.domain.enums import TypeDocument

    valides = sorted(type_.value for type_ in TypeDocument)
    if brute not in valides:
        raise ValidationError(
            f"Type de document inconnu : {payload.get('type_document')!r}",
            details={
                "types_valides": valides,
                "suggestion": (
                    "Pour une offre financière/technique, utiliser le type "
                    "'offre' — le profil de livrable se règle dans le contenu. "
                    "Les alias usuels (offre_financiere, note_de_synthese, "
                    "check_list...) sont convertis automatiquement."
                ),
            },
        )
    return brute


def _kwargs_ancre(payload: dict[str, Any]) -> dict[str, UUID]:
    """Ancre réelle → FK. Ancre absente / nom inconnu / UUID manquant → {} (repli).

    Un nom d'ancre hors vocabulaire reste une erreur : ce n'est pas un oubli,
    c'est une valeur inventée. Un UUID manquant ou une ancre omise n'en est pas
    une — le service range alors sous ``generated/{id}/``.
    """
    brute = payload.get("ancre")
    if brute is None:
        return {}
    ancre = str(brute).strip().lower()
    if not ancre:
        return {}
    if ancre not in _ANCRES:
        raise ValidationError(
            f"Ancre inconnue : {brute} (attendues : {', '.join(sorted(_ANCRES))})"
        )
    identifiant = payload.get("entity_id")
    if identifiant is None or not str(identifiant).strip():
        return {}
    try:
        return {_ANCRES[ancre]: UUID(str(identifiant).strip())}
    except ValueError:
        # UUID inventé / mal formé : on ne l'écrit pas, le service replie.
        return {}


def _format_depot(demande: Any) -> str:
    """Format de dépôt normalisé, ou erreur explicite listant les formats valides."""
    fmt = str(demande or "docx").strip().lower()
    if fmt not in _FORMATS:
        raise ValidationError(
            f"Format inconnu : {demande} (attendus : {', '.join(sorted(_FORMATS))})"
        )
    return fmt


def _spec_depuis(payload: dict[str, Any]) -> Any:
    """Construit un ``DocumentSpec`` depuis l'entrée du modèle.

    Les erreurs de schéma sont reformulées en liste champ/problème : l'agent
    corrige précisément au lieu de deviner ce qui n'a pas plu.
    """
    from pydantic import ValidationError as PydanticValidationError

    from app.documents.styles import style_par_nom

    donnees = payload.get("document_spec")
    if not isinstance(donnees, dict):
        raise ValidationError(
            "document_spec doit être un objet : {'metadata': {...}, 'blocs': [...]}"
        )
    donnees = dict(donnees)
    if payload.get("style") and not donnees.get("style"):
        donnees["style"] = style_par_nom(str(payload["style"])).model_dump()
    try:
        return DocumentSpec.model_validate(donnees)
    except PydanticValidationError as erreur:
        problemes = [
            {"champ": ".".join(str(partie) for partie in detail["loc"]), "probleme": detail["msg"]}
            for detail in erreur.errors()[:15]
        ]
        msg = "Document structuré invalide : corriger les champs signalés"
        raise ValidationError(msg, details={"erreurs": problemes}) from erreur


def _valider_spec(spec: Any, profil: Any, *, portees: Any = None) -> Any:
    """Valide un spec (profil nommé, ou déduit du document)."""
    from app.documents.profiles import profil_par_nom
    from app.documents.validation_document import valider_document

    return valider_document(
        spec,
        profil=profil_par_nom(str(profil)) if profil else None,
        portees=tuple(portees) if portees else None,
    )


def _contrat_validation(rapport: Any) -> dict[str, Any]:
    """Contrat de retour de validation : état par portée + constats lisibles."""
    return {
        "ok": rapport.ok,
        "structure": rapport.par_portee.get("structure", "skipped"),
        "contenu": rapport.par_portee.get("contenu", "skipped"),
        "style": rapport.par_portee.get("style", "skipped"),
        "profil": rapport.resume.get("profil"),
        "nb_mots": rapport.resume.get("nb_mots"),
        "erreurs": rapport.messages("erreur")[:15],
        "avertissements": rapport.messages("avertissement")[:15],
    }


def _message_livrable(sortie: dict[str, Any], conforme: bool) -> str:
    """Message de retour d'une génération structurée (ce qui reste à faire)."""
    base = (
        f"Document déposé en statut '{sortie.get('statut')}' "
        f"(validation humaine requise). Étape suivante : appeler "
        "render_document puis afficher_apercu_document pour montrer le rendu, "
        "et proposer_telechargement_document pour le fichier."
    )
    if conforme:
        return base
    return (
        f"ATTENTION : le document n'est PAS conforme aux attentes de son profil. "
        f"{base} Corriger d'abord les erreurs listées dans 'validation'."
    )


def build_document_tools(
    *,
    identite: AgentIdentity,
    policy: PermissionPolicy,
    service_factory: Any,
) -> list[Any]:
    """Construit les tools documentaires pour UN agent.

    Args:
        identite: identité de l'agent appelant (traçabilité).
        policy: permissions de l'agent. Capacités exigées : ``document_read``
            pour la lecture, ``document_propose`` pour la génération (dépôt en
            ``proposed``). Un agent sans l'une d'elles ne reçoit pas le tool —
            et un appel direct reste refusé par ``policy.require``.
        service_factory: callable ``f() -> DocumentService`` fourni par le
            composant appelant (l'agent n'ouvre pas de session lui-même).

    Returns:
        Liste de ``TypedTool`` : ``generer_document`` et ``lire_document``.
    """
    from app.tools.base import TypedTool

    def _uuid(valeur: Any, champ: str) -> UUID:
        """Identifiant déjà validé par le schéma, ou texte à convertir.

        Le schéma type désormais les identifiants en ``UUID`` : le handler reçoit
        donc un ``UUID`` (jamais un texte), mais il reste tolérant à un texte pour
        un appel direct au handler (tests, chemin déterministe).
        """
        if isinstance(valeur, UUID):
            return valeur
        try:
            return UUID(str(valeur))
        except (ValueError, TypeError, AttributeError) as erreur:
            raise ValidationError(f"{champ} n'est pas un UUID valide") from erreur

    def _deposer(octets: bytes, payload: dict[str, Any], extension: str) -> dict[str, Any]:
        service = service_factory()
        nom = payload["nom_fichier"]
        if not nom.lower().endswith(extension):
            nom = f"{nom}{extension}"
        # Alias de type converti ici (offre_financiere → offre...) : la
        # conversion est signalée au modèle dans le retour, pour qu'il
        # s'aligne tout seul sur le vocabulaire réel.
        type_demande = str(payload.get("type_document") or "").strip().lower()
        type_depot = _type_depot(payload)
        kwargs_dto: dict[str, Any] = {
            "type_document": type_depot,
            "nom": nom,
            "stream": io.BytesIO(octets),
            "proposed_by_agent": payload.get("propose_par") or identite.agent_id,
            "created_by": payload.get("propose_par") or identite.agent_id,
        }
        kwargs_dto.update(_kwargs_ancre(payload))
        entree = DocumentUploadInput(**kwargs_dto)
        document = service.enregistrer_document(entree)
        non_classe = all(
            getattr(document, champ) is None for champ in _ANCRES.values()
        )
        sortie: dict[str, Any] = {
            "document_id": str(document.id),
            "statut": document.statut,
            "nom": document.nom,
            "type_document": type_depot,
        }
        if type_demande and type_demande != type_depot:
            sortie["type_document_convertis"] = (
                f"'{type_demande}' → '{type_depot}' : les noms de profils "
                "(offre_financiere, offre_technique...) ne sont pas des types "
                "de dépôt ; utiliser le type effectif ci-dessus au prochain appel."
            )
        if non_classe:
            sortie["non_classe"] = True
            sortie["message"] = (
                f"Document déposé en statut 'proposed' sans rattachement métier "
                f"(generated/{document.id}). Validation humaine requise. "
                "Proposer le téléchargement via proposer_telechargement_document."
            )
        else:
            sortie["message"] = (
                "Document déposé en statut 'proposed' (validation humaine requise)."
            )
        return sortie

    def _generer(payload: dict[str, Any]) -> dict[str, Any]:
        policy.require(identite.agent_id, "document_propose")
        fmt = _format_depot(payload.get("format"))
        if payload.get("document_spec"):
            return _generer_structurer(payload, fmt)
        if not payload.get("titre"):
            raise ValidationError(
                "Fournir 'document_spec' (document structuré, recommandé) ou "
                "'titre' (génération simple : titre, paragraphes, tableau)."
            )
        extension, _mime = _FORMATS[fmt]
        from app.documents.generation import GenerateurDocument

        generateur = GenerateurDocument()
        if fmt == "xlsx":
            octets = generateur.xlsx(
                titre_feuille=payload.get("titre") or "Feuille",
                lignes=payload.get("tableau") or [],
            )
        elif fmt == "docx":
            octets = generateur.docx(
                titre=payload["titre"],
                paragraphes=payload.get("paragraphes"),
                tableau=payload.get("tableau"),
            )
        else:
            octets = generateur.pdf(
                titre=payload["titre"],
                paragraphes=payload.get("paragraphes"),
                tableau=payload.get("tableau"),
            )
        resultat = _deposer(octets, payload, extension)
        if not resultat.get("non_classe"):
            resultat["message"] = (
                "Document généré et déposé en statut 'proposed' : il nécessite "
                "une validation humaine avant approbation. "
                "Appeler proposer_telechargement_document pour offrir le téléchargement."
            )
        else:
            resultat["message"] = (
                "Document généré et déposé en statut 'proposed' sans rattachement "
                f"métier (generated/{resultat['document_id']}). "
                "Appeler proposer_telechargement_document pour offrir le téléchargement."
            )
        return resultat

    def _generer_html(payload: dict[str, Any]) -> dict[str, Any]:
        """HTML → DOCX (fidèle au HTML+CSS) ou PDF (moteur CARSO), dépôt HITL."""
        policy.require(identite.agent_id, "document_propose")
        from app.documents.generation_html import convertir_html_bytes

        fmt = str(payload["format"]).strip().lower()
        octets = convertir_html_bytes(
            payload["html"], format=fmt, css=payload.get("css")
        )
        extension, _mime = _FORMATS[fmt]
        sortie = _deposer(octets, payload, extension)
        if sortie.get("non_classe"):
            sortie["message"] = (
                "Document HTML converti et déposé en statut 'proposed' sans "
                f"rattachement métier (generated/{sortie['document_id']}). "
                "Validation humaine requise. Appeler proposer_telechargement_document."
            )
        else:
            sortie["message"] = (
                "Document HTML converti et déposé en statut 'proposed' "
                "(validation humaine requise). Appeler proposer_telechargement_document."
            )
        if fmt == "pdf":
            sortie["note_pdf"] = (
                "Le PDF est rendu par le moteur CARSO depuis la structure du HTML "
                "(contenu identique ; pas de convertisseur bureautique sur ce "
                "serveur, la mise en page n'est pas une copie pixel du DOCX)."
            )
        return sortie

    def _generer_professionnel(payload: dict[str, Any]) -> dict[str, Any]:
        """Corps HTML + thème → PDF ou DOCX professionnel, déposé en proposition.

        Version CARSO du module ``generation-document`` : le moteur injecte le
        thème, la couverture, le sommaire numéroté et la pagination — l'agent
        n'écrit que le corps. Le rendu reste **en mémoire** (le moteur travaille
        dans un dossier temporaire effacé à la sortie) : aucun chemin disque
        n'est renvoyé et le dépôt passe par ``DocumentService``, statut
        ``proposed`` (règle 6 [C]).

        Le moteur est importé **paresseusement** : ``playwright`` est une
        dépendance optionnelle (extra ``documents_pro``), et le backend doit
        démarrer sans elle. Son absence produit une erreur explicite au premier
        appel, jamais une dégradation silencieuse.
        """
        policy.require(identite.agent_id, "document_propose")
        from app.documents.professionnel import rendre_professionnel
        from app.documents.professionnel.html_builder import slugifier

        graphes = _specs_graphiques(payload.get("graphiques"))
        rendu = rendre_professionnel(
            payload["contenu_html"],
            payload["titre"],
            format=str(payload["format"]),
            auteur=payload.get("auteur") or "",
            theme=payload.get("theme") or "corporate",
            orientation=payload.get("orientation") or "portrait",
            css_supplementaire=payload.get("css_supplementaire"),
            graphiques=graphes or None,
        )
        if rendu.format == "pptx":
            extension, _mime = _FORMAT_PPTX
        else:
            extension, _mime = _FORMATS[rendu.format]
        base = payload.get("nom_fichier") or payload["titre"]
        nom = slugifier(Path(str(base)).stem, "_")[:60] or "document"

        sortie = _deposer(rendu.octets, {**payload, "nom_fichier": nom}, extension)
        documents: dict[str, Any] = {
            "principal": sortie["document_id"],
            "format": rendu.format,
        }
        if rendu.jumeau_pdf is not None:
            # Le jumeau PDF rend le livrable prévisualisable (render_document ne
            # rend que des PDF) : même contenu, même pagination.
            jumeau = _deposer(
                rendu.jumeau_pdf,
                {**payload, "nom_fichier": f"{nom}.pdf"},
                ".pdf",
            )
            documents["pdf"] = jumeau["document_id"]

        sortie["documents"] = documents
        sortie["pages"] = rendu.pages
        sortie["theme"] = rendu.theme
        if rendu.format == "pptx":
            # Un deck compte ses diapositives : dire « pages » à l'agent
            # l'inviterait à en rajouter pour tenir une pagination A4.
            sortie["diapositives"] = rendu.pages
            # render_document ne rend que des PDF : le dire plutôt que laisser
            # l'agent découvrir l'échec en appelant l'outil sur un .pptx.
            sortie["limite_apercu"] = (
                "render_document ne prévisualise que les PDF : pour un PPTX, "
                "relis le contenu de chaque diapositive avant de le présenter."
            )
        if graphes:
            sortie["graphiques"] = {
                "nombre": len(graphes),
                "ids": list(graphes),
                "integration": {
                    "pdf": "SVG vectoriel (nettete maximale a l'impression).",
                    "docx": "PNG haute resolution (Word ne lit pas le SVG).",
                    "pptx": "PNG haute resolution (PowerPoint ne lit pas le SVG).",
                }.get(rendu.format, "SVG vectoriel."),
            }
        if rendu.avertissements:
            sortie["avertissements"] = list(rendu.avertissements)
        sortie["conseil"] = rendu.conseil
        libelle = {
            "pptx": "Présentation",
            "xlsx": "Classeur Excel",
        }.get(rendu.format, "Document professionnel")
        sortie["message"] = (
            f"{libelle} généré et déposé en statut 'proposed' sans "
            f"rattachement métier (generated/{sortie['document_id']}). "
            "Validation humaine requise. Appeler proposer_telechargement_document."
            if sortie.get("non_classe")
            else (
                f"{libelle} généré et déposé en statut 'proposed' "
                "(validation humaine requise). Lire ``avertissements`` et les "
                "corriger avant de présenter : appeler render_document puis "
                "proposer_telechargement_document."
            )
        )
        return sortie

    def _generer_structurer(payload: dict[str, Any], fmt: str) -> dict[str, Any]:
        """Génère un vrai livrable depuis un ``DocumentSpec`` (avec validation).

        Le même contenu est déposé en PDF quand le format demandé n'est pas déjà
        le PDF : un livrable remis à un tiers doit être **lisible** (et donc
        prévisualisable) sans dépendre d'un logiciel bureautique.
        """
        from app.documents.renderers import rendre

        spec = _spec_depuis(payload)
        rapport = None
        if payload.get("valider", True):
            rapport = _valider_spec(spec, payload.get("profil"))

        extension, _mime = _FORMATS[fmt]
        sortie = _deposer(rendre(spec, format=fmt), payload, extension)
        documents: dict[str, Any] = {"principal": sortie["document_id"], "format": fmt}
        if fmt != "pdf":
            jumeau = _deposer(
                rendre(spec, format="pdf"),
                {**payload, "nom_fichier": f"{Path(str(payload['nom_fichier'])).stem}.pdf"},
                ".pdf",
            )
            documents["pdf"] = jumeau["document_id"]

        sortie["documents"] = documents
        sortie["resume_document"] = spec.resume()
        if rapport is not None:
            sortie["validation"] = _contrat_validation(rapport)
            sortie["warnings"] = [
                *rapport.messages("erreur"),
                *rapport.messages("avertissement"),
            ][:20]
        sortie["message"] = _message_livrable(sortie, rapport is not None and rapport.ok)
        return sortie

    def _reference(payload: dict[str, Any]) -> dict[str, Any]:
        policy.require(identite.agent_id, "document_read")
        from app.documents.reference import analyser_reference

        service = service_factory()
        document_id = _uuid(payload["document_id"], "document_id")
        document = service.obtenir(document_id)
        _nom, chemin = service.telecharger(document_id)
        niveau = str(payload.get("niveau") or "resume").strip().lower()
        if niveau not in ("resume", "complet"):
            raise ValidationError(
                f"Niveau d'analyse inconnu : {payload.get('niveau')} (attendus : resume, complet)"
            )
        analyse = analyser_reference(chemin, niveau=niveau)
        return {
            "document_id": str(document.id),
            "nom": document.nom,
            "message": (
                "Plan et style de la référence extraits. Réutiliser ``style_spec`` "
                "pour générer un document au même style : le contenu de la "
                "référence n'est jamais recopié."
            ),
            **analyse,
        }

    def _verifier(payload: dict[str, Any]) -> dict[str, Any]:
        policy.require(identite.agent_id, "document_read")
        spec = _spec_depuis(payload)
        portees = payload.get("portees")
        if portees is not None and not isinstance(portees, list):
            raise ValidationError("portees doit être une liste : structure, contenu, style")
        rapport = _valider_spec(spec, payload.get("profil"), portees=portees)
        return {
            "document_id": None,
            "ok": rapport.ok,
            "validation": _contrat_validation(rapport),
            "findings": [constat.model_dump() for constat in rapport.findings][:40],
            "resume": rapport.resume,
            "message": (
                "Document conforme aux attentes de son profil."
                if rapport.ok
                else "Document NON conforme : corriger les erreurs signalées avant de le présenter."
            ),
        }

    def _rendre(payload: dict[str, Any]) -> dict[str, Any]:
        policy.require(identite.agent_id, "document_read")
        service = service_factory()
        document_id = _uuid(payload["document_id"], "document_id")
        pages = payload.get("pages")
        if pages is not None and not isinstance(pages, list):
            raise ValidationError("pages doit être une liste de numéros de page (1-based)")
        apercu = service.apercu_document(
            document_id,
            pages=[int(page) for page in pages] if pages else None,
            dpi=payload.get("dpi"),
        )
        numeros = [page.numero for page in apercu.pages]
        sortie: dict[str, Any] = {
            "ok": True,
            "document_id": str(apercu.document_id),
            "nom": apercu.nom,
            "nb_pages": apercu.nb_pages,
            "pages": numeros,
            "nb_pages_rendues": len(numeros),
            "dpi": apercu.dpi,
            "tronque": apercu.tronque,
            "warnings": (
                [
                    f"Seules {len(numeros)} page(s) sur {apercu.nb_pages} ont été rendues "
                    "(plafond de pages du serveur)."
                ]
                if apercu.tronque
                else []
            ),
            "message": (
                "Aperçu prêt : appeler afficher_apercu_document pour montrer les "
                "pages à l'utilisateur dans le fil."
            ),
        }
        return sortie

    def _corriger(payload: dict[str, Any]) -> dict[str, Any]:
        """Applique des corrections ciblées et dépose (ou versionne) le résultat.

        Le patch ne reconstruit pas le document : il modifie la copie du spec,
        qui est ensuite revalidée, puis rendue. Le journal des changements est
        renvoyé tel quel — l'agent peut le montrer à l'utilisateur.
        """
        policy.require(identite.agent_id, "document_propose")
        from app.documents.repair import appliquer_patch

        spec = _spec_depuis(payload)
        corrige, journal = appliquer_patch(spec, payload["operations"])
        rapport = None
        if payload.get("valider", True):
            rapport = _valider_spec(corrige, payload.get("profil"))
        validation = _contrat_validation(rapport) if rapport is not None else None
        avertissements = (
            [
                constat.message
                for constat in rapport.findings
                if constat.gravite != "info"
            ][:10]
            if rapport is not None
            else []
        )

        fmt = _format_depot(payload.get("format") or "docx")
        document_id = payload.get("document_id")
        if not document_id:
            if not payload.get("nom_fichier") or not payload.get("type_document"):
                raise ValidationError(
                    "Sans 'document_id', fournir 'nom_fichier' et 'type_document' "
                    "pour déposer le document corrigé."
                )
            sortie = _generer_structurer(
                {
                    **payload,
                    "document_spec": corrige.model_dump(mode="json"),
                    "valider": False,
                },
                fmt,
            )
            resultat = {
                **sortie,
                "document_spec": corrige.model_dump(mode="json"),
                "operations_appliquees": journal,
            }
        else:
            principal = _versionner(
                _uuid(str(document_id), "document_id"), corrige, fmt, payload
            )
            documents: dict[str, Any] = {
                "principal": principal["document_id"],
                "format": fmt,
            }
            if payload.get("document_pdf_id"):
                jumeau = _versionner(
                    _uuid(str(payload["document_pdf_id"]), "document_pdf_id"),
                    corrige,
                    "pdf",
                    payload,
                )
                documents["pdf"] = jumeau["document_id"]
            resultat = {
                "document_id": principal["document_id"],
                "version": principal["version"],
                "nom": principal["nom"],
                "documents": documents,
                "document_spec": corrige.model_dump(mode="json"),
                "operations_appliquees": journal,
                "resume_document": corrige.resume(),
                "message": (
                    f"Correction appliquée en nouvelle version (v{principal['version']}) : "
                    "la version précédente est conservée. Appeler render_document puis "
                    "proposer_telechargement_document, et rafraichir_application."
                ),
            }
        if validation is not None:
            resultat["validation"] = validation
            resultat["warnings"] = avertissements
        return resultat

    def _versionner(
        document_id: UUID, spec: Any, fmt: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        """Dépose le document corrigé comme version suivante du document visé."""
        from app.documents.renderers import rendre

        service = service_factory()
        courant = service.obtenir(document_id)
        entree = DocumentUploadInput(
            type_document=courant.type_document,
            nom=courant.nom,
            stream=io.BytesIO(rendre(spec, format=fmt)),
            proposed_by_agent=payload.get("propose_par") or identite.agent_id,
            created_by=payload.get("propose_par") or identite.agent_id,
        )
        document = service.remplacer_document(document_id, entree)
        return {
            "document_id": str(document.id),
            "version": document.version,
            "nom": document.nom,
            "statut": document.statut,
        }

    def _importer_tableau(payload: dict[str, Any]) -> dict[str, Any]:
        policy.require(identite.agent_id, "document_read")
        from app.documents.tableau_xlsx import import_tableau_xlsx

        service = service_factory()
        document_id = _uuid(payload["document_id"], "document_id")
        document = service.obtenir(document_id)
        _nom, chemin = service.telecharger(document_id)
        if chemin.suffix.lower() != ".xlsx":
            raise ValidationError(
                f"Import de tableau : un classeur .xlsx est attendu (reçu {document.nom})"
            )
        tableau = import_tableau_xlsx(
            chemin,
            feuille=payload.get("feuille"),
            ligne_entete=int(payload.get("ligne_entete") or 0),
            colonne_min=int(payload.get("colonne_min") or 1),
            colonne_max=payload.get("colonne_max"),
            ligne_min=int(payload.get("ligne_min") or 1),
            ligne_max=payload.get("ligne_max"),
            titre_tableau=payload.get("titre_tableau"),
        )
        grille = tableau.grille()
        return {
            "document_id": str(document.id),
            "tableau": tableau.model_dump(mode="json", exclude_none=True),
            "resume": {
                "lignes": len(grille),
                "colonnes": tableau.largeur_grille(),
                "lignes_entete": tableau.nb_lignes_entete(),
            },
            "message": (
                "Placer ce bloc ``tableau`` tel quel dans ``document_spec.blocs`` : "
                "fusions, trames et largeurs du classeur sont conservées. "
                "Ne pas ressaisir les cellules à la main."
            ),
        }

    def _lire(payload: dict[str, Any]) -> dict[str, Any]:
        """Lit un document : métadonnées **et** aperçu borné de son texte.

        Le nom du tool dit « lire » : ne rendre que les métadonnées faisait
        relire l'agent en boucle (incrément 53). L'aperçu reste borné
        (``APERCU_TEXTE``) et dit s'il est tronqué ; pour un contenu précis,
        l'agent enchaîne ``chercher_dans_document`` puis
        ``lire_element_document`` (``hit['relire']``).
        """
        policy.require(identite.agent_id, "document_read")
        from app.documents.extraction import supported_extensions

        document_id = _uuid(payload["document_id"], "document_id")
        service = service_factory()
        document = service.obtenir(document_id)
        _nom_fichier, chemin = service.telecharger(document_id)
        extension = chemin.suffix.lower().lstrip(".")
        sortie: dict[str, Any] = {
            "document_id": str(document.id),
            "nom": document.nom,
            "statut": document.statut,
            "type_document": document.type_document,
            "mime_type": document.mime_type,
            "version": document.version,
            "taille_octets": chemin.stat().st_size,
        }
        if extension not in supported_extensions():
            return {
                **sortie,
                "texte_lisible": False,
                "apercu_texte": None,
                "message": (
                    f"Aucun extracteur de texte pour .{extension or '?'} : le "
                    "document existe, son contenu n'est pas lisible par les outils."
                ),
            }
        extrait = service.extraire_texte(document_id, max_chars=APERCU_EXTRACTION)
        apercu = extrait.texte[:APERCU_TEXTE]
        return {
            **sortie,
            "texte_lisible": True,
            "nb_caracteres": extrait.nb_caracteres,
            "nb_pages": extrait.nb_pages,
            "nb_feuilles": extrait.nb_feuilles,
            "apercu_tronque": extrait.tronque or len(apercu) < len(extrait.texte),
            "apercu_texte": apercu,
            "message": (
                "Aperçu borné du DÉBUT du document (tableaux compris pour un "
                "DOCX). Pour un contenu précis : chercher_dans_document "
                "(mot-clé) puis lire_element_document avec hit['relire'] ; "
                "read_document_range pour une plage d'éléments."
            ),
        }

    def _inspecter(payload: dict[str, Any]) -> dict[str, Any]:
        policy.require(identite.agent_id, "document_read")
        from app.documents.inspection import inspecter_docx, inspecter_pdf, inspecter_xlsx

        service = service_factory()
        document_id = _uuid(payload["document_id"], "document_id")
        document = service.obtenir(document_id)
        _nom, chemin = service.telecharger(document_id)
        suffixe = chemin.suffix.lower()
        if suffixe == ".docx":
            structure = inspecter_docx(chemin)
        elif suffixe == ".pdf":
            structure = inspecter_pdf(chemin)
        elif suffixe == ".xlsx":
            structure = inspecter_xlsx(chemin)
        else:
            raise ValidationError(f"Inspection non supportée pour {document.nom}")
        return {"document_id": str(document.id), "nom": document.nom, **structure}

    def _lire_plage(payload: dict[str, Any]) -> dict[str, Any]:
        """Lit une ou plusieurs plages d'un document (docx, pdf, xlsx).

        ``plages`` est une **liste** : plusieurs plages sont lues en un appel
        et rendues sous ``plages`` (une entrée par plage, dans l'ordre
        demandé) — même contrat groupé que ``lire_element_document`` pour ses
        index et ``chercher_dans_document`` pour ses mots-clés. Une plage en
        échec est **dite dans son entrée** sans faire perdre les autres
        lectures ; une cause documentaire (PDF chiffré, feuille inconnue…)
        vaut pour toutes et remonte une seule fois. Une plage seule (ou les
        bornes scalaires ``from``/``to``) garde le retour unitaire d'origine.
        """
        policy.require(identite.agent_id, "document_read")
        from app.documents.inspection import lire_docx_plage, lire_pdf_plage, lire_xlsx_plage

        service = service_factory()
        document_id = _uuid(payload["document_id"], "document_id")
        _nom, chemin = service.telecharger(document_id)
        suffixe = chemin.suffix.lower()

        def lire_une(debut: int | None, fin: int | None) -> dict[str, Any]:
            if suffixe == ".docx":
                return lire_docx_plage(chemin, from_page=debut, to_page=fin)
            if suffixe == ".pdf":
                return lire_pdf_plage(chemin, from_page=debut, to_page=fin)
            if suffixe == ".xlsx":
                return lire_xlsx_plage(
                    chemin, sheet=payload.get("sheet"), from_row=debut, to_row=fin
                )
            raise ValidationError(f"Lecture de plage non supportée pour {chemin.suffix}")

        plages = payload.get("plages")
        if plages:
            # Ambiguïté refusée, jamais devinée (même règle que sous_index avec
            # plusieurs index) : la liste OU les bornes scalaires, jamais les deux.
            if any(
                payload.get(cle) is not None
                for cle in ("from_index", "to_index", "from_page", "to_page")
            ):
                raise ValidationError(
                    "'plages' et les bornes scalaires sont exclusifs : donner "
                    "plages=[{from_page: ..., to_page: ...}, ...] OU from_page/"
                    "to_page, jamais les deux",
                    details={"nb_plages": len(plages)},
                )
            if len(plages) == 1:
                # Une seule plage : retour unitaire d'origine (rétro-compatibilité
                # des appels et des locators existants).
                payload = {
                    **payload,
                    **{cle: valeur for cle, valeur in plages[0].items() if valeur is not None},
                }
                plages = None

        if not plages:
            # ``from_page``/``to_page`` sont des alias : même repère que
            # ``from_index``/``to_index`` (pages PDF, paragraphes DOCX, lignes
            # XLSX). L'ancienne forme reste acceptée pour ne rien casser.
            debut = payload.get("from_index")
            if debut is None:
                debut = payload.get("from_page")
            fin = payload.get("to_index")
            if fin is None:
                fin = payload.get("to_page")
            return lire_une(debut, fin)

        # Lecture groupée : une entrée par plage demandée.
        document = service.obtenir(document_id)
        entete: dict[str, Any] = {
            "document_id": str(document.id),
            "nom": document.nom,
            "statut": document.statut,
            "type_document": document.type_document,
            "feuille": payload.get("sheet"),
        }
        lectures: list[dict[str, Any]] = []
        for entree in plages:
            debut = entree.get("from_index")
            if debut is None:
                debut = entree.get("from_page")
            fin = entree.get("to_index")
            if fin is None:
                fin = entree.get("to_page")
            bornes = {"from_index": debut, "to_index": fin}
            if debut is None and fin is None:
                # Une plage sans bornes lirait le document entier : dans une
                # liste, ce n'est jamais ce que l'agent veut — refus explicite.
                lectures.append(
                    {
                        "plage": bornes,
                        "lu": False,
                        "erreur": (
                            "Chaque plage de 'plages' doit porter ses bornes "
                            "(from_page/to_page ou from_index/to_index)"
                        ),
                    }
                )
                continue
            try:
                resultat = lire_une(debut, fin)
            except ValidationError as refus:
                # L'échec d'une plage est **dit** dans son entrée (hors
                # document, plage inversée…) sans faire perdre les autres.
                lectures.append({"plage": bornes, "lu": False, "erreur": str(refus)})
                continue
            lectures.append({"plage": bornes, "lu": True, **resultat})
        nb_lues = sum(1 for lecture in lectures if lecture["lu"])
        unite = next(
            (lecture.get("unite") for lecture in lectures if lecture.get("unite")), None
        )
        return {
            **entete,
            "unite": unite,
            "nb_plages": len(lectures),
            "nb_lues": nb_lues,
            "nb_echecs": len(lectures) - nb_lues,
            "plages": lectures,
            "message": (
                "Une entrée de 'plages' par plage demandée, dans l'ordre : "
                "chacune garde le contrat d'un appel unitaire (unite, debut, "
                "fin, total, elements/texte). Une entrée 'lu': false porte "
                "'erreur' (les autres lectures restent valables). Une plage "
                "seule rend le retour unitaire d'origine."
            ),
        }

    def _chercher(payload: dict[str, Any]) -> dict[str, Any]:
        """Recherche ciblée : pages + extraits bornés (remplace le shell/grep).

        Aucun chemin n'est manipulé ici : le service téléverse dans le stockage
        contrôlé, ``app.documents.recherche`` fouille le fichier en mémoire et
        borne le retour (extraits courts, plafond de résultats).

        ``mot_cle`` est une **liste** : plusieurs mots-clés sont fouillés en un
        seul appel et rendus sous la même forme — une entrée par mot-clé, dans
        l'ordre demandé (``recherches``). Un mot-clé unique garde le retour
        unitaire d'origine (rétro-compatibilité des appels et des tests).
        Aucune erreur n'est avalée : une cause **documentaire** (PDF chiffré,
        feuille inconnue…) vaut pour tous les mots-clés et remonte donc une
        seule fois, pas N fois.
        """
        policy.require(identite.agent_id, "document_read")
        from app.documents.recherche import (
            chercher_dans_docx,
            chercher_dans_pdf,
            chercher_dans_xlsx,
        )

        service = service_factory()
        document_id = _uuid(payload["document_id"], "document_id")
        document = service.obtenir(document_id)
        _nom, chemin = service.telecharger(document_id)
        suffixe = chemin.suffix.lower()
        mots_cles: list[str] = list(payload.get("mot_cle") or [])
        options = {"max_resultats": payload.get("max_resultats")}

        def fouiller(mot_cle: str) -> dict[str, Any]:
            if suffixe == ".docx":
                return chercher_dans_docx(chemin, mot_cle, **options)
            if suffixe == ".pdf":
                return chercher_dans_pdf(chemin, mot_cle, **options)
            if suffixe == ".xlsx":
                return chercher_dans_xlsx(
                    chemin,
                    mot_cle,
                    feuille=payload.get("feuille"),
                    **options,
                )
            raise ValidationError(
                f"Recherche non supportée pour {document.nom} "
                "(formats acceptés : docx, pdf, xlsx)"
            )

        def enrichir(resultat: dict[str, Any]) -> dict[str, Any]:
            """Ajoute ``document_id`` aux locators ``relire`` des hits.

            L'agent appelle alors lire_element_document **tel quel**, sans
            reconstruire les arguments (c'est ce qui faisait reboucler les
            lectures : traduire « page » en appel d'outil était laissé au modèle).
            """
            for hit in resultat.get("resultats") or []:
                relire = hit.get("relire")
                if isinstance(relire, dict):
                    relire["arguments"] = {
                        "document_id": str(document.id),
                        **(relire.get("arguments") or {}),
                    }
            return resultat

        commun = {
            "document_id": str(document.id),
            "nom": document.nom,
            "statut": document.statut,
            "type_document": document.type_document,
        }
        if len(mots_cles) == 1:
            return {**commun, **enrichir(fouiller(mots_cles[0]))}

        recherches: list[dict[str, Any]] = []
        for mot_cle in mots_cles:
            resultat = enrichir(fouiller(mot_cle))
            # Le message du moteur décrit le contrat commun : il est porté une
            # seule fois au niveau de l'appel, pas répété par mot-clé.
            recherches.append(
                {cle: valeur for cle, valeur in resultat.items() if cle != "message"}
            )
        return {
            **commun,
            "format": suffixe.lstrip("."),
            "nb_recherches": len(recherches),
            "mots_cles": list(mots_cles),
            "nb_resultats": sum(entree["nb_resultats"] for entree in recherches),
            "nb_correspondances": sum(
                entree["nb_correspondances"] for entree in recherches
            ),
            "tronque": any(entree["tronque"] for entree in recherches),
            "recherches": recherches,
            "message": (
                "Une entrée de 'recherches' par mot-clé, dans l'ordre demandé : "
                "chacune garde le contrat d'un appel unitaire (mot_cle, unite, "
                "total, nb_resultats, tronque, resultats, relire). 'tronque' au "
                "niveau de l'appel = au moins une recherche bornée ; affiner le "
                "mot-clé concerné plutôt que de relire le document en entier."
            ),
        }

    def _lire_element(payload: dict[str, Any]) -> dict[str, Any]:
        """Lit un ou plusieurs éléments précis d'un document (docx, pdf, xlsx).

        C'est le pendant de ``chercher_dans_document`` : un tableau, une ligne de
        tableau, un en-tête, une page, une ligne de feuille — tout élément du
        document est lisible, sans relire le document entier.

        ``index`` est une **liste** : plusieurs éléments sont lus en un appel et
        rendus sous ``elements`` (une entrée par index, dans l'ordre demandé).
        Un index en échec (hors document…) est **dit dans son entrée** sans faire
        perdre les autres lectures — même contrat que ``read_urls`` pour ses
        adresses. Un index unique garde le retour unitaire d'origine (forme des
        locators ``hit['relire']``).
        """
        policy.require(identite.agent_id, "document_read")
        from app.documents.elements import lire_element

        service = service_factory()
        document_id = _uuid(payload["document_id"], "document_id")
        document = service.obtenir(document_id)
        _nom, chemin = service.telecharger(document_id)
        indexes = payload.get("index")
        type_element = payload.get("type_element") or "element"
        commun: dict[str, Any] = {
            "type_element": type_element,
            "sous_index": payload.get("sous_index"),
            "feuille": payload.get("feuille"),
            "max_lignes": payload.get("max_lignes"),
            "max_caracteres": payload.get("max_caracteres"),
        }
        entete = {
            "document_id": str(document.id),
            "nom": document.nom,
            "statut": document.statut,
            "type_document": document.type_document,
        }

        # Un index unique (ou absent : le moteur dira l'obligation) : retour
        # unitaire inchangé.
        if not indexes or len(indexes) == 1:
            resultat = lire_element(
                chemin, index=(indexes[0] if indexes else None), **commun
            )
            return {**entete, **resultat}

        # Lecture groupée : deux ambiguïtés refusées, jamais devinées.
        if type_element == "feuille":
            raise ValidationError(
                "type_element='feuille' décrit une feuille nommée : il n'accepte "
                "pas une liste d'index (utiliser type_element='ligne' avec les "
                "index de lignes)",
                details={"type_element": type_element, "index": indexes},
            )
        if commun["sous_index"] is not None:
            raise ValidationError(
                "sous_index ne s'applique qu'à UN index : appeler une fois par "
                "ligne, ou lire le tableau entier avec sous_index omis",
                details={"index": indexes, "sous_index": commun["sous_index"]},
            )

        elements: list[dict[str, Any]] = []
        for numero in indexes:
            try:
                resultat = lire_element(chemin, index=numero, **commun)
            except ValidationError as refus:
                # L'échec d'un index est **dit** (message exact : hors document,
                # type absent du format…) sans faire perdre les autres lectures.
                elements.append({"index": numero, "lu": False, "erreur": str(refus)})
                continue
            elements.append({"index": numero, "lu": True, **resultat})
        nb_lus = sum(1 for element in elements if element["lu"])
        return {
            **entete,
            "type_element": type_element,
            "nb_demandes": len(indexes),
            "nb_lus": nb_lus,
            "nb_echecs": len(elements) - nb_lus,
            "elements": elements,
            "message": (
                "Une entrée de 'elements' par index demandé, dans l'ordre : les "
                "extraits sont bornés (max_lignes / max_caracteres). Une entrée "
                "'lu': false porte 'erreur' (les autres lectures restent "
                "valables). Un seul index rend le retour unitaire d'origine."
            ),
        }

    def _cloner(payload: dict[str, Any]) -> dict[str, Any]:
        policy.require(identite.agent_id, "document_propose")
        from app.documents.cloning import cloner_docx, cloner_pdf, cloner_xlsx

        fmt = str(payload["format"]).strip().lower()
        if fmt not in _FORMATS:
            raise ValidationError(
                f"Format inconnu : {payload['format']} (attendus : {', '.join(sorted(_FORMATS))})"
            )
        extension, _mime = _FORMATS[fmt]
        service = service_factory()
        _nom, chemin = service.telecharger(
            _uuid(payload["reference_document_id"], "reference_document_id")
        )
        if fmt == "docx":
            octets = cloner_docx(
                chemin,
                titre=payload.get("titre") or "",
                paragraphes=payload.get("paragraphes"),
            )
        elif fmt == "xlsx":
            octets = cloner_xlsx(
                chemin,
                titre_feuille=payload.get("titre") or "Feuille",
                lignes=payload.get("tableau") or [],
            )
        else:
            octets = cloner_pdf(
                chemin,
                titre=payload.get("titre") or "",
                paragraphes=payload.get("paragraphes"),
            )
        return _deposer(octets, payload, extension)

    def _remplir(payload: dict[str, Any]) -> dict[str, Any]:
        policy.require(identite.agent_id, "document_propose")
        from app.documents.cloning import remplir_template_docx

        service = service_factory()
        _nom, chemin = service.telecharger(
            _uuid(payload["template_document_id"], "template_document_id")
        )
        if chemin.suffix.lower() != ".docx":
            raise ValidationError("fill_template n'accepte que des templates DOCX")
        octets = remplir_template_docx(chemin, payload.get("valeurs") or {})
        return _deposer(octets, payload, ".docx")

    return [
        TypedTool(
            name="generer_document",
            description=(
                "Génère un livrable et le dépose comme PROPOSITION (validation "
                "humaine obligatoire). Deux usages : "
                "(1) ``document_spec`` — le document structuré complet "
                "(métadonnées, sections, tableaux, images, provenances) : c'est le "
                "chemin attendu pour une offre, un rapport ou une fiche, le code "
                "se charge de la mise en forme professionnelle (couverture, "
                "en-tête, pagination, styles) ; "
                "(2) ``titre`` + ``paragraphes`` + ``tableau`` — dépôt rapide d'un "
                "contenu simple. "
                "Fournir soit l'un, soit l'autre. Le retour contient "
                "``validation`` (structure/contenu/style) et ``warnings`` : lire "
                "ces constats et corriger avant de présenter le document. "
                "Rattachement : une ancre métier réelle classe le fichier sous "
                "l'objet ; une ancre absente le range sous generated/{id} "
                "(non classé) — ne jamais inventer d'UUID. "
                "Après succès : render_document, puis "
                "proposer_telechargement_document."
            ),
            handler=_generer,
            input_schema=GenererDocumentInput,
            tags=("documents", "generation"),
        ),
        TypedTool(
            name="generer_document_professionnel",
            description=(
                "GÉNÉRATION PRIORITAIRE d'un livrable DOCX, PDF, PPTX ou XLSX : écris le "
                "**corps** du document en HTML avec les classes du thème "
                "(cover, toc, lead, table.kpi, table.data, callout, table.layout, "
                "badge, page-break, signature) et le moteur se charge du design — "
                "thème professionnel injecté (couverture pleine page, en-tête, "
                "pied 'n / N', sommaire aux numéros de page CALCULÉS, styles Word "
                "natifs pour un DOCX). Le fichier est déposé comme PROPOSITION "
                "(validation humaine obligatoire). "
                "À utiliser par DÉFAUT pour tout document DOCX/PDF : rapports, "
                "offres, notes, comptes rendus, propositions, courriers, CV, "
                "fiches personnalisées. "
                "PRÉSENTATION : format='pptx' rend un deck 16:9 natif "
                "(vraies formes, tableaux et images PowerPoint, notes "
                "présentateur, thème appliqué) à partir du même ``contenu_html`` : "
                "<section class='cover'> puis une <section class='slide'> par "
                "diapositive (h1, p.lead, ul/ol, table.kpi, table.data, figure, "
                "div.callout, aside.notes). Le moteur REFUSE une diapositive "
                "dont le texte déborderait (message « scinde la diapositive ») "
                "plutôt que de le rogner. "
                "CLASSEUR : format='xlsx' rend chaque table.data en feuille Excel "
                "stylée (thème, en-tête figé, filtre automatique) : exige au moins "
                "un tableau. "
                "Graphiques de DONNÉES via le paramètre ``graphiques`` (ancre "
                "<figure data-graphique='id'></figure>) ; SCHÉMAS et "
                "ILLUSTRATIONS en écrivant toi-même un <svg> inline dans "
                "``contenu_html`` (organigramme, flèches, cycle, frise, "
                "diagramme) : il est nettoyé, rendu vectoriel en PDF et "
                "converti en image en DOCX. "
                "NE PAS écrire de <style> ni de CSS en ligne : le thème est "
                "injecté (un <style> est supprimé et signalé en avertissement). "
                "Autres voies : ``generer_document_html`` pour un document "
                "LIBRE (styles entièrement écrits par l'agent, aucun thème "
                "imposé) ; ``generer_document(document_spec)`` pour une structure "
                "stricte avec provenances. "
                "Le retour porte ``pages``, ``avertissements`` (CSS interdit, "
                "Markdown, placeholders, formules creuses, tableau trop large…) "
                "et ``documents`` (pour un DOCX, le PDF jumeau est déposé aussi) : "
                "lire les avertissements et les corriger avant de présenter. "
                "Données : uniquement des informations réelles — une donnée "
                "absente s'écrit '(à compléter)', jamais une invention. "
                "Rattachement : même règle d'ancre que ``generer_document``. "
                "Après succès : render_document puis "
                "proposer_telechargement_document."
            ),
            handler=_generer_professionnel,
            input_schema=GenererDocumentProfessionnelInput,
            tags=("documents", "generation", "professionnel"),
        ),
        TypedTool(
            name="generer_document_html",
            description=(
                "Génération d'un document **LIBRE** : écris toi-même tout le "
                "HTML+CSS (couleurs, polices, encadrés, images data-URI ou "
                "http(s), tableaux stylés, listes, liens) et le fichier est "
                "converti puis déposé comme PROPOSITION (validation humaine "
                "obligatoire). "
                "Formats : 'docx' (conversion fidèle du HTML+CSS) ou 'pdf' "
                "(même contenu rendu par le moteur CARSO — pas de copie pixel du "
                "DOCX, aucun convertisseur bureautique sur ce serveur). "
                "Réserver à une demande EXPLICITE de design imposé ou de mise en "
                "page hors thème : pour un livrable DOCX/PDF ordinaire, utiliser "
                "``generer_document_professionnel`` (PRIORITAIRE — thème "
                "professionnel, couverture, sommaire numéroté, avertissements "
                "qualité). ``generer_document(document_spec)`` reste le chemin "
                "attendu pour les livrables à structure stricte (offre "
                "technique/financière avec couverture et provenances). "
                "Limites : les images distantes nécessitent ``requests``. "
                "Tableaux : colspan/rowspan HTML sont gérés (fusions DOCX "
                "réelles ; PDF : la cellule fusionnée reste sur sa première "
                "colonne). Une ligne de total pleine largeur s'écrit donc "
                "sans crainte : <td colspan=\"N\"> où N = nombre de colonnes. "
                "Données : uniquement des informations réelles — une donnée "
                "absente s'écrit '(à compléter)', jamais une invention. "
                "Rattachement : même règle d'ancre que ``generer_document``. "
                "Après succès : proposer_telechargement_document (et "
                "render_document pour un PDF)."
            ),
            handler=_generer_html,
            input_schema=GenererDocumentHtmlInput,
            tags=("documents", "generation", "html"),
        ),
        TypedTool(
            name="analyze_document_reference",
            description=(
                "Analyse un document de référence et renvoie deux choses "
                "distinctes : son ``content_structure`` (plan : sections et "
                "niveaux) et son ``style_spec`` (format, marges, typographie, "
                "couleurs, tableaux, en-tête/pied). "
                "À appeler AVANT de générer lorsqu'un document modèle définit la "
                "structure ou l'identité visuelle attendue : réutiliser le style "
                "n'est pas recopier le contenu de la référence (le texte de "
                "l'en-tête d'un client n'est jamais repris). "
                "Lecture seule."
            ),
            handler=_reference,
            input_schema=ReferenceDocumentInput,
            tags=("documents", "analyse"),
        ),
        TypedTool(
            name="valider_document",
            description=(
                "Vérifie un document structuré avant de le générer (ou après "
                "correction) : sections attendues du profil, contenu réellement "
                "suffisant, cohérence d'un total budgétaire avec la somme de ses "
                "lignes, métadonnées obligatoires, niveaux de titre, largeurs de "
                "tableau, pagination. "
                "Renvoie ``ok``, l'état par portée (structure/contenu/style) et "
                "la liste des constats avec leur code. "
                "À utiliser quand un document ne doit pas être présenté avant "
                "d'être complet : un livrable court et vide est un échec, pas un "
                "brouillon."
            ),
            handler=_verifier,
            input_schema=ValiderDocumentInput,
            tags=("documents", "validation"),
        ),
        TypedTool(
            name="render_document",
            description=(
                "Rend les pages d'un document PDF en images, pour VOIR le rendu "
                "réel au lieu de le supposer. "
                "À appeler après une génération, puis "
                "``afficher_apercu_document`` pour montrer les pages à "
                "l'utilisateur dans le fil. "
                "Limite connue : seuls les PDF sont rendus (aucune conversion "
                "bureautique sur ce serveur) — pour un livrable, générer "
                "``format='pdf'`` (ou réutiliser la version PDF produite avec "
                "le DOCX) puis appeler ce tool."
            ),
            handler=_rendre,
            input_schema=RenderDocumentInput,
            tags=("documents", "inspection"),
        ),
        TypedTool(
            name="corriger_document",
            description=(
                "Corrige un document structuré de façon CIBLÉE : une cellule, un bloc, "
                "les propriétés d'un tableau, un token de style ou les métadonnées — "
                "sans régénérer le document. "
                "À préférer dès qu'une relecture remonte des corrections précises (un "
                "montant, une trame, une colonne qui déborde, une phrase) : tout ce qui "
                "était déjà bon est conservé, et un appel de génération complet est évité. "
                "Avec ``document_id``, le résultat devient la VERSION SUIVANTE du document "
                "(l'ancienne reste sur disque) ; sinon il est déposé en proposition. "
                "Le retour contient le ``document_spec`` corrigé (à réutiliser pour la "
                "suite), ``operations_appliquees`` (journal lisible), ``validation`` et "
                "``warnings``. Enchaîner : render_document → proposer_telechargement_document "
                "→ rafraichir_application."
            ),
            handler=_corriger,
            input_schema=CorrigerDocumentInput,
            tags=("documents", "correction"),
        ),
        TypedTool(
            name="importer_tableau_xlsx",
            description=(
                "Importe un tableau d'un classeur XLSX et rend le bloc ``tableau`` "
                "prêt à insérer dans ``document_spec.blocs``. "
                "À utiliser AVANT de décrire un tableau à la main quand une annexe "
                "ou une matrice existe déjà dans un classeur : fusions de cellules, "
                "trames, largeurs et en-tête sont reprises telles quelles. "
                "Le retour contient ``tableau`` (à placer tel quel), ``resume`` "
                "(lignes/colonnes) et les erreurs explicites (feuille inconnue, "
                "plage vide, tableau trop large). "
                "Lecture seule : aucun document n'est créé."
            ),
            handler=_importer_tableau,
            input_schema=ImporterTableauInput,
            tags=("documents", "tableau", "analyse"),
        ),
        TypedTool(
            name="lire_document",
            description=(
                "Lit un document : métadonnées (nom, type, version, taille) ET "
                "aperçu borné de son texte (début du document, tableaux "
                "compris pour un DOCX). Pour un contenu précis, ne pas s'en "
                "servir seul : chercher_dans_document puis "
                "lire_element_document. Lecture seule, aucun chemin disque."
            ),
            handler=_lire,
            input_schema=LireDocumentInput,
            tags=("documents", "lecture"),
        ),
        TypedTool(
            name="read_document",
            description=(
                "Lit un document : métadonnées (nom, type, version, taille) ET "
                "aperçu borné de son texte (début du document). Alias de "
                "lire_document. Lecture seule, aucun chemin disque exposé."
            ),
            handler=_lire,
            input_schema=LireDocumentInput,
            tags=("documents", "lecture"),
        ),
        TypedTool(
            name="get_document_structure",
            description=(
                "Inspecte la structure d'un document (styles DOCX, pages PDF, "
                "feuilles XLSX) ET son unité de lecture : ``nb_pages`` (exact "
                "pour un PDF ; ``null`` pour un DOCX/XLSX, qui n'ont pas de "
                "pages physiques) + ``unite_pagination`` (pages | elements | "
                "lignes). Pour un DOCX : ``nb_elements``, ``nb_paragraphes``, "
                "``nb_tableaux`` et ``hierarchie`` dont CHAQUE entrée porte son "
                "``element`` — l'index à lire. Lecture seule."
            ),
            handler=_inspecter,
            input_schema=InspecterDocumentInput,
            tags=("documents", "lecture"),
        ),
        TypedTool(
            name="read_document_range",
            description=(
                "Lit une ou plusieurs plages de document : ``from_page``/"
                "``to_page`` (1-indexés, bornes inclusives ; alias "
                "``from_index``/``to_index``) pour une plage, ou ``plages`` "
                "pour PLUSIEURS en UN appel : plages: [{from_page: 10, "
                "to_page: 12}, {from_page: 40, to_page: 42}] → ``plages`` au "
                "retour (une entrée par plage, dans l'ordre ; une entrée en "
                "échec porte 'erreur' sans faire perdre les autres lectures). "
                "Unité = celle annoncée par get_document_structure : pages "
                "PDF, ÉLÉMENTS DOCX (paragraphes ET tableaux), lignes XLSX. "
                "Le retour porte ``unite``, ``debut``/``fin``/``total``, "
                "``elements`` (type + texte de chaque bloc) et ``texte``. Lire "
                "des plages ciblées, jamais le document entier : "
                "chercher_dans_document d'abord. Lecture seule."
            ),
            handler=_lire_plage,
            input_schema=LirePlageInput,
            tags=("documents", "lecture"),
        ),
        TypedTool(
            name="chercher_dans_document",
            description=(
                "Recherche ciblée dans un document (docx, pdf, xlsx) : renvoie "
                "les éléments qui contiennent le mot-clé — "
                "``[{page, content, type_bloc, relire, ...}]``, plus "
                "``nb_resultats``/``tronque``. ``mot_cle`` accepte une LISTE : "
                "``mot_cle: ['budget', 'lot 3']`` fouille plusieurs mots-clés en "
                "UN appel et rend ``recherches`` (une entrée par mot-clé, même "
                "contrat qu'un appel unitaire) — un seul aller-retour au lieu "
                "d'un par mot-clé. À appeler AVANT toute autre lecture : c'est "
                "le remplaçant du grep/shell et des lectures en boucle. 'page' "
                "est le repère de lecture du format (élément DOCX — paragraphe "
                "ou tableau —, page PDF, ligne XLSX) et chaque hit porte "
                "``relire`` : les arguments EXACTS de lire_element_document "
                "pour le relire à l'identique. "
                "En-têtes et pieds d'un DOCX sont cherchés aussi. Bornes : 50 "
                "résultats par défaut (200 au plus) PAR mot-clé, 8 mots-clés au "
                "plus par appel, contexte tronqué autour de chaque hit — "
                "affiner le mot-clé si 'tronque' est true, jamais relire le "
                "document en entier. Lecture seule."
            ),
            handler=_chercher,
            input_schema=ChercherDocumentInput,
            tags=("documents", "lecture", "recherche"),
        ),
        TypedTool(
            name="lire_element_document",
            description=(
                "Lit un ou plusieurs éléments d'un document, avec le repère "
                "rendu par chercher_dans_document (``hit['relire']``) : "
                "'element' (élément de flux DOCX, page PDF, ligne XLSX), "
                "'tableau' avec ``sous_index`` = ligne, 'paragraphe', "
                "'entete'/'pied' (DOCX, par section), 'feuille' (XLSX). "
                "``index`` accepte une LISTE : index: [100, 120, 130] lit "
                "plusieurs éléments en UN appel et rend ``elements`` (une "
                "entrée par index ; une entrée en échec porte 'erreur' sans "
                "faire perdre les autres lectures) — un seul aller-retour au "
                "lieu d'un par élément. Un index seul garde le retour unitaire. "
                "Retour borné (``max_lignes``, ``max_caracteres``) qui dit sa "
                "troncature. Lecture seule."
            ),
            handler=_lire_element,
            input_schema=LireElementInput,
            tags=("documents", "lecture"),
        ),
        TypedTool(
            name="clone_document_structure",
            description=(
                "Clone la structure d'un document de référence et y place un "
                "contenu fourni. Déposé en statut 'proposed' (HITL)."
            ),
            handler=_cloner,
            input_schema=ClonerDocumentInput,
            tags=("documents", "generation"),
        ),
        TypedTool(
            name="fill_template",
            description=(
                "Remplit les placeholders {{cle}} d'un template DOCX. "
                "Déposé en statut 'proposed' (HITL)."
            ),
            handler=_remplir,
            input_schema=RemplirTemplateInput,
            tags=("documents", "generation"),
        ),
    ]
