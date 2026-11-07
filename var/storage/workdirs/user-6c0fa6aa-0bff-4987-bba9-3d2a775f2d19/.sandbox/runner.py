
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
