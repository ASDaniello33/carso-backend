"""Outils frontend connus du runtime—présentation seulement.

Les hooks ``useFrontendTool`` du navigateur enregistrent ces noms auprès de
CopilotKit. L'inspecteur les voit. Deep Agents, lui, ne lie que les
``TypedTool`` du contrat—sans ce catalogue + le middleware, le modèle
affirme n'avoir « aucun outil d'affichage ».

Règles :

- **Allowlist figée.** Un nom hors de ``OUTILS_FRONTEND`` n'est jamais stubé,
  même s'il arrive dans ``state["tools"]`` (AG-UI).
- **Aucun service.** Un stub renvoie ``{ok: true, rendu: "client"}``. Le
  handler réel vit dans le navigateur.
- **Hors contrat métier.** Ces noms n'entrent pas dans
  ``AgentDefinition.tools``—``allowed_tools_only`` reste inchangé.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

#: Agents exposés au chat (tous les agents du catalogue UI).
AGENT_GENERALISTE = "agent_generaliste_readonly"
AGENT_GENERATEUR = "agent_generateur_offre"
AGENT_RH = "agent_rh"
AGENT_ASSISTANT = "agent_assistant_formateur"
AGENT_STATISTIQUE = "agent_statistique"
AGENT_PERSONNEL = "agent_personnel"

TOUS_LES_AGENTS_UI: frozenset[str] = frozenset(
    {
        AGENT_GENERALISTE,
        AGENT_GENERATEUR,
        AGENT_RH,
        AGENT_ASSISTANT,
        AGENT_STATISTIQUE,
        AGENT_PERSONNEL,
    }
)


@dataclass(frozen=True)
class OutilFrontend:
    """Un outil d'affichage déjà enregistré côté navigateur."""

    name: str
    description: str
    agents: frozenset[str]
    usage: str
    #: HITL : le modèle le voit, le ToolNode est interrompu (``respond``).
    hitl: bool = False


