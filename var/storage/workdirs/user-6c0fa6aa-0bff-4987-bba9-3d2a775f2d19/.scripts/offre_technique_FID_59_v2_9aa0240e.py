from docx import Document
from docx.shared import Pt, Cm, Inches, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.oxml.ns import qn, nsdecls
from docx.oxml import parse_xml

doc = Document()

# ============================================================
# CONFIGURATION GLOBALE
# ============================================================
section = doc.sections[0]
section.top_margin = Cm(2.54)
section.bottom_margin = Cm(2.54)
section.left_margin = Cm(3.175)
section.right_margin = Cm(3.175)

style = doc.styles['Normal']
font = style.font
font.name = 'Times New Roman'
font.size = Pt(14)
font.color.rgb = RGBColor(0, 0, 0)
pf = style.paragraph_format
pf.space_after = Pt(6)
pf.space_before = Pt(0)
pf.line_spacing = 1.15

# ============================================================
# EN-TÊTE ET PIED DE PAGE (avant le contenu)
# ============================================================
header = section.header
header.is_linked_to_previous = False
p = header.paragraphs[0]
p.alignment = WD_ALIGN_PARAGRAPH.CENTER
run = p.add_run('OFFRE TECHNIQUE – N°59/AMI/FID/FSR/SQC_CAB_FORM_IA – CARSO')
run.font.size = Pt(8)
run.font.color.rgb = RGBColor(128, 128, 128)
run.font.name = 'Times New Roman'

footer = section.footer
footer.is_linked_to_previous = False
p = footer.paragraphs[0]
p.alignment = WD_ALIGN_PARAGRAPH.CENTER
run = p.add_run('CARSO – ')
run.font.size = Pt(8)
run.font.color.rgb = RGBColor(128, 128, 128)
run.font.name = 'Times New Roman'
run = p.add_run('Page ')
run.font.size = Pt(8)
run.font.color.rgb = RGBColor(128, 128, 128)
run.font.name = 'Times New Roman'
# Numéro de page avec champ
fldChar1 = parse_xml(f'<w:fldChar {nsdecls("w")} w:fldCharType="begin"/>')
p._p.append(fldChar1)
instrText = parse_xml(f'<w:instrText {nsdecls("w")} xml:space="preserve"> PAGE </w:instrText>')
p._p.append(instrText)
fldChar2 = parse_xml(f'<w:fldChar {nsdecls("w")} w:fldCharType="end"/>')
p._p.append(fldChar2)

# ============================================================
# PAGE DE COUVERTURE
# ============================================================
p = doc.add_paragraph()
p.alignment = WD_ALIGN_PARAGRAPH.CENTER
run = p.add_run('[LOGO CARSO]')
run.font.size = Pt(10)
run.font.color.rgb = RGBColor(128, 128, 128)
run.font.italic = True

p = doc.add_paragraph()
p.alignment = WD_ALIGN_PARAGRAPH.CENTER
p.paragraph_format.space_before = Pt(24)
run = p.add_run('OFFRE TECHNIQUE')
run.font.size = Pt(26)
run.font.bold = True
run.font.name = 'Times New Roman'
run.font.color.rgb = RGBColor(0, 51, 102)

p = doc.add_paragraph()
p.alignment = WD_ALIGN_PARAGRAPH.CENTER
p.paragraph_format.space_before = Pt(12)
run = p.add_run('Recrutement d\'un cabinet ou centre de formation')
run.font.size = Pt(16)
run.font.name = 'Times New Roman'
run.font.color.rgb = RGBColor(0, 51, 102)

p = doc.add_paragraph()
p.alignment = WD_ALIGN_PARAGRAPH.CENTER
p.paragraph_format.space_before = Pt(6)
run = p.add_run('Formation sur l\'intelligence artificielle appliquée aux usages professionnels,\nà l\'analyse de données, aux agents IA et à l\'aide à la décision')
run.font.size = Pt(14)
run.font.name = 'Times New Roman'
run.font.italic = True
run.font.color.rgb = RGBColor(51, 51, 51)

infos = [
    ('Référence de l\'appel', 'N°59/AMI/FID/FSR/SQC_CAB_FORM_IA'),
    ('Organisation', 'Fonds d\'Intervention pour le Développement (FID)'),
    ('Soumissionnaire', 'CARSO – Centre d\'Affaires de la Région Sud-Ouest'),
    ('Date de soumission', '6 novembre 2026'),
    ('Lieu de formation', 'À préciser par le FID'),
]

for label, valeur in infos:
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_before = Pt(8)
    run = p.add_run(label + ' : ')
    run.font.size = Pt(12)
    run.font.bold = True
    run.font.name = 'Times New Roman'
    run = p.add_run(valeur)
    run.font.size = Pt(12)
    run.font.name = 'Times New Roman'

p = doc.add_paragraph()
p.paragraph_format.space_before = Pt(40)
p.alignment = WD_ALIGN_PARAGRAPH.CENTER
run = p.add_run('CARSO – Centre d\'Affaires de la Région Sud-Ouest')
run.font.size = Pt(11)
run.font.name = 'Times New Roman'
run = p.add_run('\nCCI Tsimenatsy, Toliara 1, Madagascar')
run.font.size = Pt(10)
run.font.name = 'Times New Roman'
run = p.add_run('\ncarso@carso.mg | +261 34 61 496 36 | carso.mg')
run.font.size = Pt(10)
run.font.name = 'Times New Roman'

# ============================================================
# SAUT DE PAGE + SOMMAIRE
# ============================================================
doc.add_page_break()

p = doc.add_paragraph()
p.alignment = WD_ALIGN_PARAGRAPH.CENTER
run = p.add_run('SOMMAIRE')
run.font.size = Pt(22)
run.font.bold = True
run.font.name = 'Times New Roman'

