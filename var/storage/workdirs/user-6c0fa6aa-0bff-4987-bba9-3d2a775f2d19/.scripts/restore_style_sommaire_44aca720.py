import pathlib, re, zipfile
from docx import Document
from docx.oxml.ns import qn

dossier = pathlib.Path(r'C:\Users\Asus\Documents\CARSO_AI\backend\var\storage\carso\offres\f12fb5d5-0a4b-4eee-a918-1d4f31810e26')
versions = sorted(dossier.glob('modele-OFFRE-TECH-L01_v*.docx'), key=lambda f: f.stat().st_mtime)
src = versions[-1]
print('SOURCE:', src.name)

d = Document(str(src))
ps = d.paragraphs

# 1. restaurer le style heading 1 sur le titre Sommaire
cible = None
for p in ps:
    if p.text.strip() == 'Sommaire':
        cible = p
        break
if cible is None:
    raise SystemExit('paragraphe Sommaire introuvable')

print('AVANT : style=%r' % cible.style.name)
cible.style = d.styles['heading 1']
for r in cible.runs:
    rPr = r._element.find(qn('w:rPr'))
    if rPr is not None:
        r._element.remove(rPr)
print('APRES : style=%r' % cible.style.name)

# 2. AUDIT COMPLET
print('\n================ AUDIT ================')
print('paragraphes:', len(ps))
print('tableaux:', len(d.tables))

with zipfile.ZipFile(src) as z:
    noms = z.namelist()
    media = [n for n in noms if n.startswith('word/media/')]
    xml = z.read('word/document.xml').decode('utf-8')
print('medias dans le paquet:', media)

residus = []
for i, p in enumerate(ps):
    t = p.text
    if '<' in t and re.search(r'<(p|strong|span|div|table|br|/?b)[ >/]', t):
        residus.append((i, t[:60]))
print('HTML residuel:', residus if residus else 'AUCUN')

marq = []
for i, p in enumerate(ps):
    for r in p.runs:
        if 'à compléter' in r.text:
            marq.append((i, r.bold is True, r.text[:60]))
print('\nmarqueurs "à compléter" :', len(marq))
for i, b, t in marq:
    print('   p%-4d gras=%-5s %s' % (i, b, t))

print('\n--- titres de sections ---')
for i, p in enumerate(ps):
    t = p.text.strip()
    if re.match(r'^\d{1,2}\.\s+[A-ZÉ]', t) and p.runs and p.runs[0].bold and p.runs[0].font.size and p.runs[0].font.size.pt >= 20:
        print('   p%-4d %s' % (i, t[:70]))

print('\n--- tableaux ---')
for ti, tb in enumerate(d.tables):
    print('  tableau', ti, ':', len(tb.rows), 'lignes x', len(tb.columns), 'colonnes')
    for r in tb.rows:
        print('     |', ' | '.join(c.text.strip()[:46] for c in r.cells))

print('\n--- chiffres cles presents ---')
for mot in ('7 jours ouvrables', '24 participants', '30', '4 jours', '3 jours', 'Ampefy', 'français', 'malagasy'):
    print('   %-20s %s' % (mot, any(mot.lower() in p.text.lower() for p in ps)))

# 3. ecrire la version corrigee
out = pathlib.Path('modele-OFFRE-TECH-L01.docx')
d.save(str(out))
print('\nECRIT:', out.name, out.stat().st_size, 'octets')

with zipfile.ZipFile(out) as z:
    xml2 = z.read('word/document.xml').decode('utf-8')
for m in re.finditer(r'<w:p\b.*?</w:p>', xml2, re.S):
    if 'Sommaire' in m.group(0):
        print('pPr final:', re.search(r'<w:pPr>.*?</w:pPr>', m.group(0), re.S).group(0))
        break
