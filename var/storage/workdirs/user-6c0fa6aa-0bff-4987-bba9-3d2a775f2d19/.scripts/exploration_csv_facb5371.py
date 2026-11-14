from pathlib import Path
import pandas as pd
import numpy as np

chemin = Path("../carso/workdir/6c0fa6aa-0bff-4987-bba9-3d2a775f2d19/cebfac35-dcd9-430d-834c-8e8200e7d8be/MDG_RTFX_mkt_2007_2026-11-02.csv")
print("existe:", chemin.exists(), "taille:", chemin.stat().st_size)

df = pd.read_csv(chemin, low_memory=False)
print("forme:", df.shape)
print("colonnes:", list(df.columns))
print()
print("types:")
print(df.dtypes.to_string())
print()
print("periode:", df["price_date"].min(), "->", df["price_date"].max())
print("marches:", df["mkt_name"].nunique(), "| regions:", df["adm1_name"].nunique(), "| districts:", df["adm2_name"].nunique())
print()
print("valeurs manquantes par colonne:")
print(df.isna().sum().to_string())
print()
print("lignes dupliquees:", int(df.duplicated().sum()))
print("doublons cle (geo_id, price_date):", int(df.duplicated(subset=["geo_id", "price_date"]).sum()))
print()
print("5 dernieres lignes:")
print(df.tail(5).to_string())
