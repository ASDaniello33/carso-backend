from pathlib import Path
import re, docx
from docx.shared import Pt

base = Path('..').resolve()
d = docx.Document(str(base / 'Offre technique OFFRE-TECH-L99.docx'))

# styles
for name in ['Normal', 'Heading 1']:
    st = d.styles[name]
    pf = st.paragraph_format
    print(f'STYLE {name}: font={st.font.name} sz={st.font.size.pt if st.font.size else None} bold={st.font.bold} align={pf.alignment} sp_before={pf.space_before} sp_after={pf.space_after}')
    print('   xml:', st.element.xml[:400].replace('\n', ' '))

print('=== PARAGRAPHES CLES ===')
for i, p in enumerate(d.paragraphs, 1):
    xml = p._p.xml
    brk = 'PAGEBREAK' if 'w:br w:type="page"' in xml else ''
    pbb = 'pageBreakBefore' if 'pageBreakBefore' in xml else ''
    sect = 'SECTPR' if '<w:sectPr' in xml else ''
    if i in (1,7,8,20,21,22,23,24,25,26,63,64,65) or brk or pbb or sect:
        runs = [(r.text, r.font.name, r.font.size.pt if r.font.size else None, r.font.bold) for r in p.runs]
        print(f'P{i:03d} {brk} {pbb} {sect} align={p.alignment} runs={runs}')

print('=== LISTES / NUMEROTATIONS ===')
print('numbering part:', [pt.partname for pt in d.part.package.parts if 'numbering' in str(pt.partname)])
print('styles xml defs:', re.findall(r'w:styleId="([^"]+)"', d.styles.element.xml))
