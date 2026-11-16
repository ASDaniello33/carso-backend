## Modèle d'une organisation : copier le fichier, puis le modifier

Quand une organisation impose **son** document, le livrable doit être **son
fichier**, modifié sur place. Tu ne le recrées jamais « au même style » et tu ne
le remplis pas de marqueurs : les modèles réels n'ont pas de `{{placeholder}}`.
Tu copies le DOCX du client dans l'offre, puis tu réécris les éléments un par
un. Couverture, en-tête, pied, logo, styles, numérotation et textes fixes
restent ceux du client — c'est mesuré, pas promis.

**La génération libre est fermée quand un modèle s'applique.** Les outils qui
créent un document neuf (`generer_document_professionnel`,
`generer_document_html`, `generer_document`, la rédaction progressive) sont
refusés par le moteur dès que l'organisation a déposé un modèle **du type de
l'offre visée**, ou dès qu'une copie de ce type existe sur cet objet : le
message nomme le document à éditer. Un modèle ne couvre que son type d'offre :
un `modele_offre_technique` ne sert pas l'offre financière du même lot (et
réciproquement) ; seul un modèle générique `offre` vaut pour les deux. Ce n'est
pas un contournement à chercher (autre nom, autre ancre) : c'est la voie
unique. Sans modèle applicable, ces outils restent les bons.

### La chaîne, dans cet ordre

```text
1. lister_modeles_documents(organisation_id)
     → modele_id, nom, type, version   (liste vide ⇒ aucun modèle)
2. obtenir_structure(organisation_id=…, modele_id=…)
     → éléments réels (element_id, texte, style, images), zones, {{clé}} éventuels
3. copier_modele_document(ancre, entity_id, modele_id?)
     → document_id + la même liste d'éléments : la copie est le document de travail
4. remplacer_element_model(ancre, entity_id, elements=[…], nouveaux_elements=[…])
     ← tous les titres/lignes à réécrire en UN appel (une seule version)
   remplir_modele(ancre, entity_id, remplissages=[{element_id, contenu_html}]) ← sections
5. obtenir_structure(ancre=…, entity_id=…)   ← relire avant la suite (ids recalculés)
6. revenir_version_document(ancre, entity_id, version?)   ← si un lot a abîmé
     la copie : la version restaurée (défaut : la précédente) redevient
     le document de travail
```

`ancre` + `entity_id` désignent l'objet qui reçoit la copie : `offre` (réponse à
un appel), `mission` (préparation/exécution) ou `session` (déroulement). Ce sont
les trois objets qui ont un dossier documentaire et un périmètre d'accès ; une
autre ancre est refusée. Le type de dépôt du document de travail se précise avec
`type_document` à la copie (`fiche_technique`, `rapport`, `checklist`… ; défaut
`offre` pour une offre, `autre` sinon).

### Les deux outils d'édition, et quand utiliser l'un ou l'autre

| Outil | Ce qu'il change | Ce qu'il ne change pas |
|---|---|---|
| `remplacer_element_model` | le **texte** d'un élément | style de paragraphe, police, taille, couleur, alignement, retrait, interligne du modèle |
| `remplir_modele` | remplace (ou complète : `position: avant\|apres`) un élément par du **HTML stylisé** : `h1`-`h6`, `p`, `ul`/`ol`, `table`, `img` en data URI base64, `<svg>` rasterisé | tout le reste du fichier |

