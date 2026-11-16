import pathlib, re
from docx import Document
from docx.shared import Pt

base = pathlib.Path(r'C:\Users\Asus\Documents\CARSO_AI\backend\var\storage\carso\offres\f12fb5d5-0a4b-4eee-a918-1d4f31810e26')
v6 = sorted(base.glob('modele-OFFRE-TECH-L01_v*.docx'), key=lambda f: f.stat().st_mtime)[-1]
d = Document(str(v6))
ps = d.paragraphs
print('nb paras', len(ps))

def show(i):
    p = ps[i]
    pPr = p._p.find('{http://schemas.openxmlformats.org/wordprocessingml/2006/main}pPr')
    ppr = pPr.xml if pPr is not None else None
    print('--- para', i, repr(p.text[:50]))
    print('   pPr:', (ppr.replace('\n','')[:600] if ppr else None))
    for k, r in enumerate(p.runs[:2]):
        print('   run', k, repr(r.text[:40]), 'b=', r.bold, 'sz=', (r.font.size.pt if r.font.size else None),
              'font=', r.font.name, 'color=', (r.font.color.rgb if r.font.color and r.font.color.type is not None else None))

for i in (73, 84, 93, 94, 102, 103, 104, 141):
    show(i)

print('\n=== TABLEAUX ===')
for ti, t in enumerate(d.tables):
    print('\nTABLE', ti, 'style=', t.style.name if t.style else None, 'lignes=', len(t.rows), 'cols=', len(t.columns))
    tblPr = t._tbl.find('{http://schemas.openxmlformats.org/wordprocessingml/2006/main}tblPr')
    print('  tblPr:', tblPr.xml.replace('\n','')[:800] if tblPr is not None else None)
    # premiere ligne
    for ci, c in enumerate(t.rows[0].cells):
        tcPr = c._tc.find('{http://schemas.openxmlformats.org/wordprocessingml/2006/main}tcPr')
        txt = c.text[:35].replace('\n','|')
        print('   h0c%d' % ci, repr(txt), '| tcPr:', (tcPr.xml.replace('\n','')[:260] if tcPr is not None else None))
    if ti == 0:
        c = t.rows[1].cells[0]
        tcPr = c._tc.find('{http://schemas.openxmlformats.org/wordprocessingml/2006/main}tcPr')
        print('   data cell tcPr:', (tcPr.xml.replace('\n','')[:260] if tcPr is not None else None))
        for ci, cc in enumerate(t.rows[1].cells):
            for p in cc.paragraphs:
                for r in p.runs[:1]:
                    print('    datarun', ci, 'b=', r.bold, 'sz=', (r.font.size.pt if r.font.size else None))
