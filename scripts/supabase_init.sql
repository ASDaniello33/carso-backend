-- ===========================================================================
-- CARSO — Schéma PostgreSQL complet pour Supabase
-- ---------------------------------------------------------------------------
-- OÙ     : Supabase Dashboard → SQL Editor → New query → coller CE FICHIER
--        entier → Run. (Rien d'autre à exécuter : les 22 migrations sont
--        déjà repliées dans ce script.)
-- QUAND   : sur un projet Supabase neuf (schéma public vide). Le script n'est
--        PAS idempotent : le re-exécuter échoue sur les CREATE TABLE déjà
--        présents. Utiliser un schéma vierge ou repartir d'une base neuve.
-- CE QU'IL CRÉE : 36 tables (dont alembic_version = 0022), 48 contraintes
--        étrangères, 89 index, + les ALTER/RENAME de la chaîne 0001 → 0022.
-- APRÈS L'EXÉCUTION (2e étape, hors SQL) : premier administrateur =
--        BOOTSTRAP_ADMIN_EMAIL / BOOTSTRAP_ADMIN_PASSWORD dans l'environnement
--        du backend (mdp ≥ 8 caractères) puis redémarrage — création idempotante
--        documentée dans docs/api/AUTH.md. Aucun INSERT de données métier n'est
--        fourni ici : les migrations n'en créent aucune et le projet interdit
--        d'inventer des données de référence.
-- RÉGÉNÉRER APRÈS UNE NOUVELLE MIGRATION :
--        cd backend && PYTHONUTF8=1 python -m alembic upgrade head --sql \
--          > scripts/supabase_init.sql
-- VÉRIFICATION : exécuté sans erreur (ON_ERROR_STOP) sur PostgreSQL 18 vide,
--        puis schéma introspecté et comparé aux modèles SQLAlchemy le
--        17/10/2026 : aucune table ni colonne manquante, head = 0022.
-- ===========================================================================

BEGIN;

CREATE TABLE alembic_version (
    version_num VARCHAR(32) NOT NULL, 
    CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num)
);

-- Running upgrade  -> 0001

INSERT INTO alembic_version (version_num) VALUES ('0001') RETURNING alembic_version.version_num;

-- Running upgrade 0001 -> 0002

CREATE TABLE organisations (
    id UUID DEFAULT gen_random_uuid() NOT NULL, 
    nom VARCHAR(255) NOT NULL, 
    type VARCHAR(100), 
    adresse TEXT, 
    email VARCHAR(255), 
    telephone VARCHAR(50), 
    statut VARCHAR(50), 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_organisations PRIMARY KEY (id)
);

CREATE TABLE equipes (
    id UUID DEFAULT gen_random_uuid() NOT NULL, 
    nom VARCHAR(100) NOT NULL, 
    prenom VARCHAR(100) NOT NULL, 
    email VARCHAR(255), 
    telephone VARCHAR(50), 
    profil TEXT, 
    statut VARCHAR(50), 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_equipes PRIMARY KEY (id)
);

CREATE TABLE beneficiaires (
    id UUID DEFAULT gen_random_uuid() NOT NULL, 
    nom VARCHAR(100) NOT NULL, 
    prenom VARCHAR(100) NOT NULL, 
    contact VARCHAR(255), 
    organisation_origine VARCHAR(255), 
    identifiant_externe VARCHAR(100), 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_beneficiaires PRIMARY KEY (id)
);

CREATE TABLE appels_offre (
    id UUID DEFAULT gen_random_uuid() NOT NULL, 
    organisation_id UUID NOT NULL, 
    reference VARCHAR(100) NOT NULL, 
    titre VARCHAR(255) NOT NULL, 
    description TEXT, 
    date_reception DATE, 
    date_limite DATE, 
    statut VARCHAR(50) DEFAULT 'recu' NOT NULL, 
    donnees_extraites JSONB, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_appels_offre PRIMARY KEY (id), 
    CONSTRAINT fk_appels_offre_organisation_id_organisations FOREIGN KEY(organisation_id) REFERENCES organisations (id) ON DELETE RESTRICT, 
    CONSTRAINT uq_appels_offre_reference UNIQUE (reference)
);

CREATE INDEX ix_appels_offre_organisation_id ON appels_offre (organisation_id);

CREATE INDEX ix_appels_offre_statut ON appels_offre (statut);

CREATE TABLE lots (
    id UUID DEFAULT gen_random_uuid() NOT NULL, 
    appel_offre_id UUID NOT NULL, 
    numero VARCHAR(50) NOT NULL, 
    titre VARCHAR(255) NOT NULL, 
    zone VARCHAR(255), 
    objectifs TEXT, 
    resultats_attendus TEXT, 
    mission_description TEXT, 
    partenariat TEXT, 
    date_fin DATE, 
    donnees_source JSONB, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_lots PRIMARY KEY (id), 
    CONSTRAINT fk_lots_appel_offre_id_appels_offre FOREIGN KEY(appel_offre_id) REFERENCES appels_offre (id) ON DELETE CASCADE, 
    CONSTRAINT uq_lots_appel_offre_id_numero UNIQUE (appel_offre_id, numero)
);

CREATE INDEX ix_lots_appel_offre_id ON lots (appel_offre_id);

CREATE TABLE offres_formation (
    id UUID DEFAULT gen_random_uuid() NOT NULL, 
    organisation_id UUID NOT NULL, 
    lot_id UUID NOT NULL, 
    reference VARCHAR(100) NOT NULL, 
    titre VARCHAR(255) NOT NULL, 
    date_debut_prevue DATE, 
    date_fin_prevue DATE, 
    statut VARCHAR(50) DEFAULT 'brouillon' NOT NULL, 
    modele_document_id UUID, 
    version INTEGER DEFAULT '1' NOT NULL, 
    approved_at TIMESTAMP WITH TIME ZONE, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_offres_formation PRIMARY KEY (id), 
    CONSTRAINT fk_offres_formation_organisation_id_organisations FOREIGN KEY(organisation_id) REFERENCES organisations (id) ON DELETE RESTRICT, 
    CONSTRAINT fk_offres_formation_lot_id_lots FOREIGN KEY(lot_id) REFERENCES lots (id) ON DELETE RESTRICT, 
    CONSTRAINT uq_offres_formation_reference UNIQUE (reference)
);