OUTILS_FRONTEND: tuple[OutilFrontend, ...] = (
    OutilFrontend(
        name="naviguer_vers",
        description=(
            "Ouvre une page de l'application CARSO pour l'utilisateur "
            "(fiche, liste, rapport). L'agent ne modifie jamais les données "
            "par ce biais."
        ),
        agents=TOUS_LES_AGENTS_UI,
        usage="Après une lecture, amener l'utilisateur sur la fiche concernée.",
    ),
    OutilFrontend(
        name="page_actuelle",
        description=(
            "Lit la page actuellement ouverte par l'utilisateur : chemin, section, "
            "identifiant de l'entité affichée et ancre documentaire suggérée "
            "(appel_id, offre_id, session_id, mission_id, equipe_id). "
            "À appeler AVANT de générer ou déposer un document quand l'utilisateur "
            "est sur une fiche : le document se rattache au bon élément sans "
            "demander d'UUID. Lecture seule : ne modifie rien."
        ),
        agents=TOUS_LES_AGENTS_UI,
        usage=(
            "Avant generer_document / generer_document_professionnel : récupérer "
            "l'ancre réelle de la page ouverte (recopier les champs tels quels)."
        ),
    ),
    OutilFrontend(
        name="afficher_tableau_donnees",
        description=(
            "Affiche des lignes métier déjà lues (appels, offres, missions, "
            "sessions, documents…) dans un tableau. Obligatoire : titre, "
            "colonnes[{cle, titre}] et lignes[{id, valeurs}]. Renseigner "
            "ressource quand toutes les lignes sont de la même ressource et "
            "que chaque id est l'identifiant réel de l'élément : la ligne "
            "ouvre alors consulter, modifier et supprimer, dans les limites "
            "des permissions du compte. Renseigner colonnes[].mono pour une "
            "colonne d'identifiants et colonnes[].num pour une colonne de "
            "nombres, seulement quand c'est vrai. Sans ressource, le tableau "
            "reste en lecture seule. N'invente aucune colonne, aucune valeur "
            "et aucun identifiant. 50 lignes maximum."
        ),
        agents=TOUS_LES_AGENTS_UI,
        usage=(
            "Après un search_* : passer titre, colonnes[{cle, titre}] et "
            "lignes[{id, valeurs}]. Sans ces champs le tableau est vide."
        ),
    ),
    OutilFrontend(
        name="afficher_resultats_web",
        description=(
            "Affiche des résultats trouvés sur le web (images, vidéos ou "
            "documents) : titre, lien, vignette, type de fichier. Obligatoire : "
            "titre, nature (image | video | document) et resultats[{titre, url}]. "
            "Recopier aussi, tels quels, `image` (fichier de l'image) et `media` "
            "(fichier de la vidéo) : c'est ce qui affiche l'image en grand et lit "
            "la vidéo dans l'interface. Ne présente que ce que le tool de recherche "
            "a réellement renvoyé."
        ),
        agents=TOUS_LES_AGENTS_UI,
        usage=(
            "Après une recherche web qui a rendu des fichiers ou une vidéo "
            "(langsearch_web_search avec filetype:, ou rechercher_video) : "
            "afficher les liens trouvés et dire qu'ils sont externes, à vérifier. "
            "Ne jamais recopier un contenu qu'on n'a pas lu."
        ),
    ),
    OutilFrontend(
        name="afficher_carte",
        description=(
            "Affiche une carte dans la conversation : des lieux et zones (un nom "
            "suffit, la position est recherchée), et éventuellement un itinéraire "
            "départ → arrivée avec distance routière, durée et carburant. "
            "Obligatoire : titre. `points[{nom, lieu_id?, latitude?, longitude?, "
            "nature?}]` ; `itineraire{depart, arrivee, aller_retour?, "
            "consommation_l_100km?, prix_litre?}` pour un trajet. `nature` vaut "
            "lieu (défaut), zone (zone d'un lot) ou depart. `mode_couleur` vaut "
            "etat (smoothness/accès OSM) ou type (autoroute, rue, piste…), `fond` "
            "vaut plan ou satellite ; `couleurs` surcharge la palette par catégorie "
            "et `legende[{libelle, couleur, detail?, categorie?}]` personnalise "
            "la légende : avec `categorie`, renomme/recolore la catégorie "
            "correspondante et le tracé suit ; sans, c'est une annotation en "
            "plus, jamais une couleur inventée sur le tracé. "
            "N'invente ni coordonnée ni identifiant ; lecture seule."
        ),
        agents=TOUS_LES_AGENTS_UI,
        usage=(
            "Dès qu'une réponse parle de lieux, de zones de lots, de trajet ou de "
            "déplacement : passer les noms réels (ou un lieu_id lu), et un "
            "itineraire pour obtenir distance, durée et carburant. Ne pas "
            "recopier une distance calculée à la main. Pour un trajet, choisir "
            "mode_couleur selon la question (état ou type) ; n'utiliser legende "
            "et couleurs que pour une intention explicite. Pour renommer ou "
            "recolorer une catégorie du tracé, passer sa `categorie` (bon, "
            "route_principale, piste…) : le tracé et la légende resteront "
            "accordés. Ne jamais décrire une catégorie absente du tracé."
        ),
    ),
    OutilFrontend(
        name="proposer_telechargement_document",
        description=(
            "Propose le téléchargement d'un document déjà enregistré "
            "(généré ou déposé). L'utilisateur clique ; l'agent n'écrit rien."
        ),
        agents=TOUS_LES_AGENTS_UI,
        usage=(
            "Après generer_document_professionnel / generer_document_html / "
            "generer_document / fill_template / clone_document_structure, offrir "
            "le téléchargement. Ne pas inventer d'identifiant."
        ),
    ),
    OutilFrontend(
        name="poser_questionnaire",
        description=(
            "Pose une ou plusieurs questions à l'utilisateur dans le chat "
            "(choix prédéfinis, texte libre, ou approbation). Le chat n'est "
            "pas coupé : l'agent attend les réponses avant d'agir. Aucune "
            "écriture métier."
        ),
        agents=TOUS_LES_AGENTS_UI,
        usage=(
            "Questionner l'utilisateur (choix, texte libre, approbation) "
            "sans couper le chat. Attendre les réponses avant d'agir. "
            "N'invente aucun choix."
        ),
        hitl=True,
    ),
    OutilFrontend(
        name="afficher_synthese_lecture",
        description=(
            "Présente une réponse de consultation sous forme de fiche lisible "
            "(titre, champs clé/valeur, sources). Lecture seule. Chaque entrée de "
            "`champs` porte un `libelle` (le nom de l'information, jamais vide) et "
            "une `valeur` : le libellé est obligatoire, pas un complément."
        ),
        agents=frozenset({AGENT_GENERALISTE, AGENT_PERSONNEL}),
        usage=(
            "Croiser plusieurs lectures en une fiche, sans rien modifier. Donner "
            "chaque information sous la forme {libelle, valeur} — un libellé absent "
            "laisse la fiche sans intitulé."
        ),
    ),
    OutilFrontend(
        name="afficher_lots_appel",
        description=(
            "Affiche les lots d'un appel avec, pour chacun, "
            "l'état des offres déjà créées et la création du brouillon. "
            "Un lot porte au plus une offre par type (technique, financière, autre)."
        ),
        agents=frozenset({AGENT_GENERATEUR}),
        usage="Quand l'utilisateur doit choisir un lot ou voir l'état des offres.",
    ),
    OutilFrontend(
        name="proposer_creation_organisation",
        description=(
            "Affiche un formulaire de création d'organisation pré-rempli "
            "(nom, type, adresse, e-mail, téléphone, statut). L'utilisateur "
            "corrige puis enregistre, ou renonce : rien n'est créé sans son "
            "geste. À utiliser dès qu'un appel nomme une organisation absente "
            "du référentiel — l'agent ne crée jamais l'organisation lui-même."
        ),
        agents=frozenset({AGENT_GENERATEUR}),
        usage=(
            "Quand `search_organisations` ne trouve pas l'organisation "
            "émettrice : proposer sa création avec les valeurs réellement lues "
            "dans le document, puis reprendre `enregistrer_appel`."
        ),
    ),
    OutilFrontend(
        name="proposer_creation_mission",
        description=(
            "Affiche un formulaire de création de mission pré-rempli "
            "(lot, lieu, dates, offres déjà connues). L'utilisateur modifie ce "
            "qu'il veut, puis crée la mission ou renonce : rien n'est "
            "enregistré sans son geste. La mission naît du lot — les offres "
            "approuvées du lot se rattacheront ensuite (règle 26/10)."
        ),
        agents=frozenset({AGENT_GENERATEUR}),
        usage=(
            "Dès qu'un lot est officiel, proposer la mission sans l'imposer : "
            "n'invente ni lieu, ni date, et n'attends pas l'approbation des "
            "offres."
        ),
    ),
    OutilFrontend(
        name="proposer_modification_appel_offre",
        description=(
            "Ouvre un formulaire de **modification** pré-rempli pour un appel "
            "ou une offre existant. Obligatoire : ressource (appel|offre), id "
            "réel de la fiche, titre. Tu ne fournis aucune valeur : le "
            "formulaire relit la fiche, l'utilisateur corrige les champs puis "
            "enregistre. N'affirme jamais qu'une modification est faite avant "
            "son geste."
        ),
        agents=frozenset({AGENT_GENERATEUR}),
        usage=(
            "Quand l'utilisateur veut corriger un appel ou une offre : lire la "
            "fiche d'abord (`get_appel`, `search_offres`…), puis proposer le "
            "formulaire avec l'identifiant réel — jamais un titre ni un numéro "
            "de ligne."
        ),
    ),
    OutilFrontend(
        name="proposer_suppression_appel_offre",
        description=(
            "Ouvre une **confirmation de suppression** pour un appel ou une "
            "offre. Le formulaire affiche d'abord l'inventaire de ce qui "
            "disparaît, exige un motif, et n'exécute la suppression qu'au geste "
            "de l'utilisateur. Ne supprime jamais toi-même et ne dis jamais que "
            "c'est fait."
        ),
        agents=frozenset({AGENT_GENERATEUR}),
        usage=(
            "Quand l'utilisateur demande de supprimer un appel ou une offre : "
            "proposer la confirmation avec l'identifiant réel, sans dramatiser "
            "et sans promettre que ce sera réversible."
        ),
    ),
    OutilFrontend(
        name="proposer_modification_rh",
        description=(
            "Ouvre un formulaire de **modification** pré-rempli pour une "
            "personne du vivier, un bénéficiaire, une mission ou une session. "
            "Obligatoire : ressource (equipe|beneficiaire|mission|session), id "
            "réel, titre. Tu ne fournis aucune valeur : le formulaire relit la "
            "fiche, l'utilisateur corrige puis enregistre. N'affirme jamais "
            "qu'une modification est faite avant son geste."
        ),
        agents=frozenset({AGENT_RH}),
        usage=(
            "Quand l'utilisateur veut corriger une fiche (personne, "
            "bénéficiaire, mission, session) : lire la fiche d'abord, puis "
            "proposer le formulaire avec l'identifiant réel."
        ),
    ),
    OutilFrontend(
        name="proposer_suppression_rh",
        description=(
            "Ouvre une **confirmation de suppression** pour une personne du "
            "vivier, un bénéficiaire, une mission ou une session. Le formulaire "
            "affiche d'abord l'inventaire de ce qui disparaît, exige un motif, "
            "et n'exécute la suppression qu'au geste de l'utilisateur. Ne "
            "supprime jamais toi-même."
        ),
        agents=frozenset({AGENT_RH}),
        usage=(
            "Quand l'utilisateur demande de supprimer une de ces fiches : "
            "proposer la confirmation avec l'identifiant réel ; rappeler ce "
            "qui disparaît sans le minimiser."
        ),
    ),
    OutilFrontend(
        name="afficher_proposition_affectation",
        description=(
            "Présente un classement de candidats pour une mission. "
            "L'utilisateur déclenche lui-même la proposition ; elle reste "
            "en attente d'approbation."
        ),
        agents=frozenset({AGENT_RH}),
        usage="Après analyse des CV, afficher le classement : ne pas affecter.",
    ),
    OutilFrontend(
        name="afficher_liste_beneficiaires",
        description=(
            "Affiche les bénéficiaires d'une mission ou d'une session. "
            "Les documents (présence, checklist) se préparent ensuite "
            "depuis la session."
        ),
        agents=frozenset({AGENT_ASSISTANT}),
        usage="Montrer les participants déjà lus, sans en inventer.",
    ),
    OutilFrontend(
        name="afficher_tableau_import_beneficiaires",
        description=(
            "Affiche le tableau éditable d'un import Excel. L'utilisateur "
            "corrige puis clique Enregistrer. N'appelle pas import_beneficiaires."
        ),
        agents=frozenset({AGENT_ASSISTANT}),
        usage="Après aperçu Excel : montrer le tableau, ne pas écrire.",
    ),
    OutilFrontend(
        name="afficher_indicateurs",
        description=(
            "Présente des indicateurs déjà calculés sous forme de bloc chiffré. "
            "Jamais d'estimation ni de chiffre inventé."
        ),
        agents=frozenset({AGENT_STATISTIQUE}),
        usage="Après un calcul réel du service statistique.",
    ),
    OutilFrontend(
        name="generer_dashboard",
        description=(
            "Affiche un dashboard complet (tuiles, graphiques, tableaux) dans la "
            "page Rapports et indicateurs — interface principale, pas le chat — "
            "exportable en PDF. Valeurs issues des tools réels, sources déclarées."
        ),
        agents=frozenset({AGENT_STATISTIQUE}),
        usage=(
            "Quand l'utilisateur veut une vue visuelle, une synthèse ou un "
            "rapport : composer avec query_analytics, read_document et "
            "langsearch_web_search, puis déclarer chaque source."
        ),
    ),
)

