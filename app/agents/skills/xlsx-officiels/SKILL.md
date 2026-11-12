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

## Ce qu'un classeur CARSO doit contenir

Mesure du 11/11 sur 1 354 classeurs produits : 100 % stylés, mais **0,6 %**
seulement portaient une formule et **aucun** ne contenait de graphique. Le
réflexe à corriger est là, pas dans l'outil.

### 1. Toute valeur calculée porte une formule

Un total, un sous-total, un écart, un pourcentage, une moyenne : écrire une
formule, jamais un nombre recopié. Un total tapé en dur devient faux au premier
changement et le tableur ne recalcule rien.

```html
<tr class="total"><td>Total</td>
    <td data-formule="=SUM(B3:B4)" data-format="devise:MGA">3 250 000</td></tr>
<tr><td>Écart</td>
    <td data-formule="=B4-B3" data-format="devise:MGA">750 000</td></tr>
<tr><td>Part</td>
    <td data-formule="=B3/B4" data-format="pourcentage">77 %</td></tr>
```

Le texte affiché dans la cellule est la valeur d'aujourd'hui (pour une relecture
hors tableur) ; la formule est ce qui vit dans le fichier. Ne jamais évaluer la
formule soi-même : elle part telle quelle.

### 2. Le style suit la donnée, pas le goût

| Donnée | Ce qu'on écrit |
|---|---|
| Ligne de total / sous-total | `class="total"` ou `class="sous-total"` |
| Valeur hors cible, dépassement | `class="alerte"` sur la ligne |
| Montant | `class="num" data-format="devise:MGA"` |
| Taux, part | `data-format="pourcentage"` (valeur en fraction) |
| Date | `data-format="date"` (`AAAA-MM-JJ`) |
| Cellule à mettre en avant | `data-gras`, `data-fond="primaire\|doux\|ok\|alerte"` |
| Cellule qui se renvoie à la ligne | `data-renvoi` |
| En-tête de colonne large | `data-largeur="24"` |
| Fusion (titre, total pleine largeur) | `colspan` / `rowspan` |

Les couleurs viennent des rôles du thème, jamais d'un code hexadécimal :
l'agent nomme un rôle, le thème choisit la teinte.

### 3. Un graphique quand les chiffres se comparent

Dès qu'une feuille présente des chiffres comparables, poser une ancre **juste
après le tableau** et déclarer le graphique :

```html
<table class="data">…</table>
<figure data-graphique="repartition"></figure>
```

```json
{"id": "repartition", "type": "pie",
 "titre": "Répartition du budget",
 "labels": ["Formation", "Logistique"],
 "valeurs": [2500000, 750000]}
```

- `pie` : une répartition (≤ 5-6 parts), avec `valeurs`.
- `bar` : comparer des lots, des postes, des périodes, avec `series`
  (`{"2025": [120, 90], "2026": [180, 150]}`).
- `line` : une évolution dans le temps, avec `series`.
- `donut` : une répartition avec un total au centre.

Dans un classeur, le graphique est un **objet Excel natif** lié à une feuille de
données dédiée : il se modifie et se filtre dans le tableur. Une spec
inutilisable produit un avertissement et aucun graphique : lire
`avertissements` avant de présenter le livrable.

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
