
import pathlib
base = pathlib.Path(".")
for p in sorted(base.rglob("*")):
    if p.is_file():
        print(str(p), p.stat().st_size)
