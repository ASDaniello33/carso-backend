from pathlib import Path

# Chemin absolu possible du fichier joint (d'après un script antérieur)
candidats = [
    Path("C:/Users/Asus/Documents/CARSO_AI/backend/var/storage/carso/workdir/6c0fa6aa-0bff-4987-bba9-3d2a775f2d19/cebfac35-dcd9-430d-834c-8e8200e7d8be/MDG_RTFX_mkt_2007_2026-11-02.csv"),
    Path("C:/Users/Asus/Documents/CARSO_AI/backend/var/storage/workdir/6c0fa6aa-0bff-4987-bba9-3d2a775f2d19/cebfac35-dcd9-430d-834c-8e8200e7d8be/MDG_RTFX_mkt_2007_2026-11-02.csv"),
]
for c in candidats:
    print(c, "existe:", c.exists())

# Explorer le dossier parent du workspace (zone de stockage des pièces jointes)
courant = Path('.').resolve()
print("cwd:", courant)
parent = courant.parent
print("parent:", parent)
try:
    for e in sorted(parent.iterdir())[:40]:
        print("  ", e.name, "(dossier)" if e.is_dir() else "")
except Exception as ex:
    print("erreur:", ex)
