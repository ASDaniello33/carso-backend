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
print("fichier:", cible.name)

doc = Document(str(cible))
ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
body = doc.element.body

enfants = list(body.iterchildren())
print("total enfants body:", len(enfants))

def eid(i):
    return f"block_{i:03d}"

def texte(i):
    ch = enfants[i-1]
    if ch.tag == ns + "p":
        return Paragraph(ch, doc).text
    return "[TABLEAU]"

print("\n=== ORDRE section 3 (Objectifs) ===")
for i in range(len(enfants)):
    t = texte(i+1)
    if t.strip().startswith("3. Objectifs de la formation") and i > 60:
        for j in range(i-1, i+30):
            print(f"  {eid(j+1)} :: {texte(j+1)[:110]}")
        break

print("\n=== SOMMAIRE : XML des premiers elements ===")
for j in range(21, 35):
    ch = enfants[j]
    if ch.tag == ns + "p":
        t = Paragraph(ch, doc).text
        x = re.sub(r'\sxmlns:[a-zA-Z0-9]+="[^"]*"', "", ch.xml)
        print(f"\n--- {eid(j+1)} :: {t!r}")
        print(x.replace("><", ">\n<")[:1400])

print("\n=== VAGUE 1 - JOUR 1 : puces ===")
for j in range(len(enfants)):
    t = texte(j+1)
    if "Fondamentaux de l'intelligence artificielle" in t:
        for k in range(j-1, j+6):
            print(f"  {eid(k+1)} :: {texte(k+1)[:120]}")
        break

print("\n=== DUREE TOTALE ===")
for j in range(len(enfants)):
    t = texte(j+1)
    if "durée totale de la formation" in t:
        print(f"  {eid(j+1)} :: {t}")
        break
