import classeur

# L'aide classeur ne sait pas télécharger depuis le web directement.
# Essayons une autre approche : utiliser fetch_url pour obtenir le fichier,
# puis le lire avec zipfile + openpyxl.

# Mais d'abord, vérifions si le fichier a été déposé quelque part
from pathlib import Path

# Cherchons dans les dossiers parents aussi
for base in [Path('.').resolve(), Path('..').resolve()]:
    print(f"\n=== {base} ===")
    for f in base.rglob('*'):
        if f.is_file() and f.suffix in ['.xlsx', '.xls']:
            print(f"  TROUVÉ: {f}")