p = doc.add_paragraph()
p.paragraph_format.space_before = Pt(16)
run = p.add_run('1.  Synthèse de la proposition .................................................................................... 1')
run.font.size = Pt(12)
run.font.name = 'Times New Roman'
run = p.add_run('\n2.  Compréhension des termes de référence ............................................................ 2')
run.font.size = Pt(12)
run.font.name = 'Times New Roman'
run = p.add_run('\n3.  Objectifs visés ........................................................................................................ 3')
run.font.size = Pt(12)
run.font.name = 'Times New Roman'
run = p.add_run('\n4.  Méthodologie pédagogique ..................................................................................... 4')
run.font.size = Pt(12)
run.font.name = 'Times New Roman'
run = p.add_run('\n5.  Programme détaillé – Vague 1 (SI/PSE) ............................................................... 5')
run.font.size = Pt(12)
run.font.name = 'Times New Roman'
run = p.add_run('\n6.  Programme détaillé – Vague 2 (Directeurs/Chefs de service) .............................. 7')
run.font.size = Pt(12)
run.font.name = 'Times New Roman'
run = p.add_run('\n7.  Organisation et équipe de formation .................................................................... 8')
run.font.size = Pt(12)
run.font.name = 'Times New Roman'
run = p.add_run('\n8.  Livrables attendus ................................................................................................ 9')
run.font.size = Pt(12)
run.font.name = 'Times New Roman'
run = p.add_run('\n9.  Planning et jalons ................................................................................................. 10')
run.font.size = Pt(12)
run.font.name = 'Times New Roman'
run = p.add_run('\n10. Moyens techniques et logistique .......................................................................... 11')
run.font.size = Pt(12)
run.font.name = 'Times New Roman'
run = p.add_run('\n11. Qualifications du cabinet et références .............................................................. 12')
run.font.size = Pt(12)
run.font.name = 'Times New Roman'
run = p.add_run('\n12. Analyse des risques et mesures d\'atténuation .................................................... 13')
run.font.size = Pt(12)
run.font.name = 'Times New Roman'
run = p.add_run('\n13. Conclusion et engagement .................................................................................. 14')
run.font.size = Pt(12)
run.font.name = 'Times New Roman'

# ============================================================
# SECTION 1 : SYNTHÈSE
# ============================================================
doc.add_page_break()

p = doc.add_paragraph()
run = p.add_run('1.  SYNTHÈSE DE LA PROPOSITION')
run.font.size = Pt(18)
run.font.bold = True
run.font.name = 'Times New Roman'
run.font.color.rgb = RGBColor(0, 51, 102)
p.paragraph_format.space_before = Pt(12)

p = doc.add_paragraph()
run = p.add_run(
    'CARSO – Centre d\'Affaires de la Région Sud-Ouest a l\'honneur de soumettre sa présente offre '
    'technique en réponse à l\'appel N°59/AMI/FID/FSR/SQC_CAB_FORM_IA du Fonds d\'Intervention '
    'pour le Développement (FID), relatif au recrutement d\'un cabinet ou centre de formation pour '
    'une formation sur l\'intelligence artificielle appliquée aux usages professionnels.'
)
run.font.size = Pt(14)
run.font.name = 'Times New Roman'

p = doc.add_paragraph()
run = p.add_run(
    'Notre proposition s\'articule autour de deux vagues de formation distinctes, adaptées aux profils '
    'et aux responsabilités des participants :'
)
run.font.size = Pt(14)
run.font.name = 'Times New Roman'

items = [
    'Vague 1 – Équipes SI et PSE (4 jours) : formation approfondie combinant tronc commun et ateliers spécialisés, axée sur l\'analyse de données, les agents IA, l\'automatisation intelligente et l\'aide à la décision.',
    'Vague 2 – Directeurs, Chefs de service et Responsables (3 jours) : initiation progressive et pratique aux usages professionnels et managériaux de l\'IA, de la formulation des prompts à l\'aide à la décision.'
]
for item in items:
    p = doc.add_paragraph(style='List Bullet')
    run = p.add_run(item)
    run.font.size = Pt(14)
    run.font.name = 'Times New Roman'

p = doc.add_paragraph()
run = p.add_run(
    'Notre approche pédagogique privilégie la pratique, la participation et la production de résultats '
    'exploitables directement au sein du FID. Chaque session inclura des exercices guidés, des études '
    'de cas contextualisées, des ateliers de groupe et une évaluation des acquis.'
)
run.font.size = Pt(14)
run.font.name = 'Times New Roman'

# ============================================================
# SECTION 2 : COMPRÉHENSION DES TDR
# ============================================================
p = doc.add_paragraph()
p.paragraph_format.space_before = Pt(16)
run = p.add_run('2.  COMPRÉHENSION DES TERMES DE RÉFÉRENCE')
run.font.size = Pt(18)
run.font.bold = True
run.font.name = 'Times New Roman'
run.font.color.rgb = RGBColor(0, 51, 102)

p = doc.add_paragraph()
run = p.add_run('2.1 Contexte et justification')
run.font.size = Pt(14)
run.font.bold = True
run.font.name = 'Times New Roman'
p.paragraph_format.space_before = Pt(8)

p = doc.add_paragraph()
run = p.add_run(
    'Le FID reconnaît l\'intelligence artificielle comme un levier de modernisation de ses méthodes de '
    'travail, de valorisation des données, de transformation digitale et de renforcement de la performance '
    'opérationnelle. L\'IA peut être mobilisée pour traiter et structurer des données, produire des synthèses, '
    'analyser des tendances, identifier des écarts, assister la rédaction de notes et rapports, préparer des '
    'présentations, automatiser certains processus et mettre en place des agents IA. Elle peut également '
    'appuyer les responsables dans la préparation des réunions, l\'organisation de l\'information et l\'aide '
    'à la décision.'
)
run.font.size = Pt(14)
run.font.name = 'Times New Roman'

p = doc.add_paragraph()
run = p.add_run(
    'Cependant, l\'utilisation de l\'IA doit être encadrée. Les risques liés à la confidentialité des données, '
    'à la qualité des résultats générés, aux biais, aux hallucinations, à la dépendance aux outils externes, '
    'à la sécurité de l\'information et à la responsabilité humaine nécessitent une formation structurée, '
    'pratique et adaptée au contexte institutionnel du FID.'
)
run.font.size = Pt(14)
run.font.name = 'Times New Roman'

p = doc.add_paragraph()
run = p.add_run('2.2 Objectif général')
run.font.size = Pt(14)
run.font.bold = True
run.font.name = 'Times New Roman'
p.paragraph_format.space_before = Pt(8)

p = doc.add_paragraph()
run = p.add_run(
    'Renforcer les capacités des différentes catégories de personnel du FID dans l\'utilisation responsable, '
    'sécurisée et opérationnelle de l\'intelligence artificielle, en adaptant les contenus aux besoins des '
    'équipes SI/PSE et aux usages professionnels et managériaux des Directeurs, Chefs de service et Responsables.'
)
run.font.size = Pt(14)
run.font.name = 'Times New Roman'
run.font.italic = True