OUTILS_PAR_NOM: dict[str, OutilFrontend] = {outil.name: outil for outil in OUTILS_FRONTEND}


def outils_pour_agent(agent_id: str) -> tuple[OutilFrontend, ...]:
    """Outils d'affichage autorisés pour cet agent (vide si hors UI)."""
    return tuple(outil for outil in OUTILS_FRONTEND if agent_id in outil.agents)


def noms_pour_agent(agent_id: str) -> frozenset[str]:
    """Noms seuls — pour filtrer un payload AG-UI."""
    return frozenset(outil.name for outil in outils_pour_agent(agent_id))


def filtrer_outils_agui(payload: object, agent_id: str) -> list[dict[str, object]]:
    """Retient les outils AG-UI dont le nom est allowlisté pour l'agent.

    Un dict sans ``name``, un nom inconnu, ou un outil d'un autre agent
    est ignoré. Rien n'est inventé.
    """
    if not isinstance(payload, list):
        return []
    autorises = noms_pour_agent(agent_id)
    retenus: list[dict[str, object]] = []
    vus: set[str] = set()
    for item in payload:
        if not isinstance(item, dict):
            continue
        nom = item.get("name")
        if not isinstance(nom, str) or nom not in autorises or nom in vus:
            continue
        vus.add(nom)
        retenus.append(item)
    return retenus


