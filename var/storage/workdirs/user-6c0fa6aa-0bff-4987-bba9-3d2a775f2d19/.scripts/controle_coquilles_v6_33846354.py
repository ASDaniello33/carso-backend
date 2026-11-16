from pathlib import Path
import docx

base = Path('..').resolve()
target = base / 'modele-OFFRE-TECH-L99_v6.docx'
d = docx.Document(str(target))

bad = ['Tel qu', 'rooms', 'adapted', "who's", '辨别', 'en_identifier', 'elaborated',
       'assurance le', 'indicates', 'mobilized', 'toute_', 'pertains',
       'production/utilisable', 'le RID', 'En commun']

print('=== PARAGRAPHES ===')
for i, p in enumerate(d.paragraphs, 1):
    for b in bad:
        if b in p.text:
            print(f'P{i}: {p.text}')
            break

print('=== TABLEAUX (cellules) ===')
for ti, t in enumerate(d.tables, 1):
    for ri, row in enumerate(t.rows, 1):
        for ci, c in enumerate(row.cells, 1):
            for b in bad:
                if b in c.text:
                    print(f'T{ti} r{ri} c{ci}: {c.text[:250]}')
                    break

print('=== COMPTE ===')
print('paragraphes:', len(d.paragraphs), '| tableaux:', len(d.tables))
print('derniers paragraphes:')
for i, p in enumerate(d.paragraphs[-4:], len(d.paragraphs)-3):
    print(f'  P{i}: {p.text[:120]!r}')
