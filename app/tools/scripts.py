"""Exécution contrôlée de scripts Python (Lot T1, clinrules 08 §Shell).

Ce n'est **pas** un shell libre. L'agent fournit le *source* ; le tool vérifie
les imports (AST, deny by default) et refuse les motifs d'exploitation
(serveur, processus, services, secrets — ``verifier_script_sur``), l'écrit dans
l'**espace de travail du compte**, l'exécute via ``sys.executable -I`` sous une
garde d'audit, borne timeout/sortie et journalise.

INC-116 — l'exécution est désormais **par compte** :

```text
<storage_root>/../workdirs/user-<uuid>/   (backend/var/storage/workdirs)
├── .scripts/<nom>_<8hex>.py     script conservé (relançable, lisible)
├── .sandbox/runner.py           garde d'audit (générée)
└── …                            fichiers produits par l'agent
```

La garde d'audit (``sys.addaudithook``) est la barrière réelle : l'allowlist
d'imports empêche ``os``/``subprocess``/``socket``, mais ``pathlib`` permettrait
d'écrire n'importe où. Le lanceur refuse donc :

- toute **écriture**, suppression, renommage ou changement de permissions hors
  de l'espace du compte ;
- toute **lecture** hors de l'espace, du stockage documentaire et de
  l'installation Python (imports) — un script d'agent ne lit pas un secret ;
- le réseau, les sous-processus, ``os.system`` et ``ctypes``.

Aucun espace partagé de repli : sans compte identifié dans le contexte du run,
l'appel est refusé (message explicite).
"""

from __future__ import annotations

import ast
import io
import os
import re
import subprocess
import sys
import unicodedata
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, model_validator

from app.application.dto import DocumentUploadInput
from app.application.services.document_service import CHAMP_PAR_ANCRE
from app.core.errors import CarsoError, PermissionDeniedError, ValidationError
from app.tools.permissions import AgentIdentity, PermissionPolicy
from app.tools.workspace import (
    dossier_temporaire,
    espace_utilisateur,
    lister_fichiers,
    nom_espace,
    purger_temporaires,
    supprimer_temporaire,
)

__all__ = [
    "LivrableInput",
    "RunPythonScriptInput",
    "TypeAncreScript",
    "build_run_python_script",
    "racine_scripts",
    "verifier_imports",
    "verifier_script_sur",
]

#: Vocabulaire **fermé** des ancres acceptées par un script (INC-124).
TypeAncreScript = Literal[
    "organisation",
    "appel",
    "offre",
    "mission",
    "equipe",
    "session",
]

#: Livrables déposés par un seul appel : au-delà, l'agent groupe son travail.
MAX_LIVRABLES = 10

#: Bibliothèque standard et paquets connus (liste indicative, montrée dans le
#: refus) : tout ce qui pilote le système (``os``, ``subprocess``, ``socket``…)
#: reste refusé, et la garde d'exécution en reste seule juge.
#:
#: Imports interdits A L'EXECUTION (voir ``_garde``) : meme importes par une
#: bibliotheque, ils ne s'ouvrent pas.
INTERDITS = frozenset(
    {
        "os",
        "sys",
        "subprocess",
        "shutil",
        "socket",
        "http",
        "urllib",
        "ctypes",
        "multiprocessing",
        "importlib",
        "builtins",
    }
)

_IMPORTS_AUTORISES = frozenset(
    {
        "abc",
        "base64",
        "calendar",
        "collections",
        "copy",
        "csv",
        "dataclasses",
        "datetime",
        "decimal",
        "difflib",
        "enum",
        "fractions",
        "functools",
        "hashlib",
        "io",
        "itertools",
        "json",
        "math",
        "pathlib",
        "random",
        "re",
        "statistics",
        "string",
        "textwrap",
        "typing",
        "unicodedata",
        "uuid",
        "zipfile",
        "docx",
        "openpyxl",
        "pypdf",
        "fpdf",
        "fpdf2",
    }
)
_IMPORTS_INTERDITS = INTERDITS

#: Modules de mise en reseau refuses DANS LE SOURCE (message lisible).
#: La garde d'audit les refuse de toute facon a l'execution : cette table
#: sert a refuser a l'ecriture, pas a la place de la garde.
_MODULES_RESEAU = frozenset(
    {
        "requests",
        "httpx",
        "aiohttp",
        "urllib3",
        "httpcore",
        "websocket",
        "websockets",
        "ftplib",
        "smtplib",
        "telnetlib",
        "asyncio",
        "ssl",
    }
)

#: Nombre maximal de caractères du nom de script conservé.
_NOM_MAX = 40

