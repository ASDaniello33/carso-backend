import io
import zipfile

# Le fichier a été téléchargé, on va l'analyser
# On va lire le contenu brut du fichier XLSX

# D'abord, lister les feuilles disponibles
# On va utiliser l'outil inspect_beneficiaires_xlsx si c'est un import bénéficiaires
# Mais ici c'est un fichier externe, donc on va l'analyser manuellement

# On va lire le fichier XLSX téléchargé
# Le fichier est disponible via le chemin du téléchargement

# On va utiliser openpyxl pour lire le classeur
try:
    import openpyxl
    wb = openpyxl.load_workbook('/tmp/downloaded_file.xlsx')
    print("=== FEUILLES DISPONIBLES ===")
    for nom_feuille in wb.sheetnames:
        ws = wb[nom_feuille]
        print(f"\nFeuille: {nom_feuille}")
        print(f"  Dimensions: {ws.dimensions}")
        print(f"  Lignes: {ws.max_row}, Colonnes: {ws.max_column}")
        
        # Lire les 10 premières lignes
        print(f"  Aperçu (10 premières lignes):")
        for row_idx, row in enumerate(ws.iter_rows(min_row=1, max_row=min(10, ws.max_row), values_only=False), 1):
            valeurs = [cell.value for cell in row]
            print(f"    Ligne {row_idx}: {valeurs}")
            
except Exception as e:
    print(f"Erreur: {e}")
    print("Tentative avec zipfile pour lire les fichiers XML directement...")
    
    # Lire le fichier XLSX comme un ZIP
    with zipfile.ZipFile('/tmp/downloaded_file.xlsx', 'r') as z:
        print("Fichiers dans le classeur:")
        for name in z.namelist():
            print(f"  {name}")
