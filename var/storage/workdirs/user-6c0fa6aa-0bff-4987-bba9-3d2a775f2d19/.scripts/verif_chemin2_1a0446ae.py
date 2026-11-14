from pathlib import Path

# le chemin absolu vu par le script precedent
cible = Path("C:/Users/Asus/Documents/CARSO_AI/backend/var/storage/carso/workdir/6c0fa6aa-0bff-4987-bba9-3d2a775f2d19/cebfac35-dcd9-430d-834c-8e8200e7d8be/MDG_RTFX_mkt_2007_2026-11-02.csv")
print("absolu existe:", cible.exists())

# chemin relatif depuis cwd
rel = Path("../carso/workdir/6c0fa6aa-0bff-4987-bba9-3d2a775f2d19/cebfac35-dcd9-430d-834c-8e8200e7d8be/MDG_RTFX_mkt_2007_2026-11-02.csv")
print("relatif existe:", rel.exists(), "->", rel.resolve() if rel.exists() else "")
