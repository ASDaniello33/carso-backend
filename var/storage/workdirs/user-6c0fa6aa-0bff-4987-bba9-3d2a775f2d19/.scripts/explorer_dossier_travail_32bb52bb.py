from pathlib import Path

cwd = Path('.').resolve()
print('CWD:', cwd)
for p in sorted(cwd.parent.iterdir()):
    print('PARENT:', p.name, '[dir]' if p.is_dir() else '')
print('--- sous-dossiers du parent ---')
for p in sorted(cwd.parent.iterdir()):
    if p.is_dir():
        try:
            files = [f.name for f in p.iterdir()]
        except Exception as e:
            files = [str(e)]
        print(p.name, '->', files[:20])
