import pathlib, zipfile
import xml.etree.ElementTree as ET

cible = pathlib.Path.cwd().parent / "offre_financiere_hi_lab_budget_detaille_v7.xlsx"
NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"

with zipfile.ZipFile(cible) as z:
    print("PARTIES:", [n for n in z.namelist() if n.startswith("xl/")][:40])
    wb = z.read("xl/workbook.xml").decode("utf-8")
    print("\n--- workbook.xml (extrait calcPr) ---")
    i = wb.find("calcPr")
    print(wb[max(0, i-200):i+300] if i > 0 else "PAS de calcPr")

    st = ET.fromstring(z.read("xl/styles.xml"))
    numfmts = {}
    for nf in st.iter(NS + "numFmt"):
        numfmts[nf.get("numFmtId")] = nf.get("formatCode")
    print("\n--- numFmts personnalisés ---")
    for k, v in numfmts.items():
        print(k, "=", repr(v))

    xfs = st.find(NS + "cellXfs")
    print("\n--- cellXfs (index: numFmtId / formatCode) ---")
    for idx, xf in enumerate(xfs):
        nid = xf.get("numFmtId")
        print(idx, "->", nid, repr(numfmts.get(nid, "(builtin)")))
