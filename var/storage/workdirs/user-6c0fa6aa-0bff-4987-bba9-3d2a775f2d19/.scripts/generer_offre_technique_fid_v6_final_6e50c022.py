from docx import Document
from docx.shared import Pt, Cm, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.oxml.ns import nsdecls
from docx.oxml import parse_xml

REFERENCE_APPEL = "N°59/AMI/FID/FSR/SQC_CAB_FORM_IA"
NOM_CABINET = "CARSO — Centre d'Affaires de la Région Sud-Ouest"
LIEU_DATE = "Toliara, le 6 novembre 2026"

COULEUR_PRIMAIRE = RGBColor(0x00, 0x33, 0x66)
COULEUR_SECONDAIRE = RGBColor(0x00, 0x66, 0x99)
COULEUR_GRIS = RGBColor(0x66, 0x66, 0x66)
COULEUR_BLANC = RGBColor(0xFF, 0xFF, 0xFF)
COULEUR_NOIR = RGBColor(0x00, 0x00, 0x00)

doc = Document()

section = doc.sections[0]
section.page_width = Cm(21)
section.page_height = Cm(29.7)
section.top_margin = Cm(2.54)
section.bottom_margin = Cm(2.54)
section.left_margin = Cm(3.17)
section.right_margin = Cm(3.17)

style_normal = doc.styles['Normal']
style_normal.font.name = 'Times New Roman'
style_normal.font.size = Pt(12)
style_normal.font.color.rgb = COULEUR_NOIR
style_normal.paragraph_format.space_after = Pt(6)
style_normal.paragraph_format.line_spacing = 1.15

style_h1 = doc.styles['Heading 1']
style_h1.font.name = 'Times New Roman'
style_h1.font.size = Pt(16)
style_h1.font.bold = True
style_h1.font.color.rgb = COULEUR_PRIMAIRE
style_h1.paragraph_format.space_before = Pt(18)
style_h1.paragraph_format.space_after = Pt(12)

style_h2 = doc.styles['Heading 2']
style_h2.font.name = 'Times New Roman'
style_h2.font.size = Pt(14)
style_h2.font.bold = True
style_h2.font.color.rgb = COULEUR_SECONDAIRE
style_h2.paragraph_format.space_before = Pt(14)
style_h2.paragraph_format.space_after = Pt(8)

style_h3 = doc.styles['Heading 3']
style_h3.font.name = 'Times New Roman'
style_h3.font.size = Pt(12)
style_h3.font.bold = True
style_h3.font.italic = True
style_h3.font.color.rgb = COULEUR_PRIMAIRE
style_h3.paragraph_format.space_before = Pt(10)
style_h3.paragraph_format.space_after = Pt(6)

for _ in range(4):
    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(12)

p = doc.add_paragraph()
p.alignment = WD_ALIGN_PARAGRAPH.CENTER
run = p.add_run("OFFRE TECHNIQUE")
run.font.name = 'Times New Roman'
run.font.size = Pt(28)
run.font.bold = True
run.font.color.rgb = COULEUR_PRIMAIRE

p = doc.add_paragraph()
p.alignment = WD_ALIGN_PARAGRAPH.CENTER
run = p.add_run("LOT 01 : FORMATION SUR L'IA APPLIQUEE")
run.font.name = 'Times New Roman'
run.font.size = Pt(18)
run.font.color.rgb = COULEUR_SECONDAIRE

p = doc.add_paragraph()
p.alignment = WD_ALIGN_PARAGRAPH.CENTER
p.paragraph_format.space_before = Pt(12)
p.paragraph_format.space_after = Pt(12)
pPr = p._p.get_or_add_pPr()
pBdr = parse_xml(f'<w:pBdr {nsdecls("w")}><w:bottom w:val="single" w:sz="12" w:space="1" w:color="003366"/></w:pBdr>')
pPr.append(pBdr)

p = doc.add_paragraph()
p.alignment = WD_ALIGN_PARAGRAPH.CENTER
run = p.add_run(f"Appel à Manifestation d'Intérêt N° {REFERENCE_APPEL}")
run.font.name = 'Times New Roman'
run.font.size = Pt(14)
run.font.color.rgb = COULEUR_GRIS

