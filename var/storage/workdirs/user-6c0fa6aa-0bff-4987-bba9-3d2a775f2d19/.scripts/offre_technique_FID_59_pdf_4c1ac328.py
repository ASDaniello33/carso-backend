from pathlib import Path
from fpdf import FPDF

# Le DOCX a déjà été généré avec succès.
# On ne peut pas convertir DOCX->PDF ici (pas de docx2pdf disponible).
# Le document DOCX est déjà déposé et prêt.
# On informe l'utilisateur.

p = Path('offre_technique_FID_59.docx')
if p.exists():
    print(f"Le DOCX existe ({p.stat().st_size} octets).")
    print("La conversion DOCX->PDF n'est pas disponible dans cette sandbox.")
    print("Le document DOCX est prêt et téléchargeable.")
else:
    print("Erreur : DOCX introuvable")
