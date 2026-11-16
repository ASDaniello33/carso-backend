from pathlib import Path
import docx

base = Path('..')
print('--- ARBORESCENCE OFFRE ---')
for p in sorted(base.rglob('*')):
    if p.is_file():
        print(f'{p.stat().st_size:>9}  {p.relative_to(base)}')

target = None
for p in base.rglob('*.docx'):
    try:
        d = docx.Document(str(p))
    except Exception as e:
        print('ERR', p.name, e)
        continue
    if len(d.paragraphs) == 152 or len(d.tables) == 2:
        print('\n>>> CANDIDAT', p.name, 'paras=', len(d.paragraphs), 'tables=', len(d.tables))
        target = p
        break

if target is None:
    raise SystemExit('copie de travail non identifiee')

d = docx.Document(str(target))
print('\n=== FORMATAGE PARAGRAPHES CLEFS ===')
for i in (0, 6, 7, 8, 19, 20, 22, 23, 31, 32, 72, 73, 74, 75, 82, 83, 89, 90, 101, 102):
    if i >= len(d.paragraphs):
        continue
    p = d.paragraphs[i]
    runs = [(r.text[:22], r.bold, r.font.size.pt if r.font.size else None, r.font.name) for r in p.runs][:3]
    xml = p._p.xml
    brk = 'PAGEBREAK' if ('w:br' in xml and 'type="page"' in xml) else ''
    pbb = p.paragraph_format.page_break_before
    print(f'P{i:3} | {p.text[:46]!r} | style={p.style.name} | runs={runs} | {brk} pbb={pbb}')

print('\n=== TABLEAU 1 (public) ===')
t = d.tables[0]
print('style:', t.style.name if t.style else None)
for r in t.rows:
    print(' | '.join(c.text[:34] for c in r.cells))
print('--- XML en-tete T1 (1500) ---')
print(t._tbl.xml[:1500])
