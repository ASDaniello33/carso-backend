from pathlib import Path
import re, docx

base = Path('..').resolve()
d = docx.Document(str(base / 'modele-OFFRE-TECH-L01_v29.docx'))
idxs = [7, 8, 9, 10, 20, 21, 24, 25, 78, 84, 115, 117, 126, 127, 133, 147, 165, 175, 178, 184, 189, 190]
for i in idxs:
    p = d.paragraphs[i-1]
    x = p._p.xml
    x = re.sub(r'\sxmlns:[a-zA-Z0-9]+="[^"]*"', '', x)
    x = re.sub(r'>\s+<', '><', x)
    x = re.sub(r'<w:pPr>.*?</w:pPr>', '', x, flags=re.S)
    print(f'P{i:03d} style={p.style.name!r} align={p.alignment} :: {p.text[:52]!r}')
    print('     ', x[:260])