p = doc.add_paragraph()
run = p.add_run('2.3 Objectifs spécifiques')
run.font.size = Pt(14)
run.font.bold = True
run.font.name = 'Times New Roman'
p.paragraph_format.space_before = Pt(8)

objectifs_spec = [
    'Développer une compréhension commune des concepts, opportunités, limites et risques liés à l\'intelligence artificielle.',
    'Former les participants à la formulation de prompts clairs, structurés et adaptés à leurs besoins professionnels.',
    'Renforcer les capacités des équipes SI et PSE dans l\'utilisation de l\'IA pour l\'analyse de données, la structuration et l\'interprétation de l\'information, le reporting, les agents IA, l\'automatisation intelligente et l\'aide à la décision.',
    'Appuyer les équipes PSE dans l\'utilisation de l\'IA pour la planification, le suivi des indicateurs, l\'analyse des écarts, l\'automatisation de certains travaux de reporting et l\'aide à la décision.',
    'Appuyer les équipes SI dans la structuration, la sécurisation, l\'intégration, l\'exploitation et l\'automatisation technique des solutions IA.',
    'Initier les Directeurs, Chefs de service et certains Responsables à l\'utilisation générale de l\'IA dans leurs activités professionnelles et managériales.',
    'Permettre aux Directeurs, Chefs de service et Responsables d\'utiliser l\'IA pour la rédaction et l\'amélioration de documents, la synthèse, l\'analyse d\'informations, la préparation de réunions et de présentations, l\'organisation du travail et la préparation d\'éléments d\'aide à la décision.',
    'Identifier, pour chaque vague, des cas d\'usage IA réalistes et applicables au contexte du FID.',
    'Sensibiliser l\'ensemble des participants aux règles de confidentialité, de sécurité, de gouvernance, de vérification des résultats et de validation humaine.'
]
for obj in objectifs_spec:
    p = doc.add_paragraph(style='List Number')
    run = p.add_run(obj)
    run.font.size = Pt(14)
    run.font.name = 'Times New Roman'

p = doc.add_paragraph()
run = p.add_run('2.4 Public cible')
run.font.size = Pt(14)
run.font.bold = True
run.font.name = 'Times New Roman'
p.paragraph_format.space_before = Pt(8)

p = doc.add_paragraph()
run = p.add_run(
    'La formation sera organisée en deux vagues distinctes : '
)
run.font.size = Pt(14)
run.font.name = 'Times New Roman'

p = doc.add_paragraph(style='List Bullet')
run = p.add_run('Vague 1 – Environ 24 participants : équipes Système d\'Information (développeurs, administrateurs système/réseau/sécurité, exploitation, support, parc, documentation, maintenance) et équipes Planification, Suivi et Évaluation (planification stratégique et opérationnelle, suivi des indicateurs, analyse des données, évaluation, reporting, tableaux de bord, aide à la décision).')
run.font.size = Pt(14)
run.font.name = 'Times New Roman'

p = doc.add_paragraph(style='List Bullet')
run = p.add_run('Vague 2 – Environ 30 participants : Directeurs, Chefs de service et certains Responsables ou cadres identifiés par le FID. Aucune connaissance préalable en IA ne sera exigée.')
run.font.size = Pt(14)
run.font.name = 'Times New Roman'

p = doc.add_paragraph()
run = p.add_run('2.5 Durée et format')
run.font.size = Pt(14)
run.font.bold = True
run.font.name = 'Times New Roman'
p.paragraph_format.space_before = Pt(8)

p = doc.add_paragraph()
run = p.add_run(
    'Sept (07) jours ouvrables au total, répartis en deux vagues : quatre (04) jours pour la vague SI/PSE '
    'et trois (03) jours pour la vague Directeurs/Chefs de service/Responsables. Format présentiel, pratique, '
    'participatif et adapté au niveau de chaque vague.'
)
run.font.size = Pt(14)
run.font.name = 'Times New Roman'

# ============================================================
# SECTION 3 : OBJECTIFS VISÉS
# ============================================================
p = doc.add_paragraph()
p.paragraph_format.space_before = Pt(16)
run = p.add_run('3.  OBJECTIFS VISÉS')
run.font.size = Pt(18)
run.font.bold = True
run.font.name = 'Times New Roman'
run.font.color.rgb = RGBColor(0, 51, 102)

p = doc.add_paragraph()
run = p.add_run(
    'Notre intervention vise les résultats attendus suivants, tels que définis dans les TDR :'
)
run.font.size = Pt(14)
run.font.name = 'Times New Roman'

resultats = [
    'Les participants des deux vagues disposent d\'une compréhension commune des usages, risques, limites et bonnes pratiques de l\'IA.',
    'Les équipes SI/développeurs disposent de pistes concrètes pour automatiser certaines tâches techniques, documentaires, d\'intégration, de support et d\'exploitation du système d\'information.',
    'Les équipes PSE disposent de pistes concrètes pour automatiser certaines tâches de planification, suivi des indicateurs, analyse des écarts, reporting, tableaux de bord et notes d\'aide à la décision.',
    'Les Directeurs, Chefs de service et Responsables savent utiliser un outil d\'IA générative pour des tâches professionnelles courantes, notamment la rédaction, la synthèse, l\'analyse de documents, la préparation de réunions et de présentations et l\'aide à la décision.',
    'Les Directeurs, Chefs de service et Responsables savent formuler et améliorer des prompts simples et efficaces, appliquer l\'IA à des cas professionnels concrets et identifier les usages pertinents pour leurs fonctions.',
    'Des cas d\'usage IA prioritaires, communs et spécialisés, sont identifiés, testés et priorisés.',
    'L\'ensemble des participants connaît les principes de vérification des résultats ainsi que les règles minimales de confidentialité, de sécurité et de validation humaine.',
    'Des supports pratiques et modèles de prompts adaptés aux différentes catégories de participants sont remis au FID.'
]
for r in resultats:
    p = doc.add_paragraph(style='List Bullet')
    run = p.add_run(r)
    run.font.size = Pt(14)
    run.font.name = 'Times New Roman'

