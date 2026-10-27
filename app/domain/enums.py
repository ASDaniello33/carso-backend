"""Statuses and type vocabularies as Python enums (instruction/04 §4: "enums contrôlés").

PROVISOIRE [P] : les intitulés exacts (Phase 2 §3) restent à confirmer par CARSO.
C'est pourquoi ils sont stockés en base comme ``String`` (pas de types ENUM
natifs PostgreSQL) : faire évoluer un vocabulaire ne coûte alors aucune
migration ``ALTER TYPE``. Les enums ci-dessous sont la source de vérité Python.
"""

from enum import StrEnum


class TypeAppel(StrEnum):
    """Nature de l'appel reçu (règle métier confirmée avec CARSO).

    Un **Appel** est soit un appel à proposition (réponse en offre technique +
    financière), soit un appel à manifestation d'intérêt (réponse en offre de
    type ``AUTRE``), soit un autre type d'appel reçu (réponse en offre ``AUTRE``
    jusqu'à ce que CARSO confirme un traitement dédié). Le type n'est jamais
    déduit d'un autre champ.
    """

    APPEL_A_PROPOSITION = "appel_a_proposition"
    APPEL_A_MANIFESTATION_INTERET = "appel_a_manifestation_interet"
    AUTRE = "autre"


class StatutAppel(StrEnum):
    """Phase 2 §3 : recu → en_analyse → propose → corrige → valide (→ rejete)."""

    RECU = "recu"
    EN_ANALYSE = "en_analyse"
    PROPOSE = "propose"
    CORRIGE = "corrige"
    VALIDE = "valide"
    REJETE = "rejete"


class StatutOffre(StrEnum):
    """Phase 2 §3 : brouillon → en_revue → approuve → archive."""

    BROUILLON = "brouillon"
    EN_REVUE = "en_revue"
    APPROUVE = "approuve"
    ARCHIVE = "archive"


class TypeOffre(StrEnum):
    """Type d'une offre répondant à ``Appel + Lot`` (règle confirmée CARSO).

    Une offre répond toujours à un appel **et** un lot. Un appel à proposition
    se répond par une ``OFFRE_TECHNIQUE`` (méthodologie, activités, planning,
    équipe, moyens, livrables) **et** une ``OFFRE_FINANCIERE`` (budget, coûts,
    lignes budgétaires, hypothèses). ``AUTRE`` sert lorsque l'appel n'est pas un
    appel à proposition — notamment une manifestation d'intérêt.
    """

    OFFRE_TECHNIQUE = "offre_technique"
    OFFRE_FINANCIERE = "offre_financiere"
    AUTRE = "autre"


class RoleMission(StrEnum):
    """Rôles d'une personne **dans le contexte d'une mission** (règle CARSO).

    Le rôle est porté par l'affectation, jamais par la personne. Règle métier
    confirmée : **un Chef de Mission est également un Formateur** — la relation
    est portée par :func:`roles_impliquant_formateur`, jamais par un doublon de
    rôle contradictoire sur la même affectation.
    """

    FORMATEUR = "formateur"
    ASSISTANCE_LOGISTIQUE = "assistance_logistique"
    CHEF_DE_MISSION = "chef_de_mission"
    ACCOMPAGNATEUR = "accompagnateur"
    #: Rôle confirmé au catalogue CARSO ; l'implication « formateur » reste
    #: À CONFIRMER — ce rôle n'est donc PAS présumé formateur (pas d'invention).
    COACH_FORMATEUR = "coach_formateur"


#: Rôles qui impliquent aussi la qualité de Formateur (règle confirmée :
#: un Chef de Mission est également un Formateur). ``COACH_FORMATEUR`` est
#: volontairement absent tant que CARSO ne l'a pas confirmé.
ROLES_IMPLIQUANT_FORMATEUR: frozenset[RoleMission] = frozenset(
    {RoleMission.CHEF_DE_MISSION}
)


