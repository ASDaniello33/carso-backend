# Étape 1 : Cloner le modèle FID pour avoir une copie exacte
# On utilise clone_document_structure qui préserve la structure DOCX

# Mais clone_document_structure ne préserve pas bien la structure (vu précédemment).
# Alternative : on utilise le modèle comme template et on remplit les placeholders.

# La meilleure approche : utiliser le modèle DOCX directement via python-docx
# dans l'espace de travail. Mais on ne peut pas accéder au stockage directement.

# Solution : on va utiliser fill_template qui est fait pour ça.
# Mais fill_template ne remplit que les {{placeholders}}.

# La vraie solution : on recopie le modèle dans l'espace de travail via un script
# qui utilise l'API CARSO pour télécharger le modèle, puis on le modifie.

# En fait, le plus simple est d'utiliser le document cloné existant (411ed4c5)
# et de le modifier avec modifier_section_document.

# Mais le clonage a perdu la structure. Donc on doit recommencer.

# APPROCHE : Utiliser le modèle comme base via python-docx
# On ne peut pas accéder au stockage directement, mais on peut utiliser
# le fait que le modèle est un document enregistré.

# La seule façon fiable : utiliser le script pour recréer le document
# en copiant EXACTEMENT la structure du modèle (65 paragraphes, image, styles)
# puis en remplaçant les textes.

# On va d'abord analyser le modèle élément par élément pour comprendre sa structure exacte.
print("Le modèle a 65 paragraphes. Structure clé:")
print("- Élément 1: paragraphe avec image (logo FID)")
print("- Élément 7: 'OFFRE TECNIQUE'")
print("- Élément 8: 'LOL ICI'")
print("- Élément 20: 'Lieu + date'")
print("- Élément 21: 'Entreprise (ex : CARSO)'")
print("- Élément 23: 'Sommaire' (Heading 1)")
print("- Élément 65: 'Contenus commence ici'")
print()
print("Les 65 paragraphes contiennent des sauts de page et des espaces vides.")
print("On doit préserver TOUTES les sauts de page et la structure exacte.")
print()
print("STRATEGIE:")
print("1. Lire le modèle DOCX via python-docx (on le télécharge d'abord)")
print("2. Le copier dans l'espace de travail")
print("3. Remplacer les textes placeholders")
print("4. Ajouter le contenu de l'offre après 'Contenus commence ici'")
print("5. Sauvegarder en conservant les styles du modèle")
