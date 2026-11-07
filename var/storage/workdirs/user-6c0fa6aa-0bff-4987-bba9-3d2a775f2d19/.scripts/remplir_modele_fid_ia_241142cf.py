from docx import Document
from docx.shared import Pt, Cm, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.oxml.ns import qn

# Chemins
modele_path = "e80dbc38-138c-4060-905c-07bca0ab3e16.docx"
output_path = "Offre_Technique_AMI59_FID_Lot01.docx"

doc = Document(modele_path)

# ============================================================
# 1. NETTOYER LE DOCUMENT : supprimer tous les paragraphes existants
# ============================================================
body = doc.body
while body.paragraphs:
    p = body.paragraphs[0]
    p._element.getparent().remove(p._element)

# ============================================================
# 2. COUVERTURE
# ============================================================

def add_cover_paragraph(doc, text, bold=False, size=14, alignment=WD_ALIGN_PARAGRAPH.CENTER, space_after=6, space_before=0, color=None, italic=False):
    p = doc.add_paragraph()
    p.alignment = alignment
    p.paragraph_format.space_after = Pt(space_after)
    p.paragraph_format.space_before = Pt(space_before)
    run = p.add_run(text)
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.italic = italic
    run.font.name = 'Times New Roman'
    if color:
        run.font.color.rgb = color
    return p

# Titre principal couverture
add_cover_paragraph(doc, "OFFRE TECHNIQUE", bold=True, size=22, space_after=24)

# Sous-titre
add_cover_paragraph(doc, "Formation sur l'intelligence artificielle appliquée aux usages professionnels,\nà l'analyse de données, aux agents IA et à l'aide à la décision", bold=False, size=14, space_after=16)

# Référence
add_cover_paragraph(doc, "Appel à Manifestation d'Intérêt N°59/AMI/FID/FSR/SQC_CAB_FORM_IA", bold=True, size=12, space_after=24)

# Informations
add_cover_paragraph(doc, "Organisme destinataire : Fonds d'Intervention pour le Développement (FID)", size=11, space_after=4)
add_cover_paragraph(doc, "Lot n°01 : Formation IA appliquée aux usages professionnels", size=11, space_after=4)
add_cover_paragraph(doc, "Zone d'intervention : Antananarivo (Direction Générale FID)", size=11, space_after=4)
add_cover_paragraph(doc, "Date de soumission : Novembre 2026", size=11, space_after=4)
add_cover_paragraph(doc, "Entreprise : CARSO - Centre d'Affaires de la Région Sud-Ouest", size=11, space_after=24)

# Saut de page après couverture
doc.add_page_break()

# ============================================================
# 3. SOMMAIRE (titre)
# ============================================================
p = doc.add_paragraph()
p.alignment = WD_ALIGN_PARAGRAPH.LEFT
run = p.add_run("Sommaire")
run.font.name = 'Times New Roman'
run.font.size = Pt(22)
run.font.bold = True
p.paragraph_format.space_after = Pt(12)

# ============================================================
# 4. CONTENU PRINCIPAL
# ============================================================

def add_heading_custom(doc, text, level=2):
    p = doc.add_paragraph()
    run = p.add_run(text)
    run.font.name = 'Times New Roman'
    run.font.bold = True
    if level == 2:
        run.font.size = Pt(14)
        p.paragraph_format.space_before = Pt(14)
        p.paragraph_format.space_after = Pt(6)
        pPr = p._element.get_or_add_pPr()
        pBdr = pPr.makeelement(qn('w:pBdr'), {})
        bottom = pBdr.makeelement(qn('w:bottom'), {
            qn('w:val'): 'single',
            qn('w:sz'): '12',
            qn('w:space'): '1',
            qn('w:color'): '0B4A8F'
        })
        pBdr.append(bottom)
        pPr.append(pBdr)
    elif level == 3:
        run.font.size = Pt(12)
        p.paragraph_format.space_before = Pt(10)
        p.paragraph_format.space_after = Pt(4)
    return p

