# Patch openpyxl pour contourner numpy/ctys dans la sandbox
import openpyxl.compat.numbers
openpyxl.compat.numbers.NUMPY = False

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

wb = Workbook()

header_fmt = Font(name='Calibri', bold=True, color='FFFFFF', size=10)
header_fill = PatternFill(start_color='1F4E79', end_color='1F4E79', fill_type='solid')
total_fmt = Font(name='Calibri', bold=True, size=10)
total_fill = PatternFill(start_color='F2F6FC', end_color='F2F6FC', fill_type='solid')
bold_fmt = Font(name='Calibri', bold=True, size=10)
normal_fmt = Font(name='Calibri', size=10)
title_fmt = Font(name='Calibri', bold=True, size=14, color='1F4E79')
subtitle_fmt = Font(name='Calibri', bold=True, size=11, color='1F4E79')
ttc_fmt = Font(name='Calibri', bold=True, size=11, color='FFFFFF')
ttc_fill = PatternFill(start_color='1F4E79', end_color='1F4E79', fill_type='solid')
thin_border = Border(left=Side(style='thin', color='B4C6E7'), right=Side(style='thin', color='B4C6E7'), top=Side(style='thin', color='B4C6E7'), bottom=Side(style='thin', color='B4C6E7'))
num_fmt = '#,##0'
center_a = Alignment(horizontal='center', vertical='center')
right_a = Alignment(horizontal='right', vertical='center')
wrap_a = Alignment(wrap_text=True, vertical='center')

def style_cell(cell, fmt=None, fill=None, border=True, align=None):
    if fmt: cell.font = fmt
    if fill: cell.fill = fill
    if border: cell.border = thin_border
    if align: cell.alignment = align

def add_table(ws, title, subtitle, headers, data_rows, total_label, total_formula):
    ws.merge_cells('A1:F1')
    ws['A1'].value = title
    ws['A1'].font = title_fmt
    ws.merge_cells('A2:F2')
    ws['A2'].value = subtitle
    ws['A2'].font = subtitle_fmt
    for c, h in enumerate(headers, 1):
        cell = ws.cell(row=4, column=c, value=h)
        style_cell(cell, header_fmt, header_fill, border=True, align=center_a)
    for r, row_data in enumerate(data_rows):
        for c, val in enumerate(row_data):
            cell = ws.cell(row=4+r, column=c)
            if isinstance(val, tuple):
                cell.value = val[0]
                cell.font = val[1]
                cell.number_format = num_fmt
                cell.border = thin_border
                cell.alignment = Alignment(horizontal='center', vertical='center')
            elif isinstance(val, str) and val.startswith('='):
                cell.value = val
                cell.font = normal_fmt
                cell.number_format = num_fmt
                cell.border = thin_border
                cell.alignment = Alignment(horizontal='center', vertical='center')
            else:
                cell.value = val
                cell.font = normal_fmt
                cell.border = thin_border
                cell.alignment = Alignment(horizontal='center', vertical='center')
    tr = 4 + len(data_rows)
    ws.merge_cells(f'B{tr}:E{tr}')
    ws.cell(row=tr, column=2, value=total_label).font = total_fmt
    ws.cell(row=tr, column=2).alignment = right_a
    ws.cell(row=tr, column=2).fill = total_fill
    ws.cell(row=tr, column=2).border = thin_border
    cell = ws.cell(row=tr, column=6, value=total_formula)
    cell.font = total_fmt
    cell.number_format = num_fmt
    cell.fill = total_fill
    cell.border = thin_border
    cell.alignment = Alignment(horizontal='center', vertical='center')

headers = ["N°", "Poste", "Unité", "Qté", "Coût unitaire (MGA)", "Coût total (MGA)"]