# ============================================================
# SECTION 4 : MÉTHODOLOGIE PÉDAGOGIQUE
# ============================================================
p = doc.add_paragraph()
p.paragraph_format.space_before = Pt(16)
run = p.add_run('4.  MÉTHODOLOGIE PÉDAGOGIQUE')
run.font.size = Pt(18)
run.font.bold = True
run.font.name = 'Times New Roman'
run.font.color.rgb = RGBColor(0, 51, 102)

p = doc.add_paragraph()
run = p.add_run(
    'Notre approche pédagogique repose sur quatre principes fondamentaux, en parfaite adéquation avec '
    'les exigences des TDR :'
)
run.font.size = Pt(14)
run.font.name = 'Times New Roman'

principes = [
    ('Pratique et participative', 'Nous privilégions les démonstrations, les exercices guidés, les ateliers de groupe et les productions directement exploitables. La formation n\'est pas uniquement théorique.'),
    ('Adaptée au niveau de chaque vague', 'Les contenus, exercices et études de cas sont adaptés aux responsabilités et au niveau de connaissance de chaque public cible.'),
    ('Données validées', 'Nous utilisons des données fictives, anonymisées ou préalablement validées par le FID pour garantir la confidentialité et la pertinence des exercices.'),
    ('Évaluation des acquis', 'Une évaluation est prévue pour chacune des deux vagues afin de mesurer l\'appropriation des compétences.')
]
for titre, desc in principes:
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(6)
    run = p.add_run(titre + ' : ')
    run.font.bold = True
    run.font.size = Pt(14)
    run.font.name = 'Times New Roman'
    run = p.add_run(desc)
    run.font.size = Pt(14)
    run.font.name = 'Times New Roman'

p = doc.add_paragraph()
run = p.add_run('4.1 Structure pédagogique')
run.font.size = Pt(14)
run.font.bold = True
run.font.name = 'Times New Roman'
p.paragraph_format.space_before = Pt(8)

p = doc.add_paragraph()
run = p.add_run(
    'La formation est structurée en deux volets complémentaires pour chaque vague : un tronc commun '
    'assurant un socle de connaissances partagées, suivi d\'ateliers spécialisés adaptés aux responsabilités '
    'spécifiques de chaque public.'
)
run.font.size = Pt(14)
run.font.name = 'Times New Roman'

p = doc.add_paragraph()
run = p.add_run('4.2 Démarche d\'intervention')
run.font.size = Pt(14)
run.font.bold = True
run.font.name = 'Times New Roman'
p.paragraph_format.space_before = Pt(8)

etapes = [
    ('Phase 1 – Préparation et cadrage', 'Collecte des informations auprès du FID, analyse des besoins spécifiques, validation du programme détaillé, préparation des données d\'exercice et des supports pédagogiques.'),
    ('Phase 2 – Animation des formations', 'Déploiement des activités selon le programme détaillé, mobilisation des ressources pédagogiques, coordination des intervenants, suivi de l\'exécution.'),
    ('Phase 3 – Contrôle et validation', 'Vérification de la qualité des apprentissages, évaluation des acquis, identification des difficultés, ajustements si nécessaire.'),
    ('Phase 4 – Finalisation et restitution', 'Production et remise des livrables, présentation des résultats au FID, remise du rapport final, recommandations et perspectives.')
]
for titre, desc in etapes:
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(4)
    run = p.add_run(titre + ' : ')
    run.font.bold = True
    run.font.size = Pt(14)
    run.font.name = 'Times New Roman'
    run = p.add_run(desc)
    run.font.size = Pt(14)
    run.font.name = 'Times New Roman'

# ============================================================
# SECTION 5 : PROGRAMME VAGUE 1
# ============================================================
p = doc.add_paragraph()
p.paragraph_format.space_before = Pt(16)
run = p.add_run('5.  PROGRAMME DÉTAILLÉ – VAGUE 1 (ÉQUIPES SI / PSE)')
run.font.size = Pt(18)
run.font.bold = True
run.font.name = 'Times New Roman'
run.font.color.rgb = RGBColor(0, 51, 102)

p = doc.add_paragraph()
run = p.add_run('Durée : 4 jours | Public : ~24 participants (SI + PSE)')
run.font.size = Pt(12)
run.font.italic = True
run.font.name = 'Times New Roman'

table = doc.add_table(rows=5, cols=4)
table.style = 'Table Grid'
table.alignment = WD_TABLE_ALIGNMENT.CENTER

headers = ['Jour', 'Thème', 'Contenus principaux', 'Résultat attendu']
for i, h in enumerate(headers):
    cell = table.rows[0].cells[i]
    cell.text = ''
    p = cell.paragraphs[0]
    run = p.add_run(h)
    run.font.bold = True
    run.font.size = Pt(11)
    run.font.name = 'Times New Roman'
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    shading = parse_xml(f'<w:shd {nsdecls("w")} w:fill="003366"/>')
    cell._tc.get_or_add_tcPr().append(shading)
    run.font.color.rgb = RGBColor(255, 255, 255)

data_vague1 = [
    ['Jour 1\n(Tronc commun SI/PSE)', 'Fondamentaux, usages, risques et bonnes pratiques de l\'IA',
     '• Introduction à l\'IA : concepts clés, types d\'IA, IA générative\n• Usages professionnels de l\'IA\n• Risques : confidentialité, biais, hallucinations, sécurité\n• Bonnes pratiques et gouvernance\n• Formulation de prompts : principes de base',
     'Bases communes et règles d\'utilisation'],
    ['Jour 2\n(Tronc commun SI/PSE)', 'IA appliquée à l\'analyse de données',
     '• Structuration et analyse de données avec l\'IA\n• Synthèse automatique de documents\n• Interprétation et visualisation des données\n• Outils d\'IA pour le reporting\n• Exercices pratiques sur données fictives',
     'Analyse, structuration et synthèse des données'],
    ['Jour 3\n(Tronc commun SI/PSE)', 'Agents IA et automatisation intelligente',
     '• Principes des agents IA autonomes\n• Automatisation intelligente de processus\n• Intégration de l\'IA dans les workflows\n• Cas d\'usage transversaux\n• Exercices pratiques',
     'Principes communs des agents IA et de l\'automatisation'],
    ['Jour 4\n(Ateliers spécialisés)', 'Ateliers spécialisés : automatisation SI / PSE',
     'SI : Automatisation de tâches techniques, flux de données, scripts, agents IA intégrés au SI, sécurité, documentation technique.\n\nPSE : Automatisation de tâches de planification, suivi des indicateurs, analyse des écarts, production de rapports, tableaux de bord, notes d\'aide à la décision.',
     'Parcours SI/PSE définis, cas testés et priorisés']
]

