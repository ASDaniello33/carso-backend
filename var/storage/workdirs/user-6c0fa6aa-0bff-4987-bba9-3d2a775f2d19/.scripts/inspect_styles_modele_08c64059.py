import pathlib, zipfile, re
from docx import Document

base = pathlib.Path(r'C:\Users\Asus\Documents\CARSO_AI\backend\var\storage\carso\offres\f12fb5d5-0a4b-4eee-a918-1d4f31810e26')
target = base / 'modele-OFFRE-TECH-L01_v4.docx'
d = Document(str(target))
paras = d.paragraphs

print('=== TOC / SOMMAIRE (par. 22-31) ===')
for i in range(22, 32):
    p = paras[i]
    r0 = p.runs[0] if p.runs else None
    print(i, '|', p.style.name, '| al:', p.alignment,
          '| b:', (r0.bold if r0 is not None else None),
          '| sz:', (round(r0.font.size.pt,1) if (r0 is not None and r0.font.size) else None),
          '| ind:', p.paragraph_format.left_indent,
          '|', repr(p.text[:50]))

print('=== CORPS : titres et listes ===')
for i in [73, 74, 82, 83, 92, 93, 102, 103, 143, 144]:
    p = paras[i]
    r0 = p.runs[0] if p.runs else None
    print(i, '|', p.style.name, '| al:', p.alignment,
          '| b:', (r0.bold if r0 is not None else None),
          '| sz:', (round(r0.font.size.pt,1) if (r0 is not None and r0.font.size) else None),
          '| ind:', p.paragraph_format.left_indent,
          '| esp:', p.paragraph_format.space_after,
          '|', repr(p.text[:60]))

with zipfile.ZipFile(str(target)) as z:
    st = z.read('word/styles.xml').decode('utf8', 'ignore')
    m = re.search(r'<w:docDefaults>.*?</w:docDefaults>', st, re.S)
    print('=== DOCDEFAULTS ===')
    print(m.group(0)[:1200] if m else 'absent')
    for sid in ['Normal', 'Heading1', 'Heading2']:
        mm = re.search(r'<w:style [^>]*w:styleId="%s".*?</w:style>' % sid, st, re.S)
        if mm:
            print('=== STYLE', sid, '===')
            print(mm.group(0)[:900])

print('=== TABLEAU 0 (public) cellule en-tete ===')
t = d.tables[0]
print('style table:', t.style.name if t.style is not None else None)
for ri, row in enumerate(t.rows[:2]):
    for ci, cell in enumerate(row.cells):
        for p in cell.paragraphs:
            r0 = p.runs[0] if p.runs else None
            print(' c', ri, ci, '| al:', p.alignment, '| b:', (r0.bold if r0 is not None else None),
                  '| sz:', (round(r0.font.size.pt,1) if (r0 is not None and r0.font.size) else None),
                  '|', repr(p.text[:40]))
print('=== TABLEAU 1 (livrables) ===')
t1 = d.tables[1]
for ri, row in enumerate(t1.rows[:2]):
    for ci, cell in enumerate(row.cells):
        for p in cell.paragraphs:
            r0 = p.runs[0] if p.runs else None
            print(' c', ri, ci, '| al:', p.alignment, '| b:', (r0.bold if r0 is not None else None),
                  '| sz:', (round(r0.font.size.pt,1) if (r0 is not None and r0.font.size) else None),
                  '|', repr(p.text[:40]))
