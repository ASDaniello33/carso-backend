from pathlib import Path

print("cwd:", Path(".").resolve())
base = Path("..") / "carso"
print("base existe:", base.exists(), base.resolve() if base.exists() else "")
d = base / "workdir" / "6c0fa6aa-0bff-4987-bba9-3d2a775f2d19" / "cebfac35-dcd9-430d-834c-8e8200e7d8be"
print("dossier doc existe:", d.exists())
if d.exists():
    for p in d.iterdir():
        print(" -", p.name, p.stat().st_size)
