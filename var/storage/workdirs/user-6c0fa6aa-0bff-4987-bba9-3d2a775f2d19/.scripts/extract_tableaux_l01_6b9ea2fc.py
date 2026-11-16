from pathlib import Path
import docx

base = Path('..').resolve()
cible = base / 'modele-OFFRE-TECH-L01.docx'
d = docx.Document(str(cible))
print('PARAGRAPHES:', len(d.paragraphs), 'TABLEAUX:', len(d.tables))
for ti, t in enumerate(d.tables, 1):
    print(f'=== TABLEAU {ti} : {len(t.rows)} lignes x {len(t.columns)} colonnes ===')
    for ri, row in enumerate(t.rows):
        cells = [c.text.strip().replace('\n', ' / ') for c in row.cells]
        print(f'  L{ri}: ' + ' || '.join(cells))
