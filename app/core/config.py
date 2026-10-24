"""Centralized, validated application configuration (instruction/09 §10)."""

from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

#: Racine du paquet backend (``backend/``), indépendante du répertoire courant.
RACINE_BACKEND = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """Application settings loaded from environment / backend/.env.

    ``env_file`` est **absolu** : un chemin relatif (``.env``) est résolu contre
    le répertoire courant. Lancé depuis la racine du dépôt —
    ``uvicorn app.main:app --app-dir backend``, un script, un service systemd —
    le fichier n'était pas lu : ``CORS_ORIGINS`` retombait silencieusement sur
    les seules origines locales, le préflight du frontend déployé recevait 400
    et le navigateur annulait la connexion. Les autres réglages (base, secret
    JWT) retombaient de la même manière sur leurs valeurs par défaut.
    """

    model_config = SettingsConfigDict(
        env_file=str(RACINE_BACKEND / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "CARSO AI"
    environment: str = Field(default="development")
    debug: bool = Field(default=True)

    api_v1_prefix: str = "/api/v1"

    # --- Adresses publiques (OAuth des connecteurs MCP, incr. 47) -----------
    # L'URI de redirection déclarée au serveur d'autorisation doit être **celle
    # que le navigateur de l'utilisateur peut atteindre** : c'est le backend qui
    # reçoit le code et échange le jeton (jamais le JavaScript), puis renvoie le
    # navigateur vers l'interface. Ces valeurs sont des adresses de déploiement,
    # pas des règles métier : surchargeables par BACKEND_PUBLIC_URL /
    # FRONTEND_PUBLIC_URL sans toucher au code.
    backend_public_url: str = Field(default="http://127.0.0.1:8000")
    frontend_public_url: str = Field(default="http://localhost:3000")

    # --- Origines CORS (CORS_ORIGINS, séparées par des virgules) ------------
    # Allowlist explicite : une origine absente voit son préflight CORS refusé
    # (400 « Disallowed CORS origin ») et le navigateur annule la requête. Les
    # défauts couvrent le développement local ; pour tester via un tunnel
    # Cloudflare ou déployer, ajouter l'origine **publique du frontend** (et non
    # celle du backend : c'est l'origine de la PAGE qui est vérifiée).
    # Annotation ``NoDecode`` : sans elle, pydantic-settings tente un
    # ``json.loads`` sur la valeur d'environnement AVANT le validateur et
    # lève SettingsError sur ``CORS_ORIGINS=a,b`` (constaté au démarrage réel).
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default=[
            "http://localhost:3000",
            "http://127.0.0.1:3000",
            "http://localhost:3950",
            "http://127.0.0.1:3950",
        ],
        description=(
            "Origines autorisées par le middleware CORS "
            "(CORS_ORIGINS, séparées par des virgules)."
        ),
    )

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _scinder_origins(cls, valeur: object) -> object:
        """Accepte ``https://a,https://b`` (et un tableau JSON) en plus d'une liste."""
        if isinstance(valeur, str):
            if valeur.lstrip().startswith("["):
                import json

                lu = json.loads(valeur)
                if isinstance(lu, list):
                    return lu
            return [origine.strip() for origine in valeur.split(",") if origine.strip()]
        return valeur

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

    # Rapatriement d'un **média externe** (image ou vidéo trouvée sur le web,
    # importée par un utilisateur depuis l'écran de lecture) : plafond de taille
    # et durée d'attente propres à ce geste, plus serrés que le dépôt humain
    # (une vidéo de 15 Mo est déjà longue à rapatrier). Bornes de service
    # (anti-abus), pas des règles métier — surchargeables par l'environnement.
    media_import_max_bytes: int = Field(default=15 * 1024 * 1024, ge=1)
    media_import_timeout_seconds: float = Field(default=20.0, gt=0, le=120)
    # Lecture d'une vidéo du web (extra « video » : yt-dlp) : métadonnées et
    # adresses de flux. **Aucun plafond de taille** — rien n'est téléchargé ni
    # conservé côté serveur : le client télécharge depuis la source, sur son
    # disque. Il ne reste donc qu'une borne de temps pour la lecture elle-même.
    video_analyse_timeout_seconds: float = Field(default=60.0, gt=0, le=600)
    #: Binaire ffmpeg utilisé **uniquement** pour assembler, à la volée, une piste
    #: vidéo et une piste son en un fichier lisible (mesuré le 09/10/2026 : les
    #: plateformes n'en publient plus, et leurs liens combinés directs exigent un
    #: PO Token anti-bot que CARSO ne peut pas produire). Chemin explicite sinon :
    #: ``ffmpeg`` du PATH, puis le binaire fourni par ``imageio-ffmpeg`` (extra
    #: ``video``). Absent partout ⇒ l'écran rend les deux fichiers séparés et le
    #: dit, sans jamais inventer.
    video_ffmpeg_path: str | None = Field(default=None)
    #: Borne d'un assemblage en flux : le temps de la retransmission, pas d'un
    #: stockage (rien n'est écrit sur le disque du serveur).
    video_muxage_timeout_seconds: float = Field(default=3600.0, gt=0, le=86_400)

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
    # Vérification du certificat TLS des **connecteurs MCP**. Défaut sécurisé :
    # vrai. Une passerelle auto-hébergée à certificat auto-signé (cas d'un
    # « Personal MCP Gateway » en https) demande une décision **explicite** :
    # ``MCP_SSL_CA_BUNDLE`` (on fait confiance à ce certificat-là — recommandé)
    # ou ``MCP_SSL_VERIFY=false`` (plus aucune vérification : développement
    # uniquement, avertissement journalisé une fois). Jamais un contournement
    # silencieux.
    mcp_ssl_verify: bool = Field(default=True)
    # Chemin d'un certificat (PEM) ou d'un dossier de certificats ajouté aux
    # autorités de confiance pour les connecteurs MCP uniquement.
    mcp_ssl_ca_bundle: str | None = Field(default=None)
    # Borne d'itérations du graphe LangGraph (``recursion_limit``). Passée
    # à chaque run AG-UI. 25 (défaut LangGraph) coupe un parcours outils
    # réaliste ; surchargeable via ``AGENT_MAX_ITERATIONS`` dans ``.env``.
    agent_max_iterations: int = Field(default=1000, ge=1, le=10_000)
    # Persistance LangGraph des threads d'agents (ADR 0009) : un PostgresSaver
    # partagé remplace le MemorySaver process (contexte conservé entre les
    # redémarrages). ``0`` force le fallback mémoire — la suite de tests le
    # pose pour ne jamais toucher la base réelle.
    agent_checkpoint_persistant: bool = Field(default=True)
    # Initialisation au démarrage (restauration de la configuration d'agents,
    # premier administrateur) : exécutée en tâche de fond pour que l'API réponde
    # sans attendre PostgreSQL. ``0`` la désactive (suite de tests, qui ne doit
    # jamais toucher la base réelle ni payer son délai de connexion).
    demarrage_initialise_runtime: bool = Field(default=True)
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
    # Nombre d'appels à ``langsearch_web_search`` autorisés **par tour** de
    # conversation (anti-boucle). Le prompt fixe les paliers utiles (8, 15, 25) ;
    # cette borne est le plafond exécutable, réglable sans redéploiement.
    web_search_max_calls_per_turn: int = Field(default=25, ge=1, le=50)
    # Nombre de requêtes distinctes acceptées par **un** appel de recherche :
    # regrouper les mots-clés évite la rafale d'appels quasi identiques.
    web_search_max_queries: int = Field(default=5, ge=1, le=10)
    # Mode texte officiel LangSearch (contents.text) — pas un scraping libre.
    web_search_text_max_chars: int = Field(default=3000, ge=200, le=5000)
    # Géographie (demande du 22/10) : recherche Photon et itinéraires
    # OpenRouteService. Aucune clé n'est exposée au frontend — la recherche et
    # le calcul passent par des routes du backend. ``heigit_base_url`` est
    # surchargeable : le certificat de ``api.heigit.org`` est expiré (22/10),
    # l'API officielle ORS reste atteignable via
    # ``https://api.openrouteservice.org`` avec la même clé et la même forme
    # de chemins (``/v2/directions/{profil}/geojson``).
    photon_base_url: str = Field(
        default="https://photon.komoot.io/api",
        description="Endpoint de recherche de lieux (API Photon, sans clé).",
    )
    photon_timeout_s: float = Field(default=8.0, ge=1, le=30)
    heigit_api_key: SecretStr | None = Field(default=None)
    heigit_base_url: str = Field(
        default="https://api.heigit.org/openrouteservice",
        description="Base des itinéraires OpenRouteService (chemins /v2/...).",
    )
    itineraires_timeout_s: float = Field(default=15.0, ge=1, le=60)
    #: Durée de validité d'un itinéraire en cache (quotas gratuits) : un
    #: résultat expiré est recalculé, jamais servi. Les coordonnées font partie
    #: de la clé, donc déplacer un marqueur invalide immédiatement le trajet.
    itineraires_cache_jours: int = Field(default=30, ge=1, le=365)
    # Tags OSM des voies (qualité des routes) — Overpass, service public.
    overpass_base_url: str = Field(
        default="https://overpass-api.de/api/interpreter",
        description="Endpoint Overpass pour les tags OSM des voies traversées.",
    )
    #: Overpass est un service public **saturé** (502 « at capacity », 30 s et
    #: plus par requête mesurés) : un délai trop court classerait tout en
    #: « qualité inconnue » alors que la réponse arrive. Un échec reste
    #: silencieux pour l'itinéraire (estimation par extras ORS).
    overpass_timeout_s: float = Field(default=45.0, ge=5, le=120)
    #: Durée de validité des tags d'une voie : OSM évolue, un itinéraire non.
    osm_tags_cache_jours: int = Field(default=30, ge=1, le=365)
    # Lecture d'une page web par un agent (tools ``fetch_url`` / ``read_urls``) :
    # bornes de service (anti-abus), pas des règles métier. Le texte rendu est un
    # **extrait** : un agent ne doit pas tirer une page entière dans le contexte.
    web_fetch_max_chars: int = Field(default=20_000, ge=200, le=200_000)
    #: Plafond d'octets lus sur une page : le corps est lu en flux et jamais
    #: entier — une adresse qui sert un fichier de plusieurs gigaoctets ne
    #: remplit pas la mémoire du serveur pour finir en « type non supporté ».
    web_fetch_max_bytes: int = Field(default=2_000_000, ge=1024, le=20_000_000)
    web_fetch_timeout_seconds: float = Field(default=15.0, gt=0, le=120)
    #: Nombre d'adresses lues en un appel ``read_urls``. Au-delà, le tool refuse
    #: explicitement (jamais une liste silencieusement tronquée).
    web_read_urls_max: int = Field(default=5, ge=1, le=8)
    #: Budget de caractères d'un appel ``read_urls``, toutes pages confondues.
    web_read_urls_total_chars: int = Field(default=60_000, ge=1_000, le=500_000)

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
        description="Racine des skills agents (vide = app/agents/skills, livrée avec le paquet).",
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
    # Durée de vie du jeton de session. Défaut validé : **un mois** (30 jours) —
    # surchargeable par AUTH_TOKEN_MINUTES dans .env sans toucher au code. Le
    # frontend décode l'``exp`` du jeton et déconnecte l'utilisateur à
    # l'expiration (retour automatique sur la page de connexion).
    auth_token_minutes: int = Field(
        default=30 * 24 * 60,
        ge=5,
        le=366 * 24 * 60,
        description="Durée de vie du JWT en minutes (AUTH_TOKEN_MINUTES, défaut 1 mois).",
    )
    bootstrap_admin_email: str | None = Field(default=None)
    bootstrap_admin_password: SecretStr | None = Field(default=None)

    # --- Envoi d'email (SMTP) — secrets exclusivement dans .env -------------
    # Utilisé par la réinitialisation de mot de passe (« mot de passe oublié »).
    # ``smtp_host`` absent : en développement l'email part sur la console
    # (journaux), en production l'envoi refuse explicitement — jamais un code
    # jeté silencieusement.
    smtp_host: str | None = Field(
        default=None, description="Serveur SMTP (SMTP_HOST). Absent ⇒ mode console en dev."
    )
    smtp_port: int = Field(default=587, ge=1, le=65535, description="Port SMTP (SMTP_PORT).")
    smtp_utilisateur: SecretStr | None = Field(
        default=None, description="Identifiant SMTP (SMTP_UTILISATEUR) — secret .env."
    )
    smtp_mot_de_passe: SecretStr | None = Field(
        default=None, description="Mot de passe SMTP (SMTP_MOT_DE_PASSE) — secret .env."
    )
    smtp_from: str | None = Field(
        default=None, description="Adresse émettrice (SMTP_FROM), ex. no-reply@carso.mg."
    )
    smtp_tls: bool = Field(default=True, description="STARTTLS activé (SMTP_TLS).")
    # Plafond **total** des pièces jointes d'un email sortant (borne de service,
    # pas une règle métier) : plusieurs documents peuvent accompagner un message,
    # un envoi trop lourd est refusé explicitement, jamais tronqué en silence.
    email_pieces_jointes_max_octets: int = Field(
        default=10 * 1024 * 1024,
        ge=1,
        description="Poids total max des pièces jointes (EMAIL_PIECES_JOINTES_MAX_OCTETS).",
    )

    # --- Mot de passe oublié (code à usage unique envoyé par email) ---------
    mot_de_passe_oublie_code_minutes: int = Field(
        default=15,
        ge=1,
        le=120,
        description="Durée de vie du code en minutes (MOT_DE_PASSE_OUBLIE_CODE_MINUTES).",
    )
    mot_de_passe_oublie_tentatives_max: int = Field(
        default=5,
        ge=1,
        le=20,
        description="Essais max par code (MOT_DE_PASSE_OUBLIE_TENTATIVES_MAX).",
    )
    mot_de_passe_oublie_delai_secondes: int = Field(
        default=60,
        ge=0,
        le=3600,
        description="Délai entre deux demandes de code (MOT_DE_PASSE_OUBLIE_DELAI_SECONDES).",
    )


@lru_cache
def get_settings() -> Settings:
    """Return the cached Settings singleton."""
    return Settings()
