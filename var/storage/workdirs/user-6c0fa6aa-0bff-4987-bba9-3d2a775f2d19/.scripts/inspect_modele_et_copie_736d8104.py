import pathlib, re, zipfile
from docx import Document

root = pathlib.Path(r'C:\Users\Asus\Documents\CARSO_AI\backend\var\storage\carso')
cands = list(root.glob('organisations/13382ac8-d3ef-4cb1-a1ed-9d6c5da306d0/**/*.docx'))
print('FICHIERS MODELLE TROUVES:', [c.name for c in cands])
base = root / 'offres' / 'f12fb5d5-0a4b-4eee-a918-1d4f31810e26'

def dump(path, label):
    print('\n=====', label, path.name, '=====')
    d = Document(str(path))
    for i, p in enumerate(d.paragraphs[:34]):
        r0 = p.runs[0] if p.runs else None
        brs = p._p.findall('.//{http://schemas.openxmlformats.org/wordprocessingml/2006/main}br')
        pbs = [b.get('{http://schemas.openxmlformats.org/wordprocessingml/2006/main}type') for b in brs]
        print(i, '|', p.style.name, '| b:', (r0.bold if r0 is not None else None),
              '| sz:', (round(r0.font.size.pt,1) if (r0 is not None and r0.font.size) else None),
              '| br:', pbs, '|', repr(p.text[:45]))

for c in cands:
    dump(c, 'MODELE')

v6 = sorted(base.glob('modele-OFFRE-TECH-L01_v*.docx'), key=lambda f: f.stat().st_mtime)[-1]
dump(v6, 'COPIE v6')

# styles disponibles dans le modele
if cands:
    with zipfile.ZipFile(str(cands[0])) as z:
        st = z.read('word/styles.xml').decode('utf8', 'ignore')
        print('\nSTYLES DEFINIS:', sorted(set(re.findall(r'w:styleId="([^"]+)"', st))))
    with zipfile.ZipFile(str(v6)) as z:
        st6 = z.read('word/styles.xml').decode('utf8', 'ignore')
        print('STYLES COPIE:', sorted(set(re.findall(r'w:styleId="([^"]+)"', st6))))
