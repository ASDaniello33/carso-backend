---
name: scripts-python
description: >
  Écrire un script Python pour un traitement documentaire en série (JSON, CSV,
  python-docx, pypdf, aide classeur), exécuté par run_python_script dans une
  sandbox : espace personnel, ou dossier d'un objet métier avec dépôt des
  livrables (INC-124). Pas de os/sys/subprocess/réseau, pas d'openpyxl.
---

# Scripts Python d'agent (CARSO)

## Quand

- transformation que les tools typés ne couvrent pas : consolider plusieurs
  classeurs, nettoyer une liste, calculer un budget, produire un document par
  session (traitement **en série**) ;
- pour **un** livrable rédigé (offre, rapport, fiche), les tools de génération
  restent la voie : `generer_document_professionnel`, ou la chaîne « copier le
  modèle puis l'éditer » quand une organisation impose son document.

## Deux modes

| Mode | Paramètres | Où le script écrit | Ce qui est enregistré |
|---|---|---|---|
| **Personnel** | `source`, `nom` | ton espace (`workdirs/user-<compte>`) | rien : les fichiers restent privés |
| **Ancré** | + `ancre` + `entity_id` (+ `livrables`) | `<dossier métier>/.tmp-run-…` (supprimé après) | uniquement les fichiers de `livrables`, en `proposed` |

```jsonc
// ancré : produit une fiche par session et les dépose
{
  "source": "…",
  "ancre": "session", "entity_id": "<uuid>",
  "livrables": [{"fichier": "sorties/fiche.xlsx",
                 "type_document": "fiche_presence",
                 "nom": "fiche_presence.xlsx"}]
}
```

- `livrables` **exige** une ancre ; sans ancre, le fichier n'entre pas dans le
  référentiel ;
- `type_document` est un vocabulaire **fermé** (jamais deviné) ; un livrable
  refusé est listé avec sa cause, les autres sont déposés ;
- même `nom` + même `type_document` + même ancre ⇒ **nouvelle version** ;
- `code_retour` ≠ 0 ⇒ **aucun dépôt** (corriger puis relancer) ;
- le dossier de travail est supprimé **dans tous les cas** : ce qui n'est pas
  déclaré est perdu, volontairement.

## Ce que le script peut lire en mode ancré

1. son répertoire courant (le dossier de travail) ;
2. le dossier métier — c'est son **parent** (`Path('..')`) ;
3. le dossier de l'organisation émettrice (ses modèles) ;
4. ton espace de travail, l'installation Python, les temporaires système.

Tout le reste du stockage est refusé par la garde d'audit.

## Écrire le script

- `run_python_script(source=…, nom=…)`, source UTF-8 complète en **un** appel ;
- imports utiles : `json`, `csv`, `re`, `datetime`, `pathlib`, `docx`, `pypdf`,
  `zipfile`, et l'aide `classeur` de ton espace ;
- **`openpyxl` est bloqué** à l'exécution : pour un classeur, `import classeur` puis
  `classeur.ecrire_classeur('liste.xlsx', 'Feuille', lignes, titre='…')` — ou
  `generer_document_professionnel` avec `format='xlsx'` pour un livrable stylé ;
- lis `stdout`/`stderr` au retour, puis `fichiers` (ce qui a réellement été écrit).

## Interdits (refus explicite, jamais silencieux)

`os`, `sys`, `subprocess`, `shutil`, `socket`, `urllib`, `ctypes`, imports
relatifs ; réseau ; lancer un autre programme ; écrire, renommer ou supprimer
hors de la zone d'écriture ; lire un `.env` ou du code applicatif ; arrêter ou
redémarrer le serveur, piloter un service (`kill`, `taskkill`, `systemctl`,
`uvicorn`, `ssh`, `git push`). Ne réécris pas un script refusé pour « passer » la
garde : explique le refus au compte.

## Erreurs fréquentes

| Erreur | Cause | Correctif |
|---|---|---|
| `ModuleNotFoundError: openpyxl` | import bloqué par la garde | aide `classeur`, ou génération XLSX par tool |
| « Écriture refusee hors de l'espace de travail » | chemin absolu ou `..` hors zone | écrire en relatif dans le répertoire courant |
| aucun document déposé | `code_retour` ≠ 0, `livrables` absent, ou fichier non déclaré | corriger le script, déclarer chaque fichier |
| `refuses` non vide | fichier absent / type inconnu / format refusé | lire la `cause` et corriger l'entrée |
| livrable introuvable ensuite | version créée sous un autre nom ou une autre ancre | même `nom` + même `type_document` + même ancre |
