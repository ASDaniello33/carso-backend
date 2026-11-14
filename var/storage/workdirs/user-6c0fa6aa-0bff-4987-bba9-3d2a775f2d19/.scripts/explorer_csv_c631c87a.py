from pathlib import Path

# Explorer l'espace de travail pour trouver le CSV joint
racine = Path('.')
print("Contenu de l'espace de travail :")
for p in sorted(racine.rglob('*'))[:80]:
    if p.is_file():
        print(" ", p, p.stat().st_size, "octets")
    else:
        print(" [dossier]", p)
