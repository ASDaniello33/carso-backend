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
- `clone_document_structure` format=xlsx — largeurs de la 1re feuille + données fournies.
- Formules : recopiées telles quelles, **jamais calculées par le LLM**.

## Import bénéficiaires
Analyse de structure + mapping + preview + validation humaine.
Aucun INSERT tant que l'humain n'a pas approuvé (instruction/06 §8).
