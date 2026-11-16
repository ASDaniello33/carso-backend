from pathlib import Path
import re, docx

cwd = Path('.').resolve()
print('FICHIERS:', [p.name for p in cwd.iterdir()])
cands = sorted([p for p in cwd.iterdir() if p.suffix == '.docx'])
print('CANDIDATS:', [p.name for p in cands])

target = None
for p in cands:
    if 'L99' in p.name:
        target = p
print('CIBLE:', target.name if target else None)

d = docx.Document(str(target))
paras = d.paragraphs
print('nb paragraphes:', len(paras), '| nb tableaux:', len(d.tables))

bad = ['Tel qu', 'rooms', 'adapted', 'adapté à', "who's", '辨别', 'en_identifier', 'elaborated', 'assurance le', 'indicates', 'mobilized', 'toute_', 'pertains', 'production/utilisable']
for i, p in enumerate(paras, 1):
    t = p.text
    for b in bad:
        if b in t:
            print(f'!! P{i}: {t[:200]}')
            break

print('--- derniers paragraphes ---')
for i, p in enumerate(paras[-8:], len(paras)-7):
    print(f'P{i}: {p.text[:160]!r}')
