import io
import base64
from docx import Document
from docx.shared import Inches, Pt, Cm, RGBColor, Emu
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.section import WD_ORIENT
from docx.oxml.ns import qn, nsdecls
from docx.oxml import parse_xml
import datetime

# ============================================================
# CONFIGURATION
# ============================================================
ANCRE_OFFRE = "f12fb5d5-0a4b-4eee-a918-1d4f31810e26"
REFERENCE_APPEL = "N°59/AMI/FID/FSR/SQC_CAB_FORM_IA"
TITRE_OFFRE = "OFFRE TECHNIQUE — LOT 01 : FORMATION IA SUR L'IA APPLIQUEE"
NOM_CABINET = "CARSO — Centre d'Affaires de la Région Sud-Ouest"
LIEU_DATE = "Toliara, le 6 novembre 2026"
DATE_DU_JOUR = "6 novembre 2026"

# Couleurs FID (à adapter si nécessaire)
COULEUR_PRIMAIRE = RGBColor(0x00, 0x33, 0x66)  # Bleu foncé
COULEUR_SECONDAIRE = RGBColor(0x00, 0x66, 0x99)  # Bleu moyen
COULEUR_ACCENT = RGBColor(0xCC, 0x66, 0x00)  # Orange
COULEUR_GRIS = RGBColor(0x66, 0x66, 0x66)
COULEUR_BLANC = RGBColor(0xFF, 0xFF, 0xFF)
COULEUR_NOIR = RGBColor(0x00, 0x00, 0x00)

# ============================================================
# CRÉATION DU DOCUMENT
# ============================================================
doc = Document()

# Configuration de la page
section = doc.sections[0]
section.page_width = Cm(21)
section.page_height = Cm(29.7)
section.top_margin = Cm(2.54)
section.bottom_margin = Cm(2.54)
section.left_margin = Cm(3.17)
section.right_margin = Cm(3.17)

# ============================================================
# STYLES
# ============================================================
style_normal = doc.styles['Normal']
style_normal.font.name = 'Times New Roman'
style_normal.font.size = Pt(12)
style_normal.font.color.rgb = COULEUR_NOIR
style_normal.paragraph_format.space_after = Pt(6)
style_normal.paragraph_format.line_spacing = 1.15

# Style Heading 1
style_h1 = doc.styles['Heading 1']
style_h1.font.name = 'Times New Roman'
style_h1.font.size = Pt(16)
style_h1.font.bold = True
style_h1.font.color.rgb = COULEUR_PRIMAIRE
style_h1.paragraph_format.space_before = Pt(18)
style_h1.paragraph_format.space_after = Pt(12)

# Style Heading 2
style_h2 = doc.styles['Heading 2']
style_h2.font.name = 'Times New Roman'
style_h2.font.size = Pt(14)
style_h2.font.bold = True
style_h2.font.color.rgb = COULEUR_SECONDAIRE
style_h2.paragraph_format.space_before = Pt(14)
style_h2.paragraph_format.space_after = Pt(8)

# Style Heading 3
style_h3 = doc.styles['Heading 3']
style_h3.font.name = 'Times New Roman'
style_h3.font.size = Pt(12)
style_h3.font.bold = True
style_h3.font.italic = True
style_h3.font.color.rgb = COULEUR_PRIMAIRE
style_h3.paragraph_format.space_before = Pt(10)
style_h3.paragraph_format.space_after = Pt(6)

# ============================================================
# PAGE DE COUVERTURE
# ============================================================

# Espace en haut
for _ in range(4):
    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(12)

# Titre principal
p = doc.add_paragraph()
p.alignment = WD_ALIGN_PARAGRAPH.CENTER
run = p.add_run("OFFRE TECHNIQUE")
run.font.name = 'Times New Roman'
run.font.size = Pt(28)
run.font.bold = True
run.font.color.rgb = COULEUR_PRIMAIRE

