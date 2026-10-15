"""Point d'entrée Render : migrations Alembic puis serveur uvicorn (déploiement de test).

Alembic (config/migrations + URL) est branché à la main car le script ne s'exécute
pas depuis backend/ mais depuis la racine du dépôt.
"""
from pathlib import Path

import uvicorn
from alembic import command
from alembic.config import Config

RACINE = Path(__file__).resolve().parent.parent          # backend/
cfg = Config(str(RACINE / "alembic.ini"))
cfg.set_main_option("script_location", str(RACINE / "migrations"))
command.upgrade(cfg, "head")

uvicorn.run("app.main:app", host="0.0.0.0", port=8000)