def est_formateur(role: str | None) -> bool:
    """Vrai si le rôle vaut Formateur, seul ou par implication métier.

    Args:
        role: valeur brute de ``AffectationEquipe.role_dans_mission``.

    Returns:
        ``True`` pour ``FORMATEUR`` et pour tout rôle listé dans
        :data:`ROLES_IMPLIQUANT_FORMATEUR` (aujourd'hui : Chef de Mission).
        Une valeur inconnue est ``False`` — on ne présume jamais un rôle.
    """
    if role is None:
        return False
    if role == RoleMission.FORMATEUR.value:
        return True
    try:
        return RoleMission(role) in ROLES_IMPLIQUANT_FORMATEUR
    except ValueError:
        return False


def est_role_mission_valide(role: str | None) -> bool:
    """Vrai si ``role`` appartient au vocabulaire confirmé ``RoleMission``."""
    if role is None:
        return False
    try:
        RoleMission(role)
    except ValueError:
        return False
    return True


class StatutMission(StrEnum):
    """Cycle de vie d'une mission (ADR 0012, validé avec CARSO le 26/10).

    ``en_planification`` → ``planifie`` : action humaine « Planifier », qui
    **crée la session** de la mission. ``en_cours`` et ``cloture`` sont
    appliqués automatiquement par le balayage de cycle de vie, jamais saisis.
    ``annule`` est une décision humaine de l'équipe interne ; elle annule aussi
    la session.
    """

    EN_PLANIFICATION = "en_planification"
    PLANIFIE = "planifie"
    EN_COURS = "en_cours"
    CLOTURE = "cloture"
    ANNULE = "annule"


class StatutSessionFormation(StrEnum):
    """Cycle de vie d'une session (ADR 0012).

    La session naît ``planifie`` quand c'est la planification de la mission qui
    la crée. ``en_planification`` ne sert qu'à une session créée à la main, que
    l'équipe interne confirme ensuite. ``en_cours`` (jour J) et ``cloture``
    (fin dépassée) sont automatiques ; ``annule`` annule aussi la mission.
    """

    EN_PLANIFICATION = "en_planification"
    PLANIFIE = "planifie"
    EN_COURS = "en_cours"
    CLOTURE = "cloture"
    ANNULE = "annule"


class StatutEquipe(StrEnum):
    """État d'un membre du vivier (décision utilisateur 20/09).

    Le champ ``equipes.statut`` existait déjà en texte libre sans vocabulaire.
    L'archivage est **logique** : la personne sort des listes actives, son
    historique (affectations, CV, présences) reste intact — c'est pourquoi il
    n'existe aucune route de suppression définitive.
    """

    ACTIF = "actif"
    ARCHIVE = "archive"


class StatutBeneficiaire(StrEnum):
    """Même décision d'archivage logique que :class:`StatutEquipe`."""

    ACTIF = "actif"
    ARCHIVE = "archive"


class StatutPresence(StrEnum):
    """Pointage d'un bénéficiaire à une session (décision utilisateur 20/09).

    La colonne reste un ``String`` : le vocabulaire des présences demeure [?]
    côté CARSO, on ne fige donc aucun type ENUM PostgreSQL.
    """

    PRESENT = "present"
    ABSENT = "absent"
    RETARD = "retard"
    EXCUSE = "excuse"


class StatutDocument(StrEnum):
    """Phase 2 §3 : draft → proposed → approved → archive.

    ``SUPPRIME`` est l'état d'une fiche placée en **corbeille** (règle validée
    23/09) : la fiche documentaire ne disparaît pas, seul son fichier quitte le
    stockage. Elle reste donc lisible — et l'audit garde qui, quand et pourquoi.

    Cet état n'est plus un cul-de-sac : une fiche se **restaure** dans l'état
    qu'elle avait avant sa suppression (ADR 0006), et se **purge** définitivement
    (administrateur, fiche encore référencée refusée) lorsque sa fenêtre de
    conservation est écoulée. Les deux opérations sont tracées.
    """

    DRAFT = "draft"
    PROPOSED = "proposed"
    APPROVED = "approved"
    ARCHIVED = "archived"
    SUPPRIME = "supprime"
    # Documents de **session** (demande 30/09) : une fiche de présence, une
    # checklist ou un rapport de session n'est pas une offre — le cycle
    # offre (draft/proposed) ne leur convient pas. Un document de session est
    # soit **généré** (par l'assistant formateur ou l'outil de génération),
    # soit **importé** (déposé manuellement par le formateur). Les deux
    # restent supprimables/archivables et peuvent être approuvés.
    #
    # **CV et documents d'appel** (règle CARSO 26/10) : pas non plus de cycle
    # de décision — un CV ou une pièce d'appel déposée par un humain naît
    # ``importe``, puis devient ``archived`` ou ``supprime`` selon son cycle
    # de vie, jamais ``draft``. Le dépôt d'un **agent** reste ``proposed`` :
    # la règle 6 [C] n'est pas contournée.
    GENERE = "genere"
    IMPORTE = "importe"


