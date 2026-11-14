from pathlib import Path

base = Path("..") / "carso"
if not base.exists():
    base = Path(".").resolve().parent.parent / "carso"
print("base:", base, base.exists())

trouves = []
if base.exists():
    for p in base.rglob("MDG_RTFX*"):
        trouves.append(p)
    for p in base.rglob("cebfac35*"):
        trouves.append(p)
for t in trouves[:20]:
    print("TROUVE:", t, t.stat().st_size if t.is_file() else "(dossier)")
