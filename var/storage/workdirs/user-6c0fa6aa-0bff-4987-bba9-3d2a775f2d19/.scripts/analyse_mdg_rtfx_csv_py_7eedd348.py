from pathlib import Path
import pandas as pd
import numpy as np

chemin = Path('/storage/workdir/6c0fa6aa-0bff-4987-bba9-3d2a775f2d19/cebfac35-dcd9-430d-834c-8e8200e7d8be/MDG_RTFX_mkt_2007_2026-11-02.csv')
print('existe', chemin.exists(), 'taille_octets', chemin.stat().st_size if chemin.exists() else None)
df = pd.read_csv(chemin, low_memory=False)
print('forme', df.shape)
print('colonnes', list(df.columns))
print('types')
print(df.dtypes.to_string())
print('periode', df['price_date'].min(), df['price_date'].max())
print('marches_nombre', df['mkt_name'].nunique())
print('marches_top')
print(df['mkt_name'].value_counts().head(20).to_string())
print('valeurs_manquantes')
print((df.isna().sum()).sort_values(ascending=False).to_string())
print('lignes_dupliquees_completes', int(df.duplicated().sum()))
for c in df.columns:
    if df[c].dtype == object:
        vals = df[c].dropna().astype(str).unique()
        if len(vals) <= 30:
            print('valeurs_uniques', c, len(vals), vals.tolist())
        else:
            print('valeurs_uniques', c, len(vals), 'exemples', vals[:10].tolist())
print('apercu_fin')
print(df.tail(5).to_string())
