from pathlib import Path

# chemin POSIX utilise par le script de la session precedente
cible = Path("/storage/workdir/6c0fa6aa-0bff-4987-bba9-3d2a775f2d19/cebfac35-dcd9-430d-834c-8e8200e7d8be/MDG_RTFX_mkt_2007_2026-11-02.csv")
print("existe:", cible.exists())
if cible.exists():
    print("taille:", cible.stat().st_size)
