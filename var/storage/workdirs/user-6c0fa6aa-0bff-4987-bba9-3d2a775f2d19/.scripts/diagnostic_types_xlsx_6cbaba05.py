import pathlib, zipfile, re
import xml.etree.ElementTree as ET

cwd = pathlib.Path.cwd()
parent = cwd.parent
print("cwd:", cwd)
print("parent:", parent)
for p in sorted(parent.rglob("*.xlsx")):
    print("XLSX:", p.relative_to(parent), p.stat().st_size)

cible = None
for p in sorted(parent.rglob("*v7*.xlsx")):
    cible = p
if cible is None:
    for p in sorted(parent.rglob("*.xlsx")):
        cible = p
print("CIBLE:", cible)

NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
with zipfile.ZipFile(cible) as z:
    noms = z.namelist()
    wb = ET.fromstring(z.read("xl/workbook.xml"))
    sheets = [(s.get("name"), s.get("sheetId"), s.get(
        "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"))
        for s in wb.iter(NS + "sheet")]
    rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
    relmap = {r.get("Id"): r.get("Target") for r in rels}
    print("\nFEUILLES:", [s[0] for s in sheets])

    def dump(nom_feuille, maxl=12):
        cible_rel = None
        for n, sid, rid in sheets:
            if n == nom_feuille:
                cible_rel = relmap[rid]
        if cible_rel is None:
            print("feuille absente:", nom_feuille)
            return
        chemin = "xl/" + cible_rel.lstrip("/").replace("xl/", "")
        data = z.read(chemin)
        root = ET.fromstring(data)
        print("\n=== " + nom_feuille + " ===")
        for row in list(root.iter(NS + "row"))[:maxl]:
            cells = []
            for c in row.iter(NS + "c"):
                ref = c.get("r")
                t = c.get("t") or "n"
                s = c.get("s")
                f = c.find(NS + "f")
                v = c.find(NS + "v")
                isel = c.find(NS + "is")
                val = v.text if v is not None else (
                    "".join(x.text or "" for x in isel.iter(NS + "t")) if isel is not None else None)
                cells.append((ref, t, s, (f.text if f is not None else None), val))
            print("L" + row.get("r") + ":", cells)

    dump("Personnel et honoraires")
    dump("Récapitulatif général")
    dump("Ventilation par phase")
