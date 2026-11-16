import pathlib, re
from docx import Document

base = pathlib.Path(r'C:\Users\Asus\Documents\CARSO_AI\backend\var\storage\carso\offres\f12fb5d5-0a4b-4eee-a918-1d4f31810e26')
v11 = sorted(base.glob('modele-OFFRE-TECH-L01_v*.docx'), key=lambda f: f.stat().st_mtime)[-1]
print('FICHIER:', v11.name)
d = Document(str(v11))
ps = d.paragraphs

print('=== runs des paragraphes avec marqueurs ===')
for i, p in enumerate(ps):
    if 'à compléter' in p.text:
        print('\npara', i)
        for k, r in enumerate(p.runs):
            print('   run', k, repr(r.text[:52]), '| b=', r.bold,
                  '| sz=', (r.font.size.pt if r.font.size else None),
                  '| font=', r.font.name)

print('\n=== verif marqueurs tous en gras ===')
allok = True
for i, p in enumerate(ps):
    for r in p.runs:
        if 'à compléter' in r.text and r.bold is not True:
            allok = False
            print('  NON GRAS -> para', i, repr(r.text[:50]))
print('  RESULTAT:', 'OK tous les marqueurs sont en gras' if allok else 'KO')

print('\n=== texte des 4 paragraphes corriges ===')
for i in (19, 76, 169, 172):
    print(i, '|', ps[i].text[:120])