CREATE INDEX ix_offres_formation_lot_id ON offres_formation (lot_id);

CREATE INDEX ix_offres_formation_organisation_id ON offres_formation (organisation_id);

CREATE INDEX ix_offres_formation_statut ON offres_formation (statut);

CREATE TABLE missions (
    id UUID DEFAULT gen_random_uuid() NOT NULL, 
    offre_formation_id UUID, 
    organisation_id UUID NOT NULL, 
    reference VARCHAR(100) NOT NULL, 
    titre VARCHAR(255) NOT NULL, 
    description TEXT, 
    date_debut DATE, 
    date_fin DATE, 
    lieu VARCHAR(255), 
    statut VARCHAR(50) DEFAULT 'planifiee' NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_missions PRIMARY KEY (id), 
    CONSTRAINT fk_missions_offre_formation_id_offres_formation FOREIGN KEY(offre_formation_id) REFERENCES offres_formation (id) ON DELETE SET NULL, 
    CONSTRAINT fk_missions_organisation_id_organisations FOREIGN KEY(organisation_id) REFERENCES organisations (id) ON DELETE RESTRICT, 
    CONSTRAINT uq_missions_reference UNIQUE (reference)
);

CREATE INDEX ix_missions_dates ON missions (date_debut, date_fin);

CREATE INDEX ix_missions_offre_formation_id ON missions (offre_formation_id);

CREATE INDEX ix_missions_organisation_id ON missions (organisation_id);

CREATE INDEX ix_missions_statut ON missions (statut);

CREATE TABLE sessions (
    id UUID DEFAULT gen_random_uuid() NOT NULL, 
    mission_id UUID NOT NULL, 
    date_debut DATE, 
    date_fin DATE, 
    lieu VARCHAR(255), 
    theme VARCHAR(255), 
    statut VARCHAR(50) DEFAULT 'planifiee' NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_sessions PRIMARY KEY (id), 
    CONSTRAINT fk_sessions_mission_id_missions FOREIGN KEY(mission_id) REFERENCES missions (id) ON DELETE CASCADE
);

CREATE INDEX ix_sessions_dates ON sessions (date_debut, date_fin);

CREATE INDEX ix_sessions_mission_id ON sessions (mission_id);

CREATE TABLE participations (
    id UUID DEFAULT gen_random_uuid() NOT NULL, 
    session_id UUID NOT NULL, 
    beneficiaire_id UUID NOT NULL, 
    presence VARCHAR(50), 
    heure_arrivee TIME WITHOUT TIME ZONE, 
    heure_depart TIME WITHOUT TIME ZONE, 
    evaluation TEXT, 
    observations TEXT, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_participations PRIMARY KEY (id), 
    CONSTRAINT fk_participations_beneficiaire_id_beneficiaires FOREIGN KEY(beneficiaire_id) REFERENCES beneficiaires (id) ON DELETE RESTRICT, 
    CONSTRAINT fk_participations_session_id_sessions FOREIGN KEY(session_id) REFERENCES sessions (id) ON DELETE CASCADE, 
    CONSTRAINT uq_participations_session_beneficiaire UNIQUE (session_id, beneficiaire_id)
);

CREATE INDEX ix_participations_beneficiaire_id ON participations (beneficiaire_id);

CREATE INDEX ix_participations_session_id ON participations (session_id);

CREATE TABLE affectations_equipe (
    id UUID DEFAULT gen_random_uuid() NOT NULL, 
    mission_id UUID NOT NULL, 
    equipe_id UUID NOT NULL, 
    role_dans_mission VARCHAR(100) NOT NULL, 
    date_debut DATE, 
    date_fin DATE, 
    statut VARCHAR(50), 
    source_affectation VARCHAR(50) DEFAULT 'manual' NOT NULL, 
    approved_by VARCHAR(255), 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_affectations_equipe PRIMARY KEY (id), 
    CONSTRAINT fk_affectations_equipe_equipe_id_equipes FOREIGN KEY(equipe_id) REFERENCES equipes (id) ON DELETE RESTRICT, 
    CONSTRAINT fk_affectations_equipe_mission_id_missions FOREIGN KEY(mission_id) REFERENCES missions (id) ON DELETE CASCADE
);

CREATE INDEX ix_affectations_equipe_equipe_id ON affectations_equipe (equipe_id);

CREATE INDEX ix_affectations_equipe_mission_id ON affectations_equipe (mission_id);

CREATE TABLE documents (
    id UUID DEFAULT gen_random_uuid() NOT NULL, 
    nom VARCHAR(255) NOT NULL, 
    type_document VARCHAR(50) NOT NULL, 
    mime_type VARCHAR(255), 
    taille_octets BIGINT, 
    storage_path VARCHAR(1024) NOT NULL, 
    storage_disk VARCHAR(50), 
    checksum_sha256 VARCHAR(64), 
    version INTEGER DEFAULT '1' NOT NULL, 
    statut VARCHAR(50) DEFAULT 'draft' NOT NULL, 
    organisation_id UUID, 
    appel_offre_id UUID, 
    offre_formation_id UUID, 
    mission_id UUID, 
    equipe_id UUID, 
    doc_metadata JSONB, 
    created_by VARCHAR(255), 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_documents PRIMARY KEY (id), 
    CONSTRAINT fk_documents_appel_offre_id_appels_offre FOREIGN KEY(appel_offre_id) REFERENCES appels_offre (id) ON DELETE CASCADE, 
    CONSTRAINT fk_documents_equipe_id_equipes FOREIGN KEY(equipe_id) REFERENCES equipes (id) ON DELETE CASCADE, 
    CONSTRAINT fk_documents_mission_id_missions FOREIGN KEY(mission_id) REFERENCES missions (id) ON DELETE CASCADE, 
    CONSTRAINT fk_documents_offre_formation_id_offres_formation FOREIGN KEY(offre_formation_id) REFERENCES offres_formation (id) ON DELETE CASCADE, 
    CONSTRAINT fk_documents_organisation_id_organisations FOREIGN KEY(organisation_id) REFERENCES organisations (id) ON DELETE SET NULL
);

