import pathlib, zipfile
import xml.etree.ElementTree as ET

NS_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
NS = "{" + NS_MAIN + "}"
ET.register_namespace("", NS_MAIN)
ET.register_namespace("r", "http://schemas.openxmlformats.org/officeDocument/2006/relationships")

base = pathlib.Path.cwd().parent
# v8_v2 = version dont les caches de formules sont CORRECTS (plages rectangulaires)
src = base / "offre_financiere_hi_lab_budget_detaille_v8_v2.xlsx"
dst = pathlib.Path.cwd() / "offre_financiere_hi_lab_budget_detaille_v8.xlsx"

with zipfile.ZipFile(src) as z:
    noms = z.namelist()
    contenu = {n: z.read(n) for n in noms}

# --- 1. formats numeriques : MGA sans decimales + entier a milliers ---
st = ET.fromstring(contenu["xl/styles.xml"])
numfmts = st.find(NS + "numFmts")
for nf in numfmts.iter(NS + "numFmt"):
    if nf.get("numFmtId") == "164":
        nf.set("formatCode", '#,##0 "MGA"')
existants = {nf.get("numFmtId") for nf in numfmts.iter(NS + "numFmt")}
if "165" not in existants:
    n = ET.SubElement(numfmts, NS + "numFmt")
    n.set("numFmtId", "165")
    n.set("formatCode", "#,##0")
numfmts.set("count", str(len(list(numfmts.iter(NS + "numFmt")))))
contenu["xl/styles.xml"] = ET.tostring(st, encoding="UTF-8", xml_declaration=True)

with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as z:
    for n in noms:
        z.writestr(n, contenu[n])

# --- 2. CONTROLE : chaque formule a-t-elle une valeur en cache coherente ? ---
with zipfile.ZipFile(dst) as z:
    wb = ET.fromstring(z.read("xl/workbook.xml"))
    rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
    relmap = {r.get("Id"): r.get("Target") for r in rels}
    feuilles = []
    for s in wb.iter(NS + "sheet"):
        rid = s.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")
        feuilles.append((s.get("name"), "xl/" + relmap[rid].lstrip("/").replace("xl/", "")))

    total_f = total_cache = 0
    anomalies = []
    for nom, chemin in feuilles:
        root = ET.fromstring(z.read(chemin))
        for c in root.iter(NS + "c"):
            f = c.find(NS + "f")
            if f is None or not f.text:
                continue
            total_f += 1
            v = c.find(NS + "v")
            if v is None or v.text in (None, ""):
                anomalies.append((nom, c.get("r"), f.text, "SANS CACHE"))
            else:
                total_cache += 1
                if c.get("t") not in (None, "n"):
                    anomalies.append((nom, c.get("r"), f.text, "type=" + str(c.get("t"))))
    print("formules:", total_f, "| avec cache numerique:", total_cache)
    print("anomalies:", anomalies if anomalies else "aucune")

    # controle arithmetique des totaux cles
    def lire(nom_feuille, refs):
        chemin = dict(feuilles)[nom_feuille]
        root = ET.fromstring(z.read(chemin))
        out = {}
        for c in root.iter(NS + "c"):
            if c.get("r") in refs:
                v = c.find(NS + "v")
                out[c.get("r")] = v.text if v is not None else None
        return out

    print("\nRecapitulatif B9/B10/B11 :", lire("Récapitulatif général", {"B9", "B10", "B11"}))
    print("Ventilation F10/F11 :", lire("Ventilation par phase", {"F10", "F11"}))
    print("Ventilation ligne 3 :", lire("Ventilation par phase", {"B3", "C3", "D3", "E3", "F3"}))
    print("Echeancier E6/E7 :", lire("Échéancier de paiement", {"E6", "E7"}))
    print("Personnel D9 :", lire("Personnel et honoraires", {"D9"}))
    print("Indicateurs B5/B7/B9/B11 :", lire("Coût par projet et indicateurs", {"B5", "B7", "B9", "B11"}))

    # styles : verifier que totaux et en-tetes sont distincts
    print("\n--- styles (s) des lignes cles du Recapitulatif ---")
    root = ET.fromstring(z.read(dict(feuilles)["Récapitulatif général"]))
    for row in root.iter(NS + "row"):
        if row.get("r") in ("2", "3", "9", "10", "11"):
            print("L" + row.get("r"), [(c.get("r"), c.get("s")) for c in row.iter(NS + "c")])

    print("\nparties:", len(z.namelist()), "| integrite:", "OK" if z.testzip() is None else "KO")
    print("graphiques:", [n for n in z.namelist() if "chart" in n and n.endswith(".xml")])
