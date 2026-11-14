from pathlib import Path

base = Path("..") / "carso"
print("existe:", base.exists())
if base.exists():
    for p in sorted(base.iterdir()):
        print(" -", p.name)