p = doc.add_paragraph()
p.alignment = WD_ALIGN_PARAGRAPH.CENTER
run = p.add_run("Fonds d'Intervention pour le Développement (FID)")
run.font.name = 'Times New Roman'
run.font.size = Pt(14)
run.font.bold = True
run.font.color.rgb = COULEUR_PRIMAIRE

for _ in range(3):
    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(12)

p = doc.add_paragraph()
p.alignment = WD_ALIGN_PARAGRAPH.CENTER
run = p.add_run("Soumis par :")
run.font.name = 'Times New Roman'
run.font.size = Pt(12)
run.font.color.rgb = COULEUR_GRIS

p = doc.add_paragraph()
p.alignment = WD_ALIGN_PARAGRAPH.CENTER
run = p.add_run(NOM_CABINET)
run.font.name = 'Times New Roman'
run.font.size = Pt(16)
run.font.bold = True
run.font.color.rgb = COULEUR_PRIMAIRE

p = doc.add_paragraph()
p.alignment = WD_ALIGN_PARAGRAPH.CENTER
run = p.add_run(LIEU_DATE)
run.font.name = 'Times New Roman'
run.font.size = Pt(12)
run.font.color.rgb = COULEUR_GRIS

for _ in range(4):
    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(12)

doc.add_page_break()

p = doc.add_paragraph()
p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
run = p.add_run(LIEU_DATE)
run.font.name = 'Times New Roman'
run.font.size = Pt(12)

doc.add_paragraph()

p = doc.add_paragraph()
run = p.add_run("Objet : Proposition de formation sur l'IA appliquée aux usages professionnels")
run.font.name = 'Times New Roman'
run.font.size = Pt(12)
run.font.bold = True

doc.add_paragraph()

p = doc.add_paragraph()
run = p.add_run("Madame, Monsieur,")
run.font.name = 'Times New Roman'
run.font.size = Pt(12)

doc.add_paragraph()

paragraphes_lettre = [
    f"Nous avons l'honneur de vous soumettre notre offre technique en réponse à votre Appel à Manifestation d'Intérêt N° {REFERENCE_APPEL} pour la prestation de formation sur l'Intelligence Artificielle appliquée aux usages professionnels.",
    f"CARSO — Centre d'Affaires de la Région Sud-Ouest — est un organisme spécialisé dans l'accompagnement entrepreneurial et le renforcement de compétences. Notre expertise en ingénierie de formation et notre maîtrise des outils d'IA nous permettent de proposer une formation de haute qualité, adaptée aux besoins spécifiques du Fonds d'Intervention pour le Développement.",
    "Notre offre technique détaillée ci-après présente notre compréhension de vos besoins, notre méthodologie pédagogique, le contenu détaillé de la formation, notre organisation et nos engagements.",
    "Nous restons à votre entière disposition pour toute information complémentaire et vous prions d'agréer, Madame, Monsieur, l'expression de nos salutations distinguées."
]

for paragraphe in paragraphes_lettre:
    p = doc.add_paragraph(paragraphe)
    p.style = doc.styles['Normal']
    p.paragraph_format.first_line_indent = Cm(1)

doc.add_paragraph()

p = doc.add_paragraph()
run = p.add_run("CARSO — Centre d'Affaires de la Région Sud-Ouest")
run.font.name = 'Times New Roman'
run.font.size = Pt(12)
run.font.bold = True

doc.add_page_break()

p = doc.add_heading("SOMMAIRE", level=1)

sommaire_items = [
    "1. Synthèse de la proposition",
    "2. Compréhension des termes de référence",
    "3. Approche et méthodologie",
    "4. Contenu détaillé de la formation",
    "5. Organisation et équipe de réalisation",
    "6. Planning et jalons",
    "7. Livrables",
    "8. Suivi, évaluation et assurance qualité",
    "9. Analyse des risques",
    "10. Engagement",
]

