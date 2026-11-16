import pathlib
from docx import Document

base = pathlib.Path(r'C:\Users\Asus\Documents\CARSO_AI\backend\var\storage\carso\offres\f12fb5d5-0a4b-4eee-a918-1d4f31810e26')
files = sorted(base.glob('modele-OFFRE-TECH-L01_v*.docx'), key=lambda f: f.stat().st_mtime)
target = files[-1]
print('FICHIER:', target.name, target.stat().st_size)
d = Document(str(target))
paras = d.paragraphs
print('NB PARAS:', len(paras))
for i in list(range(19, 34)):
    p = paras[i]
    r0 = p.runs[0] if p.runs else None
    print(i, '|', p.style.name, '| b:', (r0.bold if r0 is not None else None),
          '| sz:', (round(r0.font.size.pt,1) if (r0 is not None and r0.font.size) else None),
          '|', repr(p.text[:60]))
