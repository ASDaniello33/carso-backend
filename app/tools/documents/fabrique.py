"""Fabrique des tools documentaires : agregateur des familles.

``build_document_tools`` ne contient plus aucun handler. Il construit le
``ContexteOutils`` partage, puis assemble la liste des ``TypedTool`` dont les
handlers vivent dans un module par famille :

- ``lecture`` : lire, inspecter, chercher, lire un element ou une image ;
- ``redaction`` : generer, corriger, valider, rendre ;
- ``cycle_vie`` : analyser une reference, importer un tableau, renommer, supprimer ;
- ``modeles`` : inventorier, copier et remplir les modeles d'organisation ;
- ``image`` : generer et controler une image ou une composition ;
- ``brouillon`` : redaction progressive et finalisation.

Chaque handler recoit le contexte en premier argument ; la fabrique le lie par
``functools.partial``. Une famille ne peut importer que les familles dont elle
depend reellement (mesure : ``brouillon`` vers ``redaction``, seule arete).
"""

from __future__ import annotations

from functools import partial
from typing import Any

from app.tools.base import TypedTool
from app.tools.documents.brouillon import (
    _brouillon_demarrer as _brouillon_demarrer,
)
from app.tools.documents.brouillon import (
    _brouillon_finaliser as _brouillon_finaliser,
)
from app.tools.documents.brouillon import (
    _brouillon_section as _brouillon_section,
)
from app.tools.documents.brouillon import (
    _generer_professionnel_ou_brouillon as _generer_professionnel_ou_brouillon,
)
from app.tools.documents.contexte import ContexteOutils
from app.tools.documents.cycle_vie import (
    _importer_tableau as _importer_tableau,
)
from app.tools.documents.cycle_vie import (
    _reference as _reference,
)
from app.tools.documents.cycle_vie import (
    _renommer as _renommer,
)
from app.tools.documents.cycle_vie import (
    _supprimer as _supprimer,
)
from app.tools.documents.image import (
    _bloquants as _bloquants,
)
from app.tools.documents.image import (
    _composer_image as _composer_image,
)
from app.tools.documents.image import (
    _diagnostics_svg as _diagnostics_svg,
)
from app.tools.documents.image import (
    _generer_image as _generer_image,
)
from app.tools.documents.image import (
    _generer_image_svg as _generer_image_svg,
)
from app.tools.documents.image import (
    _nom_image as _nom_image,
)
from app.tools.documents.image import (
    _sortie_image as _sortie_image,
)
from app.tools.documents.image import (
    _verifier_image_svg as _verifier_image_svg,
)
from app.tools.documents.lecture import (
    _chercher as _chercher,
)
from app.tools.documents.lecture import (
    _inspecter as _inspecter,
)
from app.tools.documents.lecture import (
    _lire as _lire,
)
from app.tools.documents.lecture import (
    _lire_element as _lire_element,
)
from app.tools.documents.lecture import (
    _lire_image as _lire_image,
)
from app.tools.documents.lecture import (
    _lire_plage as _lire_plage,
)
from app.tools.documents.modeles import (
    _copier_modele as _copier_modele,
)
from app.tools.documents.modeles import (
    _lister_modeles as _lister_modeles,
)
from app.tools.documents.modeles import (
    _remplacer_element as _remplacer_element,
)
from app.tools.documents.modeles import (
    _remplir_modele as _remplir_modele,
)
from app.tools.documents.modeles import (
    _revenir_version as _revenir_version,
)
from app.tools.documents.modeles import (
    _structure_document as _structure_document,
)
from app.tools.documents.modeles import (
    _structure_modele as _structure_modele,
)
from app.tools.documents.redaction import (
    _corriger as _corriger,
)
from app.tools.documents.redaction import (
    _enregistrer_source as _enregistrer_source,
)
from app.tools.documents.redaction import (
    _generer as _generer,
)
from app.tools.documents.redaction import (
    _generer_html as _generer_html,
)
from app.tools.documents.redaction import (
    _generer_libre as _generer_libre,
)
from app.tools.documents.redaction import (
    _generer_professionnel as _generer_professionnel,
)
from app.tools.documents.redaction import (
    _generer_structurer as _generer_structurer,
)
from app.tools.documents.redaction import (
    _modifier_section as _modifier_section,
)
from app.tools.documents.redaction import (
    _refuser_generation_si_modele as _refuser_generation_si_modele,
)
from app.tools.documents.redaction import (
    _rendre as _rendre,
)
from app.tools.documents.redaction import (
    _rendre_et_deposer_professionnel as _rendre_et_deposer_professionnel,
)
from app.tools.documents.redaction import (
    _verifier as _verifier,
)
from app.tools.documents.redaction import (
    _versionner as _versionner,
)
from app.tools.documents.schemas import (
    ChercherDocumentInput,
    ComposerImageInput,
    CopierModeleDocumentInput,
    CorrigerDocumentInput,
    GenererDocumentHtmlInput,
    GenererDocumentInput,
    GenererDocumentProfessionnelInput,
    GenererImageInput,
    GenererImageSvgInput,
    ImporterTableauInput,
    InspecterDocumentInput,
    LireDocumentInput,
    LireElementInput,
    LireImageInput,
    LirePlageInput,
    ListerModelesDocumentsInput,
    ModifierSectionDocumentInput,
    ObtenirStructureDocumentInput,
    ObtenirStructureModeleInput,
    ReferenceDocumentInput,
    RemplacerElementModeleInput,
    RemplirModeleInput,
    RenderDocumentInput,
    RenommerDocumentInput,
    RevenirVersionDocumentInput,
    SupprimerDocumentInput,
    ValiderDocumentInput,
    VerifierImageSvgInput,
)
from app.tools.permissions import AgentIdentity, PermissionPolicy


