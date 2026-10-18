# CARSO AI — Backend

Backend FastAPI du système CARSO (voir `../instruction/09_BACKEND_CODING_RULES.md` pour les règles).

## Prérequis

- Python 3.11+
- PostgreSQL accessible (dev local : PG 17 sur `localhost:5432`, voir `../docs/adr/0001-stack-and-structure.md`)

## Installation

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate        # Windows (Git Bash : source .venv/Scripts/activate)
pip install -e ".[dev]"
copy .env.example .env        # puis renseigner DATABASE_URL
```

## Base de données

Créer la base de dev puis appliquer les migrations :

```bash
# psql (une fois) :
CREATE DATABASE carso_dev;

alembic upgrade head          # depuis backend/ avec .env configuré
```

La migration `0001` est une base vide : les entités métier arrivent en Phase 3
après validation du modèle (`../docs/analysis/PHASE0_INVENTORY.md §7`).

### Supabase (base de déploiement)

Le schéma complet est livré dans `scripts/supabase_init.sql` (les 22
migrations repliées, `alembic_version = 0022`) : à coller **une seule fois**
dans le SQL Editor d'un projet Supabase vierge. Alembic reprend ensuite la main
sur les migrations suivantes (`alembic upgrade head`).

`DATABASE_URL` doit viser le **pooler de session**, jamais le direct endpoint :
ce dernier (`db.<ref>.supabase.co:5432`) est **IPv6-only** par défaut et reste
injoignable depuis une machine sans routage IPv6 — symptôme : `connection
timeout expired`, checkpointer en mémoire, configs MCP non résolues.

```bash
# .env — hôte et utilisateur repris du dialogue Connect du projet Supabase :
DATABASE_URL=postgresql+psycopg://postgres.<ref>:<mot_de_passe>@aws-N-<region>.pooler.supabase.com:5432/postgres?sslmode=require
```

Quatre pièges documentés (vérifiés le 17/10/2026, projet « Carso », eu-west-1) :

- **mode session (5432), pas transaction (6543)** : psycopg3 et SQLAlchemy
  utilisent les prepared statements, que le mode transaction refuse — les
  échecs n'apparaîtraient qu'après la 5ᵉ requête identique ;
- l'utilisateur du pooler est `postgres.<ref>`, **pas** `postgres` (sinon
  `ENOIDENTIFIER`) ;
- l'index `aws-N` **n'est pas déductible de la région** : `aws-0-eu-west-1`
  répond mais ne connaît pas le tenant (`ENOTFOUND`) — reprendre l'hôte exact
  du dialogue Connect (ici `aws-1-eu-west-1.pooler.supabase.com`) ;
- `sslmode=require` est obligatoire (Supabase n'accepte que TLS).

Le premier administrateur est créé **au démarrage** si `BOOTSTRAP_ADMIN_EMAIL`
et `BOOTSTRAP_ADMIN_PASSWORD` (≥ 8 caractères) sont posés dans `.env` —
création idempotante, voir `../docs/api/AUTH.md`. Les secrets de déploiement
restent dans l'environnement du backend, jamais dans le SQL.

## Génération documentaire professionnelle (option)

Le tool `generer_document_professionnel` (thème injecté, couverture, sommaire
numéroté, styles Word — ADR 0011) rend le PDF avec un **vrai navigateur
Chromium** : Edge ou Chrome déjà installés sur la machine, sinon le Chromium de
Playwright.

```bash
uv pip install -e ".[documents_pro]"

# Uniquement si ni Edge ni Chrome n'est présent (serveur Linux, conteneur) :
.venv/Scripts/python -m playwright install chromium   # Windows
.venv/bin/python -m playwright install chromium       # Linux
```

Sans ce paquet, le backend **démarre** et les autres tools documentaires restent
utilisables : seul l'appel à `generer_document_professionnel` échoue, avec une
erreur explicite qui nomme l'installation attendue (le DOCX, lui, est rendu par
`python-docx` et reste disponible).

## Lancer l'API

Depuis `backend/` (pas `backend/app/`) :

```bash
# extra agents requis pour AG-UI
uv pip install -e ".[agents]"

