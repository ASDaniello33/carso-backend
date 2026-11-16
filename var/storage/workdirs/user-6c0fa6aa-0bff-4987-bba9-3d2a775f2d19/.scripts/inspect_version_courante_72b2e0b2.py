import pathlib, zipfile, re
from docx import Document

root = pathlib.Path('.').resolve()
base = None
for p in root.parents:
    cand = p / 'offres' / 'f12fb5d5-0a4b-4eee-a918-1d4f31810e26'
    if cand.exists():
        base = cand
        break
print('BASE:', base)
files = sorted(base.glob('*.docx'), key=lambda f: f.stat().st_mtime)
for f in files:
    print(' ', f.name, f.stat().st_size, f.stat().st_mtime)

target = files[-1] if files else None
print('CIBLE:', target)
if target:
    d = Document(str(target))
    paras = d.paragraphs
    print('NB PARAS:', len(paras), 'NB TABLEAUX:', len(d.tables))
    for i in list(range(70, 95)):
        if i < len(paras):
            p = paras[i]
            r0 = p.runs[0] if p.runs else None
            print(i, '|', p.style.name, '| al:', p.alignment,
                  '| b:', (r0.bold if r0 is not None else None),
                  '| f:', (r0.font.name if r0 is not None else None),
                  '| sz:', (round(r0.font.size.pt,1) if (r0 is not None and r0.font.size) else None),
                  '|', repr(p.text[:70]))
    with zipfile.ZipFile(str(target)) as z:
        xml = z.read('word/document.xml').decode('utf8', 'ignore')
        print('CHAMP TOC:', 'TOC' in xml, '| fldSimple:', xml.count('fldSimple'),
              '| instrText:', xml.count('instrText'))
        for m in re.finditer(r'<w:instrText[^>]*>([^<]*)</w:instrText>', xml):
            print('  instr:', m.group(1)[:80])
        print('NB HYPERLINK:', xml.count('w:hyperlink'))
