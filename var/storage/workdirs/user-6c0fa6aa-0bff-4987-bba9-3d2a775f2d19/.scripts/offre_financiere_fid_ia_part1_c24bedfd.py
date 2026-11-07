from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

wb = Workbook()

header_font = Font(name='Calibri', bold=True, color='FFFFFF', size=10)
header_fill = PatternFill(start_color='1F4E79', end_color='1F4E79', fill_type='solid')
total_font = Font(name='Calibri', bold=True, size=10)
total_fill = PatternFill(start_color='F2F6FC', end_color='F2F6FC', fill_type='solid')
bold_font = Font(name='Calibri', bold=True, size=10)
normal_font = Font(name='Calibri', size=10)
title_font = Font(name='Calibri', bold=True, size=14, color='1F4E79')
subtitle_font = Font(name='Calibri', bold=True, size=11, color='1F4E79')
thin_border = Border(left=Side(style='thin', color='B4C6E7'), right=Side(style='thin', color='B4C6E7'), top=Side(style='thin', color='B4C6E7'), bottom=Side(style='thin', color='B4C6E7'))
num_format = '#,##0'

def style_header_row(ws, row, max_col):
    for col in range(1, max_col + 1):
        cell = ws.cell(row=row, column=col)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
        cell.border = thin_border

def style_data_row(ws, row, max_col, is_total=False):
    for col in range(1, max_col + 1):
        cell = ws.cell(row=row, column=col)
        cell.border = thin_border
        if is_total:
            cell.font = total_font
            cell.fill = total_fill
        else:
            cell.font = normal_font
        cell.alignment = Alignment(vertical='center')

def set_col_widths(ws, widths):
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w

headers = ["N°", "Poste", "Unité", "Qté", "Coût unitaire (MGA)", "Coût total (MGA)"]

# PAGE 1: COUVERTURE
ws_cover = wb.active
ws_cover.title = "Couverture"
ws_cover.merge_cells('A1:F1')
c = ws_cover['A1']
c.value = "OFFRE FINANCIÈRE"
c.font = Font(name='Calibri', bold=True, size=28, color='1F4E79')
c.alignment = Alignment(horizontal='center', vertical='center')

ws_cover.merge_cells('A2:F2')
c = ws_cover['A2']
c.value = "Formation sur l'intelligence artificielle appliquée aux usages professionnels, à l'analyse de données, aux agents IA et à l'aide à la décision"
c.font = Font(name='Calibri', size=14, color='1F4E79')
c.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
ws_cover.row_dimensions[2].height = 40

ws_cover.merge_cells('A4:F4')
ws_cover['A4'].value = "Appel : N°59/AMI/FID/FSR/SQC_CAB_FORM_IA"
ws_cover['A4'].font = Font(name='Calibri', size=12, bold=True)
ws_cover['A4'].alignment = Alignment(horizontal='center')

ws_cover.merge_cells('A5:F5')
ws_cover['A5'].value = "Lot n°01 — Fonds d'Intervention pour le Développement (FID)"
ws_cover['A5'].font = Font(name='Calibri', size=12)
ws_cover['A5'].alignment = Alignment(horizontal='center')

ws_cover.merge_cells('A6:F6')
ws_cover['A6'].value = "Zone : Antananarivo (Direction Générale)"
ws_cover['A6'].font = Font(name='Calibri', size=12)
ws_cover['A6'].alignment = Alignment(horizontal='center')

ws_cover.merge_cells('A7:F7')
ws_cover['A7'].value = "Date : Novembre 2026"
ws_cover['A7'].font = Font(name='Calibri', size=12)
ws_cover['A7'].alignment = Alignment(horizontal='center')

for r in [4,5,6,7]:
    ws_cover.row_dimensions[r].height = 25
set_col_widths(ws_cover, [10, 45, 15, 10, 22, 22])

# PAGE 2: SOMMAIRE & HYPOTHÈSES
ws2 = wb.create_sheet("Sommaire & Hypothèses")
ws2.merge_cells('A1:F1')
ws2['A1'].value = "Sommaire"
ws2['A1'].font = title_font

ws2.merge_cells('A3:F3')
ws2['A3'].value = "La présente offre financière détaille les coûts prévisionnels de la mission de formation sur l'intelligence artificielle appliquée aux usages professionnels, à l'analyse de données, aux agents IA et à l'aide à la décision, destinée au personnel du Fonds d'Intervention pour le Développement (FID) à Antananarivo."
ws2['A3'].font = normal_font
ws2['A3'].alignment = Alignment(wrap_text=True)
ws2.row_dimensions[3].height = 40

