import copy, pathlib, re, zipfile
from docx import Document
from docx.oxml.ns import qn

dossier = pathlib.Path(r'C:\Users\Asus\Documents\CARSO_AI\backend\var\storage\carso\offres\f12fb5d5-0a4b-4eee-a918-1d4f31810e26')
versions = sorted(dossier.glob('modele-OFFRE-TECH-L01_v*.docx'), key=lambda f: f.stat().st_mtime)
src = versions[-1]
print('SOURCE:', src.name)

d = Document(str(src))
paras = d.paragraphs

def find(prefix):
    for p in paras:
        if p.text.strip().startswith(prefix):
            return p
    raise SystemExit('introuvable: ' + prefix)

p_cible = find('Les pièces justificatives exigées')
p_sstitre = find('6.1. Vague 1')
p_corps = find('Le CARSO comprend que la formation')
print('CIBLE  :', p_cible.text[:60])
print('SOUSTIT:', p_sstitre.text[:60])
print('CORPS  :', p_corps.text[:60])

XMLSPACE = '{http://www.w3.org/XML/1998/namespace}space'

def set_run_text(r, text):
    for t in r.findall(qn('w:t')):
        r.remove(t)
    t = r.makeelement(qn('w:t'), {})
    t.text = text
    if text != text.strip():
        t.set(XMLSPACE, 'preserve')
    r.append(t)

def set_bold(r, bold):
    rPr = r.find(qn('w:rPr'))
    if rPr is None:
        rPr = r.makeelement(qn('w:rPr'), {})
        r.insert(0, rPr)
    b = rPr.find(qn('w:b'))
    if b is None:
        b = rPr.makeelement(qn('w:b'), {})
        rPr.insert(0, b)
    b.set(qn('w:val'), '1' if bold else '0')

def fill_cell(tc, text, bold):
    p = tc.findall(qn('w:p'))[0]
    gabarit = None
    for r in p.findall(qn('w:r')):
        if r.find(qn('w:t')) is not None:
            gabarit = r
            break
    for r in list(p.findall(qn('w:r'))):
        p.remove(r)
    if gabarit is None:
        gabarit = p.makeelement(qn('w:r'), {})
    else:
        gabarit = copy.deepcopy(gabarit)
    set_run_text(gabarit, text)
    set_bold(gabarit, bold)
    p.append(gabarit)

def clone_table(src_tbl, entete, lignes):
    new = copy.deepcopy(src_tbl)
    trs = new.findall(qn('w:tr'))
    proto_h, proto_d = trs[0], trs[1]
    for tr in trs:
        new.remove(tr)
    for tc, txt in zip(proto_h.findall(qn('w:tc')), entete):
        fill_cell(tc, txt, True)
    new.append(proto_h)
    for ligne in lignes:
        tr = copy.deepcopy(proto_d)
        for tc, txt in zip(tr.findall(qn('w:tc')), ligne):
            fill_cell(tc, txt, txt.strip().startswith('(à compléter'))
        new.append(tr)
    return new

def clone_paragraph(proto, texte):
    new = copy.deepcopy(proto._p)
    gabarit = None
    for r in new.findall(qn('w:r')):
        if r.find(qn('w:t')) is not None:
            gabarit = r
            break
    for r in list(new.findall(qn('w:r'))):
        new.remove(r)
    if gabarit is None:
        gabarit = new.makeelement(qn('w:r'), {})
    else:
        gabarit = copy.deepcopy(gabarit)
    set_run_text(gabarit, texte)
    new.append(gabarit)
    return new

tbl_proto = d.tables[1]._tbl

CRIT_CAB = ('Critère', 'Exigence des termes de référence', 'Réponse et justificatif à fournir')
CAB = [
 ('Existence légale',
  "Avoir une existence légale d'au moins trois (03) ans à la date de soumission de l'offre",
  "(à compléter : joindre le justificatif d'existence légale du cabinet ou centre de formation et indiquer sa date de création)"),
 ('Volume de formations professionnelles',
  "Avoir animé au moins six (06) formations professionnelles au cours des trois (03) dernières années",
  "(à compléter : indiquer l'intitulé, la date et l'effectif de chacune des formations animées)"),
 ('Personnes formées et attestées',
  "Avoir formé et fait certifier ou attester au moins vingt (20) personnes au cours des trois (03) dernières années",
  "(à compléter : préciser le nombre de personnes formées et joindre les attestations correspondantes)"),
 ('Formations spécifiques en IA',
  "Avoir animé au moins deux (02) formations en intelligence artificielle, IA générative, automatisation, transformation digitale ou thématiques similaires",
  "(à compléter : identifier l'intitulé et la date des deux formations concernées)"),
 ('Missions de formation ou d’accompagnement',
  "Avoir réalisé au moins deux (02) missions de formation ou d'accompagnement auprès d'institutions publiques, parapubliques, projets de développement, ONG, entreprises ou organisations similaires",
  "(à compléter : préciser l'intitulé, le commanditaire et la période des deux missions)"),
]