# Sous-titre
p = doc.add_paragraph()
p.alignment = WD_ALIGN_PARAGRAPH.CENTER
run = p.add_run("LOT 01 : FORMATION SUR L'IA APPLIQUEE")
run.font.name = 'Times New Roman'
run.font.size = Pt(18)
run.font.color.rgb = COULEUR_SECONDAIRE

# Ligne séparatrice
p = doc.add_paragraph()
p.alignment = WD_ALIGN_PARAGRAPH.CENTER
p.paragraph_format.space_before = Pt(12)
p.paragraph_format.space_after = Pt(12)
# On ajoute un paragraphe avec une bordure en dessous
pPr = p._p.get_or_add_pPr()
pBdr = parse_xml(f'<w:pBdr {nsdecls("w")}><w:bottom w:val="single" w:sz="12" w:space="1" w:color="003366"/></w:pBdr>')
pPr.append(pBdr)

# Référence appel
p = doc.add_paragraph()
p.alignment = WD_ALIGN_PARAGRAPH.CENTER
run = p.add_run(f"Appel à Manifestation d'Intérêt N° {REFERENCE_APPEL}")
run.font.name = 'Times New Roman'
run.font.size = Pt(14)
run.font.color.rgb = COULEUR_GRIS

# Organisation
p = doc.add_paragraph()
p.alignment = WD_ALIGN_PARAGRAPH.CENTER
run = p.add_run("Fonds d'Intervention pour le Développement (FID)")
run.font.name = 'Times New Roman'
run.font.size = Pt(14)
run.font.bold = True
run.font.color.rgb = COULEUR_PRIMAIRE

# Espace
for _ in range(3):
    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(12)

# Soumis par
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

# Date
p = doc.add_paragraph()
p.alignment = WD_ALIGN_PARAGRAPH.CENTER
run = p.add_run(LIEU_DATE)
run.font.name = 'Times New Roman'
run.font.size = Pt(12)
run.font.color.rgb = COULEUR_GRIS

# Espace avant saut de page
for _ in range(4):
    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(12)

doc.add_page_break()

# ============================================================
# LETTRE DE SOUMISSION
# ============================================================
p = doc.add_paragraph()
p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
run = p.add_run(LIEU_DATE)
run.font.name = 'Times New Roman'
run.font.size = Pt(12)

doc.add_paragraph()  # espace

p = doc.add_paragraph()
run = p.add_run("Objet : Proposition de formation sur l'IA appliquée aux usages professionnels")
run.font.name = 'Times New Roman'
run.font.size = Pt(12)
run.font.bold = True

doc.add_paragraph()  # espace

p = doc.add_paragraph()
run = p.add_run("Madame, Monsieur,")
run.font.name = 'Times New Roman'
run.font.size = Pt(12)

doc.add_paragraph()  # espace

# Corps de la lettre
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

doc.add_paragraph()  # espace

p = doc.add_paragraph()
run = p.add_run("CARSO — Centre d'Affaires de la Région Sud-Ouest")
run.font.name = 'Times New Roman'
run.font.size = Pt(12)
run.font.bold = True

doc.add_page_break()

# ============================================================
# SOMMAIRE
# ============================================================
p = doc.add_heading("SOMMAIRE", level=1)

sommaire_items = [
    ("1.", "Synthèse de la proposition", "3"),
    ("2.", "Compréhension des termes de référence", "4"),
    ("3.", "Approche et méthodologie", "5"),
    ("4.", "Contenu détaillé de la formation", "6"),
    ("5.", "Organisation et équipe de réalisation", "7"),
    ("6.", "Planning et jalons", "8"),
    ("7.", "Livrables", "8"),
    ("8.", "Suivi, évaluation et assurance qualité", "9"),
    ("9.", "Analyse des risques", "9"),
    ("10.", "Engagement", "10"),
]

for num, titre, page in sommaire_items:
    p = doc.add_paragraph()
    run = p.add_run(f"{num} {titre}")
    run.font.name = 'Times New Roman'
    run.font.size = Pt(12)
    run.font.color.rgb = COULEUR_PRIMAIRE
    p.paragraph_format.space_after = Pt(4)

doc.add_page_break()