def add_body(doc, text, bold=False, size=11, space_after=6, space_before=0, italic=False):
    p = doc.add_paragraph()
    run = p.add_run(text)
    run.font.name = 'Times New Roman'
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.italic = italic
    p.paragraph_format.space_after = Pt(space_after)
    p.paragraph_format.space_before = Pt(space_before)
    p.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    return p

def add_bullet(doc, text, level=0, size=11):
    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(3)
    p.paragraph_format.space_before = Pt(1)
    p.paragraph_format.left_indent = Cm(1.27 + level * 1.0)
    run = p.add_run("• " + text)
    run.font.name = 'Times New Roman'
    run.font.size = Pt(size)
    return p

def add_table(doc, headers, rows, col_widths=None):
    table = doc.add_table(rows=1 + len(rows), cols=len(headers))
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    
    # En-tête
    for i, header in enumerate(headers):
        cell = table.rows[0].cells[i]
        cell.text = ""
        p = cell.paragraphs[0]
        run = p.add_run(header)
        run.font.name = 'Times New Roman'
        run.font.size = Pt(10)
        run.font.bold = True
        run.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
        p.alignment = WD_ALIGN_PARAGRAPH.LEFT
        shading = p._element.makeelement(qn('w:shd'), {
            qn('w:fill'): '0B4A8F',
            qn('w:val'): 'clear'
        })
        p._element.getparent().insert(0, shading)
        tc = cell._element
        tcPr = tc.get_or_add_tcPr()
        tcBorders = tcPr.makeelement(qn('w:tcBorders'), {})
        for border_name in ['top', 'left', 'bottom', 'right']:
            border = tcBorders.makeelement(qn(f'w:{border_name}'), {
                qn('w:val'): 'single',
                qn('w:sz'): '4',
                qn('w:space'): '0',
                qn('w:color'): 'B4C6E7'
            })
            tcBorders.append(border)
        tcPr.append(tcBorders)
    
    # Lignes de données
    for row_idx, row_data in enumerate(rows):
        for col_idx, cell_text in enumerate(row_data):
            cell = table.rows[row_idx + 1].cells[col_idx]
            cell.text = ""
            p = cell.paragraphs[0]
            run = p.add_run(str(cell_text))
            run.font.name = 'Times New Roman'
            run.font.size = Pt(10)
            p.alignment = WD_ALIGN_PARAGRAPH.LEFT
            if row_idx % 2 == 1:
                shading = p._element.makeelement(qn('w:shd'), {
                    qn('w:fill'): 'F0F4F8',
                    qn('w:val'): 'clear'
                })
                p._element.getparent().insert(0, shading)
            tc = cell._element
            tcPr = tc.get_or_add_tcPr()
            tcBorders = tcPr.makeelement(qn('w:tcBorders'), {})
            for border_name in ['top', 'left', 'bottom', 'right']:
                border = tcBorders.makeelement(qn(f'w:{border_name}'), {
                    qn('w:val'): 'single',
                    qn('w:sz'): '4',
                    qn('w:space'): '0',
                    qn('w:color'): 'B4C6E7'
                })
                tcBorders.append(border)
            tcPr.append(tcBorders)
    
    if col_widths:
        for i, width in enumerate(col_widths):
            for row in table.rows:
                row.cells[i].width = Cm(width)
    
    return table

# ============================================================
# SECTION 1 : SYNTHÈSE
# ============================================================
add_heading_custom(doc, "1. Synthèse de la proposition", level=2)
add_body(doc, "Le Fonds d'Intervention pour le Développement (FID), organisme public de financement soutenu par la Banque mondiale, a lancé un Appel à Manifestation d'Intérêt N°59/AMI/FID/FSR/SQC_CAB_FORM_IA pour recruter un cabinet ou centre de formation afin de dispenser une formation sur l'intelligence artificielle appliquée aux usages professionnels, à l'analyse de données, aux agents IA et à l'aide à la décision. Cette formation s'adresse au personnel de la Direction Générale du FID à Antananarivo.")
add_body(doc, "Notre proposition s'articule autour de quatre piliers pédagogiques correspondant aux quatre axes demandés :", bold=True, space_before=6)
add_bullet(doc, "Intelligence artificielle appliquée aux usages professionnels : comprendre les concepts fondamentaux de l'IA et les appliquer concrètement aux tâches quotidiennes.")
add_bullet(doc, "Analyse de données : maîtriser les outils et méthodes d'analyse pour transformer les données en informations actionnables.")
add_bullet(doc, "Agents IA : découvrir et utiliser les agents conversationnels et automatisés pour optimiser les processus de travail.")
add_bullet(doc, "Aide à la décision : intégrer l'IA dans les processus décisionnels pour améliorer la qualité et la rapidité des choix stratégiques.")
add_body(doc, "La formation sera dispensée en présentiel au siège du FID à Antananarivo, avec une approche 100 % pratique, des études de cas réels et des exercices guidés sur des outils accessibles et utilisables immédiatement.")

