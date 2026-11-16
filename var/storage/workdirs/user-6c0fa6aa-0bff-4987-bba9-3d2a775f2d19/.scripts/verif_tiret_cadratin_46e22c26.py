
from pypdf import PdfReader
import pathlib, re, json

base = pathlib.Path(".")
cands = []
for p in base.rglob("*.pdf"):
    cands.append(p)
print("PDF trouves:", [str(c) for c in cands])

for p in cands:
    if "hi_nov" not in p.name.lower():
        continue
    r = PdfReader(str(p))
    txt = "\n".join((pg.extract_text() or "") for pg in r.pages)
    hits = []
    for i, line in enumerate(txt.split("\n")):
        if "\u2014" in line:
            hits.append((i, line.strip()))
    print("Fichier:", p.name, "| pages:", len(r.pages), "| lignes avec tiret cadratin:", len(hits))
    for h in hits:
        print("  L", h[0], "->", h[1][:160])