class StatutBudget(StrEnum):
    """Phase 2 §3 : brouillon → propose → approuve."""

    BROUILLON = "brouillon"
    PROPOSE = "propose"
    APPROUVE = "approuve"


class SourceAffectation(StrEnum):
    """Origine d'une affectation (instruction/04 : manual, ai_proposal, etc.)."""

    MANUAL = "manual"
    AI_PROPOSAL = "ai_proposal"


class DecisionApprobation(StrEnum):
    """Décision humaine sur une proposition (Phase 2, Approbation [C])."""

    APPROUVE = "approuve"
    REJETE = "rejete"


class TypeDocument(StrEnum):
    """Types initiaux confirmés (Phase 2 §1, Document) — vocabulaire final [?]."""

    APPEL_PROPOSITION = "appel_proposition"
    # Valeur historique : documents déjà typés ``appel_offre`` restent valides.
    APPEL_OFFRE = "appel_offre"
    OFFRE = "offre"
    # Valeur historique : documents déjà typés ``offre_formation`` restent
    # lisibles (lecture seule) ; les nouveaux dépôts utilisent ``OFFRE``.
    OFFRE_FORMATION = "offre_formation"
    CV = "cv"
    FICHE_TECHNIQUE = "fiche_technique"
    FICHE_PRESENCE = "fiche_presence"
    LISTE_BENEFICIAIRES = "liste_beneficiaires"
    CHECKLIST = "checklist"
    RAPPORT = "rapport"
    JUSTIFICATIF = "justificatif"
    BUDGET = "budget"
    TEMPLATE = "template"
    AUTRE = "autre"
    # Document sans objet métier (génération ou dépôt hors fiche) :
    # rangé sous ``generated/{id}/``, aucune FK.
    NON_CLASSE = "non_classe"


class ActorType(StrEnum):
    """Type d'acteur d'un AuditEvent."""

    HUMAIN = "humain"
    AGENT = "agent"
    SYSTEME = "systeme"


class StatutAgentTask(StrEnum):
    """Phase 2 §3 : pending → running → completed | failed | timeout."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMEOUT = "timeout"


class StatutAffectation(StrEnum):
    """Cycle de validation d'une affectation (Phase 2, événements
    AffectationProposee / Validee). Le vocabulaire des rôles lui-même reste [P] Q5.
    """

    PROPOSEE = "proposee"
    APPROUVEE = "approuvee"
    REFUSEE = "refusee"


class TypeProposition(StrEnum):
    """proposal_type d'une Approbation (Phase 2 §1, règle 6)."""

    EXTRACTION_APPEL_A_PROPOSITION = "extraction_appel_a_proposition"
    PUBLICATION_OFFRE = "publication_offre"
    # Valeur historique conservée : les Approbations déjà écrites référencent
    # ``publication_offre_formation`` et restent lisibles.
    PUBLICATION_OFFRE_FORMATION = "publication_offre_formation"
    AFFECTATION_EQUIPE = "affectation_equipe"
    BUDGET = "budget"
    DOCUMENT_OFFICIEL = "document_officiel"
    REMPLACEMENT_DOCUMENT = "remplacement_document"


class RoleUtilisateur(StrEnum):
    """Rôles de compte (AUTH). Distincts des rôles de mission (Q5).

    ``FORMATEUR`` : compte restreint — l'utilisateur n'accède qu'aux missions
    et sessions où il est affecté (la liste de ses affectations décide ; la
    page de validation lui est fermée comme toute page d'administration).
    """

    EN_ATTENTE = "en_attente"
    COLLABORATEUR = "collaborateur"
    FORMATEUR = "formateur"
    ADMINISTRATEUR = "administrateur"


class TypeConversation(StrEnum):
    """Chat social : directe (2 membres) ou conférence (groupe temporaire)."""

    DIRECTE = "directe"
    CONFERENCE = "conference"


