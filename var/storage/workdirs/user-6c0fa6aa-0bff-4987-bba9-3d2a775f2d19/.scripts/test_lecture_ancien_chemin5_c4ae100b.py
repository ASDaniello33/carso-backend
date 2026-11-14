from pathlib import Path

# le script de la session precedente utilisait /storage/workdir/<compte>/...
# tester si ce chemin POSIX est monte dans le sandbox actuel
cible = Path("/storage/workdir/6c0fa6aa-0bff-4987-bba9-3d2a775f2d19/cebfac35-dcd9-430d-834c-8e8200e7d8be/MDG_RTFX_mkt_2007_2026-11-02.csv")
print("existe:", cible.exists())

# variante : le dossier de l'objet monte sous un autre nom
for candidat in [
    Path("/storage/workdir"),
    Path("/storage"),
    Path("/workdir"),
    Path("/data"),
]:
    print(candidat, "->", candidat.exists())
