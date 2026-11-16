import pathlib, zipfile, re
from docx import Document

root = pathlib.Path(r'C:\Users\Asus\Documents\CARSO_AI\backend\var\storage\carso')
cands = sorted(root.rglob('Model_offre_technique_FID.docx'))
print('MODELES TROUVES:', [str(c) for c in cands])
if not cands:
    raise SystemExit('modele introuvable')
tpl = cands[0]
d = Document(str(tpl))
paras = d.paragraphs
print('NB PARAS:', len(paras), 'NB TABLEAUX:', len(d.tables))
for i in range(20, 34):
    p = paras[i]
    r0 = p.runs[0] if p.runs else None
    print(i, '|', p.style.name, '| al:', p.alignment,
          '| b:', (r0.bold if r0 is not None else None),
          '| f:', (r0.font.name if r0 is not None else None),
          '| sz:', (round(r0.font.size.pt,1) if (r0 is not None and r0.font.size) else None),
          '|', repr(p.text[:60]))

with zipfile.ZipFile(str(tpl)) as z:
    xml = z.read('word/document.xml').decode('utf8', 'ignore')
    print('TOC field:', 'TOC' in xml, '| fldChar:', xml.count('fldChar'),
          '| instrText:', xml.count('instrText'), '| hyperlink:', xml.count('w:hyperlink'))
    for m in re.finditer(r'<w:instrText[^>]*>([^<]*)</w:instrText>', xml):
        print('  instr:', m.group(1)[:100])
    print('--- tblPr ---')
    for m in re.finditer(r'<w:tblPr>.*?</w:tblPr>', xml, re.S):
        print(m.group(0)[:600])
        print('...')
    print('--- sectPr ---')
    for m in re.finditer(r'<w:sectPr.*?</w:sectPr>', xml, re.S):
        print(m.group(0)[:500])