CREATE INDEX ix_documents_appel_offre_id ON documents (appel_offre_id);

CREATE INDEX ix_documents_checksum ON documents (checksum_sha256);

CREATE INDEX ix_documents_equipe_id ON documents (equipe_id);

CREATE INDEX ix_documents_mission_id ON documents (mission_id);

CREATE INDEX ix_documents_offre_formation_id ON documents (offre_formation_id);

CREATE INDEX ix_documents_organisation_id ON documents (organisation_id);

CREATE INDEX ix_documents_statut ON documents (statut);

CREATE INDEX ix_documents_type_document ON documents (type_document);

ALTER TABLE offres_formation ADD CONSTRAINT fk_offres_formation_modele_document_id_documents FOREIGN KEY(modele_document_id) REFERENCES documents (id) ON DELETE SET NULL;

CREATE TABLE budgets (
    id UUID DEFAULT gen_random_uuid() NOT NULL, 
    offre_formation_id UUID, 
    mission_id UUID, 
    devise VARCHAR(10) DEFAULT 'MAD' NOT NULL, 
    statut VARCHAR(50) DEFAULT 'brouillon' NOT NULL, 
    version INTEGER DEFAULT '1' NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_budgets PRIMARY KEY (id), 
    CONSTRAINT fk_budgets_mission_id_missions FOREIGN KEY(mission_id) REFERENCES missions (id) ON DELETE CASCADE, 
    CONSTRAINT fk_budgets_offre_formation_id_offres_formation FOREIGN KEY(offre_formation_id) REFERENCES offres_formation (id) ON DELETE CASCADE
);

CREATE INDEX ix_budgets_mission_id ON budgets (mission_id);

CREATE INDEX ix_budgets_offre_formation_id ON budgets (offre_formation_id);

CREATE TABLE lignes_budget (
    id UUID DEFAULT gen_random_uuid() NOT NULL, 
    budget_id UUID NOT NULL, 
    categorie VARCHAR(100) NOT NULL, 
    description TEXT, 
    unite VARCHAR(50), 
    quantite NUMERIC(14, 2) NOT NULL, 
    cout_unitaire NUMERIC(14, 2) NOT NULL, 
    cout_total NUMERIC(14, 2) NOT NULL, 
    ordre INTEGER, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_lignes_budget PRIMARY KEY (id), 
    CONSTRAINT fk_lignes_budget_budget_id_budgets FOREIGN KEY(budget_id) REFERENCES budgets (id) ON DELETE CASCADE
);

CREATE INDEX ix_lignes_budget_budget_id ON lignes_budget (budget_id);

CREATE TABLE modeles_documents (
    id UUID DEFAULT gen_random_uuid() NOT NULL, 
    organisation_id UUID, 
    nom VARCHAR(255) NOT NULL, 
    type_document VARCHAR(50) NOT NULL, 
    document_template_id UUID NOT NULL, 
    version INTEGER DEFAULT '1' NOT NULL, 
    statut VARCHAR(50), 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_modeles_documents PRIMARY KEY (id), 
    CONSTRAINT fk_modeles_documents_document_template_id_documents FOREIGN KEY(document_template_id) REFERENCES documents (id) ON DELETE RESTRICT, 
    CONSTRAINT fk_modeles_documents_organisation_id_organisations FOREIGN KEY(organisation_id) REFERENCES organisations (id) ON DELETE SET NULL
);

CREATE INDEX ix_modeles_documents_organisation_id ON modeles_documents (organisation_id);

CREATE TABLE approbations (
    id UUID DEFAULT gen_random_uuid() NOT NULL, 
    entity_type VARCHAR(100) NOT NULL, 
    entity_id UUID NOT NULL, 
    proposal_type VARCHAR(100) NOT NULL, 
    proposed_by_agent VARCHAR(100), 
    decision VARCHAR(50) NOT NULL, 
    decided_by VARCHAR(255), 
    decision_reason TEXT, 
    decided_at TIMESTAMP WITH TIME ZONE, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_approbations PRIMARY KEY (id)
);

CREATE INDEX ix_approbations_decision ON approbations (decision);

CREATE INDEX ix_approbations_entity ON approbations (entity_type, entity_id);

CREATE TABLE audit_events (
    id UUID DEFAULT gen_random_uuid() NOT NULL, 
    actor_type VARCHAR(50) NOT NULL, 
    actor_id VARCHAR(255), 
    action VARCHAR(100) NOT NULL, 
    entity_type VARCHAR(100) NOT NULL, 
    entity_id VARCHAR(255), 
    before JSONB, 
    after JSONB, 
    event_metadata JSONB, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_audit_events PRIMARY KEY (id)
);

CREATE INDEX ix_audit_events_action ON audit_events (action);

CREATE INDEX ix_audit_events_created_at ON audit_events (created_at);

CREATE INDEX ix_audit_events_entity ON audit_events (entity_type, entity_id);

CREATE TABLE agent_tasks (
    id UUID DEFAULT gen_random_uuid() NOT NULL, 
    correlation_id VARCHAR(100) NOT NULL, 
    from_agent VARCHAR(100) NOT NULL, 
    to_agent VARCHAR(100), 
    task_type VARCHAR(100) NOT NULL, 
    status VARCHAR(50) DEFAULT 'pending' NOT NULL, 
    payload JSONB, 
    result JSONB, 
    requires_approval BOOLEAN DEFAULT false NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_agent_tasks PRIMARY KEY (id)
);

CREATE INDEX ix_agent_tasks_correlation_id ON agent_tasks (correlation_id);

CREATE INDEX ix_agent_tasks_from_to ON agent_tasks (from_agent, to_agent);