ws2.merge_cells('A5:F5')
ws2['A5'].value = "1. Hypothèses financières"
ws2['A5'].font = title_font

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
for i, (label, value) in enumerate(hypotheses, 7):
    ws2.cell(row=i, column=1, value=label).font = bold_font
    ws2.cell(row=i, column=2, value=value).font = normal_font
    ws2.merge_cells(start_row=i, start_column=2, end_row=i, end_column=6)
set_col_widths(ws2, [10, 45, 15, 10, 22, 22])

# PAGE 3: TABLEAU 1 - HONORAIRES
ws3 = wb.create_sheet("2.1 Honoraires")
ws3.merge_cells('A1:F1')
ws3['A1'].value = "Tableau 1 : Honoraires des formateurs et experts"
ws3['A1'].font = title_font
ws3.merge_cells('A2:F2')
ws3['A2'].value = "2.1 Honoraires des formateurs et experts"
ws3['A2'].font = subtitle_font

for col, h in enumerate(headers, 1):
    ws3.cell(row=4, column=col, value=h)
style_header_row(ws3, 4, 6)

data1 = [("1","Expert en Intelligence Artificielle appliquée : formation IA, agents IA, aide à la décision","Jour",5,1500000),("2","Expert en Analyse de données : outils et méthodes (Excel avancé, Power BI, Python/R)","Jour",5,1200000),("3","Formateur pratique / accompagnateur : séances de travaux dirigés et exercices","Jour",5,800000)]
for i, (num, poste, unite, qte, cu) in enumerate(data1, 5):
    ws3.cell(row=i, column=1, value=num).font = normal_font
    ws3.cell(row=i, column=1).alignment = Alignment(horizontal='center')
    ws3.cell(row=i, column=2, value=poste).font = normal_font
    ws3.cell(row=i, column=3, value=unite).font = normal_font
    ws3.cell(row=i, column=3).alignment = Alignment(horizontal='center')
    ws3.cell(row=i, column=4, value=qte).font = normal_font
    ws3.cell(row=i, column=4).number_format = num_format
    ws3.cell(row=i, column=4).alignment = Alignment(horizontal='center')
    ws3.cell(row=i, column=5, value=cu).font = normal_font
    ws3.cell(row=i, column=5).number_format = num_format
    ws3.cell(row=i, column=6).font = normal_font
    ws3.cell(row=i, column=6).number_format = num_format
    ws3.cell(row=i, column=6).value = f"=D{i}*E{i}"

tr = 8
ws3.cell(row=tr, column=2).font = total_font
ws3.cell(row=tr, column=2).value = "Sous-total Honoraires"
ws3.cell(row=tr, column=2).alignment = Alignment(horizontal='right')
ws3.merge_cells(start_row=tr, start_column=2, end_row=tr, end_column=5)
ws3.cell(row=tr, column=6).font = total_font
ws3.cell(row=tr, column=6).number_format = num_format
ws3.cell(row=tr, column=6).value = "=SUM(F5:F7)"
style_data_row(ws3, tr, 6, is_total=True)
set_col_widths(ws3, [10, 55, 15, 10, 22, 22])

# PAGE 4: TABLEAU 2 - PRÉPARATION
ws4 = wb.create_sheet("2.2 Préparation")
ws4.merge_cells('A1:F1')
ws4['A1'].value = "Tableau 2 : Préparation pédagogique et développement des supports"
ws4['A1'].font = title_font
ws4.merge_cells('A2:F2')
ws4['A2'].value = "2.2 Préparation pédagogique et développement des supports"
ws4['A2'].font = subtitle_font
for col, h in enumerate(headers, 1):
    ws4.cell(row=4, column=col, value=h)
style_header_row(ws4, 4, 6)

data2 = [("1","Conception du programme et du contenu pédagogique (adaptation au contexte FID)","Forfait",1,3000000),("2","Élaboration des supports de présentation (diapositives, guides participants)","Forfait",1,2500000),("3","Création des exercices pratiques, cas concrets et études de cas","Forfait",1,2000000),("4","Préparation des environnements de travail (postes, logiciels, données d'exemple)","Forfait",1,1500000)]
for i, (num, poste, unite, qte, cu) in enumerate(data2, 5):
    ws4.cell(row=i, column=1, value=num).font = normal_font
    ws4.cell(row=i, column=1).alignment = Alignment(horizontal='center')
    ws4.cell(row=i, column=2, value=poste).font = normal_font
    ws4.cell(row=i, column=3, value=unite).font = normal_font
    ws4.cell(row=i, column=3).alignment = Alignment(horizontal='center')
    ws4.cell(row=i, column=4, value=qte).font = normal_font
    ws4.cell(row=i, column=4).number_format = num_format
    ws4.cell(row=i, column=4).alignment = Alignment(horizontal='center')
    ws4.cell(row=i, column=5, value=cu).font = normal_font
    ws4.cell(row=i, column=5).number_format = num_format
    ws4.cell(row=i, column=6, value=cu).font = normal_font
    ws4.cell(row=i, column=6).number_format = num_format