# PAGE 1: COUVERTURE
ws = wb.active
ws.title = "Couverture"
ws.merge_cells('A1:F1')
ws['A1'].value = "OFFRE FINANCIÈRE"
ws['A1'].font = Font(name='Calibri', bold=True, size=28, color='1F4E79')
ws['A1'].alignment = Alignment(horizontal='center', vertical='center')
ws.merge_cells('A2:F2')
ws['A2'].value = "Formation sur l'intelligence artificielle appliquée aux usages professionnels, à l'analyse de données, aux agents IA et à l'aide à la décision"
ws['A2'].font = Font(name='Calibri', size=14, color='1F4E79')
ws['A2'].alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
ws.row_dimensions[1].height = 50
ws.merge_cells('A4:F4')
ws['A4'].value = "Appel : N°59/AMI/FID/FSR/SQC_CAB_FORM_IA"
ws['A4'].font = Font(name='Calibri', size=12, bold=True)
ws['A4'].alignment = Alignment(horizontal='center')
ws.merge_cells('A5:F5')
ws['A5'].value = "Lot n°01 — Fonds d'Intervention pour le Développement (FID)"
ws['A5'].font = Font(name='Calibri', size=12)
ws['A5'].alignment = Alignment(horizontal='center')
ws.merge_cells('A6:F6')
ws['A6'].value = "Zone : Antananarivo (Direction Générale)"
ws['A6'].font = Font(name='Calibri', size=12)
ws['A6'].alignment = Alignment(horizontal='center')
ws.merge_cells('A7:F7')
ws['A7'].value = "Date : Novembre 2026"
ws['A7'].font = Font(name='Calibri', size=12)
ws['A7'].alignment = Alignment(horizontal='center')
for r in [3,4,5,6]:
    ws.row_dimensions[r].height = 25
for i in range(1,7):
    ws.column_dimensions[chr(64+i)].width = 18

# PAGE 2: SOMMAIRE & HYPOTHÈSES
ws = wb.create_sheet("Sommaire & Hypothèses")
ws.merge_cells('A1:F1')
ws['A1'].value = "Sommaire"
ws['A1'].font = title_fmt
ws.merge_cells('A3:F3')
ws['A3'].value = "La présente offre financière détaille les coûts prévisionnels de la mission de formation sur l'intelligence artificielle appliquée aux usages professionnels, à l'analyse de données, aux agents IA et à l'aide à la décision, destinée au personnel du Fonds d'Intervention pour le Développement (FID) à Antananarivo."
ws['A3'].font = normal_fmt
ws['A3'].alignment = wrap_a
ws.row_dimensions[2].height = 40
ws.merge_cells('A5:F5')
ws['A5'].value = "1. Hypothèses financières"
ws['A5'].font = title_fmt
hypotheses = [
    ("Format de la formation", "Présentielle, au siège du FID à Antananarivo (Direction Générale)"),
    ("Participants", "20 participants"),
    ("Durée", "5 jours de formation intensive"),
    ("Fréquence", "Formation intensive en continu"),
    ("Devise", "Ariary malgache (MGA)"),
    ("Taux de TVA", "20% (taux standard Madagascar)"),
    ("Validité de l'offre", "90 jours à compter de la date de soumission"),
    ("Périmètre", "Honoraires, préparation pédagogique, supports, logistique, frais de fonctionnement"),
    ("Modalités de paiement", "30% à la signature, 40% à mi-parcours, 30% à la remise du rapport final"),
]
for i, (label, value) in enumerate(hypotheses, 6):
    ws.cell(row=i, column=1, value=label).font = bold_fmt
    ws.cell(row=i, column=2, value=value).font = normal_fmt
    ws.merge_cells(start_row=i, start_column=2, end_row=i, end_column=6)

# PAGE 3: TABLEAU 1 - HONORAIRES
ws = wb.create_sheet("2.1 Honoraires")
data1 = [
    ("1", "Expert en Intelligence Artificielle appliquée : formation IA, agents IA, aide à la décision", "Jour", 5, 1500000, '=D5*E5'),
    ("2", "Expert en Analyse de données : outils et méthodes (Excel avancé, Power BI, Python/R)", "Jour", 5, 1200000, '=D6*E6'),
    ("3", "Formateur pratique / accompagnateur : séances de travaux dirigés et exercices", "Jour", 5, 800000, '=D7*E7'),
]
add_table(ws, "Tableau 1 : Honoraires des formateurs et experts", "2.1 Honoraires des formateurs et experts", headers, data1, "Sous-total Honoraires", "=SUM(F5:F7)")
for i in range(1,7):
    ws.column_dimensions[chr(64+i)].width = [10,55,15,10,22,22][i-1]

# PAGE 4: TABLEAU 2 - PRÉPARATION
ws = wb.create_sheet("2.2 Préparation")
data2 = [
    ("1", "Conception du programme et du contenu pédagogique (adaptation au contexte FID)", "Forfait", 1, 3000000, 3000000),
    ("2", "Élaboration des supports de présentation (diapositives, guides participants)", "Forfait", 1, 2500000, 2500000),
    ("3", "Création des exercices pratiques, cas concrets et études de cas", "Forfait", 1, 2000000, 2000000),
    ("4", "Préparation des environnements de travail (postes, logiciels, données d'exemple)", "Forfait", 1, 1500000, 1500000),
]
add_table(ws, "Tableau 2 : Préparation pédagogique et développement des supports", "2.2 Préparation pédagogique et développement des supports", headers, data2, "Sous-total Préparation pédagogique", "=SUM(F5:F8)")
for i in range(1,7):
    ws.column_dimensions[chr(64+i)].width = [10,55,15,10,22,22][i-1]