- Une phrase, un titre existant, une cellule de titre : `remplacer_element_model`.
  Plusieurs d'un coup : deux listes alignées `elements` / `nouveaux_elements`
  (jamais une appel par ligne — le lot ne crée qu'une version).
- Une section entière à rédiger, une liste, un tableau, un schéma : `remplir_modele`.
  **Plusieurs éléments HTML en un seul appel** : une entrée `remplissages` par
  élément, chacune avec sa `position` (`remplacer`, `avant`, `apres`) — un lot,
  une version, jamais un appel par section.
- Mélanger les styles **dans** un paragraphe : passer une liste de segments
  `{texte, gras, italique, souligne, couleur, taille_pt}` à `remplacer_element_model`.
- Les identifiants (`block_004`…) viennent toujours d'une lecture : jamais inventés.
  Ils sont **recalculés** après chaque édition — relis la structure avant un
  nouveau lot.

### Corriger un lot raté : le retour arrière

Un lot HTML peut atterrir au mauvais endroit (mauvais `element_id`, `position`
mal choisie, contenu trop long). Ne répare pas à l'aveugle par-dessus :

```text
obtenir_structure(ancre=…, entity_id=…)   ← relire : `historique` liste les
                                                versions (numero, nature, courante)
revenir_version_document(ancre, entity_id)                 ← annule le dernier geste
revenir_version_document(ancre, entity_id, version=2)      ← revient à la version 2
   → le contenu de la version choisie redevient la copie de travail, déposé comme
     NOUVELLE version (rien n'est supprimé, l'historique reste complet)
obtenir_structure(ancre=…, entity_id=…)   ← relire les identifiants
   → puis refaire le lot correctement
```

- `version` omise : annule la dernière édition (recul d'un cran) ;
- `version` fournie : revient à **cette** version précise, telle qu'elle
  apparaît dans `historique` (`nature` indique le geste d'origine :
  `remplacement_texte`, `remplissage_html`, `retour_arriere`…) ;
- une version inconnue est refusée avec la liste des versions disponibles ; la
  version courante aussi (« c'est déjà le document de travail ») ;
- la restauration est elle-même une version : l'historique reste complet, rien
  n'est écrasé, et l'annulation d'une annulation est possible ;
- après un retour arrière, relis systématiquement la structure : les
  identifiants de la version restaurée ne sont pas ceux de la version annulée.

### Règles de fond

| Règle | Détail |
|---|---|
| Aucun modèle ⇒ autre voie | `lister_modeles_documents` vide : produis le document avec `generer_document_professionnel` (thème CARSO) et dis-le. Jamais un gabarit inventé. |
| Un seul document par objet et par modèle | La copie porte un nom déterministe (`modele-<référence>.docx`) : relancer la copie sur le même objet renvoie la version en cours, elle n'en crée pas une seconde. |
| Copie, jamais régénération | Le document de travail est la copie du fichier client. Ne le reconstruis pas avec `generer_document_professionnel` / `_html` : ce serait un autre document. |
| Images | En `data:image/png;base64,…` (ou jpeg), ou `<svg>` : le serveur les embarque. Jamais de chemin local, jamais d'URL distante supposée accessible. |
| Paragraphe à image | `remplacer_element_model` le refuse (l'image disparaîtrait). Utilise `remplir_modele` avec la nouvelle image en base64. |
| Tableau du modèle | `remplacer_element_model` refuse un tableau : réécris-le en HTML (`<table>`) avec `remplir_modele`. |
| Un lot = un document | Le contenu d'un lot ne déborde jamais sur un autre, même modèle partagé. |
| Modèle d'un tiers | Un `modele_id` d'une autre organisation répond « introuvable ». |
| Rien d'inventé | Une donnée absente se demande à l'utilisateur ou s'écrit « (à compléter) ». Un montant, une date, un nom ne se devinent pas. |

### Ce que le moteur garantit sur la copie éditée

- seule la partie visée change (`word/document.xml`, ses relations, les médias
  ajoutés) : toutes les autres parties du paquet restent **identiques octet pour
  octet** — vérifié à chaque édition, avant dépôt ;
- aucune image n'est perdue (le contrôle refuse une copie qui en perdrait une) ;
- chaque édition crée une **version** : l'historique est conservé, rien n'est
  écrasé ; le document reste en `proposed` jusqu'à la validation humaine ;
- une copie invalide n'est **pas** déposée : l'erreur dit ce qui a échoué.

### Pièges qui font échouer un appel

| Symptôme | Cause | Correctif |
|---|---|---|
| `Aucun document de travail pour …` | édition avant copie | `copier_modele_document(ancre, entity_id, modele_id)` |
| `Élément 'block_0xx' introuvable` | identifiant d'une version précédente | relire `obtenir_structure` |
| `L'élément … est un tableau` | texte visé sur un tableau | décrire le tableau en HTML |
| `L'élément … contient une image` | texte visé sur un paragraphe illustré | `remplir_modele` avec l'image en base64 |
| `HTML vide` / `aucun bloc Word` | contenu sans texte ni image | fournir un vrai fragment HTML |
| `Aucun modèle … pour l'organisation` | pas de modèle au registre | `generer_document_professionnel`, et le dire |
| `Plusieurs modèles existent` | mission/session sans type ni identifiant | choisir un `modele_id` dans `lister_modeles_documents` |
