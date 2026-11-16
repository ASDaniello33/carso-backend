import pathlib, re, zipfile

root = pathlib.Path(r'C:\Users\Asus\Documents\CARSO_AI\backend\var\storage\carso')
# trouver le fichier modele de l'organisation
mfiles = list(root.glob('organisations/13382ac8-d3ef-4cb1-a1ed-9d6c5da306d0/**/*.docx'))
print('FICHIERS ORG:', [f.name for f in mfiles])

base = root / 'offres' / 'f12fb5d5-0a4b-4eee-a918-1d4f31810e26'
copies = sorted(base.glob('modele-OFFRE-TECH-L01_v*.docx'), key=lambda f: f.stat().st_mtime)
copie = copies[-1]

def para_xml(path, needle):
    with zipfile.ZipFile(path) as z:
        xml = z.read('word/document.xml').decode('utf-8')
    # isoler le paragraphe contenant le texte
    for m in re.finditer(r'<w:p\b.*?</w:p>', xml, re.S):
        seg = m.group(0)
        if needle in seg:
            return seg
    return None

for label, path in (('MODELE', mfiles[0] if mfiles else None), ('COPIE', copie)):
    if path is None:
        continue
    print('\n===== %s : %s =====' % (label, path.name))
    for needle in ('Sommaire', '1. Présentation du soumissionnaire'):
        seg = para_xml(path, needle)
        if seg:
            ppr = re.search(r'<w:pPr>.*?</w:pPr>', seg, re.S)
            print('\n-- paragraphe', needle, '--')
            print('pPr:', (ppr.group(0)[:600] if ppr else 'AUCUN pPr'))
            runs = re.findall(r'<w:rPr>.*?</w:rPr>', seg, re.S)
            print('rPr x%d:' % len(runs))
            for r in runs[:3]:
                print('   ', r[:300])
