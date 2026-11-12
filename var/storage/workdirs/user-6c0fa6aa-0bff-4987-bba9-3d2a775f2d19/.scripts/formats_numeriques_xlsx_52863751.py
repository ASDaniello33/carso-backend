import pathlib, zipfile
import xml.etree.ElementTree as ET

NS_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
NS = "{" + NS_MAIN + "}"
ET.register_namespace("", NS_MAIN)
ET.register_namespace("r", "http://schemas.openxmlformats.org/officeDocument/2006/relationships")

src = pathlib.Path.cwd().parent / "offre_financiere_hi_lab_budget_detaille_v8.xlsx"
dst = pathlib.Path.cwd() / "offre_financiere_hi_lab_budget_detaille_v8.xlsx"

with zipfile.ZipFile(src) as z:
    noms = z.namelist()
    contenu = {n: z.read(n) for n in noms}

st = ET.fromstring(contenu["xl/styles.xml"])
numfmts = st.find(NS + "numFmts")
print("--- avant ---")
for nf in numfmts.iter(NS + "numFmt"):
    print(nf.get("numFmtId"), "=", repr(nf.get("formatCode")))

# Ariary : pas de decimales (unite monetaire sans centimes en pratique)
for nf in numfmts.iter(NS + "numFmt"):
    if nf.get("numFmtId") == "164":
        nf.set("formatCode", '#,##0 "MGA"')

# Ajouter un format entier a separateur de milliers pour les quantites
existants = {nf.get("numFmtId") for nf in numfmts.iter(NS + "numFmt")}
if "165" not in existants:
    nouveau = ET.SubElement(numfmts, NS + "numFmt")
    nouveau.set("numFmtId", "165")
    nouveau.set("formatCode", "#,##0")
numfmts.set("count", str(len(list(numfmts.iter(NS + "numFmt")))))

contenu["xl/styles.xml"] = ET.tostring(st, encoding="UTF-8", xml_declaration=True)

with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as z:
    for n in noms:
        z.writestr(n, contenu[n])

print("ecrit:", dst.name, dst.stat().st_size, "octets")

with zipfile.ZipFile(dst) as z:
    st2 = ET.fromstring(z.read("xl/styles.xml"))
    print("--- apres ---")
    for nf in st2.iter(NS + "numFmt"):
        print(nf.get("numFmtId"), "=", repr(nf.get("formatCode")))
    # controle integrite : toutes les parties presentes
    print("parties:", len(z.namelist()))
    bad = z.testzip()
    print("integrite zip:", "OK" if bad is None else bad)
