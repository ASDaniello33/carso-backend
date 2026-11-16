import json, re
from pathlib import Path

base = Path('..')
print('Fichiers visibles dans le dossier metier :')
try:
    for p in sorted(base.rglob('*')):
        if p.is_file():
            print(' -', p.relative_to(base), p.stat().st_size)
except Exception as e:
    print('listing impossible:', e)

# 1) PDF : extraction de la couche texte avec pypdf
pdf = base / 'REG_AMI_UNIPOD_PlateformeUnipod.pdf'
if pdf.exists():
    try:
        from pypdf import PdfReader
        r = PdfReader(str(pdf))
        print('\nPDF: nb_pages =', len(r.pages))
        for i, page in enumerate(r.pages[:12], 1):
            try:
                t = page.extract_text() or ''
            except Exception as e:
                t = '[extraction impossible: %s]' % e
            print('\n--- page %d (%d caracteres) ---' % (i, len(t)))
            print(t[:3000])
    except Exception as e:
        print('PDF non lisible:', e)
else:
    print('PDF absent du dossier')

# 2) XLSX : lecture brute du zip (sans openpyxl)
for nom in ['suivi_budget_hi_lab.xlsx', 'suivi_budget_hi_lab_v2.xlsx']:
    f = base / nom
    if not f.exists():
        continue
    print('\n===== XLSX', nom, '=====')
    try:
        import zipfile
        z = zipfile.ZipFile(str(f))
        names = z.namelist()
        print('entrees:', len(names))
        for n in names:
            if n.endswith(('.xml', '.rels')) and ('sharedStrings' in n or 'core.xml' in n or 'workbook.xml' == n.split('/')[-1]):
                raw = z.read(n).decode('utf-8', 'ignore')
                txt = re.sub(r'<[^>]+>', ' ', raw)
                txt = re.sub(r'\s+', ' ', txt).strip()
                print('--', n, '=>', txt[:2500])
    except Exception as e:
        print('XLSX non lisible:', e)
