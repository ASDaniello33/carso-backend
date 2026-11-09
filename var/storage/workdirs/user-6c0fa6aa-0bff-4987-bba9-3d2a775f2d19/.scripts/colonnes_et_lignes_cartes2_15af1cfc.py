from pathlib import Path
from PIL import Image

cible = Path(r'C:\Users\Asus\Documents\CARSO_AI\backend\var\storage\carso\generated\8a92263f-b914-4933-9209-9e1dc93c54a3\895124117_1536882931801862_6566280990836471475_n.jpg')
im = Image.open(cible).convert('RGB')
w, h = im.size
px = im.load()
print('taille', w, h)

def proche(p, ref, tol):
    return all(abs(p[i]-ref[i]) <= tol for i in range(3))

FOND = (0x27, 0x21, 0x61)
CARTE = (0x3B, 0x43, 0x82)

def groupes_de(vals, ecart=6):
    if not vals:
        return []
    out = []
    d = vals[0]; prec = vals[0]
    for v in vals[1:]:
        if v - prec > ecart:
            out.append((d, prec)); d = v
        prec = v
    out.append((d, prec))
    return out

print('=== GOUTTIERES VERTICALES (colonnes de fond) ===')
gout = []
for x in range(150, w - 4, 2):
    nf = 0; tot = 0
    for y in range(620, 1900, 6):
        tot += 1
        if proche(px[x, y], FOND, 18):
            nf += 1
    if nf / tot > 0.5:
        gout.append(x)
print(groupes_de(gout))

print('=== GOUTTIERES HORIZONTALES (lignes de fond) ===')
gout = []
for y in range(600, h - 4, 2):
    nf = 0; tot = 0
    for x in range(220, 2040, 6):
        tot += 1
        if proche(px[x, y], FOND, 18):
            nf += 1
    if nf / tot > 0.5:
        gout.append(y)
print(groupes_de(gout))
