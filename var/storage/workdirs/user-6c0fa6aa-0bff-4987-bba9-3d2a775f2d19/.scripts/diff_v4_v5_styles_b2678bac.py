import pathlib, zipfile, re
from docx import Document

base = pathlib.Path(r'C:\Users\Asus\Documents\CARSO_AI\backend\var\storage\carso\offres\f12fb5d5-0a4b-4eee-a918-1d4f31810e26')
v4 = base / 'modele-OFFRE-TECH-L01_v4.docx'
v5 = base / 'modele-OFFRE-TECH-L01_v5.docx'

def sig(path):
    d = Document(str(path))
    out = []
    for p in d.paragraphs:
        r0 = p.runs[0] if p.runs else None
        out.append((p.text[:40], p.style.name,
                    (r0.bold if r0 is not None else None),
                    (round(r0.font.size.pt,1) if (r0 is not None and r0.font.size) else None)))
    return d, out

d4, s4 = sig(v4)
d5, s5 = sig(v5)
print('=== DIFF PARAGRAPHES v4 -> v5 (texte/style/taille) ===')
for i in range(min(len(s4), len(s5))):
    if s4[i] != s5[i]:
        print(i, '\n   v4:', s4[i], '\n   v5:', s5[i])
print('\n=== v5 : styles des titres de corps (73-95) ===')
for i in range(73, 96):
    if i < len(s5):
        print(i, s5[i])

print('\n=== BORDURES DES TABLEAUX (v5) ===')
with zipfile.ZipFile(str(v5)) as z:
    xml = z.read('word/document.xml').decode('utf8', 'ignore')
    for m in re.finditer(r'<w:tblPr>.*?</w:tblPr>', xml, re.S):
        print(m.group(0)[:500], '\n---')
    print('NB tblBorders:', xml.count('tblBorders'), '| NB shd:', xml.count('<w:shd'))
    print('header refs:', xml.count('headerReference'), '| footer refs:', xml.count('footerReference'))
    names = z.namelist()
    print('MEDIAS:', [n for n in names if n.startswith('word/media')])
