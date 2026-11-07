"""
Copier le modèle FID dans l'espace de travail.
"""
from pathlib import Path

# Chemin du modèle dans le stockage
source = Path("/app/storage/documents/8f53cbb7-25b0-46a4-bf1b-65e8caf52612/Model_offre_technique_FID.docx")
dest = Path("Model_offre_technique_FID.docx")

# Lire le fichier source
with open(source, 'rb') as f:
    data = f.read()

# Écrire dans l'espace de travail
with open(dest, 'wb') as f:
    f.write(data)

print(f"Modèle copié : {dest}")
print(f"Taille : {dest.stat().st_size} octets")
