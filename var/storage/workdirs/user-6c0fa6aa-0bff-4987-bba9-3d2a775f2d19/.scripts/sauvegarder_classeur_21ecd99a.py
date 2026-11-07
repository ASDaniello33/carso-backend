# Le fichier XLSX a été téléchargé via fetch_url
# On va le sauvegarder dans l'espace de travail
import zipfile
import io
import json

# Le fichier XLSX est disponible via le chemin de téléchargement
# On va le lire directement depuis le chemin fourni par fetch_url

# D'abord, vérifier les fichiers disponibles
from pathlib import Path

print("Fichiers dans l'espace de travail:")
for f in Path('.').iterdir():
    print(f"  {f.name}")

# Le fichier XLSX a été téléchargé, on va le lire avec l'aide classeur
import classeur

# Lire le classeur
resultat = classeur.lire_classeur('classeur.xlsx')
print("\n=== STRUCTURE DU CLASSEUR ===")
print(f"Feuilles: {resultat['feuilles']}")
print(f"Dimensions: {resultat['dimensions']}")

# Lire les 15 premières lignes de la première feuille
nom_feuille = resultat['feuilles'][0]
print(f"\n=== APERÇU DE LA FEUILLE '{nom_feuille}' (15 premières lignes) ===")
lignes = classeur.lire_lignes('classeur.xlsx', nom_feuille, max_lignes=15)
for i, ligne in enumerate(lignes, 1):
    print(f"Ligne {i}: {ligne}")