#: Dossier des aides importables, sous l'espace du compte.
NOM_AIDES = "carso_outils"

#: Exploitation de l'application : arrêter ou redémarrer le serveur (uvicorn,
#: serveur de dev, service système, processus du backend) n'est jamais un
#: traitement documentaire. Les imports d'``os``/``subprocess`` sont déjà
#: refusés par l'allowlist ; cette garde vise la source pour offrir un refus
#: **explicite** plutôt qu'une erreur d'import au milieu du script.
_MOTIFS_REFUSES: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"\b(uvicorn|gunicorn|hypercorn|celery|procfile)\b", re.IGNORECASE),
        "démarrer ou piloter un serveur d'application",
    ),
    (
        re.compile(
            r"\b(kill|pkill|killall|taskkill|systemctl|service\s+(carso|restart|stop"
            r"|start)|sc\s+(stop|start|delete)|supervisorctl|nssm|pm2|launchctl)\b",
            re.IGNORECASE,
        ),
        "arrêter un processus ou un service",
    ),
    (
        re.compile(
            r"ssh\s+\S+|invoke-command|enter-pssession|winrs|schtask|start-process",
            re.IGNORECASE,
        ),
        "piloter un autre programme ou une autre machine",
    ),
    (
        re.compile(r"\b(shutdown|restart[-_ ]?(computer|service)|reboot)\b", re.IGNORECASE),
        "redémarrer la machine",
    ),
    (
        re.compile(r"\bgit\s+(push|reset|clean|checkout)\b", re.IGNORECASE),
        "modifier un dépôt de code",
    ),
    (
        re.compile(
            r"[\"'](?:[^\"']*[/\\])?\.(?:env|dev\.vars)[\"']",
            re.IGNORECASE,
        ),
        "lire un fichier de secrets",
    ),
)


class LivrableInput(BaseModel):
    """Un fichier produit par le script et à déposer comme **document** (INC-124)."""

    fichier: str = Field(
        min_length=1,
        max_length=300,
        description=(
            "Nom **relatif** du fichier dans le dossier de travail (ex. "
            "'offre_technique.docx' ou 'sorties/annexe.pdf'). Un chemin absolu "
            "ou une remontée ('..') est refusé."
        ),
    )
    type_document: str = Field(
        min_length=1,
        max_length=60,
        description=(
            "Type de dépôt (vocabulaire FERMÉ : 'offre', 'rapport', "
            "'fiche_technique', 'xlsx'→'budget', 'template', 'autre'…). "
            "Jamais deviné : un type inconnu fait refuser ce livrable, avec la "
            "liste des types valides."
        ),
    )
    nom: str | None = Field(
        default=None,
        max_length=255,
        description=(
            "Nom logique du document en base (avec extension). Sans nom, le "
            "nom du fichier est repris."
        ),
    )


class RunPythonScriptInput(BaseModel):
    """Source Python à exécuter, dans l'espace du compte **ou** près d'un objet."""

    source: str = Field(min_length=1, description="Code Python (UTF-8).")
    nom: str | None = Field(
        default=None,
        max_length=80,
        description=(
            "Nom court du script (conservé dans l'espace de travail). "
            "Sans nom, « script » est utilisé."
        ),
    )
    ancre: TypeAncreScript | None = Field(
        default=None,
        description=(
            "Rattachement de l'exécution : 'organisation' | 'appel' | 'offre' | "
            "'mission' | 'equipe' | 'session'. Avec une ancre, le script écrit "
            "dans un dossier de travail créé sous le dossier métier (supprimé "
            "ensuite) et lit ce dossier plus les modèles de l'organisation. "
            "Omettre pour l'espace de travail personnel."
        ),
    )
    entity_id: UUID | None = Field(
        default=None,
        description=(
            "UUID de l'objet métier, requis avec ``ancre`` — jamais un "
            "identifiant inventé."
        ),
    )
    livrables: list[LivrableInput] | None = Field(
        default=None,
        max_length=MAX_LIVRABLES,
        description=(
            "Fichiers produits à enregistrer comme documents rattachés à "
            "l'ancre, en statut 'proposed'. Seuls les fichiers DÉCLARÉS sont "
            "déposés ; les autres restent dans le dossier de travail, qui est "
            "supprimé. Même nom + même type + même ancre ⇒ nouvelle version."
        ),
    )

    @model_validator(mode="after")
    def _coherence_ancre(self) -> RunPythonScriptInput:
        """Ancre et identifiant vont ensemble ; les livrables exigent l'ancre."""
        if (self.ancre is None) != (self.entity_id is None):
            raise ValueError(
                "'ancre' et 'entity_id' vont ensemble : donner les deux "
                "(exécution ancrée) ou aucun des deux (espace personnel)."
            )
        if self.livrables and self.ancre is None:
            raise ValueError(
                "'livrables' exige 'ancre' : un document doit être rattaché à un "
                "objet métier. Sans ancre, utiliser les tools de génération "
                "(generer_document, generer_document_professionnel…)."
            )
        return self


