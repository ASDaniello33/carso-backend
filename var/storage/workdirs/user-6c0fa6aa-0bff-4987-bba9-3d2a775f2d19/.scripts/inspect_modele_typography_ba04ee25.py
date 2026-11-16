import pathlib
from docx import Document
from docx.shared import Pt

root = pathlib.Path('.').resolve()
cands = sorted(root.rglob('*.docx'))
print('FICHIERS:', [c.name for c in cands][:20])
target = None
for c in cands:
    if c.name.startswith('modele-OFFRE-TECH-L01'):
        target = c
        break
if target is None:
    for p in root.parents:
        for c in p.rglob('*.docx'):
            if c.name.startswith('modele-OFFRE-TECH-L01'):
                target = c
                break
        if target:
            break
print('CIBLE:', target)
d = Document(str(target))
for i, p in enumerate(d.paragraphs):
    r0 = p.runs[0] if p.runs else None
    sz = None
    if r0 is not None and r0.font.size is not None:
        sz = round(r0.font.size.pt, 1)
    print(i, '|', p.style.name, '| al:', p.alignment, '| b:', (r0.bold if r0 is not None else None),
          '| i:', (r0.italic if r0 is not None else None), '| f:', (r0.font.name if r0 is not None else None),
          '| sz:', sz, '|', repr(p.text[:80]))
print('--- TABLEAUX ---')
for ti, t in enumerate(d.tables):
    print('tableau', ti, 'style:', t.style.name if t.style is not None else None,
          'lignes:', len(t.rows), 'colonnes:', len(t.columns))
    for ri, row in enumerate(t.rows[:2]):
        for ci, cell in enumerate(row.cells):
            for p in cell.paragraphs:
                r0 = p.runs[0] if p.runs else None
                print('  c', ri, ci, 'al:', p.alignment, 'b:', (r0.bold if r0 is not None else None),
                      'f:', (r0.font.name if r0 is not None else None),
                      'sz:', (round(r0.font.size.pt,1) if (r0 is not None and r0.font.size) else None),
                      '|', repr(p.text[:60]))
