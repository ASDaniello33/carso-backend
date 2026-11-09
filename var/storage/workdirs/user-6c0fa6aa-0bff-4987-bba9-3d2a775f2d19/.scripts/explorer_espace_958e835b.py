from pathlib import Path

cwd = Path.cwd()
print('cwd =', cwd)
print('--- contenu cwd ---')
for p in sorted(cwd.iterdir()):
    print(p.name, 'dir' if p.is_dir() else 'file')

print('--- racine ---')
try:
    for p in sorted(Path('/').iterdir()):
        print(p)
except Exception as e:
    print('err', e)

# recherche de tout jpg/png visible depuis cwd et parents
print('--- recherche images ---')
vus = 0
for base in [cwd, cwd.parent, cwd.parent.parent]:
    try:
        for p in base.rglob('*'):
            if p.suffix.lower() in ('.jpg', '.jpeg', '.png') and p.is_file():
                print('IMG', p, p.stat().st_size)
                vus += 1
                if vus > 40:
                    break
    except Exception as e:
        print('err', base, type(e).__name__, e)
    if vus > 40:
        break
print('total images vues', vus)
