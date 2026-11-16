import pathlib
from docx import Document

base = pathlib.Path(r'C:\Users\Asus\Documents\CARSO_AI\backend\var\storage\carso\offres\f12fb5d5-0a4b-4eee-a918-1d4f31810e26')
v = sorted(base.glob('modele-OFFRE-TECH-L01_v*.docx'), key=lambda f: f.stat().st_mtime)[-1]
print('FICHIER:', v.name)
d = Document(str(v))
ps = d.paragraphs

allok = True
for i, p in enumerate(ps):
    for r in p.runs:
        if 'à compléter' in r.text and r.bold is not True:
            allok = False
            print('  NON GRAS -> para', i, repr(r.text[:50]))
print('Marqueurs en gras :', 'OK' if allok else 'KO')

for i, p in enumerate(ps):
    if 'à compléter' in p.text:
        print('\npara', i, '| style=', p.style.name)
        for k, r in enumerate(p.runs):
            print('   run', k, repr(r.text[:46]), '| b=', r.bold,
                  '| sz=', (r.font.size.pt if r.font.size else None))