# --------------------------------------------------------------------------- garde
#: Lanceur écrit dans ``<espace>/.sandbox/runner.py``. Il n'importe **rien** du
#: paquet CARSO : il doit rester valable même si le code évolue, et il est lu
#: par un interpréteur ``-I`` (aucun chemin du projet dans ``sys.path``).
_RUNNER = '''
"""Garde d'audit d'un script d'agent CARSO (généré, ne pas modifier)."""
import os
import runpy
import sys

SCRIPT = sys.argv[1]
ESPACE = os.path.realpath(sys.argv[2])
LECTURE = [ESPACE] + [os.path.realpath(p) for p in sys.argv[3:] if p]
sys.dont_write_bytecode = True

#: Temporaires systeme : les binaires de l'interprete et les fichiers de
#: travail d'une bibliotheque (openpyxl ecrit un .xlsx temporaire) s'y trouvent.
#: Ecriture toujours refusee ; lecture admise, sauf secrets et code applicatif.
TEMP_DIRS = [os.path.realpath(t) for t in (__import__('tempfile').gettempdir(),) if t]
SYSTEME = [os.environ.get("SYSTEMROOT") or "C:" + os.sep + "Windows"]

RE = __import__('re')
RE_SEPARATEURS = RE.compile(r'[\/]+')


INTERDITS = ()
#: `re` n'est pas importe par le lanceur : il se charge ici, AVANT la
#: pose de l'audit hook (les imports de Python lui-meme restent libres).


def _norm(chemin):
    return os.path.normcase(os.path.realpath(os.fspath(chemin)))


def _sous(chemin, racines):
    cible = _norm(chemin)
    for racine in racines:
        base = _norm(racine)
        if cible == base or cible.startswith(base + os.sep):
            return True
    return False


def _refus(message):
    raise PermissionError(message)


#: Fichiers jamais lisibles par un script, MEME sous une racine autorisee :
#: secrets et code applicatif CARSO (AGENTS.md 7 et 9). Un ``open`` de
#: bibliotheque reste normal ; lire ``.env`` ou le paquet ``app`` n'en est pas un.
_SECRETS_INTERDITS = ("[.]env", "[.]dev[.]vars", "[.]streamlit")
_DOSSIERS_INTERDITS = ("app", "tests", ".venv", "site-packages")



def _fichier_interdit(chemin):
    """Vrai si le fichier est un secret ou du code applicatif."""
    try:
        texte = os.path.normcase(os.path.realpath(os.fspath(chemin)))
    except (TypeError, ValueError):
        return True
    nom = os.path.basename(texte)
    for motif in _SECRETS_INTERDITS:
        if RE.search(motif + "$", nom):
            return True
    morceaux = RE_SEPARATEURS.split(texte)
    return any(m in _DOSSIERS_INTERDITS for m in morceaux[:-1])


def _garde(event, args):
    if event == "open":
        chemin, mode, _drapeaux = args
        if isinstance(chemin, int):
            return
        ecriture = bool(mode) and any(c in mode for c in "wax+")
        if ecriture:
            # Une ecriture n'a lieu que dans l'espace du compte.
            if not _sous(chemin, [ESPACE]):
                _refus("Ecriture refusee hors de l'espace de travail : %s" % (chemin,))
        elif not _sous(chemin, LECTURE) and not _sous(chemin, TEMP_DIRS):
            _refus("Acces refuse hors de l'espace de travail : %s" % (chemin,))
        if _fichier_interdit(chemin):
            _refus("Acces refuse a un fichier protege : %s" % (chemin,))
    elif event in ("os.remove", "os.rmdir", "os.mkdir", "os.chmod", "os.truncate", "os.utime"):
        chemin = args[0] if args else None
        if isinstance(chemin, (str, bytes, os.PathLike)) and not _sous(chemin, [ESPACE]):
            _refus("Operation refusee hors de l'espace de travail : %s" % (chemin,))
    elif event in ("os.rename", "os.replace", "os.link", "os.symlink"):
        for chemin in args[:2]:
            if isinstance(chemin, (str, bytes, os.PathLike)) and not _sous(chemin, [ESPACE]):
                _refus("Operation refusee hors de l'espace de travail : %s" % (chemin,))
    elif event in (
        "subprocess.Popen",
        "os.system",
        "os.spawn",
        "os.exec",
        "os.posix_spawn",
        "os.startfile",
        "socket.connect",
        "socket.bind",
        "socket.getaddrinfo",
        "socket.gethostbyname",
        "urllib.Request",
        "http.client.connect",
        "ftplib.connect",
        "smtplib.connect",
    ):
        _refus("Operation interdite dans un script d'agent : %s" % event)
    elif event == "ctypes.dlopen":
        # openpyxl a besoin du zip natif de Windows (``zipfile`` s'appuie sur
        # ``shutil``). Le chargement reste refuse : il ouvre les DLL de l'hote
        # (sqlite3, ssl du serveur), ce qu'un script de document n'a pas a
        # manipuler. ``zipfile`` reste utilisable pour les fichiers SANS
        # compression ; c'est annonce dans la description du tool.
        _refus(
            "Chargement de bibliotheque native refuse (openpyxl et ctypes sont bloques) : "
            "pour un classeur, importe l'aide 'classeur' de ton espace de travail"
        )
    elif event == "ctypes.dlsym":
        # Resolution d'un symbole dans une DLL deja chargee : openpyxl s'en sert
        # (zip natif de Windows). L'adresse seule n'ouvre aucun fichier.
        if not args or not _sous(os.fspath(args[0]), SYSTEME):
            _refus("Resolution de symbole refusee : %s" % (args[0] if args else "?",))


sys.addaudithook(_garde)
sys.argv = [SCRIPT]
try:
    runpy.run_path(SCRIPT, run_name="__main__")
except PermissionError:
    raise
except ModuleNotFoundError as erreur:
    # Un module absent remontait une trace VIDE (-- par defaut) : l'agent ne
    # pouvait pas savoir ce qui manquait pour corriger son script.
    import traceback
    traceback.print_exc()
    sys.exit("Module introuvable : %s" % (erreur.name or "?",))
except Exception:
    import traceback
    traceback.print_exc()
    sys.exit(1)
'''