# PAGE 5: TABLEAU 3 - MATÉRIEL
ws = wb.create_sheet("2.3 Matériel")
data3 = [
    ("1", "Licences logicielles temporaires (outils IA, analyse de données, agents IA)", "Lot", 1, 2000000, 2000000),
    ("2", "Fournitures pédagogiques (cahiers, stylos, marqueurs, paperboard)", "Lot", 1, 500000, 500000),
    ("3", "Impression des supports de formation (guides participants, exercices)", "Exemplaire", 20, 15000, '=D8*E8'),
    ("4", "Clés USB ou supports numériques de distribution des contenus", "Lot", 1, 400000, 400000),
]
add_table(ws, "Tableau 3 : Matériel, logiciels et fournitures", "2.3 Matériel, logiciels et fournitures", headers, data3, "Sous-total Matériel et fournitures", "=SUM(F5:F8)")
for i in range(1,7):
    ws.column_dimensions[chr(64+i)].width = [10,55,15,10,22,22][i-1]

# PAGE 6: TABLEAU 4 - LOGISTIQUE
ws = wb.create_sheet("2.4 Logistique")
data4 = [
    ("1", "Location salle de formation (si non fourni par le FID) : vidéoprojecteur, écran, son", "Jour", 5, 300000, '=D5*E5'),
    ("2", "Restauration des participants et formateurs (déjeuner, pauses)", "Participant/jour", 100, 25000, '=D6*E6'),
    ("3", "Boissons et en-cas (café, thé, eau tout au long de la formation)", "Jour", 5, 200000, '=D7*E7'),
    ("4", "Connexion Internet haut débit dédiée (si non fournie par le FID)", "Jour", 5, 100000, '=D8*E8'),
    ("5", "Transport des formateurs (déplacements Antananarivo)", "Forfait", 1, 750000, 750000),
]
add_table(ws, "Tableau 4 : Logistique et fonctionnement", "2.4 Logistique et fonctionnement", headers, data4, "Sous-total Logistique", "=SUM(F5:F9)")
for i in range(1,7):
    ws.column_dimensions[chr(64+i)].width = [10,55,15,10,22,22][i-1]

# PAGE 7: TABLEAU 5 - ÉVALUATION
ws = wb.create_sheet("2.5 Évaluation")
data5 = [
    ("1", "Élaboration de l'outil d'évaluation (questionnaire de satisfaction, test de connaissances)", "Forfait", 1, 800000, 800000),
    ("2", "Rapport de synthèse de la formation (compte rendu, résultats d'évaluation, recommandations)", "Forfait", 1, 1200000, 1200000),
    ("3", "Suivi post-formation (évaluation à 30 jours : optionnel)", "Forfait", 1, 1000000, 1000000),
]
add_table(ws, "Tableau 5 : Évaluation et reporting", "2.5 Évaluation et reporting", headers, data5, "Sous-total Évaluation et reporting", "=SUM(F5:F7)")
for i in range(1,7):
    ws.column_dimensions[chr(64+i)].width = [10,55,15,10,22,22][i-1]

# PAGE 8: TABLEAU 6 - RÉCAPITULATIF
ws = wb.create_sheet("3. Récapitulatif")
ws.merge_cells('A1:C1')
ws['A1'].value = "Tableau 6 : Récapitulatif du budget"
ws['A1'].font = title_fmt
ws.merge_cells('A2:C2')
ws['A2'].value = "3. Tableau récapitulatif du budget"
ws['A2'].font = subtitle_fmt
for c, h in enumerate(["N°", "Poste de coût", "Montant (MGA)"], 1):
    cell = ws.cell(row=4, column=c, value=h)
    style_cell(cell, header_fmt, header_fill, border=True, align=center_a)
