from pathlib import Path
import pandas as pd

# Chemin relatif depuis l'espace de travail vers le dossier de la piece jointe
chemin = Path("../carso/workdir/6c0fa6aa-0bff-4987-bba9-3d2a775f2d19/cebfac35-dcd9-430d-834c-8e8200e7d8be/MDG_RTFX_mkt_2007_2026-11-02.csv")
print("existe:", chemin.exists(), "taille:", chemin.stat().st_size if chemin.exists() else None)

df = pd.read_csv(chemin, low_memory=False)
print("forme:", df.shape)
print("colonnes:", list(df.columns))
print()
print("types:")
print(df.dtypes.to_string())
