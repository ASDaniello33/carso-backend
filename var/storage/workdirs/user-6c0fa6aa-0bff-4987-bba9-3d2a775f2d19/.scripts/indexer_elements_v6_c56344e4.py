from pathlib import Path
import docx
from docx.oxml.ns import qn

base = Path('..').resolve()
d = docx.Document(str(base / 'modele-OFFRE-TECH-L99_v6.docx'))
body = d.element.body

idx = 0
for child in body.iterchildren():
    tag = child.tag.split('}')[-1]
    if tag == 'p':
        idx += 1
        txt = ''.join(n.text or '' for n in child.iter(qn('w:t')))
        if idx >= 130:
            print(f'block_{idx:03d} PARA  {txt[:95]!r}')
    elif tag == 'tbl':
        idx += 1
        first = ''.join(n.text or '' for n in child.iter(qn('w:t')))[:95]
        print(f'block_{idx:03d} TABLE {first!r}')
print('TOTAL elements corps:', idx)