# ============================================================
# SECTION 1 : SYNTHÈSE
# ============================================================
p = doc.add_heading("1. Synthèse de la proposition", level=1)

paragraphes_synthese = [
    "CARSO propose au Fonds d'Intervention pour le Développement (FID) une formation de haute qualité sur l'Intelligence Artificielle appliquée aux usages professionnels, répondant précisément aux exigences de l'AMI N°59/AMI/FID/FSR/SQC_CAB_FORM_IA.",
    
    "Cette formation s'adresse à deux vagues de participants : les agents SI/PSE (4 jours) et les Directeurs/Cadres (3 jours), avec un contenu adapté aux profils et aux responsabilités de chacun.",
    
    "Notre approche pédagogique combine théorie et pratique, avec une forte dimension expérimentale sur des outils concrets d'IA générative, d'analyse de données et d'aide à la décision.",
]

for paragraphe in paragraphes_synthese:
    p = doc.add_paragraph(paragraphe)
    p.style = doc.styles['Normal']
    p.paragraph_format.first_line_indent = Cm(1)

# Tableau résumé
table = doc.add_table(rows=5, cols=2, style='Table Grid')
table.alignment = WD_TABLE_ALIGNMENT.CENTER

donnees_tableau = [
    ("Organisation", NOM_CABINET),
    ("Appel", REFERENCE_APPEL),
    ("Durée totale", "7 jours (Vague 1 : 4 jours + Vague 2 : 3 jours)"),
    ("Public cible", "Agents SI/PSE + Directeurs/Cadres du FID"),
]

for i, (cle, valeur) in enumerate(donnees_tableau):
    cell0 = table.rows[i].cells[0]
    cell1 = table.rows[i].cells[1]
    
    run0 = cell0.paragraphs[0].add_run(cle)
    run0.font.name = 'Times New Roman'
    run0.font.size = Pt(11)
    run0.font.bold = True
    run0.font.color.rgb = COULEUR_BLANC
    cell0.paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.LEFT
    
    # Fond bleu pour la cellule de gauche
    shading_elm = parse_xml(f'<w:shd {nsdecls("w")} w:fill="003366"/>')
    cell0.paragraphs[0].runs[0].element.get_or_add_pPr()
    cell0._tc.get_or_add_tcPr().append(shading_elm)
    
    run1 = cell1.paragraphs[0].add_run(valeur)
    run1.font.name = 'Times New Roman'
    run1.font.size = Pt(11)
    run1.font.color.rgb = COULEUR_NOIR

# Ajuster largeur colonne 1
for row in table.rows:
    row.cells[0].width = Cm(6)
    row.cells[1].width = Cm(14)

doc.add_page_break()

# ============================================================
# SECTION 2 : COMPRÉHENSION
# ============================================================
p = doc.add_heading("2. Compréhension des termes de référence", level=1)

paragraphes_comp = [
    "Le Fonds d'Intervention pour le Développement (FID) lance un Appel à Manifestation d'Intérêt en vue du recrutement d'un cabinet ou centre de formation pour la prestation de formation sur l'Intelligence Artificielle (IA) appliquée aux usages professionnels.",
    
    "Contexte et enjeux :",
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
    "Appliquer l'IA à l'aide à la décision professionnelle",
]

for obj in objectifs:
    p = doc.add_paragraph(obj, style='List Bullet')

p = doc.add_heading("Public cible", level=2)

p = doc.add_paragraph("Vague 1 : Agents SI/PSE — 4 jours")
p.style = doc.styles['Normal']
p = doc.add_paragraph("Vague 2 : Directeurs/Cadres — 3 jours")
p.style = doc.styles['Normal']

doc.add_page_break()

# ============================================================
# SECTION 3 : MÉTHODOLOGIE
# ============================================================
p = doc.add_heading("3. Approche et méthodologie", level=1)

p = doc.add_paragraph("Notre approche pédagogique repose sur un équilibre entre théorie et pratique, avec une forte dimension expérimentale.")
p.style = doc.styles['Normal']
p.paragraph_format.first_line_indent = Cm(1)

