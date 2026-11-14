from pathlib import Path

# remonter quelques niveaux et chercher le fichier
courant = Path(".").resolve()
print("cwd:", courant)
niveau = courant
for i in range(4):
    parent = niveau.parent
    print("parent", i+1, ":", parent)
    niveau = parent

# chercher dans les 2 premiers parents (borné)
for base in [courant.parent, courant.parent.parent]:
    try:
        entrees = list(base.iterdir())
        print("contenu de", base, ":", [e.name for e in entrees][:40])
    except Exception as e:
        print("erreur lecture", base, e)
