"""Tools documentaires pour agents (Lot 1, pipeline [D2] ; ADR 0005).

Rôle unique : donner aux agents un accès **contrôlé** à la lecture, l'analyse,
la génération, la validation et l'inspection visuelle des documents. La
frontière HITL (human-in-the-loop) reste intacte :

- ``generer_document`` produit les octets (``app.documents.renderers``), les
  dépose via ``DocumentService.enregistrer_document`` avec ``proposed_by_agent``
  => statut ``proposed`` (règle 6 [C] : jamais ``approved`` depuis un agent) ;
- ``lire_document`` / ``get_document_structure`` / ``read_document_range`` /
  ``extraire_texte`` délèguent à ``DocumentService`` pour la lecture
  (``telecharger`` / ``extraire_texte``), sans contourner le stockage contrôlé
  (jamais de chemin disque manipulé par l'agent) ;
- ``analyze_document_reference`` rend le **plan** et le **style** d'un document
  de référence (jamais son texte : on réutilise une identité visuelle, on ne
  recopie pas un contenu) ;
- ``valider_document`` vérifie une structure avant ou après génération ;
- ``render_document`` rend les pages d'un PDF en images : l'agent (et
  l'utilisateur) voient le **rendu réel**, pas une promesse.

Enchaînement attendu pour un livrable :
``analyze_document_reference → generer_document(document_spec) → valider_document
→ render_document → (corriger) → présenter``.

Le tool ne porte aucune règle métier : il valide son entrée, vérifie la
permission, délègue (AGENTS.md §2.5 : Agent → Tool → Service → Repository).

Une ancre métier **réelle** range le fichier sous l'objet correspondant.
Une ancre absente, inconnue ou inventée range le document sous
``generated/{id}/`` (type ``non_classe``) — aucun UUID n'est inventé.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

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
    "ImporterTableauInput",
    "LireDocumentInput",
    "InspecterDocumentInput",
    "LirePlageInput",
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


class LirePlageInput(BaseModel):
    """Lecture bornée. Pour un DOCX, from/to désignent des paragraphes."""

    document_id: UUID
    from_index: int | None = None
    to_index: int | None = None
    sheet: str | None = Field(default=None, description="Feuille XLSX (optionnel).")


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
        policy.require(identite.agent_id, "document_read")
        document_id = _uuid(payload["document_id"], "document_id")
        service = service_factory()
        document = service.obtenir(document_id)
        _nom_fichier, chemin = service.telecharger(document_id)
        taille = chemin.stat().st_size
        return {
            "document_id": str(document.id),
            "nom": document.nom,
            "statut": document.statut,
            "type_document": document.type_document,
            "mime_type": document.mime_type,
            "version": document.version,
            "taille_octets": taille,
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
        policy.require(identite.agent_id, "document_read")
        from app.documents.inspection import lire_docx_plage, lire_pdf_plage, lire_xlsx_plage

        service = service_factory()
        document_id = _uuid(payload["document_id"], "document_id")
        _nom, chemin = service.telecharger(document_id)
        suffixe = chemin.suffix.lower()
        debut = payload.get("from_index")
        fin = payload.get("to_index")
        if suffixe == ".docx":
            return lire_docx_plage(chemin, from_page=debut, to_page=fin)
        if suffixe == ".pdf":
            return lire_pdf_plage(chemin, from_page=debut, to_page=fin)
        if suffixe == ".xlsx":
            return lire_xlsx_plage(
                chemin, sheet=payload.get("sheet"), from_row=debut, to_row=fin
            )
        raise ValidationError(f"Lecture de plage non supportée pour {chemin.suffix}")

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
            name="generer_document_html",
            description=(
                "OUTIL PRIORITAIRE de génération d'un document personnalisé : "
                "écris le livrable en **HTML+CSS** (type standard, styles très "
                "variés : couleurs, polices, encadrés, images data-URI ou http(s), "
                "tableaux stylés, listes, liens) et dépose le fichier converti "
                "comme PROPOSITION (validation humaine obligatoire). "
                "Formats : 'docx' (conversion fidèle du HTML+CSS) ou 'pdf' "
                "(même contenu rendu par le moteur CARSO — pas de copie pixel du "
                "DOCX, aucun convertisseur bureautique sur ce serveur). "
                "À préférer à ``generer_document`` dès que le document n'a pas de "
                "structure imposée : offres libres, notes de synthèse, courriers "
                "habillés, fiches personnalisées, rapports illustrés. "
                "``generer_document(document_spec)`` reste le chemin attendu pour "
                "les livrables à structure stricte (offre technique/financière "
                "avec couverture et provenances). "
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
                "Lit les métadonnées d'un document (nom, type, version, taille). "
                "Lecture seule, aucune mutation, aucun chemin disque exposé."
            ),
            handler=_lire,
            input_schema=LireDocumentInput,
            tags=("documents", "lecture"),
        ),
        TypedTool(
            name="read_document",
            description=(
                "Lit les métadonnées d'un document (nom, type, version, taille). "
                "Lecture seule, aucune mutation, aucun chemin disque exposé."
            ),
            handler=_lire,
            input_schema=LireDocumentInput,
            tags=("documents", "lecture"),
        ),
        TypedTool(
            name="get_document_structure",
            description=(
                "Inspecte la structure d'un document (styles DOCX, pages PDF, "
                "feuilles XLSX). Lecture seule."
            ),
            handler=_inspecter,
            input_schema=InspecterDocumentInput,
            tags=("documents", "lecture"),
        ),
        TypedTool(
            name="read_document_range",
            description=(
                "Lit une plage d'un document (pages PDF, paragraphes DOCX, "
                "lignes XLSX). Lecture seule."
            ),
            handler=_lire_plage,
            input_schema=LirePlageInput,
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
