# syntax=docker/dockerfile:1
# CARSO AI — image du backend (FastAPI + agents Deep Agents).
#
# Le contexte de build est la RACINE du dépôt, pas `backend/` : les skills des
# agents vivent dans `skills/agents`, hors de `backend/`, et
# `app/agents/skills.py` les résout par défaut à cet emplacement. La disposition
# du dépôt est donc préservée dans l'image : `/app/backend` + `/app/skills`.
#
#   docker build -f backend/Dockerfile -t carso-backend .
#   docker compose up --build backend     # avec la base PostgreSQL du compose
#
# Ce que l'image inclut, et pourquoi :
# - les extras `agents, documents, documents_pro, web, video` : l'image livre le
#   produit complet, jamais un backend amputé de ses agents ou de sa génération
#   documentaire (les extras exacts vivent dans `backend/pyproject.toml`) ;
# - Chromium (Playwright) : `generer_document_professionnel` rend les PDF dans un
#   vrai navigateur — c'est le chemin PRIORITAIRE des livrables, et ni Edge ni
#   Chrome n'existent dans un conteneur ;
# - `pdftoppm` (poppler-utils) : `render_document`, l'aperçu visuel que l'agent
#   regarde avant de présenter un document ;
# - ffmpeg : rien à installer, le wheel `imageio-ffmpeg` embarque son binaire
#   (extra `video` : assemblage vidéo+son à la volée, aucun fichier écrit) ;
# - polices Liberation + DejaVu : le thème CARSO nomme Segoe UI / Arial
#   (`app/documents/professionnel/theme_css.py`), absentes d'un Linux nu — sans
#   elles le PDF rendu ne ressemble plus au document que CARSO signe.
#
# Aucun secret dans l'image : `backend/.env` est exclu du contexte
# (`.dockerignore`). La configuration arrive par l'environnement au lancement.

# --- Étape 1 : dépendances Python --------------------------------------------
# Un seul paquet déclaré n'a pas de wheel Linux : `psycopg2`. PyPI ne publie que
# des wheels Windows et une archive source (vérifié le 15/10/2026), donc il se
# compile ici — avec `pg_config` et un compilateur, que l'image finale ne porte
# pas. Tout le reste s'installe en wheel (manylinux, vérifié pour psycopg,
# playwright, pillow, lxml, tiktoken, imageio-ffmpeg, uvloop, httptools,
# greenlet).
FROM python:3.11-slim-bookworm AS deps

# Extras installés : tout le produit sauf `dev`. Surchargeable au build
# (`--build-arg CARSO_EXTRAS=agents` pour une image sans outils documentaires).
ARG CARSO_EXTRAS="agents,documents,documents_pro,web,video"

RUN apt-get update \
 && apt-get install -y --no-install-recommends gcc libpq-dev \
 && rm -rf /var/lib/apt/lists/*

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    VIRTUAL_ENV=/opt/venv
# Un venv plutôt que le `site-packages` système : il se copie d'une étape à
# l'autre sans traîner le compilateur.
RUN python -m venv "$VIRTUAL_ENV"
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

WORKDIR /app
COPY backend/pyproject.toml backend/README.md ./backend/
COPY backend/app ./backend/app
RUN python -m pip install --upgrade pip \
 && python -m pip install "./backend[${CARSO_EXTRAS}]"

# --- Étape 2 : image d'exécution ---------------------------------------------
FROM python:3.11-slim-bookworm AS runtime

# Outils système : aperçu PDF, polices, client libpq (utilisé par le psycopg2
# compilé ci-dessus — sans lui, `import psycopg2` échouerait à l'exécution).
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        poppler-utils \
        libpq5 \
        fontconfig \
        fonts-liberation \
        fonts-dejavu-core \
 && rm -rf /var/lib/apt/lists/*

# PLAYWRIGHT_BROWSERS_PATH : les navigateurs ne vivent pas dans le HOME du
# compte d'exécution — un chemin unique, lisible par ce compte (non root).
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    VIRTUAL_ENV=/opt/venv \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

COPY --from=deps /opt/venv /opt/venv

# Chromium : poser `--build-arg INSTALL_PLAYWRIGHT=0` pour une image légère —
# `generer_document_professionnel` échouera alors explicitement (le DOCX, lui,
# reste rendu). `--with-deps` installe les bibliothèques partagées par Chromium :
# c'est le seul moment où le build travaille en root.
ARG INSTALL_PLAYWRIGHT="1"
RUN if [ "$INSTALL_PLAYWRIGHT" = "1" ]; then \
        python -m playwright install --with-deps chromium \
     && chmod -R a+rX /ms-playwright ; \
    else \
        echo "Chromium non installé (INSTALL_PLAYWRIGHT=0) : generer_document_professionnel échouera explicitement." ; \
    fi

# --- Le backend complet, puis les skills des agents --------------------------
WORKDIR /app
COPY backend/ ./backend/
COPY skills/ ./skills/

# --- Stockage documentaire et compte d'exécution -----------------------------
# `/storage/carso` est la racine documentaire de référence : créée et possédée
# par le compte applicatif, sinon le premier dépôt échouerait.
RUN useradd --create-home --uid 10001 carso \
 && mkdir -p /storage/carso \
 && chown -R carso:carso /storage/carso /app
# Garde-fou de build : l'aperçu visuel des PDF dépend de `pdftoppm`. Mieux vaut
# un build qui échoue ici qu'un `render_document` cassé en production.
RUN command -v pdftoppm >/dev/null \
 && echo "pdftoppm : $(pdftoppm -v 2>&1 | head -1)"

USER carso
WORKDIR /app/backend
ENV STORAGE_ROOT=/storage/carso

EXPOSE 8000

# `/health` répond sans authentification : c'est la sonde de vie, pas une route
# métier. `urllib` évite d'installer curl pour ce seul geste.
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD python -c "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=5).status == 200 else 1)"

# `python -m uvicorn` plutôt que le script `uvicorn` : même environnement, aucun
# PATH implicite à supposer.
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