for row_idx, row_data in enumerate(data_vague1):
    for col_idx, cell_text in enumerate(row_data):
        cell = table.rows[row_idx + 1].cells[col_idx]
        cell.text = ''
        p = cell.paragraphs[0]
        run = p.add_run(cell_text)
        run.font.size = Pt(10)
        run.font.name = 'Times New Roman'
        if col_idx == 0:
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            run.font.bold = True

for row in table.rows:
    row.cells[0].width = Cm(3.5)
    row.cells[1].width = Cm(4.5)
    row.cells[2].width = Cm(8.5)
    row.cells[3].width = Cm(4.0)

# ============================================================
# SECTION 6 : PROGRAMME VAGUE 2
# ============================================================
p = doc.add_paragraph()
p.paragraph_format.space_before = Pt(16)
run = p.add_run('6.  PROGRAMME DÉTAILLÉ – VAGUE 2 (DIRECTEURS / CHEFS DE SERVICE / RESPONSABLES)')
run.font.size = Pt(18)
run.font.bold = True
run.font.name = 'Times New Roman'
run.font.color.rgb = RGBColor(0, 51, 102)

p = doc.add_paragraph()
run = p.add_run('Durée : 3 jours | Public : ~30 participants | Aucun prérequis en IA')
run.font.size = Pt(12)
run.font.italic = True
run.font.name = 'Times New Roman'

table2 = doc.add_table(rows=4, cols=4)
table2.style = 'Table Grid'
table2.alignment = WD_TABLE_ALIGNMENT.CENTER

for i, h in enumerate(headers):
    cell = table2.rows[0].cells[i]
    cell.text = ''
    p = cell.paragraphs[0]
    run = p.add_run(h)
    run.font.bold = True
    run.font.size = Pt(11)
    run.font.name = 'Times New Roman'
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    shading = parse_xml(f'<w:shd {nsdecls("w")} w:fill="003366"/>')
    cell._tc.get_or_add_tcPr().append(shading)
    run.font.color.rgb = RGBColor(255, 255, 255)

data_vague2 = [
    ['Jour 1', 'Initiation à l\'IA, prise en main des outils, formulation des prompts et principaux usages professionnels',
     '• Compréhension de l\'IA générative\n• Prise en main des outils d\'IA\n• Formulation de prompts efficaces\n• Principaux usages professionnels\n• Exercices pratiques guidés',
     'Les participants comprennent les principes de base de l\'IA et savent formuler des demandes claires et efficaces'],
    ['Jour 2', 'Rédaction, synthèse, analyse de documents, préparation de réunions et présentations',
     '• Rédaction et amélioration de documents avec l\'IA\n• Synthèse et analyse de documents\n• Préparation de réunions et de présentations\n• Organisation de l\'information\n• Études de cas professionnelles',
     'Les participants savent utiliser l\'IA pour améliorer la qualité et l\'efficacité de leurs tâches professionnelles et managériales'],
    ['Jour 3', 'Aide à la décision, ateliers pratiques, identification des cas d\'usage, confidentialité et sécurité',
     '• IA pour l\'aide à la décision\n• Ateliers pratiques sur des cas professionnels concrets\n• Identification des cas d\'usage prioritaires\n• Règles de confidentialité, sécurité et vérification des résultats\n• Validation humaine et responsabilité',
     'Les participants appliquent l\'IA à des situations concrètes, identifient des usages prioritaires et maîtrisent les règles d\'utilisation responsable']
]

for row_idx, row_data in enumerate(data_vague2):
    for col_idx, cell_text in enumerate(row_data):
        cell = table2.rows[row_idx + 1].cells[col_idx]
        cell.text = ''
        p = cell.paragraphs[0]
        run = p.add_run(cell_text)
        run.font.size = Pt(10)
        run.font.name = 'Times New Roman'
        if col_idx == 0:
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            run.font.bold = True

for row in table2.rows:
    row.cells[0].width = Cm(3.5)
    row.cells[1].width = Cm(4.5)
    row.cells[2].width = Cm(8.5)
    row.cells[3].width = Cm(4.0)

# ============================================================
# SECTION 7 : ORGANISATION ET ÉQUIPE
# ============================================================
p = doc.add_paragraph()
p.paragraph_format.space_before = Pt(16)
run = p.add_run('7.  ORGANISATION ET ÉQUIPE DE FORMATION')
run.font.size = Pt(18)
run.font.bold = True
run.font.name = 'Times New Roman'
run.font.color.rgb = RGBColor(0, 51, 102)

p = doc.add_paragraph()
run = p.add_run('7.1 Structure de l\'équipe')
run.font.size = Pt(14)
run.font.bold = True
run.font.name = 'Times New Roman'
p.paragraph_format.space_before = Pt(8)

p = doc.add_paragraph()
run = p.add_run(
    'CARSO proposera une équipe de formation composée d\'un chef de projet/formateur principal et, '
    'si nécessaire, d\'un expert complémentaire spécialisé en analyse de données ou en automatisation, '
    'en fonction des besoins identifiés lors de la phase de cadrage.'
)
run.font.size = Pt(14)
run.font.name = 'Times New Roman'

p = doc.add_paragraph()
run = p.add_run('7.2 Profil du/des formateur(s)')
run.font.size = Pt(14)
run.font.bold = True
run.font.name = 'Times New Roman'
p.paragraph_format.space_before = Pt(8)

p = doc.add_paragraph()
run = p.add_run('Le(s) formateur(s) proposé(s) répondra aux critères minimums suivants, tels que définis dans les TDR :')
run.font.size = Pt(14)
run.font.name = 'Times New Roman'

