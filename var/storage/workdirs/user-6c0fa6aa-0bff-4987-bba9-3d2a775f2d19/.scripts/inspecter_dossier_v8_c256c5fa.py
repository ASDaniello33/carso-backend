import pathlib, zipfile
import xml.etree.ElementTree as ET

NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
base = pathlib.Path.cwd().parent
print("dossier:", base.name)
for p in sorted(base.glob("*v8*")):
    print(" -", p.name, p.stat().st_size, "octets")

for p in sorted(base.glob("*v8*.xlsx")):
    print("\n###", p.name)
    with zipfile.ZipFile(p) as z:
        root = ET.fromstring(z.read("xl/worksheets/sheet9.xml"))
        for row in list(root.iter(NS + "row"))[:11]:
            cells = []
            for c in row.iter(NS + "c"):
                v = c.find(NS + "v")
                f = c.find(NS + "f")
                if f is not None or v is not None:
                    cells.append((c.get("r"), (f.text if f is not None else None),
                                  (v.text if v is not None else None)))
            if cells:
                print("L" + row.get("r"), cells)
