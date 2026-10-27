"""Vocabulaire lisible du journal d'audit—nom d'action affiché à l'utilisateur.

Le journal stocke des clés techniques (`offre.publiee`, `suppression.effectuee`) :
elles sont stables, comparables et ne dépendent d'aucune langue. Ce module porte
leur **traduction française**, seule source de vérité pour l'interface—le filtre
d'audit et la colonne « Action » lisent le même vocabulaire, donc un libellé ne
peut pas diverger entre les deux.

Pourquoi une table explicite plutôt qu'une règle mécanique—la dérivation
automatique (`nom.replace("_", " ")`) rendait « Effectuee », « Metadonnees
modifiees » ou « Brouillon creee »—incompréhensibles pour qui n'écrit pas le
code (constat 06/10). Deux actions génériques portent en plus leur entité
(`suppression.effectuee` + `entity_type = "offres"`)—leur libellé est composé,
et c'est le journal qui dit quelles entités ont réellement été supprimées.

Ce module est pur (aucune dépendance FastAPI ni base)—les libellés sont donc
testables sans serveur.
"""

from __future__ import annotations

from app.domain.enums import ActionAudit

__all__ = [
    "ENTITES_SUPPRIMEES",
    "ACTIONS_PAR_ENTITE",
    "LIBELLES_ACTION",
    "LIBELLES_HISTORIQUES",
    "libelle_action",
]

#: Actions dont le libellé dépend de l'entité touchée (une entrée de filtre par
#: entité réellement présente dans le journal).
ACTIONS_PAR_ENTITE: frozenset[str] = frozenset(
    {ActionAudit.SUPPRESSION_EFFECTUEE.value}
)

#: Entité supprimable → forme articulée (« Suppression **d'une offre** »).
#: Les clés sont celles de ``ServiceSuppression`` (``TYPES_SUPPRIMABLES``).
ENTITES_SUPPRIMEES: dict[str, str] = {
    "appels": "d'un appel",
    "offres": "d'une offre de formation",
    "missions": "d'une mission",
    "sessions": "d'une session",
    "documents": "d'un document",
    "equipes": "d'un intervenant",
    "organisations": "d'une organisation",
    "beneficiaires": "d'un bénéficiaire",
    "lots": "d'un lot",
}

