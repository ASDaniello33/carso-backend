# Le modèle est stocké dans le backend. On ne peut pas y accéder directement par chemin.
# Mais on peut utiliser fill_template ou remplir_document_modele qui accèdent au stockage.
# 
# Alternative : on utilise le document cloné existant (411ed4c5) comme base,
# mais il n'a pas la structure complète.
#
# La seule façon fiable : utiliser le document_id du modèle pour le lire,
# puis le recopier via un script qui utilise le stockage CARSO.
#
# En fait, le plus simple est de lire le modèle via lire_document,
# puis de le cloner via clone_document_structure (déjà fait),
# puis de modifier le document cloné.

# Vérifions le document cloné existant
from pathlib import Path
cwd = Path('.')
print("Fichiers disponibles:")
for f in cwd.rglob('*'):
    if f.is_file():
        print(f"  {f.relative_to(cwd)} ({f.stat().st_size} octets)")
