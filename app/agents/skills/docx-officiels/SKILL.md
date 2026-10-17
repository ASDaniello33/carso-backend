---
name: docx-officiels
description: >
  Lire, inspecter, cloner la structure et remplir un template DOCX CARSO.
  Utiliser pour offres de formation, fiches techniques, checklists.
  Jamais de chemin disque libre : uniquement document_id. Création = proposed.
---

# Manipulation DOCX (CARSO)

## Règles
- Identifiant = `document_id` UUID (jamais un path, jamais une URL).
- Création / clonage / template → statut `proposed` (validation humaine).
- Un DOCX n'a pas de pages physiques fiables : `read_document_range` parle de **paragraphes**.

## Tools
1. `get_document_structure` — styles, sections, nb tableaux.
2. `read_document_range` — paragraphes `from_index` / `to_index`.
3. `clone_document_structure` — reprend marges/orientation de la référence.
4. `fill_template` — remplace `{{cle}}` uniquement.
5. `run_python_script` — automatisation ponctuelle (imports allowlistés).

## Interdits
Inventer un lot, un montant, une date absente du document source.
