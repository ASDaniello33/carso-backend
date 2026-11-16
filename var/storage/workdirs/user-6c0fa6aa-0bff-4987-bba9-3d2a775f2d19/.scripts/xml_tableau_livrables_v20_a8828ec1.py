import pathlib, re
from docx import Document
from docx.table import Table

parent = pathlib.Path.cwd().parent
fichiers = list(parent.glob("modele-OFFRE-TECH-L01*.docx"))
def version(f):
    m = re.search(r"_v(\d+)\.docx$", f.name)
    return int(m.group(1)) if m else 0
cible = max(fichiers, key=version)
print("fichier:", cible.name)

doc = Document(str(cible))
ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
body = doc.element.body

tbls = [c for c in body.iterchildren() if c.tag == ns + "tbl"]
print("nombre de tableaux:", len(tbls))
liv = tbls[-1]
t = Table(liv, doc)

# Proprietes du tableau
tblPr = liv.find(ns + "tblPr")
print("\n=== tblPr ===")
print(tblPr.xml.replace("><", ">\n<") if tblPr is not None else "aucun")
grid = liv.find(ns + "tblGrid")
print("\n=== tblGrid ===")
print(grid.xml.replace("><", ">\n<") if grid is not None else "aucun")

# Derniere ligne (livrable 6), cellule 2
print("\n=== XML COMPLET de la derniere ligne ===")
last_row = t.rows[-1]._tr
xml = last_row.xml
# nettoyer les namespaces pour la lecture
xml = re.sub(r'\sxmlns:[a-zA-Z0-9]+="[^"]*"', "", xml)
print(xml.replace("><", ">\n<"))

print("\n=== XML COMPLET de la premiere ligne (en-tete) ===")
first_row = t.rows[0]._tr
xml2 = re.sub(r'\sxmlns:[a-zA-Z0-9]+="[^"]*"', "", first_row.xml)
print(xml2.replace("><", ">\n<"))
