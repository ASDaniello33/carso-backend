"""
Générer l'offre technique FID en préservant la structure du modèle.
"""
from pathlib import Path
from docx import Document
from docx.shared import Pt, Inches, Cm, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.oxml.ns import qn
import json

# Chemins
TEMPLATE_PATH = Path("Model_offre_technique_FID.docx")
OUTPUT_PATH = Path("OFFRE-TECH-L01_FID_Formation_IA.docx")

# Charger le document
doc = Document(TEMPLATE_PATH)

# Configuration des styles
style_normal = doc.styles['Normal']
font_normal = style_normal.font
font_normal.name = 'Times New Roman'
font_normal.size = Pt(14)
font_normal.color.rgb = RGBColor(0, 0, 0)
style_normal.paragraph_format.space_after = Pt(6)
style_normal.paragraph_format.space_before = Pt(4)
style_normal.paragraph_format.line_spacing = 1.5

style_heading1 = doc.styles['Heading 1']
font_h1 = style_heading1.font
font_h1.name = 'Times New Roman'
font_h1.size = Pt(14)
font_h1.bold = True
font_h1.color.rgb = RGBColor(0, 0, 0)
style_heading1.paragraph_format.space_before = Pt(16)
style_heading1.paragraph_format.space_after = Pt(8)
style_heading1.paragraph_format.page_break_before = True

# Modifier les paragraphes existants
for i, para in enumerate(doc.paragraphs):
    if i == 6:  # "OFFRE TECHNIQUE"
        para.text = "OFFRE TECHNIQUE"
        para.alignment = WD_ALIGN_PARAGRAPH.CENTER
        for run in para.runs:
            run.font.size = Pt(18)
            run.font.bold = True
            run.font.name = 'Times New Roman'
    elif i == 7:  # "LOL ICI" -> titre de l'offre
        para.text = "Formation sur l'intelligence artificielle appliquée aux usages professionnels, à l'analyse de données, aux agents IA et à l'aide à la décision"
        para.alignment = WD_ALIGN_PARAGRAPH.CENTER
        for run in para.runs:
            run.font.size = Pt(14)
            run.font.name = 'Times New Roman'
    elif i == 19:  # "Lieu + date"
        para.text = "Antananarivo, le [DATE]"
        para.alignment = WD_ALIGN_PARAGRAPH.RIGHT
        for run in para.runs:
            run.font.size = Pt(12)
            run.font.name = 'Times New Roman'
    elif i == 20:  # "Entreprise (ex : CARSO)"
        para.text = "[NOM DU CABINET / CENTRE DE FORMATION]"
        para.alignment = WD_ALIGN_PARAGRAPH.CENTER
        for run in para.runs:
            run.font.size = Pt(12)
            run.font.name = 'Times New Roman'
    elif i == 22:  # "Sommaire"
        para.text = "SOMMAIRE"
        para.style = doc.styles['Heading 1']
        para.alignment = WD_ALIGN_PARAGRAPH.CENTER

# Supprimer les paragraphes vides entre sommaire et fin (indices 24 à 64)
paragraphs_to_delete = []
for i in range(24, 65):
    if i < len(doc.paragraphs):
        paragraphs_to_delete.append(doc.paragraphs[i])

# Supprimer les paragraphes dans l'ordre inverse pour ne pas décaler les indices
for para in reversed(paragraphs_to_delete):
    para._element.getparent().remove(para._element)

