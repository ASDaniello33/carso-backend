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
print("fichier inspecte:", cible.name)

doc = Document(str(cible))
ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
body = doc.element.body

paras = []
tbls = []
for child in body.iterchildren():
    tag = child.tag.replace(ns, "")
    if tag == "p":
        paras.append(child)
    elif tag == "tbl":
        tbls.append(child)

def find_para(needle):
    for p in paras:
        t = Paragraph(p, doc).text
        if needle in t:
            return p
    return None

print("\n===== XML COMPLET d'une puce EXISTANTE du modele =====")
p = find_para("Renforcer les capacités des équipes SI dans la structuration")
print(p.xml[:2500])

print("\n===== XML COMPLET d'une puce INSEREE (vague 2) =====")
p2 = find_para("Initier les Directeurs, Chefs de service")
print(p2.xml[:2500])

print("\n===== TABLEAUX : contenu integral =====")
for i, tbl in enumerate(tbls):
    t = Table(tbl, doc)
    print(f"\n----- TABLEAU {i} : {len(t.rows)} lignes x {len(t.columns)} colonnes")
    for ri, row in enumerate(t.rows):
        cells = []
        for c in row.cells:
            cells.append(c.text.replace("\n", " / ").strip())
        print(f"  L{ri}: " + " || ".join(cells))