for titre in sommaire_items:
    p = doc.add_paragraph()
    run = p.add_run(titre)
    run.font.name = 'Times New Roman'
    run.font.size = Pt(12)
    run.font.color.rgb = COULEUR_PRIMAIRE
    p.paragraph_format.space_after = Pt(4)

doc.add_page_break()

p = doc.add_heading("1. Synthèse de la proposition", level=1)

paragraphes_synthese = [
    "CARSO propose au Fonds d'Intervention pour le Développement (FID) une formation de haute qualité sur l'Intelligence Artificielle appliquée aux usages professionnels, répondant précisément aux exigences de l'AMI N°59/AMI/FID/FSR/SQC_CAB_FORM_IA.",
    "Cette formation s'adresse à deux vagues de participants : les agents SI/PSE (4 jours) et les Directeurs/Cadres (3 jours), avec un contenu adapté aux profils et aux responsabilités de chacun.",
    "Notre approche pédagogique combine théorie et pratique, avec une forte dimension expérimentale sur des outils concrets d'IA générative, d'analyse de données et d'aide à la décision."
]

for paragraphe in paragraphes_synthese:
    p = doc.add_paragraph(paragraphe)
    p.style = doc.styles['Normal']
    p.paragraph_format.first_line_indent = Cm(1)

table = doc.add_table(rows=5, cols=2, style='Table Grid')
table.alignment = WD_TABLE_ALIGNMENT.CENTER

donnees_tableau = [
    ("Organisation", NOM_CABINET),
    ("Appel", REFERENCE_APPEL),
    ("Durée totale", "7 jours (Vague 1 : 4 jours + Vague 2 : 3 jours)"),
    ("Public cible", "Agents SI/PSE + Directeurs/Cadres du FID")
]

for i, (cle, valeur) in enumerate(donnees_tableau):
    cell0 = table.rows[i].cells[0]
    cell1 = table.rows[i].cells[1]
    run0 = cell0.paragraphs[0].add_run(cle)
    run0.font.name = 'Times New Roman'
    run0.font.size = Pt(11)
    run0.font.bold = True
    run0.font.color.rgb = COULEUR_BLANC
    shading_elm = parse_xml(f'<w:shd {nsdecls("w")} w:fill="003366"/>')
    cell0._tc.get_or_add_tcPr().append(shading_elm)
    run1 = cell1.paragraphs[0].add_run(valeur)
    run1.font.name = 'Times New Roman'
    run1.font.size = Pt(11)
    run1.font.color.rgb = COULEUR_NOIR

for row in table.rows:
    row.cells[0].width = Cm(6)
    row.cells[1].width = Cm(14)

doc.add_page_break()

p = doc.add_heading("2. Compréhension des termes de référence", level=1)

paragraphes_comp = [
    "Le Fonds d'Intervention pour le Développement (FID) lance un Appel à Manifestation d'Intérêt en vue du recrutement d'un cabinet ou centre de formation pour la prestation de formation sur l'Intelligence Artificielle (IA) appliquée aux usages professionnels.",
    "Contexte et enjeux :"
]

for paragraphe in paragraphes_comp:
    p = doc.add_paragraph(paragraphe)
    p.style = doc.styles['Normal']
    p.paragraph_format.first_line_indent = Cm(1)

p = doc.add_paragraph("Le FID souhaite renforcer les capacités de ses agents et cadres dans l'utilisation des outils d'IA pour améliorer l'efficacité opérationnelle, l'analyse de données et l'aide à la décision. Cette formation s'inscrit dans une démarche de modernisation des pratiques professionnelles et d'adaptation aux nouvelles technologies.")
p.style = doc.styles['Normal']
p.paragraph_format.first_line_indent = Cm(1)

p = doc.add_heading("Objectifs spécifiques", level=2)

objectifs = [
    "Sensibiliser les participants aux concepts fondamentaux de l'IA",
    "Développer les compétences en analyse de données et visualisation",
    "Maîtriser les outils d'IA générative et les agents IA",
    "Appliquer l'IA à l'aide à la décision professionnelle"
]

