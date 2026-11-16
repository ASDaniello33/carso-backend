import pathlib
from docx import Document
from docx.shared import Pt

base = pathlib.Path.cwd()
parent = base.parent
print("CWD:", base)
print("PARENT:", parent)

candidats = []
for p in sorted(parent.rglob("*.docx")):
    candidats.append(p)
    print("DOCX:", p.relative_to(parent), p.stat().st_size)

cible = None
for p in candidats:
    if p.name.startswith("modele-OFFRE-TECH-L01"):
        cible = p
        break
if cible is None:
    print("copie de travail introuvable")
    raise SystemExit(1)

doc = Document(str(cible))
body = doc.element.body
ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

def fmt_run(r):
    f = r.font
    col = None
    try:
        if f.color is not None and f.color.rgb is not None:
            col = str(f.color.rgb)
    except Exception:
        col = None
    return f"b={f.bold} i={f.italic} u={f.underline} sz={f.size.pt if f.size else None} police={f.name} col={col}"

idx = 0
from docx.text.paragraph import Paragraph
from docx.table import Table

for child in body.iterchildren():
    tag = child.tag.replace(ns, "")
    idx += 1
    if tag == "p":
        par = Paragraph(child, doc)
        txt = par.text.strip()
        runs = [fmt_run(r) for r in par.runs if r.text.strip()]
        if txt or any(r.get_or_add_pPr().find(ns+'numPr') is not None for r in [child]):
            print(f"[{idx:03d}] P style={par.style.name!r} align={par.alignment} :: {txt[:70]!r}")
            for rr in runs[:3]:
                print(f"        run: {rr}")
    elif tag == "tbl":
        t = Table(child, doc)
        print(f"[{idx:03d}] TABLEAU style={t.style.name if t.style else None} lignes={len(t.rows)} cols={len(t.columns)}")
        tblpr = child.find(ns + "tblPr")
        if tblpr is not None:
            bord = tblpr.find(ns + "tblBorders")
            if bord is not None:
                for b in bord:
                    print("        bordure", b.tag.replace(ns, ""), b.get(ns + "sz"), b.get(ns + "val"), b.get(ns + "color"))
        # coloration de la premiere ligne
        for ri, row in enumerate(t.rows[:2]):
            cells = []
            for c in row.cells:
                shd = c._tc.find(ns + "tcPr")
                fond = None
                if shd is not None:
                    s = shd.find(ns + "shd")
                    if s is not None:
                        fond = s.get(ns + "fill")
                runs = []
                for par in c.paragraphs:
                    for r in par.runs:
                        if r.text.strip():
                            runs.append(fmt_run(r))
                cells.append(f"{c.text.strip()[:26]!r} fond={fond} {runs[:1]}")
            print(f"        ligne{ri}: {cells}")
