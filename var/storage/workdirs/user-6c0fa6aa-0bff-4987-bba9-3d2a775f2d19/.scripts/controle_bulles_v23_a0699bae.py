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

print("\n### PUCE INSEREE (vague 2) ###")
print(nettoyer(trouver("Initier les Directeurs").xml))

print("\n### OBJECTIF GENERAL ###")
print(nettoyer(trouver("Renforcer les capacités des différentes catégories").xml))

print("\n### CONTROLE indentation de TOUTES les puces du document ###")
susp = []
for i, p in enumerate(paras):
    t = Paragraph(p, doc).text
    if t.strip().startswith("•"):
        ppr = p.find(ns + "pPr")
        ind = ppr.find(ns + "ind") if ppr is not None else None
        attrs = dict(ind.attrib) if ind is not None else {}
        attrs = {re.sub(r'\{.*\}', '', k): v for k, v in attrs.items()}
        tab = "\t" in t
        susp.append((i + 1, attrs, tab, t[:60]))
for s in susp:
    marque = "OK " if (s[1].get("left") == "360" and s[1].get("hanging") == "360" and s[2]) else "!! "
    print(f"{marque}L{s[0]} ind={s[1]} tab={s[2]} :: {s[3]}")

print("\n### TABLEAUX : nombre et dims ###")
tbls = [c for c in body.iterchildren() if c.tag == ns + "tbl"]
for i, tb in enumerate(tbls):
    rows = tb.findall(ns + "tr")
    print(f"  tableau {i}: {len(rows)} lignes")
