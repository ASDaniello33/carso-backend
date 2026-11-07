from docx import Document
from docx.shared import Pt, Cm, Inches, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.oxml.ns import qn, nsdecls
from docx.oxml import parse_xml
import copy

# ============================================================
# 1. CLONER LE MODÈLE FID
# ============================================================
modele_path = 'Model_offre_technique_FID.docx'
doc = Document(modele_path)

# ============================================================
# 2. IDENTIFIER LES ÉLÉMENTS À REMPLACER
# ============================================================
# Structure du modèle (65 paragraphes) :
# Élément 1 : image (logo) - à conserver
# Élément 7 : "OFFRE TECNIQUE" -> titre
# Élément 8 : "LOL ICI" -> référence de l'appel
# Élément 20 : "Lieu + date" -> lieu + date
# Élément 21 : "Entreprise (ex : CARSO)" -> nom du soumissionnaire
# Élément 23 : "Sommaire" (Heading 1) -> titre section
# Élément 65 : "Contenus commence ici" -> contenu à remplacer

# ============================================================
# 3. SUPPRIMER LES ANCIENS PARAGRAPHE(S) PLACEHOLDER
# ============================================================
# On va parcourir tous les paragraphes et supprimer ceux qui sont des placeholders
# puis insérer le contenu au bon endroit

# Récupérer tous les paragraphes
paragraphes = doc.paragraphs

# Trouver les indices des paragraphes à supprimer (1-based dans docx, 0-based en Python)
# Élément 8 (LOL ICI) = index 7
# Élément 20 (Lieu + date) = index 19
# Élément 21 (Entreprise) = index 20
# Élément 65 (Contenus commence ici) = index 64

# On supprime les paragraphes placeholder en partant de la fin pour ne pas décaler les indices
indices_a_supprimer = [64, 20, 19, 7]  # indices 0-based

for idx in sorted(indices_a_supprimer, reverse=True):
    p = paragraphes[idx]
    p._element.getparent().remove(p._element)

# ============================================================
# 4. INSÉRER LE CONTENU AU BON ENDROIT
# ============================================================
# Après suppression, les indices ont changé. On insère après l'élément 1 (image/logo)
# et avant le "Sommaire" (qui était élément 23, maintenant vers index 3)

# On va insérer les paragraphes après le premier paragraphe (l'image)
# et avant le paragraphe "Sommaire"

# Trouver le paragraphe "Sommaire"
sommaire_idx = None
for i, p in enumerate(doc.paragraphs):
    if p.text == 'Sommaire':
        sommaire_idx = i
        break

print(f"Sommaire trouvé à l'index {sommaire_idx}")

# Contenu à insérer entre le logo et le Sommaire
contenu_avant_sommaire = [
    ('N°59/AMI/FID/FSR/SQC_CAB_FORM_IA', False, WD_ALIGN_PARAGRAPH.CENTER, 14),
    ('Fonds d\'Intervention pour le Développement (FID)', False, WD_ALIGN_PARAGRAPH.CENTER, 12),
    ('CARSO – Centre d\'Affaires de la Région Sud-Ouest', True, WD_ALIGN_PARAGRAPH.CENTER, 14),
    ('Toliara, le 6 novembre 2026', False, WD_ALIGN_PARAGRAPH.CENTER, 12),
]

for texte, gras, alignement, taille in contenu_avant_sommaire:
    # Insérer avant le sommaire
    new_p = doc.add_paragraph()
    # Déplacer avant le sommaire
    if sommaire_idx is not None:
        # On insère en ajoutant d'abord, puis on réorganise
        pass
    run = new_p.add_run(texte)
    run.font.size = Pt(taille)
    run.font.name = 'Times New Roman'
    if gras:
        run.font.bold = True
    new_p.alignment = alignement

# ============================================================
# 5. INSÉRER LE CONTENU APRÈS "Sommaire"
# ============================================================
# Remplacer "Contenus commence ici" par le contenu complet

# Trouver le paragraphe après Sommaire
contenu_start_idx = None
for i, p in enumerate(doc.paragraphs):
    if p.text == 'Sommaire':
        contenu_start_idx = i + 1
        break

print(f"Contenu commence à l'index {contenu_start_idx}")

# ============================================================
# 6. GÉNÉRER LE DOCUMENT COMPLET À PARTIR DU MODÈLE
# ============================================================
# Le modèle est trop simple pour être rempli directement.
# On va plutôt cloner le modèle, puis reconstruire le document complet
# en conservant les styles du modèle.

# Sauvegarder le modèle cloné
output_path = 'offre_technique_FID_59_modele.docx'
doc.save(output_path)
print(f"Modèle cloné sauvegardé : {output_path}")