CREATE INDEX ix_agent_tasks_status ON agent_tasks (status);

UPDATE alembic_version SET version_num='0002' WHERE alembic_version.version_num = '0001';

-- Running upgrade 0002 -> 0003

CREATE TABLE utilisateurs (
    id UUID DEFAULT gen_random_uuid() NOT NULL, 
    email VARCHAR(255) NOT NULL, 
    nom VARCHAR(100) NOT NULL, 
    prenom VARCHAR(100) NOT NULL, 
    password_hash VARCHAR(255) NOT NULL, 
    role VARCHAR(50) DEFAULT 'en_attente' NOT NULL, 
    statut VARCHAR(50) DEFAULT 'pending' NOT NULL, 
    approved_at TIMESTAMP WITH TIME ZONE, 
    approved_by UUID, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_utilisateurs PRIMARY KEY (id), 
    CONSTRAINT uq_utilisateurs_email UNIQUE (email)
);

CREATE INDEX ix_utilisateurs_statut ON utilisateurs (statut);

UPDATE alembic_version SET version_num='0003' WHERE alembic_version.version_num = '0002';

-- Running upgrade 0003 -> 0004

CREATE TABLE configurations_runtime_agent (
    id UUID DEFAULT gen_random_uuid() NOT NULL, 
    provider VARCHAR(50) NOT NULL, 
    model VARCHAR(150) NOT NULL, 
    base_url VARCHAR(500), 
    temperature FLOAT DEFAULT 0 NOT NULL, 
    max_iterations INTEGER DEFAULT 25 NOT NULL, 
    actif BOOLEAN DEFAULT false NOT NULL, 
    modifie_par VARCHAR(64), 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_configurations_runtime_agent PRIMARY KEY (id)
);

CREATE INDEX ix_configurations_runtime_agent_actif ON configurations_runtime_agent (actif);

CREATE INDEX ix_configurations_runtime_agent_created_at ON configurations_runtime_agent (created_at);

UPDATE alembic_version SET version_num='0004' WHERE alembic_version.version_num = '0003';

-- Running upgrade 0004 -> 0005

ALTER TABLE appels_offre RENAME TO appels_a_proposition;

ALTER TABLE appels_a_proposition RENAME CONSTRAINT pk_appels_offre TO pk_appels_a_proposition;

ALTER TABLE appels_a_proposition RENAME CONSTRAINT uq_appels_offre_reference TO uq_appels_a_proposition_reference;

ALTER TABLE appels_a_proposition RENAME CONSTRAINT fk_appels_offre_organisation_id_organisations TO fk_appels_a_proposition_organisation_id_organisations;

ALTER INDEX ix_appels_offre_organisation_id RENAME TO ix_appels_a_proposition_organisation_id;

ALTER INDEX ix_appels_offre_statut RENAME TO ix_appels_a_proposition_statut;

ALTER TABLE lots RENAME appel_offre_id TO appel_a_proposition_id;

ALTER TABLE lots RENAME CONSTRAINT fk_lots_appel_offre_id_appels_offre TO fk_lots_appel_a_proposition_id_appels_a_proposition;

ALTER TABLE lots RENAME CONSTRAINT uq_lots_appel_offre_id_numero TO uq_lots_appel_a_proposition_id_numero;

ALTER INDEX ix_lots_appel_offre_id RENAME TO ix_lots_appel_a_proposition_id;

ALTER TABLE documents RENAME appel_offre_id TO appel_a_proposition_id;

ALTER TABLE documents RENAME CONSTRAINT fk_documents_appel_offre_id_appels_offre TO fk_documents_appel_a_proposition_id_appels_a_proposition;

ALTER INDEX ix_documents_appel_offre_id RENAME TO ix_documents_appel_a_proposition_id;

UPDATE alembic_version SET version_num='0005' WHERE alembic_version.version_num = '0004';

-- Running upgrade 0005 -> 0006

ALTER TABLE offres_formation ADD CONSTRAINT uq_offres_formation_lot_id UNIQUE (lot_id);

DROP INDEX ix_offres_formation_lot_id;

ALTER TABLE missions ADD CONSTRAINT uq_missions_offre_formation_id UNIQUE (offre_formation_id);

DROP INDEX ix_missions_offre_formation_id;

UPDATE alembic_version SET version_num='0006' WHERE alembic_version.version_num = '0005';

-- Running upgrade 0006 -> 0007

ALTER TABLE beneficiaires ADD COLUMN statut VARCHAR(50) DEFAULT 'actif' NOT NULL;

UPDATE equipes SET statut = 'actif' WHERE statut IS NULL;

ALTER TABLE documents ADD COLUMN session_id UUID;

ALTER TABLE documents ADD CONSTRAINT fk_documents_session_id_sessions FOREIGN KEY(session_id) REFERENCES sessions (id) ON DELETE CASCADE;

CREATE INDEX ix_documents_session_id ON documents (session_id);

UPDATE alembic_version SET version_num='0007' WHERE alembic_version.version_num = '0006';

-- Running upgrade 0007 -> 0008

ALTER TABLE offres_formation RENAME TO offres;

ALTER TABLE offres RENAME CONSTRAINT pk_offres_formation TO pk_offres;

ALTER TABLE offres RENAME CONSTRAINT uq_offres_formation_reference TO uq_offres_reference;

ALTER TABLE offres RENAME CONSTRAINT fk_offres_formation_organisation_id_organisations TO fk_offres_organisation_id_organisations;

ALTER TABLE offres RENAME CONSTRAINT fk_offres_formation_lot_id_lots TO fk_offres_lot_id_lots;

ALTER TABLE offres RENAME CONSTRAINT fk_offres_formation_modele_document_id_documents TO fk_offres_modele_document_id_documents;

ALTER INDEX ix_offres_formation_organisation_id RENAME TO ix_offres_organisation_id;

ALTER INDEX ix_offres_formation_statut RENAME TO ix_offres_statut;

ALTER TABLE offres ADD COLUMN type VARCHAR(50);

UPDATE offres SET type = 'offre_technique' WHERE type IS NULL;