# --------------------------------------------------------------------------- imports
def _modules_importes(source: str) -> set[str]:
    """Noms de modules de premier niveau importés (AST, pas d'exécution)."""
    try:
        arbre = ast.parse(source)
    except SyntaxError as exc:
        raise ValidationError(
            f"Script Python syntaxiquement invalide : {exc.msg}",
            details={"lineno": exc.lineno},
        ) from exc
    modules: set[str] = set()
    for noeud in ast.walk(arbre):
        if isinstance(noeud, ast.Import):
            for alias in noeud.names:
                modules.add(alias.name.split(".", 1)[0])
        elif isinstance(noeud, ast.ImportFrom):
            if noeud.level and noeud.level > 0:
                raise PermissionDeniedError(
                    "Import relatif interdit dans un script d'agent"
                )
            if noeud.module:
                modules.add(noeud.module.split(".", 1)[0])
    return modules


def verifier_imports(source: str) -> None:
    """Refuse les imports d'exploitation ; **hors ligne**, les autres passent.

    Le script est un outil d execution, pas une selection de modules : un agent
    qui a besoin de ``pandas`` ou de ``lxml`` pour traiter un fichier doit
    pouvoir l'importer.

    Ce qui reste refuse dans le **source** : les modules de ``INTERDITS``, et
    les modules de mise en reseau (``requests``, ``urllib3``, ``httpx``) : un
    script de document ne joint jamais un service, et laisser passer la
    bibliotheque pour voir la garde la refuser au fond d'une pile d'appels
    serait un refus illisible. Les autres imports restent bornes par la garde
    d'audit : ni reseau, ni sous-processus, ni ecriture hors de l'espace du
    compte, ni lecture d'un secret.
    """
    for module in sorted(_modules_importes(source)):
        if module in _IMPORTS_INTERDITS:
            raise PermissionDeniedError(
                f"Import {module!r} interdit dans un script d'agent",
                details={"module": module, "autorises": sorted(_IMPORTS_AUTORISES)},
            )
        if module in _MODULES_RESEAU:
            raise PermissionDeniedError(
                f"Import {module!r} refuse : un script d'agent n'accede pas au "
                "reseau",
                details={"module": module, "motif": "reseau"},
            )


