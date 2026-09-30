"""Contrat HITL — scénarios de questionnaire et réponses prédéfinies.

Définition validée : **un agent n'exécute aucune action sensible ou ambiguë
sans questionner l'utilisateur dans le chat.** Le questionnaire propose quatre
voies : approuver, refuser, choisir une réponse prédéfinie, ou écrire une
réponse personnalisée.

Ce module rend les réponses prédéfinies **déclaratives** : chaque agent les
déclare dans son contrat (``AgentDefinition.hitl_scenarios``), et le catalogue
(``/agents/catalogue``, ``/agui/``) les expose à l'interface. Le chat
CopilotKit les consomme telles quelles pour afficher le questionnaire —
aucune réponse prédéfinie n'est inventée côté client.

Règles :

- un scénario correspond à un moment précis où l'agent doit interrompre
  (généralement un ``approval_required`` ou une action à impact) ;
- les réponses prédéfinies sont des **choix proposés par l'agent**, jamais des
  décisions : la décision reste humaine (règle 6 [C]) ;
- ``applique`` nomme l'effet si la réponse est choisie — l'utilisateur sait ce
  qui se passe avant de répondre ;
- la réponse personnalisée (texte libre) est toujours disponible en plus des
  choix listés, sans qu'elle soit déclarée ici.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class HitlReponse:
    """Réponse prédéfinie proposée par l'agent dans le questionnaire.

    Attributes:
        valeur: identifiant stable transmis au backend avec la décision.
        libelle: texte affiché (infinitif ou phrase courte, voix active).
        description: conséquence exacte du choix — ce qui va se passer.
    """

    valeur: str
    libelle: str
    description: str


@dataclass(frozen=True)
class HitlScenario:
    """Moment où l'agent interrompt et questionne l'utilisateur.

    Attributes:
        id: identifiant stable du scénario (référence chat/audit).
        titre: question affichée à l'utilisateur.
        contexte: pourquoi l'agent interrompt (données en jeu, impact).
        action: ce que l'agent fera si la réponse valide la continuation.
        reponses: choix prédéfinis ; le texte libre s'ajoute toujours.
    """

    id: str
    titre: str
    contexte: str
    action: str
    reponses: tuple[HitlReponse, ...] = field(default=())


__all__ = ["HitlReponse", "HitlScenario"]
