import pathlib
from docx import Document

base = pathlib.Path(r'C:\Users\Asus\Documents\CARSO_AI\backend\var\storage\carso\offres\f12fb5d5-0a4b-4eee-a918-1d4f31810e26')
v = sorted(base.glob('modele-OFFRE-TECH-L01_v*.docx'), key=lambda f: f.stat().st_mtime)[-1]
d = Document(str(v))
ps = d.paragraphs

print('FICHIER:', v.name)
print('\n=== styles des paragraphes de reperage ===')
for i, p in enumerate(ps):
    t = p.text.strip()
    if t in ('Sommaire', '1. Présentation du soumissionnaire', '10. Conclusion et engagement',
             '6.1. Vague 1 : équipes SI et PSE (4 jours ouvrables)',
             'Jour 1 : fondamentaux de l\'IA et bonnes pratiques',
             'Toliara, le (à compléter : date de soumission de la présente offre)'):
        r0 = p.runs[0] if p.runs else None
        print(f'  p{i:3d} style={p.style.name!r:16} | bold={r0.bold if r0 else None} '
              f'| sz={(r0.font.size.pt if r0 and r0.font.size else None)} | {t[:52]}')

print('\n=== inventaire des styles utilises ===')
from collections import Counter
c = Counter(p.style.name for p in ps)
for k, n in c.most_common():
    print(f'  {k!r}: {n}')

print('\n=== styles disponibles dans le document (Heading ?) ===')
names = sorted({s.name for s in d.styles})
print(' ', [n for n in names if 'ead' in n or 'itle' in n or 'ommaire' in n])

print('\n=== paragraphes 73-79 (titres de section) ===')
for i in range(73, 80):
    print(f'  p{i} [{ps[i].style.name}] {ps[i].text[:70]}')
