from pathlib import Path

cwd = Path.cwd()
print("CWD:", cwd)

# Lister le contenu du repertoire courant (2 niveaux)
for p in sorted(cwd.iterdir()):
    print(" -", p.name, "(dir)" if p.is_dir() else f"({p.stat().st_size} o)")
    if p.is_dir():
        for q in sorted(p.iterdir())[:20]:
            print("     *", q.name, "(dir)" if q.is_dir() else f"({q.stat().st_size} o)")
