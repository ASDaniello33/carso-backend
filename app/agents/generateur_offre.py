"""AgentGenerateurOffre (clinrules 02) — **agent unique de la chaîne de l'appel**.

Il porte désormais toute la chaîne, sans sous-agent (l'ancien
``AgentAnalyseurAppelAProposition`` a été supprimé et ses responsabilités
fusionnées ici) :

```text
réception de l'appel (proposition ou manifestation d'intérêt)
  ↓ analyse du document source
  ↓ proposition d'extraction structurée (zone *proposal*, HITL)
  ↓ validation humaine → lots officiels
  ↓ sélection des lots que CARSO veut traiter
  ↓ offres par type (technique / financière / autre), une par lot et par type
  ↓ documents générés, ancrés à l'offre, téléchargeables dans le chat
  ↓ révision avec l'utilisateur (nouvelle version, jamais d'écrasement)
  ↓ proposition de création de mission (formulaire pré-rempli, HITL)
```

Il n'officialise rien : aucune donnée extraite, aucune offre, aucun document
ne devient officiel sans décision humaine.

Matrice d'accès (validée) : Read/Write sous HITL sur ``Document``,
``AppelAProposition``, ``Lot``, ``Offre`` ; autres tables lecture seule.
Périmètre outils : ``app/agents/tools/agent_generateur_offre_tools.py``.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.agents import prompt
from app.agents.base import AgentDefinition, CollaborationGrant
from app.agents.hitl import HitlReponse, HitlScenario
from app.agents.runtime import AgentTaskService
from app.agents.toolkit import KIT_CAPABILITY, ouvrir_trace
from app.agents.tools.agent_generateur_offre_tools import (
    NOMS_TOOLS,
    construire_tools_generateur_offre,
)
from app.application.services import LotService
from app.core.errors import NotFoundError
from app.domain.document import AgentTask
from app.domain.enums import TypeOffre
from app.infrastructure.database import SessionLocal
from app.tools.permissions import PermissionPolicy

AGENT_ID = "agent_generateur_offre"
#: Chemin déterministe : un lot validé → un brouillon d'offre.
TASK_TYPE = "preparer_offre"
#: Run conversationnel (deep agent) dans un appel : la chaîne complète.
TASK_TYPE_APPEL = "conduire_flux_appel"

PROMPT = prompt.GENERATEUR_OFFRE_SYSTEM_PROMPT

DEFINITION = AgentDefinition(
    agent_id=AGENT_ID,
    display_name="Agent Générateur d'Offre",
    description="Brouillon d'offre (technique, financière ou autre) + document proposé (HITL).",
    prompt=PROMPT,
    policy=PermissionPolicy(
        allowed_capabilities=frozenset(
            {
                KIT_CAPABILITY,
                "analyze_rfp",
                "request_agent_task",
                "get_agent_task_result",
                "web_search",
                "document_read",
                "document_propose",
            }
        )
    ),
    capabilities=(
        KIT_CAPABILITY,
        "analyze_rfp",
        "request_agent_task",
        "get_agent_task_result",
        "web_search",
        "document_read",
        "document_propose",
    ),
    tools=NOMS_TOOLS,
    collaboration=(
        CollaborationGrant(
            agent_id="agent_rh",
            task_types=frozenset({"propose_team_assignment"}),
        ),
    ),
    approval_required=(
        "enregistrer_appel",
        "rattacher_document_appel",
        "submit_rfp_extraction",
        "create_offer_draft",
        "fill_template",
        "generer_document",
        "generer_document_html",
        "corriger_document",
        "clone_document_structure",
        "propose_lot_update",
        "propose_appel_update",
    ),
    output_schema_name="OffreBrouillon",
    skills=("docx-officiels", "pdf-officiels"),
    hitl_scenarios=(
        HitlScenario(
            id="reception_appel",
            titre="Enregistrer cet appel reçu dans le système ?",
            contexte=(
                "L'appel est enregistré avec sa nature explicite (appel à "
                "proposition ou appel à manifestation d'intérêt), son organisation "
                "émettrice déjà connue et son document source. Rien n'est inventé : "
                "une organisation absente du référentiel arrête l'enregistrement."
            ),
            action=(
                "créer l'appel en statut 'recu' et rattacher son document source "
                "au dossier de l'appel"
            ),
            reponses=(
                HitlReponse(
                    valeur="enregistrer",
                    libelle="Enregistrer l'appel",
                    description=(
                        "Appel créé au statut 'recu' avec son document source."
                    ),
                ),
                HitlReponse(
                    valeur="preciser",
                    libelle="Corriger les informations d'abord",
                    description=(
                        "Vous corrigez nature, référence, titre ou organisation "
                        "avant tout enregistrement."
                    ),
                ),
                HitlReponse(
                    valeur="annuler",
                    libelle="Ne pas enregistrer",
                    description="Aucun appel créé, aucune écriture en base.",
                ),
            ),
        ),
        HitlScenario(
            id="validation_extraction",
            titre="Proposer les données extraites de l'appel ?",
            contexte=(
                "L'agent lit lui-même le document source : aucune donnée n'est "
                "inventée, ce qui est ambigu reste marqué à vérifier. La "
                "proposition reste en zone de validation : les lots officiels "
                "naissent de votre approbation."
            ),
            action=(
                "afficher une proposition de données structurées à vérifier, "
                "puis attendre votre décision"
            ),
            reponses=(
                HitlReponse(
                    valeur="analyser",
                    libelle="Analyser le document",
                    description="Lance l'extraction structurée et affiche la proposition.",
                ),
                HitlReponse(
                    valeur="preciser",
                    libelle="Préciser le périmètre d'abord",
                    description=(
                        "Vous indiquez les lots ou sections à cibler avant l'extraction."
                    ),
                ),
                HitlReponse(
                    valeur="annuler",
                    libelle="Ne rien analyser",
                    description="Aucune lecture du document, aucune proposition créée.",
                ),
            ),
        ),
        HitlScenario(
            id="creation_brouillon_offre",
            titre="Créer le brouillon d'offre pour ce lot ?",
            contexte=(
                "Une offre répond à un appel + un lot et porte un type (technique, "
                "financière, autre). Un appel à proposition se répond par une offre "
                "technique et une offre financière. Le brouillon naît en révision et "
                "ne devient officiel qu'après votre publication."
            ),
            action=(
                "créer le brouillon d'offre depuis les données approuvées du lot "
                "et générer le document proposé"
            ),
            reponses=(
                HitlReponse(
                    valeur="creer",
                    libelle="Créer le brouillon",
                    description=(
                        "Brouillon créé avec le document proposé, en révision."
                    ),
                ),
                HitlReponse(
                    valeur="ajuster",
                    libelle="Ajuster les orientations d'abord",
                    description=(
                        "Vous précisez objectifs ou modalités avant la création."
                    ),
                ),
                HitlReponse(
                    valeur="annuler",
                    libelle="Ne pas créer",
                    description="Aucun brouillon, aucune écriture en base.",
                ),
            ),
        ),
        HitlScenario(
            id="creation_mission",
            titre="Proposer la création de la mission de ce lot ?",
            contexte=(
                "La mission est une conséquence possible des offres d'un lot : "
                "elle porte son propre lieu d'exécution et référence ses offres "
                "(technique et financière). L'agent pré-remplit un formulaire, "
                "vous pouvez modifier chaque valeur, l'approuver, ou créer la "
                "mission vous-même plus tard depuis l'interface."
            ),
            action=(
                "préparer un formulaire de mission pré-rempli (offres du lot, "
                "lieu, dates) et l'afficher dans le chat"
            ),
            reponses=(
                HitlReponse(
                    valeur="proposer",
                    libelle="Afficher le formulaire pré-rempli",
                    description=(
                        "Le formulaire s'affiche ; rien n'est enregistré avant "
                        "votre clic sur « Créer la mission »."
                    ),
                ),
                HitlReponse(
                    valeur="plus_tard",
                    libelle="Plus tard, depuis l'interface",
                    description=(
                        "Aucun formulaire : vous créez la mission quand vous voulez."
                    ),
                ),
            ),
        ),
    ),
)


class AgentGenerateurOffre:
    """Prépare brouillon + document proposé. N'officialise rien."""

    def __init__(
        self,
        session: Session,
        *,
        session_factory: Callable[[], Session] | None = None,
        extra_tools: tuple[Any, ...] = (),
    ) -> None:
        self._session = session
        self.definition = DEFINITION
        self._tasks = AgentTaskService(session)
        self._session_factory = session_factory or SessionLocal
        self._extra_tools = extra_tools

    def preparer(
        self,
        lot_id: UUID,
        *,
        reference: str,
        titre: str,
        type: str = TypeOffre.OFFRE_TECHNIQUE.value,
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        """Chemin déterministe : lot validé → brouillon (HITL).

        ``type`` est explicite (technique par défaut) : un appel à proposition
        se répond par une offre technique **et** une offre financière.
        """
        self.definition.policy.require(self.definition.agent_id, KIT_CAPABILITY)
        task = ouvrir_trace(
            self._tasks,
            from_agent=self.definition.agent_id,
            task_type=TASK_TYPE,
            payload={"lot_id": str(lot_id), "reference": reference, "type": type},
            correlation_id=correlation_id,
            requires_approval=True,
        )
        try:
            from app.agents.tools.agent_generateur_offre_tools import (
                _create_offer_draft,
            )

            lot = LotService(self._session).obtenir(lot_id)
            offre = _create_offer_draft(
                self._session,
                {
                    "lot_id": str(lot_id),
                    "reference": reference,
                    "titre": titre,
                    "type": type,
                },
            )
            self._tasks.complete(task, {"offre_id": offre["offre_id"], "lot": lot.numero})
        except Exception as exc:
            # ``type`` est ici le paramètre d'offre : on nomme la classe via
            # l'instance pour ne pas masquer le builtin.
            self._tasks.fail(task, exc.__class__.__name__)
            raise
        reponse = AgentTaskService.as_response(task)
        reponse["offre"] = offre
        return reponse

    # --- Chemin conversationnel (deep agent, tracé) -------------------------

    def run(
        self,
        appel_a_proposition_id: UUID | None = None,
        *,
        instruction: str | None = None,
        correlation_id: str | None = None,
        thread_id: str | None = None,
        runtime: Any | None = None,
    ) -> dict[str, Any]:
        """Exécute le deep agent sur la chaîne de l'appel, sous trace ``AgentTask``.

        L'agent lit lui-même les documents, propose l'extraction, crée les offres
        et les documents : le LLM décide de ses appels d'outils, le harnais
        n'expose que les tools du contrat, et chaque écriture reste HITL.

        Args:
            appel_a_proposition_id: appel en cours de traitement (facultatif :
                le message de l'utilisateur peut porter le contexte).
            instruction: consigne complémentaire de l'utilisateur.
            correlation_id: corrélation inter-agent éventuelle.
            thread_id: fil LangGraph (persistance/checkpoint).
            runtime: configuration d'exécution (modèle, checkpointer).

        Returns:
            La réponse au contrat inter-agent ; en cas d'interruption
            interactive, ``warnings`` porte l'action en attente.
        """
        self.definition.policy.require(self.definition.agent_id, "analyze_rfp")

        # Un run d'agent est une unité de travail à part entière : la trace est
        # commitée immédiatement (elle survit à un échec du run).
        task_id = self._ouvrir_trace(
            {
                "appel_a_proposition_id": (
                    str(appel_a_proposition_id) if appel_a_proposition_id else None
                ),
                "mode": "deep_agent",
                "instruction": instruction,
            },
            correlation_id,
        )
        try:
            graphe = self.build_agent(runtime=runtime)
            resultat = graphe.invoke(
                {
                    "messages": [
                        {
                            "role": "user",
                            "content": self._instruction(appel_a_proposition_id, instruction),
                        }
                    ]
                },
                config=self._config(thread_id or str(task_id), runtime),
            )
        except Exception as exc:
            self._clore_trace(task_id, error=f"{type(exc).__name__}")
            raise

        reponse = self._clore_trace(task_id, result=self._resultat(resultat))
        if "__interrupt__" in resultat:
            reponse["warnings"] = [
                "Action soumise à approbation humaine : reprendre avec resume()"
            ]
        return reponse

    def resume(
        self,
        *,
        thread_id: str,
        decision: str = "approve",
        message: str | None = None,
        runtime: Any | None = None,
    ) -> dict[str, Any]:
        """Reprend un run interrompu après décision humaine (LangGraph ``Command``).

        Args:
            thread_id: identifiant de fil utilisé lors de ``run``.
            decision: ``approve``, ``reject`` ou ``edit`` (voir le harnais).
            message: retour humain transmis à l'agent (utile en cas de rejet).
            runtime: **doit** réutiliser le même checkpointer que le run
                interrompu : un ``MemorySaver`` local ne survit pas entre deux
                requêtes (limitation documentée).
        """
        from langgraph.types import Command

        self.definition.policy.require(self.definition.agent_id, "analyze_rfp")

        graphe = self.build_agent(runtime=runtime)
        decision_payload: dict[str, Any] = {"type": decision}
        if message:
            decision_payload["message"] = message

        resultat = graphe.invoke(
            Command(resume={"decisions": [decision_payload]}),
            config=self._config(thread_id, runtime),
        )
        return self._resultat(resultat)

    # --- internes ----------------------------------------------------------

    @contextmanager
    def _unit_of_work(self) -> Iterator[Any]:
        """Une transaction par écriture de trace (un run n'est pas une transaction)."""
        session = self._session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def _ouvrir_trace(self, payload: dict[str, Any], correlation_id: str | None) -> UUID:
        """Ouvre la tâche ``agent_tasks`` dans sa propre transaction (commitée)."""
        with self._unit_of_work() as session:
            task = AgentTaskService(session).start(
                from_agent=self.definition.agent_id,
                task_type=TASK_TYPE_APPEL,
                payload=payload,
                correlation_id=correlation_id,
                requires_approval=True,
            )
            task_id: UUID = task.id
        return task_id

    def _clore_trace(
        self,
        task_id: UUID,
        *,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> dict[str, Any]:
        """Clôt la tâche (completed/failed) et renvoie la réponse du contrat."""
        with self._unit_of_work() as session:
            tasks = AgentTaskService(session)
            task = session.get(AgentTask, task_id)
            if task is None:
                raise NotFoundError(f"Trace de tâche {task_id} introuvable")
            if error is not None:
                tasks.fail(task, error)
            else:
                tasks.complete(task, result or {})
            reponse = tasks.as_response(task)
        return reponse

    def _instruction(self, appel_a_proposition_id: UUID | None, instruction: str | None) -> str:
        """Message initial du run : l'appel est un contexte, jamais un scénario figé."""
        if appel_a_proposition_id is not None:
            base = (
                f"Conduis la chaîne de l'appel {appel_a_proposition_id} : "
                "lis son document source, propose l'extraction structurée, puis "
                "attends mes décisions à chaque étape."
            )
        else:
            base = (
                "Conduis la chaîne de l'appel à partir de ce que je t'indique "
                "(document en pièce jointe ou appel déjà enregistré). Tu "
                "proposes, je décide."
            )
        if instruction:
            return f"{base}\n\nConsigne complémentaire : {instruction}"
        return base

    def _config(self, thread_id: str, runtime: Any | None) -> dict[str, Any]:
        """Config d'invocation LangGraph : fil + borne anti-boucle."""
        from app.agents.harness import recursion_limit

        return {
            "configurable": {"thread_id": thread_id},
            "recursion_limit": recursion_limit(runtime),
        }

    def _resultat(self, resultat: dict[str, Any]) -> dict[str, Any]:
        """Résumé exploitable d'un état de graphe (jamais le dump brut)."""
        return {
            "messages": _dernier_message(resultat),
            "interrompu": "__interrupt__" in resultat,
            "outils_utilises": _outils_utilises(resultat),
        }

    def deep_tools(self) -> tuple[Any, ...]:
        from app.agents.collaboration_registre import tools_collaboration_generateur
        from app.agents.outillage import tools_documents_pour

        extras = tools_documents_pour(self.definition, self._session_factory)
        extras = extras + tools_collaboration_generateur(self._session_factory)
        return construire_tools_generateur_offre(
            self.definition, self._session_factory, extra_tools=extras
        )

    def build_agent(
        self, *, runtime: Any | None = None, system_prompt_extra: str | None = None
    ) -> Any:
        from app.agents.harness import create_carso_deep_agent

        return create_carso_deep_agent(
            definition=self.definition,
            tools=self.deep_tools(),
            runtime=runtime,
            system_prompt_extra=system_prompt_extra,
        )


def build_generateur(
    session: Session,
    *,
    session_factory: Callable[[], Session] | None = None,
    extra_tools: tuple[Any, ...] = (),
) -> AgentGenerateurOffre:
    return AgentGenerateurOffre(
        session, session_factory=session_factory, extra_tools=extra_tools
    )


# --- utilitaires ---------------------------------------------------------------


def _outils_utilises(resultat: dict[str, Any]) -> list[str]:
    """Noms des tools appelés pendant le run (observabilité du parcours de l'agent)."""
    noms: set[str] = set()
    for message in resultat.get("messages", []) or []:
        for appel in getattr(message, "tool_calls", None) or []:
            nom = (
                appel.get("name")
                if isinstance(appel, dict)
                else getattr(appel, "name", None)
            )
            if nom:
                noms.add(str(nom))
    return sorted(noms)


def _dernier_message(resultat: dict[str, Any]) -> str | None:
    """Contenu du dernier message d'agent (résumé pour ``agent_tasks``)."""
    messages = resultat.get("messages")
    if not messages:
        return None
    contenu = getattr(messages[-1], "content", None)
    if isinstance(contenu, str):
        return contenu[:2000]
    return None


__all__ = [
    "AGENT_ID",
    "DEFINITION",
    "TASK_TYPE",
    "TASK_TYPE_APPEL",
    "AgentGenerateurOffre",
    "build_generateur",
]