# ============================================================
# SECTION 2 : COMPRÉHENSION DU BESOIN
# ============================================================
add_heading_custom(doc, "2. Compréhension du besoin et du contexte", level=2)

add_heading_custom(doc, "2.1. Contexte de la demande", level=3)
add_body(doc, "Le FID, organisme public de financement soutenu par la Banque mondiale, a identifié un besoin stratégique de renforcement des capacités de son personnel en matière d'intelligence artificielle. Dans un contexte où l'IA transforme profondément les pratiques professionnelles et les processus décisionnels, il est essentiel que les agents du FID maîtrisent ces outils pour :")
add_bullet(doc, "Améliorer l'efficacité et la productivité des traitements quotidiens.")
add_bullet(doc, "Renforcer la qualité de l'analyse des données liées aux projets de développement financés par le FID.")
add_bullet(doc, "Optimiser les processus de prise de décision grâce à des outils d'aide à la décision alimentés par l'IA.")
add_bullet(doc, "Se positionner comme une organisation innovante et à la pointe des technologies numériques.")

add_heading_custom(doc, "2.2. Objectifs visés", level=3)
add_body(doc, "L'objectif général est le renforcement des capacités du personnel du FID sur l'IA appliquée aux usages professionnels, l'analyse de données, les agents IA et l'aide à la décision. Cet objectif se décline en objectifs opérationnels :", bold=True)
add_bullet(doc, "Comprendre les concepts fondamentaux de l'intelligence artificielle et leurs applications dans le secteur du développement.")
add_bullet(doc, "Maîtriser les outils d'analyse de données pour extraire des informations pertinentes des données projet.")
add_bullet(doc, "Utiliser les agents IA pour automatiser des tâches répétitives et améliorer la productivité.")
add_bullet(doc, "Intégrer l'IA dans les processus de prise de décision pour des choix plus éclairés et plus rapides.")

add_heading_custom(doc, "2.3. Bénéficiaires", level=3)
add_body(doc, "Les bénéficiaires directs sont les agents de la Direction Générale du FID à Antananarivo. Le nombre exact de participants n'est pas précisé dans l'avis et doit être confirmé par le FID. La formation sera adaptée au niveau de départ des participants, qui peuvent avoir des compétences informatiques variables.")

add_heading_custom(doc, "2.4. Contraintes identifiées", level=3)
add_bullet(doc, "Formation dispensée au siège du FID à Antananarivo.")
add_bullet(doc, "Public hétérogène en termes de compétences numériques : la formation doit être accessible à tous les niveaux.")
add_bullet(doc, "Nécessité d'une approche pratique et directement applicable au contexte professionnel du FID.")
add_bullet(doc, "Disponibilité des participants à concilier formation et activités quotidiennes.")

# ============================================================
# SECTION 3 : OBJECTIFS ET RÉSULTATS ATTENDUS
# ============================================================
add_heading_custom(doc, "3. Objectifs et résultats attendus", level=2)

add_heading_custom(doc, "3.1. Objectifs pédagogiques", level=3)
add_body(doc, "À l'issue de la formation, les participants seront capables de :")