for obj in objectifs:
    doc.add_paragraph(obj, style='List Bullet')

p = doc.add_heading("Public cible", level=2)
doc.add_paragraph("Vague 1 : Agents SI/PSE — 4 jours")
doc.add_paragraph("Vague 2 : Directeurs/Cadres — 3 jours")

doc.add_page_break()

p = doc.add_heading("3. Approche et méthodologie", level=1)
doc.add_paragraph("Notre approche pédagogique repose sur un équilibre entre théorie et pratique, avec une forte dimension expérimentale.")

p = doc.add_heading("Phase 1 — Cadrage et préparation (J-15)", level=2)
for item in ["Analyse détaillée des profils des participants", "Adaptation des supports aux spécificités du FID", "Préparation de l'environnement de travail (outils, licences)", "Validation du plan de formation avec le FID"]:
    doc.add_paragraph(item, style='List Bullet')

p = doc.add_heading("Phase 2 — Déploiement de la formation", level=2)
for item in ["Approche par modules progressifs", "Alternance de présentations, démonstrations et exercices pratiques", "Études de cas réels ou simulés en lien avec les activités du FID", "Travaux en sous-groupes pour favoriser l'apprentissage collaboratif", "Sessions de questions-réponses et retours d'expérience"]:
    doc.add_paragraph(item, style='List Bullet')

p = doc.add_heading("Phase 3 — Suivi post-formation", level=2)
for item in ["Envoi d'un guide de référence rapide", "Support à distance pendant 30 jours", "Évaluation de satisfaction et de l'acquisition des compétences"]:
    doc.add_paragraph(item, style='List Bullet')

doc.add_page_break()

p = doc.add_heading("4. Contenu détaillé de la formation", level=1)

p = doc.add_heading("Vague 1 — Agents SI/PSE (4 jours)", level=2)

p = doc.add_heading("Module 1 : Fondamentaux de l'IA (J1)", level=3)
for item in ["Qu'est-ce que l'IA ? Histoire, concepts et terminologie", "Types d'IA : IA faible vs IA forte, apprentissage supervisé/non supervisé", "Domaines d'application de l'IA dans le secteur public et le développement", "Éthique de l'IA et limites"]:
    doc.add_paragraph(item, style='List Bullet')

p = doc.add_heading("Module 2 : Analyse de données et visualisation (J2)", level=3)
for item in ["Introduction à l'analyse de données avec outils IA", "Nettoyage et préparation des données", "Visualisation de données : principes et outils", "Étude de cas : analyse de données de projets FID"]:
    doc.add_paragraph(item, style='List Bullet')

p = doc.add_heading("Module 3 : IA générative et agents IA (J3)", level=3)
for item in ["IA générative : principes et outils (ChatGPT, Gemini, Claude, etc.)", "Prompt engineering : techniques avancées", "Agents IA : concepts et applications", "Automatisation de tâches professionnelles avec IA"]:
    doc.add_paragraph(item, style='List Bullet')

p = doc.add_heading("Module 4 : IA pour l'aide à la décision (J4)", level=3)
for item in ["Aide à la décision assistée par IA", "Outils d'aide à la décision et tableaux de bord intelligents", "Étude de cas : aide à la décision pour les projets FID", "Synthèse et évaluation des acquis"]:
    doc.add_paragraph(item, style='List Bullet')

p = doc.add_heading("Vague 2 — Directeurs/Cadres (3 jours)", level=2)
doc.add_paragraph("Contenu adapté avec un focus sur :")
for item in ["L'IA comme levier stratégique de pilotage", "L'analyse de données pour la prise de décision", "L'IA générative pour la production de rapports et synthèses", "La gouvernance des projets IA"]:
    doc.add_paragraph(item, style='List Bullet')

doc.add_page_break()

p = doc.add_heading("5. Organisation et équipe de réalisation", level=1)

p = doc.add_heading("Équipe pédagogique", level=2)
doc.add_paragraph("Formateur principal : Expert en IA et formation professionnelle, minimum 5 ans d'expérience dans la formation aux outils d'IA")
doc.add_paragraph("Assistant pédagogique : Support technique et accompagnement des participants")

