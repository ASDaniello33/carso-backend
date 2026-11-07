---
name: docx-officiels
description: >
  Lire, inspecter puis MODIFIER LE MODÈLE DOCX D'UN CLIENT en conservant ses
  styles, sa numérotation, ses en-têtes, son logo et sa couverture. La méthode :
  lire les éléments du modèle, COPIER le fichier de l'organisation dans l'objet
  métier, puis modifier cette copie (texte à style conservé, ou contenu HTML
  stylisé avec images base64/SVG). Les outils exacts de cette chaîne sont décrits
  dans le bloc « modèle d'une organisation », fourni aux agents qui en disposent.
  Jamais de reconstruction, jamais de chemin disque libre : uniquement des
  identifiants. Création = statut proposed.
---

# Manipulation DOCX (CARSO)

## Règles

- Identifiant = `document_id` / `offre_id` UUID (jamais un path, jamais une URL).
- Création / copie / édition → statut `proposed` (validation humaine).
- Un DOCX n'a pas de pages physiques fiables : `read_document_range` parle de
  **paragraphes** (ou d'« éléments » : paragraphes + tableaux).

## Choisir la bonne route

| Besoin | Route |
|---|---|
| **Modifier le modèle d'une organisation** (styles, numérotation, en-têtes, pieds, logo, couverture) | lire les éléments du modèle → copier son fichier dans l'objet métier → modifier la copie (bloc « modèle d'une organisation ») |
| Pas de modèle pour l'organisation | `generer_document_professionnel` (thème CARSO) |
| Livrable CARSO soigné, sans modèle imposé | `generer_document_professionnel` |
| Composants HTML libres (design hors thème) | `generer_document_html` |

## Modèle d'une organisation : copier puis modifier

Les modèles réels n'ont pas de `{{placeholder}}` : ce sont des textes rédigés,
des tableaux, des titres. On **copie le fichier** dans l'objet métier, puis on
réécrit ses éléments. Le reste du fichier (styles, numérotation, logo, en-tête,
pied, couverture, textes fixes) n'est jamais reconstruit.

```text
1. lister les modèles de l'organisation   → identifiant, nom, version
                                            (liste vide ⇒ aucun modèle)
2. lire les éléments du modèle            → element_id, texte, style, images
3. copier le fichier dans l'objet métier  → la COPIE est le document de travail
4. modifier la copie :
     - remplacer le texte d'un élément (style du modèle conservé)
     - remplir un ou plusieurs éléments par du HTML stylisé
5. relire les éléments de la copie        ← identifiants recalculés
```

| Geste | Ce qu'il change | Ce qu'il préserve |
|---|---|---|
| remplacer le texte d'un élément | le **texte** | style de paragraphe, police, taille, couleur, alignement, retrait, interligne |
| remplir par du HTML | remplace (ou complète : avant / après) un élément par du **contenu HTML stylisé** | tout le reste du fichier |

- **Phrase, titre existant** → remplacement de texte. Pour alterner les styles
  dans le même paragraphe, fournir une liste de segments
  `{texte, gras, italique, souligne, couleur, taille_pt}`.
- **Section rédigée, liste, tableau, figure** → remplissage par du HTML :
  `h1`-`h6`, `p`, `ul`/`ol`, `table`, `img`. Les images passent en
  `data:image/png;base64,…` (aucun chemin local), un `<svg>` est rasterisé
  automatiquement.
- **Tableau du modèle** : le remplacement de texte le refuse — décrire le
  tableau en HTML (fusions `colspan`/`rowspan` respectées).
- **Paragraphe illustré** : le remplacement de texte refuse de peur de perdre
  l'image ; passer par le remplissage HTML avec la nouvelle image en base64.
- **Identifiants** : toujours lus, jamais inventés. Ils changent après chaque
  édition : relire les éléments avant le lot suivant.

## Ce que le moteur garantit

- seule la partie visée change (`word/document.xml`, ses relations, les médias
  ajoutés) : les autres parties du paquet restent **identiques octet pour
  octet**, vérifié à chaque édition ;
- aucune image perdue (le contrôle refuse la copie qui en perdrait une) ;
- chaque édition crée une **version** : historique conservé, rien d'écrasé ;
- une copie invalide n'est pas déposée : l'erreur dit ce qui a échoué ;
- le fichier du modèle n'est jamais modifié ; un modèle d'une autre organisation
  répond « introuvable ».

## Pièges

| Symptôme | Cause | Correctif |
|---|---|---|
| `Aucun document de travail pour l'offre` | édition avant copie | copier le modèle dans l'objet |
| `Élément 'block_0xx' introuvable` | identifiant d'une version antérieure | relire les éléments de la copie |
| `L'élément … est un tableau` | texte visé sur un tableau | décrire le tableau en HTML |
| `L'élément … contient une image` | texte visé sur un paragraphe illustré | remplissage HTML avec l'image en base64 |
| HTML vide / aucun bloc | contenu sans texte ni image | fournir un fragment HTML réel |

## Autres tools

1. `get_document_structure` — styles, sections, nb tableaux, unité de lecture.
2. `read_document_range` / `lire_element_document` — paragraphes ou éléments ciblés.
3. `generer_document_professionnel` — la voie **sans modèle** (thème CARSO).

## Interdits

Inventer un lot, un montant, une date absente du document source ; recréer le
document du client « au même style » ; annoncer un document comme officiel (il
reste `proposed`).
