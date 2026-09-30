"""Outils frontend connus du runtime — présentation seulement.

Les hooks ``useFrontendTool`` du navigateur enregistrent ces noms auprès de
CopilotKit. L'inspecteur les voit. Deep Agents, lui, ne lie que les
``TypedTool`` du contrat : sans ce catalogue + le middleware, le modèle
affirme n'avoir « aucun outil d'affichage ».

Règles :

- **Allowlist figée.** Un nom hors de ``OUTILS_FRONTEND`` n'est jamais stubé,
  même s'il arrive dans ``state["tools"]`` (AG-UI).
- **Aucun service.** Un stub renvoie ``{ok: true, rendu: "client"}``. Le
  handler réel vit dans le navigateur.
- **Hors contrat métier.** Ces noms n'entrent pas dans
  ``AgentDefinition.tools`` : ``allowed_tools_only`` reste inchangé.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

#: Agents exposés au chat (tous les agents du catalogue UI).
AGENT_GENERALISTE = "agent_generaliste_readonly"
AGENT_GENERATEUR = "agent_generateur_offre"
AGENT_RH = "agent_rh"
AGENT_ASSISTANT = "agent_assistant_formateur"
AGENT_STATISTIQUE = "agent_statistique"

TOUS_LES_AGENTS_UI: frozenset[str] = frozenset(
    {
        AGENT_GENERALISTE,
        AGENT_GENERATEUR,
        AGENT_RH,
        AGENT_ASSISTANT,
        AGENT_STATISTIQUE,
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
        name="afficher_tableau_donnees",
        description=(
            "Affiche des lignes métier déjà lues (appels, offres, missions, "
            "équipes, documents…) dans un tableau. Obligatoire : titre, "
            "colonnes[{cle, titre}] et lignes[{id, valeurs}]. N'invente "
            "aucune colonne ni aucune valeur. 50 lignes maximum."
        ),
        agents=TOUS_LES_AGENTS_UI,
        usage=(
            "Après un search_* : passer titre, colonnes[{cle, titre}] et "
            "lignes[{id, valeurs}]. Sans ces champs le tableau est vide."
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
            "Après generer_document / fill_template / clone_document_structure, "
            "offrir le téléchargement. Ne pas inventer d'identifiant."
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
            "(titre, champs clé/valeur, sources). Lecture seule."
        ),
        agents=frozenset({AGENT_GENERALISTE}),
        usage="Croiser plusieurs lectures en une fiche, sans rien modifier.",
    ),
    OutilFrontend(
        name="afficher_lots_appel",
        description=(
            "Affiche les lots d'un appel à proposition avec, pour chacun, "
            "l'état des offres déjà créées et la création du brouillon. "
            "Un lot porte au plus une offre par type (technique, financière, autre)."
        ),
        agents=frozenset({AGENT_GENERATEUR}),
        usage="Quand l'utilisateur doit choisir un lot ou voir l'état des offres.",
    ),
    OutilFrontend(
        name="proposer_creation_mission",
        description=(
            "Affiche un formulaire de création de mission pré-rempli "
            "(offres du lot, lieu, dates). L'utilisateur modifie ce qu'il veut, "
            "puis crée la mission ou renonce : rien n'est enregistré sans son geste."
        ),
        agents=frozenset({AGENT_GENERATEUR}),
        usage=(
            "Après les offres d'un lot, proposer la mission sans l'imposer : "
            "n'invente ni lieu, ni date."
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
        usage="Après analyse des CV, afficher le classement — ne pas affecter.",
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
    """Arguments de ``afficher_tableau_donnees`` — même contrat que le hook client."""

    model_config = ConfigDict(extra="allow")

    titre: str = Field(
        description="Titre du tableau, par exemple « Appels à proposition reçus ».",
    )
    description: str | None = Field(default=None, description="Précision sous le titre.")
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


class PayloadTelechargement(BaseModel):
    """Arguments de ``proposer_telechargement_document``."""

    document_id: str = Field(
        description="UUID du document déjà enregistré. Ne pas inventer.",
    )
    nom: str | None = Field(default=None, description="Nom affiché sur le bouton.")
    libelle: str | None = Field(default=None, description="Texte du bouton.")


class PayloadSynthese(BaseModel):
    """Arguments de ``afficher_synthese_lecture``."""

    model_config = ConfigDict(extra="allow")

    titre: str = Field(description="Titre de la synthèse.")
    sous_titre: str | None = Field(default=None)
    champs: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Informations clé/valeur déjà lues.",
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
        description="Lignes d'aperçu — aucune n'est encore enregistrée.",
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
        description="Conséquence exacte du choix — ce qui va se passer.",
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


SCHEMAS_FRONTEND: dict[str, type[BaseModel]] = {
    "naviguer_vers": PayloadNavigation,
    "afficher_tableau_donnees": PayloadTableau,
    "proposer_telechargement_document": PayloadTelechargement,
    "poser_questionnaire": PayloadQuestionnaire,
    "afficher_synthese_lecture": PayloadSynthese,
    "afficher_lots_appel": PayloadLots,
    "proposer_creation_mission": PayloadPropositionMission,
    "afficher_proposition_affectation": PayloadProposition,
    "afficher_liste_beneficiaires": PayloadBeneficiaires,
    "afficher_tableau_import_beneficiaires": PayloadImportBeneficiaires,
    "afficher_indicateurs": PayloadIndicateurs,
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
        lignes.append(f"- `{outil.name}` — {outil.usage}")
    return "\n".join(lignes)
