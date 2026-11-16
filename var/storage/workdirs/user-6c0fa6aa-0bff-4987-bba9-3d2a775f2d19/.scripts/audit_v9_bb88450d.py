import pathlib, re, zipfile
from docx import Document

base = pathlib.Path(r'C:\Users\Asus\Documents\CARSO_AI\backend\var\storage\carso\offres\f12fb5d5-0a4b-4eee-a918-1d4f31810e26')
v9 = sorted(base.glob('modele-OFFRE-TECH-L01_v*.docx'), key=lambda f: f.stat().st_mtime)[-1]
print('FICHIER:', v9.name)
d = Document(str(v9))
ps = d.paragraphs
print('NB PARAS:', len(ps), '| TABLEAUX:', len(d.tables))

def sig(p):
    r0 = p.runs[0] if p.runs else None
    return (p.style.name, r0.bold if r0 is not None else None,
            round(r0.font.size.pt,1) if (r0 is not None and r0.font.size) else None)

print('\n=== BLOCS INSERES (resultats attendus + modalites) ===')
targets = ['Resultat attendu', 'Les resultats attendus', 'Encadrement des participants',
           'Evaluation des acquis', 'Gestion des exercices', 'Remise des livrables',
           'Les participants des deux vagues disposent']
for i, p in enumerate(ps):
    if any(t in p.text for t in targets) or p.text.startswith('•\tLes participants des deux vagues'):
        print(i, sig(p), '|', p.text[:78])
        for r in p.runs[:3]:
            print('      run:', repr(r.text[:30]), 'b=', r.bold, 'sz=', r.font.size.pt if r.font.size else None)

print('\n=== MARQUEURS "a completer" : gras ou non ? ===')
for i, p in enumerate(ps):
    if 'à compléter' in p.text:
        segs = [(r.text[:26], r.bold) for r in p.runs]
        print(i, '|', p.text[:58])
        print('     runs:', segs)

print('\n=== INTEGRITE ===')
with zipfile.ZipFile(str(v9)) as z:
    names = z.namelist()
    xml = z.read('word/document.xml').decode('utf8','ignore')
    print('MEDIA:', [n for n in names if n.startswith('word/media')])
    print('tblBorders:', xml.count('tblBorders'), '| tbl count:', xml.count('<w:tbl>'))
    print('header/footer refs:', xml.count('headerReference'), xml.count('footerReference'))
    print('sectPr:', xml.count('<w:sectPr'))
    print('LOGO drawings:', xml.count('<w:drawing>'))
    st = z.read('word/styles.xml').decode('utf8','ignore')
    print('styleIds:', sorted(set(re.findall(r'w:styleId="([^"]+)"', st))))

print('\n=== TABLEAUX (contenu final) ===')
for ti, t in enumerate(d.tables):
    print('T', ti, len(t.rows), 'x', len(t.columns))
    for ri, row in enumerate(t.rows):
        print('   ', ri, ' | '.join(c.text.replace('\n',' / ')[:58] for c in row.cells))