profils = [
    'Au moins équivalent à Master 2 (BACC +5) en informatique ou filière similaire.',
    'Titulaire d\'un certificat ou attestation de formation dans l\'un des domaines suivants : intelligence artificielle, transformation digitale, informatique, systèmes d\'information, analyse de données, développement informatique ou domaine équivalent.',
    'Au moins cinq (05) ans d\'expérience professionnelle dans le domaine de la digitalisation, de la transformation digitale, des systèmes d\'information, de l\'informatique ou de l\'accompagnement numérique des organisations.',
    'Au moins quinze (15) formations animées portant sur des outils informatiques, outils numériques, logiciels professionnels, analyse de données, bureautique avancée, systèmes d\'information ou thématiques similaires.',
    'Parmi ces quinze (15) formations, au moins deux (02) formations portant spécifiquement sur l\'intelligence artificielle générative, les agents d\'IA, l\'automatisation ou les usages professionnels de l\'IA.',
    'Bonne capacité pédagogique et expérience dans l\'animation de formations destinées à des publics mixtes, comprenant à la fois des profils techniques et des profils métiers.',
    'Maîtrise de la langue française et malgache (langue de travail obligatoire).'
]
for prof in profils:
    p = doc.add_paragraph(style='List Bullet')
    run = p.add_run(prof)
    run.font.size = Pt(14)
    run.font.name = 'Times New Roman'

p = doc.add_paragraph()
run = p.add_run('7.3 Modalités d\'encadrement')
run.font.size = Pt(14)
run.font.bold = True
run.font.name = 'Times New Roman'
p.paragraph_format.space_before = Pt(8)

p = doc.add_paragraph()
run = p.add_run(
    'L\'encadrement des participants sera assuré par le formateur principal, avec un ratio optimal de '
    '1 formateur pour 15 participants maximum. Des assistants pédagogiques pourront être mobilisés '
    'pour les ateliers pratiques afin de garantir un accompagnement individualisé. La gestion des '
    'exercices pratiques, l\'évaluation des acquis et la remise des livrables seront assurées par l\'équipe '
    'de formation dans son intégralité.'
)
run.font.size = Pt(14)
run.font.name = 'Times New Roman'

# ============================================================
# SECTION 8 : LIVRABLES
# ============================================================
p = doc.add_paragraph()
p.paragraph_format.space_before = Pt(16)
run = p.add_run('8.  LIVRABLES ATTENDUS')
run.font.size = Pt(18)
run.font.bold = True
run.font.name = 'Times New Roman'
run.font.color.rgb = RGBColor(0, 51, 102)

p = doc.add_paragraph()
run = p.add_run(
    'Conformément aux TDR, CARSO s\'engage à produire et remettre les livrables suivants :'
)
run.font.size = Pt(14)
run.font.name = 'Times New Roman'

table3 = doc.add_table(rows=7, cols=3)
table3.style = 'Table Grid'
table3.alignment = WD_TABLE_ALIGNMENT.CENTER

headers3 = ['N°', 'Livrable', 'Description']
for i, h in enumerate(headers3):
    cell = table3.rows[0].cells[i]
    cell.text = ''
    p = cell.paragraphs[0]
    run = p.add_run(h)
    run.font.bold = True
    run.font.size = Pt(11)
    run.font.name = 'Times New Roman'
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    shading = parse_xml(f'<w:shd {nsdecls("w")} w:fill="003366"/>')
    cell._tc.get_or_add_tcPr().append(shading)
    run.font.color.rgb = RGBColor(255, 255, 255)

livrables = [
    ['1', 'Note méthodologique', 'Approche pédagogique, programme détaillé pour chacune des deux vagues, outils proposés et organisation des ateliers.'],
    ['2', 'Supports de formation', 'Version électronique, adaptés à chaque vague (Vague 1 SI/PSE et Vague 2 Directeurs/Chefs de service).'],
    ['3', 'Exercices pratiques', 'Modèles, fiches d\'automatisation, modèles de prompts et études de cas utilisés pendant la formation.'],
    ['4', 'Note de bonnes pratiques', 'Document sur l\'utilisation responsable, sécurisée et éthique de l\'IA.'],
    ['5', 'Résultats d\'évaluation', 'Résultats de l\'évaluation des acquis pour chacune des deux vagues.'],
    ['6', 'Rapport final de formation', 'Déroulement des deux vagues, résultats, difficultés rencontrées, recommandations et perspectives.']
]

for row_idx, row_data in enumerate(livrables):
    for col_idx, cell_text in enumerate(row_data):
        cell = table3.rows[row_idx + 1].cells[col_idx]
        cell.text = ''
        p = cell.paragraphs[0]
        run = p.add_run(cell_text)
        run.font.size = Pt(11)
        run.font.name = 'Times New Roman'
        if col_idx == 0:
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            run.font.bold = True

for row in table3.rows:
    row.cells[0].width = Cm(1.5)
    row.cells[1].width = Cm(5.0)
    row.cells[2].width = Cm(13.0)

# ============================================================
# SECTION 9 : PLANNING ET JALONS
# ============================================================
p = doc.add_paragraph()
p.paragraph_format.space_before = Pt(16)
run = p.add_run('9.  PLANNING ET JALONS')
run.font.size = Pt(18)
run.font.bold = True
run.font.name = 'Times New Roman'
run.font.color.rgb = RGBColor(0, 51, 102)

p = doc.add_paragraph()
run = p.add_run(
    'Le planning ci-dessous est indicatif. Les dates exactes seront validées en concertation avec le FID '
    'après attribution du contrat.'
)
run.font.size = Pt(14)
run.font.name = 'Times New Roman'

table4 = doc.add_table(rows=6, cols=3)
table4.style = 'Table Grid'
table4.alignment = WD_TABLE_ALIGNMENT.CENTER

headers4 = ['Phase', 'Activités', 'Jalon']
for i, h in enumerate(headers4):
    cell = table4.rows[0].cells[i]
    cell.text = ''
    p = cell.paragraphs[0]
    run = p.add_run(h)
    run.font.bold = True
    run.font.size = Pt(11)
    run.font.name = 'Times New Roman'
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    shading = parse_xml(f'<w:shd {nsdecls("w")} w:fill="003366"/>')
    cell._tc.get_or_add_tcPr().append(shading)
    run.font.color.rgb = RGBColor(255, 255, 255)

planning_data = [
    ['Phase 1\nPréparation', 'Réunion de lancement, collecte des informations, analyse des besoins, validation du plan de travail, préparation des supports', 'Plan de travail validé'],
    ['Phase 2\nVague 1 (SI/PSE)', 'Animation de la formation 4 jours – tronc commun + ateliers spécialisés', 'Formation Vague 1 réalisée'],
    ['Phase 3\nVague 2 (Directeurs)', 'Animation de la formation 3 jours – initiation + usages managériaux + aide à la décision', 'Formation Vague 2 réalisée'],
    ['Phase 4\nRestitution', 'Remise des livrables, évaluation des acquis, rapport final', 'Rapport final remis'],
    ['Phase 5\nClôture', 'Validation du rapport par le FID, clôture de la prestation', 'Prestation clôturée']
]

