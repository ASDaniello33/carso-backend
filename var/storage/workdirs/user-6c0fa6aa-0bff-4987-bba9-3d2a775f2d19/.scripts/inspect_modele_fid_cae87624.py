from pathlib import Path
import docx

base = Path('..').resolve()
fichiers = sorted(p for p in base.rglob('*') if p.is_file())
print('FICHIERS:', [str(p.relative_to(base)) for p in fichiers][:40])

d = docx.Document(str(base / 'Offre technique OFFRE-TECH-L99.docx'))
print('NB PARAGRAPHES:', len(d.paragraphs), 'NB TABLEAUX:', len(d.tables))
for i, p in enumerate(d.paragraphs, 1):
    txt = p.text.strip()
    drawings = len(p._p.findall('.//{http://schemas.openxmlformats.org/drawingml/2006/main}blip'))
    has_fld = 'fldChar' in p._p.xml or 'instrText' in p._p.xml
    instr = ''
    if has_fld:
        import re
        m = re.findall(r'<w:instrText[^>]*>([^<]*)</w:instrText>', p._p.xml)
        instr = ' | '.join(m)
    al = p.alignment
    runs = p.runs
    fmt = ''
    if runs:
        r = runs[0]
        fmt = f"font={r.font.name} sz={r.font.size.pt if r.font.size else None} b={r.font.bold} color={r.font.color.rgb if r.font.color and r.font.color.type is not None else None}"
    if txt or drawings or has_fld:
        print(f'P{i:03d} align={al} img={drawings} fld={instr[:60]!r} [{fmt}] :: {txt[:90]!r}')
print('--- SECTIONS ---')
for s in d.sections:
    print('header:', [x.text for x in s.header.paragraphs], 'footer:', [x.text for x in s.footer.paragraphs])
print('--- STYLES DISPONIBLES ---')
print([st.name for st in d.styles if st.type is not None][:60])
