from pathlib import Path

# lecture brute (sans pandas) du chemin absolu : la garde bloque-t-elle aussi open() ?
cible = Path("C:/Users/Asus/Documents/CARSO_AI/backend/var/storage/carso/workdir/6c0fa6aa-0bff-4987-bba9-3d2a775f2d19/cebfac35-dcd9-430d-834c-8e8200e7d8be/MDG_RTFX_mkt_2007_2026-11-02.csv")
try:
    with open(cible, "r", encoding="utf-8", errors="replace") as f:
        entete = f.readline()
        ligne1 = f.readline()
    print("OK lecture directe")
    print("entete:", entete.strip())
    print("ligne1:", ligne1.strip())
except Exception as e:
    print("ECHEC:", type(e).__name__, e)