recap = [
    ("1", "Honoraires des formateurs et experts", "='2.1 Honoraires'!F8"),
    ("2", "Préparation pédagogique et développement des supports", "='2.2 Préparation'!F9"),
    ("3", "Matériel, logiciels et fournitures", "='2.3 Matériel'!F9"),
    ("4", "Logistique et fonctionnement", "='2.4 Logistique'!F10"),
    ("5", "Évaluation et reporting", "='2.5 Évaluation'!F8"),
]
for r, (num, poste, form) in enumerate(recap):
    ws.cell(row=5+r, column=1, value=num).font = normal_fmt
    ws.cell(row=5+r, column=1).alignment = center_a
    ws.cell(row=5+r, column=1).border = thin_border
    ws.cell(row=5+r, column=2, value=poste).font = normal_fmt
    ws.cell(row=5+r, column=2).border = thin_border
    cell = ws.cell(row=5+r, column=3, value=form)
    cell.font = normal_fmt
    cell.number_format = num_fmt
    cell.border = thin_border
    cell.alignment = center_a

# Sous-total HT
ws.merge_cells('B10:C10')
ws.cell(row=10, column=2, value="Sous-total HT").font = total_fmt
ws.cell(row=10, column=2).alignment = right_a
ws.cell(row=10, column=2).fill = total_fill
ws.cell(row=10, column=2).border = thin_border
cell = ws.cell(row=10, column=3, value="=SUM(C5:C9)")
cell.font = total_fmt
cell.number_format = num_fmt
cell.fill = total_fill
cell.border = thin_border
cell.alignment = center_a

# TVA
ws.cell(row=11, column=2, value="TVA (20%)").font = normal_fmt
ws.cell(row=11, column=2).border = thin_border
cell = ws.cell(row=11, column=3, value="=C10*0.20")
cell.font = normal_fmt
cell.number_format = num_fmt
cell.border = thin_border
cell.alignment = center_a

# Autres taxes
ws.cell(row=12, column=2, value="Autres taxes et contributions (non précisé)").font = normal_fmt
ws.cell(row=12, column=2).border = thin_border
ws.cell(row=12, column=3, value=0).font = normal_fmt
ws.cell(row=12, column=3).number_format = num_fmt
ws.cell(row=12, column=3).border = thin_border
ws.cell(row=12, column=3).alignment = center_a

# TOTAL TTC
ws.merge_cells('B13:C13')
ws.cell(row=13, column=2, value="TOTAL TTC").font = ttc_fmt
ws.cell(row=13, column=2).alignment = right_a
ws.cell(row=13, column=2).fill = ttc_fill
ws.cell(row=13, column=2).border = thin_border
cell = ws.cell(row=13, column=3, value="=SUM(C10:C12)")
cell.font = ttc_fmt
cell.number_format = num_fmt
cell.fill = ttc_fill
cell.border = thin_border
cell.alignment = center_a
for i in range(1,4):
    ws.column_dimensions[chr(64+i)].width = [10,55,22][i-1]

# PAGE 9: CONDITIONS FINANCIÈRES
ws = wb.create_sheet("4. Conditions")
ws.merge_cells('A1:F1')
ws['A1'].value = "4. Conditions financières"
ws['A1'].font = title_fmt
conditions = [
    ("Devise", "Tous les montants sont exprimés en Ariary malgache (MGA)."),
    ("Modalités de paiement", "30% à la signature, 40% à mi-parcours, 30% à la remise du rapport final."),
    ("Validité de l'offre", "90 jours à compter de la date de soumission."),
    ("Taxes", "Les montants TTC incluent la TVA au taux de 20% et les autres contributions fiscales applicables à Madagascar."),
    ("Exclusions", "Ne sont pas inclus dans le présent budget :\n- Les frais de déplacement des participants (pris en charge par le FID)\n- Les coûts d'hébergement pour les formateurs externes (non applicable)\n- Les achats de matériel informatique permanent (postes PC, serveurs)"),
]
for i, (label, value) in enumerate(conditions, 3):
    ws.cell(row=i, column=1, value=label).font = bold_fmt
    ws.cell(row=i, column=2, value=value).font = normal_fmt
    ws.cell(row=i, column=2).alignment = wrap_a
    ws.merge_cells(start_row=i, start_column=2, end_row=i, end_column=6)
    ws.row_dimensions[i].height = 30
for i in range(1,7):
    ws.column_dimensions[chr(64+i)].width = [25,80,15,10,22,22][i-1]

wb.save('offre_financiere_fid_ia.xlsx')
print("Excel généré avec succès : offre_financiere_fid_ia.xlsx")
print("Formules Tableau 1:", ws['F5'].value, ws['F6'].value, ws['F7'].value, ws['F8'].value)
print("Formules Tableau 6:", ws['C5'].value, ws['C10'].value, ws['C11'].value, ws['C13'].value)
print("Sheets:", wb.sheetnames)
