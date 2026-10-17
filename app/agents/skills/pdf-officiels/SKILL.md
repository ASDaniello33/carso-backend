---
name: pdf-officiels
description: >
  Inspecter et lire un PDF par plage de pages. Générer un PDF neuf (fpdf2).
  Ne pas prétendre cloner polices embarquées ou formulaires AcroForm.
---

# Manipulation PDF (CARSO)

## Réaliste
- Lecture : `get_document_structure` (nb_pages, métadonnées) + `read_document_range` (pages 1-indexées).
- Génération : `generer_document` / `clone_document_structure` format=pdf → PDF **neuf** (fpdf2).
- PDF scanné sans couche texte → erreur explicite (pas d'OCR silencieux).
- PDF chiffré → refusé.

## Interdits
OCR inventé, clonage WYSIWYG des polices, écrasement d'un original.