def extraire_outils_etat(state: object) -> list[object]:
    """Lit ``state["tools"]`` puis ``state["ag-ui"]["tools"]`` (fusion AG-UI)."""
    if not isinstance(state, dict):
        return []
    tools = state.get("tools")
    if isinstance(tools, list) and tools:
        return list(tools)
    agui = state.get("ag-ui") or state.get("ag_ui")
    if isinstance(agui, dict):
        tools_agui = agui.get("tools")
        if isinstance(tools_agui, list):
            return list(tools_agui)
    return []


class ColonneTableau(BaseModel):
    """Une colonne du tableau d'affichage — même contrat que le hook client."""

    model_config = ConfigDict(extra="allow")

    cle: str | None = Field(
        default=None,
        description="Clé de la colonne, reprise dans valeurs.",
    )
    titre: str | None = Field(
        default=None,
        description="Libellé affiché en en-tête.",
    )
    mono: bool | None = Field(
        default=None,
        description=(
            "true si la colonne porte des identifiants (références, codes) : "
            "elle s'affiche en chasse fixe."
        ),
    )
    num: bool | None = Field(
        default=None,
        description=(
            "true si la colonne porte des nombres à comparer : elle "
            "s'aligne à droite."
        ),
    )


#: Ressources qu'un tableau peut lister (mêmes clés que ``ServiceSuppression``).
#: Vocabulaire fermé : l'affichage ne peut pas réclamer un geste sur une
#: ressource qu'il ne connaît pas, et le schéma le dit au modèle.
RESSOURCES_TABLEAU = Literal[
    "offres",
    "appels",
    "missions",
    "sessions",
    "documents",
    "equipes",
    "organisations",
    "beneficiaires",
    "lots",
]


class LigneTableau(BaseModel):
    """Une ligne du tableau — jamais inventée."""

    model_config = ConfigDict(extra="allow")

    id: str | None = Field(
        default=None,
        description="Identifiant métier de la ligne (UUID).",
    )
    valeurs: dict[str, Any] = Field(
        default_factory=dict,
        description="Valeurs par clé de colonne.",
    )
    url: str | None = Field(
        default=None,
        description="Chemin de la fiche, par exemple /missions/<id>.",
    )
    nom: str | None = Field(default=None, description="Nom lisible de la ligne.")
    document_id: str | None = Field(
        default=None,
        description="Identifiant du document, uniquement si la ligne EST un document.",
    )


class PayloadTableau(BaseModel):
    """Arguments de ``afficher_tableau_donnees`` — même contrat que le hook client.

    ``ressource`` est l'interrupteur des actions de ligne : l'interface ne les
    ouvre que sur une ressource **déclarée**, dont les identifiants sont réels.
    Le vocabulaire est fermé (mêmes clés que ``ServiceSuppression``) pour que
    l'affichage ne puisse pas réclamer un geste sur une ressource inconnue.
    """

    model_config = ConfigDict(extra="allow")

    titre: str = Field(
        description="Titre du tableau, par exemple « Appels à proposition reçus ».",
    )
    description: str | None = Field(default=None, description="Précision sous le titre.")
    ressource: RESSOURCES_TABLEAU | None = Field(
        default=None,
        description=(
            "Ressource listée. À renseigner seulement si toutes les lignes en "
            "sont et si chaque `id` est l'identifiant réel de l'élément — "
            "c'est ce qui ouvre les actions de ligne. À omettre sinon."
        ),
    )
    colonnes: list[ColonneTableau] = Field(
        default_factory=list,
        description="Colonnes affichées, dans l'ordre.",
    )
    lignes: list[LigneTableau] = Field(
        default_factory=list,
        description="Lignes du tableau (jamais plus de 50).",
    )


