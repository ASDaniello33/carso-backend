import copy, pathlib, re, zipfile
from docx import Document
from docx.oxml.ns import qn

dossier = pathlib.Path(r'C:\Users\Asus\Documents\CARSO_AI\backend\var\storage\carso\offres\f12fb5d5-0a4b-4eee-a918-1d4f31810e26')
versions = sorted(dossier.glob('modele-OFFRE-TECH-L01_v*.docx'), key=lambda f: f.stat().st_mtime)
src = versions[-1]
print('SOURCE:', src.name)

d = Document(str(src))
XMLSPACE = '{http://www.w3.org/XML/1998/namespace}space'

def set_run_text(r, text):
    for t in r.findall(qn('w:t')):
        r.remove(t)
    t = r.makeelement(qn('w:t'), {})
    t.text = text
    if text != text.strip():
        t.set(XMLSPACE, 'preserve')
    r.append(t)

def set_bold(r, bold):
    rPr = r.find(qn('w:rPr'))
    if rPr is None:
        rPr = r.makeelement(qn('w:rPr'), {})
        r.insert(0, rPr)
    b = rPr.find(qn('w:b'))
    if b is None:
        b = rPr.makeelement(qn('w:b'), {})
        rPr.insert(0, b)
    b.set(qn('w:val'), '1' if bold else '0')

def remplace_paragraph(p, segments):
    """segments : liste de (texte, gras)"""
    new_p = copy.deepcopy(p._p)
    gabarit = None
    for r in new_p.findall(qn('w:r')):
        if r.find(qn('w:t')) is not None:
            gabarit = r
            break
    for r in list(new_p.findall(qn('w:r'))):
        new_p.remove(r)
    for texte, gras in segments:
        if gabarit is None:
            r = new_p.makeelement(qn('w:r'), {})
        else:
            r = copy.deepcopy(gabarit)
            gabarit = r
        set_run_text(r, texte)
        set_bold(r, gras)
        new_p.append(r)
    p._p.getparent().replace(p._p, new_p)
    return new_p

def find(prefix):
    for p in d.paragraphs:
        if p.text.strip().startswith(prefix):
            return p
    return None

# --- 1. Presentation du soumissionnaire : formulation prudente ---
p_carso = find('Le CARSO (Centre d')
p_site = find('Site officiel : carso.mg')
print('p_carso:', bool(p_carso), '| p_site:', bool(p_site))

remplace_paragraph(p_carso, [
 ("Le CARSO, Centre d'Affaires de la Région Sud-Ouest, est le soumissionnaire de la présente offre. "
  "Sa forme juridique, son adresse de rattachement, son statut d'enregistrement ainsi que son "
  "organisation interne sont précisés ci-dessous et justifiés en annexe.", False),
 (" (à compléter : préciser la forme juridique du CARSO, son siège, son numéro d'enregistrement "
  "et l'institution de rattachement)", True),
])

remplace_paragraph(p_site, [
 ("Coordonnées du soumissionnaire : ", False),
 ("(à compléter : renseigner l'adresse, le site internet et les contacts du CARSO)", True),
])

# --- 2. Conclusion : retirer la reference non verifiable au site ---
p_concl = find('Le CARSO se tient à la disposition du FID')
remplace_paragraph(p_concl, [
 ("Le CARSO se tient à la disposition du FID pour toute information complémentaire, et pour "
  "l organised de la présente mission dans les conditions prévues par les termes de référence.", False),
])
print('conclusion mise a jour')

out = pathlib.Path('modele-OFFRE-TECH-L01.docx')
d.save(str(out))

print('\n================ AUDIT ================')
d3 = Document(str(out))
print('paragraphes:', len(d3.paragraphs), '| tableaux:', len(d3.tables))

res = []
for i, p in enumerate(d3.paragraphs):
    if re.search(r'<(p|strong|span|div|table|br)[ >/]', p.text):
        res.append((i, p.text[:70]))
print('HTML residuel:', res if res else 'AUCUN')

bad = []
for i, p in enumerate(d3.paragraphs):
    for r in p.runs:
        if 'à compléter' in r.text and r.bold is not True:
            bad.append((i, r.text[:55]))
for tb in d3.tables:
    for r in tb.rows:
        for c in r.cells:
            for p in c.paragraphs:
                for run in p.runs:
                    if 'à compléter' in run.text and run.bold is not True:
                        bad.append(('tbl', run.text[:55]))
print('marqueurs non gras:', bad if bad else 'AUCUN')

# mentions non etayees restantes
for terme in ('MIARY', 'Pôles Intégrés', 'carso.mg', '+261', 'Chambre de Commerce', 'GIE'):
    occurrences = [i for i, p in enumerate(d3.paragraphs) if terme in p.text]
    print('  mention %-18s ->' % terme, occurrences if occurrences else 'AUCUNE')

with zipfile.ZipFile(out) as z:
    media = [n for n in z.namelist() if n.startswith('word/media/')]
print('medias:', media)
print('sections niveau 1:', sum(1 for p in d3.paragraphs if re.match(r'^\d{1,2}\.\s+[A-ZÉ]', p.text.strip())
      and p.runs and p.runs[0].bold and p.runs[0].font.size and p.runs[0].font.size.pt >= 20))
print('Sommaire style:', [p.style.name for p in d3.paragraphs if p.text.strip() == 'Sommaire'])
print('\n--- section 1 relue ---')
for i, p in enumerate(d3.paragraphs):
    if p.text.strip().startswith('1. Présentation') and p.runs and p.runs[0].font.size and p.runs[0].font.size.pt >= 20:
        for j in range(i, i + 6):
            print('   p%-4d %s' % (j, d3.paragraphs[j].text[:130]))
        break
print('\nECRIT:', out.name, out.stat().st_size, 'octets')
