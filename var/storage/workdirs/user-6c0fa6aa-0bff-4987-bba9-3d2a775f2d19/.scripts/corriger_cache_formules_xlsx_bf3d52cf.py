import pathlib, zipfile, re, ast, operator
import xml.etree.ElementTree as ET

NS_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
NS = "{" + NS_MAIN + "}"
ET.register_namespace("", NS_MAIN)
ET.register_namespace("r", "http://schemas.openxmlformats.org/officeDocument/2006/relationships")

src = pathlib.Path.cwd().parent / "offre_financiere_hi_lab_budget_detaille_v7.xlsx"
dst = pathlib.Path.cwd() / "offre_financiere_hi_lab_budget_detaille_v8.xlsx"

OPS = {ast.Add: operator.add, ast.Sub: operator.sub,
       ast.Mult: operator.mul, ast.Div: operator.truediv}


def eval_expr(s):
    node = ast.parse(s, mode="eval").body

    def ev(n):
        if isinstance(n, ast.Constant):
            return n.value
        if isinstance(n, ast.BinOp):
            return OPS[type(n.op)](ev(n.left), ev(n.right))
        if isinstance(n, ast.UnaryOp) and isinstance(n.op, ast.USub):
            return -ev(n.operand)
        raise ValueError("expr non supportee")
    return ev(node)


def expand(plage):
    a, b = plage.split(":")
    ca = re.match(r"([A-Z]+)(\d+)", a)
    cb = re.match(r"([A-Z]+)(\d+)", b)
    col1, row1 = ca.group(1), int(ca.group(2))
    row2 = int(cb.group(2))
    return [col1 + str(r) for r in range(row1, row2 + 1)]


with zipfile.ZipFile(src) as z:
    noms = z.namelist()
    contenu = {n: z.read(n) for n in noms}

wb = ET.fromstring(contenu["xl/workbook.xml"])
rels = ET.fromstring(contenu["xl/_rels/workbook.xml.rels"])
relmap = {r.get("Id"): r.get("Target") for r in rels}
feuilles = []
for s in wb.iter(NS + "sheet"):
    rid = s.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")
    chemin = "xl/" + relmap[rid].lstrip("/").replace("xl/", "")
    feuilles.append((s.get("name"), chemin))

arbres, valeurs, formules = {}, {}, {}
for nom, chemin in feuilles:
    root = ET.fromstring(contenu[chemin])
    arbres[nom] = root
    for c in root.iter(NS + "c"):
        ref = c.get("r")
        f = c.find(NS + "f")
        v = c.find(NS + "v")
        if f is not None and f.text:
            formules[(nom, ref)] = f.text
        if v is not None and v.text not in (None, ""):
            try:
                valeurs[(nom, ref)] = float(v.text)
            except ValueError:
                pass

print("formules:", len(formules), "| valeurs en cache:", len(valeurs))
manquantes = [k for k in formules if k not in valeurs]
print("formules sans cache:", len(manquantes))

for passe in range(15):
    progres = 0
    for cle in list(manquantes):
        nom, ref = cle
        f = formules[cle]

        def getval(r, _nom=nom):
            return valeurs.get((_nom, r))

        try:
            def repl_sum(m):
                total = 0.0
                for rr in expand(m.group(1)):
                    vv = getval(rr)
                    if vv is None:
                        raise ValueError("dep")
                    total += vv
                return repr(total)

            expr = re.sub(r"SUM\(([A-Z]+\d+:[A-Z]+\d+)\)", repl_sum, f)

            def repl_ref(m):
                rr = m.group(0).replace("$", "")
                vv = getval(rr)
                if vv is None:
                    raise ValueError("dep")
                return repr(vv)

            expr = re.sub(r"\$?[A-Z]{1,2}\$?\d+", repl_ref, expr)
            valeurs[cle] = float(eval_expr(expr))
            manquantes.remove(cle)
            progres += 1
        except Exception:
            continue
    print("passe", passe + 1, "resolues:", progres, "restantes:", len(manquantes))
    if not manquantes or progres == 0:
        break

print("NON RESOLUES:", manquantes)

modifiees = 0
for nom, chemin in feuilles:
    root = arbres[nom]
    touche = False
    for c in root.iter(NS + "c"):
        ref = c.get("r")
        f = c.find(NS + "f")
        if f is None or not f.text:
            continue
        cle = (nom, ref)
        if cle not in valeurs:
            continue
        v = c.find(NS + "v")
        if v is None:
            v = ET.SubElement(c, NS + "v")
        val = valeurs[cle]
        v.text = str(int(val)) if abs(val - round(val)) < 1e-9 else repr(round(val, 10))
        c.set("t", "n")
        touche = True
        modifiees += 1
    if touche:
        contenu[chemin] = ET.tostring(root, encoding="UTF-8", xml_declaration=True)

print("cellules completees:", modifiees)

with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as z:
    for n in noms:
        z.writestr(n, contenu[n])
print("ecrit:", dst.name, dst.stat().st_size, "octets")

# controle
with zipfile.ZipFile(dst) as z:
    for feuille, attendu in [("sheet2", "Personnel"), ("sheet8", "Recap"), ("sheet9", "Ventilation")]:
        root = ET.fromstring(z.read("xl/worksheets/" + feuille + ".xml"))
        print("\n=== " + feuille + " (" + attendu + ") ===")
        for row in list(root.iter(NS + "row"))[:11]:
            cells = []
            for c in row.iter(NS + "c"):
                v = c.find(NS + "v")
                f = c.find(NS + "f")
                cells.append((c.get("r"), c.get("t"),
                              (f.text if f is not None else None),
                              (v.text if v is not None else None)))
            print("L" + row.get("r"), cells)