class PayloadNavigation(BaseModel):
    """Arguments de ``naviguer_vers``."""

    url: str = Field(description="Chemin d'une page, par exemple /offres/<id>.")
    libelle: str | None = Field(default=None, description="Texte du bouton.")
    raison: str | None = Field(default=None, description="Pourquoi ouvrir cette page.")


class PayloadPageActuelle(BaseModel):
    """Arguments de ``page_actuelle`` : aucun.

    L'outil lit l'écran courant du navigateur : tout argument serait une
    invention du modèle, il n'en accepte donc aucun.
    """

    model_config = ConfigDict(extra="forbid")


class PayloadTelechargement(BaseModel):
    """Arguments de ``proposer_telechargement_document``."""

    document_id: str = Field(
        description="UUID du document déjà enregistré. Ne pas inventer.",
    )
    nom: str | None = Field(default=None, description="Nom affiché sur le bouton.")
    libelle: str | None = Field(default=None, description="Texte du bouton.")


#: Libellé d'une information : espaces rognés, longueur minimale stricte. Un
#: libellé fait d'espaces est un libellé **absent** — il est refusé, jamais
#: affiché comme s'il disait quelque chose (même règle côté hook client).
LibelleObligatoire = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class ChampSynthese(BaseModel):
    """Une information clé/valeur d'une fiche de synthèse.

    ``libelle`` est **obligatoire** (constat 07/10 : le modèle remplissait
    souvent la seule valeur, et la fiche s'affichait sans intitulé). Le schéma
    le dit maintenant : un appel sans libellé est refusé avec un message clair,
    que le modèle corrige au coup suivant — plutôt qu'une fiche à moitié lisible.
    """

    model_config = ConfigDict(extra="allow")

    libelle: LibelleObligatoire = Field(
        description=(
            "Nom de l'information, en clair et jamais vide : « Nombre d'appels », "
            "« Date limite », « Organisation »."
        ),
    )
    valeur: Any = Field(
        description="Valeur telle qu'elle a été lue (texte, nombre, booléen).",
    )
    precision: str | None = Field(
        default=None,
        description="Détail court, par exemple « sur 12 au total ».",
    )


class PayloadSynthese(BaseModel):
    """Arguments de ``afficher_synthese_lecture``."""

    model_config = ConfigDict(extra="allow")

    titre: str = Field(description="Titre de la synthèse.")
    sous_titre: str | None = Field(default=None)
    champs: list[ChampSynthese] = Field(
        default_factory=list,
        description=(
            "Informations déjà lues, dans l'ordre de lecture : chaque entrée porte "
            "`libelle` (le nom, jamais vide) ET `valeur`."
        ),
    )
    sources: list[str] | None = Field(default=None)
    url: str | None = Field(default=None)


class PayloadLots(BaseModel):
    """Arguments de ``afficher_lots_appel``."""

    model_config = ConfigDict(extra="allow")

    appel_id: str = Field(description="Identifiant de l'appel (UUID).")
    appel_titre: str = Field(description="Référence et titre de l'appel.")
    organisation: str | None = Field(default=None)
    lots: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Lots de l'appel, dans l'ordre du document.",
    )


class PayloadProposition(BaseModel):
    """Arguments de ``afficher_proposition_affectation``."""

    model_config = ConfigDict(extra="allow")

    mission_id: str = Field(description="Identifiant de la mission (UUID).")
    mission_titre: str = Field(description="Titre lisible de la mission.")
    role_dans_mission: str = Field(description="Rôle proposé dans cette mission.")
    justification: str | None = Field(default=None)
    candidats: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Candidats classés, du plus pertinent au moins pertinent.",
    )


class PayloadBeneficiaires(BaseModel):
    """Arguments de ``afficher_liste_beneficiaires``."""

    model_config = ConfigDict(extra="allow")

    mission_id: str = Field(description="Identifiant de la mission (UUID).")
    mission_titre: str = Field(description="Titre de la mission.")
    session_titre: str | None = Field(default=None)
    beneficiaires: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Bénéficiaires déjà lus.",
    )
    note: str | None = Field(default=None)


class PayloadImportBeneficiaires(BaseModel):
    """Arguments de ``afficher_tableau_import_beneficiaires``."""

    model_config = ConfigDict(extra="allow")

    session_id: str = Field(description="Identifiant de la session (UUID).")
    session_titre: str | None = Field(default=None)
    feuille: str | None = Field(default=None)
    nb_valides: int | None = Field(default=None)
    lignes: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Lignes d'aperçu : aucune n'est encore enregistrée.",
    )
    note: str | None = Field(default=None)