def build_document_tools(
    *,
    identite: AgentIdentity,
    policy: PermissionPolicy,
    service_factory: Any,
    dossier_brouillons: Any = None,
    dossier_sources: Any = None,
) -> list[Any]:
    """Construit les tools documentaires pour UN agent.

    Args:
        identite: identité de l'agent appelant (traçabilité).
        policy: permissions de l'agent. Capacités exigées : ``document_read``
            pour la lecture, ``document_propose`` pour la génération (dépôt en
            ``proposed``). Un agent sans l'une d'elles ne reçoit pas le tool —
            et un appel direct reste refusé par ``ctx.policy.require``.
        service_factory: callable ``f() -> DocumentService`` fourni par le
            composant appelant (l'agent n'ouvre pas de session lui-même).

    Returns:
        Liste de ``TypedTool`` : ``generer_document`` et ``lire_document``.
    """

    ctx = ContexteOutils(
        identite=identite,
        policy=policy,
        service_factory=service_factory,
        dossier_brouillons=dossier_brouillons,
        dossier_sources=dossier_sources,
    )

    return [
        TypedTool(
            name="generer_document",
            description=(
                "Génère un livrable DOCX ou PDF LIBRE et le dépose comme "
                "PROPOSITION (validation humaine obligatoire). "
                "VOIE PRINCIPALE : ``html`` — le corps du document en HTML "
                "sémantique, habillé par la feuille de style CARSO : "
                "``<div class=\"h1\">`` à ``h6``, ``<p class=\"p\">``, "
                "``<span class=\"bold|italic|souligne|reference\">``, "
                "``<ul class=\"liste\">``, ``<table class=\"tableau\">``, "
                "``<div class=\"encadre|avertissement|citation|legende\">``, "
                "``<div class=\"saut-page\">``. ``css`` reprend ou redéfinit une "
                "classe (couleur, police, taille en ``em``) : la liberté porte "
                "sur la mise en forme, le serveur garantit l'uniforme (tailles "
                "en pt alignées sur 8, 9, 11, 13, 16, 20, 26). "
                "Figures : ``graphiques`` / ``illustrations`` + "
                "``<figure data-graphique=\"id\">`` — le serveur dessine le SVG, "
                "ne jamais coller une image de graphique. "
                "Images : ``<img src=\"https://…\">`` — le serveur la télécharge, "
                "vérifie, réduit et embarque ; base64 interdit (trop lourd), un "
                "lien mort laisse un repère visible et un avertissement. "
                "Aucun « — » : utiliser « - » ou une virgule. "
                "Héritage : ``document_spec`` (document structuré) et "
                "``titre``/``paragraphes``/``tableau`` restent acceptés pour les "
                "appels existants ; pour un nouveau document, écrire du HTML. "
                "Le retour contient ``format``, ``modele``, ``pages`` (PDF) et "
                "``avertissements`` (tailles alignées, images écartées, tirets) : "
                "les lire avant de présenter le document. "
                "Rattachement : une ancre métier réelle classe le fichier sous "
                "l'objet ; une ancre absente le range dans le dossier du compte "
                "(non classé) — ne jamais inventer d'UUID. "
                "Après succès : render_document, puis "
                "proposer_telechargement_document."
            ),
            handler=partial(_generer, ctx),
            input_schema=GenererDocumentInput,
            tags=("documents", "generation"),
        ),
        TypedTool(
            name="modifier_section_document",
            description=(
                "Corrige UNE section d'un document rendu par "
                "generer_document_professionnel, sans le réécrire entier "
                "(nouvelle version du même document). action='lister' d'abord "
                "(index et titres), puis 'remplacer' / 'ajouter' / 'supprimer'."
            ),
            handler=partial(_modifier_section, ctx),
            input_schema=ModifierSectionDocumentInput,
            tags=("documents", "écriture"),
        ),
        TypedTool(
            name="generer_document_professionnel",
            description=(
                "Génération d'un livrable DOCX, PDF, PPTX ou XLSX **sans modèle** : "
                "écris le **corps** du document en HTML avec les classes du thème "
                "(cover, toc, lead, table.kpi, table.data, callout, table.layout, "
                "badge, page-break, signature) et le moteur se charge du design — "
                "thème professionnel injecté (couverture pleine page, en-tête, "
                "pied 'n / N', sommaire aux numéros de page CALCULÉS, styles Word "
                "natifs pour un DOCX). Le fichier est déposé comme PROPOSITION "
                "(validation humaine obligatoire). "
                "RÉSERVÉ AUX DOCUMENTS QU'AUCUN MODÈLE NE COUVRE (note interne, "
                "lettre, compte rendu, rapport, CV) : dès que l'organisation a "
                "déposé un modèle pour l'objet visé, l'appel est REFUSÉ — il faut "
                "copier le modèle (`copier_modele_document`) puis l'éditer "
                "(`remplacer_element_model`, `remplir_modele`). Un document "
                "d'offre ne se produit jamais à neuf quand un modèle existe : "
                "son design, ses styles, son logo et ses mentions sont ceux du "
                "client. "
                "PRÉSENTATION : format='pptx' rend un deck 16:9 natif "
                "(vraies formes, tableaux et images PowerPoint, notes "
                "présentateur, thème appliqué) à partir du même ``contenu_html`` : "
                "<section class='cover'> puis une <section class='slide'> par "
                "diapositive (h1, p.lead, ul/ol, table.kpi, table.data, figure, "
                "div.callout, aside.notes). Le moteur REFUSE une diapositive "
                "dont le texte déborderait (message « scinde la diapositive ») "
                "plutôt que de le rogner. "
                "CLASSEUR : format='xlsx' rend chaque table.data en feuille Excel "
                "stylée (thème, en-tête figé, filtre automatique, formules "
                "data-formule, formats de nombres data-format, styles de cellule "
                "data-gras/data-fond/data-align, couleur de texte data-couleur, "
                "taille data-taille, bordure data-bordure/data-bordure-couleur, "
                "fusions colspan/rowspan) : exige au moins un tableau. "
                "RÈGLE DE CLASSEUR : toute cellule dont la valeur est CALCULÉE "
                "(total, sous-total, écart, pourcentage, moyenne) porte une "
                "formule data-formule (=SOMME(...), =B4-B3, =B4/B3) — une valeur "
                "tapée en dur devient fausse au premier changement et le tableur "
                "ne recalcule rien. L'agent n'évalue jamais la formule lui-même : "
                "il écrit la formule, Excel calcule. "
                "STYLE PILOTÉ PAR LA DONNÉE : une ligne de total prend "
                "class='total' (gras, fond doux, filet haut), une valeur hors "
                "cible ou en dépassement class='alerte', un montant "
                "data-format='devise:MGA', un taux 'pourcentage'. "
                "GRAPHIQUES DANS UN CLASSEUR : dès qu'une feuille présente des "
                "chiffres qui se comparent (budget par poste, bénéficiaires par "
                "lot, évolution mensuelle), poser une ancre "
                "<figure data-graphique='id'></figure> juste après le tableau et "
                "définir le graphique dans ``graphiques`` : il devient un objet "
                "Excel natif lié à ses données (camembert pour une répartition, "
                "barres pour comparer des lots, courbes pour une évolution). "
                "Graphiques de DONNÉES via le paramètre ``graphiques`` (ancre "
                "<figure data-graphique='id'></figure>) ; SCHÉMAS et "
                "ILLUSTRATIONS en écrivant toi-même un <svg> inline dans "
                "``contenu_html`` (organigramme, flèches, cycle, frise, "
                "diagramme) : il est nettoyé, rendu vectoriel en PDF et "
                "converti en image en DOCX. IMAGES BITMAP : "
                "<img src='data:image/png;base64,...'> dans ``contenu_html`` — "
                "embarquées (aucun accès réseau au rendu) ; une image dont le "
                "côté dépasse 1600 px ou le poids 1 Mio est réduite "
                "automatiquement et signalée dans ``avertissements``. "
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
            handler=partial(_generer_professionnel_ou_brouillon, ctx),
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
            handler=partial(_generer_html, ctx),
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
            handler=partial(_reference, ctx),
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
            handler=partial(_verifier, ctx),
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
            handler=partial(_rendre, ctx),
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
            handler=partial(_corriger, ctx),
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
            handler=partial(_importer_tableau, ctx),
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
            handler=partial(_lire, ctx),
            input_schema=LireDocumentInput,
            tags=("documents", "lecture"),
        ),
        TypedTool(
            name="lire_image",
            description=(
                "Ouvre une image déposée (PNG, JPEG, GIF, WebP) et la fait "
                "**voir** au modèle : capture d'écran, photo d'un document, "
                "schéma, plan, scan. À utiliser dès qu'un document à lire est "
                "une image — ``lire_document`` ne rend qu'un texte, et une "
                "image n'en a pas. "
                "Le retour porte la fiche (nom, dimensions), l'image "
                "elle-même et sa valeur ``base64`` ; décris ce que tu observes "
                "et cite ce que tu lis vraiment (ne devine jamais un chiffre "
                "flou : dis qu'il l'est). L'outil reste actif même quand la "
                "vision du modèle est désactivée : ``vision_activee`` dit alors "
                "la vérité, et tu ne décris pas une image que tu ne vois pas. "
                "Lecture seule, aucun chemin disque."
            ),
            handler=partial(_lire_image, ctx),
            input_schema=LireImageInput,
            tags=("documents", "lecture", "vision"),
            multimodal=True,
        ),
        TypedTool(
            name="read_document",
            description=(
                "Lit un document : métadonnées (nom, type, version, taille) ET "
                "aperçu borné de son texte (début du document). Alias de "
                "lire_document. Lecture seule, aucun chemin disque exposé."
            ),
            handler=partial(_lire, ctx),
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
            handler=partial(_inspecter, ctx),
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
            handler=partial(_lire_plage, ctx),
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
            handler=partial(_chercher, ctx),
            input_schema=ChercherDocumentInput,
            tags=("documents", "lecture", "recherche"),
        ),
        TypedTool(
            name="lire_element_document",
            description=(
                "Lit un ou plusieurs éléments d'un document (docx, pdf, xlsx), "
                "avec le repère rendu par chercher_dans_document "
                "(``hit['relire']``) : 'element' (flux DOCX, page PDF, ligne "
                "XLSX), 'tableau' (rang du tableau, pas la position du flux), "
                "'paragraphe', 'entete'/'pied' (DOCX), 'feuille' (XLSX). "
                "DEUX formes groupées, jamais un appel par élément : "
                "`cibles: [{\"index\": 16, \"type_element\": \"tableau\", "
                "\"sous_index\": 3}, {\"index\": 12, \"type_element\": "
                "\"paragraphe\"}]` pour des éléments hétérogènes (chacun son "
                "type, son index, sa ligne) ; `index: [100, 120]` quand le "
                "type est commun. Les deux rendent `elements` (une entrée par "
                "cible ; une entrée en échec porte 'erreur' sans faire perdre "
                "les autres). TABLEAUX : un appel lit `max_lignes` lignes "
                "(40 par défaut, 200 au plus) ; si `tronque` est vrai, relire "
                "la suite avec `ligne_debut` = `relire_suite.ligne_debut` — "
                "JAMAIS un appel par ligne (sous_index sert à cibler UNE ligne "
                "précise, pas à parcourir le tableau). Retour borné qui dit sa "
                "troncature. Lecture seule."
            ),
            handler=partial(_lire_element, ctx),
            input_schema=LireElementInput,
            tags=("documents", "lecture"),
        ),
        TypedTool(
            name="copier_modele_document",
            description=(
                "Copie le modèle DOCX de l'organisation **tel quel** dans un objet "
                "métier (`ancre` = offre, mission ou session + `entity_id`) : "
                "c'est le document qui sera édité ensuite. "
                "Renvoie `document_id`, la version et la liste des éléments "
                "(identifiants `block_001`…, texte, style). Les styles, la "
                "numérotation, les en-têtes, les pieds, le logo et les textes "
                "fixes du client sont donc conservés par construction. À appeler "
                "quand `lister_modeles_documents` trouve un modèle ; s'il n'y en "
                "a aucun, utiliser `generer_document_professionnel` (ne jamais "
                "inventer de gabarit). Relancer l'outil sur le même objet renvoie "
                "la copie en cours, sans empiler de doublon."
            ),
            handler=partial(_copier_modele, ctx),
            input_schema=CopierModeleDocumentInput,
            tags=("documents", "generation", "modele"),
        ),
        TypedTool(
            name="renommer_document",
            description=(
                "Renomme un document existant **sans le régénérer** : seuls le nom "
                "et la fiche changent — contenu, version et rattachements sont "
                "préservés. Le nom porte son extension ('.docx', '.pdf'…), exigée "
                "par le stockage. **Ne renomme JAMAIS la copie de travail d'un "
                "modèle** (`copier_modele_document`) : l'édition retrouve cette "
                "copie par son nom déterministe `modele-<référence>.docx`, et la "
                "renommer la rend introuvable (`remplir_modele`, "
                "`obtenir_structure_document` et `remplacer_element_model` "
                "échouent alors). Pour changer le nom du **livrable final**, "
                "copier le fichier sous un autre nom une fois le contenu terminé. "
                "Refusé sur un document à plusieurs versions (le nom est la clé de "
                "l'historique) : le message indique alors la sortie."
            ),
            handler=partial(_renommer, ctx),
            input_schema=RenommerDocumentInput,
            tags=("documents", "generation"),
        ),
        TypedTool(
            name="supprimer_document",
            description=(
                "Demande la suppression d'un document : mise à la **corbeille** "
                "(fichier retiré, fiche conservée, restauration possible). "
                "**Validation humaine obligatoire** : l'appel est suspendu et "
                "l'utilisateur approuve ou refuse ; sans accord, le document reste "
                "intact. `motif` est conservé dans l'audit et la corbeille. Ne "
                "jamais supprimer de sa propre initiative : proposer, l'utilisateur "
                "décide."
            ),
            handler=partial(_supprimer, ctx),
            input_schema=SupprimerDocumentInput,
            tags=("documents", "suppression"),
        ),
        TypedTool(
            name="obtenir_structure_document",
            description=(
                "Relit la copie de modèle d'un objet (`ancre` + `entity_id`) : éléments avec leur "
                "`element_id`, leur texte actuel, leur style, leur niveau de titre "
                "et leurs images, plus l'`historique` des versions (numéro, "
                "nature du geste, courante). Lecture seule, à appeler AVANT "
                "d'éditer, APRÈS un lot d'éditions (les identifiants sont "
                "recalculés à chaque modification) et avant de restaurer une "
                "version précise avec `revenir_version_document`."
            ),
            handler=partial(_structure_document, ctx),
            input_schema=ObtenirStructureDocumentInput,
            tags=("documents", "modele", "lecture"),
        ),
        TypedTool(
            name="remplacer_element_model",
            description=(
                "Remplace le TEXTE d'éléments existants de la copie, **sans "
                "changer leur mise en forme** : style de paragraphe, police, "
                "alignement, retrait et interligne du modèle sont conservés. "
                "Un élément (`element_id` + `nouveau_element`) OU plusieurs en un "
                "seul appel (`elements` + `nouveaux_elements`, deux listes de "
                "même longueur) : le lot ne produit qu'UNE version, inutile "
                "d'appeler l'outil une fois par ligne. Chaque contenu est une "
                "chaîne ou une liste de segments {texte, gras, italique, "
                "souligne, couleur, taille_pt}. Un tableau ou un paragraphe "
                "portant une image est refusé (utiliser `remplir_modele`) : "
                "aucune image n'est supprimée en silence."
            ),
            handler=partial(_remplacer_element, ctx),
            input_schema=RemplacerElementModeleInput,
            tags=("documents", "generation", "modele"),
        ),
        TypedTool(
            name="remplir_modele",
            description=(
                "Remplit un ou plusieurs éléments de la copie avec du **HTML "
                "stylisé** : titres, paragraphes, listes <ul>/<ol>, tableaux, "
                "images en data URI base64 et <svg> (rasterisé). Les éléments "
                "visés sont remplacés (ou complétés avec `position: avant|apres`), "
                "le reste du modèle est intact. Une seule version est créée pour "
                "tout l'appel : fournir les identifiants lus ensemble. À utiliser "
                "pour rédiger les sections ; `remplacer_element_model` reste la "
                "voie courte pour un titre ou une ligne."
            ),
            handler=partial(_remplir_modele, ctx),
            input_schema=RemplirModeleInput,
            tags=("documents", "generation", "modele"),
        ),
        TypedTool(
            name="revenir_version_document",
            description=(
                "Restaure une version de l'historique d'une copie de modèle : son "
                "contenu redevient le document de travail, déposé comme NOUVELLE "
                "version (rien n'est supprimé ni écrasé, l'historique reste "
                "complet). `version` omise ⇒ annule la dernière édition ; "
                "`version` fournie ⇒ revient à cette version précise, lue dans "
                "`historique` de `obtenir_structure_document`. À utiliser quand "
                "un lot (`remplir_modele` / `remplacer_element_model`) a abîmé le "
                "document : revenir à l'état sain voulu, relire la structure, "
                "puis refaire le lot. Une version inconnue est refusée avec la "
                "liste des versions disponibles."
            ),
            handler=partial(_revenir_version, ctx),
            input_schema=RevenirVersionDocumentInput,
            tags=("documents", "generation", "modele"),
        ),
        TypedTool(
            name="lister_modeles_documents",
            description=(
                "Liste les modèles DOCX d'une organisation (registre, toutes "
                "versions) : nom, type, version, statuts. À appeler en PREMIER "
                "quand l'utilisateur parle d'un « modèle », d'un « gabarit » ou "
                "d'un document imposé par un client. Liste vide ⇒ aucun modèle : "
                "produire le document avec `generer_document_professionnel`, "
                "jamais avec un gabarit inventé. Lecture seule."
            ),
            handler=partial(_lister_modeles, ctx),
            input_schema=ListerModelesDocumentsInput,
            tags=("documents", "modele", "lecture"),
        ),
        TypedTool(
            name="obtenir_structure_modele",
            description=(
                "Renvoie les ÉLÉMENTS réels d'un modèle DOCX : identifiants "
                "(`block_001`…), texte, style, niveau de titre, images, tableaux, "
                "plus les zones (en-tête, pied, format) et les `{{clé}}` "
                "éventuels. Analyse le fichier s'il ne l'est pas encore (lecture "
                "déterministe, aucun contenu inventé). Sert à choisir le modèle et "
                "à préparer le plan de remplissage ; le fichier n'est jamais "
                "modifié. Lecture seule."
            ),
            handler=partial(_structure_modele, ctx),
            input_schema=ObtenirStructureModeleInput,
            tags=("documents", "modele", "lecture"),
        ),
        TypedTool(
            name="generer_image",
            description=(
                "Dépose une IMAGE RÉELLE produite par le modèle : son contenu "
                "arrive en base64 (PNG, JPEG, GIF ou WebP, avec ou sans préfixe "
                "data:image/...;base64,). Le serveur reconnaît le format à la "
                "signature binaire, réduit l'image aux bornes d'insertion (côté "
                "≤ 1600 px, poids ≤ 1 Mio) et le dit dans `avertissements` ; une "
                "image animée n'est jamais aplatie. Le fichier est déposé comme "
                "PROPOSITION (validation humaine obligatoire) et le retour donne "
                "`document_id`, dimensions, format et poids. NE JAMAIS inventer "
                "un base64 : si ton modèle n'émet pas d'octets d'image, appelle "
                "`generer_image_svg`. Pour que l'utilisateur la voie et la "
                "télécharge, appeler ensuite `afficher_image`."
            ),
            handler=partial(_generer_image, ctx),
            input_schema=GenererImageInput,
            tags=("documents", "generation", "image"),
        ),
        TypedTool(
            name="generer_image_svg",
            description=(
                "Dessine une image en SVG puis la dépose en PNG : le chemin des "
                "modèles qui n'émettent que du texte. Fournir un SVG complet "
                "(`<svg viewBox=...>` avec formes, textes, dégradés) ; il est "
                "nettoyé (aucun script, aucun `href` distant, `style` filtré), "
                "rasterisé en PNG, réduit aux bornes d'insertion et déposé comme "
                "PROPOSITION. Le retour donne `document_id`, dimensions, poids et "
                "`avertissements` (rasterisation, réduction éventuelle). Pour que "
                "l'utilisateur la voie et la télécharge, appeler ensuite "
                "`afficher_image`. Une image à partir d'octets réels se dépose "
                "avec `generer_image`."
            ),
            handler=partial(_generer_image_svg, ctx),
            input_schema=GenererImageSvgInput,
            tags=("documents", "generation", "image"),
        ),
        TypedTool(
            name="composer_image",
            description=(
                "VOIE RECOMMANDÉE pour une image CARSO : tu fournis le CONTENU "
                "(surtitre, titre, sous-titre, points, pied) et le serveur compose "
                "le visuel — marges, hiérarchie typographique, palette contrastée, "
                "gabarit 1:1 (`carte`), 16:9 (`banniere`), bandeau ou A4 "
                "(`affiche`). Aucun balisage à écrire : le résultat est propre et "
                "reproductible. L'image est déposée en PROPOSITION (validation "
                "humaine) et le retour donne `document_id`, dimensions et "
                "`diagnostics` (défauts mesurés). Appeler ensuite `afficher_image`. "
                "N'invente aucun texte : ce qui doit figurer sur l'image vient de "
                "la demande ou des données consultées."
            ),
            handler=partial(_composer_image, ctx),
            input_schema=ComposerImageInput,
            tags=("documents", "generation", "image"),
        ),
        TypedTool(
            name="verifier_image_svg",
            description=(
                "CONTRÔLE un SVG avant de le livrer : rasterise en mémoire et "
                "renvoie les défauts mesurables — `texte_hors_cadre`, "
                "`texte_trop_petit`, `contraste_insuffisant`, `image_vide`, "
                "`palette_trop_riche`, `fond_transparent` — avec un niveau "
                "(`bloquant` / `avertissement` / `conseil`) et un correctif. "
                "RIEN n'est déposé : c'est la boucle de correction d'un SVG écrit "
                "à la main (corriger → revérifier → `generer_image_svg`). Un "
                "`pret_a_deposer: false` interdit d'afficher l'image en l'état."
            ),
            handler=partial(_verifier_image_svg, ctx),
            input_schema=VerifierImageSvgInput,
            tags=("documents", "generation", "image", "controle"),
        ),
    ]
