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
racine du dépôt**, pas `backend/` : l'image embarque `backend/` **et**
`skills/agents` (les skills des agents vivent hors de `backend/`).

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
