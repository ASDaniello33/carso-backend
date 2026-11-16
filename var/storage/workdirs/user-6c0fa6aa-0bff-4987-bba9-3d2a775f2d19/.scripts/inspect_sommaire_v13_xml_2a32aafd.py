import pathlib, re, zipfile

base = pathlib.Path(r'C:\Users\Asus\Documents\CARSO_AI\backend\var\storage\carso\offres\f12fb5d5-0a4b-4eee-a918-1d4f31810e26')
v = sorted(base.glob('modele-OFFRE-TECH-L01_v*.docx'), key=lambda f: f.stat().st_mtime)[-1]
print('FICHIER:', v.name)
with zipfile.ZipFile(v) as z:
    xml = z.read('word/document.xml').decode('utf-8')
for m in re.finditer(r'<w:p\b.*?</w:p>', xml, re.S):
    seg = m.group(0)
    if 'Sommaire' in seg:
        ppr = re.search(r'<w:pPr>.*?</w:pPr>', seg, re.S)
        print('pPr du paragraphe Sommaire:', ppr.group(0) if ppr else 'AUCUN pPr')
        print('runs:', re.findall(r'<w:rPr>.*?</w:rPr>', seg, re.S))
        break
# styles disponibles dans styles.xml
with zipfile.ZipFile(v) as z:
    st = z.read('word/styles.xml').decode('utf-8')
for m in re.finditer(r'<w:style [^>]*w:styleId="([^"]+)"[^>]*>(.*?)</w:style>', st, re.S):
    sid, body = m.group(1), m.group(2)
    nm = re.search(r'<w:name w:val="([^"]+)"', body)
    if nm and 'eading' in (nm.group(1) or ''):
        print('\nstyle id=%s name=%s' % (sid, nm.group(1)))
        print('  ', body[:400])
