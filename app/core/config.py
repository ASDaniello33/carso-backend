"""Centralized, validated application configuration (instruction/09 §10)."""

from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment / backend/.env."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "CARSO AI"
    environment: str = Field(default="development")
    debug: bool = Field(default=True)

    api_v1_prefix: str = "/api/v1"

    # PostgreSQL (ADR 0001: local PG 17, base carso_dev, credentials in backend/.env)
    database_url: str = Field(
        default="postgresql+psycopg://carso:carso@localhost:5432/carso_dev",
        description="SQLAlchemy URL, e.g. postgresql+psycopg://user:pass@host:5432/db",
    )
    db_echo: bool = Field(default=False)

    # Root of the managed document storage tree (instruction/06).
    storage_root: str = Field(default="/storage/carso")

    # Phase 4 (instruction/11) — bornes de l'infrastructure documentaire.
    # Plafond d'upload et taille maximale de texte retourné par l'extraction
    # (bornes de service, pas des règles métier).
    max_upload_bytes: int = Field(default=25 * 1024 * 1024, ge=1)
    extraction_max_chars: int = Field(default=200_000, ge=1)

    # Police Unicode optionnelle pour le rendu PDF (chemin d'un fichier .ttf).
    # Absente : les polices de base du PDF sont utilisées et le texte est
    # translittéré (tirets longs, puces, « € ») — aucun caractère ne casse un
    # rendu (app.documents.renderers.pdf).
    document_police_ttf: str | None = Field(default=None)

    # Artefacts dérivés (PDF de rendu, images de pages) : sous-dossier du
    # stockage, jamais des ``documents`` en base (ADR 0005).
    previews_subdir: str = Field(default="previews")

    # --- Corbeille documentaire (ADR 0006) ---------------------------------
    # Durée de conservation d'une **fiche** supprimée avant que sa purge
    # définitive ne soit proposée à un administrateur. Le fichier, lui, quitte
    # le stockage dès la suppression (règle 23/09) : cette fenêtre ne concerne
    # que la trace documentaire (référence, auteur, motif, date).
    # Aucune politique de rétention CARSO n'est confirmée [?] : 30 jours est une
    # valeur de travail, surchargeable par ``DOCUMENTS_CORBEILLE_JOURS`` sans
    # toucher au code. La date d'échéance est **écrite** sur chaque fiche au
    # moment de la suppression : changer ce réglage ne déplace pas les échéances
    # déjà annoncées à l'utilisateur.
    documents_corbeille_jours: int = Field(default=30, ge=1, le=3650)

    # Résolution des images de page pour l'inspection visuelle (DPI).
    previews_dpi: int = Field(default=110, ge=40, le=300)

    # Nombre maximal de pages rendues en images (garde-fou de volume).
    previews_max_pages: int = Field(default=40, ge=1, le=500)

    # Exécutable de rasterisation PDF (pdftoppm, paquet poppler). Absent :
    # recherche dans le PATH ; introuvable : l'aperçu visuel est refusé par une
    # erreur explicite (le document et sa validation restent disponibles).
    pdftoppm_path: str | None = Field(default=None)

    # --- Agents (Deep Agents / LangChain) ---------------------------------
    # Provider sélectionnable au runtime, sans changer le code des agents :
    #   openai            -> langchain-openai (ChatOpenAI)
    #   google_genai      -> langchain-google-genai
    #   openai_compatible -> endpoint compatible OpenAI (base_url custom)
    agent_provider: str = Field(default="openai_compatible")
    agent_model: str = Field(default="")
    agent_base_url: str | None = Field(default=None)
    # SecretStr : la clé ne peut pas fuir via un log ou un repr accidentel
    # (instruction/08 §6). Jamais de clé dans le code, les prompts ou la base.
    agent_api_key: SecretStr | None = Field(default=None)
    # Clé de chiffrement au repos des secrets administrables (clé du provider
    # saisie depuis la page Paramètres, en-têtes de connecteurs MCP). Clé Fernet
    # URL-safe base64. Absente ⇒ dérivation depuis ``AUTH_SECRET``
    # (``app.core.chiffrement``) ; les deux absentes ⇒ l'enregistrement d'un
    # secret est refusé, jamais stocké en clair.
    parametres_chiffrement_key: SecretStr | None = Field(default=None)
    agent_temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    # Attente maximale (secondes) entre deux chunks de streaming du LLM. Les
    # modèles raisonneurs (grok, o1…) peuvent rester silencieux longtemps
    # pendant leur raisonnement interne ; le défaut de langchain-openai (120 s)
    # interrompt alors des runs valides. 0 désactive la garde.
    agent_stream_chunk_timeout: float = Field(default=300.0, ge=0.0)
    # Vérification du certificat TLS du provider LLM. Défaut sécurisé : vrai.
    # Un agrégateur auto-signé ou à certificat expiré (cas logfare.ai courant)
    # demande AGENT_SSL_VERIFY=false explicitement — jamais un contournement
    # silencieux dans le code.
    agent_ssl_verify: bool = Field(default=True)
    # Borne d'itérations du graphe LangGraph (``recursion_limit``). Passée
    # à chaque run AG-UI. 25 (défaut LangGraph) coupe un parcours outils
    # réaliste ; surchargeable via ``AGENT_MAX_ITERATIONS`` dans ``.env``.
    agent_max_iterations: int = Field(default=1000, ge=1, le=10_000)
    # Persistance LangGraph des threads d'agents (ADR 0009) : un PostgresSaver
    # partagé remplace le MemorySaver process (contexte conservé entre les
    # redémarrages). ``0`` force le fallback mémoire — la suite de tests le
    # pose pour ne jamais toucher la base réelle.
    agent_checkpoint_persistant: bool = Field(default=True)
    # Extrait de texte injecté dans le ``context`` AG-UI pour chaque pièce
    # jointe du tour (caractères). Les octets ne passent jamais au modèle ;
    # la suite se lit via les tools du contrat.
    agent_attachment_excerpt_chars: int = Field(default=4000, ge=200, le=50_000)

    # --- Collaboration inter-agent (instruction/05 §5/§7, instruction/10 §8) ---
    # Garde-fou anti-boucle : profondeur maximale d'une chaîne de délégation
    # partageant le même ``correlation_id``. Une chaîne plus profonde est refusée
    # (``AgentLoopError``) — aucun coordinateur central ne compte les tours.
    agent_max_collaboration_depth: int = Field(default=3, ge=1)
    # Budget de temps d'une tâche inter-agent. Il est *mesuré après* l'appel
    # (aucune interruption préemptive en cours de processus : voir
    # docs/agents/INTER_AGENT_COLLABORATION.md §5).
    # Un run deep-agent (analyseur via A2A) dépasse souvent 60 s : le budget
    # s'aligne sur le timeout de streaming LLM (modèles raisonneurs).
    agent_task_timeout_seconds: float = Field(default=300.0, gt=0)

    # Set to "1" to enable DB-dependent tests once backend/.env is configured.
    run_db_tests: bool = Field(default=False)

    # Set to "1" to enable agent tests that call a real LLM (network + clé).
    run_agent_tests: bool = Field(default=False)

    # --- Outils communs des agents (Lot 1, docs/agents/TOOLS_COMMUNS.md) ----
    # Recherche web (D4 : LangSearch). SecretStr : la clé ne fuit ni dans les
    # logs ni dans les prompts (instruction/08 §6). Absente => erreur explicite
    # à l'INVOCATION du tool (jamais au démarrage de l'application).
    langsearch_api_key: SecretStr | None = Field(default=None)
    langsearch_base_url: str = Field(
        default="https://api.langsearch.com",
        description="Endpoint de l'API LangSearch (v1).",
    )
    # Plafond de résultats par requête : le tool serre la borne, jamais l'inverse.
    web_search_max_results: int = Field(default=5, ge=1, le=10)
    # Mode texte officiel LangSearch (contents.text) — pas un scraping libre.
    web_search_text_max_chars: int = Field(default=3000, ge=200, le=5000)

    # Shell contrôlé (D1 : jamais de shell brut). Deny par défaut : une commande
    # hors allowlist est refusée ; un chemin hors des racines autorisées aussi.
    # Noms de commandes en minuscules, séparés par des virgules. La allowlist
    # par défaut ne contient que des commandes d'inspection sans mutation.
    shell_allowed_commands: str = Field(
        default="python,pip,git,get-childitem,get-content,get-item,select-string",
        description="Allowlist CSV des exécutables/cmdlets autorisés (deny par défaut).",
    )
    # Racines de travail autorisées, CSV. Vide => shell désactivé (le tool
    # refuse toute exécution) : activer le shell est une décision explicite.
    shell_allowed_roots: str = Field(
        default="",
        description="CSV des répertoires racines autorisés pour cwd et chemins.",
    )
    # Plafond de sortie renvoyée à l'agent (octets) et durée max par commande.
    shell_max_output_bytes: int = Field(default=100_000, ge=1)
    shell_timeout_seconds: int = Field(default=30, ge=1, le=600)

    # --- Skills Deep Agents + scripts contrôlés (Lot T1) -------------------
    # Répertoire racine des ``SKILL.md`` (clinrules 08 : skills = instructions
    # chargées à la demande, jamais du code exécuté implicitement).
    skills_root: str = Field(
        default="",
        description="Racine des skills agents (vide = <repo>/skills/agents).",
    )
    # Sandbox des scripts Python lancés par ``run_python_script``. Vide =
    # <storage_root>/_scripts — jamais le disque hôte entier.
    scripts_root: str = Field(
        default="",
        description="Racine sandbox des scripts Python d'agent.",
    )
    scripts_timeout_seconds: int = Field(default=30, ge=1, le=120)
    scripts_max_output_bytes: int = Field(default=80_000, ge=1)
    scripts_max_source_chars: int = Field(default=20_000, ge=1)

    # --- Authentification humaine (Lot AUTH) --------------------------------
    auth_secret: SecretStr | None = Field(
        default=None,
        description="Secret JWT (AUTH_SECRET). Absent ⇒ login refuse explicitement.",
    )
    auth_token_minutes: int = Field(default=60, ge=5, le=24 * 60)
    bootstrap_admin_email: str | None = Field(default=None)
    bootstrap_admin_password: SecretStr | None = Field(default=None)


@lru_cache
def get_settings() -> Settings:
    """Return the cached Settings singleton."""
    return Settings()
