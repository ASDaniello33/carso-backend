import io

# Le fichier Excel a été téléchargé via fetch_url et est disponible dans l'espace de travail
# On va le lire avec openpyxl (mais openpyxl est bloqué, donc on utilise une autre approche)

# Essayons avec xlrd d'abord
try:
    import xlrd
    workbook = xlrd.open_workbook('download.xlsx')
    print(f"Feuilles: {workbook.sheet_names()}")
    for sheet_name in workbook.sheet_names():
        sheet = workbook.sheet_by_name(sheet_name)
        print(f"\n=== Feuille: {sheet_name} ===")
        print(f"Dimensions: {sheet.nrows}x{sheet.ncols}")
        for row_idx in range(sheet.nrows):
            row = sheet.row_values(row_idx)
            print(row)
except Exception as e:
    print(f"xlrd error: {e}")
