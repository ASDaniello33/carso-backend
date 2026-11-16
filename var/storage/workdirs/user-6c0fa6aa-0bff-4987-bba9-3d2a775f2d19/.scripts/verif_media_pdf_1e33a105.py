from pathlib import Path
import hashlib

cible = Path('/conversation_history/media/9fb40c8db837fcb2.pdf')
original = Path('/storage/appel_proposition/3bf8b917-9017-4f29-94e9-23d2ba41b520/REG_AMI_UNIPOD_PlateformeUnipod.pdf')

for p in (cible, original):
    try:
        data = p.read_bytes()
        print(p, '->', len(data), 'octets, sha256', hashlib.sha256(data).hexdigest()[:16])
    except Exception as e:
        print(p, '-> ILLISIBLE:', type(e).__name__, e)

try:
    from pypdf import PdfReader
    r = PdfReader(str(cible))
    print('media PDF: nb_pages =', len(r.pages))
    t = (r.pages[0].extract_text() or '')[:400]
    print('page 1 (extrait):', t.replace('\n', ' | '))
except Exception as e:
    print('media PDF: lecture impossible:', e)
