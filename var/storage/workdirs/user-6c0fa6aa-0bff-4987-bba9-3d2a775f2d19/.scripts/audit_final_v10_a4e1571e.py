import pathlib, re, zipfile
from docx import Document

base = pathlib.Path(r'C:\Users\Asus\Documents\CARSO_AI\backend\var\storage\carso\offres\f12fb5d5-0a4b-4eee-a918-1d4f31810e26')
v10 = sorted(base.glob('modele-OFFRE-TECH-L01_v*.docx'), key=lambda f: f.stat().st_mtime)[-1]
print('FICHIER:', v10.name)
d = Document(str(v10))
ps = d.paragraphs

full = '\n'.join(p.text for p in ps)
for t in d.tables:
    for row in t.rows:
        for c in row.cells:
            full += '\n' + c.text

print('\n=== TYPographie ===')
print('tiret cadratin (—):', full.count('—'))
print('guillemets doubles:', full.count('«'), full.count('»'))
print('balise HTML residuelle:', re.findall(r'<[a-z/][^>]{0,20}>', full)[:5])
print('double espace dans texte:', sum(1 for p in ps if '  ' in p.text and 'à compléter' not in p.text))

print('\n=== MOTS INTERDITS (base/outil/brouillon) ===')
for w in ['base de donn', 'enregistr', 'outil', 'brouillon', 'à valider par l\'agent', 'TDR ', 'extrait']:
    hits = [p.text[:80] for p in ps if w.lower() in p.text.lower()]
    if hits:
        print(' ', w, '->', hits[:3])

print('\n=== MARQUEURS "a completer" (doivent etre en gras) ===')
for i, p in enumerate(ps):
    if 'à compléter' in p.text:
        ok = all(r.bold for r in p.runs if 'à compléter' in r.text)
        print(' para', i, '| gras OK:', ok, '|', p.text[:70])

print('\n=== RESULTATS ATTENDUS : listes ===')
for i, p in enumerate(ps):
    if p.text.startswith('•\tLes ') or p.text.startswith('Résultat attendu'):
        r0 = p.runs[0] if p.runs else None
        print(' ', i, '| b0=', (r0.bold if r0 is not None else None),
              '| sz=', (round(r0.font.size.pt,1) if (r0 is not None and r0.font.size) else None),
              '|', p.text[:62])

print('\n=== CHIFFRES CLES presents ===')
for pat in ['7 jours ouvrables', '4 jours ouvrables', '3 jours ouvrables', '24 participants', '30 participants',
            '09', '15', 'français', 'malagasy', 'Antananarivo', 'Ampefy']:
    print(' ', pat, '->', full.count(pat))

print('\n=== COHERENCE : 7 jours = 4 + 3 ===')
print('  mentions "7 jours":', full.count('7 jours'), '| "4 jours":', full.count('4 jours'), '| "3 jours":', full.count('3 jours'))

print('\n=== COUVERTURE / SOMMAIRE ===')
for i in range(6, 34):
    t = ps[i].text
    if t.strip():
        r0 = ps[i].runs[0] if ps[i].runs else None
        print(' ', i, '| b=', (r0.bold if r0 is not None else None),
              '| sz=', (round(r0.font.size.pt,1) if (r0 is not None and r0.font.size) else None),
              '|', t[:70])

print('\n=== TITRES DE CORPS ===')
for i, p in enumerate(ps):
    r0 = p.runs[0] if p.runs else None
    if r0 is not None and r0.bold and r0.font.size and round(r0.font.size.pt,1) == 22.0:
        print(' ', i, '|', p.text[:70])
