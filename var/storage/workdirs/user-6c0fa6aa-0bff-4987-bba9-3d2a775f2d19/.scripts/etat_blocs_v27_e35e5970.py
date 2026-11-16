import pathlib, re
from docx import Document
from docx.text.paragraph import Paragraph

parent = pathlib.Path.cwd().parent
fichiers = list(parent.glob("modele-OFFRE-TECH-L01*.docx"))
def version(f):
    m = re.search(r"_v(\d+)\.docx$", f.name)
    return int(m.group(1)) if m else 0
cible = max(fichiers, key=version)
print("fichier:", cible.name)

doc = Document(str(cible))
ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
body = doc.element.body
enfants = list(body.iterchildren())

print("\n=== TOUTES LES PUCES : index body (1-based = numero block) et conformite ===")
for i, ch in enumerate(enfants):
    if ch.tag != ns + "p":
        continue
    t = Paragraph(ch, doc).text
    if t.strip().startswith("•"):
        ppr = ch.find(ns + "pPr")
        ind = ppr.find(ns + "ind") if ppr is not None else None
        attrs = {re.sub(r'\{.*\}', '', k): v for k, v in (ind.attrib.items() if ind is not None else [])}
        tab = "\t" in t
        ok = attrs.get("left") == "360" and attrs.get("hanging") == "360" and tab
        print(f"{'OK ' if ok else '!! '}block_{i+1:03d} ind={attrs} tab={tab} :: {t[:75]}")

print("\n=== TABLEAUX : index body et contenu 1re cellule ===")
for i, ch in enumerate(enfants):
    if ch.tag == ns + "tbl":
        from docx.table import Table
        t = Table(ch, doc)
        print(f"  block_{i+1:03d} :: {len(t.rows)} lignes x {len(t.columns)} col :: {t.rows[0].cells[0].text!r}")