p = doc.add_heading("Modalités pédagogiques", level=2)
for item in ["Salle de formation équipée d'ordinateurs pour chaque participant", "Accès internet haut débit", "Outils logiciels : licences IA générative, outils d'analyse de données", "Supports de formation remis à chaque participant"]:
    doc.add_paragraph(item, style='List Bullet')

doc.add_page_break()

p = doc.add_heading("6. Planning et jalons", level=1)

table = doc.add_table(rows=5, cols=4, style='Table Grid')
table.alignment = WD_TABLE_ALIGNMENT.CENTER

en_tete = ["Phase", "Activités", "Durée", "Jalon"]
for i, texte in enumerate(en_tete):
    cell = table.rows[0].cells[i]
    run = cell.paragraphs[0].add_run(texte)
    run.font.name = 'Times New Roman'
    run.font.size = Pt(10)
    run.font.bold = True
    run.font.color.rgb = COULEUR_BLANC
    shading = parse_xml(f'<w:shd {nsdecls("w")} w:fill="003366"/>')
    cell._tc.get_or_add_tcPr().append(shading)

donnees = [
    ["Préparation", "Cadrage, adaptation supports", "J-15 à J-1", "Plan validé"],
    ["Vague 1", "Formation SI/PSE", "4 jours", "Formation réalisée"],
    ["Vague 2", "Formation Directeurs/Cadres", "3 jours", "Formation réalisée"],
    ["Suivi", "Guide, support, évaluation", "J+1 à J+30", "Rapport final"]
]

for i, ligne in enumerate(donnees):
    for j, texte in enumerate(ligne):
        cell = table.rows[i+1].cells[j]
        run = cell.paragraphs[0].add_run(texte)
        run.font.name = 'Times New Roman'
        run.font.size = Pt(10)
        if i % 2 == 0:
            shading = parse_xml(f'<w:shd {nsdecls("w")} w:fill="F2F6FC"/>')
            cell._tc.get_or_add_tcPr().append(shading)

for row in table.rows:
    row.cells[0].width = Cm(3.5)
    row.cells[1].width = Cm(6)
    row.cells[2].width = Cm(3.5)
    row.cells[3].width = Cm(5)

doc.add_page_break()

p = doc.add_heading("7. Livrables", level=1)

table = doc.add_table(rows=5, cols=3, style='Table Grid')
table.alignment = WD_TABLE_ALIGNMENT.CENTER

en_tete_liv = ["Livrable", "Description", "Échéance"]
for i, texte in enumerate(en_tete_liv):
    cell = table.rows[0].cells[i]
    run = cell.paragraphs[0].add_run(texte)
    run.font.name = 'Times New Roman'
    run.font.size = Pt(10)
    run.font.bold = True
    run.font.color.rgb = COULEUR_BLANC
    shading = parse_xml(f'<w:shd {nsdecls("w")} w:fill="003366"/>')
    cell._tc.get_or_add_tcPr().append(shading)

donnees_liv = [
    ["Plan de formation détaillé", "Programme jour par jour", "J-15"],
    ["Supports de formation", "Présentations, exercices, études de cas", "J-1"],
    ["Guide de référence rapide", "Synthèse des outils et techniques", "J+1"],
    ["Rapport de formation", "Bilan, évaluations, recommandations", "J+5"]
]

for i, ligne in enumerate(donnees_liv):
    for j, texte in enumerate(ligne):
        cell = table.rows[i+1].cells[j]
        run = cell.paragraphs[0].add_run(texte)
        run.font.name = 'Times New Roman'
        run.font.size = Pt(10)
        if i % 2 == 0:
            shading = parse_xml(f'<w:shd {nsdecls("w")} w:fill="F2F6FC"/>')
            cell._tc.get_or_add_tcPr().append(shading)

for row in table.rows:
    row.cells[0].width = Cm(5)
    row.cells[1].width = Cm(9)
    row.cells[2].width = Cm(4)

doc.add_page_break()

p = doc.add_heading("8. Suivi, évaluation et assurance qualité", level=1)

