---
name: xlsx-officiels
description: >
  Inspecter un classeur, lire une plage de lignes, cloner largeurs de colonnes.
  Base de l'import bénéficiaires (preview HITL — pas d'import silencieux).
  .xlsm (macros) refusé.
---

# Manipulation XLSX (CARSO)

## Tools
- `get_document_structure` — feuilles, dimensions.
- `read_document_range` — `sheet`, `from_index` / `to_index` = lignes.
- `generer_document` format=xlsx — largeurs de la 1re feuille + données fournies.
- `importer_tableau_xlsx` — un tableau existant, imports/fusions/largeurs conservés.
- `generer_document_professionnel` avec `format='xlsx'` — classeur stylé (formules
  vivantes `data-formule`, formats, totaux).
- Formules : recopiées telles quelles, **jamais calculées par le LLM**.

## Classeurs en série : script ancré (INC-124)

Pour produire **un classeur par session** (ou consolider des listes), le script
est le bon outil — mais il doit déposer ses fichiers :

```jsonc
{"source": "…", "ancre": "session", "entity_id": "<uuid>",
 "livrables": [{"fichier": "presences.xlsx",
                "type_document": "liste_beneficiaires", "nom": "presences.xlsx"}]}
```

- `openpyxl` est **bloqué** dans la sandbox : `import classeur` (aide de l'espace
  de travail) → `classeur.ecrire_classeur('x.xlsx', 'Feuille', lignes)` ;
- le script tourne dans un dossier de travail supprimé après l'appel : un
  fichier non déclaré dans `livrables` n'est pas enregistré ;
- `code_retour` ≠ 0 ⇒ aucun dépôt ; un livrable refusé est listé avec sa cause.

## Import bénéficiaires
Analyse de structure + mapping + preview + validation humaine.
Aucun INSERT tant que l'humain n'a pas approuvé (instruction/06 §8).
