"""Schémas d'entrée des tools documentaires (ADR 0008).

Extrait de ``app.tools.documents`` sans modification de contenu : ces classes
sont l'interface que le modèle lit (description, bornes, énumérations). Elles
ne portent aucune règle métier, seulement la validation du contrat d'appel.

Le vocabulaire fermé (``TypeFormat``, ``TypeAncre``, ``TypeStyle``,
``TypeProfil``, ``TypePortee``, ``TypeElement``) et les bornes partagées avec
les outils vivent ici, parce que les schémas en dépendent.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

from app.documents.repair import Operation
from app.documents.spec import DocumentSpec

#: Vocabulaire fermé des formats : le modèle ne peut pas inventer ``word`` ou ``exe``.
TypeFormat = Literal["docx", "xlsx", "pdf"]

#: Vocabulaire fermé des ancres métier réelles (doit rester aligné sur ``_ANCRES``).
#: Ancres de la chaîne « copier le modèle puis l'éditer » : ce sont les seuls
#: objets qui ont un dossier documentaire ET un périmètre d'accès pour la copie.
ModeleAncre = Literal["offre", "mission", "session"]

TypeAncre = Literal[
    "organisation",
    "appel",
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
    "appel": "appel_id",
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

class ModeleRefInput(BaseModel):
    """Référence d'un modèle d'organisation, pour en reprendre l'identité visuelle."""

    organisation_id: UUID = Field(description="Organisation cliente propriétaire du modèle.")
    type_document: str = Field(
        min_length=1,
        max_length=60,
        description=(
            "Type du MODÈLE dans le registre (ex. 'offre_technique', "
            "'offre_financiere', 'rapport', 'fiche_technique')."
        ),
    )


class ImageDocumentInput(BaseModel):
    """Une image **du stockage CARSO** insérée dans le HTML d'un livrable.

    L'agent désigne l'image par son ``document_id`` (document image déjà
    déposé) ou par son chemin logique : il n'a pas à coller un base64, qui
    était tronqué en cours d'émission et laissait le document sans image.
    Le serveur résout la référence **avec le périmètre du compte** et
    l'embarque dans le fichier rendu.
    """

    id: str = Field(
        min_length=1,
        max_length=40,
        description=(
            "Identifiant d'ancre, repris tel quel dans le HTML : "
            "<figure data-image='id'></figure>."
        ),
    )
    document_id: UUID | None = Field(
        default=None,
        description=(
            "UUID d'un document **image** (PNG, JPEG, GIF, WebP) déjà déposé. "
            "Ne jamais inventer d'identifiant ; l'objet doit être dans le "
            "périmètre du compte."
        ),
    )
    chemin: str | None = Field(
        default=None,
        max_length=400,
        description=(
            "Chemin **logique** de l'image dans le stockage (ex. "
            "'missions/<id>/plan.png'), jamais un chemin absolu."
        ),
    )
    legende: str | None = Field(
        default=None,
        max_length=300,
        description="Légende (figcaption) et texte alternatif de l'image.",
    )

    @model_validator(mode="after")
    def _source_unique(self) -> ImageDocumentInput:
        """Exactement une source : un identifiant de document ou un chemin."""
        par_document = self.document_id is not None
        par_chemin = bool((self.chemin or "").strip())
        if par_document == par_chemin:
            raise ValueError(
                "Une seule source par image : `document_id` OU `chemin`."
            )
        return self