def verifier_script_sur(source: str) -> None:
    """Refuse un script qui touche à l'exploitation (serveur, processus, secrets).

    Ce contrôle s'ajoute à l'allowlist d'imports et à la garde d'exécution : il
    ne remplace ni l'une ni l'autre (un détour reste bloqué par la garde), il
    donne un refus lisible — « commande interdite » — au lieu d'une erreur
    technique au milieu du script.

    Raises:
        PermissionDeniedError: motif d'exploitation détecté, avec le motif en
            toutes lettres pour que l'agent explique le refus au compte.
    """
    for motif, raison in _MOTIFS_REFUSES:
        trouve = motif.search(source)
        if trouve:
            raise PermissionDeniedError(
                "Script refusé : il cherche à " + raison + " "
                f"(« {trouve.group(0).strip()} »). Un script d'agent traite des "
                "données dans son espace de travail ; arrêter, redémarrer ou "
                "piloter le serveur, un service ou un autre programme n'est pas "
                "une opération autorisée.",
                details={"motif": trouve.group(0).strip(), "raison": raison},
            )


# --------------------------------------------------------------------------- chemins
def racine_scripts(settings: Any) -> Path:
    """Racine historique des scripts (conservée pour la compatibilité).

    L'exécution d'agent, elle, se fait dans l'espace du compte : voir
    ``espace_utilisateur``.
    """
    brut = getattr(settings, "scripts_root", "") or ""
    if str(brut).strip():
        return Path(str(brut)).expanduser().resolve()
    storage = Path(getattr(settings, "storage_root", "/storage/carso"))
    return (storage / "_scripts").resolve()


def _nom_fichier(nom: str | None) -> str:
    """Nom de script sûr : ASCII, sans séparateur, borné (jamais un chemin)."""
    brut = (nom or "script").strip() or "script"
    normalise = unicodedata.normalize("NFKD", brut).encode("ascii", "ignore").decode()
    normalise = re.sub(r"[^A-Za-z0-9_-]+", "_", normalise).strip("_")
    return (normalise or "script")[:_NOM_MAX]


def _ecrire_runner(espace: Path) -> Path:
    """Écrit (ou met à jour) la garde d'audit dans ``.sandbox/runner.py``."""
    dossier = espace / ".sandbox"
    dossier.mkdir(parents=True, exist_ok=True)
    runner = dossier / "runner.py"
    if not runner.exists() or runner.read_text(encoding="utf-8") != _RUNNER:
        runner.write_text(_RUNNER, encoding="utf-8")
    return runner


def _ecrire_aides(espace: Path) -> Path:
    """Copie les aides Python dans ``<espace>/carso_outils/`` (importables).

    Une aide n'importe rien de tiers : ``openpyxl`` est bloque par la garde,
    mais un classeur d'une table s'ecrit avec ``zipfile`` seul.
    """
    from app.tools import aides_agent

    dossier = espace / NOM_AIDES
    dossier.mkdir(parents=True, exist_ok=True)
    for source in sorted(aides_agent.__path__):
        for fichier in Path(source).glob("*.py"):
            cible = dossier / fichier.name
            contenu = fichier.read_text(encoding="utf-8")
            if not cible.exists() or cible.read_text(encoding="utf-8") != contenu:
                cible.write_text(contenu, encoding="utf-8")
    return dossier


def _racines_lecture(settings: Any, espace: Path) -> list[str]:
    """Chemins lisibles par le script en plus de son espace de travail.

    L'installation Python : ``sys.path`` entier (stdlib, ``site-packages``,
    dossiers utilisateur) **et** ``sysconfig`` — sur Windows, l'import d'un
    module installé commence par lire ``Lib/pkgutil.py`` de la distribution
    système : une liste réduite à ``sys.prefix`` rendait tout import de paquet
    (``openpyxl``, ``docx``, ``csv`` compris) impossible — « Accès refusé hors
    de l'espace de travail ».
    """
    import sysconfig

    chemins_python = [
        sys.prefix,
        sys.base_prefix,
        str(Path(sys.executable).resolve().parent),
        *sysconfig.get_paths().values(),
        *[chemin for chemin in sys.path if chemin],
    ]
    racines = [str(espace), *chemins_python]
    vus: set[str] = set()
    uniques: list[str] = []
    for racine in racines:
        if racine and racine not in vus:
            vus.add(racine)
            uniques.append(racine)
    return uniques