class RoleMembreConversation(StrEnum):
    """Rôle dans une conversation : l'animateur gère les membres / la clôture."""

    MEMBRE = "membre"
    ANIMATEUR = "animateur"


class TypeCibleReaction(StrEnum):
    """Cible polymorphe d'une réaction emoji du chat social."""

    MESSAGE = "message"
    ANNONCE = "annonce"
    COMMENTAIRE = "commentaire"


class TypeAuteurMessage(StrEnum):
    """Auteur d'un message social : un compte humain ou un agent invoqué."""

    HUMAIN = "humain"
    AGENT = "agent"


class StatutUtilisateur(StrEnum):
    """pending → actif → suspendu (validation admin obligatoire)."""

    PENDING = "pending"
    ACTIF = "actif"
    SUSPENDU = "suspendu"


class ActionAudit(StrEnum):
    """Actions tracées dans AuditEvent (Phase 2 §4). Pas de magic strings."""

    APPEL_A_PROPOSITION_ENREGISTRE = "appel_a_proposition.enregistre"
    APPEL_A_PROPOSITION_MODIFIE = "appel_a_proposition.modifie"
    EXTRACTION_PROPOSEE = "appel_a_proposition.extraction_proposee"
    EXTRACTION_VALIDEE = "appel_a_proposition.extraction_validee"
    EXTRACTION_REJETEE = "appel_a_proposition.extraction_rejetee"
    LOTS_MATERIALISES = "appel_a_proposition.lots_materialises"
    OFFRE_CREEE = "offre.brouillon_creee"
    OFFRE_SOUMISE = "offre.soumise_revue"
    OFFRE_PUBLIEE = "offre.publiee"
    OFFRE_ARCHIVEE = "offre.archivee"
    AFFECTATION_PROPOSEE = "affectation.proposee"
    AFFECTATION_VALIDEE = "affectation.validee"
    AFFECTATION_REFUSEE = "affectation.refusee"
    AFFECTATION_MODIFIEE = "affectation.modifiee"
    #: Annulation d'une proposition **jamais décidée** (retrait tracé).
    #: Distincte du refus : un refus est une décision conservee, une annulation
    #: retire une proposition sans effet de la liste d'attente.
    AFFECTATION_ANNULEE = "affectation.annulee"
    BUDGET_APPROUVE = "budget.approuve"
    DOCUMENT_ENREGISTRE = "document.enregistre"
    DOCUMENT_SOUMIS = "document.soumis"
    DOCUMENT_APPROUVE = "document.approuve"
    DOCUMENT_REMPLACE = "document.remplace"
    DOCUMENT_RATTACHE = "document.rattache"
    # Lot L0 (instruction/06 §6) — missions et sessions : actions humaines
    # traçables (la création n'est pas une proposition d'agent, règle 6 [C]).
    MISSION_CREEE = "mission.creee"
    MISSION_MODIFIEE = "mission.modifiee"
    MISSION_STATUT_CHANGE = "mission.statut_change"
    #: Passage explicite à « Planifié » : crée la session dans la même
    #: transaction (ADR 0012). Distinct d'un simple changement de statut.
    MISSION_PLANIFIEE = "mission.planifiee"
    #: Rattachement / retrait d'offres approuvées du même lot (mission née
    #: avant l'approbation de toutes ses offres).
    MISSION_OFFRES_RATTACHEES = "mission.offres_rattachees"
    SESSION_PLANIFIEE = "session.planifiee"
    SESSION_STATUT_CHANGE = "session.statut_change"
    #: Session créée automatiquement par la planification d'une mission.
    SESSION_CREEE = "session.creee"
    # Archivage : décision humaine tracée sans Approbation (ce n'est pas une
    # approbation de proposition) et sans destruction de fichier.
    DOCUMENT_ARCHIVE = "document.archive"
    # Suppression (rule validée 18/09) : l'utilisateur peut retirer une version
    # dont il n'a plus besoin — version non officielle uniquement, tracée,
    # fichier physique conservé (rétention CARSO non confirmée).
    DOCUMENT_SUPPRIME = "document.supprime"
    # Corbeille (ADR 0006). Restauration : la fiche revient en service dans son
    # état d'avant suppression (le fichier est redéposé s'il a quitté le disque).
    # Purge : la fiche disparaît de la base — réservée aux administrateurs, motif
    # obligatoire, refusée tant qu'un objet la référence.
    DOCUMENT_RESTAURE = "document.restaure"
    DOCUMENT_PURGE = "document.purge"
    # Annulation d'un dépôt (pièce jointe retirée avant tout envoi) : le fichier
    # et sa ligne partent ensemble. Sans objet métier, la fiche n'entre pas en
    # corbeille — elle n'a jamais servi.
    DOCUMENT_DEPOT_ANNULE = "document.depot_annule"
    MODELE_DOCUMENT_ENREGISTRE = "modele_document.enregistre"
    BUDGET_LIGNE_AJOUTEE = "budget.ligne_ajoutee"
    BUDGET_SOUMIS = "budget.soumis"
    BUDGET_REPRIS = "budget.repris"
    # Lot C1 — CRUD des entités jusqu'ici sans service (instruction/02).
    # Créations/modifications humaines tracées (règle 10 : mutation audité) ;
    # ce ne sont pas des propositions d'agent, pas d'Approbation.
    ORGANISATION_ENREGISTREE = "organisation.enregistree"
    ORGANISATION_MODIFIEE = "organisation.modifiee"
    EQUIPE_ENREGISTREE = "equipe.enregistree"
    EQUIPE_MODIFIEE = "equipe.modifiee"
    # Archivage logique (20/09) : réversible, aucune donnée détruite.
    EQUIPE_ARCHIVEE = "equipe.archivee"
    EQUIPE_REACTIVEE = "equipe.reactivee"
    LOT_ENREGISTRE = "lot.enregistre"
    LOT_MODIFIE = "lot.modifie"
    # Lieu d'exécution d'une mission (donnée distincte, règle confirmée CARSO).
    LIEU_ENREGISTRE = "lieu.enregistre"
    LIEU_MIS_A_JOUR = "lieu.mis_a_jour"
    LIEU_POSITION_MODIFIEE = "lieu.position_modifiee"
    ITINERAIRE_CALCULE = "itineraire.calcule"
    PARAMETRES_VOYAGE_MODIFIES = "parametres_voyage.modifies"
    # Support de formation : relation Formateur ↔ Mission ↔ Document.
    SUPPORT_FORMATION_AJOUTE = "support_formation.ajoute"
    SUPPORT_FORMATION_RETIRE = "support_formation.retire"
    BENEFICIAIRE_ENREGISTRE = "beneficiaire.enregistre"
    BENEFICIAIRE_MODIFIE = "beneficiaire.modifie"
    BENEFICIAIRE_ARCHIVE = "beneficiaire.archive"
    BENEFICIAIRE_REACTIVE = "beneficiaire.reactive"
    # Import Excel d'une session : geste humain confirmé (ou agent sous HITL),
    # même service applicatif dans les deux cas.
    BENEFICIAIRES_IMPORTES = "beneficiaire.importes"
    FICHE_PRESENCE_GENEREE = "session.fiche_presence_generee"
    # Pointage **par date** (règle confirmée CARSO, 24/09) : une ligne par
    # ``(participation, date)``. ``PARTICIPATION_PRESENCE_POINTEE`` reste pour
    # les événements déjà écrits avant ce changement — on ne réécrit pas l'audit.
    PRESENCE_ENREGISTREE = "presence.enregistree"
    PARTICIPATION_INSCRITE = "participation.inscrite"
    PARTICIPATION_PRESENCE_POINTEE = "participation.presence_pointee"
    PARTICIPATION_MODIFIEE = "participation.modifiee"
    UTILISATEUR_INSCRIT = "utilisateur.inscrit"
    UTILISATEUR_ACTIVE = "utilisateur.active"
    UTILISATEUR_REFUSE = "utilisateur.refuse"
    UTILISATEUR_SUSPENDU = "utilisateur.suspendu"
    UTILISATEUR_REACTIVE = "utilisateur.reactive"
    # Gestion des comptes (page Utilisateurs, administrateur) : suppression et
    # réinitialisation de mot de passe. Comme pour le refus, la suppression
    # trace l'identité du compte (la fiche disparaît) ; la réinitialisation ne
    # porte jamais la valeur du mot de passe ni son hash (AGENTS.md §9).
    UTILISATEUR_SUPPRIME = "utilisateur.supprime"
    UTILISATEUR_MOT_DE_PASSE_REINITIALISE = "utilisateur.mot_de_passe_reinitialise"
    # Profil personnel (page Profil) : modification d'identité et changement de
    # mot de passe. L'événement ne contient **jamais** de mot de passe ni de
    # hash (AGENTS.md §9) — seulement l'auteur et les champs modifiés.
    UTILISATEUR_MODIFIE = "utilisateur.modifie"
    UTILISATEUR_MOT_DE_PASSE_CHANGE = "utilisateur.mot_de_passe_change"
    # Mot de passe oublié : demande d'un code à usage unique envoyé par email.
    # Le code lui-même n'entre jamais dans l'événement (AGENTS.md §9).
    UTILISATEUR_MOT_DE_PASSE_REINITIALISATION_DEMANDEE = (
        "utilisateur.mot_de_passe_reinitialisation_demandee"
    )
    # Configuration runtime des agents (modèle/provider) : décision
    # d'administration tracée. Ce n'est pas une proposition d'agent.
    CONFIGURATION_AGENT_MODIFIEE = "configuration_agent.modifiee"
    CONFIGURATION_AGENT_RECHARGEE = "configuration_agent.rechargee"
    # Modification et suppression (règle confirmée CARSO, 22/09). La suppression
    # emporte le sous-arbre : l'événement conserve l'INVENTAIRE de ce qui a
    # disparu (auteur, motif, comptages), jamais les données elles-mêmes.
    OFFRE_MODIFIEE = "offre.modifiee"
    SESSION_MODIFIEE = "session.modifiee"
    PARTICIPATION_RETIREE = "participation.retiree"
    DOCUMENT_METADONNEES_MODIFIEES = "document.metadonnees_modifiees"
    #: Bascule privé ↔ public d'un document personnel (ADR 0013). Réservée au
    #: propriétaire et à l'administration ; n'a de sens que sans ancre métier.
    DOCUMENT_VISIBILITE_MODIFIEE = "document.visibilite_modifiee"
    SUPPRESSION_EFFECTUEE = "suppression.effectuee"
    SURCHARGE_AGENT_MODIFIEE = "surcharge_agent.modifiee"
    # Agent personnel (10/10) : un compte règle **son** agent — instructions
    # personnelles et outils retirés. Ce n'est pas une surcharge d'agent
    # (réglage d'administration, une ligne par agent, appliqué à tous) :
    # l'événement porte le compte concerné et n'affecte aucun autre graphe.
    CONFIGURATION_AGENT_PERSONNEL_MODIFIEE = "agent_personnel.configuration_modifiee"
    SKILL_AGENT_ENREGISTRE = "skill_agent.enregistre"
    # Skill d'organisation : l'administration écrit un guide court, nommé
    # `/nom` dans le chat et porté par le prompt de **tous** les runs. Distinct
    # de ``SKILL_AGENT_ENREGISTRE`` (rattacher un skill du paquet à un agent) :
    # ici, le contenu est commun et change le comportement des agents.
    # Les événements antérieurs gardent leur valeur ``skill_personnel.*`` — un
    # journal d'audit ne se réécrit pas.
    SKILL_CREE = "skill.cree"
    SKILL_MODIFIE = "skill.modifie"
    SKILL_SUPPRIME = "skill.supprime"
    # Envoi d'email par un agent (RH, 12/10) : l'événement dit **qui** a reçu
    # quoi (destinataire résolu, sujet, taille), jamais le corps du message —
    # un email peut porter des données personnelles (AGENTS.md §9).
    EMAIL_ENVOYE = "email.envoye"
    SERVEUR_MCP_ENREGISTRE = "serveur_mcp.enregistre"
    SERVEUR_MCP_MODIFIE = "serveur_mcp.modifie"
    SERVEUR_MCP_SUPPRIME = "serveur_mcp.supprime"
    # Purge validée des orphelins du scope chat (incrément 34) : geste
    # d'administration, motif obligatoire, inventaire before conservé — jamais
    # une destruction silencieuse de fichiers.
    CHAT_ORPHELINS_PURGES = "chat_orphelins.purges"