ALTER TABLE offres ALTER COLUMN type SET NOT NULL;

ALTER TABLE offres DROP CONSTRAINT uq_offres_formation_lot_id;

ALTER TABLE offres ADD CONSTRAINT uq_offres_lot_id_type UNIQUE (lot_id, type);

CREATE INDEX ix_offres_lot_id ON offres (lot_id);

CREATE INDEX ix_offres_type ON offres (type);

ALTER TABLE offres ADD COLUMN appel_a_proposition_id UUID;

UPDATE offres o SET appel_a_proposition_id = l.appel_a_proposition_id FROM lots l WHERE o.lot_id = l.id AND o.appel_a_proposition_id IS NULL;

ALTER TABLE offres ALTER COLUMN appel_a_proposition_id SET NOT NULL;

ALTER TABLE offres ADD CONSTRAINT fk_offres_appel_a_proposition_id_appels_a_proposition FOREIGN KEY(appel_a_proposition_id) REFERENCES appels_a_proposition (id) ON DELETE RESTRICT;

CREATE INDEX ix_offres_appel_a_proposition_id ON offres (appel_a_proposition_id);

ALTER TABLE appels_a_proposition ADD COLUMN type VARCHAR(50) DEFAULT 'appel_a_proposition' NOT NULL;

CREATE TABLE lieux (
    id UUID DEFAULT gen_random_uuid() NOT NULL, 
    nom VARCHAR(255) NOT NULL, 
    adresse TEXT, 
    ville VARCHAR(255), 
    pays VARCHAR(100), 
    zone VARCHAR(255), 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_lieux PRIMARY KEY (id)
);

CREATE INDEX ix_lieux_nom ON lieux (nom);

CREATE INDEX ix_lieux_ville ON lieux (ville);

ALTER TABLE missions ADD COLUMN lieu_id UUID;

INSERT INTO lieux (id, nom, created_at, updated_at) SELECT gen_random_uuid(), s.nom, now(), now() FROM (SELECT DISTINCT btrim(lieu) AS nom FROM missions       WHERE lieu IS NOT NULL AND btrim(lieu) <> '') s;

UPDATE missions m SET lieu_id = l.id FROM lieux l WHERE m.lieu IS NOT NULL AND btrim(m.lieu) = l.nom;

ALTER TABLE missions ADD CONSTRAINT fk_missions_lieu_id_lieux FOREIGN KEY(lieu_id) REFERENCES lieux (id) ON DELETE SET NULL;

CREATE INDEX ix_missions_lieu_id ON missions (lieu_id);

CREATE TABLE mission_offres (
    id UUID DEFAULT gen_random_uuid() NOT NULL, 
    mission_id UUID NOT NULL, 
    offre_id UUID NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_mission_offres PRIMARY KEY (id), 
    CONSTRAINT fk_mission_offres_mission_id_missions FOREIGN KEY(mission_id) REFERENCES missions (id) ON DELETE CASCADE, 
    CONSTRAINT fk_mission_offres_offre_id_offres FOREIGN KEY(offre_id) REFERENCES offres (id) ON DELETE CASCADE, 
    CONSTRAINT uq_mission_offres_mission_id_offre_id UNIQUE (mission_id, offre_id)
);

CREATE INDEX ix_mission_offres_mission_id ON mission_offres (mission_id);

CREATE INDEX ix_mission_offres_offre_id ON mission_offres (offre_id);

INSERT INTO mission_offres (id, mission_id, offre_id, created_at, updated_at) SELECT gen_random_uuid(), id, offre_formation_id, now(), now() FROM missions WHERE offre_formation_id IS NOT NULL;

ALTER TABLE missions DROP CONSTRAINT uq_missions_offre_formation_id;

ALTER TABLE missions DROP CONSTRAINT fk_missions_offre_formation_id_offres_formation;

ALTER TABLE missions DROP COLUMN offre_formation_id;

ALTER TABLE missions DROP COLUMN lieu;

CREATE TABLE supports_formation (
    id UUID DEFAULT gen_random_uuid() NOT NULL, 
    mission_id UUID NOT NULL, 
    equipe_id UUID NOT NULL, 
    document_id UUID NOT NULL, 
    libelle VARCHAR(255), 
    statut VARCHAR(50), 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_supports_formation PRIMARY KEY (id), 
    CONSTRAINT fk_supports_formation_mission_id_missions FOREIGN KEY(mission_id) REFERENCES missions (id) ON DELETE CASCADE, 
    CONSTRAINT fk_supports_formation_equipe_id_equipes FOREIGN KEY(equipe_id) REFERENCES equipes (id) ON DELETE RESTRICT, 
    CONSTRAINT fk_supports_formation_document_id_documents FOREIGN KEY(document_id) REFERENCES documents (id) ON DELETE RESTRICT, 
    CONSTRAINT uq_supports_formation_mission_equipe_document UNIQUE (mission_id, equipe_id, document_id)
);

CREATE INDEX ix_supports_formation_mission_id ON supports_formation (mission_id);

CREATE INDEX ix_supports_formation_equipe_id ON supports_formation (equipe_id);

CREATE INDEX ix_supports_formation_document_id ON supports_formation (document_id);

ALTER TABLE documents RENAME offre_formation_id TO offre_id;

ALTER TABLE documents RENAME CONSTRAINT fk_documents_offre_formation_id_offres_formation TO fk_documents_offre_id_offres;

ALTER INDEX ix_documents_offre_formation_id RENAME TO ix_documents_offre_id;

ALTER TABLE budgets RENAME offre_formation_id TO offre_id;

ALTER TABLE budgets RENAME CONSTRAINT fk_budgets_offre_formation_id_offres_formation TO fk_budgets_offre_id_offres;

ALTER INDEX ix_budgets_offre_formation_id RENAME TO ix_budgets_offre_id;

UPDATE documents SET type_document = 'offre' WHERE type_document = 'offre_formation';

UPDATE alembic_version SET version_num='0008' WHERE alembic_version.version_num = '0007';