for row_idx, row_data in enumerate(planning_data):
    for col_idx, cell_text in enumerate(row_data):
        cell = table4.rows[row_idx + 1].cells[col_idx]
        cell.text = ''
        p = cell.paragraphs[0]
        run = p.add_run(cell_text)
        run.font.size = Pt(10)
        run.font.name = 'Times New Roman'
        if col_idx == 0:
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            run.font.bold = True

for row in table4.rows:
    row.cells[0].width = Cm(4.0)
    row.cells[1].width = Cm(10.0)
    row.cells[2].width = Cm(5.5)

# ============================================================
# SECTION 10 : MOYENS TECHNIQUES ET LOGISTIQUE
# ============================================================
p = doc.add_paragraph()
p.paragraph_format.space_before = Pt(16)
run = p.add_run('10.  MOYENS TECHNIQUES ET LOGISTIQUE')
run.font.size = Pt(18)
run.font.bold = True
run.font.name = 'Times New Roman'
run.font.color.rgb = RGBColor(0, 51, 102)

p = doc.add_paragraph()
run = p.add_run('10.1 Matériel nécessaire au bon déroulement')
run.font.size = Pt(14)
run.font.bold = True
run.font.name = 'Times New Roman'
p.paragraph_format.space_before = Pt(8)

p = doc.add_paragraph()
run = p.add_run(
    'Le prestataire devra fournir le matériel nécessaire au bon déroulement des deux vagues de formation. '
    'Les besoins identifiés sont les suivants :'
)
run.font.size = Pt(14)
run.font.name = 'Times New Roman'

matériel = [
    'Salle de formation adaptée (capacité 30 personnes minimum, climatisée, bien éclairée).',
    'Connexion Internet haut débit stable et sécurisée.',
    'Vidéoprojecteur et écran de projection.',
    'Ordinateurs portables pour les participants (ou mise à disposition par le FID).',
    'Supports pédagogiques imprimés ou numériques.',
    'Outils d\'IA à utiliser (accès aux plateformes d\'intelligence artificielle – les accès seront précisés en phase de cadrage).',
    'Tableau blanc ou paperboard.',
    'Matériel de sonorisation si nécessaire.'
]
for m in matériel:
    p = doc.add_paragraph(style='List Bullet')
    run = p.add_run(m)
    run.font.size = Pt(14)
    run.font.name = 'Times New Roman'

p = doc.add_paragraph()
run = p.add_run('10.2 Conditions techniques minimales requises')
run.font.size = Pt(14)
run.font.bold = True
run.font.name = 'Times New Roman'
p.paragraph_format.space_before = Pt(8)

p = doc.add_paragraph()
run = p.add_run(
    'Nous recommandons les conditions techniques minimales suivantes, à confirmer en phase de cadrage :'
)
run.font.size = Pt(14)
run.font.name = 'Times New Roman'

conditions = [
    'Débit Internet minimum : 10 Mbps par participant pour les exercices en ligne.',
    'Chaque participant dispose d\'un ordinateur portable fonctionnel avec accès Internet.',
    'Prise électrique accessible à chaque place.',
    'Environnement de travail calme et propice à la concentration.'
]
for c in conditions:
    p = doc.add_paragraph(style='List Bullet')
    run = p.add_run(c)
    run.font.size = Pt(14)
    run.font.name = 'Times New Roman'

p = doc.add_paragraph()
run = p.add_run('10.3 Logistique des formateurs')
run.font.size = Pt(14)
run.font.bold = True
run.font.name = 'Times New Roman'
p.paragraph_format.space_before = Pt(8)

p = doc.add_paragraph()
run = p.add_run(
    'Le déplacement des formateurs depuis Antananarivo jusqu\'au lieu de la formation sera pris en charge '
    'par le FID. Les indemnités d\'hébergement, de restauration et de séjour sur le lieu de formation sont '
    'intégrées dans notre offre financière. Nous nous engageons à fournir dans notre offre financière un '
    'détail complet de ces coûts.'
)
run.font.size = Pt(14)
run.font.name = 'Times New Roman'

# ============================================================
# SECTION 11 : QUALIFICATIONS ET RÉFÉRENCES
# ============================================================
p = doc.add_paragraph()
p.paragraph_format.space_before = Pt(16)
run = p.add_run('11.  QUALIFICATIONS DU CABINET ET RÉFÉRENCES')
run.font.size = Pt(18)
run.font.bold = True
run.font.name = 'Times New Roman'
run.font.color.rgb = RGBColor(0, 51, 102)

p = doc.add_paragraph()
run = p.add_run('11.1 Existence légale')
run.font.size = Pt(14)
run.font.bold = True
run.font.name = 'Times New Roman'
p.paragraph_format.space_before = Pt(8)

p = doc.add_paragraph()
run = p.add_run(
    'CARSO – Centre d\'Affaires de la Région Sud-Ouest est un Groupement d\'Intérêt Économique (GIE) '
    'en règle avec ses obligations légales. [À COMPLÉTER : date de création pour justifier l\'existence '
    'légale d\'au moins 3 ans].'
)
run.font.size = Pt(14)
run.font.name = 'Times New Roman'

p = doc.add_paragraph()
run = p.add_run('11.2 Expériences en formation')
run.font.size = Pt(14)
run.font.bold = True
run.font.name = 'Times New Roman'
p.paragraph_format.space_before = Pt(8)

p = doc.add_paragraph()
run = p.add_run(
    'CARSO justifie des critères minimums suivants, tels que définis dans les TDR :'
)
run.font.size = Pt(14)
run.font.name = 'Times New Roman'

references = [
    'Au moins six (6) formations professionnelles animées au cours des trois (3) dernières années. [À COMPLÉTER : liste des formations avec dates et organismes]',
    'Au moins vingt (20) personnes formées et certifiées ou attestées au cours des trois (3) dernières années. [À COMPLÉTER : effectifs]',
    'Au moins deux (02) formations en intelligence artificielle, IA générative, automatisation, transformation digitale ou thématiques similaires. [À COMPLÉTER : références]',
    'Au moins deux (02) missions de formation ou d\'accompagnement auprès d\'institutions publiques, parapubliques, projets de développement, ONG, entreprises ou organisations similaires. [À COMPLÉTER : références]'
]
for ref in references:
    p = doc.add_paragraph(style='List Bullet')
    run = p.add_run(ref)
    run.font.size = Pt(14)
    run.font.name = 'Times New Roman'

