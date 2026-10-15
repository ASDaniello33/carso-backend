"""Tools de ``agent_statistique`` (clinrules 05, refactor outils).

Lieu unique du périmètre outils de l'agent statistique : **lecture seule sur
toutes les tables** (matrice validée) via des agrégations déterministes
nommées. Le service calcule, l'agent explique (instruction/10 §9) : aucun
chiffre de KPI ne naît d'une sortie de modèle.

Tools :

- ``query_analytics`` : agrégations nommées (périmètre fermé) ;
- ``search_missions`` / ``search_sessions`` / ``search_beneficiaires`` /
  ``search_offres`` : lectures nommées bornées utiles au ciblage d'une
  agrégation (mêmes fonctions que le généraliste, readonly) ;
- ``read_document`` / ``lire_document`` : fiche d'un document (nom, type,
  version, taille) + aperçu borné de son texte ;
- ``get_document_structure`` / ``read_document_range`` : structure (feuilles
  XLSX, pages PDF, styles DOCX, ``nb_pages`` + ``unite_pagination``) puis
  lecture d'une plage (``from_page``/``to_page`` : pages PDF, **éléments**
  DOCX, lignes XLSX) : c'est par là que passent les **données** à tabuler
  (demande 04/10) ;
- ``chercher_dans_document`` : recherche ciblée (éléments + extraits bornés)
  : localiser une donnée précise AVANT de lire, sans déverser le document ;
  ``mot_cle`` accepte une **liste** (plusieurs mots-clés en un appel) et chaque
  hit porte ``relire`` pour ``lire_element_document`` (tableau, ligne de
  tableau, en-tête, page, ligne de feuille : ``index`` accepte une **liste**) ;
- ``importer_tableau_xlsx`` : tableau d'un classeur rendu prêt à tabuler
  (fusions, en-tête, largeurs), lecture seule ;
- ``langsearch_web_search`` (recherche web, capacité ``web_search``).
"""

from __future__ import annotations

from typing import Any

from app.agents.consultation import (
    compter_affectations_par_role,
    compter_beneficiaires_par_session,
    compter_missions_par_statut,
    rechercher_appels_a_proposition,
    rechercher_documents,
    rechercher_equipes,
    rechercher_missions,
    rechercher_organisations,
)
from app.agents.hitl import HitlReponse, HitlScenario
from app.agents.toolkit import AgentTools, OperationSpec, PageSearchInput
from app.agents.tools.agent_generaliste_tools import (
    ANALYTIQUES_AUTORISEES,
    AffectationSearchInput,
    AnalyticsInput,
    BeneficiaireSearchInput,
    LotSearchInput,
    OffreSearchInput,
    SessionSearchInput,
    compter_beneficiaires_total,
    compter_offres_par_statut,
    rechercher_affectations,
    rechercher_beneficiaires,
    rechercher_lots,
    rechercher_offres,
    rechercher_sessions,
)
from app.agents.tools.agent_generaliste_tools import (
    executer_analytique as _executer,
)

AGENT_ID = "agent_statistique"
TASK_TYPE = "produire_statistiques"

NOMS_TOOLS: tuple[str, ...] = (
    "query_analytics",
    "read_document",
    "lire_document",
    "get_document_structure",
    "read_document_range",
    "chercher_dans_document",
    "lire_element_document",
    "importer_tableau_xlsx",
    "search_organisations",
    "search_appels_a_proposition",
    "search_lots",
    "search_offres",
    "search_missions",
    "search_sessions",
    "search_beneficiaires",
    "search_equipes",
    "search_affectations",
    "search_documents",
    "langsearch_web_search",
    # Lecture d'une page trouvée : le chiffre se lit **dans la source**, borné,
    # jamais estimé. Même capacité ``web_search`` que la recherche.
    "fetch_url",
    "read_urls",
    # Recherche média (module ``app.tools.recherche_media``) : attachée au
    # contrat par le même kit, donc déclarée ici comme le reste du périmètre.
    "rechercher_image",
    "rechercher_video",
    "rechercher_document",
)

SCENARIOS_HITL = (
    HitlScenario(
        id="portee_analyse",
        titre="Quelle portée pour cette analyse ?",
        contexte=(
            "L'agent est strictement en lecture : il n'écrit rien, mais le "
            "périmètre d'une agrégation peut être large (confidentialité)."
        ),
        action=("exécuter la requête analytique sur la portée choisie"),
        reponses=(
            HitlReponse(
                valeur="portee_section",
                libelle="Limiter à la section concernée",
                description="Agrégation restreinte aux données de la section courante.",
            ),
            HitlReponse(
                valeur="portee_globale",
                libelle="Portée globale autorisée",
                description="Agrégation sur l'ensemble des données autorisées.",
            ),
            HitlReponse(
                valeur="annuler",
                libelle="Annuler l'analyse",
                description="Aucune requête exécutée.",
            ),
        ),
    ),
)


