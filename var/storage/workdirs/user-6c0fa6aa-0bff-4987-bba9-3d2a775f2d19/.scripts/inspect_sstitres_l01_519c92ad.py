from pathlib import Path
import re, docx

base = Path('..').resolve()
d = docx.Document(str(base / 'modele-OFFRE-TECH-L01_v29.docx'))

def c(x):
    x = re.sub(r'\sxmlns:[a-zA-Z0-9]+="[^"]*"', '', x)
    return re.sub(r'>\s+<', '><', x)

for i in (128, 143, 152, 184, 187):
    p = d.paragraphs[i-1]
    print(f'P{i:03d} {p.text[:48]!r}')
    print('   ', c(p._p.xml)[:420]); print()

# largeurs des colonnes du tableau 3 et du tableau 4
for ti in (2, 3, 4):
    t = d.tables[ti]
    print(f'T{ti+1} largeurs:', [t.rows[0].cells[j]._tc.tcPr.find('{http://schemas.openxmlformats.org/wordprocessingml/2006/main}tcW').get('{http://schemas.openxmlformats.org/wordprocessingml/2006/main}w') for j in range(len(t.columns))])
print('nb paragraphes:', len(d.paragraphs))
