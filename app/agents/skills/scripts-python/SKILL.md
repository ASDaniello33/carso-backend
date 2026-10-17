---
name: scripts-python
description: >
  Écrire un script Python court pour une transformation documentaire
  (JSON, CSV, python-docx, openpyxl, pypdf). Exécuté via run_python_script
  dans une sandbox. Pas de os/sys/subprocess/réseau.
---

# Scripts Python d'agent (CARSO)

## Quand
Automatiser une transformation ponctuelle (normaliser un tableau, extraire
des cellules) que les tools typés ne couvrent pas encore.

## Comment
Appeler `run_python_script` avec `source` UTF-8. Imports autorisés :
`json`, `csv`, `re`, `datetime`, `pathlib`, `docx`, `openpyxl`, `pypdf`, `fpdf`.

## Interdits
`os`, `sys`, `subprocess`, `socket`, `urllib`, imports relatifs, shell libre.
Le script ne doit pas écrire hors de la sandbox ni toucher PostgreSQL.