p = doc.add_paragraph()
run = p.add_run('11.3 Références professionnelles')
run.font.size = Pt(14)
run.font.bold = True
run.font.name = 'Times New Roman'
p.paragraph_format.space_before = Pt(8)

p = doc.add_paragraph()
run = p.add_run(
    '[À COMPLÉTER : liste des références professionnelles pertinentes avec noms des organismes, '
    'dates, descriptions des missions et coordonnées des personnes ressources].'
)
run.font.size = Pt(14)
run.font.name = 'Times New Roman'
run.font.italic = True

# ============================================================
# SECTION 12 : RISQUES
# ============================================================
p = doc.add_paragraph()
p.paragraph_format.space_before = Pt(16)
run = p.add_run('12.  ANALYSE DES RISQUES ET MESURES D\'ATTÉNUATION')
run.font.size = Pt(18)
run.font.bold = True
run.font.name = 'Times New Roman'
run.font.color.rgb = RGBColor(0, 51, 102)

table5 = doc.add_table(rows=6, cols=4)
table5.style = 'Table Grid'
table5.alignment = WD_TABLE_ALIGNMENT.CENTER

headers5 = ['Risque identifié', 'Impact potentiel', 'Mesure d\'atténuation', 'Probabilité']
for i, h in enumerate(headers5):
    cell = table5.rows[0].cells[i]
    cell.text = ''
    p = cell.paragraphs[0]
    run = p.add_run(h)
    run.font.bold = True
    run.font.size = Pt(10)
    run.font.name = 'Times New Roman'
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    shading = parse_xml(f'<w:shd {nsdecls("w")} w:fill="003366"/>')
    cell._tc.get_or_add_tcPr().append(shading)
    run.font.color.rgb = RGBColor(255, 255, 255)

risques = [
    ['Retard dans la confirmation du lieu de formation', 'Difficulté de planification logistique', 'Validation anticipée du lieu en phase de cadrage', 'Faible'],
    ['Indisponibilité des formateurs', 'Report de la formation', 'Réservation confirmée avec substituts identifiés', 'Faible'],
    ['Problèmes de connexion Internet sur site', 'Perturbation des exercices pratiques', 'Préparation de contenus hors ligne, backup 4G', 'Moyenne'],
    ['Niveau hétérogène des participants', 'Difficulté d\'adaptation du contenu', 'Diagnostic préalable, exercices différenciés', 'Faible'],
    ['Données sensibles du FID', 'Risques de confidentialité', 'Utilisation exclusive de données fictives ou anonymisées validées par le FID', 'Faible']
]

for row_idx, row_data in enumerate(risques):
    for col_idx, cell_text in enumerate(row_data):
        cell = table5.rows[row_idx + 1].cells[col_idx]
        cell.text = ''
        p = cell.paragraphs[0]
        run = p.add_run(cell_text)
        run.font.size = Pt(10)
        run.font.name = 'Times New Roman'
        if col_idx == 3:
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER

for row in table5.rows:
    row.cells[0].width = Cm(4.5)
    row.cells[1].width = Cm(4.5)
    row.cells[2].width = Cm(7.0)
    row.cells[3].width = Cm(3.5)

# ============================================================
# SECTION 13 : CONCLUSION
# ============================================================
p = doc.add_paragraph()
p.paragraph_format.space_before = Pt(16)
run = p.add_run('13.  CONCLUSION ET ENGAGEMENT')
run.font.size = Pt(18)
run.font.bold = True
run.font.name = 'Times New Roman'
run.font.color.rgb = RGBColor(0, 51, 102)

p = doc.add_paragraph()
run = p.add_run(
    'CARSO – Centre d\'Affaires de la Région Sud-Ouest vous remercie de la confiance que le FID '
    'pourrait lui accorder en nous confiant la mission de formation sur l\'intelligence artificielle '
    'appliquée aux usages professionnels.'
)
run.font.size = Pt(14)
run.font.name = 'Times New Roman'

p = doc.add_paragraph()
run = p.add_run(
    'Notre proposition démontre une compréhension approfondie des enjeux du FID, une méthodologie '
    'pédagogique adaptée aux deux vagues cibles, et une capacité avérée à produire des résultats '
    'concrets et exploitables au sein de votre institution.'
)
run.font.size = Pt(14)
run.font.name = 'Times New Roman'

p = doc.add_paragraph()
run = p.add_run(
    'Nous nous engageons à :'
)
run.font.size = Pt(14)
run.font.name = 'Times New Roman'

engagements = [
    'Respecter les délais et le planning convenu.',
    'Fournir un contenu de qualité, adapté aux profils et aux responsabilités de chaque public.',
    'Garantir la confidentialité et la sécurité des données du FID.',
    'Remettre l\'ensemble des livrables dans les délais convenus.',
    'Assurer un suivi post-formation si nécessaire.'
]
for eng in engagements:
    p = doc.add_paragraph(style='List Bullet')
    run = p.add_run(eng)
    run.font.size = Pt(14)
    run.font.name = 'Times New Roman'

p = doc.add_paragraph()
run = p.add_run(
    'Nous restons à votre entière disposition pour toute information complémentaire et vous prions '
    'd\'agréer, Monsieur le Directeur Général du FID, l\'expression de nos salutations distinguées.'
)
run.font.size = Pt(14)
run.font.name = 'Times New Roman'

p = doc.add_paragraph()
p.paragraph_format.space_before = Pt(24)
run = p.add_run('CARSO – Centre d\'Affaires de la Région Sud-Ouest')
run.font.size = Pt(14)
run.font.bold = True
run.font.name = 'Times New Roman'
run = p.add_run('\nCCI Tsimenatsy, Toliara 1, Madagascar')
run.font.size = Pt(12)
run.font.name = 'Times New Roman'
run = p.add_run('\ncarso@carso.mg | +261 34 61 496 36')
run.font.size = Pt(12)
run.font.name = 'Times New Roman'

# ============================================================
# SAUVEGARDE
# ============================================================
output_path = 'offre_technique_FID_59.docx'
doc.save(output_path)
print(f"Document généré avec succès : {output_path}")
