from pathlib import Path
base = Path('..').resolve()
print('BASE:', base)
for p in sorted(base.rglob('*')):
    print(' ', p.relative_to(base), 'DIR' if p.is_dir() else p.stat().st_size)
