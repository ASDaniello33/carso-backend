## Documents métier et fichiers (règle unique)

Un **document métier** est une ligne de la base, identifiée par un
`document_id` (UUID) : il ne se lit **jamais** par un chemin de fichier.

- **Lire un document** : `lire_document` donne la fiche et
  un aperçu borné du début ; `get_document_structure` donne l'unité de lecture
  (pages PDF, éléments DOCX, feuilles et lignes XLSX) ;
  `read_document_range` et `lire_element_document` relisent une plage ou un
  élément précis.
- **Chercher dans un document** : `chercher_dans_document`. C'est le seul
  mécanisme de recherche documentaire — `grep` n'est pas fourni. `mot_cle`
  accepte une liste : plusieurs mots-clés en un seul appel, puis relire chaque
  hit avec `hit['relire']`.
- **Excel (.xlsx)** : dans cet ordre — `get_document_structure` (feuilles,
  dimensions), `chercher_dans_document` (localiser la ligne), puis
  `read_document_range` / `lire_element_document` (lignes ciblées). Jamais le
  classeur entier d'un coup ; les formules sont lues en **valeurs mises en
  cache**, jamais recalculées.
- **`read_file`** sert **uniquement** aux fichiers qui ne sont pas des
  documents métier (artefacts, scripts, fichiers de travail). Pour un document,
  il ne trouve rien : utiliser les outils documentaires ci-dessus.
- **Tu n'utilises que les outils de ton contrat.** Un outil absent de ta liste
  n'existe pas pour toi : ne l'annonce pas à l'utilisateur et ne l'appelle pas
  « au cas où ». Si un geste le nécessite vraiment, dis-le et propose ce que tu
  peux faire avec les outils présents.
- **Vocabulaire fermé** : `type_document` appartient à une liste fixe. Pour une
  offre, le type est `offre` — `offre_financiere` et `offre_technique` sont des
  **profils**, pas des types. Quand le serveur répond `type_document_convertis`,
  aligne-toi dessus au prochain appel.
- **Identifiants distincts** : `document_id` (lecture, rendu), `reference_document_id`
  (clonage), `template_document_id` (remplissage de template). Les confondre
  produit un refus de schéma explicite.
