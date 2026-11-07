---
name: pdf-officiels
description: >
  Inspecter et lire un PDF par plage de pages. Générer un PDF neuf (fpdf2).
  Ne pas prétendre cloner polices embarquées ou formulaires AcroForm.
---

# Manipulation PDF (CARSO)

## Réaliste
- Lecture : `get_document_structure` (nb_pages, métadonnées) + `read_document_range` (pages 1-indexées).
- Génération : `generer_document` format=pdf → PDF **neuf** (fpdf2).
- PDF scanné sans couche texte → erreur explicite (pas d'OCR silencieux).
- PDF chiffré → refusé.

## Un modèle PDF d'organisation (INC-125)

Un PDF **ne se remplit pas** : il n'a pas de styles Word ni de structure
éditable. La chaîne « copier le modèle dans une offre » le refuse explicitement.

La seule fidélité possible est l'**identité visuelle** :

```text
generer_document(
  document_spec = …,
  modele = {organisation_id, type_document}   // le modèle PDF de ce client
)
  → polices, couleurs, marges, gabarit de page repris du modèle ;
    la structure reste celle du document_spec
```

Ce que cela ne donne pas : la couverture, les en-têtes/pieds, la numérotation ni
la mise en page exacte du PDF d'origine. Le dire à l'utilisateur plutôt que de
présenter le résultat comme une copie — si le client exige l'identique, demander
un DOCX de son modèle.

## Interdits
OCR inventé, clonage WYSIWYG des polices, écrasement d'un original, promesse de
« copie exacte » d'un PDF.
