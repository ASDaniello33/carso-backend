from pathlib import Path

cwd = Path.cwd()
print("CWD:", cwd)
for p in sorted(cwd.rglob("*")):
    if p.is_file():
        print("FILE:", p.relative_to(cwd), p.stat().st_size)