class PayloadPropositionMission(BaseModel):
    """Arguments de ``proposer_creation_mission`` — même contrat que le hook client.

    Incrément 16 : absent de ``SCHEMAS_FRONTEND``, l'outil était stubé avec le
    schéma générique ``PayloadFrontend`` (un seul champ ``note``). Le modèle
    appelait donc l'outil **sans** ``lot_id``/``titre``/``offres`` : le stub
    répondait ``ok`` mais la carte client, sans ``lot_id``, se rendait en
    ``null`` — le fameux « le formulaire est appelé mais rien ne s'affiche ».
    """

    model_config = ConfigDict(extra="allow")

    appel_id: str = Field(description="Identifiant de l'appel (UUID).")
    lot_id: str = Field(description="Identifiant du lot concerné (UUID).")
    lot_reference: str = Field(
        description="Référence lisible du lot (numéro et titre).",
    )
    organisation: str | None = Field(
        default=None,
        description="Organisation émettrice.",
    )
    titre: str = Field(description="Titre proposé pour la mission.")
    reference: str = Field(description="Référence proposée pour la mission.")
    description: str | None = Field(
        default=None,
        description="Objet de la mission, en une phrase.",
    )
    date_debut: str | None = Field(
        default=None,
        description="Date de début prévue (AAAA-MM-JJ).",
    )
    date_fin: str | None = Field(
        default=None,
        description="Date de fin prévue (AAAA-MM-JJ).",
    )
    lieu_propose: str | None = Field(
        default=None,
        description=(
            "Lieu d'exécution indiqué dans l'appel, s'il existe — sinon omettre."
        ),
    )
    offres: list[dict[str, Any]] = Field(
        default_factory=list,
        description=(
            "Offres du lot qui alimenteront la mission "
            "(offre_id, type, reference) — technique, financière…"
        ),
    )


class PayloadPropositionOrganisation(BaseModel):
    """Arguments de ``proposer_creation_organisation`` — même contrat que le hook client.

    Le formulaire est la surface d'écriture : l'agent pré-remplit ce qu'il a lu
    dans le document, l'utilisateur corrige et enregistre. ``nom`` est le seul
    champ exigé par le service — le schéma le dit au modèle au lieu de laisser
    un formulaire vide se rendre sans rien à créer (même régression que le
    formulaire de mission).
    """

    model_config = ConfigDict(extra="allow")

    nom: str = Field(
        min_length=1,
        max_length=255,
        description="Nom exact de l'organisation, tel qu'il figure dans le document.",
    )
    type: str | None = Field(
        default=None,
        max_length=100,
        description=(
            "Nature de l'organisation si le document la nomme : ONG, ministère, "
            "entreprise… À omettre sinon."
        ),
    )
    adresse: str | None = Field(
        default=None,
        description="Adresse postale, si elle est écrite dans le document.",
    )
    email: str | None = Field(default=None, max_length=255)
    telephone: str | None = Field(default=None, max_length=50)
    statut: str | None = Field(
        default=None,
        max_length=50,
        description="Statut administratif éventuel (« active ») — jamais déduit.",
    )
    origine: str | None = Field(
        default=None,
        description=(
            "Où ces informations ont été lues (page, en-tête du document) : "
            "affiché en pied de carte comme trace de lecture."
        ),
    )


class PayloadIndicateurs(BaseModel):
    """Arguments de ``afficher_indicateurs``."""

    model_config = ConfigDict(extra="allow")

    titre: str = Field(description="Titre du bloc d'indicateurs.")
    periode: str | None = Field(default=None)
    indicateurs: list[dict[str, Any]] = Field(
        min_length=1,
        description="Indicateurs déjà calculés, jamais estimés.",
    )
    note: str | None = Field(default=None)


class ChoixQuestionnaire(BaseModel):
    """Option proposée par l'agent — jamais inventée côté client."""

    valeur: str = Field(description="Identifiant stable transmis avec la décision.")
    libelle: str = Field(description="Texte affiché (phrase courte, voix active).")
    description: str | None = Field(
        default=None,
        description=        "Conséquence exacte du choix : ce qui va se passer.",
    )
    recommande: bool | None = Field(
        default=None,
        description="Vrai si l'agent recommande ce choix.",
    )


class QuestionQuestionnaire(BaseModel):
    """Une question du wizard (une seule visible à la fois)."""

    id: str = Field(description="Identifiant stable de la question.")
    question: str = Field(description="Question posée à l'utilisateur.")
    contexte: str | None = Field(
        default=None,
        description="Pourquoi l'agent interrompt, en une phrase.",
    )
    type: Literal["choix", "approbation"] | None = Field(
        default=None,
        description="choix = options + texte libre ; approbation = Approuver / Non approuvé.",
    )
    choix: list[ChoixQuestionnaire] | None = Field(
        default=None,
        description="Options proposées par l'agent. Jamais inventées côté client.",
    )
    reponse_libre: bool | None = Field(
        default=None,
        description="Texte libre proposé (défaut : oui). Passer false pour le masquer.",
    )
    skippable: bool | None = Field(
        default=None,
        description="Si vrai, un bouton Passer est proposé. Défaut : non.",
    )


class PayloadQuestionnaire(BaseModel):
    """Arguments de ``poser_questionnaire`` — même contrat que le hook client."""

    titre: str | None = Field(
        default=None,
        description="En-tête du questionnaire, s'il y en a un.",
    )
    questions: list[QuestionQuestionnaire] = Field(
        min_length=1,
        description="Questions posées, dans l'ordre. Une seule est visible à la fois.",
    )


class LigneGraphiqueDashboard(BaseModel):
    """Une catégorie et sa valeur calculée (widget graphique)."""

    model_config = ConfigDict(extra="allow")

    libelle: str
    valeur: float


class SourceDashboard(BaseModel):
    """Provenance déclarée d'une donnée : base, document ou web."""

    model_config = ConfigDict(extra="allow")

    type: Literal["base", "document", "web"]
    reference: str = Field(
        description=        "Métrique query_analytics, nom du document, ou URL : vérifiable.",
    )