tr2 = 9
ws4.cell(row=tr2, column=2).font = total_font
ws4.cell(row=tr2, column=2).value = "Sous-total Préparation pédagogique"
ws4.cell(row=tr2, column=2).alignment = Alignment(horizontal='right')
ws4.merge_cells(start_row=tr2, start_column=2, end_row=tr2, end_column=5)
ws4.cell(row=tr2, column=6).font = total_font
ws4.cell(row=tr2, column=6).number_format = num_format
ws4.cell(row=tr2, column=6).value = "=SUM(F5:F8)"
style_data_row(ws4, tr2, 6, is_total=True)
set_col_widths(ws4, [10, 55, 15, 10, 22, 22])

# PAGE 5: TABLEAU 3 - MATÉRIEL
ws5 = wb.create_sheet("2.3 Matériel")
ws5.merge_cells('A1:F1')
ws5['A1'].value = "Tableau 3 : Matériel, logiciels et fournitures"
ws5['A1'].font = title_font
ws5.merge_cells('A2:F2')
ws5['A2'].value = "2.3 Matériel, logiciels et fournitures"
ws5['A2'].font = subtitle_font
for col, h in enumerate(headers, 1):
    ws5.cell(row=4, column=col, value=h)
style_header_row(ws5, 4, 6)

data3 = [("1","Licences logicielles temporaires (outils IA, analyse de données, agents IA)","Lot",1,2000000),("2","Fournitures pédagogiques (cahiers, stylos, marqueurs, paperboard)","Lot",1,500000),("3","Impression des supports de formation (guides participants, exercices)","Exemplaire",20,15000),("4","Clés USB ou supports numériques de distribution des contenus","Lot",1,400000)]
for i, (num, poste, unite, qte, cu) in enumerate(data3, 5):
    ws5.cell(row=i, column=1, value=num).font = normal_font
    ws5.cell(row=i, column=1).alignment = Alignment(horizontal='center')
    ws5.cell(row=i, column=2, value=poste).font = normal_font
    ws5.cell(row=i, column=3, value=unite).font = normal_font
    ws5.cell(row=i, column=3).alignment = Alignment(horizontal='center')
    ws5.cell(row=i, column=4, value=qte).font = normal_font
    ws5.cell(row=i, column=4).number_format = num_format
    ws5.cell(row=i, column=4).alignment = Alignment(horizontal='center')
    ws5.cell(row=i, column=5, value=cu).font = normal_font
    ws5.cell(row=i, column=5).number_format = num_format
    if qte > 1:
        ws5.cell(row=i, column=6).font = normal_font
        ws5.cell(row=i, column=6).number_format = num_format
        ws5.cell(row=i, column=6).value = f"=D{i}*E{i}"
    else:
        ws5.cell(row=i, column=6, value=cu).font = normal_font
        ws5.cell(row=i, column=6).number_format = num_format

tr3 = 9
ws5.cell(row=tr3, column=2).font = total_font
ws5.cell(row=tr3, column=2).value = "Sous-total Matériel et fournitures"
ws5.cell(row=tr3, column=2).alignment = Alignment(horizontal='right')
ws5.merge_cells(start_row=tr3, start_column=2, end_row=tr3, end_column=5)
ws5.cell(row=tr3, column=6).font = total_font
ws5.cell(row=tr3, column=6).number_format = num_format
ws5.cell(row=tr3, column=6).value = "=SUM(F5:F8)"
style_data_row(ws5, tr3, 6, is_total=True)
set_col_widths(ws5, [10, 55, 15, 10, 22, 22])

wb.save("offre_financiere_fid_ia.xlsx")
print("Partie 1 sauvegardée. Sheets:", wb.sheetnames)
print("Formules Tableau 1:", ws3['F5'].value, ws3['F6'].value, ws3['F7'].value, ws3['F8'].value)
