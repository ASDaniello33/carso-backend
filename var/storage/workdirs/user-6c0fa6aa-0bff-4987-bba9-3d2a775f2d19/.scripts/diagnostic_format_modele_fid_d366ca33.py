from pathlib import Path
import docx

base = Path('..')
print('FICHIERS OFFRE:', sorted(p.name for p in base.iterdir()))

cands = sorted(base.glob('*.docx'))
print('DOCX:', [c.name for c in cands])
if not cands:
    raise SystemExit('aucun docx dans le dossier de l offre')

d = docx.Document(str(cands[0]))
paras = d.paragraphs
print('NB PARAGRAPHES:', len(paras), '| NB TABLEAUX:', len(d.tables))

for i in range(70, 96):
    if i >= len(paras):
        break
    p = paras[i]
    if not p.text.strip():
        continue
    r0 = p.runs[0] if p.runs else None
    info = ''
    if r0 is not None:
        info = f"bold={r0.bold} size={r0.font.size} name={r0.font.name}"
    print(f'P{i}: {p.text[:55]!r} | align={p.alignment} | {info}')

for ti, t in enumerate(d.tables):
    print(f'--- TABLEAU {ti+1} style={t.style.name if t.style else None} ---')
    print('  XML(1400):', t._tbl.xml[:1400].replace('\n', ' '))