class WidgetDashboard(BaseModel):
    """Widget d'un dashboard : tuile, graphique ou tableau.

    Le contrat strict par forme de widget vit côté client (zod) ; ici, un
    schéma souple ``extra=allow`` suffit — le middleware ne rejette que le
    visiblement invalide, et le rendu ignore les champs qu'il ne connaît pas.
    """

    model_config = ConfigDict(extra="allow")

    type: Literal["tuile", "graphique", "tableau"]
    libelle: str = Field(min_length=1, max_length=200)
    forme: Literal["barres", "lignes", "secteurs"] | None = None
    valeur: float | str | None = None
    detail: str | None = None
    unite: str | None = None
    donnees: list[LigneGraphiqueDashboard] = Field(default_factory=list)
    colonnes: list[dict[str, Any]] = Field(default_factory=list)
    lignes: list[dict[str, Any]] = Field(default_factory=list)


class PayloadDashboard(BaseModel):
    """Arguments de ``generer_dashboard`` — dashboard hors chat, page Rapports."""

    model_config = ConfigDict(extra="allow")

    titre: str = Field(min_length=1, max_length=200)
    sous_titre: str | None = None
    source: Literal["base", "document", "web"]
    widgets: list[WidgetDashboard] = Field(min_length=1, max_length=12)
    sources: list[SourceDashboard] = Field(
        min_length=1,
        description=        "Une entrée par donnée utilisée : chaque chiffre doit être vérifiable.",
    )
    note_methode: str | None = None


class PayloadResultatsWeb(BaseModel):
    """Arguments de ``afficher_resultats_web`` — même contrat que le hook client.

    Aucune donnée n'est enrichie ici : ce que le tool de recherche a rendu est
    affiché tel quel, liens compris. Un résultat sans titre ni URL est écarté
    côté client plutôt que rempli par défaut.
    """

    model_config = ConfigDict(extra="allow")

    titre: str = Field(description="Titre de l'affichage, par exemple « Images trouvées ».")
    nature: str = Field(description="image | video | document.")
    requete: str | None = Field(default=None, description="Requête réellement envoyée au web.")
    resultats: list[dict[str, Any]] = Field(
        default_factory=list,
        description=(
            "Résultats du tool de recherche, dans l'ordre reçu : recopier tel quel "
            "chaque champ rendu (url, image, vignette, media, duree, auteur, "
            "type_fichier, domaine)."
        ),
    )
    note: str | None = Field(
        default=None,
        description="Limite de lecture utile (source externe, date, restriction).",
    )


class PointCarte(BaseModel):
    """Un point de la carte — un nom suffit, la position est recherchée côté client.

    Les coordonnées ne sont acceptées que si elles ont été **lues** (lieu du
    référentiel, réponse d'une recherche) : le modèle ne les invente pas.
    """

    model_config = ConfigDict(extra="allow")

    nom: str = Field(min_length=1, description="Nom affiché du lieu ou de la zone.")
    lieu_id: str | None = Field(default=None, description="UUID d'un lieu existant.")
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    nature: Literal["lieu", "zone", "depart"] | None = Field(
        default=None, description="lieu (défaut), zone d'un lot, ou départ du trajet."
    )


class ItineraireCarte(BaseModel):
    """Trajet demandé : distance, durée et carburant sont calculés par le backend."""

    model_config = ConfigDict(extra="allow")

    depart: PointCarte
    arrivee: PointCarte
    aller_retour: bool | None = None
    consommation_l_100km: float | None = Field(default=None, gt=0, le=60)
    prix_litre: float | None = Field(default=None, ge=0)


class LegendeCarte(BaseModel):
    """Entrée de légende : ce que l'agent affiche doit être vrai.

    Avec ``categorie``, l'entrée renomme **et recolore une catégorie réellement
    dessinée** : la même couleur part au tracé et à la légende. Sans
    ``categorie``, c'est une annotation libre, ajoutée à la légende réelle.
    """

    model_config = ConfigDict(extra="allow")

    libelle: str = Field(min_length=1, max_length=60)
    couleur: str = Field(
        pattern=r"^#[0-9a-fA-F]{6}$",
        description="Couleur hexadécimale #rrggbb, exactement.",
    )
    detail: str | None = Field(default=None, max_length=80)
    categorie: Literal[
        "bon",
        "tolerable",
        "mauvais",
        "impraticable",
        "inconnu",
        "autoroute",
        "route_principale",
        "route_secondaire",
        "rue",
        "piste",
        "chemin",
        "bac",
        "construction",
    ] | None = Field(
        default=None,
        description=(
            "Catégorie réelle à renommer/recolorer (bon, mauvais, piste, rue…). "
            "La couleur s'applique aussi au tracé : la légende ne peut pas "
            "annoncer une teinte absente de la carte."
        ),
    )