# Ajouter le contenu complet après le sommaire
new_content = """1. SYNTHÈSE DE LA PROPOSITION

[À COMPLÉTER : Présentation synthétique de l'offre technique du cabinet/centre de formation, démontrant la compréhension des besoins du FID en matière de formation en intelligence artificielle, et résumant l'approche proposée pour les deux vagues de participants (SI/PSE et Directeurs/Chefs de service). Cette section doit permettre au comité d'évaluation de comprendre rapidement la pertinence de la proposition.]

Notre proposition répond aux exigences des Termes de Référence en proposant :
• Une méthodologie pratique, participative et adaptée au niveau de chaque vague
• Un programme détaillé distinguant clairement la vague SI/PSE (4 jours) de la vague Directeurs/Chefs de service (3 jours)
• Des exercices guidés, des ateliers de groupe et des productions directement exploitables
• Des livrables complets : supports de formation, exercices pratiques, note de bonnes pratiques, rapport final
• Un formateur qualifié, disposant d'au moins 5 ans d'expérience dans le domaine de l'IA et de la transformation digitale

2. COMPRÉHENSION DU BESOIN ET DU CONTEXTE

2.1 Contexte et justification

Le Fonds d'Intervention pour le Développement (FID) accorde une importance particulière à la modernisation de ses méthodes de travail, à la valorisation des données, à la transformation digitale et au renforcement de la performance opérationnelle. Dans ce contexte, l'intelligence artificielle constitue un levier pertinent aussi bien pour les fonctions techniques et de planification-suivi que pour les fonctions d'encadrement, de coordination et de décision.

L'intelligence artificielle peut être mobilisée pour traiter et structurer des données, produire des synthèses, analyser des tendances, identifier des écarts, assister la rédaction de notes et rapports, préparer des présentations, automatiser certains processus et mettre en place des agents IA. Elle peut également appuyer les responsables dans la préparation des réunions, l'organisation de l'information et l'aide à la décision.

Cependant, l'utilisation de l'IA doit être encadrée. Les risques liés à la confidentialité des données, à la qualité des résultats générés, aux biais, aux hallucinations, à la dépendance aux outils externes, à la sécurité de l'information et à la responsabilité humaine nécessitent une formation structurée, pratique et adaptée au contexte institutionnel.

2.2 Enjeux de la mission

La mission vise à renforcer les capacités des différentes catégories de personnel du FID dans l'utilisation responsable, sécurisée et opérationnelle de l'intelligence artificielle. Les enjeux principaux sont :

• Enjeu technique : Former les équipes SI à l'automatisation intelligente, aux agents IA, à l'analyse de données et à l'intégration technique des solutions IA
• Enjeu opérationnel : Permettre aux équipes PSE d'utiliser l'IA pour la planification, le suivi des indicateurs, l'analyse des écarts, le reporting et l'aide à la décision
• Enjeu managérial : Initier les Directeurs, Chefs de service et Responsables aux usages professionnels et managériaux de l'IA
• Enjeu de gouvernance : Sensibiliser l'ensemble des participants aux règles de confidentialité, de sécurité, de gouvernance, de vérification des résultats et de validation humaine.

2.3 Analyse des attentes du FID

Le FID attend du prestataire qu'il produise des compétences pratiques, des cas d'usage exploitables, des outils de travail et des recommandations opérationnelles. Le contenu et le niveau d'approfondissement doivent être adaptés à chaque vague, tout en garantissant un socle commun sur les usages, les limites, les risques, la confidentialité, la sécurité et la validation humaine.

3. OBJECTIFS ET RÉSULTATS ATTENDUS

3.1 Objectif général

Renforcer les capacités des différentes catégories de personnel du FID dans l'utilisation responsable, sécurisée et opérationnelle de l'intelligence artificielle, en adaptant les contenus aux besoins des équipes SI/PSE et aux usages professionnels et managériaux des Directeurs, Chefs de service et Responsables.

3.2 Objectifs spécifiques

1. Développer une compréhension commune des concepts, opportunités, limites et risques liés à l'intelligence artificielle
2. Former les participants à la formulation de prompts clairs, structurés et adaptés à leurs besoins professionnels
3. Renforcer les capacités des équipes SI et PSE dans l'utilisation de l'IA pour l'analyse de données, la structuration et l'interprétation de l'information, le reporting, les agents IA, l'automatisation intelligente et l'aide à la décision
4. Appuyer les équipes PSE dans l'utilisation de l'IA pour la planification, le suivi des indicateurs, l'analyse des écarts, l'automatisation de certains travaux de reporting et l'aide à la décision
5. Appuyer les équipes SI dans la structuration, la sécurisation, l'intégration, l'exploitation et l'automatisation technique des solutions IA
6. Initier les Directeurs, Chefs de service et certains Responsables à l'utilisation générale de l'IA dans leurs activités professionnelles et managériales
7. Permettre aux Directeurs, Chefs de service et Responsables d'utiliser l'IA pour la rédaction et l'amélioration de documents, la synthèse, l'analyse d'informations, la préparation de réunions et de présentations, l'organisation du travail et la préparation d'éléments d'aide à la décision
8. Identifier, pour chaque vague, des cas d'usage IA réalistes et applicables au contexte du FID
9. Sensibiliser l'ensemble des participants aux règles de confidentialité, de sécurité, de gouvernance, de vérification des résultats et de validation humaine

3.3 Résultats attendus

• Les participants des deux vagues disposent d'une compréhension commune des usages, risques, limites et bonnes pratiques de l'IA
• Les équipes SI/développeurs disposent de pistes concrètes pour automatiser certaines tâches techniques, documentaires, d'intégration, de support et d'exploitation du système d'information
• Les équipes PSE disposent de pistes concrètes pour automatiser certaines tâches de planification, suivi des indicateurs, analyse des écarts, reporting, tableaux de bord et notes d'aide à la décision
• Les Directeurs, Chefs de service et Responsables savent utiliser un outil d'IA générative pour des tâches professionnelles courantes
• Les Directeurs, Chefs de service et Responsables savent formuler et améliorer des prompts simples et efficaces
• Des cas d'usage IA prioritaires, communs et spécialisés, sont identifiés, testés et priorisés
• L'ensemble des participants connaît les principes de vérification des résultats ainsi que les règles minimales de confidentialité, de sécurité et de validation humaine
• Des supports pratiques et modèles de prompts adaptés aux différentes catégories de participants sont remis au FID

4. APPROCHE TECHNIQUE ET SOLUTION PROPOSÉE

4.1 Structure globale de la prestation

Notre approche repose sur une structuration en deux vagues complémentaires, avec un socle commun de bonnes pratiques mais des niveaux d'approfondissement différents, conformément aux exigences des Termes de Référence.

Vague 1 – Équipes SI et PSE : 4 jours, approche approfondie avec tronc commun (3 jours) et ateliers spécialisés (1 jour)
Vague 2 – Directeurs, Chefs de service et Responsables : 3 jours, initiation générale, progressive et pratique

4.2 Vague 1 – Équipes SI et PSE

Jours 1 à 3 : Tronc commun SI/PSE
• Jour 1 : Fondamentaux, usages, risques et bonnes pratiques de l'IA
• Jour 2 : IA appliquée à l'analyse de données
• Jour 3 : Agents IA et automatisation intelligente

Jour 4 : Ateliers spécialisés
• Équipes SI : Automatisation de tâches techniques, flux de données, scripts, agents IA intégrés au système d'information, sécurité et documentation technique
• Équipes PSE : Automatisation de tâches liées à la planification, au suivi des indicateurs, à l'analyse des écarts, à la production de rapports, aux tableaux de bord et aux notes d'aide à la décision

4.3 Vague 2 – Directeurs, Chefs de service et Responsables

Jour 1 : Initiation à l'IA générative, prise en main des outils et formulation des prompts
Jour 2 : Usages professionnels et managériaux : rédaction, synthèse, analyse de documents, préparation de réunions et de présentations
Jour 3 : Aide à la décision, ateliers pratiques, identification de cas d'usage pertinents, règles de confidentialité, sécurité et validation humaine

5. MÉTHODOLOGIE DE MISE EN ŒUVRE

5.1 Principes pédagogiques

• Approche pratique et participative : Privilégier les démonstrations, les exercices guidés, les ateliers de groupe et les productions directement exploitables
• Adaptation au niveau de chaque vague : Adapter les contenus, les exercices et le niveau d'approfondissement aux responsabilités des participants
• Données fictives ou validées : Utiliser des données fictives, anonymisées ou préalablement validées par le FID
• Cas d'usage réalistes : Animer des travaux pratiques permettant d'identifier des cas d'usage réalistes, testables et adaptés à chaque vague
• Évaluation des acquis : Prévoir une évaluation des acquis pour chacune des deux vagues
• Langue de travail : Français et Malgasy obligatoire

5.2 Déroulement de la Vague 1 (SI/PSE)

Phase 1 – Fondamentaux communs (Jours 1-3)
• Présentation des concepts fondamentaux de l'IA générative et de ses applications professionnelles
• Formation à la formulation de prompts clairs et structurés
• Initiation à l'analyse de données assistée par IA
• Présentation des agents IA et de l'automatisation intelligente
• Sensibilisation aux risques, bonnes pratiques et gouvernance

Phase 2 – Ateliers spécialisés (Jour 4)
• Ateliers pratiques séparés pour les équipes SI et PSE
• Équipes SI : automatisation de tâches techniques, scripts, agents IA, sécurité
• Équipes PSE : automatisation de reporting, tableaux de bord, analyse d'écarts, aide à la décision
• Restitution et priorisation des cas d'usage identifiés

5.3 Déroulement de la Vague 2 (Directeurs/Chefs de service)

Phase 1 – Initiation (Jour 1)
• Compréhension de l'IA générative et de ses principes de base
• Prise en main des outils d'IA
• Formulation de prompts simples et efficaces

Phase 2 – Usages professionnels (Jour 2)
• Rédaction et amélioration de documents
• Synthèse et analyse de documents
• Préparation de réunions et de présentations
• Organisation de l'information

Phase 3 – Aide à la décision (Jour 3)
• Ateliers pratiques sur des cas professionnels concrets
• Identification des cas d'usage prioritaires
• Règles de confidentialité, sécurité et vérification des résultats
• Validation humaine et responsabilité

5.4 Évaluation des acquis

Une évaluation des acquis sera réalisée pour chacune des deux vagues, incluant des exercices pratiques évalués, un questionnaire de satisfaction et d'auto-évaluation, et une restitution des résultats au FID dans le rapport final.

6. PROGRAMME DÉTAILLÉ

Un programme détaillé pour chacune des deux vagues sera fourni dans la note méthodologique (Livrable 1). Ce programme précisera le contenu horaire de chaque journée, les exercices pratiques et ateliers prévus, les outils et plateformes d'IA qui seront utilisés, les données fictives ou validées par le FID qui seront mobilisées, et les modalités d'évaluation des acquis.

[À COMPLÉTER : Le programme détaillé sera élaboré en concertation avec le FID et intégré à la note méthodologique.]

7. ORGANISATION ET ÉQUIPE DE RÉALISATION

7.1 Profil du cabinet / centre de formation

[À COMPLÉTER : Présentation du cabinet ou centre de formation soumissionnaire, incluant :
• Identité et statut juridique du cabinet/centre de formation
• Existence légale d'au moins trois (03) ans à la date de soumission de l'offre
• Avoir animé au moins six (6) formations professionnelles au cours des trois (03) dernières années
• Avoir formé et fait certifier ou attester au moins vingt (20) personnes au cours des trois (03) dernières années
• Avoir animé au moins deux (02) formations en intelligence artificielle, IA générative, automatisation, transformation digitale ou thématiques similaires
• Avoir réalisé au moins deux (02) missions de formation ou d'accompagnement auprès d'institutions publiques, parapubliques, projets de développement, ONG, entreprises ou organisations similaires
• Expériences et références professionnelles pertinentes]

7.2 Profil du ou des formateurs

Le cabinet proposera un ou plusieurs formateurs disposant d'un profil adapté aux objectifs de la formation. Les qualifications et expériences minimales requises sont :

• Formation : Au moins équivalent à Master 2 (BACC +5) en informatique ou filière similaire
• Certification : Certificat ou attestation de formation dans l'un des domaines suivants : intelligence artificielle, transformation digitale, informatique, systèmes d'information, analyse de données, développement informatique ou domaine équivalent
• Expérience professionnelle : Au moins cinq (05) ans d'expérience dans la digitalisation, la transformation digitale, les systèmes d'information, l'informatique ou l'accompagnement numérique des organisations
• Expérience en formation : Avoir déjà animé au moins quinze (15) formations portant sur des outils informatiques, outils numériques, logiciels professionnels, analyse de données, bureautique avancée, systèmes d'information ou thématiques similaires
• Spécialisation IA : Parmi ces quinze (15) formations, avoir animé au moins deux (02) formations portant spécifiquement sur l'intelligence artificielle générative, les agents d'IA, l'automatisation ou les usages professionnels de l'IA
• Capacité pédagogique : Bonne capacité pédagogique et expérience dans l'animation de formations destinées à des publics mixtes (profils techniques et profils métiers)
• Langues : Français et Malgasy obligatoire

7.3 Organisation pédagogique

[À COMPLÉTER : Description de l'organisation pédagogique proposée, incluant les modalités de gestion des exercices pratiques, l'encadrement des participants et les modalités de remise des livrables.]

8. PLANNING ET JALONS

La durée indicative totale de la prestation est de sept (07) jours ouvrables, répartis en deux vagues :

Vague 1 (SI/PSE) : 4 jours ouvrables – Formation des équipes SI et PSE – Supports de formation, exercices pratiques, évaluation des acquis
Vague 2 (Directeurs/Chefs de service) : 3 jours ouvrables – Formation des Directeurs, Chefs de service et Responsables – Supports de formation, exercices pratiques, évaluation des acquis

[À COMPLÉTER : Les dates exactes de formation seront précisées en concertation avec le FID. Le calendrier détaillé sera intégré à la note méthodologique.]

9. LIVRABLES

Le prestataire devra remettre les livrables suivants :

1. Note méthodologique : Approche pédagogique, programme détaillé pour chacune des deux vagues, outils proposés et organisation des ateliers (Avant le début de la formation)
2. Supports de formation : Supports de formation en version électronique, adaptés à chaque vague (Pendant la formation)
3. Exercices pratiques : Exercices pratiques, modèles, fiches d'automatisation, modèles de prompts et études de cas utilisés pendant la formation (Pendant la formation)
4. Note de bonnes pratiques : Note sur l'utilisation responsable, sécurisée et éthique de l'IA (Après la formation)
5. Résultats de l'évaluation des acquis : Résultats de l'évaluation des acquis pour chacune des deux vagues (Après chaque vague)
6. Rapport final de formation : Présentant le déroulement des deux vagues, les résultats, les difficultés rencontrées, les recommandations et les perspectives (Après la dernière vague)

10. MOYENS ET LOGISTIQUE

10.1 Matériel et équipements

[À COMPLÉTER : Préciser les matériels nécessaires au bon déroulement de la formation, notamment : salle de formation adaptée, connexion Internet, vidéoprojecteur, ordinateurs, supports pédagogiques, outils à utiliser, accès éventuels à des plateformes d'intelligence artificielle, conditions techniques minimales requises.]

10.2 Indemnités des formateurs

Conformément aux Termes de Référence, les déplacements des formateurs depuis Antananarivo jusqu'au lieu de la formation seront pris en charge par le FID. En revanche, les autres charges liées aux formateurs, notamment les indemnités d'hébergement, de restauration, de séjour ou toute autre indemnité éventuelle, seront entièrement à la charge du prestataire.

[À COMPLÉTER : Préciser les indemnités d'hébergement, de restauration et de séjour proposées pour le ou les formateurs.]

10.3 Modalités d'encadrement

[À COMPLÉTER : Préciser les modalités d'encadrement des participants, de gestion des exercices pratiques et de remise des livrables attendus pour chacune des deux vagues.]

11. QUALITÉ, SUIVI ET GESTION DES RISQUES

11.1 Assurance qualité

Notre démarche qualité repose sur :
• Une préparation minutieuse de chaque session de formation, en concertation avec le FID
• Une adaptation continue des contenus en fonction du niveau et des retours des participants
• Une évaluation formative en cours de formation (exercices pratiques, questions-réponses)
• Une évaluation sommative à l'issue de chaque vague
• Un contrôle qualité des livrables avant remise au FID

11.2 Suivi et reporting

Un point de suivi sera organisé avec le FID à chaque étape clé de la prestation. Le rapport final inclura un bilan détaillé de la formation, les résultats obtenus et les recommandations pour la suite.

11.3 Analyse des risques et mesures d'atténuation

• Risque : Niveau hétérogène des participants – Impact : Difficulté d'adaptation du contenu – Mesure : Pré-évaluation des niveaux des participants et adaptation des exercices en conséquence
• Risque : Problèmes techniques (connexion, outils IA) – Impact : Interruption de la formation – Mesure : Préparation de contenus hors ligne, outils de secours, test préalable des accès
• Risque : Indisponibilité du lieu de formation – Impact : Report de la formation – Mesure : Coordination anticipée avec le FID pour confirmer le lieu et disposer d'un plan B
• Risque : Résistance au changement – Impact : Adoption limitée de l'IA – Mesure : Approche progressive, démonstrations concrètes, valorisation des cas d'usage pertinents
• Risque : Données sensibles du FID – Impact : Risque de confidentialité – Mesure : Utilisation exclusive de données fictives ou anonymisées, signature d'accords de confidentialité si nécessaire

12. CONCLUSION ET ENGAGEMENT

[À COMPLÉTER : Conclusion personnalisée présentant la pertinence de la proposition, la capacité du cabinet/centre de formation à répondre aux objectifs du FID, les principaux avantages de l'approche proposée, et l'engagement envers la qualité, les délais et les résultats.]

Notre cabinet/centre de formation s'engage à :
• Respecter les exigences des Termes de Référence et les attentes du FID
• Produire une formation pratique, participative et adaptée à chaque vague de participants
• Remettre l'ensemble des livrables dans les délais convenus
• Garantir la qualité pédagogique et technique de la prestation
• Être disponible pour toute information complémentaire ou clarification

Fait à [Lieu], le [Date]

Le soumissionnaire
[Nom du représentant]
[Fonction]
[Nom du cabinet / centre de formation]
[Signature]
"""

# Insérer le contenu après le sommaire
for para in doc.paragraphs:
    if para.text == "SOMMAIRE":
        # Insérer après le sommaire
        new_para = doc.add_paragraph()
        new_para.text = new_content
        new_para.style = doc.styles['Normal']
        new_para.runs[0].font.name = 'Times New Roman'
        new_para.runs[0].font.size = Pt(14)
        break

# Sauvegarder
doc.save(OUTPUT_PATH)
print(f"Document généré : {OUTPUT_PATH}")
print(f"Taille : {OUTPUT_PATH.stat().st_size} octets")