def construire_tools_statistique(
    definition: Any,
    session_factory: Any,
    *,
    read_document_tool: Any = None,
) -> tuple[Any, ...]:
    """Tools du statistique : agrégations + lectures nommées, kit readonly.

    Args:
        definition: contrat de l'agent (allow-list appliquée par le harnais).
        session_factory: fabrique de sessions des tools (1 transaction par appel).
        read_document_tool: tool ``read_document`` câblé par l'appelant
            (source « documents » des dashboards générés) — optionnel.
    """
    kit = AgentTools(definition, readonly=True, session_factory=session_factory)
    kit.enregistrer(OperationSpec(
        name="query_analytics",
        description=(
            "Agrégation déterministe nommée : " + ", ".join(ANALYTIQUES_AUTORISEES) +
            ". Le service calcule."
        ),
        input_schema=AnalyticsInput,
        run=_executer,
    ))
    kit.enregistrer(OperationSpec(
        name="search_missions",
        description="Missions (statut, organisation, texte) — cibler une agrégation.",
        input_schema=PageSearchInput,
        run=rechercher_missions,
    ))
    kit.enregistrer(OperationSpec(
        name="search_sessions",
        description="Sessions de formation (par mission ou toutes).",
        input_schema=SessionSearchInput,
        run=rechercher_sessions,
    ))
    kit.enregistrer(OperationSpec(
        name="search_beneficiaires",
        description="Bénéficiaires par nom/prénom (données minimisées).",
        input_schema=BeneficiaireSearchInput,
        run=rechercher_beneficiaires,
    ))
    kit.enregistrer(OperationSpec(
        name="search_offres",
        description="Offres de formation (statut, organisation).",
        input_schema=OffreSearchInput,
        run=rechercher_offres,
    ))
    kit.enregistrer(OperationSpec(
        name="search_organisations",
        description="Organisations clientes par nom.",
        input_schema=PageSearchInput,
        run=rechercher_organisations,
    ))
    kit.enregistrer(OperationSpec(
        name="search_appels_a_proposition",
        description="Appels à proposition (organisation, statut, texte) + lots.",
        input_schema=PageSearchInput,
        run=rechercher_appels_a_proposition,
    ))
    kit.enregistrer(OperationSpec(
        name="search_lots",
        description="Lots d'un appel (ou tous) : numéro, titre, zone.",
        input_schema=LotSearchInput,
        run=rechercher_lots,
    ))
    kit.enregistrer(OperationSpec(
        name="search_equipes",
        description="Vivier (nom, prénom, profil). Contenu des CV exclu.",
        input_schema=PageSearchInput,
        run=rechercher_equipes,
    ))
    kit.enregistrer(OperationSpec(
        name="search_affectations",
        description="Affectations mission ↔ équipe ↔ rôle d'une mission.",
        input_schema=AffectationSearchInput,
        run=rechercher_affectations,
    ))
    kit.enregistrer(OperationSpec(
        name="search_documents",
        description="Documents (une ancre métier, type). Métadonnées seules.",
        input_schema=PageSearchInput,
        run=rechercher_documents,
    ))

    outils = tuple(kit.construire())

    # Tools documentaires du contrat (lecture seule) : construits ici pour que
    # l'agent lise réellement PDF/DOCX/XLSX (demande 04/10). Le filtre
    # ``definition.allowed_tools()`` s'applique dans l'assembleur : seuls les
    # noms déclarés au contrat sortent.
    from app.agents.outillage import tools_documents_pour

    outils = (*outils, *tools_documents_pour(definition, session_factory))

    # ``read_document`` injecté par l'appelant : conservé seulement s'il n'est pas
    # déjà construit ci-dessus (jamais deux fois le même nom dans le graphe).
    noms = {outil.name for outil in outils}
    if read_document_tool is not None and read_document_tool.name not in noms:
        outils = (*outils, read_document_tool)

    from app.tools.recherche_media import attacher_recherche_media
    from app.tools.web import attacher_outils_web

    return attacher_recherche_media(definition, attacher_outils_web(definition, outils))


# Alias stable pour les agents (chemin déterministe) : une seule porte.
executer_agregation = _executer


__all__ = [
    "AGENT_ID",
    "ANALYTIQUES_AUTORISEES",
    "NOMS_TOOLS",
    "TASK_TYPE",
    "SCENARIOS_HITL",
    "AnalyticsInput",
    "construire_tools_statistique",
    "executer_agregation",
    "compter_beneficiaires_total",
    "compter_missions_par_statut",
    "compter_beneficiaires_par_session",
    "compter_affectations_par_role",
    "compter_offres_par_statut",
]