class GenererDocumentInput(BaseModel):
    """Entrée typée : format, nom, ancre optionnelle, contenu minimal générique.

    Le contenu est volontairement **générique** (listes de lignes) : c'est de
    la mise en forme, pas une règle métier. Les cas d'usage spécialisés (offre
    de formation, fiche de présence) composeront leurs propres outils à partir
    de ``build_docx``/``build_pdf`` — ce tool générique reste la porte commune.
    """

    format: TypeFormat | None = Field(
        default=None,
        description=(
            "Format cible : 'docx' | 'xlsx' | 'pdf'. Omis, il est déduit de "
            "l'extension de ``nom_fichier`` (.pdf -> PDF …), sinon DOCX."
        ),
    )
    nom_fichier: str = Field(
        description=(
            "Nom logique du document. Son extension fixe le format quand "
            "``format`` est omis."
        )
    )
    html: str | None = Field(
        default=None,
        max_length=400_000,
        description=(
            "VOIE PRINCIPALE : corps du document en HTML sémantique, habillé par "
            "la feuille CARSO et rendu en DOCX ou PDF. Blocs : <div class=\"h1\"> "
            "à h6, <p class=\"p\">, <ul class=\"liste\">, <table class=\"tableau\">, "
            "<div class=\"encadre\">, <blockquote class=\"citation\">, "
            "<div class=\"saut-page\">. En ligne : bold, italic, souligne, "
            "reference. Aucun « — » : « - » ou une virgule. Images : "
            "<img src=\"https://…\"> (téléchargée, vérifiée, embarquée), ou une "
            "image **du stockage CARSO** par <figure data-image=\"id\"></figure> + "
            "le paramètre `images` (document_id ou chemin logique) — jamais de "
            "base64 écrit à la main. Graphiques : <figure data-graphique=\"id\"> "
            "rempli par `graphiques`."
        ),
    )
    css: str | None = Field(
        default=None,
        max_length=40_000,
        description=(
            "CSS de l'agent, appliqué APRÈS la feuille CARSO : reprendre une "
            "classe (.h2 { color: #7a1f1f }) ou en créer une. Les tailles en pt "
            "sont alignées sur l'échelle du document (8, 9, 11, 13, 16, 20, 26) ; "
            "préférer em pour rester relatif au corps choisi."
        ),
    )
    auteur: str | None = Field(
        default=None, max_length=160, description="Auteur inscrit dans les métadonnées."
    )
    graphiques: list[GraphiqueInput] | None = Field(
        default=None,
        description=(
            "Graphiques dessinés par le serveur, ancrés dans le HTML par "
            "<figure data-graphique=\"id\"></figure> (même contrat que "
            "generer_document_professionnel). Ne jamais fabriquer une image de "
            "graphique."
        ),
    )
    illustrations: list[IllustrationInput] | None = Field(
        default=None,
        description=(
            "Illustrations dessinées par le serveur, ancrées par "
            "<figure data-illustration=\"id\"></figure>."
        ),
    )
    images: list[ImageDocumentInput] | None = Field(
        default=None,
        max_length=20,
        description=(
            "Images **du stockage CARSO**, ancrées dans le HTML par "
            "<figure data-image='id'></figure>. Chaque entrée désigne l'image "
            "par `document_id` (document image déposé) ou `chemin` logique : le "
            "serveur l'embarque et l'agent n'écrit jamais de base64. Une ancre "
            "sans entrée, ou une image introuvable, fait échouer l'appel."
        ),
    )
    type_document: str = Field(description="Vocabulaire TypeDocument (valide par le service).")
    ancre: TypeAncre | None = Field(
        default=None,
        description=(
            "Rattachement optionnel : 'organisation' | 'appel' | "
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
    propose_par: str | None = Field(
        default=None,
        description=(
            "Identifiant de l'agent proposant. Omis, l'agent authentifié du "
            "contexte est inscrit : ne jamais recopier ni inventer un identifiant."
        ),
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
            "HÉRITAGE : document structuré (metadata, mise_en_page, sources, "
            "blocs) pour une structure imposée — offre technique/financière, "
            "provenance des données. Le schéma des blocs est décrit plus bas. "
            "Pour un document libre, utiliser `html`."
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
    modele: ModeleRefInput | None = Field(
        default=None,
        description=(
            "Modèle d'organisation dont reprendre l'identité visuelle (polices, "
            "couleurs, marges, gabarit de page). Utile quand le modèle est un "
            "PDF (aucune structure éditable) ou pour un livrable rendu par le "
            "moteur CARSO. Pour modifier fidèlement le DOCX d'une organisation, "
            "la chaîne dédiée est ``copier_modele_document`` puis "
            "``remplacer_element_model`` / ``remplir_modele``."
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
    """Spec d'un graphique de données, commun aux outils de documents.

    Le SVG est construit par le serveur : l'agent ne fournit que des données
    réelles (lecture ou utilisateur), jamais une balise. Deux usages : paramètre
    ``graphiques`` avec ancre ``<figure data-graphique="id">``, ou bloc
    ``{"type": "graphique"}`` du ``document_spec`` (sans ``id``).
    """

    id: str = Field(
        min_length=1,
        max_length=64,
        description=(
            "Identifiant d'ancrage unique : le corps HTML porte "
            "<figure data-graphique=\"ce-meme-id\"></figure> à cet endroit."
        ),
    )
    type: Literal["bar", "line", "pie", "donut"] = Field(
        description=(
            "'bar' barres groupées ; 'line' courbes ; 'pie' camembert "
            "(≤ 5-6 parts) ; 'donut' anneau avec total au centre."
        )
    )
    titre: str | None = Field(
        default=None, max_length=200, description="Titre affiché au-dessus du graphique."
    )
    labels: list[str] = Field(
        min_length=1,
        max_length=60,
        description=(
            "Étiquettes de l'axe X (bar/line) ou parts (pie/donut) ; courtes "
            "(tronquées à l'affichage)."
        ),
    )
    series: list[SerieGraphiqueInput] | None = Field(
        default=None,
        description=(
            "bar/line : une ou plusieurs séries nommées, autant de valeurs que "
            "de labels."
        ),
    )
    valeurs: list[float] | None = Field(
        default=None,
        description=(
            "pie/donut : une valeur par label, toutes ≥ 0 et somme > 0."
        ),
    )
    y_label: str | None = Field(
        default=None, max_length=40, description="Titre de l'axe Y (bar/line)."
    )
    legende: str | None = Field(
        default=None,
        max_length=300,
        description="Légende de figure ; sans elle, le titre est repris.",
    )
    total_reference: float | None = Field(
        default=None,
        gt=0,
        description=(
            "pie/donut : total des pourcentages quand il diffère de la somme des "
            "parts ; par défaut la somme."
        ),
    )
    afficher_valeurs: bool = Field(
        default=False,
        description="pie/donut : montant de chaque part avant son pourcentage.",
    )
    largeur: int = Field(default=640, ge=200, le=2400, description="Largeur en pixels.")
    hauteur: int = Field(default=400, ge=150, le=1600, description="Hauteur en pixels.")


class ItemIllustrationInput(BaseModel):
    """Un item d'illustration : un titre court, un texte court, une icône nommée."""

    titre: str | None = Field(
        default=None, max_length=40, description="Titre de l'item (court : la place est fixe)."
    )
    texte: str | None = Field(
        default=None, max_length=80, description="Texte de l'item (court : deux lignes au plus)."
    )
    icone: str | None = Field(
        default=None,
        max_length=30,
        description=(
            "Icône au trait, par nom ('personne', 'equipe', 'cible', 'calendrier', "
            "'graphique', 'check', 'alerte'…) ; une icône inconnue est refusée avec "
            "la liste des noms valides."
        ),
    )


class IllustrationInput(BaseModel):
    """Spec d'une illustration de composition.

    L'agent choisit la composition et fournit des libellés courts ; géométrie,
    espacements, tailles et couleurs viennent du serveur. Voie recommandée pour
    un visuel sans chiffres. Bornes d'items : ``icones`` 2–4, ``etapes`` 2–5,
    ``cycle`` 2–5, ``pyramide`` 2–4, ``frise`` 2–6, ``comparaison`` 2.
    Comme les graphiques : ``illustrations: {id: spec}`` + ancre
    ``<figure data-illustration="id">``, ou bloc du ``document_spec``.
    """

    id: str = Field(
        min_length=1,
        max_length=64,
        description=(
            "Identifiant d'ancrage unique. Le corps HTML doit porter "
            "<figure data-illustration=\"ce-meme-id\"></figure> là où "
            "l'illustration doit apparaître."
        ),
    )
    type: Literal["icones", "etapes", "cycle", "pyramide", "frise", "comparaison"] = Field(
        description=(
            "'icones' : cartes d'atouts (2–4) ; 'etapes' : processus linéaire "
            "(2–5) ; 'cycle' : boucle (2–5) ; 'pyramide' : niveaux (2–4) ; "
            "'frise' : chronologie (2–6) ; 'comparaison' : deux colonnes (2)."
        )
    )
    titre: str | None = Field(default=None, max_length=120)
    items: list[ItemIllustrationInput] = Field(
        min_length=1,
        max_length=8,
        description="Les items de la composition, dans l'ordre de lecture.",
    )
    legende: str | None = Field(
        default=None, max_length=300, description="Légende de figure (figcaption)."
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

    format: Literal["docx", "pdf"] | None = Field(
        default=None,
        description=(
            "Format cible : 'docx' (fidèle au HTML+CSS) ou 'pdf'. Omis, il est "
            "déduit de l'extension de ``nom_fichier`` (.pdf -> PDF), sinon DOCX."
        ),
    )
    html: str = Field(
        min_length=1,
        max_length=300_000,
        description=(
            "Page HTML complète (ou fragment) avec ses <style> et, si besoin, "
            "des images data-URI ou http(s). Utiliser des styles inline ou une "
            "balise <style> : le rendu respecte couleurs, polices, alignements, "
            "bordures et fonds. Le <svg> est CONSERVÉ : il devient un PNG dans le "
            "DOCX et un bloc image dans le PDF (il n'est plus supprimé). Pour des "
            "figures dessinées par le serveur, préférer 'graphiques' / "
            "'illustrations' avec une ancre <figure data-graphique|data-illustration>. "
            "Contenu des données : uniquement des informations réelles issues de "
            "lectures (search_*, lire_document) ou de l'utilisateur — une donnée "
            "absente s'écrit '(à compléter)', jamais une invention."
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
            "Rattachement optionnel : 'organisation' | 'appel' | "
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
    propose_par: str | None = Field(
        default=None,
        description=(
            "Identifiant de l'agent proposant. Omis, l'agent authentifié du "
            "contexte est inscrit : ne jamais recopier ni inventer un identifiant."
        ),
    )
    css: str | None = Field(
        default=None,
        description="CSS additionnel appliqué après le <style> du HTML (facultatif).",
    )
    graphiques: list[GraphiqueInput] | None = Field(
        default=None,
        max_length=12,
        description=(
            "Graphiques de DONNÉES (barres, courbes, camembert, anneau) : chaque "
            "entrée porte un 'id' et remplace l'ancre <figure "
            "data-graphique='ce-meme-id'> du HTML. Le graphique est dessiné par "
            "le serveur et intégré au DOCX comme au PDF. À utiliser dès qu'un jeu "
            "de chiffres réels gagne à être comparé visuellement. Pour une "
            "illustration sans chiffres, utiliser 'illustrations' ; pour un "
            "schéma libre, un <svg> dans 'html'."
        ),
    )
    illustrations: list[IllustrationInput] | None = Field(
        default=None,
        max_length=12,
        description=(
            "Illustrations de composition (icônes, étapes, cycle, pyramide, "
            "frise, comparaison) : chaque entrée porte un 'id' et remplace "
            "l'ancre <figure data-illustration='ce-meme-id'> du HTML. Géométrie "
            "et couleurs fixées par le serveur — l'agent ne fournit que des "
            "libellés courts."
        ),
    )
#: Borne d'une section en rédaction progressive : chaque appel de l'agent doit
#: rester une **petite** génération — un fournisseur qui reçoit une génération
#: géante d'un seul tenant cesse d'émettre et le run meurt sans exécuter l'outil
#: (INC-105 : aucun document déposé, silence complet côté modèle).
_BORNE_SECTION_CARACTERES = 30_000

#: Borne du corps fourni d'un tenant (``action='generer'``). Une génération
#: plus longue fait taire le fournisseur : le flux s'interrompt après plusieurs
#: milliers de chunks et l'outil n'est jamais exécuté (constaté : 13 331 chunks
#: reçus, 300 s de silence, run mort). Au-delà, l'erreur renvoie vers la
#: rédaction progressive — elle le dit AVANT que le modèle ne s'épuise.
_BORNE_CORPS_CARACTERES = 30_000

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

    contenu_html: str | None = Field(
        default=None,
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
            "IMAGE DU STOCKAGE (photo, logo, plan scanné) : écris une ancre "
            "<figure data-image='id'></figure> (VIDE) à l'endroit voulu et "
            "déclare la même entrée dans le paramètre `images` avec son "
            "`document_id` (document image déposé) ou son `chemin` logique : le "
            "serveur l'embarque, tu n'écris jamais de base64. "
            "Ne PAS écrire de <style> ni de CSS en ligne (le thème est injecté ; "
            "un <style> est supprimé et signalé en avertissement). "
            "Données : uniquement des informations réelles issues de lectures "
            "(search_*, lire_document) ou de l'utilisateur — une donnée absente "
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
            "stylée (en-tête figé, colonnes dimensionnées, lignes alternées, "
            "filtre, en-tête répété à l'impression) nommée d'après le titre qui "
            "la précède ; les table.kpi alimentent une feuille « Chiffres clés ». "
            "MISE EN FORME DE CELLULE (attributs explicites, rien n'est deviné) : "
            "data-formule='=SUM(B3:B9)' (formule recalculée à l'ouverture du "
            "classeur, JAMAIS calculée par le serveur : écris aussi la valeur en "
            "texte pour que l'aperçu PDF soit juste) ; data-format='entier' | "
            "'decimal' | 'pourcentage' (valeur en fraction, 0.125 = 12,5 %) | "
            "'devise' | 'devise:MGA' | 'date' (AAAA-MM-JJ ou JJ/MM/AAAA) ; "
            "data-align='gauche|centre|droite' ; data-gras='1' ; "
            "data-fond='primaire|doux|ok|alerte' ; data-renvoi='1' ; "
            "data-couleur='primaire|accent|ok|alerte|texte|gris|blanc' "
            "(couleur de TEXTE : un rôle nommé, jamais un code couleur) ; "
            "data-taille='12' (points, 8 à 20) ; "
            "data-bordure='aucune|fine|moyenne|epaisse' avec "
            "data-bordure-couleur='primaire|accent|ok|alerte|texte|gris|blanc|doux' "
            "(bordure sur les quatre côtés de la cellule) ; "
            "data-largeur='22' ; colspan/rowspan pour fusionner. Classes de "
            "ligne : <tr class='total'> ou 'sous-total' (gras, fond doux, filet) "
            "et 'alerte'. Une valeur non convertible reste du texte et remonte "
            "en avertissement. Un PDF jumeau est déposé pour l'aperçu. SANS "
            "AUCUN TABLEAU, l'appel échoue : pour un document texte, choisir "
            "pdf/docx."
        ),
    )
    titre: str | None = Field(
        default=None,
        min_length=1,
        max_length=300,
        description=(
            "Titre du document : il apparaît dans l'en-tête de chaque page et dans "
            "les métadonnées du fichier. Titre court et explicite. Requis pour "
            "action='generer' et action='demarrer'."
        ),
    )
    format: Literal["pdf", "docx", "pptx", "xlsx"] | None = Field(
        default=None,
        description=(
            "Format cible. Omis : extension de ``nom_fichier`` (.pdf -> PDF), "
            "sinon DOCX. "
            "'pdf' (défaut de fait pour un livrable à remettre), "
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
    images: list[ImageDocumentInput] | None = Field(
        default=None,
        max_length=20,
        description=(
            "Images **du stockage CARSO** (photo, logo, plan scanné), ancrées "
            "dans contenu_html par <figure data-image='id'></figure>. Chaque "
            "entrée désigne l'image par `document_id` (document image déposé) "
            "ou `chemin` logique : le serveur l'embarque, l'agent n'écrit jamais "
            "de base64. Formats PDF, DOCX et PPTX ; un classeur (xlsx) n'insère "
            "pas d'image de contenu et refuse l'appel s'il en reçoit."
        ),
    )
    graphiques: list[GraphiqueInput] | None = Field(
        default=None,
        max_length=12,
        description=(
            "Graphiques de DONNÉES (barres, courbes, camembert, anneau). Chaque "
            "entrée remplace l'ancre <figure data-graphique='id'> de même 'id'. "
            "PDF et DOCX : image du graphique. XLSX : **objet Excel natif** lié à "
            "une feuille de données dédiée, donc modifiable et filtrable dans le "
            "tableur. À utiliser dès qu'un jeu de chiffres réels gagnerait à être "
            "comparé visuellement, et SYSTÉMATIQUEMENT dans un classeur qui "
            "présente des statistiques (budget par poste, bénéficiaires par lot, "
            "évolution par mois) : un tableau de chiffres seul ne montre pas la "
            "répartition. Pour un schéma sans chiffres (organigramme, flux, "
            "cycle), écris directement un <svg> dans contenu_html."
        ),
    )
    plan: list[str] | None = Field(
        default=None,
        min_length=1,
        max_length=24,
        description=(
            "action='demarrer' uniquement : le PLAN du document, une entrée par "
            "partie qui fera l'objet d'UN appel action='section' (ex. "
            "['Couverture', '1. Contexte', '2. Démarche', '3. Budget']). "
            "Répartir le document en 2 à 6 parties, chacune correspondant à "
            "plusieurs pages si besoin : la progression « Partie 2/4 » est "
            "affichée à l'utilisateur, elle prouve qu'il n'y a pas de boucle. "
            "Écrire 30 000 caractères par partie (bien moins d'appels) tout en "
            "restant court pour le modèle."
        ),
    )
    type_document: str | None = Field(
        default=None,
        description=(
            "Type de dépôt (vocabulaire FERMÉ, valeurs exactes), requis pour "
            "action='generer' et action='demarrer' : 'offre'," 
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
            "Rattachement optionnel : 'organisation' | 'appel' | "
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
    propose_par: str | None = Field(
        default=None,
        description=(
            "Identifiant de l'agent proposant. Omis, l'agent authentifié du "
            "contexte est inscrit : ne jamais recopier ni inventer un identifiant."
        ),
    )

    action: Literal["generer", "demarrer", "section", "finaliser"] = Field(
        default="generer",
        description=(
            "'generer' (défaut) : le corps complet est fourni d'un tenant — pour "
            "un document COURT. "
            "'demarrer' : ouvre un brouillon (titre, format, thème…) sans corps. "
            "'section' : ajoute le HTML d'UNE section (≤ 30 000 caractères) au "
            "brouillon. "
            "'finaliser' : assemble les sections, rend et dépose le document "
            "(graphiques déclarés ici). "
            "DOCUMENT LONG (au-delà d'une dizaine de pages) : écris OBLIGATOIREMENT "
            "par sections — une génération d'un seul tenant trop longue fait "
            "taire le fournisseur et tue le run sans exécuter l'outil."
        ),
    )
    brouillon_id: UUID | None = Field(
        default=None,
        description=(
            "Identifiant du brouillon (renvoyé par action='demarrer'), requis "
            "pour 'section' et 'finaliser'. Le brouillon appartient à l'agent "
            "qui l'a ouvert."
        ),
    )
    section_index: int | None = Field(
        default=None,
        ge=1,
        description=(
            "action='section' uniquement : numéro (1-based) d'une section déjà "
            "écrite à REMPLACER (correction). Omis : la section est ajoutée à "
            "la suite."
        ),
    )

    @model_validator(mode="after")
    def _coherence_action(self) -> GenererDocumentProfessionnelInput:
        """Champs exigés selon l'action — l'erreur dit quoi faire, sans devine."""
        if self.action == "generer":
            if not self.contenu_html:
                raise ValueError(
                    "action='generer' exige contenu_html (corps complet). Pour un "
                    "document long, préférer demarrer/section/finaliser."
                )
            if len(self.contenu_html) > _BORNE_CORPS_CARACTERES:
                raise ValueError(
                    f"Corps trop long pour action='generer' : "
                    f"{len(self.contenu_html)} caractères (maximum "
                    f"{_BORNE_CORPS_CARACTERES}). Un corps plus long fait taire "
                    "le fournisseur en cours d'émission et le run meurt sans "
                    "déposer le document. Utiliser la rédaction PROGRESSIVE : "
                    "action='demarrer' (titre, format, type_document), puis "
                    "action='section' avec le HTML d'UNE section par appel "
                    f"(≤ {_BORNE_SECTION_CARACTERES} caractères), puis "
                    "action='finaliser' avec brouillon_id."
                )
            if not self.titre or not self.type_document:
                raise ValueError(
                    "action='generer' exige titre et type_document (format "
                    "facultatif : extension du nom, sinon DOCX)."
                )
        elif self.action == "demarrer":
            if self.contenu_html:
                raise ValueError(
                    "action='demarrer' ne prend pas de corps : les sections "
                    "s'écrivent ensuite, une par appel (action='section')."
                )
            if self.plan is not None and any(not (item or "").strip() for item in self.plan):
                raise ValueError(
                    "plan contient une partie vide : chaque entrée est le titre "
                    "d'UNE partie (ex. '2. Démarche')."
                )
            if self.brouillon_id is not None:
                raise ValueError(
                    "action='demarrer' crée un NOUVEAU brouillon : ne pas "
                    "fournir brouillon_id."
                )
            if not self.titre or not self.type_document:
                raise ValueError(
                    "action='demarrer' exige titre et type_document : "
                    "l'habillage est figé pour tout le brouillon (format "
                    "facultatif : extension du nom, sinon DOCX)."
                )
        elif self.action == "section":
            if self.brouillon_id is None:
                raise ValueError(
                    "action='section' exige brouillon_id (renvoyé par "
                    "action='demarrer')."
                )
            if self.plan is not None:
                raise ValueError(
                    "plan se donne à action='demarrer' (il est figé pour le "
                    "brouillon) : action='section' reçoit seulement le contenu "
                    "d'UNE partie."
                )
            if not self.contenu_html:
                raise ValueError(
                    "action='section' exige contenu_html : le HTML d'UNE section."
                )
            if len(self.contenu_html) > _BORNE_SECTION_CARACTERES:
                raise ValueError(
                    f"Une section est bornée à {_BORNE_SECTION_CARACTERES} caractères "
                    f"(reçu {len(self.contenu_html)}) : coupe le document en "
                    "sections plus courtes (une génération trop longue fait "
                    "taire le fournisseur)."
                )
        else:  # finaliser
            if self.brouillon_id is None:
                raise ValueError(
                    "action='finaliser' exige brouillon_id (renvoyé par "
                    "action='demarrer')."
                )
            if self.contenu_html:
                raise ValueError(
                    "action='finaliser' ne prend pas de contenu_html : le corps "
                    "vient des sections du brouillon."
                )
        return self


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

    document_spec: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Document structuré à corriger. PRÉFÉRER ``document_id`` : le spec "
            "déjà déposé est relu côté serveur, au lieu d'être retransmis en "
            "entier. Fournir le spec seulement pour un document jamais déposé "
            "({'metadata': {...}, 'blocs': [...]})."
        ),
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
    format: TypeFormat | None = Field(
        default=None,
        description=(
            "Format cible : 'docx' | 'pdf' | 'xlsx'. Avec ``document_id``, le "
            "format du document versionné fait foi ; sans, il est déduit de "
            "l'extension de ``nom_fichier``, sinon DOCX."
        ),
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
            "Rattachement optionnel : 'organisation' | 'appel' | 'offre' | "
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


class ModifierSectionDocumentInput(BaseModel):
    """Correction d'un livrable **par section**, sans le réécrire entier.

    Un document rendu par ``generer_document_professionnel`` garde sa source
    (corps HTML + habillage). Corriger un montant, une phrase, une diapositive
    ne demande donc plus de régénérer tout le document : on remplace, ajoute ou
    supprime **un** morceau, le reste est conservé à l'identique.

    Chaque correction produit une **nouvelle version** du même document (nom,
    type et rattachement conservés ; l'ancienne version reste sur disque) —
    l'humain valide la version proposée comme d'habitude.

    Ordre d'usage : ``lister`` (voir les sections et leur index), puis
    ``remplacer`` / ``ajouter`` / ``supprimer``.
    """

    document_id: UUID = Field(
        description=(
            "UUID du document à corriger : celui renvoyé par "
            "generer_document_professionnel. Ne jamais inventer d'identifiant."
        )
    )
    action: Literal["lister", "lire", "remplacer", "ajouter", "supprimer"] = Field(
        default="lister",
        description=(
            "'lister' (défaut) : rend les sections du document (index, balise, "
            "titre, taille) — à faire AVANT toute modification. "
            "'lire' : rend le HTML **exact** d'UNE section (`section_index`) : "
            "c'est la source à recopier corrigée dans 'remplacer', sans "
            "relire tout le document ni le régénérer. "
            "'remplacer' : remplace la section `section_index` par le HTML "
            "fourni. 'ajouter' : insère une section après `section_index` "
            "(à la fin si omis). 'supprimer' : retire la section "
            "`section_index`."
        ),
    )
    section_index: int | None = Field(
        default=None,
        ge=1,
        description=(
            "Numéro (1-based) de la section, tel que renvoyé par action='lister'. "
            "Requis pour 'lire', 'remplacer' et 'supprimer'. Pour 'ajouter' : la "
            "nouvelle section est insérée APRÈS ce numéro (omis = à la fin)."
        ),
    )
    contenu_html: str | None = Field(
        default=None,
        description=(
            "HTML d'UNE section (un seul élément racine : <section>, <table>, "
            "<div>…). Requis pour 'remplacer' et 'ajouter'. Mêmes classes de "
            "thème que la génération (lead, table.data, table.kpi, callout…), "
            "pas de <style>. Pour un deck : <section class='slide'> (ou "
            "'cover') par diapositive. Données : uniquement des informations "
            "réelles — une donnée absente s'écrit '(à compléter)'."
        ),
    )
    graphiques: list[GraphiqueInput] | None = Field(
        default=None,
        max_length=12,
        description=(
            "Graphiques de données à déclarer pour CETTE section (même "
            "contrat qu'à la génération : <figure data-graphique='id'></figure> "
            "dans le HTML, spec ici au même 'id'). Les graphiques déjà "
            "déclarés sont conservés."
        ),
    )

    @model_validator(mode="after")
    def _coherence_action(self) -> ModifierSectionDocumentInput:
        """Champs exigés par l'action — l'erreur dit quoi fournir."""
        if self.action == "lister":
            if self.section_index is not None or self.contenu_html:
                raise ValueError(
                    "action='lister' ne modifie rien : retirer section_index et "
                    "contenu_html (ce sont les actions de modification)."
                )
        elif self.action in {"lire", "remplacer", "supprimer"}:
            if self.section_index is None:
                raise ValueError(
                    f"action='{self.action}' exige section_index (le numéro "
                    "renvoyé par action='lister')."
                )
        if self.action in {"remplacer", "ajouter"} and not (self.contenu_html or "").strip():
            raise ValueError(
                f"action='{self.action}' exige contenu_html : le HTML d'UNE section."
            )
        if self.action in {"supprimer", "lister", "lire"} and self.contenu_html:
            raise ValueError(
                f"action='{self.action}' ne prend pas de contenu_html."
            )
        if self.contenu_html and len(self.contenu_html) > _BORNE_SECTION_CARACTERES:
            raise ValueError(
                f"Une section est bornée à {_BORNE_SECTION_CARACTERES} caractères "
                f"(reçu {len(self.contenu_html)}) : corriger une section plus "
                "courte, ou la découper en deux."
            )
        return self


class LireDocumentInput(BaseModel):
    """Entrée typée de la lecture : un identifiant de document.

    Un CSV rend une lecture **par colonnes** (``colonnes``, ``apercu_lignes`` en
    dictionnaires, ``separateur``, ``encodage``, ``problemes``) au lieu d'un
    bloc de texte : ses trois options ci-dessous n'agissent que sur lui, et
    sont **refusées** sur un autre format plutôt qu'ignorées en silence.
    """

    document_id: UUID = Field(description="UUID du document à lire.")
    separateur: str | None = Field(
        default=None,
        max_length=12,
        description=(
            "CSV uniquement : séparateur du fichier (',' ';' '|' ou tabulation). "
            "Omettre pour la détection automatique — la valeur retenue est rendue "
            "dans ``separateur``. Sur un autre format, l'option est refusée."
        ),
    )
    encodage: str | None = Field(
        default=None,
        max_length=20,
        description=(
            "CSV uniquement : 'utf-8' ou 'cp1252'. Omettre : UTF-8 essayé d'abord, "
            "puis cp1252 (exports Excel français) — la valeur retenue est rendue."
        ),
    )
    max_lignes: int | None = Field(
        default=None,
        ge=1,
        le=200,
        description="CSV uniquement : lignes d'aperçu rendues (défaut 40, plafond 200).",
    )


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
    colonne: str | None = Field(
        default=None,
        max_length=200,
        description=(
            "CSV uniquement : restreindre la fouille à une colonne — nom d'en-tête "
            "(insensible aux accents et à la casse) ou numéro 1-indexé. Une colonne "
            "inconnue est refusée avec la liste réelle des colonnes, jamais "
            "silencieusement traduite en zéro résultat."
        ),
    )
    separateur: str | None = Field(
        default=None,
        max_length=12,
        description=(
            "CSV uniquement : séparateur, quand la détection s'est trompée (le "
            "signe : ``problemes`` de lire_document annonce des largeurs inégales)."
        ),
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


class CibleLecture(BaseModel):
    """Une cible de lecture : **son** type, **son** index, **sa** ligne.

    ``index`` (liste simple) impose un seul ``type_element`` et interdit
    ``sous_index`` : lire l'en-tête d'un tableau et trois de ses lignes exigeait
    donc plusieurs appels. Une cible porte ses propres paramètres — un appel,
    plusieurs éléments hétérogènes.
    """

    index: int | None = Field(
        default=None,
        ge=1,
        description="Position 1-indexée dans l'unité du type (obligatoire sauf type 'feuille').",
    )
    type_element: TypeElement | None = Field(
        default=None,
        description="Type de CETTE cible ; absent = le type_element de l'appel.",
    )
    sous_index: int | None = Field(
        default=None,
        ge=1,
        description="DOCX : ligne de tableau à lire seule (1-indexée).",
    )
    ligne_debut: int | None = Field(
        default=None,
        ge=1,
        description="DOCX, tableau : première ligne d'une fenêtre de max_lignes lignes.",
    )
    max_lignes: int | None = Field(
        default=None,
        ge=1,
        le=200,
        description=(
            "Fenêtre de CETTE cible (tableau DOCX, feuille XLSX) ; absent = le "
            "plafond de l'appel."
        ),
    )


class LireElementInput(BaseModel):
    """Entrée de ``lire_element_document`` : un document + un ou plusieurs éléments.

    Deux façons de lire plusieurs éléments, jamais mélangées :

    - ``index`` : une liste de positions, **un seul** ``type_element`` commun ;
    - ``cibles`` : une liste de cibles hétérogènes — chacune porte son type,
      son index, sa ligne de tableau. C'est la forme à préférer dès que les
      éléments ne sont pas tous du même type (en-tête + lignes d'un tableau,
      paragraphe + tableau…).

    Le repère ``element`` est commun aux trois formats (élément de flux DOCX,
    page PDF, ligne XLSX) : c'est celui que ``chercher_dans_document`` renvoie
    dans ``relire`` — à recopier tel quel, jamais à recalculer.
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
    cibles: list[CibleLecture] | None = Field(
        default=None,
        min_length=1,
        max_length=MAX_ELEMENTS_PAR_LECTURE,
        description=(
            "Cibles hétérogènes lues en UN appel : cibles: [{\"index\": 16, "
            "\"type_element\": \"tableau\", \"sous_index\": 3}, {\"index\": 12, "
            "\"type_element\": \"paragraphe\"}] → une entrée par cible. "
            "Exclusif avec 'index'. À préférer quand les éléments n'ont pas le "
            "même type ou la même ligne."
        ),
    )
    sous_index: int | None = Field(
        default=None,
        ge=1,
        description="DOCX uniquement : ligne d'un tableau à lire seule (1-indexée).",
    )
    ligne_debut: int | None = Field(
        default=None,
        ge=1,
        description=(
            "DOCX, tableau : première ligne de la fenêtre lue (max_lignes). "
            "Pour lire la suite d'un grand tableau : recopier "
            "``relire_suite.ligne_debut`` rendu par l'appel précédent — jamais "
            "un appel par ligne."
        ),
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
    separateur: str | None = Field(
        default=None,
        max_length=12,
        description=(
            "CSV uniquement : séparateur du fichier, identique à celui de "
            "chercher_dans_document (mêmes arguments que ``hit['relire']``)."
        ),
    )

    @field_validator("index", mode="before")
    @classmethod
    def _index_scalaire_accepte(cls, valeur: Any) -> Any:
        """Un index seul (entier) vaut ``[index]`` — locators ``hit['relire']``."""
        if isinstance(valeur, int) and not isinstance(valeur, bool):
            return [valeur]
        return valeur


class ListerModelesDocumentsInput(BaseModel):
    """Découverte des modèles DOCX d'une organisation."""

    organisation_id: UUID = Field(description="Organisation propriétaire des modèles.")
    type_document: str | None = Field(
        default=None,
        description="Filtrer sur un type de document (ex. 'modele_offre_technique').",
    )


class ObtenirStructureModeleInput(BaseModel):
    """Lecture des éléments réels d'un modèle, avant de le copier."""

    organisation_id: UUID = Field(description="Organisation propriétaire du modèle.")
    modele_id: UUID | None = Field(
        default=None, description="UUID du modèle ; sinon le modèle actif du type est pris."
    )
    type_document: str | None = Field(
        default=None,
        description="Type de modèle, requis seulement si `modele_id` est absent.",
    )


class CopierModeleDocumentInput(BaseModel):
    """Copie du modèle de l'organisation dans l'objet métier, prête à être éditée."""

    ancre: ModeleAncre = Field(
        description="Objet qui reçoit la copie : 'offre', 'mission' ou 'session'."
    )
    entity_id: UUID = Field(description="Identifiant de cet objet (réel, jamais inventé).")
    modele_id: UUID | None = Field(
        default=None,
        description=(
            "Modèle à appliquer ; sinon le modèle actif du type de l'offre, ou "
            "l'unique modèle de l'organisation (mission/session)."
        ),
    )
    type_document: str | None = Field(
        default=None,
        description=(
            "Type de DÉPÔT du document de travail ('offre' pour une offre ; "
            "'fiche_technique', 'rapport', 'checklist'… pour une mission ou une "
            "session). Défaut : 'offre' ou 'autre'."
        ),
    )
    nom_fichier: str | None = Field(
        default=None,
        description="Nom du fichier de travail (sinon 'modele-<référence>.docx').",
    )


class RenommerDocumentInput(BaseModel):
    """Renommage d'un document existant — jamais de régénération du contenu."""

    document_id: UUID = Field(description="Identifiant du document à renommer.")
    nom: str = Field(
        min_length=1,
        max_length=255,
        description=(
            "Nouveau nom, **avec son extension** ('.docx', '.pdf'…) : elle est "
            "exigée par le stockage et le rendu."
        ),
    )


class SupprimerDocumentInput(BaseModel):
    """Suppression d'un document (corbeille), après validation humaine.

    L'appel est suspendu par l'interruption HITL : le document n'est supprimé
    qu'après l'accord explicite de l'utilisateur.
    """

    document_id: UUID = Field(description="Identifiant du document à supprimer.")
    motif: str = Field(
        min_length=3,
        max_length=500,
        description=(
            "Raison de la suppression, conservée dans l'audit et affichée dans "
            "la corbeille. Décrire le besoin de l'utilisateur, pas une supposition."
        ),
    )


class ObtenirStructureDocumentInput(BaseModel):
    """Éléments éditables de la copie de modèle en cours."""

    ancre: ModeleAncre = Field(description="Objet qui porte la copie.")
    entity_id: UUID = Field(description="Identifiant de cet objet.")


class ObtenirStructureInput(BaseModel):
    """Structure d'un **modèle** (avant copie) ou d'une **copie** de modèle.

    Un seul outil pour deux cibles qui partagent le même geste (« quels
    éléments puis-je éditer ? ») : la cible se déduit des champs fournis.

    - un modèle : ``organisation_id`` + (``modele_id`` ou ``type_document``) ;
    - une copie : ``ancre`` + ``entity_id``.

    Les deux familles ne se mélangent pas : envoyer un identifiant de modèle et
    une ancre serait ambigu, et le refus le dit plutôt que de deviner.
    """

    organisation_id: UUID | None = Field(
        default=None,
        description="Organisation propriétaire du modèle (cible « modèle »).",
    )
    modele_id: UUID | None = Field(
        default=None,
        description=(
            "UUID du modèle ; sinon le modèle actif du type est pris "
            "(cible « modèle »)."
        ),
    )
    type_document: str | None = Field(
        default=None,
        description=(
            "Type de modèle, requis seulement si `modele_id` est absent "
            "(cible « modèle »)."
        ),
    )
    ancre: ModeleAncre | None = Field(
        default=None, description="Objet qui porte la copie (cible « copie »)."
    )
    entity_id: UUID | None = Field(
        default=None, description="Identifiant de cet objet (cible « copie »)."
    )

    @model_validator(mode="after")
    def _une_seule_cible(self) -> ObtenirStructureInput:
        """Une cible à la fois — l'erreur dit quoi fournir."""
        copie = self.ancre is not None or self.entity_id is not None
        modele = self.organisation_id is not None
        if copie and modele:
            raise ValueError(
                "Deux cibles à la fois : soit la copie (`ancre` + `entity_id`), "
                "soit le modèle (`organisation_id` + `modele_id`/`type_document`)."
            )
        if copie and (self.ancre is None or self.entity_id is None):
            raise ValueError(
                "Pour lire une copie, fournir `ancre` ET `entity_id`."
            )
        if not copie and not modele:
            raise ValueError(
                "Préciser la cible : `ancre` + `entity_id` (copie), ou "
                "`organisation_id` + `modele_id`/`type_document` (modèle)."
            )
        return self


class RevenirVersionDocumentInput(BaseModel):
    """Restauration d'une version de l'historique d'une copie de modèle."""

    ancre: ModeleAncre = Field(description="Objet dont la copie est corrigée.")
    entity_id: UUID = Field(description="Identifiant de cet objet.")
    version: int | None = Field(
        default=None,
        ge=1,
        description=(
            "Version à restaurer, lue dans `historique` de "
            "`obtenir_structure` ou dans la réponse d'une édition. "
            "Omettre pour annuler simplement la dernière édition."
        ),
    )


class ModeleSegmentTexte(BaseModel):
    """Morceau de texte avec sa mise en forme (facultative)."""

    texte: str
    gras: bool | None = None
    italique: bool | None = None
    souligne: bool | None = None
    couleur: str | None = Field(default=None, description="Couleur hexadécimale, ex. '1F4E79'.")
    taille_pt: float | None = None


class RemplacerElementModeleInput(BaseModel):
    """Remplacement du texte d'un ou plusieurs éléments, mise en forme gardée.

    Deux formes, jamais mélangées :

    - **un** élément : ``element_id`` + ``nouveau_element`` ;
    - **plusieurs** en un seul appel (une seule version) : deux listes de même
      longueur, ``elements`` et ``nouveaux_elements``.
    """

    ancre: ModeleAncre = Field(description="Objet dont la copie de modèle est éditée.")
    entity_id: UUID = Field(description="Identifiant de cet objet.")
    element_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=40,
        description=(
            "Identifiant lu dans `obtenir_structure` ou renvoyé par "
            "`copier_modele_document` (ex. 'block_004'). Jamais inventé."
        ),
    )
    nouveau_element: str | list[ModeleSegmentTexte] | None = Field(
        default=None,
        description=(
            "Nouveau contenu de cet élément : texte simple, ou liste de "
            "segments {texte, gras, italique, couleur, taille_pt} pour mixer "
            "les styles. La police, la taille et le style du paragraphe du "
            "modèle restent ceux du fichier."
        ),
    )
    elements: list[str] | None = Field(
        default=None,
        max_length=40,
        description=(
            "Identifiants des éléments à remplacer, dans le même ordre que "
            "`nouveaux_elements`. Un seul appel ⇒ une seule version du document."
        ),
    )
    nouveaux_elements: list[str | list[ModeleSegmentTexte]] | None = Field(
        default=None,
        max_length=40,
        description=(
            "Nouveaux contenus, un par identifiant de `elements` (chaîne ou "
            "segments de style). Les deux listes ont la même longueur."
        ),
    )

    @model_validator(mode="after")
    def _une_seule_forme(self) -> RemplacerElementModeleInput:
        """Une forme à la fois, et des listes alignées."""
        unique = self.element_id is not None or self.nouveau_element is not None
        groupe = self.elements is not None or self.nouveaux_elements is not None
        if unique and groupe:
            raise ValueError(
                "Choisir une seule forme : `element_id` + `nouveau_element`, "
                "ou `elements` + `nouveaux_elements` (jamais les deux)."
            )
        if groupe:
            if not self.elements or self.nouveaux_elements is None:
                raise ValueError(
                    "Fournir `elements` ET `nouveaux_elements`, deux listes de "
                    "même longueur."
                )
            if len(self.elements) != len(self.nouveaux_elements):
                raise ValueError(
                    f"Listes désalignées : {len(self.elements)} élément(s) pour "
                    f"{len(self.nouveaux_elements)} contenu(s)."
                )
        elif self.element_id is None or self.nouveau_element is None:
            raise ValueError(
                "Fournir `element_id` et `nouveau_element` (ou les deux listes)."
            )
        return self


class RemplissageModeleInput(BaseModel):
    """Un élément remplacé (ou complété) par du contenu HTML stylisé."""

    element_id: str = Field(
        min_length=1,
        max_length=40,
        description="Identifiant de l'élément à remplacer (jamais inventé).",
    )
    contenu_html: str = Field(
        min_length=1,
        description=(
            "Contenu HTML stylisé : titres, paragraphes, listes <ul>/<ol>, "
            "tableaux <table>, images en data URI base64 ("
            "src='data:image/png;base64,…') et <svg> rasterisé automatiquement. "
            "Aucun chemin de fichier local : l'image doit être embarquée."
        ),
    )
    position: Literal["remplacer", "avant", "apres"] = Field(
        default="remplacer",
        description="Remplacer l'élément, ou insérer le contenu avant/après lui.",
    )


class RemplirModeleInput(BaseModel):
    """Remplissages HTML d'une copie de modèle, en une seule version."""

    ancre: ModeleAncre = Field(description="Objet dont la copie de modèle est éditée.")
    entity_id: UUID = Field(description="Identifiant de cet objet.")
    remplissages: list[RemplissageModeleInput] = Field(
        min_length=1,
        max_length=40,
        description=(
            "Un contenu par élément visé. Les identifiants viennent de "
            "`obtenir_structure` ; ils ne changent pas d'un "
            "remplissage à l'autre dans le même appel."
        ),
    )


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

    document_id: UUID | None = Field(
        default=None,
        description=(
            "UUID d'un document déjà généré depuis un ``document_spec`` : son "
            "spec est relu côté serveur. Voie à préférer — éviter de "
            "retransmettre le document entier."
        ),
    )
    document_spec: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Document structuré à vérifier, quand il n'est pas encore déposé "
            "({'metadata': {...}, 'blocs': [...]}). Sinon laisser vide et "
            "fournir ``document_id``."
        ),
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


class LireImageInput(BaseModel):
    """Vision : ouvre une image déposée et la rend au modèle.

    Aucun chemin disque : le fichier est lu par le service documentaire (même
    contrôle d'accès que ``lire_document``).
    """

    document_id: UUID = Field(
        description=(
            "UUID du document **image** (PNG, JPEG, GIF, WebP) déjà déposé. "
            "Ne jamais inventer d'identifiant : il vient de ``search_documents`` "
            "ou d'une pièce jointe."
        )
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

class GenererImageInput(BaseModel):
    """Une **image réelle** fournie en base64 par le modèle (INC-135).

    Réservé au modèle qui sait réellement émettre des octets d'image. Un modèle
    qui n'écrit que du texte passe par ``generer_image_svg`` plutôt que
    d'inventer un base64.
    """

    nom_fichier: str = Field(
        min_length=1,
        max_length=200,
        description="Nom du fichier image, sans extension (elle vient du format réel).",
    )
    base64: str = Field(
        min_length=1,
        description=(
            "Contenu de l'image encodé en base64 (PNG, JPEG, GIF ou WebP), avec "
            "ou sans préfixe data:image/...;base64,"
        ),
    )
    type_document: str = Field(
        default="autre",
        max_length=40,
        description=(
            "Type documentaire du dépôt (``autre`` par défaut ; un type métier "
            "réel s'il s'agit d'un logo d'offre, d'un justificatif...)."
        ),
    )
    ancre: TypeAncre | None = Field(
        default=None,
        description="Ancre métier réelle qui range l'image sous l'objet concerné.",
    )
    entity_id: UUID | None = Field(
        default=None, description="Identifiant de l'objet de l'ancre (UUID réel)."
    )
    designation: str | None = Field(
        default=None,
        max_length=120,
        description="Libellé repris dans les avertissements (« Figure 2 »).",
    )


class GenererImageSvgInput(BaseModel):
    """Une image **dessinée en SVG** par un modèle qui n'émet pas de binaire.

    Le serveur nettoie le SVG (mêmes règles que tout SVG d'agent) puis le
    rasterise en PNG : l'utilisateur reçoit une image, jamais du balisage.
    """

    nom_fichier: str = Field(
        min_length=1,
        max_length=200,
        description="Nom du fichier image, sans extension (le PNG est ajouté).",
    )
    svg: str = Field(
        min_length=1,
        max_length=200_000,
        description=(
            "Balisage SVG complet d'un seul dessin (balise <svg> avec viewBox) : "
            "formes, textes, dégradés. Aucune référence distante ni script."
        ),
    )
    type_document: str = Field(default="autre", max_length=40)
    ancre: TypeAncre | None = Field(
        default=None, description="Ancre métier réelle (au plus une)."
    )
    entity_id: UUID | None = Field(default=None)
    designation: str | None = Field(default=None, max_length=120)


class ComposerImageInput(BaseModel):
    """Une image **composée par le serveur** à partir d'un contenu (INC-136).

    L'agent fournit le contenu (surtitre, titre, sous-titre, points, pied) et le
    serveur applique sa mise en page : marges, hiérarchie typographique, palette
    contrastée. C'est la voie recommandée — la qualité ne dépend plus de la
    capacité du modèle à écrire un beau SVG.
    """

    nom_fichier: str = Field(
        min_length=1,
        max_length=200,
        description="Nom du fichier image, sans extension (le PNG est ajouté).",
    )
    titre: str = Field(
        min_length=1,
        max_length=160,
        description="Sujet de l'image, en quelques mots (6 à 10 maximum).",
    )
    surtitre: str | None = Field(
        default=None,
        max_length=60,
        description="Étiquette courte en capitales au-dessus du titre (type, thème).",
    )
    sous_titre: str | None = Field(
        default=None,
        max_length=240,
        description="Précision d'une ou deux lignes sous le titre.",
    )
    points: list[str] | None = Field(
        default=None,
        max_length=6,
        description="Puces courtes (une idée par puce, 6 au plus).",
    )
    pied: str | None = Field(
        default=None,
        max_length=120,
        description="Mention de bas de page : source, date, contact.",
    )
    gabarit: Literal["carte", "banniere", "bandeau", "affiche"] = Field(
        default="carte", description="Format de l'image (carré, 16:9, bandeau, A4)."
    )
    palette: Literal["carso", "clair", "institutionnel", "sable"] = Field(
        default="carso", description="Palette contrastée imposée par le serveur."
    )
    type_document: str = Field(default="autre", max_length=40)
    ancre: TypeAncre | None = Field(
        default=None, description="Ancre métier réelle (au plus une)."
    )
    entity_id: UUID | None = Field(default=None)
    designation: str | None = Field(default=None, max_length=120)


class VerifierImageSvgInput(BaseModel):
    """Le **contrôle de design** d'un SVG, avant tout dépôt (INC-136).

    Rasterise le SVG en mémoire et rend les défauts mesurables : texte hors
    cadre, police trop petite, contraste insuffisant, image uniforme. Rien n'est
    déposé : l'agent corrige puis appelle ``generer_image_svg``.
    """

    svg: str = Field(
        min_length=1,
        max_length=200_000,
        description="SVG complet à contrôler (tel qu'il sera déposé).",
    )
