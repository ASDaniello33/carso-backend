import zipfile
import xml.etree.ElementTree as ET
import re

# Le fichier a déjà été téléchargé par fetch_url et est stocké quelque part
# Cherchons les fichiers xlsx dans l'espace de travail
from pathlib import Path

workspace = Path('.')
print("Fichiers dans l'espace de travail:")
for f in workspace.rglob('*'):
    if f.is_file():
        print(f"  {f}")
