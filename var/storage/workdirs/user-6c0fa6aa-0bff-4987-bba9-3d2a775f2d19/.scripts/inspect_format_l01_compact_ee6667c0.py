from pathlib import Path
import re, docx

def compact(xml):
    x = re.sub(r'\sxmlns:[a-zA-Z0-9]+="[^"]*"', '', xml)
    x = re.sub(r'\smc:Ignorable="[^"]*"', '', x)
    return re.sub(r'>\s+<', '><', x)

base = Path('..').resolve()
d = docx.Document(str(base / 'modele-OFFRE-TECH-L01_v29.docx'))

for i in (74, 75, 96, 128):
    p = d.paragraphs[i-1]
    print(f'--- P{i} : {p.text[:45]!r}')
    print(compact(p._p.xml)[:700])
    print()

t = d.tables[2]
print('=== T3 tblPr ===');  print(compact(t._tbl.tblPr.xml)[:600])
print('=== T3 en-tete C0 ==='); print(compact(t.rows[0].cells[0]._tc.xml)[:800])
print('=== T3 L2C0 ==='); print(compact(t.rows[1].cells[0]._tc.xml)[:700])
print('=== T3 L2C3 (statut) ==='); print(compact(t.rows[1].cells[3]._tc.xml)[:700])
