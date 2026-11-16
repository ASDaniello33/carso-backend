from pathlib import Path
import docx, re

base = Path('..').resolve()

# 1) Localiser le modele de l'organisation emettrice (lecture autorisee)
found = None
for p in base.rglob('Model_offre_technique_FID.docx'):
    found = p
    break

if found is None:
    # remonter d'un niveau et chercher dans le dossier parent de l'ancre
    for p in base.parent.rglob('Model_offre_technique_FID.docx'):
        found = p
        break

print('MODELE TROUVE:', found)

def clean(x):
    x = re.sub(r'\sxmlns:[a-zA-Z0-9]+="[^"]*"', '', x)
    return ' '.join(x.split())

if found:
    d = docx.Document(str(found))
    print('paras=', len(d.paragraphs), 'tables=', len(d.tables))
    for i, p in enumerate(d.paragraphs):
        if not p.text.strip():
            continue
        runs = [(r.text[:26], r.bold, r.font.size.pt if r.font.size else None, r.font.name) for r in p.runs][:3]
        pb = 'PAGEBREAK' if 'type="page"' in p._p.xml else ''
        ind = p.paragraph_format.left_indent
        print(f'P{i:3} | {p.text[:50]!r} | style={p.style.name} | align={p.alignment} | ind={ind} | {pb} | {runs}')
    print('\n--- XML paragraphe "Sommaire" ---')
    for i, p in enumerate(d.paragraphs):
        if 'Sommaire' in p.text or 'LOL' in p.text or 'Contenus commence' in p.text or 'Lieu + date' in p.text or 'Entreprise' in p.text:
            print(f'### P{i} ###')
            print(clean(p._p.xml)[:1100])
            print()
