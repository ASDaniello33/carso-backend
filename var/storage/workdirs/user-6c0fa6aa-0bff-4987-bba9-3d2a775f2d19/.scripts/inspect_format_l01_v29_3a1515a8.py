from pathlib import Path
import re, docx

base = Path('..').resolve()
d = docx.Document(str(base / 'modele-OFFRE-TECH-L01_v29.docx'))
print('PARA:', len(d.paragraphs), 'TAB:', len(d.tables))
for i, p in enumerate(d.paragraphs, 1):
    if p.text.strip() in ('Sommaire', '1. Présentation du soumissionnaire', '2. Compréhension des termes de référence',
                          'Le CARSO – Centre d’Affaires de la Région Sud-Ouest – est le soumissionnaire de la présente offre. Il s’agit d’un groupement d’intérêt économique (GIE) installé à la CCI de Toliara (Tsimenatsy, Toliara 1, Madagascar), issu du programme MIARY et du projet PIC (Pôles Intégrés de Croissance), qui accompagne les opérateurs économiques de la région Atsimo-Andrefana par l’accompagnement entrepreneurial, le renforcement des compétences et l’appui stratégique. Sa forme juridique, son adresse de rattachement, son statut d’enregistrement ainsi que son organisation interne sont précisés ci-dessous et justifiés en annexe. (à compléter : préciser le numéro d’enregistrement du CARSO et joindre les pièces justificatives correspondantes)'):
        pf = p.paragraph_format
        runs = [(r.text[:30], r.font.name, r.font.size.pt if r.font.size else None, r.font.bold) for r in p.runs][:3]
        print(f'P{i:03d} style={p.style.name!r} align={p.alignment} sp_b={pf.space_before} sp_a={pf.space_after} runs={runs}')
        print('   XML:', p._p.xml[:700].replace('\n',' '))
        print()
t = d.tables[2]
print('=== XML en-tete T3 ===')
print(t.rows[0].cells[0]._tc.xml[:1200])
print('=== XML cellule L2C0 ===')
print(t.rows[1].cells[0]._tc.xml[:800])
print('=== tblPr ===')
print(t._tbl.tblPr.xml[:700])
