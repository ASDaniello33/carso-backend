from pathlib import Path
import re, docx

base = Path('..').resolve()
d = docx.Document(str(base / 'Offre technique OFFRE-TECH-L99.docx'))
for i, p in enumerate(d.paragraphs, 1):
    xml = p._p.xml
    brk = re.findall(r'<w:br[^>]*w:type="page"[^>]*/?>', xml)
    pbb = 'pageBreakBefore' in xml
    instr = re.findall(r'<w:instrText[^>]*>([^<]*)</w:instrText>', xml)
    if brk or pbb or instr or i in (2,3,4,5,6,19,22,23,24,63,64,65):
        print(f'P{i:03d} style={p.style.name!r} brk={brk} pbb={pbb} instr={instr} txt={p.text[:60]!r}')
print('--- style names -> ids ---')
for st in d.styles:
    try:
        print(st.name, '=>', st.style_id, st.type)
    except Exception as e:
        print('err', e)