-- Running upgrade 0008 -> 0009

ALTER TABLE configurations_runtime_agent ADD COLUMN api_key_chiffree VARCHAR(1000);

CREATE TABLE surcharges_agent (
    agent_id VARCHAR(100) NOT NULL, 
    outils_desactives JSONB DEFAULT '[]'::jsonb NOT NULL, 
    instructions_supplementaires TEXT, 
    skills_ajoutes JSONB DEFAULT '[]'::jsonb NOT NULL, 
    actif BOOLEAN DEFAULT true NOT NULL, 
    modifie_par VARCHAR(64), 
    id UUID NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_surcharges_agent PRIMARY KEY (id)
);

CREATE UNIQUE INDEX uq_surcharges_agent_agent_id ON surcharges_agent (agent_id);

CREATE TABLE serveurs_mcp (
    nom VARCHAR(100) NOT NULL, 
    transport VARCHAR(30) DEFAULT 'streamable_http' NOT NULL, 
    url VARCHAR(1000), 
    commande VARCHAR(500), 
    arguments JSONB DEFAULT '[]'::jsonb NOT NULL, 
    agents JSONB DEFAULT '[]'::jsonb NOT NULL, 
    en_tetes_chiffres TEXT, 
    actif BOOLEAN DEFAULT true NOT NULL, 
    modifie_par VARCHAR(64), 
    id UUID NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_serveurs_mcp PRIMARY KEY (id)
);

CREATE UNIQUE INDEX uq_serveurs_mcp_nom ON serveurs_mcp (nom);

CREATE INDEX ix_serveurs_mcp_actif ON serveurs_mcp (actif);

UPDATE alembic_version SET version_num='0009' WHERE alembic_version.version_num = '0008';

-- Running upgrade 0009 -> 0010

CREATE TABLE presences (
    id UUID DEFAULT gen_random_uuid() NOT NULL, 
    participation_id UUID NOT NULL, 
    date DATE NOT NULL, 
    presence VARCHAR(50), 
    heure_arrivee TIME WITHOUT TIME ZONE, 
    heure_depart TIME WITHOUT TIME ZONE, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_presences PRIMARY KEY (id), 
    CONSTRAINT fk_presences_participation_id_participations FOREIGN KEY(participation_id) REFERENCES participations (id) ON DELETE CASCADE, 
    CONSTRAINT uq_presences_participation_id_date UNIQUE (participation_id, date)
);

CREATE INDEX ix_presences_participation_id ON presences (participation_id);

CREATE INDEX ix_presences_date ON presences (date);

INSERT INTO presences (id, participation_id, date, presence, heure_arrivee, heure_depart, created_at, updated_at) SELECT gen_random_uuid(), p.id, COALESCE(s.date_debut, m.date_debut),        p.presence, p.heure_arrivee, p.heure_depart, now(), now() FROM participations p JOIN sessions s ON s.id = p.session_id LEFT JOIN missions m ON m.id = s.mission_id WHERE p.presence IS NOT NULL   AND COALESCE(s.date_debut, m.date_debut) IS NOT NULL;

DO $$ DECLARE restants integer; BEGIN   SELECT count(*) INTO restants FROM participations p   JOIN sessions s ON s.id = p.session_id   LEFT JOIN missions m ON m.id = s.mission_id   WHERE p.presence IS NOT NULL     AND COALESCE(s.date_debut, m.date_debut) IS NULL;   IF restants > 0 THEN     RAISE NOTICE 'presences: % pointage(s) sans date dérivable, non migrés',       restants;   END IF; END $$;;

ALTER TABLE participations DROP COLUMN heure_depart;

ALTER TABLE participations DROP COLUMN heure_arrivee;

ALTER TABLE participations DROP COLUMN presence;

UPDATE alembic_version SET version_num='0010' WHERE alembic_version.version_num = '0009';

-- Running upgrade 0010 -> 0011

CREATE TABLE notifications (
    id UUID NOT NULL, 
    destinataire_id UUID NOT NULL, 
    type VARCHAR(80) NOT NULL, 
    titre VARCHAR(255) NOT NULL, 
    corps TEXT, 
    objet_type VARCHAR(60), 
    objet_id UUID, 
    href VARCHAR(255), 
    lue_at TIMESTAMP WITH TIME ZONE, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_notifications PRIMARY KEY (id), 
    CONSTRAINT fk_notifications_destinataire_id_utilisateurs FOREIGN KEY(destinataire_id) REFERENCES utilisateurs (id) ON DELETE CASCADE
);

CREATE INDEX ix_notifications_destinataire_lue ON notifications (destinataire_id, lue_at);

CREATE INDEX ix_notifications_created_at ON notifications (created_at);

UPDATE alembic_version SET version_num='0011' WHERE alembic_version.version_num = '0010';

-- Running upgrade 0011 -> 0012

CREATE TABLE conversations (
    id UUID NOT NULL, 
    type VARCHAR(20) NOT NULL, 
    titre VARCHAR(255), 
    fermee_at TIMESTAMP WITH TIME ZONE, 
    creee_par_id UUID NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_conversations PRIMARY KEY (id), 
    CONSTRAINT fk_conversations_creee_par_id_utilisateurs FOREIGN KEY(creee_par_id) REFERENCES utilisateurs (id) ON DELETE RESTRICT
);

CREATE INDEX ix_conversations_type ON conversations (type);

CREATE INDEX ix_conversations_ferme ON conversations (fermee_at);

CREATE TABLE membres_conversation (
    id UUID NOT NULL, 
    conversation_id UUID NOT NULL, 
    utilisateur_id UUID NOT NULL, 
    role VARCHAR(20) NOT NULL, 
    parti_at TIMESTAMP WITH TIME ZONE, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_membres_conversation PRIMARY KEY (id), 
    CONSTRAINT fk_membres_conversation_conversation_id_conversations FOREIGN KEY(conversation_id) REFERENCES conversations (id) ON DELETE CASCADE, 
    CONSTRAINT fk_membres_conversation_utilisateur_id_utilisateurs FOREIGN KEY(utilisateur_id) REFERENCES utilisateurs (id) ON DELETE CASCADE, 
    CONSTRAINT uq_membres_conversation_paire UNIQUE (conversation_id, utilisateur_id)
);

