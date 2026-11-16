import pathlib, re
from docx import Document

base = pathlib.Path(r'C:\Users\Asus\Documents\CARSO_AI\backend\var\storage\carso\offres\f12fb5d5-0a4b-4eee-a918-1d4f31810e26')
v10 = sorted(base.glob('modele-OFFRE-TECH-L01_v*.docx'), key=lambda f: f.stat().st_mtime)[-1]
d = Document(str(v10))
ps = d.paragraphs
W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'

def clean(x):
    return re.sub(r'\sxmlns:[a-z0-9]+="[^"]*"', '', x).replace('\n','')

def dump(i):
    p = ps[i]
    pPr = p._p.find(W+'pPr')
    print('\n--- para', i, repr(p.text[:70]))
    print('   pPr:', clean(pPr.xml) if pPr is not None else None)
    for k, r in enumerate(p.runs):
        print('   run', k, repr(r.text[:45]), 'b=', r.bold, 'sz=', (r.font.size.pt if r.font.size else None),
              'color=', (r.font.color.rgb if r.font.color and r.font.color.type is not None else None))

for i in (19, 76, 89, 90, 96, 97, 116, 166, 170):
    dump(i)

print('\n\n=== COMPARAISON puces modele vs inserees ===')
for i in (105, 90, 91, 92, 93, 94, 95, 96, 97):
    pPr = ps[i]._p.find(W+'pPr')
    ind = None
    jc = None
    if pPr is not None:
        e = pPr.find(W+'ind')
        ind = clean(e.xml) if e is not None else None
        e2 = pPr.find(W+'jc')
        jc = e2.get(W+'val') if e2 is not None else None
    print(i, '| ind:', ind, '| jc:', jc, '|', ps[i].text[:45])

print('\n=== COMPARAISON corps modele vs insere (jc, sz) ===')
for i in (78, 89, 116, 165, 166, 168, 170):
    pPr = ps[i]._p.find(W+'pPr')
    jc = None
    if pPr is not None:
        e2 = pPr.find(W+'jc')
        jc = e2.get(W+'val') if e2 is not None else None
    r0 = ps[i].runs[0] if ps[i].runs else None
    print(i, '| jc:', jc, '| b0=', (r0.bold if r0 is not None else None),
          '| sz0=', (round(r0.font.size.pt,1) if (r0 is not None and r0.font.size) else None),
          '|', ps[i].text[:50])