#: Libellé affiché de chaque action tracée. Toute valeur d'``ActionAudit`` doit y
#: figurer — un test le verrouille, sinon le vocabulaire dériverait en silence.
LIBELLES_ACTION: dict[str, str] = {
    # Appels à proposition
    ActionAudit.APPEL_A_PROPOSITION_ENREGISTRE.value: "Appel enregistré",
    ActionAudit.APPEL_A_PROPOSITION_MODIFIE.value: "Appel modifié",
    ActionAudit.EXTRACTION_PROPOSEE.value: "Extraction d'appel proposée",
    ActionAudit.EXTRACTION_VALIDEE.value: "Extraction d'appel validée",
    ActionAudit.EXTRACTION_REJETEE.value: "Extraction d'appel rejetée",
    ActionAudit.LOTS_MATERIALISES.value: "Lots créés depuis l'appel",
    # Offres de formation
    ActionAudit.OFFRE_CREEE.value: "Brouillon d'offre créé",
    ActionAudit.OFFRE_SOUMISE.value: "Offre soumise à revue",
    ActionAudit.OFFRE_PUBLIEE.value: "Offre publiée",
    ActionAudit.OFFRE_ARCHIVEE.value: "Offre archivée",
    ActionAudit.OFFRE_MODIFIEE.value: "Offre modifiée",
    # Affectations
    ActionAudit.AFFECTATION_PROPOSEE.value: "Affectation proposée",
    ActionAudit.AFFECTATION_VALIDEE.value: "Affectation validée",
    ActionAudit.AFFECTATION_REFUSEE.value: "Affectation refusée",
    ActionAudit.AFFECTATION_MODIFIEE.value: "Affectation corrigée",
    ActionAudit.AFFECTATION_ANNULEE.value: "Proposition d'affectation annulée",
    # Budget
    ActionAudit.BUDGET_APPROUVE.value: "Budget approuvé",
    ActionAudit.BUDGET_LIGNE_AJOUTEE.value: "Ligne de budget ajoutée",
    ActionAudit.BUDGET_SOUMIS.value: "Budget soumis",
    ActionAudit.BUDGET_REPRIS.value: "Budget repris",
    # Documents
    ActionAudit.DOCUMENT_ENREGISTRE.value: "Document enregistré",
    ActionAudit.DOCUMENT_SOUMIS.value: "Document soumis à décision",
    ActionAudit.DOCUMENT_APPROUVE.value: "Document approuvé",
    ActionAudit.DOCUMENT_REMPLACE.value: "Document remplacé",
    ActionAudit.DOCUMENT_RATTACHE.value: "Document rattaché",
    ActionAudit.DOCUMENT_ARCHIVE.value: "Document archivé",
    ActionAudit.DOCUMENT_SUPPRIME.value: "Document supprimé",
    ActionAudit.DOCUMENT_RESTAURE.value: "Document restauré",
    ActionAudit.DOCUMENT_PURGE.value: "Document purgé définitivement",
    ActionAudit.DOCUMENT_DEPOT_ANNULE.value: "Dépôt de document annulé",
    ActionAudit.DOCUMENT_METADONNEES_MODIFIEES.value: "Métadonnées du document modifiées",
    # ADR 0013 : bascule privé ↔ public d'un document sans objet métier.
    ActionAudit.DOCUMENT_VISIBILITE_MODIFIEE.value: "Visibilité du document changée",
    ActionAudit.MODELE_DOCUMENT_ENREGISTRE.value: "Modèle de document enregistré",
    ActionAudit.SUPPORT_FORMATION_AJOUTE.value: "Support de formation ajouté",
    ActionAudit.SUPPORT_FORMATION_RETIRE.value: "Support de formation retiré",
    # Missions et sessions
    ActionAudit.MISSION_CREEE.value: "Mission créée",
    ActionAudit.MISSION_MODIFIEE.value: "Mission modifiée",
    ActionAudit.MISSION_STATUT_CHANGE.value: "Statut de mission changé",
    # ADR 0012 : la planification crée la session ; le rattachement d'offres
    # complète une mission née avant l'approbation de toutes ses offres.
    ActionAudit.MISSION_PLANIFIEE.value: "Mission planifiée (session créée)",
    ActionAudit.MISSION_OFFRES_RATTACHEES.value: "Offres rattachées à la mission",
    ActionAudit.SESSION_PLANIFIEE.value: "Session planifiée",
    # ADR 0012 : session créée automatiquement par la planification de la
    # mission (distincte d'une session saisie à la main).
    ActionAudit.SESSION_CREEE.value: "Session créée par la planification",
    ActionAudit.SESSION_MODIFIEE.value: "Session modifiée",
    ActionAudit.SESSION_STATUT_CHANGE.value: "Statut de session changé",
    ActionAudit.FICHE_PRESENCE_GENEREE.value: "Fiche de présence générée",
    ActionAudit.PRESENCE_ENREGISTREE.value: "Présence enregistrée",
    ActionAudit.PARTICIPATION_INSCRITE.value: "Participant inscrit",
    ActionAudit.PARTICIPATION_PRESENCE_POINTEE.value: "Présence pointée (ancien format)",
    ActionAudit.PARTICIPATION_MODIFIEE.value: "Participation modifiée",
    ActionAudit.PARTICIPATION_RETIREE.value: "Participant retiré",
    # Organisations, intervenants, lots, lieux, bénéficiaires
    ActionAudit.ORGANISATION_ENREGISTREE.value: "Organisation enregistrée",
    ActionAudit.ORGANISATION_MODIFIEE.value: "Organisation modifiée",
    ActionAudit.EQUIPE_ENREGISTREE.value: "Intervenant enregistré",
    ActionAudit.EQUIPE_MODIFIEE.value: "Intervenant modifié",
    ActionAudit.EQUIPE_ARCHIVEE.value: "Intervenant archivé",
    ActionAudit.EQUIPE_REACTIVEE.value: "Intervenant réactivé",
    ActionAudit.LOT_ENREGISTRE.value: "Lot enregistré",
    ActionAudit.LOT_MODIFIE.value: "Lot modifié",
    ActionAudit.LIEU_ENREGISTRE.value: "Lieu d'exécution enregistré",
    ActionAudit.LIEU_MIS_A_JOUR.value: "Lieu d'exécution mis à jour",
    ActionAudit.LIEU_POSITION_MODIFIEE.value: "Position d'un lieu ajustée",
    ActionAudit.ITINERAIRE_CALCULE.value: "Itinéraire calculé",
    ActionAudit.PARAMETRES_VOYAGE_MODIFIES.value: "Paramètres de voyage modifiés",
    ActionAudit.BENEFICIAIRE_ENREGISTRE.value: "Bénéficiaire enregistré",
    ActionAudit.BENEFICIAIRE_MODIFIE.value: "Bénéficiaire modifié",
    ActionAudit.BENEFICIAIRE_ARCHIVE.value: "Bénéficiaire archivé",
    ActionAudit.BENEFICIAIRE_REACTIVE.value: "Bénéficiaire réactivé",
    ActionAudit.BENEFICIAIRES_IMPORTES.value: "Bénéficiaires importés",
    # Comptes
    ActionAudit.UTILISATEUR_INSCRIT.value: "Compte inscrit",
    ActionAudit.UTILISATEUR_ACTIVE.value: "Compte activé",
    ActionAudit.UTILISATEUR_REFUSE.value: "Compte refusé",
    ActionAudit.UTILISATEUR_SUSPENDU.value: "Compte suspendu",
    ActionAudit.UTILISATEUR_REACTIVE.value: "Compte réactivé",
    ActionAudit.UTILISATEUR_SUPPRIME.value: "Compte supprimé",
    ActionAudit.UTILISATEUR_MODIFIE.value: "Fiche du compte modifiée",
    ActionAudit.UTILISATEUR_MOT_DE_PASSE_REINITIALISE.value: (
        "Mot de passe réinitialisé"
    ),
    ActionAudit.UTILISATEUR_MOT_DE_PASSE_CHANGE.value: "Mot de passe changé par le compte",
    ActionAudit.UTILISATEUR_MOT_DE_PASSE_REINITIALISATION_DEMANDEE.value: (
        "Code de réinitialisation envoyé"
    ),
    # Envois d'email (Agent RH)
    ActionAudit.EMAIL_ENVOYE.value: "Email envoyé par un agent",
    # Administration des agents et des connecteurs
    ActionAudit.CONFIGURATION_AGENT_MODIFIEE.value: "Configuration d'agent modifiée",
    ActionAudit.CONFIGURATION_AGENT_RECHARGEE.value: "Configuration d'agent rechargée",
    ActionAudit.SURCHARGE_AGENT_MODIFIEE.value: "Réglage d'agent modifié",
    ActionAudit.CONFIGURATION_AGENT_PERSONNEL_MODIFIEE.value: (
        "Configuration de l'agent personnel modifiée"
    ),
    ActionAudit.SKILL_AGENT_ENREGISTRE.value: "Compétence d'agent enregistrée",
    ActionAudit.SKILL_CREE.value: "Skill d'organisation créé",
    ActionAudit.SKILL_MODIFIE.value: "Skill d'organisation modifié",
    ActionAudit.SKILL_SUPPRIME.value: "Skill d'organisation supprimé",
    ActionAudit.SERVEUR_MCP_ENREGISTRE.value: "Serveur MCP enregistré",
    ActionAudit.SERVEUR_MCP_MODIFIE.value: "Serveur MCP modifié",
    ActionAudit.SERVEUR_MCP_SUPPRIME.value: "Serveur MCP supprimé",
    ActionAudit.CHAT_ORPHELINS_PURGES.value: "Fichiers de conversation purgés",
    # Suppression générique : le libellé dépend de l'entité (voir _ENTITES).
    ActionAudit.SUPPRESSION_EFFECTUEE.value: "Suppression",
}