headers = ["N°", "Objectif pédagogique", "Indicateur de réussite"]
rows = [
    ["1", "Expliquer les concepts fondamentaux de l'IA et identifier ses applications dans le contexte du FID", "Quiz de validation des connaissances théoriques"],
    ["2", "Utiliser des outils d'analyse de données pour traiter et visualiser des données projet", "Réalisation d'un exercice pratique avec données réelles"],
    ["3", "Configurer et utiliser des agents IA pour automatiser des tâches professionnelles", "Création d'un agent IA fonctionnel par participant"],
    ["4", "Intégrer des outils d'aide à la décision basés sur l'IA dans les processus du FID", "Étude de cas : proposition d'un plan d'intégration IA"],
]
add_table(doc, headers, rows)

add_heading_custom(doc, "3.2. Résultats attendus", level=3)
add_bullet(doc, "Le personnel du FID maîtrise les concepts et outils d'IA appliquée aux usages professionnels.")
add_bullet(doc, "Des compétences concrètes en analyse de données sont acquises et directement applicables aux projets financés par le FID.")
add_bullet(doc, "Les participants peuvent déployer des agents IA pour optimiser leurs processus de travail.")
add_bullet(doc, "Une culture d'aide à la décision basée sur les données et l'IA est installée au sein de la Direction Générale.")

# ============================================================
# SECTION 4 : MÉTHODOLOGIE
# ============================================================
add_heading_custom(doc, "4. Méthodologie et dispositif pédagogique", level=2)

add_heading_custom(doc, "4.1. Approche pédagogique", level=3)
add_body(doc, "Notre approche repose sur le principe du learning by doing (apprendre en faisant). Chaque module combine :")
add_bullet(doc, "Apports théoriques courts (20 % du temps) : concepts clés, cadre de référence, bonnes pratiques.")
add_bullet(doc, "Démonstrations pratiques (30 % du temps) : l'animateur montre comment utiliser les outils sur des exemples concrets.")
add_bullet(doc, "Exercices guidés (30 % du temps) : les participants appliquent les outils sur des données et scénarios réels du FID.")
add_bullet(doc, "Études de cas et projets (20 % du temps) : les participants travaillent en groupe sur des problématiques réelles du FID.")

add_heading_custom(doc, "4.2. Principes pédagogiques", level=3)
headers = ["Principe", "Description"]
rows = [
    ["Personnalisation", "Contenu adapté au niveau de chaque participant et aux besoins spécifiques du FID"],
    ["Pratique intensive", "Chaque concept théorique est immédiatement appliqué sur un outil concret"],
    ["Contextualisation", "Exemples et exercices basés sur les données et processus réels du FID"],
    ["Apprentissage collaboratif", "Travail en petits groupes pour favoriser les échanges et la mutualisation des expériences"],
    ["Transférabilité", "Chaque outil appris peut être réutilisé immédiatement dans le quotidien professionnel"],
]
add_table(doc, headers, rows)

add_heading_custom(doc, "4.3. Outils et supports", level=3)
add_bullet(doc, "Tableaux de bord et analyse de données : Microsoft Excel (avec Power Query et Power Pivot), Google Sheets, ou outils équivalents déjà disponibles au FID.")
add_bullet(doc, "Agents IA : Outils d'IA conversationnelle accessibles (ChatGPT, Claude, Gemini, ou solutions équivalentes) : les participants utilisent leurs propres appareils ou ceux mis à disposition par le FID.")
add_bullet(doc, "Aide à la décision : Outils de visualisation de données (Power BI, Tableau Public, ou solutions open source).")
add_bullet(doc, "Supports de formation : Manuels de formation, fiches pratiques, enregistrements vidéo des démonstrations, accès à une plateforme en ligne pour les ressources complémentaires.")

add_heading_custom(doc, "4.4. Prérequis techniques", level=3)
add_bullet(doc, "Ordinateur portable pour chaque participant (ou 1 pour 2 participants maximum).")
add_bullet(doc, "Accès Internet stable au siège du FID.")
add_bullet(doc, "Compétences de base en utilisation d'ordinateur et de logiciels bureautiques.")
add_bullet(doc, "Comptes créés à l'avance sur les plateformes d'IA sélectionnées.")

# ============================================================
# SECTION 5 : CONTENU DÉTAILLÉ DE LA FORMATION
# ============================================================
add_heading_custom(doc, "5. Contenu détaillé de la formation", level=2)