# --------------------------------------------------------------------------- livrables
def _fichier_du_temporaire(temporaire: Path, nom: str) -> Path:
    """Résout un **nom relatif** dans le dossier de travail, sans sortir de lui.

    Un livrable est désigné par un nom relatif (``offre.docx``,
    ``sorties/annexe.pdf``). Un chemin absolu, une remontée ``..``, un lecteur
    Windows ou un segment vide sont refusés : le dossier de travail est la seule
    zone que ce tool accepte de lire pour déposer.

    Raises:
        ValidationError: nom vide, absolu, remontant, ou hors du dossier après
            résolution (lien symbolique).
    """
    brut = str(nom or "").replace("\\", "/").strip()
    segments = [segment for segment in brut.split("/") if segment]
    if (
        not brut
        or brut.startswith("/")
        or ":" in brut.split("/")[0]
        or ".." in segments
        or not segments
    ):
        raise ValidationError(
            f"Nom de livrable invalide : {nom!r} (nom relatif attendu, par "
            "exemple 'sortie.docx' ou 'sorties/annexe.pdf')"
        )
    racine = Path(temporaire).resolve()
    chemin = (racine / "/".join(segments)).resolve()
    if not chemin.is_relative_to(racine):
        raise ValidationError(f"Nom de livrable hors du dossier de travail : {nom!r}")
    return chemin


def _uuid_ou_refus(valeur: Any) -> UUID:
    """Identifiant d'ancre : ``UUID`` déjà validé, ou texte à convertir.

    Le schéma type ``entity_id`` en ``UUID`` ; le handler reste tolérant à un
    texte (appel direct, test, chemin déterministe) et refuse le reste avec un
    message explicite plutôt qu'une erreur SQL au fond du service.
    """
    if isinstance(valeur, UUID):
        return valeur
    try:
        return UUID(str(valeur))
    except (TypeError, ValueError, AttributeError) as erreur:
        raise ValidationError("entity_id n'est pas un UUID valide") from erreur


def _deposer_livrables(
    service: Any,
    *,
    ancre: str,
    entity_id: Any,
    livrables: list[Any],
    temporaire: Path,
    code_retour: int,
    agent_id: str,
) -> dict[str, Any]:
    """Dépose les livrables **déclarés** du dossier de travail (INC-124).

    Un refus n'annule pas les autres : chaque fichier est traité séparément et
    la cause est rendue telle quelle (type inconnu, fichier absent, contenu
    refusé par la validation documentaire). Un script terminé en erreur ne
    dépose **rien** : un fichier interrompu n'entre pas en base.

    Returns:
        ``{"documents": [...], "refuses": [...]}`` — jamais de chemin disque.
    """
    if code_retour != 0:
        return {
            "documents": [],
            "refuses": [
                {
                    "fichier": "*",
                    "cause": (
                        f"script terminé en erreur (code {code_retour}) : aucun "
                        "livrable déposé — corriger le script puis relancer"
                    ),
                }
            ],
        }

    champ = CHAMP_PAR_ANCRE[ancre]
    documents: list[dict[str, Any]] = []
    refuses: list[dict[str, Any]] = []
    for brut in livrables:
        entree = brut if isinstance(brut, dict) else brut.model_dump()
        fichier = str(entree.get("fichier") or "").strip()
        try:
            # Le dossier de travail est la seule zone lue ici : un nom absolu ou
            # remontant est refusé avant toute lecture.
            chemin = _fichier_du_temporaire(temporaire, fichier)
            if not chemin.is_file():
                raise ValidationError(
                    f"Fichier {fichier!r} absent du dossier de travail"
                )
            octets = chemin.read_bytes()
            if not octets:
                raise ValidationError(f"Fichier {fichier!r} vide")
            document = service.deposer_livrable(
                DocumentUploadInput(
                    type_document=str(entree.get("type_document") or ""),
                    nom=str(entree.get("nom") or chemin.name),
                    stream=io.BytesIO(octets),
                    created_by=agent_id,
                    proposed_by_agent=agent_id,
                    **{champ: entity_id},
                )
            )
        except CarsoError as refus:
            refuses.append({"fichier": fichier, "cause": str(refus)})
            continue
        documents.append(
            {
                "document_id": str(document.id),
                "nom": document.nom,
                "type_document": document.type_document,
                "version": document.version,
                "statut": document.statut,
                "taille_octets": document.taille_octets,
            }
        )
    return {"documents": documents, "refuses": refuses}


