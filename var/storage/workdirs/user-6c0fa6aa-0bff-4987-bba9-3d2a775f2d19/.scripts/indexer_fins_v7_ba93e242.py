from pathlib import Path
import docx
from docx.oxml.ns import qn

base = Path('..').resolve()
d = docx.Document(str(base / 'modele-OFFRE-TECH-L99_v7.docx'))

idx = 0
rows = []
for child in d.element.body.iterchildren():
    tag = child.tag.split('}')[-1]
    if tag in ('p', 'tbl'):
        idx += 1
        txt = ''.join(n.text or '' for n in child.iter(qn('w:t')))
        rows.append((idx, tag, txt))

for i, tag, txt in rows[-4:]:
    print(f'block_{i:03d} [{tag}] {txt[:200]!r}')

# sections presentes (titres 'N. ')
print('--- titres de sections ---')
for i, tag, txt in rows:
    if tag == 'p' and len(txt) > 2 and txt[0].isdigit() and '. ' in txt[:5]:
        print(f'  block_{i:03d}: {txt[:80]}')