# Module 1
add_heading_custom(doc, "5.1. Module 1 : Intelligence artificielle appliquée aux usages professionnels", level=3)
headers = ["Thème", "Contenu", "Méthode", "Livrable"]
rows = [
    ["Introduction à l'IA", "Définitions, historique, types d'IA (faible, générale), domaines d'application dans le secteur du développement", "Présentation interactive", "Fiche de synthèse"],
    ["IA dans les organisations", "Cas d'usage de l'IA dans les organismes de développement, bonnes pratiques, pièges à éviter", "Études de cas", "Matrice des cas d'usage FID"],
    ["Outils d'IA accessibles", "Présentation des outils gratuits et professionnels, critères de choix, éthique et limites de l'IA", "Démonstration + exercice", "Guide d'outils IA"],
    ["Application pratique", "Exercices guidés : automatisation de tâches, génération de rapports, analyse de texte", "Pratique individuelle", "Portfolio d'exercices réalisés"],
]
add_table(doc, headers, rows)

# Module 2
add_heading_custom(doc, "5.2. Module 2 : Analyse de données pour la prise de décision", level=3)
headers = ["Thème", "Contenu", "Méthode", "Livrable"]
rows = [
    ["Fondamentaux de l'analyse de données", "Types de données, collecte, nettoyage, structuration, bonnes pratiques", "Présentation + exercice", "Fiche méthodologique"],
    ["Outils d'analyse", "Excel avancé (formules, tableaux croisés, Power Query), Google Sheets, outils de visualisation", "Démonstration + pratique", "Tableau de bord réalisé"],
    ["Visualisation de données", "Principes de dataviz, choix des graphiques, création de tableaux de bord interactifs", "Atelier pratique", "Dashboard Power BI / Tableau"],
    ["Analyse de données projet FID", "Application sur des données réelles de projets financés par le FID", "Étude de cas", "Rapport d'analyse"],
]
add_table(doc, headers, rows)

# Module 3
add_heading_custom(doc, "5.3. Module 3 : Agents IA et automatisation", level=3)
headers = ["Thème", "Contenu", "Méthode", "Livrable"]
rows = [
    ["Introduction aux agents IA", "Qu'est-ce qu'un agent IA ? Types d'agents, architecture, cas d'usage professionnels", "Présentation interactive", "Fiche de référence"],
    ["Création d'agents conversationnels", "Configuration de chatbots, prompts avancés, personnalisation, tests et itérations", "Pratique guidée", "Agent IA fonctionnel"],
    ["Automatisation des workflows", "Intégration d'agents IA dans les processus de travail, connecteurs, déclencheurs", "Atelier pratique", "Workflow automatisé"],
    ["Agents IA pour le FID", "Conception d'agents adaptés aux besoins spécifiques du FID (suivi de projets, reporting, analyse)", "Projet de groupe", "Prototype d'agent FID"],
]
add_table(doc, headers, rows)

# Module 4
add_heading_custom(doc, "5.4. Module 4 : Aide à la décision par l'IA", level=3)
headers = ["Thème", "Contenu", "Méthode", "Livrable"]
rows = [
    ["Fondamentaux de l'aide à la décision", "Processus décisionnel, critères, outils d'aide à la décision, rôle de l'IA", "Présentation + discussion", "Cadre conceptuel"],
    ["Outils d'aide à la décision basés sur l'IA", "Présentation des outils, analyse prédictive, simulation, scénarios", "Démonstration", "Guide d'outils"],
    ["Intégration de l'IA dans les processus décisionnels du FID", "Étude de cas : comment l'IA peut améliorer les décisions de financement de projets", "Étude de cas en groupe", "Plan d'intégration IA"],
    ["Synthèse et plan d'action", "Restitution des travaux de groupe, plan d'action individuel pour l'application des compétences acquises", "Restitution + engagement", "Plan d'action individuel"],
]
add_table(doc, headers, rows)

# ============================================================
# SECTION 6 : PLANNING ET JALONS
# ============================================================
add_heading_custom(doc, "6. Planning et jalons", level=2)

