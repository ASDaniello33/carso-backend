import zipfile
import xml.etree.ElementTree as ET

# Le fichier Excel est un ZIP, on peut l'ouvrir directement
with zipfile.ZipFile('download.xlsx', 'r') as z:
    print("Fichiers dans le ZIP:")
    for name in z.namelist():
        print(f"  {name}")
    
    # Les données textuelles sont dans sharedStrings.xml
    print("\n\n=== sharedStrings.xml ===")
    with z.open('xl/sharedStrings.xml') as f:
        content = f.read().decode('utf-8')
        # Afficher les premières lignes
        lines = content.split('\n')
        for line in lines[:50]:
            print(line)
