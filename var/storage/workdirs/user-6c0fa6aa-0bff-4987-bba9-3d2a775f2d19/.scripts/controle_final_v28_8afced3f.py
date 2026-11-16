import pathlib, re
from docx import Document
from docx.text.paragraph import Paragraph
from docx.table import Table

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

ko = []
for i, ch in enumerate(enfants):
    if ch.tag != ns + "p":
        continue
    t = Paragraph(ch, doc).text
    if t.strip().startswith("•"):
        ppr = ch.find(ns + "pPr")
        ind = ppr.find(ns + "ind") if ppr is not None else None
        attrs = {re.sub(r'\{.*\}', '', k): v for k, v in (ind.attrib.items() if ind is not None else [])}
        tab = "\t" in t
        if not (attrs.get("left") == "360" and attrs.get("hanging") == "360" and tab):
            ko.append((i+1, attrs, tab, t[:60]))
print("puces non conformes :", len(ko))
for k in ko:
    print("   !!", k)

print("\n=== STRUCTURE (titres et sections) ===")
for i, ch in enumerate(enfants):
    if ch.tag == ns + "p":
        t = Paragraph(ch, doc).text.strip()
        if re.match(r"^(\d+\.\s|\d+\.\d+\.\s|Jour \d|2\.1\.|1\.1\.|1\.2\.|6\.1\.|6\.2\.)", t):
            print(f"  block_{i+1:03d} :: {t[:95]}")

print("\n=== TABLEAUX ===")
for i, ch in enumerate(enfants):
    if ch.tag == ns + "tbl":
        t = Table(ch, doc)
        print(f"  block_{i+1:03d} :: {len(t.rows)}x{len(t.columns)} :: {t.rows[0].cells[0].text[:30]!r} | {t.rows[-1].cells[-1].text[:45]!r}")

print("\n=== COUVERTURE / LOGO ===")
img = 0
for i, ch in enumerate(enfants[:22]):
    if ch.tag == ns + "p":
        ppr = ch.find(ns + "pPr")
        if ppr is not None:
            for dr in ch.iter():
                if dr.tag.endswith("}drawing") or dr.tag.endswith("}blip"):
                    img += 1
        t = Paragraph(ch, doc).text.strip()
        if t:
            print(f"  block_{i+1:03d} :: {t[:80]}")
print("  images detectees dans la couverture :", img)

print("\n=== JAUGE / ETAT FINAL ===")
print("  sections :", len(doc.sections))
s = doc.sections[0]
print("  page :", s.page_width, "x", s.page_height)
print("  marges (twips) :", s.top_margin, s.bottom_margin, s.left_margin, s.right_margin)
print("  polices utilisees :", set(r.font.name for p in doc.paragraphs for r in p.runs if r.font.name))
