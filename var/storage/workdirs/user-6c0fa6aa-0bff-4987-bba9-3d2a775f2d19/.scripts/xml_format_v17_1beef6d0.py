import pathlib
from docx import Document
from docx.text.paragraph import Paragraph
from docx.table import Table

parent = pathlib.Path.cwd().parent
fichiers = sorted(parent.glob("modele-OFFRE-TECH-L01*.docx"))
print("fichiers:", [f.name for f in fichiers])

def version(f):
    import re
    m = re.search(r"_v(\d+)\.docx$", f.name)
    return int(m.group(1)) if m else 0

cible = max(fichiers, key=version)
print("cible:", cible.name)

doc = Document(str(cible))
ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
body = doc.element.body

def dump_p(child, label):
    p = Paragraph(child, doc)
    ppr = child.find(ns + "pPr")
    xml = ppr.xml if ppr is not None else "AUCUN pPr"
    print(f"--- {label} :: {p.text[:60]!r}")
    print("pPr:", xml.replace("\n", " ")[:600])
    for r in p.runs[:3]:
        rpr = r._r.find(ns + "rPr")
        print("   rPr:", (rpr.xml.replace("\n"," ")[:300] if rpr is not None else "aucun"))

idx = 0
for child in body.iterchildren():
    tag = child.tag.replace(ns, "")
    idx += 1
    if tag != "p":
        continue
    p = Paragraph(child, doc)
    t = p.text.strip()
    if t.startswith("3. Objectifs") or t.startswith("•\tRenforcer les capacités de diff") or t.startswith("Les résultats attendus de la formation") or t.startswith("6.1. Vague 1") or t.startswith("Résumé de la note"):
        dump_p(child, f"[{idx:03d}] {t[:40]}")

# tableau : structure d'une cellule d'en-tête et d'une cellule de données
for child in body.iterchildren():
    if child.tag == ns + "tbl":
        t = Table(child, doc)
        print("=== TABLEAU", len(t.rows), "x", len(t.columns))
        for ri, row in enumerate(t.rows[:2]):
            for ci, c in enumerate(row.cells):
                tcpr = c._tc.find(ns + "tcPr")
                print(f"  r{ri}c{ci} tcPr:", (tcpr.xml.replace("\n"," ")[:400] if tcpr is not None else "aucun"))
                for par in c.paragraphs:
                    ppr = par._p.find(ns + "pPr")
                    print(f"     pPr:", (ppr.xml.replace("\n"," ")[:250] if ppr is not None else "aucun"))
                    for r in par.runs[:2]:
                        rpr = r._r.find(ns + "rPr")
                        print(f"     rPr:", (rpr.xml.replace("\n"," ")[:250] if rpr is not None else "aucun"))
            if ri == 0:
                break
        break

# en-tete / pied
for si, sec in enumerate(doc.sections):
    print("=== SECTION", si)
    for nom, part in (("ENTETE", sec.header), ("PIED", sec.footer)):
        print(nom, "linked:", part.is_linked_to_previous)
        for p in part.paragraphs:
            print("   ", repr(p.text[:90]))
            for r in p.runs[:2]:
                rpr = r._r.find(ns + "rPr")
                print("      rPr:", (rpr.xml.replace("\n"," ")[:250] if rpr is not None else "aucun"))
