---
name: generation-document
description: >
  Produire un livrable DOCX ou PDF professionnel (rapport, proposition, devis,
  compte rendu, lettre, CV, note) avec l'outil generer_document_professionnel :
  le thème, la couverture, le sommaire numéroté et la pagination sont injectés
  par le moteur. À charger dès qu'un document, un fichier ou un export est
  demandé. generer_document_html reste la voie du document LIBRE (design écrit
  par l'agent) ; generer_document(document_spec) celle de la structure imposée.
---

# SKILL : génération de documents professionnels (DOCX / PDF / PPTX)

Tu produis des documents du niveau d'un cabinet de conseil : clairs, aérés,
hiérarchisés, cohérents. Ton outil par défaut est **`generer_document_professionnel`**.

## 1. Principe fondamental

L'outil **injecte automatiquement un thème professionnel** : polices, couleurs,
marges, en-tête (titre), pied de page « 2 / 5 », couverture pleine page, sommaire
avec numéros de page **calculés**.

- Tu écris **uniquement le contenu du `<body>`**, en HTML sémantique, avec les
  classes ci-dessous.
- Tu n'écris **pas** de `<style>`, pas de `style="color:…"`, pas de polices, pas
  de tailles. Les balises `<style>`, `<script>`, `<iframe>`, `<form>` sont
  **supprimées** par l'outil et signalées dans les avertissements.
- Tu choisis seulement les paramètres :

| Paramètre | Valeurs | Conseil |
|---|---|---|
| `titre` (obligatoire) | texte | Titre court : il apparaît en haut de chaque page et dans les métadonnées |
| `contenu_html` (obligatoire) | HTML du corps | Les composants de la section 3 |
| `format` | `pdf` (livrable à remettre), `docx` | `docx` seulement si l'utilisateur doit modifier le fichier |
| `theme` | `corporate` (bleu), `moderne` (violet), `nature` (vert) | `corporate` par défaut ; `nature` pour RSE/santé/agro ; `moderne` pour tech/créatif |
| `orientation` | `portrait`, `paysage` | `paysage` si un tableau a plus de 6 colonnes |
| `auteur` | texte | Toujours le renseigner si connu |
| `nom_fichier` | texte sans extension | Optionnel (dérivé du titre sinon) |
| `css_supplementaire` | CSS | **À éviter.** Sans effet sur un DOCX |

### Quel outil choisir

```text
generer_document_professionnel   PAR DÉFAUT pour tout docx/pdf/pptx : thème CARSO,
                                 couverture, sommaire numéroté, avertissements.
                                 format="pptx" = présentation 16:9 native (deck).
generer_document_html            document LIBRE : l'utilisateur impose un design
                                 ou une mise en page hors thème (CSS écrit par toi).
generer_document(document_spec)  structure IMPOSÉE : sections et provenances
                                 obligatoires (offre technique/financière).
```

## 2. Méthode (à suivre à chaque fois)

1. **Comprendre** : type de document, public, objectif, longueur. S'il manque une
   information essentielle (nom du client, montants, dates), **demande-la** avant
   de générer.
2. **Planifier** le plan (H1 / H2 / H3) avant d'écrire.
3. **Rédiger** un contenu concret. Pour chaque information, choisis le meilleur
   composant : chiffres clés → `table.kpi` ; données comparatives, planning,
   tarifs → `table.data` ; message important, recommandation, risque →
   `.callout` ; étapes → `<ol>` ; énumération courte → `<ul>`.
4. **Construire** le HTML avec les composants de la section 3.
5. **Relire** avec la checklist (section 6).
6. **Appeler l'outil**, puis **lire le résultat** (section 5) : corriger chaque
   avertissement et régénérer avant de présenter.

## 3. Composants disponibles

### Page de couverture (documents de 3 pages ou plus)
```html
<section class="cover">
  <div class="cover-label">Rapport d'analyse</div>
  <h1 class="cover-title">Titre principal du document</h1>
  <p class="cover-subtitle">Sous-titre explicatif sur une ou deux lignes</p>
  <div class="cover-meta">Préparé pour <strong>Client SA</strong> · Octobre 2026 · Version 1.0</div>
</section>
```
La couverture est rendue sur une page séparée, sans en-tête ni numéro de page.

### Sommaire (documents de 5 pages ou plus)
```html
<nav class="toc"><h2>Sommaire</h2></nav>
<div class="page-break"></div>
```
Laisse le `<nav>` **vide** : l'outil le remplit avec tous les H1 et H2 et calcule
les numéros de page. Pas de sommaire pour un document de moins de 4 pages.

### Titres et introduction
```html
<h1>1. Contexte</h1>
<p class="lead">Une phrase d'accroche qui résume la section.</p>
<h2>1.1 Sous-partie</h2>
<h3>Point détaillé</h3>
```

### Chiffres clés (2 à 4 par ligne, jamais plus)
```html
<table class="kpi"><tr>
  <td><span class="kpi-value">+24 %</span><span class="kpi-label">Croissance CA</span></td>
  <td><span class="kpi-value">1 250</span><span class="kpi-label">Clients actifs</span></td>
</tr></table>
```
`kpi-value` : un chiffre court (6 caractères max). `kpi-label` : 1 à 3 mots.

### Tableau de données
```html
<table class="data">
  <thead><tr><th>Produit</th><th class="num">Quantité</th><th class="num">Montant</th></tr></thead>
  <tbody>
    <tr><td>Licence Pro</td><td class="num">12</td><td class="num">4 800 €</td></tr>
    <tr><td>Formation</td><td class="num">2</td><td class="num">1 600 €</td></tr>
  </tbody>
  <tfoot><tr><td>Total</td><td></td><td class="num">6 400 €</td></tr></tfoot>
</table>
```
- `<thead>` **obligatoire** (sans lui, l'outil le signale).
- Classe `num` sur les colonnes de chiffres (alignées à droite).
- 6 colonnes maximum en portrait.

### Encadrés
```html
<div class="callout">Information ou point clé.</div>
<div class="callout success"><strong>Recommandation :</strong> …</div>
<div class="callout warning"><strong>Attention :</strong> …</div>
<div class="callout danger"><strong>Risque :</strong> …</div>
```
1 à 3 phrases par encadré. Pas plus de 2 encadrés par page.

### Deux colonnes
```html
<table class="layout"><tr>
  <td>Colonne gauche</td>
  <td>Colonne droite</td>
</tr></table>
```
C'est **la seule** façon de faire des colonnes (pas de flex, grid, float).

### Divers
| Besoin | Code |
|---|---|
| Saut de page | `<div class="page-break"></div>` |
| Étiquette | `<span class="badge">Nouveau</span>` |
| Image | `<figure><img src="chemin/local.png" alt="…"><figcaption>Figure 1 – Légende</figcaption></figure>` |
| Signature | `<div class="signature"><p>Antananarivo, le 10 octobre 2026</p><p><strong>Nom Prénom</strong><br>Fonction</p></div>` |

En DOCX, utilise des images **locales** ou `data:image/...` : les URL `http(s)://`
ne sont pas intégrées (avertissement explicite).

### Graphiques (barres, courbes, camembert, anneau)

Pour insérer un graphique à partir de chiffres réels : écris une **ancre vide** à
l'endroit voulu, puis déclare les données dans le paramètre `graphiques` du tool.

```html
<h2>1. Résultats</h2>
<p>Le CA 2026 dépasse l'objectif de 8 %.</p>
<figure data-graphique="ca-trimestres"></figure>
<div class="callout success"><strong>Lecture :</strong> le T4 porte la croissance.</div>
```

```json
"graphiques": [{
  "id": "ca-trimestres",
  "type": "bar",
  "titre": "CA par trimestre (M MGA)",
  "labels": ["T1", "T2", "T3", "T4"],
  "series": [{"nom": "2025", "valeurs": [120, 180, 150, 190]},
             {"nom": "2026", "valeurs": [160, 210, 240, 250]}],
  "y_label": "M MGA",
  "legende": "Figure 1 – Évolution du chiffre d'affaires"
}]
```

| Règle | Détail |
|---|---|
| Chiffres d'abord | Ce paramètre sert à **représenter des données réelles** : toi, tu fournis uniquement des chiffres lus ou donnés |
| `id` unique | Le corps et `graphiques` doivent se correspondre exactement : ancre sans spec ou spec sans ancre = erreur explicite |
| `type` | `bar` (comparer des séries) · `line` (évolution) · `pie` (répartition, ≤ 5–6 parts) · `donut` (répartition + total au centre) |
| `series` / `valeurs` | `series` pour bar/line, `valeurs` pour pie/donut — jamais les deux |
| Valeurs | Issues d'une lecture (`search_*`, `read_document`) ou de l'utilisateur : jamais inventées ; en barres, les négatives sont acceptées (axe zéro tracé) |
| PDF / DOCX | PDF : graphique **vectoriel** (net à l'impression). DOCX : converti en PNG haute résolution automatiquement |
| Placement | L'ancre se place APRÈS le paragraphe qui commente le graphique ; chaque figure porte sa `legende` (figcaption) |

### Schémas et illustrations (SVG écrit par toi)

Un graphique représente des **chiffres** ; un **schéma** représente une **idée** :
organigramme, chaîne de responsabilités, flux d'un processus, cycle, frise
chronologique, carte mentale, schéma technique. Dans ce cas, écris toi-même un
`<svg>` inline — c'est autorisé et attendu.

```html
<h2>2. Organisation de la mission</h2>
<figure>
  <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 400 150" role="img">
    <title>Organigramme de l'équipe</title>
    <rect x="140" y="8" width="120" height="32" rx="6" fill="#1E3A5F"/>
    <text x="200" y="28" text-anchor="middle" font-family="Arial" font-size="11" fill="#ffffff">Chef de mission</text>
    <line x1="200" y1="40" x2="200" y2="62" stroke="#333" stroke-width="1.5"/>
    <line x1="70" y1="62" x2="330" y2="62" stroke="#333" stroke-width="1.5"/>
    <line x1="70" y1="62" x2="70" y2="84" stroke="#333" stroke-width="1.5"/>
    <rect x="20" y="84" width="100" height="30" rx="6" fill="#EFF6FF" stroke="#2563EB"/>
    <text x="70" y="103" text-anchor="middle" font-family="Arial" font-size="11" fill="#1F2937">Formateur</text>
    <rect x="280" y="84" width="100" height="30" rx="6" fill="#EFF6FF" stroke="#2563EB"/>
    <text x="330" y="103" text-anchor="middle" font-family="Arial" font-size="11" fill="#1F2937">Logistique</text>
  </svg>
  <figcaption>Figure 2 – Organisation de l'équipe</figcaption>
</figure>
```

| Règle | Détail |
|---|---|
| `viewBox` | Obligatoire en pratique : c'est lui qui met le schéma à l'échelle de la page. `xmlns` est ajouté s'il manque |
| `<title>` | Donne-la : elle devient le **texte alternatif** de l'image en DOCX |
| Couleurs explicites | `fill` / `stroke` sur chaque forme : le thème ne colore **pas** le SVG. Reprends les teintes du thème (`#1E3A5F`, `#2563EB`, `#EFF6FF` en corporate) |
| Texte dans le SVG | `<text>` avec `font-family="Arial"` et `font-size` en px — le SVG ne connaît pas les styles du thème |
| Flèches | Un `<defs><marker id="fleche">…</marker></defs>` puis `marker-end="url(#fleche)"` sur la ligne — la référence interne `#…` est acceptée |
| Interdits | `<script>`, `<foreignObject>`, animation (`<animate>`) ; toute référence `http(s)://` ou `file://` est **retirée** (un schéma ne dépend d'aucune ressource distante) |
| Taille | Raisonnable : un schéma plus large que la page est réduit (PDF), une image plus haute que la page est réduite aussi (DOCX) |
| PDF / DOCX | PDF : schéma **vectoriel** (net à l'impression). DOCX : converti en PNG haute résolution — un livrable à remettre en PDF reste le meilleur choix pour un schéma |

Un schéma **remplace** un paragraphe explicatif : place-le après la phrase qui le
commente, dans un `<figure>` avec sa `<figcaption>`. N'écris pas de schéma
décoratif, et n'y mets aucune donnée inventée.

### Illustre par défaut (même sans demande explicite)

Un document long se lit mieux **illustré**. Quand le CONTENU s'y prête, ajoute
toi-même le visuel qui remplace ou résume le texte — sans attendre qu'on te le
demande. L'utilisateur doit comprendre d'un coup d'œil ce qu'un paragraphe
explique en dix lignes :

| Ce que dit le texte | Visuel à ajouter |
|---|---|
| étapes, phases, cycle, processus ordonnés | frise / cycle en `<svg>` inline |
| comparaison de chiffres (catégories, périodes) | graphique `bar` / `line` via `graphiques=[…]` |
| répartition d'un total en parts | `pie` / `donut` |
| structure, acteurs, responsabilités | organigramme `<svg>` |
| flux, circuit entre entités | schéma à flèches `<svg>` |
| évolution dans le temps | courbe `line` |
| plan, jalons d'un projet | frise + `table.data` de détail |

Les garde-fous, inchangés mais **décisifs** :

- **Données réelles uniquement** : un visuel sans chiffres réels à montrer ne
  s'ajoute pas — et jamais de valeur inventée pour « remplir » un graphique ;
- **1 visuel par idée** : illustre l'idée principale d'une section, pas chaque
  phrase ; pas de visuel décoratif de remplissage ;
- toujours une `<figcaption>` (« Figure N – … »), placée après la phrase qui
  commente le visuel ;
- un tableau court (≤ 4 lignes) reste un tableau : ne double pas l'information
  en tableau + graphique ;
- **PPTX** : une diapositive visuelle vaut mieux qu'une diapositive à 7 puces —
  mets la figure ou le graphique, et déplace le commentaire à l'oral dans
  `aside.notes`. Une figure seule ou un bandeau large passe automatiquement
  pleine largeur dans le deck.

### Présentation (format="pptx") — une idée par diapositive

Quand l'utilisateur demande une **présentation** (deck, slides, PowerPoint),
même outil avec `format="pptx"` et un `contenu_html` en diapositives :

```html
<section class="cover">
  <div class="cover-label">Présentation</div>
  <h1 class="cover-title">Offre de formation DATA 2026</h1>
  <p class="cover-subtitle">Proposition technique</p>
  <div class="cover-meta">CARSO · Octobre 2026</div>
</section>
<section class="slide">
  <h1>Synthèse</h1>
  <p class="lead">Le message clé en une phrase.</p>
  <ul><li>Puce courte.</li><li>Puce courte.</li></ul>
  <table class="kpi"><tr>
    <td><span class="kpi-value">5 j</span><span class="kpi-label">Durée</span></td>
  </tr></table>
  <aside class="notes">Ce que je dis à l'oral — n'apparaît pas sur la diapo.</aside>
</section>
```

| Règle | Pourquoi |
|---|---|
| `<section class="cover">` en premier | la couverture ouvre le deck |
| UNE `<section class="slide">` par diapositive, `h1` = titre | une diapositive sans titre est muette |
| 6 puces maximum, puce ≤ 180 caractères, ≤ 4 chiffres clés | une diapositive porte une idée, pas un document |
| tableau ≤ 6 colonnes et ≤ 10 lignes | le reste est illisible projeté |
| `aside.notes` sur chaque diapositive de contenu | notes présentateur PowerPoint |
| `figure`/ancre `data-graphique` admises | converties en image PNG (PowerPoint ne lit pas le SVG) |
| une idée visuelle = une diapo figure, texte en `notes` | une diapo illustrée se lit ; une diapo à 7 puces se saute |
| figure seule ou bandeau large (frise, cycle) | automatiquement PLEINE LARGEUR dans la diapositive |

Le moteur **refuse** une diapositive qui déborderait (« scinde la diapositive »)
plutôt que de rogner ton texte : coupe-la en deux. Le retour porte
`diapositives` (et non `pages`) ; `render_document` ne prévisualise pas un PPTX —
relis le contenu de tes diapositives avant de présenter. `orientation` et
`css_supplementaire` n'ont aucun effet sur un deck (16:9 fixe).

## 4. Recettes par type de document

| Type | Structure recommandée |
|---|---|
| Présentation / deck | `format="pptx"` : cover → synthèse (lead + kpi) → contexte → solution → planning (table.data) → conclusion (callout) |
| Rapport / étude | cover → toc → synthèse (kpi + callout success) → sections numérotées → conclusion → annexes |
| Offre / proposition | cover → contexte du besoin → solution proposée → planning (table.data) → moyens (table.data + tfoot) → conditions → signature |
| Devis / facture | pas de cover ; `table.layout` (émetteur / client) → h1 « Devis n° … » → table.data avec tfoot (HT, TVA, TTC) → conditions de paiement |
| Compte rendu | pas de cover ; h1 + lead (date, lieu, participants) → points abordés (h2) → décisions (callout success) → actions (table.data : action / responsable / échéance) |
| Lettre | pas de cover ; `table.layout` (expéditeur / destinataire) → objet en `<strong>` → corps → signature |
| CV | h1 nom + lead poste → `table.layout` (compétences / expériences) → sections courtes |
| Note / mémo (1–2 pages) | h1 → lead → 2 ou 3 h2 → callout final. Ni cover ni toc |
| Fiche technique / checklist | h1 → paires (table.layout) → table.data des points de contrôle → callout d'attention |

## 5. Lire le résultat de l'outil

Le retour porte `statut`, `nom`, `type_document`, `pages`, `avertissements`,
`conseil` et `documents` :

```json
{
  "statut": "proposed",
  "nom": "bilan_t3_2026.pdf",
  "documents": {"principal": "…", "format": "pdf"},
  "pages": 4,
  "avertissements": [],
  "conseil": "Rendu sans avertissement : vérifie tout de même l'aperçu des pages."
}
```

- **Le document naît en PROPOSITION** : il n'est officiel qu'après validation
  humaine. Ne l'annonce jamais comme définitif.
- Pour un DOCX, `documents.pdf` porte le **PDF jumeau** déposé en même temps :
  c'est lui qui donne l'aperçu (`render_document` ne rend que des PDF).
- **Corrige chaque avertissement** puis régénère :

| Avertissement | Correction |
|---|---|
| Balise `<style>` ignorée | Supprime le `<style>`, utilise les classes |
| flex/grid/absolute/float détecté | Remplace par `table.layout` ou `table.kpi` |
| Styles inline de couleur/police | Supprime les `style="…"` |
| Syntaxe Markdown détectée | `**x**` → `<strong>x</strong>`, `## x` → `<h2>x</h2>` |
| Placeholder détecté (`[Nom]`, `XXX`, lorem ipsum) | Mets la vraie valeur, ou demande-la à l'utilisateur |
| Aucun titre `<h1>` | Ajoute une hiérarchie de titres |
| Formules creuses | Reformule de façon concrète |
| Restes de conversation d'assistant | Supprime « Bien sûr ! », « Voici votre… » |
| Tableau de N colonnes | `orientation: "paysage"` ou découpe le tableau |
| Tableau sans `<thead>` | Ajoute la ligne d'en-tête |
| Image distante non intégrée (DOCX) | Image locale ou `data:image` |
| Ancre de graphique en double / sans id | Un `id` unique par graphique, une seule ancre chacun |
| SVG sans viewBox ni largeur | Ajoute `viewBox="0 0 800 400"` : sans lui, le schéma ne se met pas à l'échelle |
| `<foreignObject>` retiré du SVG | Dessine avec les primitives SVG (rect, circle, path, text) — pas de HTML dans un schéma |
| Référence distante retirée du SVG | Un schéma n'embarque que du dessin : pas d'URL `http`, pas de fichier externe |
| Attribut d'événement retiré | `onclick`/`onload` n'ont aucun sens dans un document : supprime-les |
| SVG dans un DOCX | Attendu : converti en image. Pour un schéma vectoriel net, livre en `pdf` |
| Beaucoup de contenu sur une seule page | Ajoute des sauts de page / découpe en sections |

- Ensuite : `render_document` pour VOIR les pages, puis
  `proposer_telechargement_document` avec l'identifiant **réel**.
- Quand le document est bon, donne à l'utilisateur le **nombre de pages** et un
  résumé en une phrase. N'affiche pas le HTML sauf si on te le demande.

## 6. Règles de qualité du contenu

- Phrases concrètes : faits, chiffres, dates, noms, exemples. Pas de remplissage.
- Paragraphes de 2 à 5 phrases, une idée par paragraphe.
- Varier les composants : pas plus de 2 listes à puces d'affilée ; chaque page doit
  avoir au moins un élément visuel (kpi, tableau, encadré, graphique) quand le contenu s'y prête.
- Numéroter les sections des documents longs (1., 1.1…).
- Chiffres au format français : `1 250`, `4 800 €`, `12,5 %`.
- **Jamais le tiret cadratin « — »** dans un document : le remplacer par « : »,
  une virgule ou des parenthèses (le moteur remonte un avertissement et il
  faudra régénérer).
- Éviter : « Il est important de noter que », « Dans un monde en constante
  évolution », « jouer un rôle crucial », « En somme », « Plongeons »,
  « N'hésitez pas à ».
- Jamais de Markdown dans le HTML. Jamais de placeholders `[Nom]` : demande
  l'information ou utilise « (à compléter) » en le signalant à l'utilisateur.
- **Aucune donnée inventée** : les montants, dates, effectifs, lieux et
  disponibilités viennent d'une lecture réelle (`search_*`, `read_document`) ou de
  l'utilisateur. Une donnée absente s'écrit « (à compléter) » — jamais un blanc
  silencieux, jamais une valeur plausible.
- Le `type_document` de dépôt est un vocabulaire **fermé**, à reprendre
  exactement : `offre`, `appel_proposition`, `cv`, `fiche_technique`,
  `fiche_presence`, `liste_beneficiaires`, `checklist`, `rapport`,
  `justificatif`, `budget`, `template`, `autre`, `non_classe`. Les noms de
  profils (`offre_financiere`, `offre_technique`) n'en sont PAS : pour une offre
  technique ou financière, le type est `offre` — le profil se règle dans le
  contenu du document, pas dans le type.
- L'ancre métier (`ancre` + `entity_id`) n'est renseignée que si l'objet existe
  réellement : **jamais d'identifiant inventé**.

## 7. Contraintes techniques

- Interdit : `display:flex`, `grid`, `position:absolute`, `float`, `<script>`,
  `<style>`, polices externes.
- Le DOCX est un vrai document Word (styles Titre 1/2/3, sommaire Word, tableaux
  colorés, numéros de page). Word peut proposer de **mettre à jour les champs** à
  l'ouverture : c'est normal, préviens l'utilisateur d'accepter.
- Le PDF est le rendu le plus fidèle (navigateur Chromium côté serveur) :
  choisis-le par défaut.
- Le rendu prend quelques secondes (chaque appel de l'outil) : c'est normal,
  n'appelle pas l'outil plusieurs fois avec le même contenu.

## 8. Checklist avant d'appeler l'outil

- [ ] Hiérarchie H1 > H2 > H3 respectée, sections numérotées si le document est long
- [ ] Couverture si ≥ 3 pages, sommaire (vide) si ≥ 5 pages
- [ ] Chiffres clés en `table.kpi`, tableaux en `table.data` avec `<thead>` et classe `num`
- [ ] Au moins un `callout` pour le message principal
- [ ] Aucun `<style>`, aucun `style="…"`, aucun Markdown, aucun placeholder
- [ ] `titre` et `auteur` renseignés, `theme` et `orientation` choisis
- [ ] `type_document` pris dans le vocabulaire fermé, ancre seulement si réelle

## 9. Exemple minimal complet

```json
{
  "titre": "Compte rendu – Comité projet du 9 octobre 2026",
  "format": "pdf",
  "auteur": "Équipe projet",
  "theme": "corporate",
  "type_document": "rapport",
  "contenu_html": "<h1>Comité projet – 9 octobre 2026</h1><p class=\"lead\">Participants : C. Martin, J. Diallo, L. Petit. Durée : 1 h.</p><h2>1. Avancement</h2><p>Le lot 2 est livré avec une semaine d'avance. Le lot 3 démarre le 14 octobre.</p><div class=\"callout success\"><strong>Décision :</strong> la recette du lot 2 est validée.</div><h2>2. Actions</h2><table class=\"data\"><thead><tr><th>Action</th><th>Responsable</th><th>Échéance</th></tr></thead><tbody><tr><td>Planifier la recette du lot 3</td><td>J. Diallo</td><td>18/10/2026</td></tr><tr><td>Mettre à jour le budget</td><td>L. Petit</td><td>21/10/2026</td></tr></tbody></table>"
}
```