p = doc.add_heading("Indicateurs de performance", level=2)
for item in ["Taux de satisfaction des participants (objectif : ≥ 85%)", "Taux de participation (objectif : ≥ 90%)", "Acquisition des compétences (test pré/post formation)"]:
    doc.add_paragraph(item, style='List Bullet')

p = doc.add_heading("Mécanismes de suivi", level=2)
for item in ["Évaluation à chaud en fin de formation", "Évaluation à froid à 30 jours (support à distance)", "Rapport intermédiaire et rapport final"]:
    doc.add_paragraph(item, style='List Bullet')

doc.add_page_break()

p = doc.add_heading("9. Analyse des risques", level=1)

table = doc.add_table(rows=4, cols=3, style='Table Grid')
table.alignment = WD_TABLE_ALIGNMENT.CENTER

en_tete_risque = ["Risques", "Impact potentiel", "Mesure d'atténuation"]
for i, texte in enumerate(en_tete_risque):
    cell = table.rows[0].cells[i]
    run = cell.paragraphs[0].add_run(texte)
    run.font.name = 'Times New Roman'
    run.font.size = Pt(10)
    run.font.bold = True
    run.font.color.rgb = COULEUR_BLANC
    shading = parse_xml(f'<w:shd {nsdecls("w")} w:fill="003366"/>')
    cell._tc.get_or_add_tcPr().append(shading)

donnees_risque = [
    ["Indisponibilité des outils IA", "Perturbation des exercices", "Solutions de secours hors ligne"],
    ["Niveau hétérogène des participants", "Difficulté d'adaptation", "Pré-évaluation des profils"],
    ["Problèmes techniques", "Retard de formation", "Support technique sur site"]
]

for i, ligne in enumerate(donnees_risque):
    for j, texte in enumerate(ligne):
        cell = table.rows[i+1].cells[j]
        run = cell.paragraphs[0].add_run(texte)
        run.font.name = 'Times New Roman'
        run.font.size = Pt(10)
        if i % 2 == 0:
            shading = parse_xml(f'<w:shd {nsdecls("w")} w:fill="F2F6FC"/>')
            cell._tc.get_or_add_tcPr().append(shading)

for row in table.rows:
    row.cells[0].width = Cm(6)
    row.cells[1].width = Cm(6)
    row.cells[2].width = Cm(6)

doc.add_page_break()

p = doc.add_heading("10. Engagement", level=1)

for engagement in ["Respecter le planning convenu", "Fournir un formateur qualifié et expérimenté", "Adapter les contenus aux spécificités du FID", "Assurer un suivi post-formation de qualité", "Remettre tous les livrables dans les délais"]:
    doc.add_paragraph(engagement, style='List Bullet')

doc.add_paragraph()

p = doc.add_paragraph()
run = p.add_run("CARSO — Centre d'Affaires de la Région Sud-Ouest")
run.font.name = 'Times New Roman'
run.font.size = Pt(12)
run.font.bold = True
run.font.color.rgb = COULEUR_PRIMAIRE

p = doc.add_paragraph()
run = p.add_run("Toliara, Madagascar")
run.font.name = 'Times New Roman'
run.font.size = Pt(11)
run.font.color.rgb = COULEUR_GRIS

p = doc.add_paragraph()
run = p.add_run("carso@carso.mg / +261 34 61 496 36")
run.font.name = 'Times New Roman'
run.font.size = Pt(11)
run.font.color.rgb = COULEUR_GRIS

p = doc.add_paragraph()
run = p.add_run("www.carso.mg")
run.font.name = 'Times New Roman'
run.font.size = Pt(11)
run.font.color.rgb = COULEUR_SECONDAIRE

nom_fichier = "offre_technique_fid_ami_59_lot01_v6.docx"
doc.save(nom_fichier)
print(f"Document sauvegardé : {nom_fichier}")
print(f"Nombre de pages estimées : ~10")
print(f"Styles appliqués : Times New Roman, couleurs FID")
print(f"Tableaux : 4 (résumé, planning, livrables, risques)")
