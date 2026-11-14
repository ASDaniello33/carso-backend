from pathlib import Path

racine = Path(".")
print("cwd:", racine.resolve())
for p in sorted(racine.rglob("*")):
    if p.is_file():
        print(p, p.stat().st_size)
    else:
        print(str(p) + "/")