p = doc.add_heading("Phase 1 — Cadrage et préparation (J-15)", level=2)
phases_1 = [
    "Analyse détaillée des profils des participants",
    "Adaptation des supports aux spécificités du FID",
    "Préparation de l'environnement de travail (outils, licences)",
    "Validation du plan de formation avec le FID",
]
for item in phases_1:
    doc.add_paragraph(item, style='List Bullet')

p = doc.add_heading("Phase 2 — Déploiement de la formation", level=2)
phases_2 = [
    "Approche par modules progressifs",
    "Alternance de présentations, démonstrations et exercices pratiques",
    "Études de cas réels ou simulés en lien avec les activités du FID",
    "Travaux en sous-groupes pour favoriser l'apprentissage collaboratif",
    "Sessions de questions-réponses et retours d'expérience",
]
for item in phases_2:
    doc.add_paragraph(item, style='List Bullet')

p = doc.add_heading("Phase 3 — Suivi post-formation", level=2)
phases_3 = [
    "Envoi d'un guide de référence rapide",
    "Support à distance pendant 30 jours",
    "Évaluation de satisfaction et de l'acquisition des compétences",
]
for item in phases_3:
    doc.add_paragraph(item, style='List Bullet')

doc.add_page_break()

# ============================================================
# SECTION 4 : CONTENU DÉTAILLÉ
# ============================================================
p = doc.add_heading("4. Contenu détaillé de la formation", level=1)

# Vague 1
p = doc.add_heading("Vague 1 — Agents SI/PSE (4 jours)", level=2)

p = doc.add_heading("Module 1 : Fondamentaux de l'IA (J1)", level=3)
mod1 = [
    "Qu'est-ce que l'IA ? Histoire, concepts et terminologie",
    "Types d'IA : IA faible vs IA forte, apprentissage supervisé/non supervisé",
    "Domaines d'application de l'IA dans le secteur public et le développement",
    "Éthique de l'IA et limites",
]
for item in mod1:
    doc.add_paragraph(item, style='List Bullet')

p = doc.add_heading("Module 2 : Analyse de données et visualisation (J2)", level=3)
mod2 = [
    "Introduction à l'analyse de données avec outils IA",
    "Nettoyage et préparation des données",
    "Visualisation de données : principes et outils",
    "Étude de cas : analyse de données de projets FID",
]
for item in mod2:
    doc.add_paragraph(item, style='List Bullet')

p = doc.add_heading("Module 3 : IA générative et agents IA (J3)", level=3)
mod3 = [
    "IA générative : principes et outils (ChatGPT, Gemini, Claude, etc.)",
    "Prompt engineering : techniques avancées",
    "Agents IA : concepts et applications",
    "Automatisation de tâches professionnelles avec IA",
]
for item in mod3:
    doc.add_paragraph(item, style='List Bullet')

p = doc.add_heading("Module 4 : IA pour l'aide à la décision (J4)", level=3)
mod4 = [
    "Aide à la décision assistée par IA",
    "Outils d'aide à la décision et tableaux de bord intelligents",
    "Étude de cas : aide à la décision pour les projets FID",
    "Synthèse et évaluation des acquis",
]
for item in mod4:
    doc.add_paragraph(item, style='List Bullet')

# Vague 2
p = doc.add_heading("Vague 2 — Directeurs/Cadres (3 jours)", level=2)
p = doc.add_paragraph("Contenu adapté avec un focus sur :")
p.style = doc.styles['Normal']
p.paragraph_format.first_line_indent = Cm(1)

vague2 = [
    "L'IA comme levier stratégique de pilotage",
    "L'analyse de données pour la prise de décision",
    "L'IA générative pour la production de rapports et synthèses",
    "La gouvernance des projets IA",
]
for item in vague2:
    doc.add_paragraph(item, style='List Bullet')

doc.add_page_break()

# ============================================================
# SECTION 5 : ORGANISATION
# ============================================================
p = doc.add_heading("5. Organisation et équipe de réalisation", level=1)

