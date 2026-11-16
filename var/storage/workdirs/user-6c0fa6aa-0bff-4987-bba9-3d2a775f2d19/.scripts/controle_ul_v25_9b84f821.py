import pathlib, re
from docx import Document
from docx.text.paragraph import Paragraph

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
paras = [c for c in body.iterchildren() if c.tag == ns + "p"]

def nettoyer(x):
    x = re.sub(r'\sxmlns:[a-zA-Z0-9]+="[^"]*"', "", x)
    x = re.sub(r'\sw14:paraId="[^"]*"', "", x)
    x = re.sub(r'\sw14:textId="[^"]*"', "", x)
    return x.replace("><", ">\n<")

def trouver(needle):
    for p in paras:
        if needle in Paragraph(p, doc).text:
            return p
    return None

print("\n### RESULTAT de la variante <ul> ###")
print(nettoyer(trouver("Sensibiliser l'ensemble").xml))

print("\n### PUCE MODELE (reference) ###")
print(nettoyer(trouver("Fondamentaux de l'intelligence artificielle").xml))
