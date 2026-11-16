import json, re
from pathlib import Path

cibles = {'mission': 'ff481621-c698-4209-83dd-1b3a2e8f4044',
          'session': 'db96dbd1-0a3d-4723-9322-c6e97c5389cd',
          'ref': 'MISSION-AMI-UNIPOD-2026-LHI-LAB'}

try:
    racine = Path('../../_sources')
    fichiers = sorted(racine.glob('*.json'))
    print('NB JSON dans _sources :', len(fichiers))
except Exception as e:
    print('ACCES REFUSE:', e)
    fichiers = []

for p in fichiers:
    try:
        d = json.loads(p.read_text(encoding='utf-8', errors='ignore'))
    except Exception:
        continue
    meta = d.get('meta') or {}
    titre = str(meta.get('titre', ''))
    ancre = str(meta.get('ancre', ''))
    ent = str(meta.get('entity_id', ''))
    td = str(meta.get('type_document', ''))
    corps = str(meta.get('corps', '') or meta.get('contenu_html', ''))
    blob = titre + ' ' + corps
    pertinent = ('LHI-LAB' in blob) or ('UNIPOD-2026' in blob) or ('hi_lab' in blob) or ('Accompagnement entrepreneurial' in blob)
    if not pertinent:
        continue
    h1 = ''
    m = re.search(r'<h1[^>]*>(.*?)</h1>', corps, re.S)
    if m:
        h1 = re.sub(r'<[^>]+>', '', m.group(1)).strip()
    lead = ''
    m2 = re.search(r"class=['\"]lead['\"][^>]*>(.*?)</p>", corps, re.S)
    if m2:
        lead = re.sub(r'<[^>]+>', '', m2.group(1)).strip()
    liens = []
    for k, v in cibles.items():
        if v in blob:
            liens.append(k)
    print('---')
    print('id       :', d.get('document_id'))
    print('titre    :', titre[:110])
    print('h1       :', h1[:110])
    print('type     :', td, '| ancre:', ancre, '| entity_id:', ent)
    print('lead     :', lead[:260])
    print('dates    :', meta.get('date_document'), '| cree:', d.get('cree'))
    print('lien mission/session :', liens if liens else 'AUCUN')
