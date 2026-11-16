from pathlib import Path
import docx
from docx.oxml.ns import qn

base = Path('..').resolve()
d = docx.Document(str(base / 'modele-OFFRE-TECH-L99_v7.docx'))

cibles = ['rooms', 'treated', 'régulation des', 'de ne pas dedicated']
idx = 0
for child in d.element.body.iterchildren():
    tag = child.tag.split('}')[-1]
    if tag in ('p', 'tbl'):
        idx += 1
        txt = ''.join(n.text or '' for n in child.iter(qn('w:t')))
        for b in cibles:
            if b in txt:
                print(f'block_{idx:03d} [{tag}] -> {txt}')
                break
print('total elements:', idx)
