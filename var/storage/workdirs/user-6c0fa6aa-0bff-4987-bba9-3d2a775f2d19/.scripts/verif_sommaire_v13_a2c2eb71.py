import pathlib
from docx import Document

base = pathlib.Path(r'C:\Users\Asus\Documents\CARSO_AI\backend\var\storage\carso\offres\f12fb5d5-0a4b-4eee-a918-1d4f31810e26')
v = sorted(base.glob('modele-OFFRE-TECH-L01_v*.docx'), key=lambda f: f.stat().st_mtime)[-1]
d = Document(str(v))
ps = d.paragraphs
print('FICHIER:', v.name, '| paragraphes:', len(ps))
for i, p in enumerate(ps):
    if p.text.strip() == 'Sommaire':
        r0 = p.runs[0] if p.runs else None
        print(f'  p{i} style={p.style.name!r} | bold={r0.bold if r0 else None} '
              f'| sz={(r0.font.size.pt if r0 and r0.font.size else None)} '
              f'| align={p.alignment} | outline={p.paragraph_format.outline_level}')
print('\n=== entrees de sommaire ===')
somm = False
for i, p in enumerate(ps):
    t = p.text.strip()
    if t == 'Sommaire':
        somm = True; continue
    if somm:
        if t == '':
            if i > 22: break
            continue
        print('  ', t)
        if t.startswith('10.'): break