CRIT_FORM = ('Qualification ou expérience', 'Exigence des termes de référence', 'Réponse et justificatif à fournir')
FORM = [
 ('Niveau de formation',
  "Être titulaire d'un niveau au moins équivalent à Master 2 (BACC +5) en informatique ou dans une filière similaire",
  "(à compléter : indiquer le niveau et le diplôme de chaque formateur mobilisé)"),
 ('Certificat ou attestation',
  "Être titulaire d'un certificat ou attestation de formation dans l'un des domaines suivants : intelligence artificielle, transformation digitale, informatique, systèmes d'information, analyse de données, développement informatique ou domaine équivalent",
  "(à compléter : joindre le certificat ou l'attestation de chaque formateur mobilisé)"),
 ('Expérience professionnelle',
  "Disposer d'au moins cinq (05) ans d'expérience professionnelle dans le domaine de la digitalisation, de la transformation digitale, des systèmes d'information, de l'informatique ou de l'accompagnement numérique des organisations",
  "(à compléter : préciser l'expérience professionnelle de chaque formateur mobilisé)"),
 ('Volume de formations animées',
  "Avoir déjà animé au moins quinze (15) formations portant sur des outils informatiques, outils numériques, logiciels professionnels, analyse de données, bureautique avancée, systèmes d'information ou thématiques similaires",
  "(à compléter : lister les quinze formations animées et les dates correspondantes)"),
 ('Formations spécifiques sur l’IA',
  "Parmi ces quinze (15) formations, avoir animé au moins deux (02) formations portant spécifiquement sur l'intelligence artificielle générative, les agents d'IA, l'automatisation ou les usages professionnels de l'intelligence artificielle",
  "(à compléter : identifier les deux formations concernées parmi les quinze)"),
 ('Aptitude pédagogique',
  "Avoir une bonne capacité pédagogique et une expérience dans l'animation de formations destinées à des publics mixtes, comprenant à la fois des profils techniques et des profils métiers",
  "(à compléter : décrire l'expérience des formateurs mobilisés auprès de publics mixtes)"),
]

blocs = []
blocs.append(clone_paragraph(p_sstitre, '1.1. Profil du cabinet ou centre de formation (critères du § 10 des termes de référence)'))
blocs.append(clone_paragraph(p_corps, "Les termes de référence (§ 10) imposent au cabinet ou centre de formation de justifier des critères minimums. Le tableau ci-dessous en reprend l'intégralité ; les justificatifs correspondants sont à joindre en annexe de la présente offre, conformément au paragraphe précédent."))
blocs.append(clone_table(tbl_proto, CRIT_CAB, CAB))
blocs.append(clone_paragraph(p_sstitre, '1.2. Profil du ou des formateurs (critères du § 11 des termes de référence)'))
blocs.append(clone_paragraph(p_corps, "Les termes de référence (§ 11) imposent de proposer un ou plusieurs formateurs disposant d'un profil adapté aux objectifs de la formation, avec les qualifications et expériences minimales suivantes. La langue de travail, français et malagasy, est traitée au paragraphe précédent."))
blocs.append(clone_table(tbl_proto, CRIT_FORM, FORM))

curseur = p_cible._p
for el in blocs:
    curseur.addnext(el)
    curseur = el
print('6 blocs inseres apres le paragraphe de reference')

out = pathlib.Path('modele-OFFRE-TECH-L01.docx')
d.save(str(out))

print('\n================ AUDIT ================')
d3 = Document(str(out))
print('paragraphes:', len(d3.paragraphs), '| tableaux:', len(d3.tables))
for ti, tb in enumerate(d3.tables):
    print('\n-- tableau %d : %d lignes x %d colonnes --' % (ti, len(tb.rows), len(tb.columns)))
    for r in tb.rows:
        print('   |', ' | '.join(c.text.strip()[:55] for c in r.cells))

res = []
for i, p in enumerate(d3.paragraphs):
    if re.search(r'<(p|strong|span|div|table|br)[ >/]', p.text):
        res.append((i, p.text[:60]))
print('\nHTML residuel:', res if res else 'AUCUN')

bad = []
for tb in d3.tables:
    for r in tb.rows:
        for c in r.cells:
            for p in c.paragraphs:
                for run in p.runs:
                    if 'à compléter' in run.text and run.bold is not True:
                        bad.append((run.text[:50], run.bold))
print('marqueurs non gras dans les tableaux:', bad if bad else 'AUCUN')

with zipfile.ZipFile(out) as z:
    media = [n for n in z.namelist() if n.startswith('word/media/')]
print('medias:', media)

print('\n1.1 present:', any('1.1.' in p.text for p in d3.paragraphs),
      '| 1.2 present:', any('1.2.' in p.text for p in d3.paragraphs))
nsect = sum(1 for p in d3.paragraphs if re.match(r'^\d{1,2}\.\s+[A-ZÉ]', p.text.strip())
            and p.runs and p.runs[0].bold and p.runs[0].font.size and p.runs[0].font.size.pt >= 20)
print('sections de niveau 1 (1 a 10):', nsect)
print('Sommaire style:', [p.style.name for p in d3.paragraphs if p.text.strip() == 'Sommaire'])
print('\nECRIT:', out.name, out.stat().st_size, 'octets')
