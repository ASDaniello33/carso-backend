# Le modèle est stocké dans le backend. On ne peut pas y accéder directement.
# Mais on peut utiliser le tool clone_document_structure pour cloner le modèle
# ou utiliser remplir_document_modele.

# Vérifions ce qui est disponible dans l'espace de travail
from pathlib import Path

# Espace de travail
cwd = Path('.')
print("Fichiers dans l'espace de travail:")
for f in cwd.iterdir():
    print(f"  {f.name} ({f.stat().st_size} octets)")

# Scripts
scripts = Path('.scripts')
if scripts.exists():
    print("\nScripts:")
    for f in scripts.iterdir():
        print(f"  {f.name}")
