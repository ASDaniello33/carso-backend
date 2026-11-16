import pathlib, re, zipfile
from docx import Document

dossier = pathlib.Path(r'C:\Users\Asus\Documents\CARSO_AI\backend\var\storage\carso\offres\f12fb5d5-0a4b-4eee-a918-1d4f31810e26')
v = sorted(dossier.glob('modele-OFFRE-TECH-L01_v*.docx'), key=lambda f: f.stat().st_mtime)[-1]
print('FICHIER:', v.name, '|', v.stat().st_size, 'octets')

# integrite du paquet
with zipfile.ZipFile(v) as z:
    bad = z.testzip()
    print('paquet DOCX valide:', bad is None)
    media = [n for n in z.namelist() if n.startswith('word/media/')]
    print('medias:', media)

d = Document(str(v))
ps = d.paragraphs
print('paragraphes:', len(ps), '| tableaux:', len(d.tables), '| sections:', len(d.sections))

print('\n=== COUVERTURE ===')
for i in range(0, 21):
    t = ps[i].text.strip()
    if t:
        r = ps[i].runs[0] if ps[i].runs else None
        print('  p%-3d sz=%-6s %s' % (i, (r.font.size.pt if r and r.font.size else '-'), t[:80]))

print('\n=== SOMMAIRE (10 entrees) ===')
start = next(i for i, p in enumerate(ps) if p.text.strip() == 'Sommaire')
ent = [ps[j].text.strip() for j in range(start + 1, start + 12) if ps[j].text.strip()]
print('  style Sommaire:', ps[start].style.name)
print('  entrees:', len(ent))
for e in ent:
    print('   -', e)

print('\n=== SECTIONS REELLES ===')
secs = [p.text.strip() for p in ps
        if re.match(r'^\d{1,2}\.\s+[A-ZÉÀ]', p.text.strip())
        and p.runs and p.runs[0].bold and p.runs[0].font.size and p.runs[0].font.size.pt >= 20]
for s in secs:
    print('  ', s)

print('\n=== SOUS-SECTIONS ===')
for p in ps:
    t = p.text.strip()
    if re.match(r'^\d\.\d\.\s', t) or t.startswith('Jour '):
        print('  ', t[:78])

print('\n=== TABLEAUX ===')
for ti, tb in enumerate(d.tables):
    print('  T%d : %d lignes x %d colonnes | en-tete: %s' % (
        ti, len(tb.rows), len(tb.columns),
        ' | '.join(c.text.strip()[:22] for c in tb.rows[0].cells)))

print('\n=== CHIFFRES CLES (fideles aux TDR) ===')
tout = '\n'.join(p.text for p in ps)
for motif in ('24 participants', '30', '09 personnes du Suivi', '15 personnes du Syst',
              '4 jours ouvrables', '3 jours ouvrables', '7 jours ouvrables',
              'Ampefy', 'français et le malagasy', 'six livrables'):
    print('  %-30s %s' % (motif, 'OK' if motif in tout else 'ABSENT'))

print('\n=== PLACEHOLDERS (a completer) ===')
n = 0
for i, p in enumerate(ps):
    for r in p.runs:
        if 'à compléter' in r.text:
            n += 1
            print('  p%-4d %s' % (i, r.text[:88]))
for tb in d.tables:
    for r in tb.rows:
        for c in r.cells:
            for p in c.paragraphs:
                for run in p.runs:
                    if 'à compléter' in run.text:
                        n += 1
                        print('  [tbl] %s' % run.text[:88])
print('  TOTAL marqueurs:', n)

print('\n=== CONTROLE FINAL ===')
print('  HTML residuel      :', 'AUCUN' if not [p for p in ps if re.search(r'<(p|strong|span|div|table|br)[ >/]', p.text)] else 'PRESENT')
print('  tiret cadratin     :', 'AUCUN' if '—' not in tout else 'PRESENT')
print('  logo conserve      :', 'word/media/image1.png' in media)
print('  Sommaire en Heading 1 :', ps[start].style.name == 'Heading 1')
print('  sections 1 a 10    :', len(secs) == 10)
print('  tables = 4         :', len(d.tables) == 4)
