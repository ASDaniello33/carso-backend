from pathlib import Path
import re, docx

base = Path('..').resolve()
d = docx.Document(str(base / 'modele-OFFRE-TECH-L01_v29.docx'))
num = [p for p in d.paragraphs if 'numPr' in p._p.xml]
print('paragraphes avec numPr :', len(num))
print('numbering.xml present :', any('numbering' in str(pt.partname) for pt in d.part.package.parts))
x = d.paragraphs[95]._p.xml
x = re.sub(r'\sxmlns:[a-zA-Z0-9]+="[^"]*"', '', x)
print('P96 compact:', re.sub(r'>\s+<', '><', x))
# verifier le prefixe litteral des puces
puces = [p.text[:6] for p in d.paragraphs if p.text.startswith('•')]
print('exemples de puces:', puces[:3])
