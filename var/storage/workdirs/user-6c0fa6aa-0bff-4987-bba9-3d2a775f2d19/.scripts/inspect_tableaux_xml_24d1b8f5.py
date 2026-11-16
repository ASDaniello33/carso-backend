import pathlib, re, zipfile

base = pathlib.Path(r'C:\Users\Asus\Documents\CARSO_AI\backend\var\storage\carso\offres\f12fb5d5-0a4b-4eee-a918-1d4f31810e26')
v = sorted(base.glob('modele-OFFRE-TECH-L01_v*.docx'), key=lambda f: f.stat().st_mtime)[-1]
print('FICHIER:', v.name)
with zipfile.ZipFile(v) as z:
    xml = z.read('word/document.xml').decode('utf-8')

tables = re.findall(r'<w:tbl>.*?</w:tbl>', xml, re.S)
print('tableaux trouves:', len(tables))
for ti, t in enumerate(tables):
    print('\n===== TABLEAU', ti, '=====')
    print('tblPr:', re.search(r'<w:tblPr>.*?</w:tblPr>', t, re.S).group(0))
    grid = re.search(r'<w:tblGrid>.*?</w:tblGrid>', t, re.S)
    print('tblGrid:', grid.group(0) if grid else 'AUCUN')
    rows = re.findall(r'<w:tr\b.*?</w:tr>', t, re.S)
    print('nb lignes:', len(rows))
    # premiere ligne complete = en-tete
    print('\n--- LIGNE 0 (en-tete) integrale ---')
    print(rows[0][:3000])
    print('\n--- LIGNE 1 (extrait) ---')
    print(rows[1][:1500] if len(rows) > 1 else 'AUCUNE')