CREATE INDEX ix_membres_conversation_utilisateur ON membres_conversation (utilisateur_id);

CREATE TABLE messages (
    id UUID NOT NULL, 
    conversation_id UUID NOT NULL, 
    auteur_id UUID, 
    auteur_type VARCHAR(10) NOT NULL, 
    auteur_agent_id VARCHAR(60), 
    contenu TEXT NOT NULL, 
    piece_nom VARCHAR(255), 
    piece_chemin VARCHAR(1024), 
    piece_mime VARCHAR(255), 
    piece_taille INTEGER, 
    supprime_at TIMESTAMP WITH TIME ZONE, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_messages PRIMARY KEY (id), 
    CONSTRAINT fk_messages_conversation_id_conversations FOREIGN KEY(conversation_id) REFERENCES conversations (id) ON DELETE CASCADE, 
    CONSTRAINT fk_messages_auteur_id_utilisateurs FOREIGN KEY(auteur_id) REFERENCES utilisateurs (id) ON DELETE SET NULL
);

CREATE INDEX ix_messages_conversation_date ON messages (conversation_id, created_at);

CREATE INDEX ix_messages_auteur ON messages (auteur_id);

CREATE INDEX ix_messages_supprime ON messages (supprime_at);

CREATE TABLE annonces (
    id UUID NOT NULL, 
    auteur_id UUID NOT NULL, 
    titre VARCHAR(255) NOT NULL, 
    contenu TEXT, 
    piece_nom VARCHAR(255), 
    piece_chemin VARCHAR(1024), 
    piece_mime VARCHAR(255), 
    piece_taille INTEGER, 
    supprime_at TIMESTAMP WITH TIME ZONE, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_annonces PRIMARY KEY (id), 
    CONSTRAINT fk_annonces_auteur_id_utilisateurs FOREIGN KEY(auteur_id) REFERENCES utilisateurs (id) ON DELETE RESTRICT
);

CREATE INDEX ix_annonces_date ON annonces (created_at);

CREATE TABLE commentaires (
    id UUID NOT NULL, 
    annonce_id UUID NOT NULL, 
    auteur_id UUID NOT NULL, 
    contenu TEXT NOT NULL, 
    supprime_at TIMESTAMP WITH TIME ZONE, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_commentaires PRIMARY KEY (id), 
    CONSTRAINT fk_commentaires_annonce_id_annonces FOREIGN KEY(annonce_id) REFERENCES annonces (id) ON DELETE CASCADE, 
    CONSTRAINT fk_commentaires_auteur_id_utilisateurs FOREIGN KEY(auteur_id) REFERENCES utilisateurs (id) ON DELETE RESTRICT
);

CREATE INDEX ix_commentaires_annonce_date ON commentaires (annonce_id, created_at);

CREATE TABLE reactions (
    id UUID NOT NULL, 
    utilisateur_id UUID NOT NULL, 
    emoji VARCHAR(16) NOT NULL, 
    cible_type VARCHAR(20) NOT NULL, 
    cible_id UUID NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_reactions PRIMARY KEY (id), 
    CONSTRAINT fk_reactions_utilisateur_id_utilisateurs FOREIGN KEY(utilisateur_id) REFERENCES utilisateurs (id) ON DELETE CASCADE, 
    CONSTRAINT uq_reactions_utilisateur_emoji_cible UNIQUE (utilisateur_id, emoji, cible_type, cible_id)
);

CREATE INDEX ix_reactions_cible ON reactions (cible_type, cible_id);

UPDATE alembic_version SET version_num='0012' WHERE alembic_version.version_num = '0011';

-- Running upgrade 0012 -> 0013

ALTER TABLE utilisateurs ADD COLUMN derniere_activite_at TIMESTAMP WITH TIME ZONE;

CREATE INDEX ix_utilisateurs_derniere_activite ON utilisateurs (derniere_activite_at);

UPDATE alembic_version SET version_num='0013' WHERE alembic_version.version_num = '0012';

-- Running upgrade 0013 -> 0014

ALTER TABLE membres_conversation ADD COLUMN derniere_lecture_at TIMESTAMP WITH TIME ZONE;

ALTER TABLE membres_conversation ADD COLUMN archive_at TIMESTAMP WITH TIME ZONE;

ALTER TABLE conversations ADD COLUMN archive_at TIMESTAMP WITH TIME ZONE;

CREATE INDEX ix_membres_conversation_archive ON membres_conversation (archive_at);

CREATE INDEX ix_conversations_archive ON conversations (archive_at);

UPDATE alembic_version SET version_num='0014' WHERE alembic_version.version_num = '0013';

-- Running upgrade 0014 -> 0015

ALTER TABLE equipes ADD COLUMN role_compte VARCHAR(50);

ALTER TABLE utilisateurs ADD COLUMN equipe_id UUID;

ALTER TABLE utilisateurs ADD CONSTRAINT fk_utilisateurs_equipe_id_equipes FOREIGN KEY(equipe_id) REFERENCES equipes (id) ON DELETE SET NULL;

ALTER TABLE utilisateurs ADD COLUMN photo_profil_chemin VARCHAR(1024);

CREATE UNIQUE INDEX ix_utilisateurs_equipe ON utilisateurs (equipe_id);

ALTER TABLE messages ADD COLUMN reponse_a_id UUID;

ALTER TABLE messages ADD CONSTRAINT fk_messages_reponse_a_id_messages FOREIGN KEY(reponse_a_id) REFERENCES messages (id) ON DELETE SET NULL;

CREATE INDEX ix_messages_reponse_a ON messages (reponse_a_id);

