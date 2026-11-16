import pathlib, re, zipfile
from docx import Document

base = pathlib.Path(r'C:\Users\Asus\Documents\CARSO_AI\backend\var\storage\carso\offres\f12fb5d5-0a4b-4eee-a918-1d4f31810e26')
v6 = sorted(base.glob('modele-OFFRE-TECH-L01_v*.docx'), key=lambda f: f.stat().st_mtime)[-1]
W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'

def clean(x):
    return re.sub(r'\sxmlns:[a-z0-9]+="[^"]*"', '', x).replace('\n','')

d = Document(str(v6))
ps = d.paragraphs

print('=== pPr brut paragraphes a puces (indices py) ===')
for i in (84, 94, 103, 105):
    pPr = ps[i]._p.find(W+'pPr')
    print(i, clean(pPr.xml)[:500] if pPr is not None else None)
    print('   runs:', [(r.text[:18], r.bold, r.font.size.pt if r.font.size else None) for r in ps[i].runs])

print('\n=== numbering.xml present ? ===')
with zipfile.ZipFile(str(v6)) as z:
    names = z.namelist()
    print([n for n in names if 'numbering' in n or 'settings' in n or 'theme' in n])
    st = z.read('word/styles.xml').decode('utf8','ignore')
    dd = re.search(r'<w:docDefaults>.*?</w:docDefaults>', st, re.S)
    print('DOCDEFAULTS:', clean(dd.group(0))[:700] if dd else None)
    nm = re.search(r'<w:style [^>]*w:styleId="1".*?</w:style>', st, re.S)
    print('\nSTYLE "1" (Normal):', clean(nm.group(0))[:700] if nm else None)
    if 'word/numbering.xml' in names:
        nx = z.read('word/numbering.xml').decode('utf8','ignore')
        print('\nNUMBERING nums:', re.findall(r'<w:num w:numId="(\d+)"', nx)[:20])
        print('ABSTRACT:', re.findall(r'w:abstractNumId="(\d+)"', nx)[:20])

print('\n=== TABLE LIVRABLES (contenu integral) ===')
t = d.tables[1]
for ri, row in enumerate(t.rows):
    print(ri, ' | '.join(c.text.replace('\n',' / ')[:70] for c in row.cells))

print('\n=== TABLE PUBLIC (contenu integral) ===')
t0 = d.tables[0]
for ri, row in enumerate(t0.rows):
    print(ri, ' | '.join(c.text.replace('\n',' / ')[:90] for c in row.cells))

print('\n=== format cellule en-tete livrables ===')
c = t.rows[0].cells[0]
tcPr = c._tc.find(W+'tcPr')
print(clean(tcPr.xml)[:600] if tcPr is not None else None)
for p in c.paragraphs:
    for r in p.runs:
        print('  hdr run:', repr(r.text[:20]), 'b=', r.bold, 'sz=', r.font.size.pt if r.font.size else None)
