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

print("\n### PUCE MODELE (reference) ###")
print(nettoyer(trouver("Fondamentaux de l'intelligence artificielle").xml))

print("\n### MES 4 PUCES ###")
for n in ["Initier les Directeurs", "Permettre aux Directeurs", "Identifier, pour chaque vague", "Sensibiliser l'ensemble"]:
    p = trouver(n)
    print(f"\n--- {n}")
    print(nettoyer(p.xml))

print("\n### CONTROLE final de TOUTES les puces ###")
ko = 0
for i, p in enumerate(paras):
    t = Paragraph(p, doc).text
    if t.strip().startswith("•"):
        ppr = p.find(ns + "pPr")
        ind = ppr.find(ns + "ind") if ppr is not None else None
        attrs = {re.sub(r'\{.*\}', '', k): v for k, v in (ind.attrib.items() if ind is not None else [])}
        tab = "\t" in t
        ok = attrs.get("left") == "360" and attrs.get("hanging") == "360" and tab
        if not ok:
            ko += 1
            print(f"  !! L{i+1} ind={attrs} tab={tab} :: {t[:60]}")
print(f"puces non conformes : {ko}")
