from pathlib import Path
import re, docx
from docx.oxml.ns import qn

base = Path('..').resolve()
files = sorted(p for p in base.glob('modele-OFFRE-TECH-L99*.docx'))
print('fichiers:', [p.name for p in files])
target = base / 'modele-OFFRE-TECH-L99_v7.docx'
if not target.exists():
    target = files[-1]
print('cible:', target.name)

d = docx.Document(str(target))

def texts(doc):
    for p in doc.paragraphs:
        yield ('P', p.text)
    for ti, t in enumerate(doc.tables, 1):
        for row in t.rows:
            for c in row.cells:
                yield (f'T{ti}', c.text)

allt = list(texts(d))
full = '\n'.join(x[1] for x in allt)

print('paragraphes:', len(d.paragraphs), '| tableaux:', len(d.tables))

print('=== PLACEHOLDERS A CORRIGER ===')
bad = ['LOL ICI', 'Lieu + date', 'Entreprise (ex', 'Contenus commence ici',
       'Tel qu', 'rooms', 'adapted', "who's", '辨别', 'en_identifier', 'elaborated',
       'assurance le', 'indicates', 'mobilized', 'toute_', 'pertains', 'production/utilisable',
       'treated', 'régulation des', 'de ne pas dedicated', 'anonymat des']
hits = 0
for kind, t in allt:
    for b in bad:
        if b in t:
            print(f'  [{kind}] {b!r} -> {t[:230]}')
            hits += 1
            break
print('  total:', hits)

print('=== PRESENCE DES MOTS-CLES ===')
for kw in ['24', '30', '7 jours', '4 jours', '3 jours', 'présentiel', 'participatif',
           'Note méthodologique', 'Note de bonnes pratiques', 'Rapport final de formation',
           'Supports de formation électroniques', "Résultats de l'évaluation des acquis",
           'Exercices pratiques', 'vague 1', 'vague 2', 'malagasy', 'Toliara']:
    print(f'  {kw!r}: {full.count(kw)}')

print('=== ETAT MARQUEURS ===')
for m in ['LOL ICI', 'Lieu + date', 'Entreprise (ex', 'Contenus commence ici']:
    print(f'  {m!r}: {full.count(m)}')

print('=== NON-ASCII SUSPECTS ===')
sus = set(re.findall(r'[^\x00-\x7F]', full))
print(' ', sorted(sus))
