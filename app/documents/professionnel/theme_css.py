"""Thème CSS du générateur professionnel—inliné, jamais lu sur le disque.

Le module d'origine lisait ``assets/theme.css`` via ``importlib.resources``.
CARSO n'embarque aujourd'hui aucun fichier de données dans ``app/`` et
``pyproject.toml`` ne déclare aucun ``package-data``—un asset CSS serait donc
absent d'une installation (et le thème silencieusement vide). La feuille est ici
une constante—même contenu, aucune dépendance à l'empaquetage.

Les variables ``--primary``, ``--accent``, ``--soft``, ``--callout`` et
``--cover-h`` sont ajoutées par ``html_builder`` selon le thème et l'orientation.
"""

from __future__ import annotations

__all__ = ["THEME_CSS"]

THEME_CSS = """\
:root { --text: #1F2937; --muted: #6B7280; --border: #E5E7EB; }

* { box-sizing: border-box; }
html {
  font-family: "Inter", "Segoe UI", "Helvetica Neue", Arial, sans-serif;
  font-size: 10.5pt; line-height: 1.55; color: var(--text);
  -webkit-print-color-adjust: exact; print-color-adjust: exact;
}
body { margin: 0; }
p { margin: 0 0 3mm; orphans: 3; widows: 3; }
strong, b { color: var(--primary); font-weight: 700; }
a { color: var(--accent); text-decoration: none; }
code { font-family: Consolas, "Courier New", monospace; font-size: 9pt; background: var(--soft);
       padding: 0 1mm; border-radius: 2px; }
pre { font-family: Consolas, "Courier New", monospace; font-size: 8.5pt; background: var(--soft);
      padding: 3mm; border-radius: 3px; white-space: pre-wrap; break-inside: avoid; }
hr { border: none; border-top: 1px solid var(--border); margin: 6mm 0; }

/* ===== Titres ===== */
h1, h2, h3, h4 { line-height: 1.25; font-weight: 700; break-after: avoid; page-break-after: avoid; }
h1 { font-size: 21pt; color: var(--primary); margin: 10mm 0 5mm; padding-bottom: 2.5mm;
     border-bottom: 2.5px solid var(--accent); }
body > h1:first-child, .page-break + h1 { margin-top: 0; }
h2 { font-size: 14pt; color: var(--primary); margin: 7mm 0 2.5mm; }
h3, h4 { font-size: 11.5pt; color: var(--accent); margin: 5mm 0 2mm; }
.lead { font-size: 12pt; color: var(--muted); margin-bottom: 5mm; }

/* ===== Listes ===== */
ul, ol { margin: 0 0 3mm; padding-left: 6mm; }
li { margin-bottom: 1.2mm; }
li::marker { color: var(--accent); font-weight: 700; }

/* ===== Couverture (rendue sur une page séparée, sans en-tête ni numéro) ===== */
.cover { position: relative; height: var(--cover-h); overflow: hidden;
         padding: calc(var(--cover-h) * 0.24) 22mm 20mm; background: var(--primary); color: #fff; }
.cover::after {
  content: "";
  position: absolute;
  right: -45mm;
  top: -45mm;
  width: 150mm;
  height: 150mm;
  border-radius: 50%;
  background: var(--accent);
  opacity: .35;
}
.cover-label { position: relative; z-index: 1; text-transform: uppercase; letter-spacing: .15em;
               font-size: 9pt; font-weight: 600; opacity: .8; margin-bottom: 6mm; }
.cover .cover-title, .cover h1 {
  position: relative;
  z-index: 1;
  font-size: 34pt;
  line-height: 1.1;
  color: #fff;
  border: none;
  padding: 0;
  margin: 0 0 6mm;
  max-width: 150mm;
}
.cover-subtitle { position: relative; z-index: 1; font-size: 14pt; opacity: .85; max-width: 140mm; }
.cover-meta {
  position: absolute;
  z-index: 1;
  left: 22mm;
  right: 22mm;
  bottom: 22mm;
  padding-top: 4mm;
  border-top: 1px solid rgba(255, 255, 255, .35);
  font-size: 10pt;
}
.cover strong, .cover b { color: #fff; }

/* ===== Sommaire (numéros de page calculés automatiquement) ===== */
.toc h2 { margin-top: 0; }
.toc a { display: flex; align-items: baseline; gap: 2mm; color: var(--text); padding: 1.6mm 0; }
.toc a.toc-n1 { font-weight: 600; }
.toc a.toc-n2 { padding-left: 6mm; font-size: 9.5pt; color: var(--muted); }
.toc .toc-points { flex: 1; border-bottom: 1px dotted #9CA3AF; }
.toc .toc-page {
  min-width: 6mm;
  text-align: right;
  color: var(--primary);
  font-variant-numeric: tabular-nums;
}

/* ===== Tableaux de données ===== */
table.data { width: 100%; border-collapse: collapse; font-size: 9.5pt; margin: 4mm 0 5mm; }
table.data thead { display: table-header-group; }
table.data th {
  background: var(--primary);
  color: #fff;
  text-align: left;
  font-weight: 600;
  padding: 2.5mm 3mm;
}
table.data th strong { color: #fff; }
table.data td { padding: 2.2mm 3mm; border-bottom: 1px solid var(--border); vertical-align: top; }
table.data tbody tr:nth-child(even) td { background: var(--soft); }
table.data tfoot td { font-weight: 700; border-top: 2px solid var(--primary); border-bottom: none; }
table.data .num { text-align: right; font-variant-numeric: tabular-nums; }
table.data tr { break-inside: avoid; }

/* ===== Chiffres clés ===== */
table.kpi { width: 100%; border-collapse: separate; border-spacing: 3mm 0; margin: 4mm 0 6mm; }
table.kpi td { background: var(--soft); border-top: 3px solid var(--accent); padding: 5mm 3mm;
               text-align: center; vertical-align: top; }
.kpi-value {
  display: block;
  font-size: 21pt;
  font-weight: 800;
  color: var(--primary);
  line-height: 1.1;
}
.kpi-label { display: block; margin-top: 1.5mm; font-size: 8pt; color: var(--muted);
             text-transform: uppercase; letter-spacing: .06em; }

/* ===== Mise en page en colonnes ===== */
table.layout { width: 100%; border-collapse: collapse; margin: 0 0 5mm; }
table.layout > tbody > tr > td, table.layout > tr > td { vertical-align: top; padding: 0 4mm 0 0; }

/* ===== Encadrés ===== */
.callout, blockquote {
  margin: 4mm 0 5mm;
  padding: 3.5mm 4.5mm;
  border-left: 4px solid var(--accent);
  background: var(--callout);
  border-radius: 0 4px 4px 0;
  break-inside: avoid;
}
.callout p:last-child, blockquote p:last-child { margin-bottom: 0; }
.callout.success { border-color: #059669; background: #ECFDF5; }
.callout.warning { border-color: #D97706; background: #FFFBEB; }
.callout.danger  { border-color: #DC2626; background: #FEF2F2; }

/* ===== Divers ===== */
.badge {
  display: inline-block;
  padding: .3mm 2mm;
  border-radius: 3mm;
  font-size: 8pt;
  font-weight: 600;
  background: var(--accent);
  color: #fff;
}
figure { margin: 5mm 0; text-align: center; break-inside: avoid; }
figure img, p img { max-width: 100%; max-height: 110mm; }
/* Un schéma écrit par l'agent est un SVG inline : il doit se replier dans la
   page (mêmes bornes que les images : largeur utile, 110 mm de haut).
   Mesuré : sans `max-height`, un schéma vertical de 400 x 900 débordait sur
   3 pages ; avec, il tient sur une page (62 x 110 mm), ratio conservé. */
figure svg, p svg { max-width: 100%; max-height: 110mm; height: auto; }
figcaption { font-size: 8.5pt; color: var(--muted); margin-top: 2mm; font-style: italic; }
.signature { margin-top: 12mm; margin-left: 55%; break-inside: avoid; }
.page-break { break-after: page; page-break-after: always; height: 0; }
"""