# --------------------------------------------------------------------------- tool
def build_run_python_script(
    *,
    identite: AgentIdentity,
    policy: PermissionPolicy,
    settings: Any,
    audit: Any | None = None,
    service_factory: Callable[[], Any] | None = None,
) -> Any:
    """Construit le tool ``run_python_script`` pour UN agent.

    L'espace de travail est résolu **à chaque appel** depuis le contexte du run
    (``app.agents.contexte``) : un graphe n'est pas lié à un compte, mais un
    appel l'est. Sans compte identifié, l'appel est refusé.

    Args:
        service_factory: fabrique de ``DocumentService`` (session de l'appel).
            Requise pour l'**exécution ancrée** (INC-124) : c'est le service qui
            résout le dossier métier, vérifie le périmètre et dépose les
            livrables. Sans elle, le mode personnel reste seul disponible et
            une demande ancrée est refusée explicitement.
    """
    timeout_s = int(settings.scripts_timeout_seconds)
    max_octets = int(settings.scripts_max_output_bytes)
    max_source = int(settings.scripts_max_source_chars)
    confiance = str(getattr(settings, "scripts_confiance", "eleve"))
    tourne_eleve = confiance == "eleve"

    def _tracer(action: str, details: dict[str, Any]) -> None:
        if audit is not None:
            audit(action, {"agent_id": identite.agent_id, **details})

    def _handler(payload: dict[str, Any]) -> dict[str, Any]:
        policy.require(identite.agent_id, "script_python")

        from app.agents.contexte import contexte_utilisateur_effectif

        contexte = contexte_utilisateur_effectif()
        if contexte is None or not contexte.utilisateur_id:
            raise PermissionDeniedError(
                "run_python_script exige un compte identifié : aucun espace de "
                "travail ne peut être déterminé pour cet appel.",
                details={"agent_id": identite.agent_id},
            )
        logique = nom_espace(contexte.utilisateur_id)

        # Tout ce qui peut refuser l'appel est vérifié **avant** de toucher au
        # disque : un script refusé ne doit pas laisser de dossier, ni de trace
        # dans l'espace du compte.
        source = payload["source"]
        if len(source) > max_source:
            raise ValidationError(
                f"Script trop long ({len(source)} > {max_source} caractères)"
            )
        verifier_imports(source)
        verifier_script_sur(source)
        espace = espace_utilisateur(settings, contexte.utilisateur_id)

        ancre = str(payload.get("ancre") or "").strip().lower() or None
        entity_id = _uuid_ou_refus(payload.get("entity_id")) if ancre else None
        livrables = list(payload.get("livrables") or [])
        service: Any | None = None
        dossier_metier: Path | None = None
        lectures: list[Path] = []
        if ancre is not None:
            if service_factory is None:
                raise ValidationError(
                    "Exécution ancrée indisponible : le service documentaire "
                    "n'est pas monté pour cet agent. Exécuter le script dans "
                    "l'espace personnel, ou générer le livrable avec les tools "
                    "de génération."
                )
            service = service_factory()
            # Résolution **avant** toute écriture : une ancre inconnue ou hors
            # périmètre ne doit laisser aucun dossier derrière elle.
            dossier_metier = service.dossier_de_l_ancre(ancre, entity_id)
            lectures.append(dossier_metier)
            dossier_organisation = service.dossier_organisation_de_l_ancre(
                ancre, entity_id
            )
            if dossier_organisation is not None and dossier_organisation != dossier_metier:
                lectures.append(dossier_organisation)

        nom = _nom_fichier(payload.get("nom"))
        chemin = espace / ".scripts" / f"{nom}_{uuid4().hex[:8]}.py"
        chemin.write_text(source, encoding="utf-8")
        runner = _ecrire_runner(espace)
        _ecrire_aides(espace)
        relatif = str(chemin.relative_to(espace)).replace("\\", "/")

        temporaire: Path | None = None
        purges = 0
        if dossier_metier is not None:
            temporaire = dossier_temporaire(dossier_metier)
            # Un appel interrompu (délai, redémarrage) laisse son dossier : le
            # suivant le nettoie, sans toucher à celui de l'exécution en cours.
            purges = purger_temporaires(dossier_metier, garder=temporaire)
            ecriture = temporaire
        else:
            ecriture = espace
        _tracer(
            "script.exec",
            {
                "nom": nom,
                "taille": len(source),
                "espace": logique,
                "ancre": ancre,
                "entity_id": str(entity_id) if entity_id else None,
            },
        )

        env = dict(os.environ)
        # Aides et modules du compte : en tete du chemin, sans quoi un script
        # ne pourrait importer ni ``classeur`` ni un paquet installe.
        env["PYTHONPATH"] = os.pathsep.join(
            [str(espace / NOM_AIDES), *(
                [env["PYTHONPATH"]] if env.get("PYTHONPATH") else []
            )]
        )
        env["PYTHONIOENCODING"] = "utf-8"
        # Racines de LECTURE : l'espace du compte (script, aides, fichiers
        # personnels), le dossier de travail en cours, le dossier métier et les
        # modèles de l'organisation quand l'appel est ancré.
        racines = _racines_lecture(settings, espace)
        for extra in (ecriture, *lectures):
            valeur = str(extra)
            if valeur not in racines:
                racines.append(valeur)

        resultat: dict[str, Any] = {}
        try:
            completed = subprocess.run(  # noqa: S603
                [
                    sys.executable,
                    "-s",
                    "-B",
                    str(runner),
                    str(chemin),
                    str(ecriture),
                    *racines,
                ],
                cwd=ecriture,
                timeout=timeout_s,
                capture_output=True,
                check=False,
                env=env,
            )
        except subprocess.TimeoutExpired:
            _tracer("script.timeout", {"nom": nom, "espace": logique})
            resultat = {
                "code_retour": 124,
                "stdout": "",
                "stderr": f"Script interrompu après {timeout_s}s",
                "timeout": True,
                "espace": logique,
                "script": relatif,
                "fichiers": lister_fichiers(ecriture),
            }
        else:
            resultat = {
                "code_retour": completed.returncode,
                "stdout": completed.stdout[:max_octets].decode("utf-8", errors="replace"),
                "stderr": completed.stderr[:max_octets].decode("utf-8", errors="replace"),
                "timeout": False,
                "espace": logique,
                "script": relatif,
                "fichiers": lister_fichiers(ecriture),
            }
            if temporaire is not None:
                resultat.update(
                    _deposer_livrables(
                        service,
                        ancre=str(ancre),
                        entity_id=entity_id,
                        livrables=livrables,
                        temporaire=temporaire,
                        code_retour=completed.returncode,
                        agent_id=identite.agent_id,
                    )
                )
        finally:
            if temporaire is not None:
                # Le dossier de travail ne survit jamais à l'appel : les fichiers
                # non déclarés n'ont pas à traîner dans le stockage officiel.
                supprime = supprimer_temporaire(temporaire)
                resultat["temporaire"] = "supprimé" if supprime else "non supprimé"
                resultat["ancre"] = ancre
                if purges:
                    resultat["temporaires_purges"] = purges
        return resultat

    from app.tools.base import TypedTool

    if tourne_eleve:
        perimetre = (
            "Niveau de confiance 'élevé' : tu peux écrire un utilitaire complet "
            "— créer les sous-dossiers de ton espace (``pathlib``), écrire, "
            "renommer, supprimer **dans** ton espace, analyser, formater, "
            "sortir un rapport."
        )
    else:
        perimetre = (
            "Niveau de confiance 'standard' : limite le script à la mise en "
            "forme et à l'analyse de données (lire, calculer, écrire un fichier "
            "de résultat dans ton espace) ; les utilitaires d'exploitation ne "
            "sont pas autorisés."
        )

    return TypedTool(
        name="run_python_script",
        description=(
            "Exécute un script Python dans TON espace de travail personnel "
            "(workdirs/user-<compte>), où les fichiers produits sont conservés, "
            "OU près d'un objet métier avec ``ancre`` + ``entity_id`` : le "
            "script tourne alors dans un dossier de travail créé sous le "
            "dossier de l'objet (supprimé à la fin), lit ce dossier et les "
            "modèles de l'organisation, et chaque fichier de ``livrables`` est "
            "déposé en position « proposé », rattaché à l'objet (même nom + même "
            "type + même ancre ⇒ nouvelle version). C'est le seul moyen de "
            "produire un livrable par script et de le rendre visible de "
            "l'utilisateur. Détail : "
            "c'est l'outil des traitements longs ou répétitifs (consolider "
            "plusieurs classeurs, nettoyer une liste, calculer un budget, "
            "générer un DOCX/XLSX en série). Écris le script complet en une "
            f"fois, lis stdout, puis réutilise les fichiers créés. {perimetre} "
            f"Budget : {max_source} caractères de source, {timeout_s}s "
            "d'exécution. Refusé avec un message explicite : arrêter ou "
            "redémarrer le serveur, piloter un service ou un autre programme, "
            "accéder au réseau, lire un fichier de secrets, écrire hors de ton "
            "espace. "
            "MODULES : les paquets installés (pandas, docx, pypdf…) sont "
            "importables ; restent refusés os, sys, subprocess, socket, "
            "ctypes et les bibliothèques réseau. openpyxl est bloqué : pour un "
            "classeur Excel, utilise l'aide fournie — import classeur ; "
            "classeur.ecrire_classeur('liste.xlsx', 'Bénéficiaires', lignes, "
            "titre='Liste') — ou, pour un livrable stylé, "
            "``generer_document_professionnel`` avec format='xlsx'."
        ),
        handler=_handler,
        input_schema=RunPythonScriptInput,
        tags=("scripts", "documents", "espace_travail"),
    )
