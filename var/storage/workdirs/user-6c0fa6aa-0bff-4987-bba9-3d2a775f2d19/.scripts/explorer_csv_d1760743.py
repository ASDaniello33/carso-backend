from pathlib import Path
import pandas as pd
import numpy as np

chemin = Path('/storage/workdir/6c0fa6aa-0bff-4987-bba9-3d2a775f2d19/cebfac35-dcd9-430d-834c-8e8200e7d8be/MDG_RTFX_mkt_2007_2026-11-02.csv')
print('existe', chemin.exists())
df = pd.read_csv(chemin, low_memory=False)
print('forme', df.shape)
print('colonnes', list(df.columns))
print('types')
print(df.dtypes.to_string())
print('periode', df['price_date'].min(), df['price_date'].max())
print('marches_nombre', df['mkt_name'].nunique())
print('regions', df['adm1_name'].nunique(), df['adm1_name'].dropna().unique()[:15].tolist())
print('valeurs_manquantes')
print((df.isna().sum()).sort_values(ascending=False).to_string())
print('lignes_dupliquees_completes', int(df.duplicated().sum()))
# colonne cible : combien de valeurs non nulles
cible = 'exchange_rate_unofficial'
print('cible non nulle', df[cible].notna().sum(), '/', len(df))
print('cible par annee')
print(df.groupby('year')[cible].agg(['count','mean','min','max']).to_string())
print('apercu_fin')
print(df.tail(5).to_string())
