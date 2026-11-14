from pathlib import Path

candidats = [
    Path("cebfac35-dcd9-430d-834c-8e8200e7d8be/MDG_RTFX_mkt_2007_2026-11-02.csv"),
    Path("../cebfac35-dcd9-430d-834c-8e8200e7d8be/MDG_RTFX_mkt_2007_2026-11-02.csv"),
    Path("/storage/workdir/6c0fa6aa-0bff-4987-bba9-3d2a775f2d19/cebfac35-dcd9-430d-834c-8e8200e7d8be/MDG_RTFX_mkt_2007_2026-11-02.csv"),
    Path("MDG_RTFX_mkt_2007_2026-11-02.csv"),
]
for c in candidats:
    print(c, "->", c.exists())

# recherche large dans l'espace et un niveau au-dessus
for base in [Path("."), Path("..")]:
    for p in base.rglob("MDG_RTFX*"):
        print("TROUVE:", p.resolve(), p.stat().st_size)
