from pathlib import Path
import re, docx
from docx.oxml.ns import qn

base = Path('..').resolve()
files = sorted(p for p in base.glob('modele-OFFRE-TECH-L99*.docx'))
target = base / 'modele-OFFRE-TECH-L99_v8.docx'
if not target.exists():
    target = files[-1]
print('cible:', target.name, '| versions:', [p.name for p in files])

d = docx.Document(str(target))

def all_text(doc):
    out = []
    for p in doc.paragraphs:
        out.append(p.text)
    for t in doc.tables:
        for row in t.rows:
            for c in row.cells:
                out.append(c.text)
    return '\n'.join(out)

full = all_text(d)

print('paragraphes:', len(d.paragraphs), '| tableaux:', len(d.tables))
print('images (inline):', len(d.inline_shapes))

print('=== RESIDUS ===')
bad = ['LOL ICI', 'Lieu + date', 'Entreprise (ex', 'Contenus commence ici',
       'Tel qu', 'rooms', 'adapted', "who's", '辨别', 'en_identifier', 'elaborated',
       'assurance le', 'indicates', 'mobilized', 'toute_', 'pertains', 'production/utilisable',
       'treated', 'desonymat', 'dedicated']
resid = [b for b in bad if b in full]
print(' ', resid if resid else 'AUCUN')

print('=== COUVERTURE EXIGENCES ===')
checks = {
 'vague 1 - 24 participants': '24 participants',
 'vague 2 - 30 participants': '30 participants',
 '9 Suivi-Evaluation': '09 membres du Suivi',
 '15 Systeme d\'Information': '15 membres du Système',
 '7 jours ouvrables': '7 jours ouvrables',
 '4 jours (vague 1)': '4 jours ouvrables',
 '3 jours (vague 2)': '3 jours ouvrables',
 'format presentiel': 'présentiel',
 'format participatif': 'participatif',
 'pratique': 'pratique',
 'note methodologique': 'Note méthodologique',
 'note bonnes pratiques': 'Note de bonnes pratiques',
 'responsable securisee ethique': 'responsable, sécurisée et éthique',
 'rapport final': 'Rapport final de formation',
 'supports electroniques': 'Supports de formation électroniques',
 'evaluation acquis vague 1+2': 'Résultats de l\'évaluation des acquis',
 'français': 'français', 'malagasy': 'malagasy',
 'donnees fictives': 'fictives',
 'validation humaine': 'validation humaine',
 'a completer present': 'à compléter',
}
for label, needle in checks.items():
    n = full.count(needle)
    print(f'  {"OK " if n else "MANQUE"} {label} ({n})')

print('=== JOURS PROGRAMMES ===')
for j in ['Jour 1', 'Jour 2', 'Jour 3', 'Jour 4']:
    print(f'  {j}: {full.count(j)}')

print('=== NON-ASCII ===')
print(' ', sorted(set(re.findall(r'[^\x00-\x7F]', full))))