#: Version de la clé → libellé, pour les valeurs **héritées** encore présentes
#: dans les journaux. D'anciennes versions écrivaient le nom d'un type de
#: proposition (``publication_offre``, ``affectation_equipe``) à la place d'une
#: action, et ``chat_orphelins.purges`` n'est plus un membre d'``ActionAudit``.
#: Ces lignes existent : leur libellé est écrit ici plutôt que dérivé, sans quoi
#: elles réapparaîtraient en « Publication Offre » dans l'historique d'une offre.
LIBELLES_HISTORIQUES: dict[str, str] = {
    "affectation_equipe": "Proposition d'affectation",
    "document_officiel": "Document officiel approuvé",
    "extraction_appel_a_proposition": "Extraction d'appel proposée",
    "publication_offre": "Offre publiée",
    "publication_offre_formation": "Offre de formation publiée",
    "chat_orphelins.purges": "Fichiers de conversation purgés",
}

#: Repli d'une entité inconnue (journal plus ancien qu'une entité ajoutée plus
#: tard) : jamais la clé technique, jamais une case vide.
_ENTITE_INCONNUE = "d'un élément"


def libelle_action(action: str, entity_type: str | None = None) -> str:
    """Nom affiché d'une action d'audit, entité comprise quand elle compte.

    Args:
        action: valeur du vocabulaire ``ActionAudit`` (``"offre.publiee"``).
        entity_type: type d'entité de l'événement, utilisé seulement pour les
            actions génériques (``suppression.effectuee``).

    Returns:
        Un libellé français lisible — « Offre publiée », « Suppression d'une
        offre ». Une action inconnue (vocabulaire étendu sans libellé) n'est
        jamais masquée : sa clé est rendue lisible plutôt que perdue.
    """
    base = LIBELLES_ACTION.get(action) or LIBELLES_HISTORIQUES.get(action)
    if base is None:
        return _rendre_lisible(action)
    if action not in ACTIONS_PAR_ENTITE:
        return base
    return f"{base} {ENTITES_SUPPRIMEES.get(entity_type or '', _ENTITE_INCONNUE)}"


def _rendre_lisible(action: str) -> str:
    """Repli : « domaine.nom_technique » → « Nom technique » (jamais masqué)."""
    _, _, nom = action.partition(".")
    texte = (nom or action).replace("_", " ").strip()
    return texte[:1].upper() + texte[1:] if texte else action