add_heading_custom(doc, "6.1. Calendrier prévisionnel", level=3)
headers = ["Phase", "Activités", "Durée", "Jalon"]
rows = [
    ["Phase 1", "Préparation : analyse des besoins, adaptation du contenu, création des supports, préparation des environnements techniques", "1 à 2 semaines avant", "Validation du plan de formation par le FID"],
    ["Module 1", "IA appliquée aux usages professionnels", "(à confirmer avec le FID)", "Validation des acquis Module 1"],
    ["Module 2", "Analyse de données", "(à confirmer avec le FID)", "Validation des acquis Module 2"],
    ["Module 3", "Agents IA et automatisation", "(à confirmer avec le FID)", "Validation des acquis Module 3"],
    ["Module 4", "Aide à la décision par l'IA", "(à confirmer avec le FID)", "Validation des acquis Module 4"],
    ["Clôture", "Restitution, évaluation, plan d'action, remise des attestations", "Dernier jour", "Rapport final remis au FID"],
]
add_table(doc, headers, rows)

add_heading_custom(doc, "6.2. Équipe de réalisation", level=3)
headers = ["Rôle", "Qualifications requises", "Responsabilités"]
rows = [
    ["Coordinateur de formation", "Master en formation professionnelle ou domaine connexe, 5+ ans d'expérience en conception et gestion de formation", "Pilotage global, coordination avec le FID, validation pédagogique, reporting"],
    ["Expert IA et données", "Master en informatique, data science ou domaine connexe, expertise en IA appliquée, 3+ ans d'expérience en formation", "Enseignement des modules 1, 2 et 4, conception des exercices pratiques, mise à jour des contenus"],
    ["Expert agents IA et automatisation", "Formation en informatique ou ingénierie des systèmes, expertise en agents IA et automatisation, 3+ ans d'expérience", "Enseignement du module 3, conception des ateliers d'automatisation, support technique"],
    ["Assistant pédagogique", "Bac+2/3, expérience en support de formation, compétences techniques de base", "Support logistique, aide aux participants, gestion des environnements techniques, prise de notes"],
]
add_table(doc, headers, rows)

# ============================================================
# SECTION 7 : MOYENS ET LOGISTIQUE
# ============================================================
add_heading_custom(doc, "7. Moyens et logistique", level=2)

add_heading_custom(doc, "7.1. Moyens pédagogiques", level=3)
add_bullet(doc, "Salle de formation équipée au siège du FID à Antananarivo (confirmer la disponibilité avec le FID).")
add_bullet(doc, "Projecteur, écran, tableau blanc, système audio.")
add_bullet(doc, "Connexion Internet haut débit au sein de la salle de formation.")
add_bullet(doc, "Manuels de formation imprimés ou numériques pour chaque participant.")
add_bullet(doc, "Fiches pratiques et guides de référence remis à chaque participant.")
add_bullet(doc, "Enregistrements vidéo des démonstrations pour consultation ultérieure.")

add_heading_custom(doc, "7.2. Moyens techniques", level=3)
add_bullet(doc, "Ordinateurs portables pour les participants (à fournir par le FID ou par CARSO : à confirmer).")
add_bullet(doc, "Comptes créés à l'avance sur les plateformes d'IA sélectionnées.")
add_bullet(doc, "Environnements de test préconfigurés pour les exercices.")
add_bullet(doc, "Données de démonstration anonymisées issues de projets FID (à fournir par le FID).")

add_heading_custom(doc, "7.3. Moyens administratifs", level=3)
add_bullet(doc, "Assurance responsabilité civile professionnelle du formateur.")
add_bullet(doc, "Attestations de formation remises à chaque participant à l'issue de la formation.")
add_bullet(doc, "Rapport de formation remis au FID sous 5 jours ouvrables après la fin de la formation.")

# ============================================================
# SECTION 8 : SUIVI, ÉVALUATION ET ASSURANCE QUALITÉ
# ============================================================
add_heading_custom(doc, "8. Suivi, évaluation et assurance qualité", level=2)