CREATE TABLE annonces_lectures (
    id UUID NOT NULL, 
    annonce_id UUID NOT NULL, 
    utilisateur_id UUID NOT NULL, 
    lu_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    CONSTRAINT pk_annonces_lectures PRIMARY KEY (id), 
    CONSTRAINT uq_annonces_lectures_paire UNIQUE (annonce_id, utilisateur_id), 
    CONSTRAINT fk_annonces_lectures_annonce_id_annonces FOREIGN KEY(annonce_id) REFERENCES annonces (id) ON DELETE CASCADE, 
    CONSTRAINT fk_annonces_lectures_utilisateur_id_utilisateurs FOREIGN KEY(utilisateur_id) REFERENCES utilisateurs (id) ON DELETE CASCADE
);

CREATE INDEX ix_annonces_lectures_utilisateur ON annonces_lectures (utilisateur_id);

UPDATE alembic_version SET version_num='0015' WHERE alembic_version.version_num = '0014';

-- Running upgrade 0015 -> 0017

ALTER TABLE annonces_lectures ADD COLUMN created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL;

ALTER TABLE annonces_lectures ADD COLUMN updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL;

UPDATE alembic_version SET version_num='0017' WHERE alembic_version.version_num = '0015';

-- Running upgrade 0017 -> 0018

CREATE TABLE annonces_pieces (
    id UUID NOT NULL, 
    annonce_id UUID NOT NULL, 
    nom VARCHAR(255) NOT NULL, 
    chemin VARCHAR(1024) NOT NULL, 
    mime VARCHAR(255), 
    taille INTEGER, 
    position SMALLINT DEFAULT '0' NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_annonces_pieces PRIMARY KEY (id), 
    CONSTRAINT fk_annonces_pieces_annonce_id_annonces FOREIGN KEY(annonce_id) REFERENCES annonces (id) ON DELETE CASCADE
);

CREATE INDEX ix_annonces_pieces_annonce ON annonces_pieces (annonce_id, position);

INSERT INTO annonces_pieces (id, annonce_id, nom, chemin, mime, taille, position)
        SELECT gen_random_uuid(), a.id, a.piece_nom, a.piece_chemin, a.piece_mime,
               a.piece_taille, 0
        FROM annonces a
        WHERE a.piece_chemin IS NOT NULL
          AND NOT EXISTS (
              SELECT 1 FROM annonces_pieces p WHERE p.annonce_id = a.id
          );

UPDATE alembic_version SET version_num='0018' WHERE alembic_version.version_num = '0017';

-- Running upgrade 0018 -> 0019

ALTER TABLE serveurs_mcp ADD COLUMN utilisateur_id UUID;

CREATE INDEX ix_serveurs_mcp_utilisateur_id ON serveurs_mcp (utilisateur_id);

ALTER TABLE serveurs_mcp ADD CONSTRAINT fk_serveurs_mcp_utilisateur FOREIGN KEY(utilisateur_id) REFERENCES utilisateurs (id) ON DELETE CASCADE;

DROP INDEX uq_serveurs_mcp_nom;

CREATE UNIQUE INDEX uq_serveurs_mcp_proprietaire_nom ON serveurs_mcp (utilisateur_id, nom);

CREATE UNIQUE INDEX uq_serveurs_mcp_nom_global ON serveurs_mcp (nom) WHERE utilisateur_id IS NULL;

UPDATE alembic_version SET version_num='0019' WHERE alembic_version.version_num = '0018';

-- Running upgrade 0019 -> 0020

ALTER TABLE serveurs_mcp ADD COLUMN mode_authentification VARCHAR(20) DEFAULT 'aucune' NOT NULL;

ALTER TABLE serveurs_mcp ADD COLUMN oauth_client_id VARCHAR(255);

ALTER TABLE serveurs_mcp ADD COLUMN oauth_etat_chiffre TEXT;

ALTER TABLE serveurs_mcp ADD COLUMN oauth_statut VARCHAR(20) DEFAULT 'non_requis' NOT NULL;

ALTER TABLE serveurs_mcp ADD COLUMN oauth_derniere_erreur TEXT;

ALTER TABLE serveurs_mcp ADD COLUMN derniere_sonde TEXT;

UPDATE serveurs_mcp SET mode_authentification = 'en_tetes' WHERE en_tetes_chiffres IS NOT NULL;

UPDATE alembic_version SET version_num='0020' WHERE alembic_version.version_num = '0019';

-- Running upgrade 0020 -> 0021

CREATE TABLE configurations_agent_personnel (
    id UUID NOT NULL, 
    utilisateur_id UUID NOT NULL, 
    instructions TEXT, 
    outils_desactives JSONB NOT NULL, 
    actif BOOLEAN DEFAULT true NOT NULL, 
    modifie_par VARCHAR(64), 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_configurations_agent_personnel PRIMARY KEY (id), 
    CONSTRAINT fk_configurations_agent_personnel_utilisateur_id_utilisateurs FOREIGN KEY(utilisateur_id) REFERENCES utilisateurs (id) ON DELETE CASCADE
);

CREATE UNIQUE INDEX uq_configurations_agent_personnel_utilisateur ON configurations_agent_personnel (utilisateur_id);

UPDATE alembic_version SET version_num='0021' WHERE alembic_version.version_num = '0020';

-- Running upgrade 0021 -> 0022

CREATE TABLE codes_reinitialisation_mot_de_passe (
    id UUID NOT NULL, 
    utilisateur_id UUID NOT NULL, 
    code_hash VARCHAR(255) NOT NULL, 
    expire_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    consomme_at TIMESTAMP WITH TIME ZONE, 
    tentatives INTEGER DEFAULT 0 NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    CONSTRAINT pk_codes_reinitialisation_mot_de_passe PRIMARY KEY (id), 
    CONSTRAINT fk_codes_reinitialisation_mot_de_passe_utilisateur_id_u_48f0 FOREIGN KEY(utilisateur_id) REFERENCES utilisateurs (id) ON DELETE CASCADE
);

CREATE INDEX ix_codes_reinit_expire ON codes_reinitialisation_mot_de_passe (expire_at);

UPDATE alembic_version SET version_num='0022' WHERE alembic_version.version_num = '0021';

COMMIT;