p = doc.add_heading("Équipe pédagogique", level=2)
p = doc.add_paragraph("Formateur principal : Expert en IA et formation professionnelle, minimum 5 ans d'expérience dans la formation aux outils d'IA")
p.style = doc.styles['Normal']
p.paragraph_format.first_line_indent = Cm(1)

p = doc.add_paragraph("Assistant pédagogique : Support technique et accompagnement des participants")
p.style = doc.styles['Normal']
p.paragraph_format.first_line_indent = Cm(1)

p = doc.add_heading("Modalités pédagogiques", level=2)
modalites = [
    "Salle de formation équipée d'ordinateurs pour chaque participant",
    "Accès internet haut débit",
    "Outils logiciels : licences IA générative, outils d'analyse de données",
    "Supports de formation remis à chaque participant",
]
for item in modalites:
    doc.add_paragraph(item, style='List Bullet')

doc.add_page_break()

# ============================================================
# SECTION 6 : PLANNING
# ============================================================
p = doc.add_heading("6. Planning et jalons", level=1)

table = doc.add_table(rows=5, cols=4, style='Table Grid')
table.alignment = WD_TABLE_ALIGNMENT.CENTER

# En-tête
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

# Données
donnees = [
    ["Préparation", "Cadrage, adaptation supports", "J-15 à J-1", "Plan validé"],
    ["Vague 1", "Formation SI/PSE", "4 jours", "Formation réalisée"],
    ["Vague 2", "Formation Directeurs/Cadres", "3 jours", "Formation réalisée"],
    ["Suivi", "Guide, support, évaluation", "J+1 à J+30", "Rapport final"],
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

# Largeurs
for row in table.rows:
    row.cells[0].width = Cm(3.5)
    row.cells[1].width = Cm(6)
    row.cells[2].width = Cm(3.5)
    row.cells[3].width = Cm(5)

doc.add_page_break()

# ============================================================
# SECTION 7 : LIVRABLES
# ============================================================
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
    ["Rapport de formation", "Bilan, évaluations, recommandations", "J+5"],
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

# ============================================================
# SECTION 8 : SUIVI ET ÉVALUATION
# ============================================================
p = doc.add_heading("8. Suivi, évaluation et assurance qualité", level=1)

p = doc.add_heading("Indicateurs de performance", level=2)
indicateurs = [
    "Taux de satisfaction des participants (objectif : ≥ 85%)",
    "Taux de participation (objectif : ≥ 90%)",
    "Acquisition des compétences (test pré/post formation)",
]
for item in indicateurs:
    doc.add_paragraph(item, style='List Bullet')

p = doc.add_heading("Mécanismes de suivi", level=2)
mecanismes = [
    "Évaluation à chaud en fin de formation",
    "Évaluation à froid à 30 jours (support à distance)",
    "Rapport intermédiaire et rapport final",
]
for item in mecanismes:
    doc.add_paragraph(item, style='List Bullet')

doc.add_page_break()

# ============================================================
# SECTION 9 : RISQUES
# ============================================================
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
    ["Problèmes techniques", "Retard de formation", "Support technique sur site"],
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

# ============================================================
# SECTION 10 : ENGAGEMENT
# ============================================================
p = doc.add_heading("10. Engagement", level=1)

engagements = [
    "Respecter le planning convenu",
    "Fournir un formateur qualifié et expérimenté",
    "Adapter les contenus aux spécificités du FID",
    "Assurer un suivi post-formation de qualité",
    "Remettre tous les livrables dans les délais",
]

for engagement in engagements:
    p = doc.add_paragraph(engagement, style='List Bullet')

doc.add_paragraph()  # espace

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

# ============================================================
# SAUVEGARDE
# ============================================================
nom_fichier = "offre_technique_fid_ami_59_lot01_v6.docx"
doc.save(nom_fichier)
print(f"Document sauvegardé : {nom_fichier}")
print(f"Nombre de pages estimées : ~10")
print(f"Styles appliqués : Times New Roman, couleurs FID")
print(f"Tableaux : 4 (résumé, planning, livrables, risques)")