add_heading_custom(doc, "8.1. Dispositif d'évaluation des acquis", level=3)
headers = ["Niveau", "Méthode", "Objectif"]
rows = [
    ["Évaluation à chaud", "Questionnaire de satisfaction remis à la fin de chaque module et de la formation", "Mesurer la satisfaction des participants et la pertinence du contenu"],
    ["Évaluation des acquis", "Quiz de validation des connaissances à la fin de chaque module", "Vérifier l'appropriation des concepts et outils"],
    ["Évaluation pratique", "Production d'un livrable concret par module (dashboard, agent IA, plan d'action)", "Valider la capacité à appliquer les compétences"],
    ["Évaluation à froid", "Enquête de suivi 3 mois après la formation (optionnelle, à convenir avec le FID)", "Mesurer l'impact réel de la formation sur les pratiques professionnelles"],
]
add_table(doc, headers, rows)

add_heading_custom(doc, "8.2. Reporting et suivi", level=3)
add_bullet(doc, "Rapport final : synthèse de la formation, résultats des évaluations, recommandations pour la suite, plan d'action des participants.")
add_bullet(doc, "Attestations : remises à chaque participant ayant validé les modules.")

add_heading_custom(doc, "8.3. Gestion des risques", level=3)
headers = ["Risque identifié", "Impact potentiel", "Mesure d'atténuation"]
rows = [
    ["Niveau de compétences hétérogène des participants", "Élevé", "Diagnostic préalable des compétences ; exercices différenciés ; support individuel renforcé"],
    ["Problèmes techniques (Internet, ordinateurs)", "Moyen", "Plan de secours : supports hors ligne, exercices sans connexion, matériel de backup"],
    ["Indisponibilité de participants", "Moyen", "Enregistrements vidéo des sessions ; rattrissage individuel si besoin"],
    ["Résistance au changement", "Moyen", "Communication en amont sur les bénéfices ; démonstrations concrètes et valorisantes"],
]
add_table(doc, headers, rows)

# ============================================================
# SECTION 9 : ANNEXES
# ============================================================
add_heading_custom(doc, "9. Annexes", level=2)

add_heading_custom(doc, "9.1. Liste des éléments à confirmer avec le FID", level=3)
headers = ["Élément", "Statut"]
rows = [
    ["Nombre exact de participants", "(à confirmer par le FID)"],
    ["Durée totale de la formation (nombre de jours)", "(à valider avec le FID)"],
    ["Durée de chaque module", "(à valider avec le FID)"],
    ["Date de début de formation", "(à confirmer par le FID)"],
    ["Disponibilité de la salle de formation au siège FID", "(à confirmer par le FID)"],
    ["Fourniture des ordinateurs portables par le FID ou CARSO", "(à confirmer)"],
    ["Données réelles de projets FID pour les exercices pratiques", "(à fournir par le FID)"],
    ["Outils logiciels déjà disponibles au FID (Excel, Power BI, etc.)", "(à confirmer par le FID)"],
]
add_table(doc, headers, rows)

# ============================================================
# SECTION 10 : CONCLUSION ET ENGAGEMENT
# ============================================================
add_heading_custom(doc, "10. Conclusion et engagement", level=2)
add_body(doc, "CARSO s'engage à dispenser une formation de haute qualité, parfaitement adaptée aux besoins du Fonds d'Intervention pour le Développement. Notre approche pédagogique pratique et contextualisée garantit que chaque participant repartira avec des compétences opérationnelles immédiatement applicables dans son travail quotidien.")
add_body(doc, "Nous nous engageons à :", bold=True, space_before=6)
add_bullet(doc, "Respecter le calendrier convenu avec le FID.")
add_bullet(doc, "Adapter le contenu en fonction du niveau et des besoins spécifiques des participants.")
add_bullet(doc, "Fournir des supports de formation complets et des ressources pour la suite.")
add_bullet(doc, "Assurer un suivi post-formation pour maximiser l'impact de la formation.")
add_bullet(doc, "Remettre un rapport final détaillé dans les délais convenus.")
add_body(doc, "Nous restons à la disposition du FID pour toute information complémentaire et pour ajuster cette proposition en fonction de ses retours.")

# ============================================================
# SAUVEGARDER
# ============================================================
doc.save(output_path)
print(f"Document sauvegardé avec succès : {output_path}")