class PayloadCarte(BaseModel):
    """Arguments de ``afficher_carte`` — même contrat que le hook client."""

    model_config = ConfigDict(extra="allow")

    titre: str = Field(description="Titre de la carte.")
    description: str | None = Field(default=None, description="Ce que la carte montre.")
    points: list[PointCarte] = Field(
        default_factory=list,
        max_length=30,
        description="Points à afficher (30 au plus).",
    )
    itineraire: ItineraireCarte | None = Field(
        default=None, description="Trajet à calculer et tracer."
    )
    mode_couleur: Literal["etat", "type"] | None = Field(
        default=None,
        description="Axe de couleur : état de la route (défaut) ou type de voie.",
    )
    fond: Literal["plan", "satellite"] | None = Field(
        default=None, description="Fond : plan OpenStreetMap (défaut) ou satellite."
    )
    couleurs: dict[str, str] | None = Field(
        default=None,
        description=(
            "Surcharges #rrggbb par catégorie (niveaux d'état ou types de voie). "
            "Toute clé inconnue est ignorée."
        ),
    )
    legende: list[LegendeCarte] | None = Field(
        default=None,
        max_length=8,
        description=(
            "Légende personnalisée. Avec `categorie`, renomme/recolore une "
            "catégorie réellement dessinée (le tracé suit). Sans `categorie`, "
            "annotation libre ajoutée à la légende automatique."
        ),
    )


class PayloadPropositionEdition(BaseModel):
    """Arguments des outils ``proposer_modification_*`` / ``proposer_suppression_*``.

    L'agent désigne l'entité ; il ne transporte **aucune valeur**. Le
    formulaire relit la fiche par l'API, l'utilisateur corrige les champs, et
    son geste exécute l'écriture : un modèle ne peut donc ni inventer une
    valeur, ni présenter une modification comme déjà faite.
    """

    ressource: Literal[
        "appel", "offre", "mission", "session", "equipe", "beneficiaire"
    ] = Field(description="Ressource de l'entité à modifier ou à supprimer.")
    action: Literal["modifier", "supprimer"] = Field(
        description=(
            "modifier = formulaire éditable ; supprimer = inventaire d'impact "
            "puis confirmation."
        )
    )
    id: str = Field(
        min_length=1,
        max_length=64,
        description="Identifiant réel de l'entité (UUID lu en base), jamais un libellé.",
    )
    titre: str = Field(
        min_length=1,
        max_length=200,
        description=(
            "Libellé court de l'entité, affiché pendant le chargement de la fiche."
        ),
    )
    resume: str | None = Field(
        default=None,
        max_length=500,
        description=(
            "Ce que tu proposes de changer (ou pourquoi supprimer), en une phrase."
        ),
    )


SCHEMAS_FRONTEND: dict[str, type[BaseModel]] = {
    "afficher_carte": PayloadCarte,
    "naviguer_vers": PayloadNavigation,
    "page_actuelle": PayloadPageActuelle,
    "afficher_tableau_donnees": PayloadTableau,
    "afficher_resultats_web": PayloadResultatsWeb,
    "proposer_telechargement_document": PayloadTelechargement,
    "poser_questionnaire": PayloadQuestionnaire,
    "afficher_synthese_lecture": PayloadSynthese,
    "afficher_lots_appel": PayloadLots,
    "proposer_creation_organisation": PayloadPropositionOrganisation,
    "proposer_creation_mission": PayloadPropositionMission,
    "proposer_modification_appel_offre": PayloadPropositionEdition,
    "proposer_suppression_appel_offre": PayloadPropositionEdition,
    "proposer_modification_rh": PayloadPropositionEdition,
    "proposer_suppression_rh": PayloadPropositionEdition,
    "afficher_proposition_affectation": PayloadProposition,
    "afficher_liste_beneficiaires": PayloadBeneficiaires,
    "afficher_tableau_import_beneficiaires": PayloadImportBeneficiaires,
    "afficher_indicateurs": PayloadIndicateurs,
    "generer_dashboard": PayloadDashboard,
}


#: Config ``interrupt_on`` : l'humain répond à la place du tool (pas d'exécution).
POLITIQUE_HITL_FRONTEND: dict[str, object] = {
    "allowed_decisions": ["respond"],
    "description": (
        "L'utilisateur répond au questionnaire dans le chat. "
        "Le tool n'écrit rien : relayer les réponses telles quelles."
    ),
}


def noms_hitl_pour_agent(agent_id: str) -> frozenset[str]:
    """Noms des outils frontend HITL de cet agent (vide si hors UI)."""
    return frozenset(outil.name for outil in outils_pour_agent(agent_id) if outil.hitl)


def politique_interrupt_frontend(agent_id: str) -> dict[str, object]:
    """``interrupt_on`` des outils frontend HITL — ``respond`` seulement."""
    return {nom: dict(POLITIQUE_HITL_FRONTEND) for nom in noms_hitl_pour_agent(agent_id)}


def bloc_outils_frontend(agent_id: str) -> str:
    """Bloc markdown injecté au system prompt — liste concrète, rien d'inventé."""
    outils = outils_pour_agent(agent_id)
    if not outils:
        return ""
    lignes = [
        "## Outils d'affichage (chat)",
        "Ces outils **sont fournis** lorsque l'utilisateur converse dans le chat.",
        "Ils présentent un résultat déjà lu. Ils ne remplacent pas `search_*`,",
        "`lire_document` ni aucun outil métier : tu lis d'abord, tu affiches ensuite.",
        "Interdit : inventer une ligne, une URL, un indicateur, un candidat.",
        "Pour questionner l'utilisateur (choix, texte libre, approbation),",
        "appelle `poser_questionnaire` — ne dis jamais que tu n'as pas cet outil.",
        "",
    ]
    for outil in outils:
        lignes.append(        f"- `{outil.name}` : {outil.usage}")
    return "\n".join(lignes)