# lancer (reste en vie même sans AGENT_MODEL)
uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
# ou :  uv run python -m app.main

# API      : http://localhost:8000/api/v1
# Health   : http://localhost:8000/api/v1/health
# Docs     : http://localhost:8000/api/v1/docs
# Catalogue agents : http://localhost:8000/api/v1/agui/
```

L'absence de `AGENT_MODEL` / `AGENT_API_KEY` **ne bloque pas** le démarrage.
Les endpoints AG-UI sont montés ; la première conversation échouera
explicitement tant que le modèle n'est pas configuré.

## Lancer avec Docker

Le backend a son image (`backend/Dockerfile`). **Le contexte de build est la
racine du dépôt**, pas `backend/` (chemins du Dockerfile exprimés depuis la
racine) ; les skills des agents vivent dans `backend/app/agents/skills/` et
voyagent avec le paquet.

```bash
# image seule
docker build -f backend/Dockerfile -t carso-backend .

# base PostgreSQL + migrations + API
docker compose up --build -d
docker compose logs -f backend
curl http://localhost:8000/api/v1/health
```

La configuration (secrets compris) vient de `backend/.env` monté par le compose,
**jamais** copiée dans l'image ; `DATABASE_URL` et `STORAGE_ROOT` sont surchargés
pour le réseau du compose et le volume `/storage/carso`.

Deux arguments de build utiles :

- `--build-arg CARSO_EXTRAS=agents` — image sans outils documentaires ;
- `--build-arg INSTALL_PLAYWRIGHT=0` — image légère, sans Chromium : la
  génération PDF professionnelle échoue alors explicitement (le DOCX reste rendu).

Détail complet, choix techniques et limites : `../docs/api/DOCKER-BACKEND.md`.

## Déployer sur Render

Le dépôt fournit un blueprint (`../render.yaml`, racine) : extras complets
(`agents,documents,documents_pro,web,video`), Chromium **et** resvg-py pour la
génération documentaire, polices et `pdftoppm` pour les aperçus, migrations en
pre-deploy. Un déploiement natif avec un simple `pip install ./backend` n'a ni
le navigateur ni `resvg-py` : `generer_document_professionnel` échoue alors
avec « Aucun navigateur Chromium trouve » et « resvg-py n'est pas installé ».

```bash
# Vérification après déploiement (Render → Shell) :
python - <<'PY'
from app.documents.professionnel.pdf_renderer import navigateur_disponible
from app.documents.professionnel.rasterisation import rasterisation_disponible
print(navigateur_disponible(), rasterisation_disponible())   # attendu : (True, ...) True
PY
```

Procédure complète, variables d'environnement, disque persistant et option
Docker : `../docs/api/RENDER-DEPLOIEMENT.md`.

## Tests et qualité

```bash
pytest                 # suite verte sans base configurée
RUN_DB_TESTS=1 pytest  # inclut les tests de connectivité DB
ruff check .
mypy app
```

## Structure

```text
backend/
├── app/
│   ├── api/            # routers FastAPI, dependencies
│   ├── core/           # config, erreurs, logging
│   ├── domain/         # entités métier (Phase 2/3)
│   ├── application/    # use cases / services (Phase 3+)
│   ├── infrastructure/ # engine SQLAlchemy, repositories, storage
│   ├── agents/         # contrats agents (Phase 5+)
│   ├── tools/          # tools agents typés (Phase 5+)
│   ├── documents/      # système documentaire (Phase 4)
│   ├── services/       # services métier (Phase 3+)
│   └── main.py         # factory FastAPI
├── migrations/         # Alembic
└── tests/
```

Règle d'architecture : `Agent → Tool → Service → Repository → PostgreSQL`
(un agent n'appelle jamais un repository directement — `instruction/09 §7`).
