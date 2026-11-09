from pathlib import Path
from PIL import Image

cible = Path(r'C:\Users\Asus\Documents\CARSO_AI\backend\var\storage\carso\generated\8a92263f-b914-4933-9209-9e1dc93c54a3\895124117_1536882931801862_6566280990836471475_n.jpg')
im = Image.open(cible).convert('RGB')
w, h = im.size
px = im.load()

def proche(p, ref, tol):
    return all(abs(p[i]-ref[i]) <= tol for i in range(3))

FOND = (0x27, 0x21, 0x61)
CARTE = (0x3B, 0x43, 0x82)

# colonnes majoritairement FOND dans la zone cartes (y 620..1900)
print('=== COLONNES DE FOND (gouttieres) y 620..1900 ===')
gout = []
for x in range(150, 2100, 2):
    nf = 0; nc = 0
    for y in range(620, 1900, 6):
        p = px[x, y]
        if proche(p, FOND, 18): nf += 1
        elif proche(p, CARTE, 26): nc += 1
    tot = (1900-620)//6
    if nf / tot > 0.55:
        gout.append(x)
# regrouper
groupes = []
if gout:
    d = gout[0]; prec = gout[0]
    for x in gout[1:]:
        if x - prec > 6:
            groupes.append((d, prec)); d = x
        prec = x
    groupes.append((d, prec))
print('gouttieres verticales:', groupes)

# lignes majoritairement FOND (gouttieres horizontales) x 220..2040
print('=== LIGNES DE FOND (gouttieres) x 220..2040 ===')
gout = []
for y in range(600, 2040, 2):
    nf = 0
    for x in range(220, 2040, 6):
        if proche(px[x, y], FOND, 18): nf += 1
    tot = (2040-220)//6
    if nf / tot > 0.55:
        gout.append(y)
groupes = []
if gout:
    d = gout[0]; prec = gout[0]
    for y in gout[1:]:
        if y - prec > 6:
            groupes.append((d, prec)); d = y
        prec = y
    groupes.append((d, prec))
print('gouttieres horizontales:', groupes)
