from pathlib import Path
import docx, re

base = Path('..')
target = base / 'modele-OFFRE-TECH-L01_v3.docx'
d = docx.Document(str(target))

def clean(x):
    x = re.sub(r'\sxmlns:[a-zA-Z0-9]+="[^"]*"', '', x)
    return x.replace('\n', ' ')

for i in (73, 83, 101, 102, 22, 23):
    p = d.paragraphs[i]
    print(f'===== P{i} : {p.text[:45]!r} =====')
    print(clean(p._p.xml)[:1300])
    print()

print('##### TABLEAU 2 (livrables) #####')
t = d.tables[1]
print('style:', t.style.name if t.style else None)
print('--- tblPr ---')
print(clean(t._tbl.tblPr.xml)[:900])
print('--- 1ere ligne (tr0) ---')
print(clean(t.rows[0]._tr.xml)[:1800])
print('--- 2e ligne (tr1) ---')
print(clean(t.rows[1]._tr.xml)[:1400])
print('--- largeur colonnes (grid) ---')
print(clean(t._tbl.find('{http://schemas.openxmlformats.org/wordprocessingml/2006/main}tblGrid').xml)[:600])

print('\n##### SECTION / SECTPR #####')
print(clean(d.sections[0]._sectPr.xml)[:900])
print('\n##### NORMAL STYLE #####')
print(clean(d.styles['Normal'].element.xml)[:700])
